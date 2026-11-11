"""Accepttest fra kravspecifikationen og tillægget – gennem det rigtige API."""

from app.main import create_app

from .conftest import (
    FakeClock,
    acquire,
    client,
    make_settings,
    png_bytes,
    publish,
    scene_named,
    state,
)


def tick(app):
    return app.state.runtime.tick()


def test_startup_shows_fallback(anon):
    st = state(anon)
    assert st["control"]["state"] == "FALLBACK_ACTIVE"
    assert st["live"]["is_fallback"] and st["live"]["name"] == "Fallback – logo"
    assert anon.get("/display").status_code == 200
    assert anon.get("/monitor").status_code == 200


def test_preview_does_not_touch_live_and_vis_switches_everything(admin):
    acquire(admin)
    up = admin.post("/api/uploads", files={"file": ("bg.png", png_bytes(), "image/png")}).json()["upload"]
    formand = scene_named(admin, "Formand")
    admin.put(f"/api/scenes/{formand['id']}", json={"bg_image_id": up["id"]})

    before = state(admin)["live"]
    r = admin.post(f"/api/scenes/{formand['id']}/preview", json={"primary_text": "Mette Hansen"})
    st = r.json()["state"]
    assert st["live"] == before  # live uændret
    assert st["preview"]["content"]["primary_text"] == "Mette Hansen"

    r = admin.post("/api/preview/activate", json={"preview_rev": st["preview"]["rev"]})
    live = r.json()["state"]["live"]
    # Billede og tekst i samme snapshot – skiftes samlet.
    assert live["content"]["primary_text"] == "Mette Hansen"
    assert live["content"]["bg_image"]["id"] == up["id"]
    assert not live["is_fallback"]


def test_closed_browser_gives_warning_then_fallback_and_stays(app, admin, clock):
    acquire(admin)
    publish(admin, scene_named(admin, "Formand")["id"])
    # "Browseren lukkes": ingen flere heartbeats.
    clock.advance(30)
    tick(app)
    assert state(admin)["control"]["state"] == "CONTROL_WARNING"
    assert state(admin)["live"]["name"] == "Formand"
    clock.advance(90)
    tick(app)
    st = state(admin)
    assert st["control"]["state"] == "FALLBACK_ACTIVE"
    assert st["live"]["is_fallback"] and st["live"]["reason"] == "fallback_timeout"
    assert "Automatisk fallback" in st["control"]["reason"]
    events = admin.get("/api/events").json()["events"]
    assert any(e["kind"] == "fallback_timeout" for e in events)

    # Den gamle fane kan ikke længere sende heartbeat.
    r = admin.post("/api/control/heartbeat")
    assert r.status_code == 409 and r.json()["code"] == "session_invalid"

    # Teknikeren åbner kontrolpanelet igen og overtager: fallback bliver stående.
    acquire(admin)
    st = state(admin)
    assert st["control"]["state"] == "CONTROL_ACTIVE"
    assert st["live"]["is_fallback"]
    clock.advance(10)
    admin.post("/api/control/heartbeat")
    assert state(admin)["live"]["is_fallback"]
    # Først VIS skifter scenen.
    publish(admin, scene_named(admin, "Dirigent")["id"])
    assert state(admin)["live"]["name"] == "Dirigent"


def test_heartbeats_prevent_fallback(app, admin, clock):
    acquire(admin)
    publish(admin, scene_named(admin, "Formand")["id"])
    for _ in range(30):
        clock.advance(10)
        assert admin.post("/api/control/heartbeat").status_code == 200
        tick(app)
    st = state(admin)
    assert st["control"]["state"] == "CONTROL_ACTIVE" and st["live"]["name"] == "Formand"
    # Alias fra minimums-API'et virker også.
    assert admin.post("/api/heartbeat").status_code == 200


def test_release_keeps_scene_for_hours_and_requires_new_control(app, admin, clock):
    acquire(admin)
    publish(admin, scene_named(admin, "Formand")["id"])
    assert admin.post("/api/control/release").status_code == 200
    clock.advance(5 * 60)
    tick(app)
    clock.advance(4 * 3600)
    tick(app)
    st = state(admin)
    assert st["control"]["state"] == "CONTROL_RELEASED"
    assert st["live"]["name"] == "Formand"
    # Ingen publicering uden at overtage kontrollen igen.
    dirigent = scene_named(admin, "Dirigent")["id"]
    assert admin.post(f"/api/scenes/{dirigent}/activate").status_code == 409
    assert admin.post(f"/api/scenes/{dirigent}/preview").status_code == 409
    acquire(admin)
    assert state(admin)["live"]["name"] == "Formand"  # overtagelse ændrer ikke scenen
    publish(admin, dirigent)
    assert state(admin)["live"]["name"] == "Dirigent"


def test_takeover_by_second_machine(app, admin, admin2, clock):
    acquire(admin, "Mac")
    publish(admin, scene_named(admin, "Formand")["id"])
    r = admin2.post("/api/control/acquire", json={"label": "Windows"})
    assert r.status_code == 409 and r.json()["code"] == "control_busy"
    r = admin2.post("/api/control/takeover", json={"label": "Windows"})
    assert r.status_code == 200
    admin2.headers["X-Control-Session"] = r.json()["session_id"]
    st = state(admin2)
    assert st["control"]["holder"]["label"] == "Windows"
    assert st["live"]["name"] == "Formand"  # overtagelse ændrer ikke scenen

    dirigent = scene_named(admin, "Dirigent")["id"]
    for path in (f"/api/scenes/{dirigent}/preview", f"/api/scenes/{dirigent}/activate",
                 "/api/control/heartbeat", "/api/control/release"):
        r = admin.post(path)
        assert r.status_code == 409, path
        assert "overtaget" in r.json()["detail"]
    # Den nye session styrer heartbeat-overvågningen.
    clock.advance(120)
    tick(app)
    assert state(admin2)["control"]["state"] == "FALLBACK_ACTIVE"


def test_manual_fallback_button(admin):
    acquire(admin)
    publish(admin, scene_named(admin, "Formand")["id"])
    r = admin.post("/api/fallback/activate")
    st = r.json()["state"]
    assert st["live"]["is_fallback"] and st["live"]["reason"] == "fallback_manual"
    assert st["control"]["state"] == "CONTROL_ACTIVE"


def test_restart_keeps_data_but_starts_in_fallback(data_dir, clock):
    app1 = create_app(make_settings(data_dir), clock=clock)
    with client(app1) as c:
        up = c.post("/api/uploads", files={"file": ("logo.png", png_bytes(), "image/png")}).json()["upload"]
        r = c.post("/api/scenes", json={"name": "Gæst", "primary_text": "Ole", "logo_image_id": up["id"]})
        gid = r.json()["scene"]["id"]
        s = c.get("/api/settings").json()
        s.update(fallback_after=90, warning_after=40)
        s.pop("canvas"), s.pop("last_live_name"), s.pop("last_live_at")
        assert c.put("/api/settings", json=s).status_code == 200
        acquire(c)
        publish(c, gid)
        c.post("/api/control/release")
        assert state(c)["control"]["state"] == "CONTROL_RELEASED"
    app1.state.db.dispose()

    app2 = create_app(make_settings(data_dir), clock=FakeClock())
    with client(app2) as c:
        st = state(c)
        assert st["control"]["state"] == "FALLBACK_ACTIVE"  # frigivelse genoptages ikke
        assert st["live"]["is_fallback"]
        assert st["settings"]["last_live_name"] == "Gæst"
        assert any(s["name"] == "Gæst" for s in c.get("/api/scenes").json()["scenes"])
        assert c.get("/api/uploads").json()["uploads"][0]["id"] == up["id"]
        assert c.get(up["url"]).status_code == 200
        assert c.get("/api/settings").json()["fallback_after"] == 90


def test_background_watchdog_triggers_fallback_without_any_browser(data_dir):
    """Ingen kalder tick() – backendens egen opgave skal opdage timeout."""
    import time as _time

    clock = FakeClock()
    s = make_settings(data_dir)
    s.watchdog_interval = 0.02
    app = create_app(s, clock=clock)
    with client(app) as c:
        acquire(c)
        publish(c, scene_named(c, "Formand")["id"])
        clock.advance(121)
        deadline = _time.monotonic() + 3
        while _time.monotonic() < deadline and state(c)["control"]["state"] != "FALLBACK_ACTIVE":
            _time.sleep(0.02)
        st = state(c)
        assert st["control"]["state"] == "FALLBACK_ACTIVE"
        assert st["live"]["is_fallback"]
