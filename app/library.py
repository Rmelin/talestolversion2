"""Scener, billeder, hurtigtekster, indstillinger og hændelseslog (databaseadgang)."""

from __future__ import annotations

import io
import logging
import secrets
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlmodel import col, select

from .db import Database
from .models import AppSettings, EventLog, QuickText, Scene, Upload, iso, utcnow
from .schemas import SceneCreate, SceneUpdate, SettingsUpdate

log = logging.getLogger("talerstol")

QUICK_TEXT_SLOTS = 8
FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "3:4": (1080, 1440), "4:3": (1440, 1080)}
# Kun rasterformater – SVG kan indeholde scripts og afvises.
ALLOWED_IMAGE_FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                         "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}
MAX_PIXELS = 40_000_000
THUMB_SIZE = (480, 480)


class LibraryError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


# ------------------------------------------------------------------ hændelseslog


def log_event(db: Database, kind: str, message: str, level: str = "info") -> None:
    getattr(log, "warning" if level == "warning" else "error" if level == "error" else "info")(
        "[%s] %s", kind, message)
    try:
        with db.write() as s:
            s.add(EventLog(kind=kind, message=message, level=level))
    except Exception:  # pragma: no cover - log må aldrig vælte en handling
        log.exception("Kunne ikke skrive hændelseslog")


def list_events(db: Database, limit: int = 200) -> list[dict[str, Any]]:
    with db.read() as s:
        rows = s.exec(select(EventLog).order_by(col(EventLog.id).desc()).limit(limit)).all()
    return [{"id": r.id, "at": iso(r.created_at), "level": r.level, "kind": r.kind,
             "message": r.message} for r in rows]


# ------------------------------------------------------------------ billeder


def upload_dict(u: Upload) -> dict[str, Any]:
    return {
        "id": u.id, "url": f"/media/{u.stored_name}", "thumb_url": f"/media/thumbs/{u.stored_name}",
        "original_name": u.original_name, "mime": u.mime, "width": u.width, "height": u.height,
        "size_bytes": u.size_bytes, "created_at": iso(u.created_at),
    }


def list_uploads(db: Database, limit: Optional[int] = None) -> list[dict[str, Any]]:
    with db.read() as s:
        q = select(Upload).order_by(col(Upload.id).desc())
        if limit:
            q = q.limit(limit)
        return [upload_dict(u) for u in s.exec(q).all()]


def _safe_display_name(name: str) -> str:
    name = Path(name or "billede").name  # fjern evt. stier
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"/\\|?*')
    return name.strip()[:120] or "billede"


def save_upload(db: Database, uploads_dir: Path, thumbs_dir: Path, original_name: str,
                data: bytes, max_bytes: int) -> dict[str, Any]:
    if not data:
        raise LibraryError("Filen er tom.")
    if len(data) > max_bytes:
        raise LibraryError(f"Filen er for stor (maks. {max_bytes // (1024 * 1024)} MB).", 413, "too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()  # fanger afkortede/ødelagte filer
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        if fmt == "MPO":
            # iPhone/kamera-JPEG med indlejrede ekstrabilleder – browsere viser det som JPEG.
            fmt = "JPEG"
        width, height = img.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise LibraryError("Filen er ikke et gyldigt billede (PNG, JPEG, WebP eller GIF).", 415, "bad_image")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise LibraryError("Kun PNG, JPEG, WebP og GIF er tilladt.", 415, "bad_image")
    if width * height > MAX_PIXELS:
        raise LibraryError("Billedet har for mange pixels.", 413, "too_large")

    ext, mime = ALLOWED_IMAGE_FORMATS[fmt]
    stored = f"{secrets.token_hex(12)}.{ext}"  # aldrig brugerens filnavn på disken
    uploads_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    target = uploads_dir / stored
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)

    try:
        thumb = ImageOps.exif_transpose(img)
        thumb.thumbnail(THUMB_SIZE)
        if fmt == "JPEG" and thumb.mode not in ("RGB", "L"):
            thumb = thumb.convert("RGB")
        thumb.save(thumbs_dir / stored, format=fmt if fmt != "GIF" else "PNG")
    except Exception:  # pragma: no cover - thumbnail er pænt at have
        log.exception("Kunne ikke lave thumbnail – bruger originalen")
        (thumbs_dir / stored).write_bytes(data)
    finally:
        img.close()

    with db.write() as s:
        u = Upload(stored_name=stored, original_name=_safe_display_name(original_name), mime=mime,
                   width=width, height=height, size_bytes=len(data))
        s.add(u)
        s.flush()
        s.refresh(u)
        result = upload_dict(u)
    log_event(db, "upload", f"Billede uploadet: {result['original_name']} ({width}×{height})")
    return result


def delete_upload(db: Database, uploads_dir: Path, thumbs_dir: Path, upload_id: int) -> None:
    with db.write() as s:
        u = s.get(Upload, upload_id)
        if u is None:
            raise LibraryError("Billedet findes ikke.", 404, "not_found")
        used = s.exec(select(Scene).where(
            (Scene.bg_image_id == upload_id) | (Scene.logo_image_id == upload_id))).all()
        if used:
            names = ", ".join(sc.name for sc in used[:5])
            raise LibraryError(f"Billedet bruges af scenen: {names}.", 409, "in_use")
        stored = u.stored_name
        s.delete(u)
    for p in (uploads_dir / stored, thumbs_dir / stored):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------ scener


def scene_dict(sc: Scene) -> dict[str, Any]:
    d = sc.model_dump()
    d["created_at"] = iso(sc.created_at)
    d["updated_at"] = iso(sc.updated_at)
    return d


def list_scenes(db: Database) -> list[dict[str, Any]]:
    with db.read() as s:
        rows = s.exec(select(Scene).order_by(col(Scene.sort_order), col(Scene.id))).all()
        return [scene_dict(r) for r in rows]


def get_scene(db: Database, scene_id: int) -> Scene:
    with db.read() as s:
        sc = s.get(Scene, scene_id)
    if sc is None:
        raise LibraryError("Scenen findes ikke.", 404, "not_found")
    return sc


def _check_images(s, data: dict[str, Any]) -> None:
    for key in ("bg_image_id", "logo_image_id"):
        if data.get(key) is not None and s.get(Upload, data[key]) is None:
            raise LibraryError("Det valgte billede findes ikke.", 400, "bad_image_ref")


def create_scene(db: Database, data: SceneCreate) -> dict[str, Any]:
    values = data.model_dump(exclude_none=True)
    with db.write() as s:
        _check_images(s, values)
        if "primary_size" not in values or "secondary_size" not in values:
            st = s.get(AppSettings, 1)
            if st:
                values.setdefault("primary_size", st.default_primary_size)
                values.setdefault("secondary_size", st.default_secondary_size)
        last = s.exec(select(Scene).order_by(col(Scene.sort_order).desc())).first()
        sc = Scene(**values, sort_order=(last.sort_order + 1) if last else 0)
        s.add(sc)
        s.flush()
        s.refresh(sc)
        return scene_dict(sc)


def update_scene(db: Database, scene_id: int, data: SceneUpdate) -> dict[str, Any]:
    values = data.model_dump(exclude_unset=True)
    # Billed-id'er må gerne sættes til null (fjern billede); andre felter ignoreres hvis null.
    values = {k: v for k, v in values.items() if v is not None or k in ("bg_image_id", "logo_image_id")}
    with db.write() as s:
        sc = s.get(Scene, scene_id)
        if sc is None:
            raise LibraryError("Scenen findes ikke.", 404, "not_found")
        _check_images(s, values)
        for k, v in values.items():
            setattr(sc, k, v)
        sc.updated_at = utcnow()
        s.add(sc)
        s.flush()
        s.refresh(sc)
        return scene_dict(sc)


def duplicate_scene(db: Database, scene_id: int) -> dict[str, Any]:
    with db.write() as s:
        sc = s.get(Scene, scene_id)
        if sc is None:
            raise LibraryError("Scenen findes ikke.", 404, "not_found")
        values = sc.model_dump(exclude={"id", "created_at", "updated_at", "sort_order"})
        values["name"] = (sc.name + " (kopi)")[:80]
        # Læg kopien lige efter originalen.
        for other in s.exec(select(Scene).where(Scene.sort_order > sc.sort_order)).all():
            other.sort_order += 1
            s.add(other)
        copy = Scene(**values, sort_order=sc.sort_order + 1)
        s.add(copy)
        s.flush()
        s.refresh(copy)
        return scene_dict(copy)


def delete_scene(db: Database, scene_id: int) -> None:
    with db.write() as s:
        sc = s.get(Scene, scene_id)
        if sc is None:
            raise LibraryError("Scenen findes ikke.", 404, "not_found")
        st = s.get(AppSettings, 1)
        if st and st.fallback_scene_id == scene_id:
            raise LibraryError("Scenen er valgt som fallback og kan ikke slettes. Vælg en anden fallback først.",
                               409, "is_fallback")
        s.delete(sc)


def reorder_scenes(db: Database, ids: list[int]) -> None:
    with db.write() as s:
        scenes = {sc.id: sc for sc in s.exec(select(Scene)).all()}
        order = [i for i in ids if i in scenes] + [i for i in scenes if i not in ids]
        for pos, sid in enumerate(order):
            scenes[sid].sort_order = pos
            s.add(scenes[sid])


def render_scene(db: Database, scene_id: int, override: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Byg et komplet, selvstændigt sceneindhold til display (inkl. billed-URL'er)."""
    with db.read() as s:
        sc = s.get(Scene, scene_id)
        if sc is None:
            raise LibraryError("Scenen findes ikke.", 404, "not_found")
        bg = s.get(Upload, sc.bg_image_id) if sc.bg_image_id else None
        logo = s.get(Upload, sc.logo_image_id) if sc.logo_image_id else None
    content = scene_dict(sc)
    content["bg_image"] = upload_dict(bg) if bg else None
    content["logo_image"] = upload_dict(logo) if logo else None
    if override:
        for key in ("primary_text", "secondary_text"):
            if override.get(key) is not None:
                content[key] = override[key]
    return content


def required_files(content: dict[str, Any]) -> list[str]:
    files = []
    for key in ("bg_image", "logo_image"):
        img = content.get(key)
        if img and (key != "logo_image" or content.get("show_logo")):
            files.append(img["url"].rsplit("/", 1)[-1])
    return files


# ------------------------------------------------------------------ hurtigtekster


def list_quick_texts(db: Database) -> list[dict[str, Any]]:
    with db.read() as s:
        rows = s.exec(select(QuickText).order_by(col(QuickText.slot))).all()
        return [{"id": r.id, "slot": r.slot, "primary_text": r.primary_text,
                 "secondary_text": r.secondary_text, "updated_at": iso(r.updated_at)} for r in rows]


def get_quick_text(db: Database, qt_id: int) -> QuickText:
    with db.read() as s:
        qt = s.get(QuickText, qt_id)
    if qt is None:
        raise LibraryError("Hurtigteksten findes ikke.", 404, "not_found")
    return qt


def save_quick_texts(db: Database, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with db.write() as s:
        for it in items:
            qt = s.get(QuickText, it["id"])
            if qt is None:
                raise LibraryError("Hurtigteksten findes ikke.", 404, "not_found")
            qt.primary_text = it["primary_text"].strip()
            qt.secondary_text = it["secondary_text"].strip()
            qt.updated_at = utcnow()
            s.add(qt)
    return list_quick_texts(db)


# ------------------------------------------------------------------ indstillinger


def get_settings(db: Database) -> AppSettings:
    with db.read() as s:
        st = s.get(AppSettings, 1)
    return st or AppSettings()


def settings_dict(st: AppSettings) -> dict[str, Any]:
    w, h = FORMATS.get(st.screen_format, FORMATS["9:16"])
    return {
        "fallback_scene_id": st.fallback_scene_id,
        "heartbeat_interval": st.heartbeat_interval,
        "warning_after": st.warning_after,
        "fallback_after": st.fallback_after,
        "auto_fallback": st.auto_fallback,
        "screen_format": st.screen_format,
        "canvas": {"width": w, "height": h},
        "default_primary_size": st.default_primary_size,
        "default_secondary_size": st.default_secondary_size,
        "last_live_name": st.last_live_name,
        "last_live_at": iso(st.last_live_at),
    }


def update_settings(db: Database, data: SettingsUpdate) -> AppSettings:
    with db.write() as s:
        st = s.get(AppSettings, 1) or AppSettings(id=1)
        if data.fallback_scene_id is not None and s.get(Scene, data.fallback_scene_id) is None:
            raise LibraryError("Fallback-scenen findes ikke.", 400, "bad_scene")
        for k, v in data.model_dump().items():
            setattr(st, k, v)
        st.updated_at = utcnow()
        s.add(st)
        s.flush()
        s.refresh(st)
        return st


def remember_live(db: Database, name: str) -> None:
    with db.write() as s:
        st = s.get(AppSettings, 1)
        if st:
            st.last_live_name = name[:120]
            st.last_live_at = utcnow()
            s.add(st)


# ------------------------------------------------------------------ opstart


def bootstrap(db: Database, seed: bool) -> None:
    """Opret tabeller, indstillinger, hurtigtekst-pladser og (første gang) standardscener."""
    db.create_all()
    with db.write() as s:
        st = s.get(AppSettings, 1)
        if st is None:
            st = AppSettings(id=1)
            s.add(st)
        existing = {q.slot for q in s.exec(select(QuickText)).all()}
        for slot in range(1, QUICK_TEXT_SLOTS + 1):
            if slot not in existing:
                s.add(QuickText(slot=slot))
        has_scenes = s.exec(select(Scene)).first() is not None
        if seed and not has_scenes:
            fallback = Scene(name="Fallback – logo", sort_order=0, bg_color="#0b1d3a",
                             primary_text="", secondary_text="", show_primary=False,
                             show_secondary=False, show_logo=True, logo_position="center", logo_size=55)
            s.add(fallback)
            s.add(Scene(name="Formand", sort_order=1, primary_text="Navn Navnesen",
                        secondary_text="Formand", v_align="bottom"))
            s.add(Scene(name="Dirigent", sort_order=2, primary_text="Navn Navnesen",
                        secondary_text="Dirigent", v_align="bottom"))
            s.flush()
            s.refresh(fallback)
            st.fallback_scene_id = fallback.id
            s.add(st)
