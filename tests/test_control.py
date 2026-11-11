"""Tilstandsmaskinen isoleret, med falsk monotont ur."""

from datetime import datetime, timedelta, timezone

import pytest

from app.control import ControlBusy, ControlMachine, ControlState, SessionInvalid, Timing

from .conftest import FakeClock


@pytest.fixture
def m():
    return ControlMachine(clock=FakeClock(), timing=Timing(10, 30, 120, True))


def test_starts_in_fallback_without_session(m):
    assert m.state == ControlState.FALLBACK_ACTIVE
    assert m.session is None
    assert m.tick() is None  # intet at overvåge


def test_missing_heartbeat_warns_then_falls_back(m):
    s, _ = m.acquire("Mac", "admin")
    m.clock.advance(29)
    assert m.tick() is None
    m.clock.advance(1)
    t = m.tick()
    assert t.new == ControlState.CONTROL_WARNING and not t.show_fallback
    m.clock.advance(89)
    assert m.tick() is None
    m.clock.advance(1)
    t = m.tick()
    assert t.new == ControlState.FALLBACK_ACTIVE and t.show_fallback
    assert m.session is None
    with pytest.raises(SessionInvalid) as e:
        m.heartbeat(s.id)
    assert "Automatisk fallback" in e.value.message


def test_heartbeat_keeps_control_alive_and_recovers_warning(m):
    s, _ = m.acquire("Mac", "admin")
    for _ in range(50):  # godt over 120 s i alt, men heartbeat hvert 10. s
        m.clock.advance(10)
        m.heartbeat(s.id)
        assert m.tick() is None
    m.clock.advance(35)
    assert m.tick().new == ControlState.CONTROL_WARNING
    t = m.heartbeat(s.id)
    assert t.new == ControlState.CONTROL_ACTIVE and t.kind == "recovered"


def test_release_pauses_timeout_for_hours(m):
    s, _ = m.acquire("Mac", "admin")
    t = m.release(s.id)
    assert t.new == ControlState.CONTROL_RELEASED and not t.show_fallback
    m.clock.advance(6 * 3600)
    assert m.tick() is None
    assert m.state == ControlState.CONTROL_RELEASED
    with pytest.raises(SessionInvalid):
        m.heartbeat(s.id)  # skal aktivt overtages igen


def test_takeover_requires_force_and_invalidates_old_session(m):
    old, _ = m.acquire("Mac", "admin")
    with pytest.raises(ControlBusy):
        m.acquire("Windows", "admin")
    new, t = m.acquire("Windows", "admin", force=True)
    assert t.kind == "takeover" and new.id != old.id
    with pytest.raises(SessionInvalid) as e:
        m.heartbeat(old.id)
    assert "overtaget" in e.value.message
    # Den gamle sessions heartbeat kan ikke forhindre fallback:
    m.clock.advance(119)
    with pytest.raises(SessionInvalid):
        m.heartbeat(old.id)
    m.clock.advance(1)
    assert m.tick().new == ControlState.FALLBACK_ACTIVE


def test_takeover_starts_new_heartbeat_period(m):
    m.acquire("Mac", "admin")
    m.clock.advance(100)
    m.acquire("Windows", "admin", force=True)
    m.clock.advance(100)
    assert m.tick().new == ControlState.CONTROL_WARNING  # 100 s, ikke 200 s
    assert m.state != ControlState.FALLBACK_ACTIVE


def test_acquire_after_fallback_needs_no_force(m):
    m.acquire("Mac", "admin")
    m.clock.advance(200)
    m.tick()
    s, t = m.acquire("Mac", "admin")
    assert t.kind == "acquire" and m.state == ControlState.CONTROL_ACTIVE


def test_auto_fallback_disabled_only_warns(m):
    m.timing.auto_fallback = False
    m.acquire("Mac", "admin")
    m.clock.advance(10_000)
    assert m.tick().new == ControlState.CONTROL_WARNING
    assert m.tick() is None
    assert m.state == ControlState.CONTROL_WARNING


def test_wall_clock_changes_do_not_matter():
    clock = FakeClock()
    wall = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    m = ControlMachine(clock=clock, wallclock=lambda: wall["t"], timing=Timing(10, 30, 120, True))
    s, _ = m.acquire("Mac", "admin")
    wall["t"] += timedelta(days=3)  # systemuret springer frem
    assert m.tick() is None
    wall["t"] -= timedelta(days=30)  # og tilbage
    clock.advance(5)
    m.heartbeat(s.id)
    assert m.tick() is None and m.state == ControlState.CONTROL_ACTIVE


def test_manual_fallback_keeps_session(m):
    s, _ = m.acquire("Mac", "admin")
    t = m.fallback_now("admin")
    assert t.show_fallback and m.state == ControlState.CONTROL_ACTIVE
    m.heartbeat(s.id)
    m.release(s.id)
    t = m.fallback_now("admin")
    assert m.state == ControlState.FALLBACK_ACTIVE
