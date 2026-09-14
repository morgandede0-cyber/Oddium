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
        rows = await self.service.live_matches(25)
        embed = discord.Embed(
            title="🔴 MATCHS EN DIRECT",
            description="⚡ **Oddium Live** — panneau piloté par notre WebSocket local. Sources gratuites redondantes : ESPN + Sofascore + FotMob + TheSportsDB. Cotes : Oddium Fusion.",
            color=discord.Color.red(),
        )
        if not rows:
            embed.description += "\n\n⚽ **Aucun match en direct actuellement.**"
        else:
            paris = ZoneInfo("Europe/Paris")
            blocks = []
            for m in rows:
                comp = COMPETITIONS.get(m["sport_key"], {})
                emoji = comp.get("emoji", "⚽")
                league = comp.get("name", m["competition_name"])
                hs = "–" if m["home_score"] is None else str(m["home_score"])
                aws = "–" if m["away_score"] is None else str(m["away_score"])
                phase = str(m["live_phase"] or m["match_status"] or "live").lower()
                clock = str(m["live_clock"] or "").strip()
                labels = {
                    "kickoff_wait": "🔴 COUP D’ENVOI • confirmation live…",
                    "first_half": "🔴 1RE MI-TEMPS", "live": "🔴 EN DIRECT",
                    "halftime": "⏸️ MI-TEMPS", "second_half": "🔴 2E MI-TEMPS",
                    "extra_time": "⏱️ PROLONGATIONS", "penalties": "🎯 TIRS AU BUT",
                    "suspended": "⏸️ SUSPENDU", "finished": "✅ TERMINÉ",
                    "postponed": "📅 REPORTÉ", "cancelled": "❌ ANNULÉ",
                }
                status_label = labels.get(phase, "🔴 EN DIRECT")
                if clock and phase not in {"halftime", "finished", "postponed", "cancelled"}:
                    status_label += f" • {clock}"
                detail = str(m["live_detail"] or "").strip()
                detail_line = f"\n_{detail}_" if detail and detail.lower() not in status_label.lower() else ""
                recent = await self.service.recent_live_events(str(m["event_id"]), 4)
                timeline = []
                for ev in reversed(recent):
                    label = LIVE_EVENT_LABELS.get(str(ev["event_type"]), "🔴 Live")
                    when = str(ev["clock"] or "").strip()
                    score = ""
                    if ev["home_score"] is not None and ev["away_score"] is not None:
                        score = f" • {ev['home_score']}-{ev['away_score']}"
                    timeline.append(f"`{when or '•'}` {label}{score}")
                timeline_line = "\n" + "\n".join(timeline) if timeline else ""
                blocks.append(
                    f"{emoji} **{league}**  •  {status_label}\n"
                    f"**{m['home_team']}  {hs} - {aws}  {m['away_team']}**{detail_line}{timeline_line}"
                )
            embed.description += "\n\n" + "\n\n".join(blocks)
            # Discord embeds cap description at 4096 chars.
            if len(embed.description) > 4000:
                embed.description = embed.description[:3970] + "\n…"

        last = await self.service.db.get_setting("last_scores_refresh")
        if last:
            try:
                dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                unix = int(dt.timestamp())
                embed.set_footer(text="Oddium Live • événements complets : coup d’envoi, chrono, buts, mi-temps, reprise et fin")
                embed.add_field(name="Dernier signal live", value=f"<t:{unix}:R>", inline=False)
            except Exception:
                pass
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
        return msg

    async def refresh_existing_live_panel(self):
        channel_id = await self.service.db.get_setting("live_panel_channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            await self.ensure_live_panel(channel)
