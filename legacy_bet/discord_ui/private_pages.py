from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

log = logging.getLogger(__name__)

@dataclass
class PrivatePage:
    message: discord.WebhookMessage
    created_at: datetime

_pages: dict[tuple[int, int], PrivatePage] = {}
_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _key(interaction: discord.Interaction) -> tuple[int, int]:
    return (interaction.guild_id or 0, interaction.user.id)


def _lock(key: tuple[int, int]) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock

async def _ack(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=False)

async def show(interaction: discord.Interaction, *, content: str | None = None,
               embed: discord.Embed | None = None, view: discord.ui.View | None = None,
               files: list[discord.File] | None = None,
               attachments: list[Any] | None = None) -> discord.WebhookMessage:
    """Open/replace the user's single navigation page.

    Panel buttons always acknowledge first. If an existing ephemeral page is still
    editable, it is edited. Otherwise a fresh ephemeral page is created. A failed
    stale page can therefore never make a panel button appear dead.
    """
    key = _key(interaction)
    async with _lock(key):
        await _ack(interaction)
        page = _pages.get(key)
        edit_attachments = attachments if attachments is not None else (files or [])
        if page is not None:
            try:
                await page.message.edit(content=content, embed=embed, view=view,
                                        attachments=edit_attachments)
                return page.message
            except Exception as exc:
                log.warning("Oddium private page stale for %s: %r; recreating", key, exc)
                _pages.pop(key, None)

        # followup.send(wait=True) is deliberately used after defer. This avoids
        # Interaction.original_response ambiguity on component interactions.
        msg = await interaction.followup.send(content=content, embed=embed, view=view,
                                              files=files or [], ephemeral=True, wait=True)
        _pages[key] = PrivatePage(message=msg, created_at=datetime.now(timezone.utc))
        return msg

async def forget(interaction: discord.Interaction) -> None:
    _pages.pop(_key(interaction), None)
