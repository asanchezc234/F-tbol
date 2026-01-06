from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


@dataclass(frozen=True)
class ParsedMessage:
    sender: str
    text: str
    timestamp: Optional[datetime] = None


# Formatos comunes de export de WhatsApp:
# - "12/1/26, 20:30 - Juan: Me apunto"
# - "12/1/2026, 20:30 - Juan Pérez: +1"
_EXPORT_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s(?P<time>\d{1,2}:\d{2})\s-\s(?P<sender>[^:]+):\s(?P<text>.*)$"
)


def _parse_dt(date_str: str, time_str: str) -> Optional[datetime]:
    # Intentos con año 2 y 4 dígitos.
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    return None


def parse_whatsapp_export(text: str) -> list[ParsedMessage]:
    msgs: list[ParsedMessage] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _EXPORT_RE.match(line)
        if m:
            dt = _parse_dt(m.group("date"), m.group("time"))
            msgs.append(
                ParsedMessage(
                    sender=m.group("sender").strip(),
                    text=m.group("text").strip(),
                    timestamp=dt,
                )
            )
            continue

        # Fallback: "Juan: me apunto"
        if ":" in line:
            sender, msg = line.split(":", 1)
            if sender.strip() and msg.strip():
                msgs.append(ParsedMessage(sender=sender.strip(), text=msg.strip()))
    return msgs


_JOIN_KEYWORDS = [
    "me apunto",
    "apunto",
    "voy",
    "+1",
    "presente",
    "ok",
    "yo",
    "cuenta conmigo",
    "anotame",
    "anótame",
]


def looks_like_join_message(msg_text: str) -> bool:
    t = msg_text.strip().lower()
    # El "+1" suelto o al inicio es común
    if t == "+1" or t.startswith("+1 "):
        return True
    return any(k in t for k in _JOIN_KEYWORDS)


def build_roster_from_messages(
    messages: Iterable[ParsedMessage],
    *,
    start_marker: str = "lista",
    capacity: int,
) -> list[str]:
    """
    Devuelve una lista de nombres (sender) en orden de aparición,
    desde el mensaje que contiene `start_marker` (case-insensitive),
    agregando senders que parezcan "me apunto", hasta completar cupo.
    """
    roster: list[str] = []
    seen: set[str] = set()
    started = False
    marker = start_marker.strip().lower()

    for msg in messages:
        txt = msg.text.strip().lower()
        if not started:
            if marker and marker in txt:
                started = True
            continue

        if not looks_like_join_message(msg.text):
            continue
        sender = msg.sender.strip()
        if not sender or sender in seen:
            continue
        roster.append(sender)
        seen.add(sender)
        if len(roster) >= capacity:
            break

    return roster

