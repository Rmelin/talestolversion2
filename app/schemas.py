"""Validering af API-input."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

HEX = r"^#[0-9a-fA-F]{6}$"


class SceneFields(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    bg_color: Optional[str] = Field(default=None, pattern=HEX)
    bg_image_id: Optional[int] = None
    bg_fit: Optional[Literal["cover", "contain"]] = None
    logo_image_id: Optional[int] = None
    show_logo: Optional[bool] = None
    logo_size: Optional[int] = Field(default=None, ge=5, le=100)
    logo_position: Optional[Literal["top", "center", "bottom"]] = None
    primary_text: Optional[str] = Field(default=None, max_length=200)
    secondary_text: Optional[str] = Field(default=None, max_length=300)
    show_primary: Optional[bool] = None
    show_secondary: Optional[bool] = None
    primary_color: Optional[str] = Field(default=None, pattern=HEX)
    secondary_color: Optional[str] = Field(default=None, pattern=HEX)
    primary_size: Optional[int] = Field(default=None, ge=10, le=400)
    secondary_size: Optional[int] = Field(default=None, ge=10, le=400)
    h_align: Optional[Literal["left", "center", "right"]] = None
    v_align: Optional[Literal["top", "center", "bottom"]] = None
    text_shadow: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Navnet må ikke være tomt")
        return v


class SceneCreate(SceneFields):
    name: str = Field(default="Ny scene", max_length=80)


class SceneUpdate(SceneFields):
    pass


class SceneOrder(BaseModel):
    ids: list[int]


class TextOverride(BaseModel):
    """Tekst der lægges oven på scenen ved preview/VIS (fx fra en hurtigtekst)."""

    primary_text: Optional[str] = Field(default=None, max_length=200)
    secondary_text: Optional[str] = Field(default=None, max_length=300)
    quick_text_id: Optional[int] = None


class PublishPreview(BaseModel):
    preview_rev: int


class QuickTextIn(BaseModel):
    id: int
    primary_text: str = Field(default="", max_length=200)
    secondary_text: str = Field(default="", max_length=300)


class QuickTextsUpdate(BaseModel):
    items: list[QuickTextIn]


class AcquireRequest(BaseModel):
    label: str = Field(default="", max_length=60)


class HeartbeatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=100)


class SettingsUpdate(BaseModel):
    fallback_scene_id: Optional[int] = None
    heartbeat_interval: int = Field(ge=2, le=60)
    warning_after: int = Field(ge=5, le=3600)
    fallback_after: int = Field(ge=10, le=7200)
    auto_fallback: bool
    screen_format: Literal["9:16", "16:9", "3:4", "4:3"]
    default_primary_size: int = Field(ge=10, le=400)
    default_secondary_size: int = Field(ge=10, le=400)

    @model_validator(mode="after")
    def _order(self):
        if not (self.heartbeat_interval * 2 <= self.warning_after < self.fallback_after):
            raise ValueError(
                "Kræver: heartbeat-interval × 2 ≤ advarselsgrænse < timeout før fallback"
            )
        return self
