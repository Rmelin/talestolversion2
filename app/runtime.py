"""Afviklingstilstand: aktiv (live) og klargjort (preview) scene + kontrolsession.

Runtime samler tilstandsmaskinen og scenevisningen. Alle ændringer sker under
én lås og udsendes bagefter som et samlet snapshot, så displayet altid får
hele scenen på én gang (aldrig et nyt navn på et gammelt baggrundsbillede).
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import library as lib
from .control import ControlError, ControlMachine, ControlState, Timing, Transition
from .db import Database
from .models import iso, utcnow


@dataclass
class Shown:
    """Et frosset sceneindhold. Ændringer i databasen påvirker det ikke."""

    rev: int
    scene_id: Optional[int]
    name: str
    content: dict[str, Any]
    reason: str  # manual | fallback_timeout | fallback_manual | startup
    at: datetime = field(default_factory=utcnow)
    by: str = ""
    quick_text_id: Optional[int] = None
    override: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"rev": self.rev, "scene_id": self.scene_id, "name": self.name,
                "content": self.content, "reason": self.reason, "at": iso(self.at),
                "by": self.by, "is_fallback": self.reason.startswith("fallback") or self.reason == "startup",
                "quick_text_id": self.quick_text_id}


EMPTY_CONTENT: dict[str, Any] = {
    "name": "Tom", "bg_color": "#000000", "bg_image": None, "bg_fit": "cover", "logo_image": None,
    "show_logo": False, "logo_size": 40, "logo_position": "center", "primary_text": "",
    "secondary_text": "", "show_primary": False, "show_secondary": False, "primary_color": "#ffffff",
    "secondary_color": "#ffffff", "primary_size": 100, "secondary_size": 60, "h_align": "center",
    "v_align": "center", "text_shadow": False,
}


class Runtime:
    def __init__(self, db: Database, uploads_dir: Path, machine: Optional[ControlMachine] = None):
        self.db = db
        self.uploads_dir = uploads_dir
        self.machine = machine or ControlMachine()
        self.lock = threading.RLock()
        self.boot_id = secrets.token_hex(6)
        self.rev = 0
        self.live: Optional[Shown] = None
        self.preview: Optional[Shown] = None
        self.preview_rev = 0
        self.listeners: list[Callable[["Runtime"], None]] = []
        self.display_count: Callable[[], int] = lambda: 0

    # ------------------------------------------------------------ opstart

    def boot(self) -> None:
        with self.lock:
            self.apply_settings()
            self._show_fallback("startup", "system")
        lib.log_event(self.db, "startup", "Systemet er startet. Fallback-scenen vises.")

    def apply_settings(self) -> None:
        st = lib.get_settings(self.db)
        with self.lock:
            self.machine.timing = Timing(st.heartbeat_interval, st.warning_after,
                                         st.fallback_after, st.auto_fallback)

    # ------------------------------------------------------------ hjælpere

    def _bump(self) -> None:
        self.rev += 1

    def _notify(self) -> None:
        for fn in list(self.listeners):
            fn(self)

    def _record(self, t: Optional[Transition]) -> None:
        if t is not None:
            lib.log_event(self.db, t.kind, t.message, t.level)

    def _fallback_content(self) -> tuple[Optional[int], str, dict[str, Any]]:
        st = lib.get_settings(self.db)
        if st.fallback_scene_id:
            try:
                content = lib.render_scene(self.db, st.fallback_scene_id)
                if all((self.uploads_dir / f).exists() for f in lib.required_files(content)):
                    return st.fallback_scene_id, content["name"], content
            except lib.LibraryError:
                pass
        # Sidste udvej: en neutral, mørk skærm. Kræver ingen filer.
        return None, "Fallback (standard)", dict(EMPTY_CONTENT)

    def _show_fallback(self, reason: str, by: str) -> None:
        scene_id, name, content = self._fallback_content()
        self._bump()
        self.live = Shown(self.rev, scene_id, name, content, reason, by=by)
        if reason != "startup":  # bevar oplysningen om sidste live-scene før genstart
            lib.remember_live(self.db, name)
        lib.log_event(self.db, "scene", f"Live: {name} (fallback)")

    def _build(self, scene_id: int, override: Optional[dict[str, Any]]) -> tuple[dict[str, Any], Optional[int]]:
        quick_id = None
        ov: dict[str, Any] = {}
        if override and override.get("quick_text_id"):
            qt = lib.get_quick_text(self.db, override["quick_text_id"])
            quick_id = qt.id
            ov = {"primary_text": qt.primary_text, "secondary_text": qt.secondary_text}
        for key in ("primary_text", "secondary_text"):
            if override and override.get(key) is not None:
                ov[key] = override[key]
        return lib.render_scene(self.db, scene_id, ov), quick_id

    def _ensure_files(self, content: dict[str, Any]) -> None:
        missing = [f for f in lib.required_files(content) if not (self.uploads_dir / f).exists()]
        if missing:
            raise lib.LibraryError("Et billede i scenen mangler på talerstolen. Scenen blev IKKE vist.",
                                   409, "missing_file")

    # ------------------------------------------------------------ kontrol

    def acquire(self, label: str, user: str, force: bool = False) -> str:
        with self.lock:
            session, t = self.machine.acquire(label, user, force=force)
            self._bump()
        self._record(t)
        self._notify()
        return session.id

    def heartbeat(self, session_id: Optional[str]) -> None:
        with self.lock:
            t = self.machine.heartbeat(session_id)
            if t:
                self._bump()
        if t:
            self._record(t)
            self._notify()

    def release(self, session_id: Optional[str]) -> None:
        with self.lock:
            t = self.machine.release(session_id)
            self._bump()
        self._record(t)
        self._notify()

    def tick(self) -> Optional[Transition]:
        with self.lock:
            t = self.machine.tick()
            if t is None:
                return None
            if t.show_fallback:
                self._show_fallback("fallback_timeout", "watchdog")
            self._bump()
        self._record(t)
        self._notify()
        return t

    def fallback_now(self, who: str) -> None:
        with self.lock:
            t = self.machine.fallback_now(who)
            self._show_fallback("fallback_manual", who)
        self._record(t)
        self._notify()

    # ------------------------------------------------------------ scener

    def set_preview(self, session_id: Optional[str], scene_id: int,
                    override: Optional[dict[str, Any]], who: str) -> None:
        content, quick_id = self._build(scene_id, override)
        with self.lock:
            self.machine.heartbeat(session_id)
            self.preview_rev += 1
            self._bump()
            kept = {k: v for k, v in (override or {}).items()
                    if k in ("primary_text", "secondary_text") and v is not None}
            self.preview = Shown(self.preview_rev, scene_id, content["name"], content, "manual",
                                 by=who, quick_text_id=quick_id, override=kept)
        self._notify()

    def refresh_preview_for(self, scene_id: int) -> None:
        """Når en scene gemmes, opdateres et preview af samme scene (live forbliver uændret)."""
        with self.lock:
            p = self.preview
            if p is None or p.scene_id != scene_id:
                return
            override: dict[str, Any] = dict(p.override)
            if p.quick_text_id:
                override["quick_text_id"] = p.quick_text_id
        try:
            content, _ = self._build(scene_id, override)
        except lib.LibraryError:
            return
        with self.lock:
            if self.preview is not p:
                return
            self.preview_rev += 1
            self._bump()
            p.content, p.name, p.rev = content, content["name"], self.preview_rev
        self._notify()

    def clear_preview_for(self, scene_id: int) -> None:
        with self.lock:
            if self.preview and self.preview.scene_id == scene_id:
                self.preview = None
                self._bump()
        self._notify()

    def _go_live(self, shown: Shown, who: str) -> None:
        self._ensure_files(shown.content)
        self._bump()
        self.live = Shown(self.rev, shown.scene_id, shown.name, shown.content, "manual",
                          by=who, quick_text_id=shown.quick_text_id)

    def publish_preview(self, session_id: Optional[str], preview_rev: int, who: str) -> None:
        """VIS: den klargjorte scene aktiveres samlet."""
        with self.lock:
            self.machine.heartbeat(session_id)
            if self.preview is None:
                raise lib.LibraryError("Der er ingen klargjort scene.", 409, "no_preview")
            if self.preview.rev != preview_rev:
                raise lib.LibraryError("Preview er ændret siden du så det. Kontrollér og tryk VIS igen.",
                                       409, "preview_changed")
            self._go_live(self.preview, who)
            name, text = self.live.name, self.live.content.get("primary_text", "")  # type: ignore[union-attr]
            self._mark_control_active()
        lib.remember_live(self.db, name)
        lib.log_event(self.db, "scene", f"Live: {name}" + (f" – {text}" if text else "") + f" (af {who})")
        self._notify()

    def activate_scene(self, session_id: Optional[str], scene_id: int,
                       override: Optional[dict[str, Any]], who: str) -> None:
        content, quick_id = self._build(scene_id, override)
        with self.lock:
            self.machine.heartbeat(session_id)
            self._go_live(Shown(0, scene_id, content["name"], content, "manual", quick_text_id=quick_id), who)
            self._mark_control_active()
        lib.remember_live(self.db, content["name"])
        lib.log_event(self.db, "scene", f"Live: {content['name']} (af {who})")
        self._notify()

    def _mark_control_active(self) -> None:
        # En VIS beviser at kontrolsessionen er i live.
        if self.machine.state == ControlState.CONTROL_WARNING:
            self.machine.state = ControlState.CONTROL_ACTIVE

    # ------------------------------------------------------------ snapshots

    def control_status(self) -> dict[str, Any]:
        with self.lock:
            return self.machine.status()

    def snapshot(self, audience: str = "admin") -> dict[str, Any]:
        with self.lock:
            st = lib.settings_dict(lib.get_settings(self.db))
            base = {
                "boot_id": self.boot_id,
                "rev": self.rev,
                "server_time": iso(utcnow()),
                "canvas": st["canvas"],
                "live": self.live.to_dict() if self.live else None,
            }
            if audience == "display":
                return {"type": "live", **base}
            base.update({
                "type": "state",
                "preview": self.preview.to_dict() if self.preview else None,
                "control": self.machine.status(),
                "settings": st,
                "displays": self.display_count(),
            })
            return base


__all__ = ["Runtime", "Shown", "ControlError"]
