from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class MatchType(str, Enum):
    fut5 = "fut5"
    fut6 = "fut6"
    fut7 = "fut7"


def capacity_for(match_type: MatchType) -> int:
    # Reglas típicas: fut5 -> 10, fut6 -> 12, fut7 -> 14
    return {"fut5": 10, "fut6": 12, "fut7": 14}[match_type.value]


class Player(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)

    # Datos opcionales (pueden venir de WhatsApp export)
    whatsapp_display_name: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class Match(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    match_type: MatchType = Field(index=True)
    capacity: int = Field(index=True)

    scheduled_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    join_code: str = Field(index=True, unique=True)
    is_open: bool = Field(default=True, index=True)


class Signup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    match_id: int = Field(index=True, foreign_key="match.id")
    player_id: int = Field(index=True, foreign_key="player.id")

    joined_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    is_cancelled: bool = Field(default=False, index=True)
    cancelled_at: Optional[datetime] = Field(default=None)

    is_paid: bool = Field(default=False, index=True)
    paid_at: Optional[datetime] = Field(default=None)

    notes: Optional[str] = Field(default=None)

