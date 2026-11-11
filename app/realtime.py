"""WebSocket-forbindelser og udsendelse af tilstand.

WebSocket-kanalen er kun til at skubbe data ud. Alle ændringer sker via
REST-API'et, hvor rettigheder kontrolleres. Hver forbindelse har sin egen
kø og skrivetråd, så en langsom skærm ikke forsinker de andre.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("talerstol.realtime")

QUEUE_SIZE = 20


class Connection:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)

    def offer(self, message: str) -> bool:
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    async def writer(self) -> None:
        while True:
            message = await self.queue.get()
            if message is None:
                return
            await self.ws.send_text(message)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[Connection] = set()

    @property
    def count(self) -> int:
        return len(self.connections)

    def add(self, conn: Connection) -> None:
        self.connections.add(conn)

    def remove(self, conn: Connection) -> None:
        self.connections.discard(conn)

    def broadcast_text(self, message: str) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for conn in list(self.connections):
            if conn.loop is current:
                self._deliver(conn, message)
            else:
                # Forbindelsen tilhører en anden event loop (f.eks. i tests).
                conn.loop.call_soon_threadsafe(self._deliver, conn, message)

    def _deliver(self, conn: Connection, message: str) -> None:
        if conn not in self.connections:
            return
        if not conn.offer(message):
            # Klienten hænger: luk den. Den genforbinder og får et frisk snapshot.
            log.warning("WebSocket-klient for langsom – afbrydes")
            self.remove(conn)
            try:
                conn.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            conn.queue.put_nowait(None)

    def broadcast(self, payload: dict[str, Any]) -> None:
        self.broadcast_text(json.dumps(payload, ensure_ascii=False))
