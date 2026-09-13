from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import ClientSession, WSMsgType, web

from config import SETTINGS

log = logging.getLogger("oddium.livews")


class LocalLiveWebSocket:
    """Local event bus exposed as a real WebSocket.

    The football sources are still HTTP because free providers do not expose a
    reliable permanent push feed.  The collector converts score/status changes
    into push events.  Discord therefore reacts to events instead of refreshing
    the panel on a fixed timer.
    """

    def __init__(self):
        self._clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def url(self) -> str:
        return f"ws://{SETTINGS.live_ws_host}:{SETTINGS.live_ws_port}{SETTINGS.live_ws_path}"

    async def start(self) -> None:
        if self._runner is not None:
            return
        app = web.Application()
        app.router.add_get(SETTINGS.live_ws_path, self._handle_ws)
        app.router.add_get("/health", self._health)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, SETTINGS.live_ws_host, SETTINGS.live_ws_port)
        await self._site.start()
        log.info("WebSocket live Oddium actif sur %s", self.url)

    async def close(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "clients": len(self._clients), "websocket": self.url})

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        self._clients.add(ws)
        try:
            await ws.send_json({"type": "hello", "source": "Oddium Live", "version": "8.8"})
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "ping":
                    await ws.send_str("pong")
                elif msg.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        finally:
            self._clients.discard(ws)
        return ws

    async def publish(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._clients):
            try:
                await ws.send_str(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


async def consume_local_live(on_event) -> None:
    """Persistent local client. Reconnects automatically if the socket restarts."""
    url = f"ws://{SETTINGS.live_ws_host}:{SETTINGS.live_ws_port}{SETTINGS.live_ws_path}"
    async with ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(url, heartbeat=25) as ws:
                    log.info("Panneau Discord connecté au WebSocket Oddium Live")
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            try:
                                payload = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            if payload.get("type") not in {None, "hello"}:
                                await on_event(payload)
                        elif msg.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("WebSocket local indisponible (%s), reconnexion...", exc)
                await asyncio.sleep(2)
