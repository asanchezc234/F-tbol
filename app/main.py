from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import select

from app.db import get_session, init_db
from app.models import Match, MatchType, Player, Signup, capacity_for


# Importar modelos antes de init_db (registro en SQLModel.metadata).
app = FastAPI(title="Reservas Fútbol API")

# CORS para dev (React en :5173)
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _parse_scheduled_at(raw: Optional[str]) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="scheduled_at inválido. Usá YYYY-MM-DD HH:MM")


def _new_join_code(session) -> str:
    code = secrets.token_hex(4)
    while session.exec(select(Match).where(Match.join_code == code)).first():
        code = secrets.token_hex(4)
    return code


def _get_or_create_player(session, *, name: str, phone: Optional[str] = None, whatsapp_display_name: Optional[str] = None) -> Player:
    name_clean = name.strip()

    if phone:
        by_phone = session.exec(select(Player).where(Player.phone == phone)).first()
        if by_phone:
            if whatsapp_display_name and not by_phone.whatsapp_display_name:
                by_phone.whatsapp_display_name = whatsapp_display_name
                session.add(by_phone)
                session.commit()
                session.refresh(by_phone)
            if name_clean and by_phone.name != name_clean:
                # No pisamos el nombre si ya existe uno “mejor”
                pass
            return by_phone

    existing = session.exec(select(Player).where(Player.name == name_clean)).first()
    if existing:
        if phone and not existing.phone:
            existing.phone = phone
        if whatsapp_display_name and not existing.whatsapp_display_name:
            existing.whatsapp_display_name = whatsapp_display_name
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    p = Player(name=name_clean or (phone or "Jugador"), whatsapp_display_name=whatsapp_display_name, phone=phone)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _count_active_signups(session, match_id: int) -> int:
    rows = session.exec(select(Signup).where(Signup.match_id == match_id, Signup.is_cancelled == False)).all()  # noqa: E712
    return len(rows)


def _signup_view(session, s: Signup) -> dict[str, Any]:
    p = session.get(Player, s.player_id)
    return {
        "id": s.id,
        "playerId": s.player_id,
        "playerName": p.name if p else f"Player #{s.player_id}",
        "isPaid": bool(s.is_paid),
        "isCancelled": bool(s.is_cancelled),
        "notes": s.notes,
        "joinedAt": s.joined_at.isoformat(),
    }


class MatchCreateIn(BaseModel):
    title: str = Field(..., min_length=1)
    matchType: MatchType
    scheduledAt: Optional[str] = None


class SignupCreateIn(BaseModel):
    name: str = Field(..., min_length=1)
    notes: Optional[str] = None


class SignupPatchIn(BaseModel):
    isPaid: Optional[bool] = None
    isCancelled: Optional[bool] = None
    notes: Optional[str] = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/matches")
def list_matches() -> list[dict[str, Any]]:
    with get_session() as session:
        matches = session.exec(select(Match).order_by(Match.created_at.desc())).all()
        out = []
        for m in matches:
            out.append(
                {
                    "id": m.id,
                    "title": m.title,
                    "matchType": m.match_type.value,
                    "capacity": m.capacity,
                    "isOpen": bool(m.is_open),
                    "scheduledAt": m.scheduled_at.isoformat() if m.scheduled_at else None,
                    "createdAt": m.created_at.isoformat(),
                    "joinCode": m.join_code,
                    "countActive": _count_active_signups(session, m.id),
                }
            )
        return out


@app.post("/api/matches", status_code=201)
def create_match(payload: MatchCreateIn) -> dict[str, Any]:
    with get_session() as session:
        cap = capacity_for(payload.matchType)
        m = Match(
            title=payload.title.strip(),
            match_type=payload.matchType,
            capacity=cap,
            scheduled_at=_parse_scheduled_at(payload.scheduledAt),
            join_code=_new_join_code(session),
            is_open=True,
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return {
            "id": m.id,
            "title": m.title,
            "matchType": m.match_type.value,
            "capacity": m.capacity,
            "isOpen": bool(m.is_open),
            "scheduledAt": m.scheduled_at.isoformat() if m.scheduled_at else None,
            "createdAt": m.created_at.isoformat(),
            "joinCode": m.join_code,
            "countActive": 0,
        }


@app.get("/api/matches/{match_id}")
def get_match(match_id: int) -> dict[str, Any]:
    with get_session() as session:
        m = session.get(Match, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="Match no encontrado")

        signups = session.exec(select(Signup).where(Signup.match_id == match_id).order_by(Signup.joined_at.asc())).all()
        return {
            "id": m.id,
            "title": m.title,
            "matchType": m.match_type.value,
            "capacity": m.capacity,
            "isOpen": bool(m.is_open),
            "scheduledAt": m.scheduled_at.isoformat() if m.scheduled_at else None,
            "createdAt": m.created_at.isoformat(),
            "joinCode": m.join_code,
            "countActive": len([s for s in signups if not s.is_cancelled]),
            "signups": [_signup_view(session, s) for s in signups],
        }


@app.post("/api/matches/{match_id}/toggle-open")
def toggle_match_open(match_id: int) -> dict[str, Any]:
    with get_session() as session:
        m = session.get(Match, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="Match no encontrado")
        m.is_open = not bool(m.is_open)
        session.add(m)
        session.commit()
        session.refresh(m)
        return {"id": m.id, "isOpen": bool(m.is_open)}


@app.post("/api/matches/{match_id}/signups", status_code=201)
def create_signup(match_id: int, payload: SignupCreateIn) -> dict[str, Any]:
    with get_session() as session:
        m = session.get(Match, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="Match no encontrado")

        active = _count_active_signups(session, match_id)
        if active >= m.capacity:
            raise HTTPException(status_code=409, detail="Cupo completo")

        player = _get_or_create_player(session, name=payload.name.strip())
        existing = session.exec(select(Signup).where(Signup.match_id == match_id, Signup.player_id == player.id)).first()
        if existing and not existing.is_cancelled:
            raise HTTPException(status_code=409, detail="Ya estaba en la lista")
        if existing and existing.is_cancelled:
            existing.is_cancelled = False
            existing.cancelled_at = None
            if payload.notes is not None:
                existing.notes = payload.notes.strip() or None
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return _signup_view(session, existing)

        s = Signup(match_id=match_id, player_id=player.id, notes=(payload.notes.strip() if payload.notes else None))
        session.add(s)
        session.commit()
        session.refresh(s)
        return _signup_view(session, s)


@app.patch("/api/signups/{signup_id}")
def patch_signup(signup_id: int, payload: SignupPatchIn) -> dict[str, Any]:
    with get_session() as session:
        s = session.get(Signup, signup_id)
        if not s:
            raise HTTPException(status_code=404, detail="Signup no encontrado")

        if payload.isPaid is not None:
            s.is_paid = bool(payload.isPaid)
            s.paid_at = datetime.utcnow() if s.is_paid else None
        if payload.isCancelled is not None:
            s.is_cancelled = bool(payload.isCancelled)
            s.cancelled_at = datetime.utcnow() if s.is_cancelled else None
        if payload.notes is not None:
            s.notes = payload.notes.strip() or None

        session.add(s)
        session.commit()
        session.refresh(s)
        return _signup_view(session, s)


@app.get("/api/analytics")
def analytics() -> list[dict[str, Any]]:
    with get_session() as session:
        players = session.exec(select(Player).order_by(Player.name.asc())).all()
        rows: list[dict[str, Any]] = []
        for p in players:
            signups = session.exec(select(Signup).where(Signup.player_id == p.id)).all()
            total = len(signups)
            if total == 0:
                continue
            cancels = len([s for s in signups if s.is_cancelled])
            paid = len([s for s in signups if s.is_paid])

            cancel_rate = cancels / total
            pay_rate = paid / total
            risk = round(100 * (0.7 * cancel_rate + 0.3 * (1 - pay_rate)))
            risk = max(0, min(100, int(risk)))

            rows.append(
                {
                    "playerId": p.id,
                    "name": p.name,
                    "total": total,
                    "cancelRate": cancel_rate,
                    "payRate": pay_rate,
                    "risk": risk,
                }
            )
        rows.sort(key=lambda r: (r["risk"], r["total"]), reverse=True)
        return rows


# --- WhatsApp Business Cloud Webhook (tiempo real vía bot 1:1) ---
#
# IMPORTANT: La API oficial NO permite “leer mensajes de un grupo normal”.
# Esto funciona para chats con el número Business (bot).

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # opcional (firma)


def _verify_meta_signature(request: Request, body_bytes: bytes) -> None:
    if not WHATSAPP_APP_SECRET:
        return
    sig = request.headers.get("X-Hub-Signature-256") or ""
    if not sig.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing signature")
    expected = "sha256=" + hmac.new(WHATSAPP_APP_SECRET.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Bad signature")


@app.get("/webhooks/whatsapp")
def whatsapp_verify(request: Request) -> Any:
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")
    if mode == "subscribe" and token and token == WHATSAPP_VERIFY_TOKEN and challenge:
        return JSONResponse(content=int(challenge))
    raise HTTPException(status_code=403, detail="Verification failed")


_JOIN_RE = re.compile(r"(?i)\\b(?:join|apunto|me\\s*apunto|lista)\\s+([0-9a-f]{8})\\b|\\b([0-9a-f]{8})\\s*(?:\\+1|join|apunto)\\b")


def _extract_join_code(text: str) -> Optional[str]:
    m = _JOIN_RE.search(text.strip())
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").lower() or None


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    _verify_meta_signature(request, body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad JSON: {e}") from e

    # Cloud API shape: entry[].changes[].value.messages[]
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for msg in value.get("messages", []) or []:
                messages.append({"msg": msg, "value": value})

    # Procesamos solo texto por ahora
    processed = 0
    for item in messages:
        msg = item["msg"]
        if msg.get("type") != "text":
            continue
        text = ((msg.get("text") or {}).get("body") or "").strip()
        join_code = _extract_join_code(text) or _extract_join_code(text.replace("+1", " +1 "))
        if not join_code:
            continue

        from_phone = msg.get("from")  # formato: "54911...."
        profile = ((item["value"].get("contacts") or [{}])[0].get("profile") or {})
        display_name = profile.get("name")
        name = display_name or (from_phone or "Jugador")

        with get_session() as session:
            match = session.exec(select(Match).where(Match.join_code == join_code)).first()
            if not match or not match.is_open:
                continue
            if _count_active_signups(session, match.id) >= match.capacity:
                continue

            player = _get_or_create_player(session, name=name, phone=from_phone, whatsapp_display_name=display_name)
            existing = session.exec(
                select(Signup).where(Signup.match_id == match.id, Signup.player_id == player.id)
            ).first()
            if existing and not existing.is_cancelled:
                continue
            if existing and existing.is_cancelled:
                existing.is_cancelled = False
                existing.cancelled_at = None
                session.add(existing)
                session.commit()
            else:
                session.add(Signup(match_id=match.id, player_id=player.id))
                session.commit()

        processed += 1

    return {"status": "ok", "processed": str(processed)}


# --- Servir React build (si existe) ---
DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
ASSETS_DIR = DIST_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/")
def serve_root() -> Any:
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Backend listo. Ejecutá el frontend React en /frontend."}


@app.get("/{path:path}")
def serve_spa(path: str) -> Any:
    # No interceptar API/webhooks
    if path.startswith("api") or path.startswith("webhooks"):
        raise HTTPException(status_code=404, detail="Not found")

    # Archivos estáticos en dist (favicon, etc)
    candidate = DIST_DIR / path
    if candidate.exists() and candidate.is_file():
        return FileResponse(str(candidate))

    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="Frontend no construido (npm run build)")

