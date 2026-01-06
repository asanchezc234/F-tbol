from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from app.db import get_session, init_db
from app.models import Match, MatchType, Player, Signup, capacity_for
from app.whatsapp_parser import build_roster_from_messages, parse_whatsapp_export


app = FastAPI(title="Reservas Fútbol")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _flash_from_request(request: Request) -> Optional[str]:
    return request.query_params.get("flash")


def _parse_scheduled_at(raw: str) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _new_join_code() -> str:
    # Corto y fácil de escribir/compartir
    return secrets.token_hex(4)


def _get_or_create_player(session, *, name: str, whatsapp_display_name: Optional[str] = None) -> Player:
    name_clean = name.strip()
    existing = session.exec(select(Player).where(Player.name == name_clean)).first()
    if existing:
        if whatsapp_display_name and not existing.whatsapp_display_name:
            existing.whatsapp_display_name = whatsapp_display_name
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing

    p = Player(name=name_clean, whatsapp_display_name=whatsapp_display_name)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _count_active_signups(session, match_id: int) -> int:
    rows = session.exec(select(Signup).where(Signup.match_id == match_id, Signup.is_cancelled == False)).all()  # noqa: E712
    return len(rows)


def _render_whatsapp_text(
    title: str,
    match_type: MatchType,
    capacity: int,
    signups: list[dict[str, Any]],
    *,
    join_url: str,
) -> str:
    lines = [
        f"LISTA {title} ({match_type.value}) {len([s for s in signups if not s['is_cancelled']])}/{capacity}",
        "",
    ]
    i = 1
    for s in signups:
        if s["is_cancelled"]:
            continue
        paid = "✅" if s["is_paid"] else "💸"
        lines.append(f"{i}. {s['player_name']} {paid}")
        i += 1
    lines.append("")
    lines.append(f"Link para apuntarse: {join_url}")
    lines.append("Responde con: 'me apunto' o '+1' para entrar en la lista (o usa el link).")
    return "\n".join(lines)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with get_session() as session:
        matches = session.exec(select(Match).order_by(Match.created_at.desc())).all()
        view = []
        for m in matches:
            view.append(
                {
                    "id": m.id,
                    "title": m.title,
                    "match_type": m.match_type.value,
                    "capacity": m.capacity,
                    "is_open": m.is_open,
                    "count_active": _count_active_signups(session, m.id),
                }
            )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"matches": view, "flash": _flash_from_request(request)},
    )


@app.get("/matches/new", response_class=HTMLResponse)
def match_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "match_new.html", {"flash": _flash_from_request(request)})


@app.post("/matches/new")
def match_new_post(
    title: str = Form(...),
    match_type: MatchType = Form(...),
    scheduled_at: str = Form(""),
) -> RedirectResponse:
    sched = _parse_scheduled_at(scheduled_at)
    cap = capacity_for(match_type)

    with get_session() as session:
        code = _new_join_code()
        # Garantizar unicidad
        while session.exec(select(Match).where(Match.join_code == code)).first():
            code = _new_join_code()

        m = Match(
            title=title.strip(),
            match_type=match_type,
            capacity=cap,
            scheduled_at=sched,
            join_code=code,
            is_open=True,
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        match_id = m.id

    return RedirectResponse(url=f"/matches/{match_id}?flash=Partido+creado", status_code=303)


@dataclass
class SignupView:
    id: int
    player_name: str
    is_paid: bool
    is_cancelled: bool
    notes: Optional[str]


@app.get("/matches/{match_id}", response_class=HTMLResponse)
def match_detail(request: Request, match_id: int) -> HTMLResponse:
    with get_session() as session:
        match = session.get(Match, match_id)
        if not match:
            return templates.TemplateResponse(
                request, "index.html", {"matches": [], "flash": "Partido no encontrado"}
            )

        signups = session.exec(select(Signup).where(Signup.match_id == match_id).order_by(Signup.joined_at.asc())).all()
        players_by_id = {
            p.id: p for p in session.exec(select(Player).where(Player.id.in_([s.player_id for s in signups]))).all()
        } if signups else {}

        view_signups: list[dict[str, Any]] = []
        for s in signups:
            p = players_by_id.get(s.player_id)
            view_signups.append(
                {
                    "id": s.id,
                    "player_name": p.name if p else f"Player #{s.player_id}",
                    "is_paid": bool(s.is_paid),
                    "is_cancelled": bool(s.is_cancelled),
                    "notes": s.notes,
                }
            )

        count_active = len([s for s in signups if not s.is_cancelled])
        join_url = f"{request.base_url}join/{match.join_code}"
        whatsapp_text = _render_whatsapp_text(
            match.title, match.match_type, match.capacity, view_signups, join_url=join_url
        )

    return templates.TemplateResponse(
        request,
        "match_detail.html",
        {
            "match": match,
            "signups": view_signups,
            "count_active": count_active,
            "whatsapp_text": whatsapp_text,
            "join_url": join_url,
            "flash": _flash_from_request(request),
        },
    )


@app.get("/join/{join_code}", response_class=HTMLResponse)
def join_get(request: Request, join_code: str) -> HTMLResponse:
    with get_session() as session:
        match = session.exec(select(Match).where(Match.join_code == join_code)).first()
        if not match:
            return templates.TemplateResponse(
                request, "index.html", {"matches": [], "flash": "Partido no encontrado"}
            )

        signups = session.exec(
            select(Signup).where(Signup.match_id == match.id).order_by(Signup.joined_at.asc())
        ).all()
        players_by_id = (
            {p.id: p for p in session.exec(select(Player).where(Player.id.in_([s.player_id for s in signups]))).all()}
            if signups
            else {}
        )

        view_signups: list[dict[str, Any]] = []
        for s in signups:
            p = players_by_id.get(s.player_id)
            view_signups.append(
                {
                    "id": s.id,
                    "player_name": p.name if p else f"Player #{s.player_id}",
                    "is_cancelled": bool(s.is_cancelled),
                }
            )
        count_active = len([s for s in signups if not s.is_cancelled])

    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "match": match,
            "signups": view_signups,
            "count_active": count_active,
            "flash": _flash_from_request(request),
        },
    )


@app.post("/join/{join_code}")
def join_post(join_code: str, name: str = Form(...)) -> RedirectResponse:
    with get_session() as session:
        match = session.exec(select(Match).where(Match.join_code == join_code)).first()
        if not match:
            return RedirectResponse(url="/?flash=Partido+no+encontrado", status_code=303)
        if not match.is_open:
            return RedirectResponse(url=f"/join/{join_code}?flash=Lista+cerrada", status_code=303)
        active = _count_active_signups(session, match.id)
        if active >= match.capacity:
            return RedirectResponse(url=f"/join/{join_code}?flash=Cupo+completo", status_code=303)

        player = _get_or_create_player(session, name=name.strip())
        existing = session.exec(select(Signup).where(Signup.match_id == match.id, Signup.player_id == player.id)).first()
        if existing and not existing.is_cancelled:
            return RedirectResponse(url=f"/join/{join_code}?flash=Ya+estas+apuntado", status_code=303)
        if existing and existing.is_cancelled:
            existing.is_cancelled = False
            existing.cancelled_at = None
            session.add(existing)
            session.commit()
            return RedirectResponse(url=f"/join/{join_code}?flash=Listo,+reincorporado", status_code=303)

        session.add(Signup(match_id=match.id, player_id=player.id))
        session.commit()

    return RedirectResponse(url=f"/join/{join_code}?flash=Listo,+estas+en+la+lista", status_code=303)


@app.post("/matches/{match_id}/toggle-open")
def match_toggle_open(match_id: int) -> RedirectResponse:
    with get_session() as session:
        match = session.get(Match, match_id)
        if not match:
            return RedirectResponse(url="/?flash=Partido+no+encontrado", status_code=303)
        match.is_open = not match.is_open
        session.add(match)
        session.commit()
    return RedirectResponse(url=f"/matches/{match_id}", status_code=303)


@app.post("/matches/{match_id}/add")
def match_add_player(
    match_id: int,
    name: str = Form(...),
    notes: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        match = session.get(Match, match_id)
        if not match:
            return RedirectResponse(url="/?flash=Partido+no+encontrado", status_code=303)

        active = _count_active_signups(session, match_id)
        if active >= match.capacity:
            return RedirectResponse(url=f"/matches/{match_id}?flash=Cupo+completo", status_code=303)

        player = _get_or_create_player(session, name=name.strip())

        existing = session.exec(
            select(Signup).where(Signup.match_id == match_id, Signup.player_id == player.id)
        ).first()
        if existing and not existing.is_cancelled:
            return RedirectResponse(url=f"/matches/{match_id}?flash=Ya+estaba+en+la+lista", status_code=303)
        if existing and existing.is_cancelled:
            existing.is_cancelled = False
            existing.cancelled_at = None
            if notes.strip():
                existing.notes = notes.strip()
            session.add(existing)
            session.commit()
            return RedirectResponse(url=f"/matches/{match_id}?flash=Reincorporado", status_code=303)

        s = Signup(match_id=match_id, player_id=player.id, notes=(notes.strip() or None))
        session.add(s)
        session.commit()

    return RedirectResponse(url=f"/matches/{match_id}", status_code=303)


@app.post("/signups/{signup_id}/toggle-paid")
def signup_toggle_paid(signup_id: int) -> RedirectResponse:
    with get_session() as session:
        s = session.get(Signup, signup_id)
        if not s:
            return RedirectResponse(url="/?flash=Inscripcion+no+encontrada", status_code=303)
        s.is_paid = not bool(s.is_paid)
        s.paid_at = datetime.utcnow() if s.is_paid else None
        session.add(s)
        session.commit()
        match_id = s.match_id
    return RedirectResponse(url=f"/matches/{match_id}", status_code=303)


@app.post("/signups/{signup_id}/toggle-cancel")
def signup_toggle_cancel(signup_id: int) -> RedirectResponse:
    with get_session() as session:
        s = session.get(Signup, signup_id)
        if not s:
            return RedirectResponse(url="/?flash=Inscripcion+no+encontrada", status_code=303)
        s.is_cancelled = not bool(s.is_cancelled)
        s.cancelled_at = datetime.utcnow() if s.is_cancelled else None
        session.add(s)
        session.commit()
        match_id = s.match_id
    return RedirectResponse(url=f"/matches/{match_id}", status_code=303)


@app.get("/parse", response_class=HTMLResponse)
def parse_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "parse.html", {"flash": _flash_from_request(request)})


@app.post("/parse")
def parse_post(
    title: str = Form(...),
    match_type: MatchType = Form(...),
    start_marker: str = Form("LISTA"),
    chat_text: str = Form(...),
) -> RedirectResponse:
    cap = capacity_for(match_type)
    messages = parse_whatsapp_export(chat_text)
    roster = build_roster_from_messages(messages, start_marker=start_marker, capacity=cap)

    with get_session() as session:
        code = _new_join_code()
        while session.exec(select(Match).where(Match.join_code == code)).first():
            code = _new_join_code()

        m = Match(
            title=title.strip(),
            match_type=match_type,
            capacity=cap,
            scheduled_at=None,
            join_code=code,
            is_open=True,
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        match_id = m.id

        for name in roster:
            p = _get_or_create_player(session, name=name.strip(), whatsapp_display_name=name.strip())
            existing = session.exec(select(Signup).where(Signup.match_id == m.id, Signup.player_id == p.id)).first()
            if existing:
                continue
            session.add(Signup(match_id=m.id, player_id=p.id))
        session.commit()

    msg = f"Creado+con+{len(roster)}+apuntados+(de+{cap})"
    return RedirectResponse(url=f"/matches/{match_id}?flash={msg}", status_code=303)


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request) -> HTMLResponse:
    with get_session() as session:
        players = session.exec(select(Player).order_by(Player.name.asc())).all()
        rows = []
        for p in players:
            signups = session.exec(select(Signup).where(Signup.player_id == p.id)).all()
            total = len(signups)
            if total == 0:
                continue
            cancels = len([s for s in signups if s.is_cancelled])
            paid = len([s for s in signups if s.is_paid])

            cancel_rate = cancels / total
            pay_rate = paid / total

            # Riesgo simple 0-100 (más cancelaciones + menos pago => más riesgo)
            risk = round(100 * (0.7 * cancel_rate + 0.3 * (1 - pay_rate)))
            risk = max(0, min(100, int(risk)))

            rows.append(
                {
                    "name": p.name,
                    "total": total,
                    "cancel_rate": f"{round(cancel_rate * 100)}%",
                    "pay_rate": f"{round(pay_rate * 100)}%",
                    "risk": risk,
                }
            )

    # Orden: riesgo desc, luego total desc
    rows.sort(key=lambda r: (r["risk"], r["total"]), reverse=True)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {"rows": rows, "flash": _flash_from_request(request)},
    )

