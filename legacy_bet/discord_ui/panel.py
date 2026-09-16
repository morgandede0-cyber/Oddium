from __future__ import annotations

from pathlib import Path
import asyncio

import discord

from ..betting.service import BettingService
from ..core.constants import COMPETITIONS
from ..discord_ui.ui import MainPanelView, LivePanelView, build_title_embed, carousel_keys, CAROUSEL_ASSETS, _live_embed

ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"


class PanelManager:
    def __init__(self, bot: discord.Client, service: BettingService):
        self.bot = bot
        self.service = service
        self._live_cleanup_done = False
        self._live_panel_lock = asyncio.Lock()

    async def _state(self):
        active = await self.service.active_competitions()
        keys = carousel_keys(active)
        selected = await self.service.db.get_setting("panel_carousel_key")
        if selected not in keys and keys:
            selected = keys[0]
            await self.service.db.set_setting("panel_carousel_key", selected)
        return active, keys, selected


    def _carousel_file(self, selected: str | None) -> discord.File | None:
        filename = CAROUSEL_ASSETS.get(selected or "")
        if not filename:
            return None
        path = ASSET_DIR / filename
        if not path.exists():
            return None
        return discord.File(path, filename=filename)

    async def ensure_panel(self, channel: discord.TextChannel) -> discord.Message:
        active = await self.service.active_competitions()
        embed = await build_title_embed(self.service)
        view = MainPanelView(self.service, active)
        image_path = ASSET_DIR / "oddium_welcome.png"
        message_id = await self.service.db.get_setting("panel_message_id")
        msg = None
        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                present = [a.filename for a in msg.attachments]
                if image_path.exists() and present != ["oddium_welcome.png"]:
                    await msg.edit(embed=embed, view=view, attachments=[discord.File(image_path, filename="oddium_welcome.png")])
                else:
                    await msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden):
                msg = None
        if msg is None:
            files = [discord.File(image_path, filename="oddium_welcome.png")] if image_path.exists() else []
            msg = await channel.send(embed=embed, view=view, files=files)
            await self.service.db.set_setting("panel_channel_id", channel.id)
            await self.service.db.set_setting("panel_message_id", msg.id)
        try:
            if not msg.pinned:
                await msg.pin(reason="Écran d'accueil Oddium")
        except discord.Forbidden:
            pass
        return msg

    async def refresh_existing_panel(self):
        """Refresh the manually installed main panel without ever creating one.

        Only /setup is allowed to create/install the panel.  Automatic loops and
        reconnects may edit the exact stored message, but if it was deleted they
        leave the channel untouched instead of silently recreating duplicates.
        """
        channel_id = await self.service.db.get_setting("panel_channel_id")
        message_id = await self.service.db.get_setting("panel_message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return
        active = await self.service.active_competitions()
        embed = await build_title_embed(self.service)
        view = MainPanelView(self.service, active)
        image_path = ASSET_DIR / "oddium_welcome.png"
        present = [a.filename for a in msg.attachments]
        if image_path.exists() and present != ["oddium_welcome.png"]:
            await msg.edit(embed=embed, view=view, attachments=[discord.File(image_path, filename="oddium_welcome.png")])
        else:
            await msg.edit(embed=embed, view=view)


    @staticmethod
    def _display_clock(row, phase: str) -> str:
        """Return only a trustworthy, phase-compatible provider clock.

        Kickoff-based timer estimation is deliberately disabled: pauses, late
        kickoffs and provider delays made those estimates drift badly. A clock is
        shown only when a provider supplied a plausible football minute.
        """
        phase = str(phase or "").strip().lower()
        # Never display a minute during a stopped/terminal phase.
        if phase in {"halftime", "half_time", "ht", "finished", "ft", "postponed", "cancelled", "suspended", "kickoff_wait"}:
            return ""

        raw = str(row["live_clock"] or "").strip()
        if not raw:
            return ""
        token = raw.upper().replace(" ", "")
        if token in {"HT", "MT", "HALF", "HALFTIME", "FT", "FULL", "FULLTIME"}:
            return ""

        # Keep only provider clocks containing a real minute.
        base = token.split("+", 1)[0]
        digits = "".join(ch for ch in base if ch.isdigit())
        if not digits:
            return ""
        try:
            minute = int(digits)
        except ValueError:
            return ""

        # Reject clocks that contradict the phase.
        if phase == "first_half" and not (0 <= minute <= 45):
            return ""
        if phase == "second_half" and not (46 <= minute <= 90):
            return ""
        if phase == "extra_time" and not (91 <= minute <= 120):
            return ""
        if phase == "penalties":
            return ""
        if phase == "live" and not (0 <= minute <= 120):
            return ""
        return raw

    async def build_live_embed(self) -> discord.Embed:
        """Build the single canonical Oddium live presentation."""
        return await _live_embed(self.service)

    async def _find_existing_live_panel(self, channel: discord.TextChannel):
        """Find an existing Oddium live board when DB state was lost after a redeploy."""
        if not self.bot.user:
            return None, []
        found = []
        try:
            async for old_msg in channel.history(limit=100):
                if old_msg.author.id != self.bot.user.id:
                    continue
                if any((e.title or "").upper() in {"🔴 MATCHS EN DIRECT", "🔴 ODDIUM LIVE"} for e in old_msg.embeds):
                    found.append(old_msg)
        except (discord.Forbidden, discord.HTTPException):
            return None, []
        # Reuse the newest card when no canonical DB id exists. Return every
        # matching message so cleanup can remove all non-canonical copies.
        return (found[0] if found else None), found

    async def ensure_live_panel(self, channel: discord.TextChannel) -> discord.Message:
        # Two concurrent refresh loops used to be able to create two panels.
        # A single lock makes creation/edit atomic inside the process.
        async with self._live_panel_lock:
            embed = await self.build_live_embed()
            view = LivePanelView(self.service)
            message_id = await self.service.db.get_setting("live_panel_message_id")
            msg = None
            if message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                except (discord.NotFound, discord.Forbidden, ValueError):
                    msg = None

            stale = []
            if msg is None:
                # Before creating anything, scan the channel and adopt an existing
                # live card. This survives DB resets/redeploys without duplicates.
                msg, found = await self._find_existing_live_panel(channel)
                stale.extend(found)

            if msg is None:
                msg = await channel.send(embed=embed, view=view)
            else:
                await msg.edit(embed=embed, view=view)

            await self.service.db.set_setting("live_panel_channel_id", channel.id)
            await self.service.db.set_setting("live_panel_message_id", msg.id)

            # Always clean stale duplicates after adoption/creation. On the first
            # pass, scan again so duplicates from previous releases disappear.
            if not self._live_cleanup_done:
                _, every_live_panel = await self._find_existing_live_panel(channel)
                stale.extend(every_live_panel)
            seen = set()
            for old_msg in stale:
                if old_msg.id == msg.id or old_msg.id in seen:
                    continue
                seen.add(old_msg.id)
                try:
                    await old_msg.delete(reason="Oddium: panneau Live dupliqué")
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
            self._live_cleanup_done = True

            try:
                if not msg.pinned:
                    await msg.pin(reason="Panneau Live Oddium")
            except discord.Forbidden:
                pass
            return msg

    async def refresh_existing_live_panel(self):
        """Refresh the manually installed live panel without ever creating one.

        Only /setup_live may create/install it.  If the stored Discord message no
        longer exists, automatic live events simply do nothing until the admin
        runs /setup_live again.
        """
        channel_id = await self.service.db.get_setting("live_panel_channel_id")
        message_id = await self.service.db.get_setting("live_panel_message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return
        embed = await self.build_live_embed()
        view = LivePanelView(self.service)
        await msg.edit(embed=embed, view=view)
