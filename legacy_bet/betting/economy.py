from __future__ import annotations

from ..storage.database import Database, utcnow_iso
from ..integrations.altherya.client import AltheryaBridgeClient, AltheryaBridgeError


class EconomyAdapter:
    """Oddium economy backed by Altherya's authoritative ``wallet_gold``.

    Oddium keeps only its idempotency/audit ledger. It never maintains an
    independent spendable balance when the Altherya bridge is configured.
    """

    def __init__(self, db: Database, bridge: AltheryaBridgeClient):
        self.db = db
        self.bridge = bridge

    async def ensure_user(self, user_id: int) -> None:
        # Kept for schema/backward compatibility; spendable Gold lives in Altherya.
        await self.db.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,0)", (user_id,))

    async def get_balance(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        if not self.bridge.enabled:
            raise AltheryaBridgeError("Gold indisponible : pont Altherya non configuré")
        return await self.bridge.get_balance(user_id)

    async def _already_recorded(self, user_id: int, reason: str, reference: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM wallet_transactions WHERE user_id=? AND reason=? AND reference=?",
            (user_id, reason, reference),
        )
        return bool(row)

    async def _record(self, user_id: int, amount: int, reason: str, reference: str) -> None:
        try:
            await self.db.execute(
                "INSERT INTO wallet_transactions(user_id,amount,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, amount, reason, reference, utcnow_iso()),
            )
        except Exception:
            # Remote operation is idempotent; retrying the business operation later
            # safely repairs a crash between Altherya commit and local audit insert.
            if not await self._already_recorded(user_id, reason, reference):
                raise

    async def debit(self, user_id: int, amount: int, reason: str, reference: str) -> bool:
        if amount <= 0:
            return False
        await self.ensure_user(user_id)
        if await self._already_recorded(user_id, reason, reference):
            return True
        ok, _ = await self.bridge.mutate(user_id, -int(amount), reason, reference)
        if not ok:
            return False
        await self._record(user_id, -int(amount), reason, reference)
        return True

    async def credit(self, user_id: int, amount: int, reason: str, reference: str) -> bool:
        if amount <= 0:
            return False
        await self.ensure_user(user_id)
        if await self._already_recorded(user_id, reason, reference):
            return False
        ok, _ = await self.bridge.mutate(user_id, int(amount), reason, reference)
        if not ok:
            return False
        await self._record(user_id, int(amount), reason, reference)
        return True
