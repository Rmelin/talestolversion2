"""SQLite i WAL-mode via SQLModel. Alle skrivninger serialiseres med en lås."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )

        @event.listens_for(self.engine, "connect")
        def _on_connect(dbapi_conn, _record):  # pragma: no cover - opsætning
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=FULL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

        self._lock = threading.RLock()

    def create_all(self) -> None:
        from . import models  # noqa: F401  (registrerer tabeller)

        SQLModel.metadata.create_all(self.engine)

    @contextmanager
    def read(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as s:
            yield s

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self._lock:
            with Session(self.engine, expire_on_commit=False) as s:
                yield s
                s.commit()

    def ping(self) -> bool:
        with self.read() as s:
            s.connection().exec_driver_sql("SELECT 1")
        return True

    def dispose(self) -> None:
        self.engine.dispose()
