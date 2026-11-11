"""Databasemodeller (SQLModel/SQLite)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class Upload(SQLModel, table=True):
    __tablename__ = "uploads"

    id: Optional[int] = Field(default=None, primary_key=True)
    stored_name: str = Field(index=True, unique=True)  # tilfældigt, sikkert filnavn
    original_name: str = ""
    mime: str = ""
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Scene(SQLModel, table=True):
    __tablename__ = "scenes"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Ny scene"
    sort_order: int = 0

    bg_color: str = "#0b1d3a"
    bg_image_id: Optional[int] = Field(default=None, foreign_key="uploads.id")
    bg_fit: str = "cover"  # cover | contain

    logo_image_id: Optional[int] = Field(default=None, foreign_key="uploads.id")
    show_logo: bool = True
    logo_size: int = 40  # procent af skærmbredden
    logo_position: str = "top"  # top | center | bottom

    primary_text: str = ""
    secondary_text: str = ""
    show_primary: bool = True
    show_secondary: bool = True
    primary_color: str = "#ffffff"
    secondary_color: str = "#c9d6ea"
    primary_size: int = 110  # px på referencelærredet (1080 bredt)
    secondary_size: int = 60

    h_align: str = "center"  # left | center | right
    v_align: str = "bottom"  # top | center | bottom
    text_shadow: bool = True

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class QuickText(SQLModel, table=True):
    __tablename__ = "quick_texts"

    id: Optional[int] = Field(default=None, primary_key=True)
    slot: int = Field(index=True, unique=True)
    primary_text: str = ""
    secondary_text: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class AppSettings(SQLModel, table=True):
    """Én række (id=1) med brugerindstillinger."""

    __tablename__ = "app_settings"

    id: int = Field(default=1, primary_key=True)
    fallback_scene_id: Optional[int] = None
    heartbeat_interval: int = 10
    warning_after: int = 30
    fallback_after: int = 120
    auto_fallback: bool = True
    screen_format: str = "9:16"
    default_primary_size: int = 110
    default_secondary_size: int = 60
    # Seneste live-scene gemmes til orientering – den genoptages IKKE ved opstart.
    last_live_name: str = ""
    last_live_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class EventLog(SQLModel, table=True):
    __tablename__ = "event_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    level: str = "info"  # info | warning | error
    kind: str = ""
    message: str = ""
