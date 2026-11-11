"""Talerstol v2 – FastAPI-applikation.

Raspberry Pi'en er både server og afviklingsmaskine. Backend er autoritativ:
tilstandsskift (heartbeat, advarsel, fallback) styres af en selvstændig
watchdog-opgave her – ikke af JavaScript i kontrolbrowseren.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import library as lib
from .auth import (
    check_credentials,
    control_session_id,
    current_user,
    require_admin,
    require_client_header,
    safe_next,
)
from .config import Settings, load_settings
from .control import ControlError, ControlMachine
from .db import Database
from .models import iso, utcnow
from .realtime import Connection, ConnectionManager
from .runtime import Runtime
from .schemas import (
    AcquireRequest,
    HeartbeatRequest,
    PublishPreview,
    QuickTextsUpdate,
    SceneCreate,
    SceneOrder,
    SceneUpdate,
    SettingsUpdate,
    TextOverride,
)

log = logging.getLogger("talerstol")

BASE_DIR = Path(__file__).resolve().parent
VERSION = "2.0.0"


def create_app(settings: Optional[Settings] = None, clock: Optional[Callable[[], float]] = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.thumbs_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.database_path)
    lib.bootstrap(db, seed=settings.seed_defaults)
    machine = ControlMachine(clock=clock or time.monotonic)
    runtime = Runtime(db, settings.uploads_dir, machine)

    displays = ConnectionManager()
    admins = ConnectionManager()
    monitors = ConnectionManager()
    runtime.display_count = lambda: displays.count

    started_mono = time.monotonic()
    started_at = utcnow()
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    for w in settings.warnings:
        log.warning(w)

    # ------------------------------------------------------------ udsendelse

    def broadcast_state(rt: Runtime) -> None:
        if displays.count:
            displays.broadcast(rt.snapshot("display"))
        if admins.count or monitors.count:
            snap = rt.snapshot("admin")
            admins.broadcast(snap)
            monitors.broadcast(snap)

    runtime.listeners.append(broadcast_state)

    def library_changed(what: str) -> None:
        admins.broadcast({"type": "library", "what": what})

    # Kendt opstartstilstand: fallback-scenen og ingen kontrolsession.
    runtime.boot()

    async def watchdog() -> None:
        """Overvåger heartbeat uafhængigt af om nogen browser er forbundet."""
        while True:
            await asyncio.sleep(settings.watchdog_interval)
            try:
                runtime.tick()
            except Exception:  # pragma: no cover - watchdog må aldrig dø
                log.exception("Fejl i watchdog")

    async def status_pulse() -> None:
        """Hyppig status til kontrolpaneler/monitorer og ping til displays."""
        n = 0
        while True:
            await asyncio.sleep(2)
            n += 1
            try:
                if admins.count or monitors.count:
                    msg = {"type": "control", "control": runtime.control_status(),
                           "server_time": iso(utcnow()), "displays": displays.count}
                    admins.broadcast(msg)
                    monitors.broadcast(msg)
                if displays.count and n % 5 == 0:
                    displays.broadcast({"type": "ping", "server_time": iso(utcnow())})
            except Exception:  # pragma: no cover
                log.exception("Fejl i status-puls")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = [asyncio.create_task(watchdog()), asyncio.create_task(status_pulse())]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            lib.log_event(db, "shutdown", "Systemet lukker ned.")
            db.dispose()

    app = FastAPI(title="Talerstol v2", version=VERSION, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.db = db
    app.state.runtime = runtime
    app.state.settings = settings
    app.state.managers = {"display": displays, "admin": admins, "monitor": monitors}

    app.add_middleware(SessionMiddleware, secret_key=settings.resolve_secret_key(),
                       session_cookie="talerstol_session", max_age=settings.session_max_age,
                       same_site="lax", https_only=False)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.mount("/media", StaticFiles(directory=str(settings.uploads_dir)), name="media")

    @app.exception_handler(ControlError)
    async def _control_error(_r: Request, exc: ControlError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status_code)

    @app.exception_handler(lib.LibraryError)
    async def _lib_error(_r: Request, exc: lib.LibraryError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status_code)

    def page(request: Request, template: str, name: str, **extra: Any) -> HTMLResponse:
        ctx = {"app_name": settings.app_name, "page": name, "user": current_user(request),
               "version": VERSION, **extra}
        return templates.TemplateResponse(request, template, ctx)

    def to_login(next_url: str) -> RedirectResponse:
        return RedirectResponse(f"/login?next={next_url}", status_code=303)

    admin = Depends(require_admin)
    guard = [Depends(require_client_header)]

    def optional_public(flag: bool):
        def dep(request: Request) -> None:
            if not flag:
                require_admin(request)
        return Depends(dep)

    # ------------------------------------------------------------ sider

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return page(request, "index.html", "index",
                    public_display=settings.public_display, public_monitor=settings.public_monitor)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/admin"):
        return page(request, "login.html", "login", next=safe_next(next), error=None)

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                           next: str = Form("/admin")):
        if not check_credentials(settings, username, password):
            await asyncio.sleep(0.6)  # bremser gætteri
            lib.log_event(db, "login_failed", f"Mislykket login for '{username[:40]}'", "warning")
            resp = page(request, "login.html", "login", next=safe_next(next),
                        error="Forkert brugernavn eller adgangskode.")
            resp.status_code = 401
            return resp
        request.session.clear()
        request.session["user"] = settings.admin_username
        return RedirectResponse(safe_next(next), status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        if not current_user(request):
            return to_login("/admin")
        return page(request, "admin.html", "admin", max_upload_mb=settings.max_upload_mb)

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        if not current_user(request):
            return to_login("/admin/settings")
        return page(request, "settings.html", "settings")

    @app.get("/display", response_class=HTMLResponse)
    async def display_page(request: Request):
        if not settings.public_display and not current_user(request):
            return to_login("/display")
        return page(request, "display.html", "display")

    @app.get("/monitor", response_class=HTMLResponse)
    async def monitor_page(request: Request):
        if not settings.public_monitor and not current_user(request):
            return to_login("/monitor")
        return page(request, "monitor.html", "monitor")

    # ------------------------------------------------------------ system

    @app.get("/api/health")
    def health():
        db.ping()
        return {"status": "ok", "state": runtime.machine.state.value, "version": VERSION}

    @app.get("/api/state", dependencies=[optional_public(settings.public_monitor)])
    def get_state():
        return runtime.snapshot("admin")

    @app.get("/api/system/status", dependencies=[optional_public(settings.public_monitor)])
    def system_status():
        disk = shutil.disk_usage(settings.data_dir)
        return {
            "version": VERSION,
            "started_at": iso(started_at),
            "uptime_seconds": round(time.monotonic() - started_mono),
            "control": runtime.control_status(),
            "connections": {"display": displays.count, "admin": admins.count, "monitor": monitors.count},
            "database": "ok" if db.ping() else "fejl",
            "disk_free_mb": disk.free // (1024 * 1024),
            "uploads": len(lib.list_uploads(db)),
            "scenes": len(lib.list_scenes(db)),
            "warnings": settings.warnings,
            "live": runtime.live.to_dict() if runtime.live else None,
        }

    @app.get("/api/events", dependencies=[admin])
    def events(limit: int = 200):
        return {"events": lib.list_events(db, max(1, min(limit, 2000)))}

    # ------------------------------------------------------------ kontrol

    @app.get("/api/control/status", dependencies=[optional_public(settings.public_monitor)])
    def control_status():
        return runtime.control_status()

    @app.post("/api/control/acquire", dependencies=guard)
    def control_acquire(body: AcquireRequest, user: str = admin):
        """Overtag kontrollen. Fejler med 409, hvis en anden session har den (brug takeover)."""
        sid = runtime.acquire(body.label, user, force=False)
        return {"session_id": sid, "state": runtime.snapshot("admin")}

    @app.post("/api/control/takeover", dependencies=guard)
    def control_takeover(body: AcquireRequest, user: str = admin):
        """Overtag kontrollen fra en anden maskine. Den gamle sessions heartbeat ugyldiggøres."""
        sid = runtime.acquire(body.label, user, force=True)
        return {"session_id": sid, "state": runtime.snapshot("admin")}

    @app.post("/api/control/release", dependencies=guard)
    def control_release(request: Request, _user: str = admin):
        runtime.release(control_session_id(request))
        return {"state": runtime.snapshot("admin")}

    @app.post("/api/control/heartbeat", dependencies=guard)
    def control_heartbeat(request: Request, body: Optional[HeartbeatRequest] = None, _user: str = admin):
        sid = (body.session_id if body else None) or control_session_id(request)
        runtime.heartbeat(sid)
        return {"ok": True, "control": runtime.control_status()}

    # Alias fra kravspecifikationens minimums-API.
    app.post("/api/heartbeat", dependencies=guard, include_in_schema=True,
             summary="Alias for /api/control/heartbeat")(control_heartbeat)

    @app.post("/api/fallback/activate", dependencies=guard)
    def fallback_activate(user: str = admin):
        """Vis fallback nu. Tilladt for alle indloggede – det er altid en sikker handling."""
        runtime.fallback_now(user)
        return {"state": runtime.snapshot("admin")}

    # ------------------------------------------------------------ scener

    @app.get("/api/scenes", dependencies=[admin])
    def scenes():
        return {"scenes": lib.list_scenes(db)}

    @app.post("/api/scenes", status_code=201, dependencies=[admin, *guard])
    def scene_create(body: SceneCreate):
        scene = lib.create_scene(db, body)
        library_changed("scenes")
        return {"scene": scene}

    @app.put("/api/scenes/order", dependencies=[admin, *guard])
    def scene_order(body: SceneOrder):
        lib.reorder_scenes(db, body.ids)
        library_changed("scenes")
        return {"scenes": lib.list_scenes(db)}

    @app.get("/api/scenes/{scene_id}", dependencies=[admin])
    def scene_get(scene_id: int):
        return {"scene": lib.scene_dict(lib.get_scene(db, scene_id))}

    @app.put("/api/scenes/{scene_id}", dependencies=[admin, *guard])
    def scene_update(scene_id: int, body: SceneUpdate):
        scene = lib.update_scene(db, scene_id, body)
        runtime.refresh_preview_for(scene_id)  # live påvirkes ikke
        library_changed("scenes")
        return {"scene": scene}

    @app.delete("/api/scenes/{scene_id}", dependencies=[admin, *guard])
    def scene_delete(scene_id: int):
        lib.delete_scene(db, scene_id)
        runtime.clear_preview_for(scene_id)
        library_changed("scenes")
        return {"deleted": scene_id}

    @app.post("/api/scenes/{scene_id}/duplicate", status_code=201, dependencies=[admin, *guard])
    def scene_duplicate(scene_id: int):
        scene = lib.duplicate_scene(db, scene_id)
        library_changed("scenes")
        return {"scene": scene}

    @app.post("/api/scenes/{scene_id}/preview", dependencies=guard)
    def scene_preview(scene_id: int, request: Request, body: Optional[TextOverride] = None,
                      user: str = admin):
        """Klargør en scene (evt. med hurtigtekst). Live påvirkes ikke. Kræver kontrol."""
        runtime.set_preview(control_session_id(request), scene_id,
                            body.model_dump() if body else None, user)
        return {"state": runtime.snapshot("admin")}

    @app.post("/api/scenes/{scene_id}/activate", dependencies=guard)
    def scene_activate(scene_id: int, request: Request, body: Optional[TextOverride] = None,
                       user: str = admin):
        """Vis en scene direkte på talerstolen. Kræver kontrol."""
        runtime.activate_scene(control_session_id(request), scene_id,
                               body.model_dump() if body else None, user)
        return {"state": runtime.snapshot("admin")}

    @app.post("/api/preview/activate", dependencies=guard)
    def preview_activate(body: PublishPreview, request: Request, user: str = admin):
        """VIS: aktivér præcis den klargjorte scene (preview_rev skal matche)."""
        runtime.publish_preview(control_session_id(request), body.preview_rev, user)
        return {"state": runtime.snapshot("admin")}

    # ------------------------------------------------------------ billeder

    @app.get("/api/uploads", dependencies=[admin])
    def uploads(limit: Optional[int] = None):
        return {"uploads": lib.list_uploads(db, limit)}

    @app.post("/api/uploads", status_code=201, dependencies=[admin, *guard])
    async def upload_create(request: Request, file: UploadFile = File(...)):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes + 64 * 1024:
            raise lib.LibraryError(f"Filen er for stor (maks. {settings.max_upload_mb} MB).", 413, "too_large")
        data = await file.read(settings.max_upload_bytes + 1)
        result = await asyncio.to_thread(lib.save_upload, db, settings.uploads_dir, settings.thumbs_dir,
                                         file.filename or "", data, settings.max_upload_bytes)
        library_changed("uploads")
        return {"upload": result}

    @app.delete("/api/uploads/{upload_id}", dependencies=[admin, *guard])
    def upload_delete(upload_id: int):
        lib.delete_upload(db, settings.uploads_dir, settings.thumbs_dir, upload_id)
        library_changed("uploads")
        return {"deleted": upload_id}

    # ------------------------------------------------------------ hurtigtekster

    @app.get("/api/quicktexts", dependencies=[admin])
    def quicktexts():
        return {"quick_texts": lib.list_quick_texts(db)}

    @app.put("/api/quicktexts", dependencies=[admin, *guard])
    def quicktexts_save(body: QuickTextsUpdate):
        items = lib.save_quick_texts(db, [i.model_dump() for i in body.items])
        library_changed("quicktexts")
        return {"quick_texts": items}

    # ------------------------------------------------------------ indstillinger

    @app.get("/api/settings", dependencies=[admin])
    def settings_get():
        return lib.settings_dict(lib.get_settings(db))

    @app.put("/api/settings", dependencies=[admin, *guard])
    def settings_put(body: SettingsUpdate, user: str = admin):
        st = lib.update_settings(db, body)
        runtime.apply_settings()
        lib.log_event(db, "settings", f"Indstillinger gemt af {user}: heartbeat {st.heartbeat_interval} s, "
                                      f"advarsel {st.warning_after} s, fallback {st.fallback_after} s, "
                                      f"automatisk fallback {'til' if st.auto_fallback else 'FRA'}.")
        broadcast_state(runtime)
        return lib.settings_dict(st)

    # ------------------------------------------------------------ WebSocket

    async def serve_ws(ws: WebSocket, manager: ConnectionManager, audience: str) -> None:
        await ws.accept()
        conn = Connection(ws)
        conn.offer(json.dumps(runtime.snapshot(audience), ensure_ascii=False))
        manager.add(conn)
        if audience == "display":
            broadcast_state(runtime)  # kontrolpaneler ser antal displays

        async def reader() -> None:
            # Kanalen er envejs – vi læser kun for at opdage afbrydelser.
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return

        writer_task = asyncio.create_task(conn.writer())
        reader_task = asyncio.create_task(reader())
        try:
            _done, pending = await asyncio.wait({writer_task, reader_task},
                                                return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        finally:
            manager.remove(conn)
            if audience == "display":
                broadcast_state(runtime)
            try:
                await ws.close()
            except Exception:
                pass

    @app.websocket("/ws/display")
    async def ws_display(ws: WebSocket):
        if not settings.public_display and not current_user(ws):
            await ws.close(code=4401)
            return
        await serve_ws(ws, displays, "display")

    @app.websocket("/ws/admin")
    async def ws_admin(ws: WebSocket):
        if not current_user(ws):
            await ws.close(code=4401)
            return
        await serve_ws(ws, admins, "admin")

    @app.websocket("/ws/monitor")
    async def ws_monitor(ws: WebSocket):
        if not settings.public_monitor and not current_user(ws):
            await ws.close(code=4401)
            return
        await serve_ws(ws, monitors, "monitor")

    return app


def build() -> FastAPI:
    """Indgang for uvicorn:  uvicorn --factory app.main:build"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
