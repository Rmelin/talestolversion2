"""Sikkerhed, billeder, scener, indstillinger og WebSocket."""

from .conftest import acquire, png_bytes, publish, scene_named, scenes, state


# ------------------------------------------------------------ sikkerhed

def test_admin_requires_login(anon):
    assert anon.get("/admin", follow_redirects=False).status_code == 303
    for method, path in [("get", "/api/scenes"), ("post", "/api/scenes"), ("post", "/api/control/acquire"),
                         ("post", "/api/control/heartbeat"), ("post", "/api/fallback/activate"),
                         ("put", "/api/settings"), ("get", "/api/uploads"), ("get", "/api/events")]:
        r = anon.request(method.upper(), path, json={} if method != "get" else None)
        assert r.status_code in (401, 422), (path, r.status_code)
        assert r.status_code == 401 or path == "/api/settings"


def test_wrong_password_rejected(anon):
    r = anon.post("/login", data={"username": "admin", "password": "forkert", "next": "/admin"},
                  follow_redirects=False)
    assert r.status_code == 401


def test_write_requires_client_header(admin):
    del admin.headers["X-Talerstol-Client"]
    assert admin.post("/api/control/acquire", json={}).status_code == 400
    assert admin.post("/api/scenes", json={"name": "x"}).status_code == 400


def test_open_redirect_blocked(anon):
    r = anon.post("/login", data={"username": "admin", "password": "test-pw", "next": "//evil.example"},
                  follow_redirects=False)
    assert r.headers["location"] == "/admin"


def test_private_display_and_monitor(data_dir, clock):
    from app.main import create_app
    from .conftest import client, make_settings
    s = make_settings(data_dir)
    s.public_display = False
    s.public_monitor = False
    app = create_app(s, clock=clock)
    with client(app, login=False) as c:
        assert c.get("/display", follow_redirects=False).status_code == 303
        assert c.get("/monitor", follow_redirects=False).status_code == 303
        assert c.get("/api/state").status_code == 401


# ------------------------------------------------------------ billeder

def test_upload_valid_image_gets_safe_name_and_thumb(admin):
    r = admin.post("/api/uploads", files={"file": ("../../etc/<evil>.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    up = r.json()["upload"]
    assert "/" not in up["url"].removeprefix("/media/")
    assert ".." not in up["url"] and "evil" not in up["url"]
    assert up["original_name"] == "evil.png"
    assert (up["width"], up["height"]) == (64, 32)
    assert admin.get(up["url"]).status_code == 200
    assert admin.get(up["thumb_url"]).status_code == 200


def test_upload_rejects_non_images_svg_and_large_files(admin):
    r = admin.post("/api/uploads", files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 415
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert admin.post("/api/uploads", files={"file": ("x.svg", svg, "image/svg+xml")}).status_code == 415
    big = png_bytes() + b"\0" * (1024 * 1024 + 10)  # maks. 1 MB i tests
    assert admin.post("/api/uploads", files={"file": ("big.png", big, "image/png")}).status_code == 413
    assert admin.post("/api/uploads", files={"file": ("tom.png", b"", "image/png")}).status_code == 400


def test_upload_in_use_cannot_be_deleted(admin):
    up = admin.post("/api/uploads", files={"file": ("a.png", png_bytes(), "image/png")}).json()["upload"]
    sid = scenes(admin)[1]["id"]
    admin.put(f"/api/scenes/{sid}", json={"bg_image_id": up["id"]})
    assert admin.delete(f"/api/uploads/{up['id']}").status_code == 409
    admin.put(f"/api/scenes/{sid}", json={"bg_image_id": None})
    assert admin.delete(f"/api/uploads/{up['id']}").status_code == 200


def test_missing_image_file_blocks_activation(app, admin):
    acquire(admin)
    up = admin.post("/api/uploads", files={"file": ("a.png", png_bytes(), "image/png")}).json()["upload"]
    sid = scene_named(admin, "Formand")["id"]
    admin.put(f"/api/scenes/{sid}", json={"bg_image_id": up["id"]})
    (app.state.settings.uploads_dir / up["url"].rsplit("/", 1)[-1]).unlink()
    before = state(admin)["live"]
    r = admin.post(f"/api/scenes/{sid}/activate")
    assert r.status_code == 409 and r.json()["code"] == "missing_file"
    assert state(admin)["live"] == before


# ------------------------------------------------------------ scener

def test_scene_crud_duplicate_order(admin):
    r = admin.post("/api/scenes", json={"name": "Gæst", "primary_text": "Ole", "primary_color": "#ff0000"})
    assert r.status_code == 201
    sid = r.json()["scene"]["id"]
    assert admin.put(f"/api/scenes/{sid}", json={"name": "Gæstetaler"}).json()["scene"]["name"] == "Gæstetaler"
    assert admin.put(f"/api/scenes/{sid}", json={"primary_color": "rød"}).status_code == 422
    dup = admin.post(f"/api/scenes/{sid}/duplicate").json()["scene"]
    assert dup["name"] == "Gæstetaler (kopi)" and dup["primary_text"] == "Ole"
    ids = [s["id"] for s in scenes(admin)]
    assert ids.index(dup["id"]) == ids.index(sid) + 1
    new_order = list(reversed(ids))
    assert [s["id"] for s in admin.put("/api/scenes/order", json={"ids": new_order}).json()["scenes"]] == new_order
    assert admin.delete(f"/api/scenes/{dup['id']}").status_code == 200
    assert admin.get(f"/api/scenes/{dup['id']}").status_code == 404


def test_editing_scene_does_not_affect_live_but_refreshes_preview(admin):
    acquire(admin)
    sid = scene_named(admin, "Formand")["id"]
    publish(admin, sid)
    admin.post(f"/api/scenes/{sid}/preview")
    admin.put(f"/api/scenes/{sid}", json={"primary_text": "Ny formand"})
    st = state(admin)
    assert st["live"]["content"]["primary_text"] == "Navn Navnesen"
    assert st["preview"]["content"]["primary_text"] == "Ny formand"


def test_stale_preview_rejected(admin):
    acquire(admin)
    sid = scene_named(admin, "Formand")["id"]
    rev = admin.post(f"/api/scenes/{sid}/preview").json()["state"]["preview"]["rev"]
    admin.put(f"/api/scenes/{sid}", json={"primary_text": "Ændret"})
    r = admin.post("/api/preview/activate", json={"preview_rev": rev})
    assert r.status_code == 409 and r.json()["code"] == "preview_changed"


def test_fallback_scene_cannot_be_deleted(admin):
    fb = state(admin)["settings"]["fallback_scene_id"]
    assert admin.delete(f"/api/scenes/{fb}").status_code == 409


def test_quick_texts_save_and_preview(admin):
    qts = admin.get("/api/quicktexts").json()["quick_texts"]
    assert len(qts) >= 5
    items = [{"id": q["id"], "primary_text": f"Navn {q['slot']}", "secondary_text": "Titel"} for q in qts]
    saved = admin.put("/api/quicktexts", json={"items": items}).json()["quick_texts"]
    assert saved[2]["primary_text"] == "Navn 3"
    acquire(admin)
    sid = scene_named(admin, "Formand")["id"]
    st = admin.post(f"/api/scenes/{sid}/preview", json={"quick_text_id": qts[2]["id"]}).json()["state"]
    assert st["preview"]["content"]["primary_text"] == "Navn 3"
    assert st["preview"]["quick_text_id"] == qts[2]["id"]


# ------------------------------------------------------------ indstillinger

def test_settings_validation_and_apply(app, admin):
    s = admin.get("/api/settings").json()
    for k in ("canvas", "last_live_name", "last_live_at"):
        s.pop(k)
    bad = dict(s, warning_after=200, fallback_after=100)
    assert admin.put("/api/settings", json=bad).status_code == 422
    good = dict(s, heartbeat_interval=5, warning_after=20, fallback_after=60, screen_format="16:9")
    r = admin.put("/api/settings", json=good)
    assert r.status_code == 200 and r.json()["canvas"] == {"width": 1920, "height": 1080}
    assert app.state.runtime.machine.timing.fallback_after == 60


def test_system_status_and_health(anon):
    assert anon.get("/api/health").json()["status"] == "ok"
    s = anon.get("/api/system/status").json()
    assert s["control"]["state"] == "FALLBACK_ACTIVE" and s["database"] == "ok"


# ------------------------------------------------------------ WebSocket

def test_display_websocket_gets_live_and_updates(admin):
    with admin.websocket_connect("/ws/display") as ws:
        first = ws.receive_json()
        assert first["type"] == "live" and first["live"]["is_fallback"]
        assert "control" not in first  # displayet får kun det, det skal vise
        acquire(admin)
        publish(admin, scene_named(admin, "Formand")["id"])
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "live" and msg["live"]["name"] == "Formand":
                break
        else:
            raise AssertionError("display fik ikke den nye scene")


def test_admin_websocket_requires_login(anon):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws/admin") as ws:
            ws.receive_json()


def test_upload_accepts_iphone_mpo_jpeg(admin):
    """iPhone-JPEG'er er ofte MPO (JPEG med ekstra billeder) og skal accepteres som JPEG."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    frames = [Image.new("RGB", (80, 40), c) for c in ((200, 0, 0), (0, 200, 0))]
    frames[0].save(buf, format="MPO", save_all=True, append_images=frames[1:])
    assert Image.open(io.BytesIO(buf.getvalue())).format == "MPO"
    r = admin.post("/api/uploads", files={"file": ("IMG_5791.jpeg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 201, r.text
    up = r.json()["upload"]
    assert up["mime"] == "image/jpeg" and up["url"].endswith(".jpg")
    assert admin.get(up["thumb_url"]).status_code == 200
