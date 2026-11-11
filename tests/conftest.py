from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app

USER, PASS = "admin", "test-pw"
HEADERS = {"X-Talerstol-Client": "1"}


class FakeClock:
    """Monotont testur, der kun flytter sig, når testen beder om det."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir, secret_key="test-secret", admin_username=USER,
                    admin_password=PASS, watchdog_interval=3600, max_upload_mb=1)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def app(data_dir, clock):
    return create_app(make_settings(data_dir), clock=clock)


def client(app, login: bool = True) -> TestClient:
    c = TestClient(app)
    c.headers.update(HEADERS)
    if login:
        r = c.post("/login", data={"username": USER, "password": PASS, "next": "/admin"},
                   follow_redirects=False)
        assert r.status_code == 303, r.text
    return c


@pytest.fixture
def admin(app):
    with client(app) as c:
        yield c


@pytest.fixture
def admin2(app):
    with client(app) as c:
        yield c


@pytest.fixture
def anon(app):
    with client(app, login=False) as c:
        yield c


def acquire(c: TestClient, label: str = "Mac") -> str:
    r = c.post("/api/control/acquire", json={"label": label})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    c.headers["X-Control-Session"] = sid
    return sid


def scenes(c: TestClient) -> list[dict]:
    return c.get("/api/scenes").json()["scenes"]


def scene_named(c: TestClient, name: str) -> dict:
    return next(s for s in scenes(c) if s["name"] == name)


def publish(c: TestClient, scene_id: int, **override) -> dict:
    r = c.post(f"/api/scenes/{scene_id}/preview", json=override)
    assert r.status_code == 200, r.text
    rev = r.json()["state"]["preview"]["rev"]
    r = c.post("/api/preview/activate", json={"preview_rev": rev})
    assert r.status_code == 200, r.text
    return r.json()["state"]


def state(c: TestClient) -> dict:
    return c.get("/api/state").json()


def png_bytes(size=(64, 32), color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()
