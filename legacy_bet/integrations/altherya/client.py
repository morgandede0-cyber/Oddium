from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger("oddium.altherya")


class AltheryaBridgeError(RuntimeError):
    pass


class AltheryaBridgeClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 4.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.enabled:
            raise AltheryaBridgeError("Pont Altherya non configuré")
        last: Exception | None = None
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout, headers=self._headers()) as session:
                    async with session.request(method, f"{self.base_url}{path}", **kwargs) as response:
                        data = await response.json(content_type=None)
                        if response.status >= 400 and response.status != 409:
                            raise AltheryaBridgeError(f"Altherya HTTP {response.status}: {data}")
                        return data
            except (aiohttp.ClientError, asyncio.TimeoutError, AltheryaBridgeError) as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
        raise AltheryaBridgeError(str(last))

    async def get_balance(self, user_id: int) -> int:
        data = await self._request("GET", f"/v1/gold/{int(user_id)}")
        return int(data["balance"])

    async def mutate(self, user_id: int, amount: int, reason: str, reference: str) -> tuple[bool, int]:
        data = await self._request("POST", "/v1/gold/transaction", json={
            "user_id": int(user_id), "amount": int(amount), "reason": str(reason), "reference": str(reference),
        })
        return bool(data.get("ok")), int(data.get("balance", 0))

    async def emit(self, event: dict) -> None:
        try:
            await self._request("POST", "/v1/events", json=event)
        except Exception:
            # Discord logging must never invalidate an already accepted bet/settlement.
            log.exception("Événement Altherya non publié: %s", event.get("type"))
