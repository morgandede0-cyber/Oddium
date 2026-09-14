from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

from .service import BettingService
from .constants import COMPETITIONS
from .ui import MainPanelView, LivePanelView, LIVE_EVENT_LABELS, build_title_embed, carousel_keys, CAROUSEL_ASSETS

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"


class PanelManager:
    def __init__(self, bot: discord.Client, service: BettingService):
        self.bot = bot
        self.service = service
        self._live_cleanup_done = False

    async def _state(self):
        active = await self.service.active_competitions()
        keys = carousel_keys(active)
        selected = await self.service.db.get_setting("panel_carousel_key")
        if selected not in keys and keys:
            selected = keys[0]
            await self.service.db.set_setting("panel_carousel_key", selected)
        return active, keys, selected

    async def build_embed(self) -> discord.Embed:
        return await build_title_embed(self.service)

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
        channel_id = await self.service.db.get_setting("panel_channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            await self.ensure_panel(channel)

    async def build_live_embed(self) -> discord.Embed:
        """Compact live board: one line group per match, no technical noise."""
        rows = await self.service.live_matches(25)
        embed = discord.Embed(title="🔴 ODDIUM LIVE", color=discord.Color.red())
        if not rows:
            embed.description = "⚽ **Aucun match en direct actuellement.**"
            return embed

        blocks = []
        labels = {
            "kickoff_wait": "🟠 DÉMARRAGE",
            "first_half": "🔴 DIRECT", "live": "🔴 DIRECT",
            "halftime": "⏸️ MI-TEMPS", "second_half": "🔴 DIRECT",
            "extra_time": "⏱️ PROLONG.", "penalties": "🎯 T.A.B.",
            "suspended": "⏸️ SUSPENDU", "finished": "✅ TERMINÉ",
            "postponed": "📅 REPORTÉ", "cancelled": "❌ ANNULÉ",
        }
        for m in rows:
            comp = COMPETITIONS.get(m["sport_key"], {})
            emoji = comp.get("emoji", "⚽")
            league = comp.get("name", m["competition_name"])
            hs = "–" if m["home_score"] is None else str(m["home_score"])
            aws = "–" if m["away_score"] is None else str(m["away_score"])
            phase = str(m["live_phase"] or m["match_status"] or "live").lower()
            status = labels.get(phase, "🔴 DIRECT")
            clock = str(m["live_clock"] or "").strip()
            if clock and phase not in {"halftime", "finished", "postponed", "cancelled"}:
                status += f" • {clock}"
            blocks.append(
                f"{emoji} **{league}** · {status}\n"
                f"**{m['home_team']}  {hs} - {aws}  {m['away_team']}**"
            )
        embed.description = "\n\n".join(blocks)
        embed.set_footer(text="Sélectionne un match pour les événements et statistiques")
        return embed

    async def ensure_live_panel(self, channel: discord.TextChannel) -> discord.Message:
        embed = await self.build_live_embed()
        message_id = await self.service.db.get_setting("live_panel_message_id")
        msg = None
        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(embed=embed, view=LivePanelView(self.service))
            except (discord.NotFound, discord.Forbidden):
                msg = None
        if msg is None:
            msg = await channel.send(embed=embed, view=LivePanelView(self.service))
            await self.service.db.set_setting("live_panel_channel_id", channel.id)
            await self.service.db.set_setting("live_panel_message_id", msg.id)
        try:
            if not msg.pinned:
                await msg.pin(reason="Panneau scores live Oddium")
        except discord.Forbidden:
            pass

        # V11: repair old duplicate live panels. Keep the canonical message only.
        # This runs best-effort and never blocks the live engine if history/delete
        # permissions are missing.
        if not self._live_cleanup_done:
            try:
                async for old_msg in channel.history(limit=60):
                    if old_msg.id == msg.id or not self.bot.user or old_msg.author.id != self.bot.user.id:
                        continue
                    if any((e.title or "").upper() in {"🔴 MATCHS EN DIRECT", "🔴 ODDIUM LIVE"} for e in old_msg.embeds):
                        await old_msg.delete(reason="Oddium V11: suppression panneau Live dupliqué")
                self._live_cleanup_done = True
            except (discord.Forbidden, discord.HTTPException):
                pass
        return msg

    async def refresh_existing_live_panel(self):
        channel_id = await self.service.db.get_setting("live_panel_channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            await self.ensure_live_panel(channel)
