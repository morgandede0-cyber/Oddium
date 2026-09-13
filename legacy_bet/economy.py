from __future__ import annotations

import sqlite3
import aiosqlite

from config import SETTINGS
from .database import Database, utcnow_iso


class EconomyAdapter:
    """Internal test economy with idempotent ledger operations.

    To plug into the existing Legacy economy later, keep these public methods:
    get_balance, debit, credit. `credit` is intentionally idempotent when the
    same (user, reason, reference) is supplied, preventing double settlement.
    """

    def __init__(self, db: Database):
        self.db = db

    async def ensure_user(self, user_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,?)",
            (user_id, SETTINGS.starting_balance),
        )

    async def get_balance(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.db.fetchone("SELECT balance FROM wallets WHERE user_id=?", (user_id,))
        return int(row["balance"])

    async def debit(self, user_id: int, amount: int, reason: str, reference: str) -> bool:
        if amount <= 0:
            return False
        await self.ensure_user(user_id)
        db = await self.db.connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT 1 FROM wallet_transactions WHERE user_id=? AND reason=? AND reference=?", (user_id, reason, reference))
            if await cur.fetchone():
                await db.rollback()
                return True
            cur = await db.execute("SELECT balance FROM wallets WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if row is None or int(row[0]) < amount:
                await db.rollback()
                return False
            await db.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (amount, user_id))
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, -amount, reason, reference, utcnow_iso()),
            )
            await db.commit()
            return True
        finally:
            await db.close()

    async def credit(self, user_id: int, amount: int, reason: str, reference: str) -> bool:
        if amount <= 0:
            return False
        await self.ensure_user(user_id)
        db = await self.db.connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT 1 FROM wallet_transactions WHERE user_id=? AND reason=? AND reference=?", (user_id, reason, reference))
            if await cur.fetchone():
                await db.rollback()
                return False
            await db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (amount, user_id))
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount,reason,reference,created_at) VALUES(?,?,?,?,?)",
                (user_id, amount, reason, reference, utcnow_iso()),
            )
            await db.commit()
            return True
        finally:
            await db.close()
