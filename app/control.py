"""Tilstandsmaskine for kontrolsession og heartbeat-overvågning.

Ren logik uden I/O, så den kan testes med et falsk ur. Alle tider måles med
et monotont ur (`time.monotonic`), så ændringer af systemklokken ikke påvirker
overvågningen. Vægur-tidspunkter bruges kun til visning.

Tilstande:
  CONTROL_ACTIVE    – en kontrolsession sender heartbeat
  CONTROL_WARNING   – heartbeat er udeblevet længere end advarselsgrænsen
  CONTROL_RELEASED  – kontrollen er frigivet; ingen timeout, scenen bliver stående
  FALLBACK_ACTIVE   – ingen kontrolsession; fallback-scenen vises (efter timeout,
                      ved opstart eller ved manuel fallback uden kontrol)
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from .models import iso, utcnow


class ControlState(str, Enum):
    CONTROL_ACTIVE = "CONTROL_ACTIVE"
    CONTROL_WARNING = "CONTROL_WARNING"
    CONTROL_RELEASED = "CONTROL_RELEASED"
    FALLBACK_ACTIVE = "FALLBACK_ACTIVE"


STATE_LABELS = {
    ControlState.CONTROL_ACTIVE: "Aktiv kontrol – heartbeat modtages",
    ControlState.CONTROL_WARNING: "Forbindelsesadvarsel – heartbeat mangler",
    ControlState.CONTROL_RELEASED: "Kontrol frigivet – scenen fastholdes uden timeout",
    ControlState.FALLBACK_ACTIVE: "Fallback aktiv – ingen kontrolsession",
}


class ControlError(Exception):
    status_code = 409
    code = "control_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ControlBusy(ControlError):
    code = "control_busy"


class SessionInvalid(ControlError):
    code = "session_invalid"


@dataclass
class Timing:
    heartbeat_interval: int = 10
    warning_after: int = 30
    fallback_after: int = 120
    auto_fallback: bool = True


@dataclass
class ControlSession:
    id: str
    label: str
    user: str
    acquired_at: datetime
    last_heartbeat: float  # monotont
    last_heartbeat_at: datetime  # vægur, kun til visning


@dataclass
class Transition:
    old: ControlState
    new: ControlState
    kind: str
    message: str
    level: str = "info"
    show_fallback: bool = False


@dataclass
class ControlMachine:
    clock: Callable[[], float] = time.monotonic
    wallclock: Callable[[], datetime] = utcnow
    timing: Timing = field(default_factory=Timing)

    def __post_init__(self) -> None:
        # Kendt, forudsigelig opstartstilstand: fallback og ingen kontrol.
        self.state = ControlState.FALLBACK_ACTIVE
        self.session: Optional[ControlSession] = None
        self.reason = "Systemet er startet – fallback-scenen vises, indtil en tekniker overtager kontrollen."
        self.reason_at = self.wallclock()
        self.last_heartbeat_at: Optional[datetime] = None
        # Afsluttede sessioner husker hvorfor, så en gammel fane får en forklaring.
        self._ended: OrderedDict[str, str] = OrderedDict()

    # ------------------------------------------------------------ hjælpere

    def _set(self, new: ControlState, kind: str, message: str, level: str = "info",
             show_fallback: bool = False) -> Transition:
        old = self.state
        self.state = new
        self.reason = message
        self.reason_at = self.wallclock()
        return Transition(old, new, kind, message, level, show_fallback)

    def _end_session(self, why: str) -> None:
        if self.session is not None:
            self._ended[self.session.id] = why
            while len(self._ended) > 50:
                self._ended.popitem(last=False)
        self.session = None

    def seconds_since_heartbeat(self) -> Optional[float]:
        if self.session is None:
            return None
        return max(0.0, self.clock() - self.session.last_heartbeat)

    def validate(self, session_id: Optional[str]) -> ControlSession:
        """Kun den aktive kontrolsession må styre. Gamle sessioner afvises."""
        if not session_id:
            raise SessionInvalid("Du har ikke kontrollen. Tryk 'Overtag kontrol' først.")
        if self.session is None or not secrets.compare_digest(self.session.id, session_id):
            why = self._ended.get(session_id)
            raise SessionInvalid(why or "Kontrolsessionen er ikke længere gyldig. Overtag kontrollen igen.")
        return self.session

    # ------------------------------------------------------------ handlinger

    def acquire(self, label: str, user: str, force: bool = False) -> tuple[ControlSession, Transition]:
        """Overtag kontrollen. Holder en anden session kontrollen, kræves force (takeover)."""
        prev = self.session
        if prev is not None and not force:
            raise ControlBusy(f"Kontrollen holdes af '{prev.label}'. Bekræft overtagelse for at tage den.")
        label = (label or "").strip()[:60] or "Kontrolpanel"
        now_mono, now_wall = self.clock(), self.wallclock()
        if prev is not None:
            self._end_session(f"Kontrollen blev overtaget af '{label}'.")
        session = ControlSession(
            id=secrets.token_urlsafe(24), label=label, user=user,
            acquired_at=now_wall, last_heartbeat=now_mono, last_heartbeat_at=now_wall,
        )
        self.session = session
        self.last_heartbeat_at = now_wall
        if prev is not None:
            t = self._set(ControlState.CONTROL_ACTIVE, "takeover",
                          f"'{label}' overtog kontrollen fra '{prev.label}'.", "warning")
        else:
            t = self._set(ControlState.CONTROL_ACTIVE, "acquire", f"'{label}' overtog kontrollen.")
        return session, t

    def heartbeat(self, session_id: Optional[str]) -> Optional[Transition]:
        session = self.validate(session_id)
        session.last_heartbeat = self.clock()
        session.last_heartbeat_at = self.wallclock()
        self.last_heartbeat_at = session.last_heartbeat_at
        if self.state == ControlState.CONTROL_WARNING:
            return self._set(ControlState.CONTROL_ACTIVE, "recovered",
                             f"Heartbeat fra '{session.label}' er genoprettet.")
        return None

    def release(self, session_id: Optional[str]) -> Transition:
        session = self.validate(session_id)
        self._end_session(f"Kontrollen blev frigivet af '{session.label}'.")
        return self._set(ControlState.CONTROL_RELEASED, "release",
                         f"'{session.label}' frigav kontrollen. Scenen fastholdes uden timeout.")

    def fallback_now(self, who: str) -> Transition:
        """Manuel fallback. Kontrolsessionen bevares, hvis der er en."""
        if self.session is not None:
            return Transition(self.state, self.state, "fallback_manual",
                              f"Fallback vist manuelt af '{who}'.", "warning", True)
        return self._set(ControlState.FALLBACK_ACTIVE, "fallback_manual",
                         f"Fallback vist manuelt af '{who}'.", "warning", True)

    def tick(self) -> Optional[Transition]:
        """Kaldes jævnligt af backendens watchdog – uafhængigt af browsere."""
        if self.session is None or self.state not in (ControlState.CONTROL_ACTIVE, ControlState.CONTROL_WARNING):
            return None
        elapsed = self.clock() - self.session.last_heartbeat
        t = self.timing
        if t.auto_fallback and elapsed >= t.fallback_after:
            label = self.session.label
            msg = (f"Automatisk fallback: intet heartbeat fra '{label}' i {int(elapsed)} s "
                   f"(grænse {t.fallback_after} s).")
            self._end_session(msg)
            return self._set(ControlState.FALLBACK_ACTIVE, "fallback_timeout", msg, "error", True)
        if self.state == ControlState.CONTROL_ACTIVE and elapsed >= t.warning_after:
            return self._set(ControlState.CONTROL_WARNING, "warning",
                             f"Advarsel: intet heartbeat fra '{self.session.label}' i {int(elapsed)} s.",
                             "warning")
        return None

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        s = self.session
        since = self.seconds_since_heartbeat()
        t = self.timing
        remaining = None
        if s is not None and t.auto_fallback and self.state != ControlState.CONTROL_RELEASED:
            remaining = max(0, round(t.fallback_after - (since or 0)))
        return {
            "state": self.state.value,
            "label": STATE_LABELS[self.state],
            "reason": self.reason,
            "reason_at": iso(self.reason_at),
            "holder": None if s is None else {
                "label": s.label,
                "user": s.user,
                "acquired_at": iso(s.acquired_at),
            },
            "last_heartbeat_at": iso(self.last_heartbeat_at),
            "seconds_since_heartbeat": None if since is None else round(since, 1),
            "seconds_to_fallback": remaining,
            "timing": {
                "heartbeat_interval": t.heartbeat_interval,
                "warning_after": t.warning_after,
                "fallback_after": t.fallback_after,
                "auto_fallback": t.auto_fallback,
            },
        }
