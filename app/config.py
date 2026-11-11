"""Konfiguration via miljøvariabler (se .env.example)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass
class Settings:
    data_dir: Path = Path("data")
    secret_key: str = ""
    admin_username: str = "admin"
    admin_password: str = "admin"
    session_max_age: int = 60 * 60 * 12
    public_display: bool = True
    public_monitor: bool = True
    max_upload_mb: int = 20
    seed_defaults: bool = True
    app_name: str = "Talerstol"
    # Hvor ofte backend kontrollerer heartbeat-timeout (sekunder).
    watchdog_interval: float = 1.0
    # Hvor ofte WebSocket-klienter får et ping, så døde forbindelser opdages.
    ping_interval: float = 10.0
    warnings: list[str] = field(default_factory=list)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "talerstol.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "uploads" / "thumbs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def resolve_secret_key(self) -> str:
        """Brug SECRET_KEY, ellers en nøgle gemt i datamappen, så login overlever genstart."""
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / "secret_key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        if key_file.exists():
            key = key_file.read_text().strip()
            if key:
                self.secret_key = key
                return key
        key = secrets.token_urlsafe(48)
        key_file.write_text(key)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        self.secret_key = key
        return key


def load_settings() -> Settings:
    env = os.environ
    s = Settings(
        data_dir=Path(env.get("DATA_DIR", "data")),
        secret_key=env.get("SECRET_KEY", "").strip(),
        admin_username=env.get("ADMIN_USERNAME", "admin").strip() or "admin",
        admin_password=env.get("ADMIN_PASSWORD", ""),
        session_max_age=int(env.get("SESSION_MAX_AGE", str(60 * 60 * 12))),
        public_display=_bool(env.get("PUBLIC_DISPLAY"), True),
        public_monitor=_bool(env.get("PUBLIC_MONITOR"), True),
        max_upload_mb=max(1, int(env.get("MAX_UPLOAD_MB", "20"))),
        seed_defaults=_bool(env.get("SEED_DEFAULTS"), True),
        app_name=env.get("APP_NAME", "Talerstol").strip() or "Talerstol",
    )
    if not s.admin_password:
        s.admin_password = "admin"
        s.warnings.append("ADMIN_PASSWORD er ikke sat – bruger standardadgangskoden 'admin'. Skift den i .env!")
    return s
