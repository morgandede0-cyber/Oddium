from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import discord

from config import SETTINGS
from .constants import BET_STATUS_ICONS, COMPETITIONS
from .service import BettingService, parse_iso

PARIS_TZ = ZoneInfo("Europe/Paris")
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"

CAROUSEL_ASSETS = {
    "soccer_france_ligue_one": "carousel_ligue1.png",
    "soccer_epl": "carousel_premier.png",
    "soccer_spain_la_liga": "carousel_laliga.png",
    "soccer_germany_bundesliga": "carousel_bundesliga.png",
    "soccer_italy_serie_a": "carousel_seriea.png",
    "soccer_uefa_champs_league": "carousel_champions.png",
}


def carousel_keys(active: list[str]) -> list[str]:
    preferred = [
        "soccer_france_ligue_one",
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_uefa_champs_league",
    ]
    return [k for k in preferred if k in active and k in COMPETITIONS]


def carousel_file(sport_key: str) -> discord.File | None:
    """Retourne l'image locale du championnat pour les carrousels Discord."""
    filename = CAROUSEL_ASSETS.get(sport_key)
    if not filename:
        return None
    path = ASSET_DIR / filename
    if not path.exists():
        return None
    return discord.File(path, filename=filename)


def carousel_attachments(active: list[str], index: int) -> list[discord.File]:
    if not active:
        return []
    f = carousel_file(active[index])
    return [f] if f else []


async def build_main_carousel_embed(service: BettingService, active: list[str], selected_key: str | None = None) -> discord.Embed:
    keys = carousel_keys(active)
    if not keys:
        return discord.Embed(title="⚽ ODDIUM", description="Aucune ligue active actuellement.", color=discord.Color.gold())
    if selected_key not in keys:
        selected_key = keys[0]
    info = COMPETITIONS[selected_key]
    matches = await service.matches_for_window(selected_key, "future", 25)
    embed = discord.Embed(
        title=f"{info['emoji']} {info['name']}",
        description="**Utilise ◀️ / ▶️ pour faire défiler les ligues, puis ouvre celle qui t'intéresse.**",
        color=discord.Color.gold(),
    )
    if matches:
        nxt = matches[0]
        local = parse_iso(nxt["commence_time"]).astimezone(PARIS_TZ)
        embed.add_field(name="Matchs disponibles", value=f"**{len(matches)}**", inline=True)
        embed.add_field(name="Prochain match", value=f"**{nxt['home_team']} — {nxt['away_team']}**\n{local.strftime('%d/%m à %H:%M')}", inline=True)
    else:
        embed.add_field(name="Matchs disponibles", value="Aucun pour le moment", inline=False)
    if selected_key in CAROUSEL_ASSETS:
        embed.set_image(url=f"attachment://{CAROUSEL_ASSETS[selected_key]}")
    embed.set_footer(text="ODDIUM • Cotes Oddium • 1 = domicile • N = nul • 2 = extérieur")
    return embed


def fmt_dt(value: str) -> str:
    dt = parse_iso(value).astimezone(PARIS_TZ)
    return dt.strftime("%d/%m/%Y à %H:%M")


def fmt_num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


class InteractionGuard:
    def __init__(self):
        # Plus de cooldown entre les clics : la navigation doit rester instantanée.
        # Les verrous par utilisateur sont conservés uniquement pour sécuriser
        # les opérations sensibles (ex. placement d'un pari).
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def allow(self, interaction: discord.Interaction) -> bool:
        return True


GUARD = InteractionGuard()


class StakeModal(discord.ui.Modal, title="🎟️ Placer le pari"):
    stake = discord.ui.TextInput(label="Mise", placeholder=f"Ex: 500 {SETTINGS.currency_name}", min_length=1, max_length=12)

    def __init__(self, service: BettingService, event_id: str, selection: str, displayed_odd: float):
        super().__init__(timeout=180)
        self.service = service
        self.event_id = event_id
        self.selection = selection
        self.displayed_odd = displayed_odd

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.stake.value).replace(" ", "").replace(",", "")
        if not raw.isdigit():
            await interaction.response.send_message("❌ Entre un montant entier valide.", ephemeral=True)
            return
        stake = int(raw)
        await interaction.response.defer(ephemeral=True)
        async with GUARD.locks[interaction.user.id]:
            ok, reason, data = await self.service.place_bet(
                interaction.user.id, self.event_id, self.selection, stake, self.displayed_odd
            )
        if not ok and reason == "ODD_CHANGED" and data:
            current = float(data["current_odd"])
            payout = int(stake * current)
            embed = discord.Embed(title="⚠️ Cote modifiée", color=discord.Color.orange())
            embed.description = (
                f"La cote est passée de **{self.displayed_odd:.2f}** à **{current:.2f}**.\n"
                f"Nouvelle mise : **{fmt_num(stake)} {SETTINGS.currency_name}**\n"
                f"Nouveau gain potentiel : **{fmt_num(payout)} {SETTINGS.currency_name}**"
            )
            await interaction.followup.send(
                embed=embed,
                view=ChangedOddView(self.service, self.event_id, self.selection, current, stake),
                ephemeral=True,
            )
            return
        if not ok:
            await interaction.followup.send(f"❌ {reason}", ephemeral=True)
            return
        match = data["match"]
        balance = await self.service.economy.get_balance(interaction.user.id)
        label = {"HOME": match["home_team"], "DRAW": "Match nul", "AWAY": match["away_team"]}[self.selection]
        embed = discord.Embed(title="✅ Pari validé", color=discord.Color.green())
        embed.description = (
            f"🎟️ **BET-{data['bet_id']}**\n\n"
            f"⚽ **{match['home_team']} — {match['away_team']}**\n"
            f"🎯 {label} • cote **{data['odd']:.2f}**\n"
            f"💰 Mise : **{fmt_num(stake)} {SETTINGS.currency_name}**\n"
            f"🏆 Gain potentiel : **{fmt_num(data['payout'])} {SETTINGS.currency_name}**\n\n"
            f"🪙 Nouveau solde : **{fmt_num(balance)} {SETTINGS.currency_name}**"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class ChangedOddView(discord.ui.View):
    def __init__(self, service: BettingService, event_id: str, selection: str, odd: float, stake: int):
        super().__init__(timeout=90)
        self.service = service
        self.event_id = event_id
        self.selection = selection
        self.odd = odd
        self.stake = stake

    @discord.ui.button(label="Accepter la nouvelle cote", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with GUARD.locks[interaction.user.id]:
            ok, reason, data = await self.service.place_bet(interaction.user.id, self.event_id, self.selection, self.stake, self.odd)
        if not ok:
            await interaction.followup.send(f"❌ {reason if reason != 'ODD_CHANGED' else 'La cote a encore changé. Reviens au panneau.'}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ **BET-{data['bet_id']}** validé • mise {fmt_num(self.stake)} • cote {data['odd']:.2f} • gain potentiel {fmt_num(data['payout'])} {SETTINGS.currency_name}",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Pari annulé.", embed=None, view=None)
        self.stop()


class BetChoiceButton(discord.ui.Button):
    def __init__(self, service: BettingService, event_id: str, selection: str, label: str, odd: float, row: int = 0):
        style = discord.ButtonStyle.primary if selection == "DRAW" else discord.ButtonStyle.success
        super().__init__(label=f"{label} • {odd:.2f}"[:80], style=style, row=row)
        self.service = service
        self.event_id = event_id
        self.selection = selection
        self.odd = odd

    async def callback(self, interaction: discord.Interaction):
        if not await GUARD.allow(interaction):
            return
        await interaction.response.send_modal(StakeModal(self.service, self.event_id, self.selection, self.odd))


class FavoriteTeamButton(discord.ui.Button):
    def __init__(self, service: BettingService, team: str, row: int):
        super().__init__(label=f"⭐ {team}"[:80], style=discord.ButtonStyle.secondary, row=row)
        self.service = service
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        state = await self.service.toggle_favorite(interaction.user.id, self.team)
        await interaction.response.send_message(
            f"{'⭐ Ajouté aux favoris' if state else '☆ Retiré des favoris'} : **{self.team}**", ephemeral=True
        )


class MatchBetView(discord.ui.View):
    def __init__(self, service: BettingService, match):
        super().__init__(timeout=180)
        self.add_item(BetChoiceButton(service, match["event_id"], "HOME", "1", float(match["home_odd"]), 0))
        self.add_item(BetChoiceButton(service, match["event_id"], "DRAW", "N", float(match["draw_odd"]), 0))
        self.add_item(BetChoiceButton(service, match["event_id"], "AWAY", "2", float(match["away_odd"]), 0))
        self.add_item(FavoriteTeamButton(service, match["home_team"], 1))
        self.add_item(FavoriteTeamButton(service, match["away_team"], 1))


class MatchSelect(discord.ui.Select):
    def __init__(self, service: BettingService, matches, sport_key: str, window: str):
        options = []
        for m in matches[:25]:
            options.append(discord.SelectOption(
                label=f"{m['home_team']} - {m['away_team']}"[:100],
                description=f"{fmt_dt(m['commence_time'])} • 1:{m['home_odd']:.2f} N:{m['draw_odd']:.2f} 2:{m['away_odd']:.2f}"[:100],
                value=m["event_id"], emoji="⚽",
            ))
        super().__init__(placeholder="⚽ Choisis un match…", options=options, min_values=1, max_values=1)
        self.service = service

    async def callback(self, interaction: discord.Interaction):
        match = await self.service.db.fetchone("SELECT * FROM matches WHERE event_id=?", (self.values[0],))
        if not match:
            await interaction.response.send_message("Ce match n'est plus disponible.", ephemeral=True)
            return
        trend = await self.service.odds_trend(match["event_id"])
        now = datetime.now(timezone.utc)
        kickoff = parse_iso(match["commence_time"])
        if now >= kickoff:
            await interaction.response.send_message("🔒 Ce match a déjà commencé.", ephemeral=True)
            return
        embed = discord.Embed(title=f"⚽ {match['home_team']} — {match['away_team']}", color=discord.Color.blurple())
        embed.description = f"🏆 **{match['competition_name']}**\n🕒 {fmt_dt(match['commence_time'])}"
        embed.add_field(name="1 — Domicile", value=f"**{match['home_team']}**\n`{match['home_odd']:.2f}` {trend['home']}", inline=True)
        embed.add_field(name="N — Nul", value=f"**Match nul**\n`{match['draw_odd']:.2f}` {trend['draw']}", inline=True)
        embed.add_field(name="2 — Extérieur", value=f"**{match['away_team']}**\n`{match['away_odd']:.2f}` {trend['away']}", inline=True)
        embed.set_footer(text=f"Cotes {match['bookmaker']} • cote figée au moment de la validation")
        await interaction.response.send_message(embed=embed, view=MatchBetView(self.service, match), ephemeral=True)


class CarouselNavButton(discord.ui.Button):
    def __init__(self, direction: int, row: int = 0):
        self.direction = direction
        super().__init__(
            emoji="◀️" if direction < 0 else "▶️",
            style=discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, LeagueCarouselView):
            return
        if not await GUARD.allow(interaction):
            return
        view.index = (view.index + self.direction) % len(view.active)
        await view.refresh(interaction)


class OpenLeagueButton(discord.ui.Button):
    def __init__(self, label: str, row: int = 0):
        super().__init__(label=label[:80], emoji="⚽", style=discord.ButtonStyle.success, row=row)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, LeagueCarouselView):
            return
        if not await GUARD.allow(interaction):
            return
        sport_key = view.active[view.index]
        matches = await view.service.matches_for_window(sport_key, "future", 25)
        embed = await build_league_embed(view.service, sport_key, matches)
        await interaction.response.edit_message(
            embed=embed,
            view=MatchBrowserView(view.service, view.active, matches, sport_key),
        )


async def build_carousel_embed(service: BettingService, active: list[str], index: int) -> discord.Embed:
    sport_key = active[index]
    info = COMPETITIONS[sport_key]
    matches = await service.matches_for_window(sport_key, "future", 25)

    embed = discord.Embed(
        title=f"{info['emoji']} {info['name']}",
        description=(
            f"### 🏆 Ligue {index + 1}/{len(active)}\n"
            "Utilise **◀️ / ▶️** pour faire défiler les championnats, puis ouvre la ligue choisie."
        ),
        color=discord.Color.gold(),
    )

    if matches:
        nxt = matches[0]
        local = parse_iso(nxt["commence_time"]).astimezone(PARIS_TZ)
        embed.add_field(
            name="📊 Disponibilité",
            value=f"**{len(matches)} match(s)** actuellement ouverts",
            inline=True,
        )
        embed.add_field(
            name="⏱️ Prochain match",
            value=f"**{local.strftime('%d/%m à %H:%M')}**",
            inline=True,
        )
        preview = []
        for m in matches[:4]:
            dt = parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
            preview.append(
                f"`{dt.strftime('%d/%m %H:%M')}`  **{m['home_team']}** — **{m['away_team']}**"
            )
        embed.add_field(
            name="⚽ Aperçu des rencontres",
            value="\n".join(preview),
            inline=False,
        )
    else:
        embed.add_field(
            name="📭 Aucun match disponible",
            value="Passe à la ligue suivante avec **▶️**.",
            inline=False,
        )

    if sport_key in CAROUSEL_ASSETS:
        embed.set_image(url=f"attachment://{CAROUSEL_ASSETS[sport_key]}")
    embed.set_footer(text="ODDIUM • Carrousel des ligues • 1 = domicile • N = nul • 2 = extérieur")
    return embed


class LeagueCarouselView(discord.ui.View):
    """Vrai carrousel Discord : une ligue à la fois + flèches précédent/suivant."""

    def __init__(self, service: BettingService, active: list[str], index: int = 0):
        super().__init__(timeout=300)
        self.service = service
        self.active = [k for k in active if k in COMPETITIONS]
        self.index = max(0, min(index, len(self.active) - 1)) if self.active else 0
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        if not self.active:
            return
        current = COMPETITIONS[self.active[self.index]]
        self.add_item(CarouselNavButton(-1, row=0))
        self.add_item(OpenLeagueButton(f"Ouvrir {current['name']}", row=0))
        self.add_item(CarouselNavButton(+1, row=0))

    async def refresh(self, interaction: discord.Interaction):
        self._rebuild()
        embed = await build_carousel_embed(self.service, self.active, self.index)
        await interaction.response.edit_message(embed=embed, view=self, attachments=carousel_attachments(self.active, self.index))


class BackToCarouselButton(discord.ui.Button):
    def __init__(self, service: BettingService, active: list[str], current: str):
        super().__init__(label="Changer de ligue", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
        self.service = service
        self.active = active
        self.current = current

    async def callback(self, interaction: discord.Interaction):
        if not await GUARD.allow(interaction):
            return
        try:
            index = self.active.index(self.current)
        except ValueError:
            index = 0
        embed = await build_carousel_embed(self.service, self.active, index)
        await interaction.response.edit_message(
            embed=embed,
            view=LeagueCarouselView(self.service, self.active, index),
            attachments=carousel_attachments(self.active, index),
        )


class MatchBrowserView(discord.ui.View):
    """Catégorie privée d'une ligue avec ses matchs uniquement."""
    def __init__(self, service: BettingService, active: list[str], matches, sport_key: str):
        super().__init__(timeout=300)
        self.service = service
        self.active = active
        self.sport_key = sport_key
        self.add_item(BackToCarouselButton(service, active, sport_key))
        if matches:
            select = MatchSelect(service, matches, sport_key, "future")
            select.row = 1
            self.add_item(select)


async def build_league_embed(service: BettingService, sport_key: str, matches) -> discord.Embed:
    info = COMPETITIONS[sport_key]
    embed = discord.Embed(
        title=f"{info['emoji']} {info['name']} • Matchs",
        description=(
            "Tous les matchs affichés ici appartiennent uniquement à cette compétition.\n"
            "Sélectionne une rencontre dans **⚽ Choisis un match…**."
        ),
        color=discord.Color.gold(),
    )

    if not matches:
        embed.description = (
            "Aucun match ouvert dans cette catégorie pour le moment.\n\n"
            "Clique sur **🏆 Changer de ligue** pour revenir au carrousel."
        )
        embed.set_footer(text="Oddium • Cotes calculées automatiquement • 1 / N / 2")
        return embed

    groups = defaultdict(list)
    for m in matches:
        local = parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
        groups[local.strftime("%Y-%m-%d")].append(m)

    for day_key in sorted(groups.keys())[:5]:
        day_matches = groups[day_key]
        local_day = parse_iso(day_matches[0]["commence_time"]).astimezone(PARIS_TZ)
        title = local_day.strftime("%A %d/%m").capitalize()
        lines = []
        for m in day_matches[:6]:
            local = parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
            if m["odds_available"] and m["home_odd"] is not None:
                odds = f"`1  {m['home_odd']:.2f}`  `N  {m['draw_odd']:.2f}`  `2  {m['away_odd']:.2f}`"
            else:
                odds = "`Cotes en préparation`"
            lines.append(f"**{local.strftime('%H:%M')} • {m['home_team']} — {m['away_team']}**\n{odds}")
        embed.add_field(name=f"📅 {title}", value="\n\n".join(lines), inline=False)

    embed.set_footer(text=f"{len(matches)} match(s) • 1 = domicile • N = nul • 2 = extérieur • Cotes Oddium")
    return embed


class CompetitionSelect(discord.ui.Select):
    """Compatibilité : ancien menu conservé mais non utilisé par le panneau principal."""
    def __init__(self, service: BettingService, active: list[str]):
        options = [
            discord.SelectOption(
                label=COMPETITIONS[k]["name"],
                value=k,
                emoji=COMPETITIONS[k]["emoji"],
                description="Voir tous les matchs de cette ligue",
            )
            for k in active if k in COMPETITIONS
        ]
        super().__init__(
            custom_id="oddium:league_select",
            placeholder="🏆 Sélectionner une ligue…",
            options=options[:25],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.service = service
        self.active = active

    async def callback(self, interaction: discord.Interaction):
        if not await GUARD.allow(interaction):
            return
        sport_key = self.values[0]
        matches = await self.service.matches_for_window(sport_key, "future", 25)
        embed = await build_league_embed(self.service, sport_key, matches)
        await interaction.response.send_message(
            embed=embed,
            view=MatchBrowserView(self.service, self.active, matches, sport_key),
            ephemeral=True,
        )

class PreferencesView(discord.ui.View):
    def __init__(self, service: BettingService):
        super().__init__(timeout=180)
        self.service = service

    async def _toggle(self, interaction: discord.Interaction, field: str, label: str):
        state = await self.service.toggle_preference(interaction.user.id, field)
        await interaction.response.send_message(f"{'✅' if state else '❌'} {label} : **{'activé' if state else 'désactivé'}**", ephemeral=True)

    @discord.ui.button(label="DM généraux", emoji="🔔", style=discord.ButtonStyle.secondary)
    async def dm(self, interaction, button): await self._toggle(interaction, "dm_notifications", "Notifications DM")

    @discord.ui.button(label="Résultats", emoji="🏁", style=discord.ButtonStyle.secondary)
    async def results(self, interaction, button): await self._toggle(interaction, "notify_result", "Résultats")

    @discord.ui.button(label="Avant-match", emoji="⏰", style=discord.ButtonStyle.secondary)
    async def reminder(self, interaction, button): await self._toggle(interaction, "notify_before_match", "Rappel avant-match")

    @discord.ui.button(label="Variation de cote", emoji="📈", style=discord.ButtonStyle.secondary)
    async def odds(self, interaction, button): await self._toggle(interaction, "notify_odds_change", "Variations de cote")


class MainPanelView(discord.ui.View):
    def __init__(self, service: BettingService, active: list[str]):
        super().__init__(timeout=None)
        self.service = service
        self.active = [k for k in active if k in COMPETITIONS]

    async def _current(self):
        active = await self.service.active_competitions()
        keys = carousel_keys([k for k in active if k in COMPETITIONS])
        selected = await self.service.db.get_setting("panel_carousel_key")
        if selected not in keys and keys:
            selected = keys[0]
            await self.service.db.set_setting("panel_carousel_key", selected)
        return keys, selected

    async def _move(self, interaction: discord.Interaction, direction: int):
        if not await GUARD.allow(interaction):
            return
        keys, selected = await self._current()
        if not keys:
            await interaction.response.send_message("Aucune ligue active actuellement.", ephemeral=True)
            return
        idx = keys.index(selected) if selected in keys else 0
        selected = keys[(idx + direction) % len(keys)]
        await self.service.db.set_setting("panel_carousel_key", selected)
        embed = await build_main_carousel_embed(self.service, keys, selected)
        filename = CAROUSEL_ASSETS.get(selected)
        path = ASSET_DIR / filename if filename else None
        if path and path.exists():
            await interaction.response.edit_message(
                embed=embed,
                view=self,
                attachments=[discord.File(path, filename=filename)],
            )
        else:
            await interaction.response.edit_message(embed=embed, view=self, attachments=[])

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="◀️", custom_id="oddium:carousel_prev", row=0)
    async def carousel_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, -1)

    @discord.ui.button(label="Voir les matchs", style=discord.ButtonStyle.success, emoji="⚽", custom_id="oddium:carousel_open", row=0)
    async def carousel_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await GUARD.allow(interaction):
            return
        keys, selected = await self._current()
        if not selected:
            await interaction.response.send_message("Aucune ligue active actuellement.", ephemeral=True)
            return
        matches = await self.service.matches_for_window(selected, "future", 25)
        embed = await build_league_embed(self.service, selected, matches)
        await interaction.response.send_message(
            embed=embed,
            view=MatchBrowserView(self.service, keys, matches, selected),
            ephemeral=True,
        )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="▶️", custom_id="oddium:carousel_next", row=0)
    async def carousel_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, +1)

    @discord.ui.button(label="Mes paris", style=discord.ButtonStyle.secondary, emoji="🎟️", custom_id="oddium:mybets", row=1)
    async def my_bets(self, interaction: discord.Interaction, button: discord.ui.Button):
        bets = await self.service.user_bets(interaction.user.id, None, 15)
        stats = await self.service.user_stats(interaction.user.id)
        embed = discord.Embed(title="🎟️ Mes paris", color=discord.Color.blurple())
        if not bets:
            embed.description = "Tu n'as encore placé aucun pari."
        else:
            lines = []
            for b in bets[:10]:
                icon = BET_STATUS_ICONS.get(b["status"], "⚪")
                sel = {"HOME": b["home_team"], "DRAW": "Nul", "AWAY": b["away_team"]}[b["selection"]]
                result = f" • gain {fmt_num(b['payout'])}" if b["status"] == "WON" else ""
                lines.append(f"{icon} **{b['home_team']} - {b['away_team']}**\n{sel} ×{b['odd']:.2f} • mise {fmt_num(b['stake'])}{result}")
            embed.description = "\n\n".join(lines)
        if stats:
            settled = int(stats["wins"] or 0) + int(stats["losses"] or 0)
            rate = (int(stats["wins"] or 0) / settled * 100) if settled else 0
            net = int(stats["returned"] or 0) - int(stats["wagered"] or 0)
            embed.add_field(name="📊 Résumé", value=f"Réussite **{rate:.0f}%** • Net **{net:+} {SETTINGS.currency_name}** • Plus gros gain **{fmt_num(int(stats['biggest_win'] or 0))}**", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Solde", style=discord.ButtonStyle.secondary, emoji="💰", custom_id="oddium:balance", row=1)
    async def balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        bal = await self.service.economy.get_balance(interaction.user.id)
        await interaction.response.send_message(f"💰 Ton solde : **{fmt_num(bal)} {SETTINGS.currency_name}**", ephemeral=True)

    @discord.ui.button(label="Classement", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="oddium:leaderboard", row=1)
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await self.service.leaderboard(10)
        embed = discord.Embed(title="🏆 Classement — bénéfice net", color=discord.Color.gold())
        if not rows:
            embed.description = "Pas encore de paris réglés."
        else:
            text = []
            for i, r in enumerate(rows, 1):
                settled = max(1, int(r["settled"] or 0))
                rate = int(r["wins"] or 0) / settled * 100
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
                text.append(f"{medal} <@{r['user_id']}> — **{int(r['net'] or 0):+}** {SETTINGS.currency_name} • {rate:.0f}%")
            embed.description = "\n".join(text)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Favoris", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="oddium:favorites", row=1)
    async def favorites(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches = await self.service.favorite_matches(interaction.user.id, 20)
        favs = await self.service.favorites(interaction.user.id)
        embed = discord.Embed(title="⭐ Mes favoris", color=discord.Color.gold())
        if not favs:
            embed.description = "Aucune équipe favorite. Ouvre un match puis clique sur ⭐ à côté de l'équipe."
        elif not matches:
            embed.description = "Équipes : " + ", ".join(r["team_name"] for r in favs) + "\n\nAucun match à venir chargé pour le moment."
        else:
            embed.description = "\n\n".join(
                f"**{m['home_team']} — {m['away_team']}**\n`{fmt_dt(m['commence_time'])}` • 1 {m['home_odd']:.2f} / N {m['draw_odd']:.2f} / 2 {m['away_odd']:.2f}"
                for m in matches[:10]
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Notifications", style=discord.ButtonStyle.secondary, emoji="🔔", custom_id="oddium:notifications", row=2)
    async def notifications(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = await self.service.ensure_preferences(interaction.user.id)
        embed = discord.Embed(title="🔔 Mes notifications", color=discord.Color.blurple())
        embed.description = (
            f"DM : {'✅' if p['dm_notifications'] else '❌'}\n"
            f"Résultats : {'✅' if p['notify_result'] else '❌'}\n"
            f"Rappel avant-match : {'✅' if p['notify_before_match'] else '❌'}\n"
            f"Variation de cote : {'✅' if p['notify_odds_change'] else '❌'}"
        )
        await interaction.response.send_message(embed=embed, view=PreferencesView(self.service), ephemeral=True)

    @discord.ui.button(label="Actualiser l'affichage", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="oddium:refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        # No API call: preserves quota. The permanent panel refresh loop reads cached DB data.
        last = self.service.last_odds_refresh
        text = "✅ Affichage à jour depuis le cache."
        if last:
            text += f" Dernière synchro des cotes : **{last.astimezone(PARIS_TZ).strftime('%H:%M:%S')}**."
        await interaction.response.send_message(text, ephemeral=True)


class CompetitionAdminSelect(discord.ui.Select):
    def __init__(self, service: BettingService, selected: list[str]):
        options = [discord.SelectOption(label=v["name"], value=k, emoji=v["emoji"], default=k in selected) for k, v in COMPETITIONS.items()]
        super().__init__(placeholder="🏆 Compétitions actives", min_values=1, max_values=len(options), options=options[:25])
        self.service = service

    async def callback(self, interaction: discord.Interaction):
        await self.service.set_active_competitions(list(self.values), interaction.user.id)
        await interaction.response.send_message("✅ Compétitions enregistrées. Le panneau va se mettre à jour.", ephemeral=True)


class VoidEventModal(discord.ui.Modal, title="♻️ Rembourser un match"):
    event_id = discord.ui.TextInput(label="ID du match", placeholder="Colle l'ID événement affiché dans l'outil admin")
    reason = discord.ui.TextInput(label="Motif", default="Match annulé / remboursé", required=True, max_length=200)

    def __init__(self, service: BettingService):
        super().__init__(timeout=180)
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        match = await self.service.db.fetchone("SELECT * FROM matches WHERE event_id=?", (str(self.event_id.value).strip(),))
        if not match:
            await interaction.response.send_message("❌ Match introuvable.", ephemeral=True)
            return
        count = await self.service.void_event(match["event_id"], interaction.user.id, str(self.reason.value))
        await interaction.response.send_message(f"♻️ **{count}** pari(s) remboursé(s) sur {match['home_team']} - {match['away_team']}.", ephemeral=True)


class AdminPanelView(discord.ui.View):
    def __init__(self, service: BettingService, active: list[str]):
        super().__init__(timeout=300)
        self.service = service
        self.add_item(CompetitionAdminSelect(service, active))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Synchroniser les cotes", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def force_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        updated, errors = await self.service.refresh_odds()
        await self.service.db.log_admin(interaction.user.id, "FORCE_REFRESH", f"{updated} événements")
        await interaction.followup.send(f"✅ {updated} matchs/cotes traités." + (f"\n⚠️ {' | '.join(errors)}" if errors else ""), ephemeral=True)

    @discord.ui.button(label="Suspendre / reprendre", emoji="🔒", style=discord.ButtonStyle.danger, row=1)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = bool(await self.service.db.get_setting("betting_paused"))
        await self.service.db.set_setting("betting_paused", not current)
        await self.service.db.log_admin(interaction.user.id, "BETTING_PAUSE", str(not current))
        await interaction.response.send_message("🔒 Paris suspendus." if not current else "🔓 Paris rouverts.", ephemeral=True)

    @discord.ui.button(label="État du système", emoji="🩺", style=discord.ButtonStyle.secondary, row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = await self.service.api_status()
        embed = discord.Embed(title="🩺 État Oddium", color=discord.Color.green() if not s["last_error"] else discord.Color.orange())
        embed.add_field(name="football-data.org", value=f"HTTP {s['last_status'] or '?'}\nRequêtes restantes/min: {s['remaining']}\nAppels locaux aujourd'hui: {s['used']}", inline=True)
        embed.add_field(name="Activité", value=f"Matchs futurs: {s['future_matches']}\nParis en cours: {s['pending_bets']}", inline=True)
        diag_lines = [
            f"**{d['name']}** — événements: `{d['events']}` • matchs: `{d['odds']}` • cotes calculées: `{d['parsed']}`"
            for d in s.get("diagnostics", [])
        ]
        if diag_lines:
            embed.add_field(name="Détection par compétition", value="\n".join(diag_lines)[:1000], inline=False)
        embed.add_field(name="Dernière erreur", value=(s["last_error"] or "Aucune")[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Matchs / IDs", emoji="⚽", style=discord.ButtonStyle.secondary, row=2)
    async def events(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await self.service.current_and_future_matches(15)
        embed = discord.Embed(title="⚽ Matchs chargés", color=discord.Color.blurple())
        embed.description = "\n\n".join(
            f"**{m['home_team']} - {m['away_team']}** • {fmt_dt(m['commence_time'])}\n`{m['event_id']}`"
            for m in rows
        ) or "Aucun match."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Rembourser un match", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def void_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoidEventModal(self.service))

    @discord.ui.button(label="Sauvegarder maintenant", emoji="💾", style=discord.ButtonStyle.secondary, row=2)
    async def backup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        path = await self.service.db.backup()
        await self.service.db.log_admin(interaction.user.id, "BACKUP", path)
        await interaction.followup.send(f"✅ Sauvegarde créée : `{path}`", ephemeral=True)

LIVE_EVENT_LABELS = {
    "match_started": "🟢 Coup d’envoi", "goal_or_score": "⚽ Score modifié",
    "halftime": "⏸️ Mi-temps", "second_half_started": "▶️ Reprise",
    "extra_time_started": "⏱️ Prolongations", "penalties_started": "🎯 Tirs au but",
    "match_suspended": "⏸️ Match suspendu", "match_postponed": "📅 Match reporté",
    "match_cancelled": "❌ Match annulé", "match_finished": "🏁 Fin du match",
    "phase_change": "🔄 Changement de phase", "live_update": "🔴 Mise à jour live",
}

class LiveFollowSelect(discord.ui.Select):
    def __init__(self, service: BettingService, matches, followed: set[str]):
        options=[]
        for m in matches[:25]:
            eid=str(m["event_id"])
            options.append(discord.SelectOption(
                label=f"{m['home_team']} - {m['away_team']}"[:100], value=eid,
                description=("🔔 Suivi — cliquer pour arrêter" if eid in followed else "Recevoir les événements importants")[:100],
                emoji="🔔" if eid in followed else "⚽"))
        super().__init__(placeholder="Choisis le match à suivre…", options=options, min_values=1, max_values=1)
        self.service=service
    async def callback(self, interaction: discord.Interaction):
        state=await self.service.toggle_match_follow(interaction.user.id,self.values[0])
        await interaction.response.edit_message(content=("🔔 **Match suivi.** Tu recevras les buts et changements de phase en DM." if state else "🔕 **Suivi désactivé.**"), view=None)

class LiveFollowSelectView(discord.ui.View):
    def __init__(self, service, matches, followed):
        super().__init__(timeout=90); self.add_item(LiveFollowSelect(service,matches,followed))

class LivePanelView(discord.ui.View):
    def __init__(self, service: BettingService):
        super().__init__(timeout=None); self.service=service

    @discord.ui.button(label="Suivre un match", style=discord.ButtonStyle.danger, emoji="🔔", custom_id="oddium:live:follow")
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches=await self.service.live_matches(25)
        if not matches:
            await interaction.response.send_message("⚽ Aucun match live à suivre actuellement.",ephemeral=True); return
        followed=await self.service.followed_event_ids(interaction.user.id)
        await interaction.response.send_message("🔔 Sélectionne un match :",view=LiveFollowSelectView(self.service,matches,followed),ephemeral=True)

    @discord.ui.button(label="Mes paris live", style=discord.ButtonStyle.success, emoji="🎫", custom_id="oddium:live:bets")
    async def bets(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows=await self.service.user_live_bets(interaction.user.id)
        if not rows:
            await interaction.response.send_message("🎫 Tu n’as aucun pari actuellement en direct.",ephemeral=True); return
        lines=[]
        for r in rows:
            hs=r['home_score'] if r['home_score'] is not None else '–'; aws=r['away_score'] if r['away_score'] is not None else '–'
            winning=(r['selection']=='HOME' and isinstance(hs,int) and isinstance(aws,int) and hs>aws) or (r['selection']=='AWAY' and isinstance(hs,int) and isinstance(aws,int) and aws>hs) or (r['selection']=='DRAW' and hs==aws and isinstance(hs,int))
            state="🟢 GAGNANT" if winning else "🔴 PAS GAGNANT"
            lines.append(f"**BET-{r['id']}** • {r['home_team']} **{hs}-{aws}** {r['away_team']}\n{state} • mise **{fmt_num(r['stake'])}** • gain potentiel **{fmt_num(r['potential_payout'])} {SETTINGS.currency_name}**")
        await interaction.response.send_message(embed=discord.Embed(title="🎫 Mes paris en direct",description="\n\n".join(lines),color=discord.Color.green()),ephemeral=True)

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="oddium:live:refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("✅ Le panneau est piloté automatiquement par Oddium Live. État relu depuis le cache live.",ephemeral=True)

# ========================= V8.9 — ÉCRAN TITRE / MODES DE PARI =========================

async def build_title_embed(service: BettingService) -> discord.Embed:
    active = await service.active_competitions()
    count = 0
    for key in active:
        if key in COMPETITIONS:
            count += len(await service.matches_for_window(key, "future", 25))
    embed = discord.Embed(
        title="⚽ ODDIUM • PARIS SPORTIFS",
        description=(
            "Bienvenue dans le mini espace paris sportifs du serveur **IV**.\n"
            "Choisis simplement ce que tu veux faire ci-dessous."
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url="attachment://oddium_welcome.png")
    embed.add_field(name="⚽ Voir les matchs", value="Parcours les championnats et consulte les rencontres disponibles.", inline=True)
    embed.add_field(name="🎟️ Parier", value="Choisis entre **pari simple** et **pari combiné**.", inline=True)
    embed.set_footer(text=f"ODDIUM • {count} match(s) disponibles • Cotes réelles PropLine")
    return embed


class BrowseMatchSelect(discord.ui.Select):
    def __init__(self, service: BettingService, matches):
        opts=[]
        for m in matches[:25]:
            opts.append(discord.SelectOption(
                label=f"{m['home_team']} - {m['away_team']}"[:100],
                description=f"{fmt_dt(m['commence_time'])} • 1:{m['home_odd']:.2f} N:{m['draw_odd']:.2f} 2:{m['away_odd']:.2f}"[:100],
                value=str(m['event_id']), emoji="⚽"))
        super().__init__(placeholder="⚽ Voir le détail d'un match…", options=opts, min_values=1, max_values=1, row=1)
        self.service=service

    async def callback(self, interaction: discord.Interaction):
        m=await self.service.db.fetchone("SELECT * FROM matches WHERE event_id=?", (self.values[0],))
        if not m:
            await interaction.response.send_message("Ce match n'est plus disponible.", ephemeral=True); return
        embed=discord.Embed(title=f"⚽ {m['home_team']} — {m['away_team']}", color=discord.Color.gold())
        embed.description=f"🏆 **{m['competition_name']}**\n🕒 {fmt_dt(m['commence_time'])}"
        embed.add_field(name="1 • Domicile", value=f"**{m['home_team']}**\n`{m['home_odd']:.2f}`", inline=True)
        embed.add_field(name="N • Nul", value=f"**Match nul**\n`{m['draw_odd']:.2f}`", inline=True)
        embed.add_field(name="2 • Extérieur", value=f"**{m['away_team']}**\n`{m['away_odd']:.2f}`", inline=True)
        embed.set_footer(text="Consultation uniquement • utilise 🎟️ Parier depuis l'accueil pour miser")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BrowseMatchBrowserView(discord.ui.View):
    def __init__(self, service: BettingService, active: list[str], matches, sport_key: str):
        super().__init__(timeout=300)
        self.service=service; self.active=active; self.sport_key=sport_key
        self.add_item(BrowseBackButton(service, active, sport_key))
        if matches:
            self.add_item(BrowseMatchSelect(service, matches))


class BrowseBackButton(discord.ui.Button):
    def __init__(self, service, active, current):
        super().__init__(label="Changer de championnat", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
        self.service=service; self.active=active; self.current=current
    async def callback(self, interaction):
        idx=self.active.index(self.current) if self.current in self.active else 0
        await interaction.response.edit_message(embed=await build_carousel_embed(self.service,self.active,idx), view=BrowseLeagueCarouselView(self.service,self.active,idx), attachments=carousel_attachments(self.active, idx))


class BrowseOpenLeagueButton(discord.ui.Button):
    def __init__(self,label):
        super().__init__(label=label[:80],emoji="⚽",style=discord.ButtonStyle.success,row=0)
    async def callback(self, interaction):
        view=self.view
        if not isinstance(view,BrowseLeagueCarouselView): return
        key=view.active[view.index]
        matches=await view.service.matches_for_window(key,"future",25)
        embed=await build_league_embed(view.service,key,matches)
        embed.set_footer(text=f"{len(matches)} match(s) • Consultation • pour miser utilise 🎟️ Parier")
        await interaction.response.edit_message(embed=embed,view=BrowseMatchBrowserView(view.service,view.active,matches,key))


class BrowseNavButton(discord.ui.Button):
    def __init__(self,direction:int):
        self.direction=direction
        super().__init__(emoji="◀️" if direction<0 else "▶️",style=discord.ButtonStyle.secondary,row=0)
    async def callback(self,interaction):
        view=self.view
        if not isinstance(view,BrowseLeagueCarouselView): return
        view.index=(view.index+self.direction)%len(view.active)
        view._rebuild()
        await interaction.response.edit_message(embed=await build_carousel_embed(view.service,view.active,view.index),view=view,attachments=carousel_attachments(view.active, view.index))


class BrowseLeagueCarouselView(discord.ui.View):
    def __init__(self,service,active,index=0):
        super().__init__(timeout=300); self.service=service; self.active=[k for k in active if k in COMPETITIONS]; self.index=index if self.active else 0; self._rebuild()
    def _rebuild(self):
        self.clear_items()
        if not self.active:return
        info=COMPETITIONS[self.active[self.index]]
        self.add_item(BrowseNavButton(-1)); self.add_item(BrowseOpenLeagueButton(f"Voir {info['name']}")); self.add_item(BrowseNavButton(1))


class BetModeView(discord.ui.View):
    def __init__(self,service:BettingService,active:list[str]):
        super().__init__(timeout=180); self.service=service; self.active=active

    @discord.ui.button(label="Pari simple",emoji="🎯",style=discord.ButtonStyle.success)
    async def simple(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not self.active:
            await interaction.response.send_message("Aucun championnat actif.",ephemeral=True); return
        embed=await build_carousel_embed(self.service,self.active,0)
        embed.title="🎯 PARI SIMPLE • " + embed.title
        embed.description="Choisis un championnat, puis un match et ton pronostic **1 / N / 2**."
        await interaction.response.edit_message(embed=embed,view=LeagueCarouselView(self.service,self.active,0),attachments=carousel_attachments(self.active, 0))

    @discord.ui.button(label="Pari combiné",emoji="🧩",style=discord.ButtonStyle.primary)
    async def combo(self,interaction:discord.Interaction,button:discord.ui.Button):
        session=ComboSession(self.service,self.active)
        await interaction.response.edit_message(
            embed=await session.embed(),
            view=ComboLeagueCarouselView(session),
            attachments=carousel_attachments(session.active, session.index),
        )


class ComboSession:
    def __init__(self,service,active):
        self.service=service; self.active=[k for k in active if k in COMPETITIONS]; self.index=0; self.legs=[]
    def total_odd(self):
        total=1.0
        for x in self.legs: total*=float(x['odd'])
        return total
    async def embed(self):
        info=COMPETITIONS[self.active[self.index]] if self.active else {"emoji":"⚽","name":"Aucune ligue"}
        matches=await self.service.matches_for_window(self.active[self.index],"future",25) if self.active else []
        desc=(f"### {info['emoji']} {info['name']}\nChoisis un championnat puis **Ajouter une sélection**.\n"
              "Un combiné doit contenir au moins **2 matchs différents**.")
        e=discord.Embed(title="🧩 PARI COMBINÉ",description=desc,color=discord.Color.blurple())
        if self.legs:
            lines=[]
            for i,l in enumerate(self.legs,1):
                lab={"HOME":"1","DRAW":"N","AWAY":"2"}[l['selection']]
                lines.append(f"**{i}.** {l['home']} — {l['away']} • **{lab} @ {l['odd']:.2f}**")
            e.add_field(name=f"🎟️ Ticket • {len(self.legs)} sélection(s)",value="\n".join(lines),inline=False)
            e.add_field(name="📈 Cote combinée",value=f"**{self.total_odd():.2f}**",inline=True)
        e.set_footer(text=f"{len(matches)} match(s) dans ce championnat • maximum 10 sélections")
        if self.active:
            sport_key = self.active[self.index]
            if sport_key in CAROUSEL_ASSETS:
                e.set_image(url=f"attachment://{CAROUSEL_ASSETS[sport_key]}")
        return e


class ComboNavButton(discord.ui.Button):
    def __init__(self,d): self.d=d; super().__init__(emoji="◀️" if d<0 else "▶️",style=discord.ButtonStyle.secondary,row=0)
    async def callback(self,interaction):
        v=self.view
        if not isinstance(v,ComboLeagueCarouselView):return
        v.session.index=(v.session.index+self.d)%len(v.session.active); v.rebuild()
        await interaction.response.edit_message(
            embed=await v.session.embed(),
            view=v,
            attachments=carousel_attachments(v.session.active, v.session.index),
        )


class ComboAddSelect(discord.ui.Select):
    def __init__(self,session,matches):
        opts=[]
        for m in matches[:25]:
            opts.append(discord.SelectOption(label=f"{m['home_team']} - {m['away_team']}"[:100],description=f"1 {m['home_odd']:.2f} • N {m['draw_odd']:.2f} • 2 {m['away_odd']:.2f}"[:100],value=str(m['event_id']),emoji="⚽"))
        super().__init__(placeholder="Ajouter une sélection…",options=opts,min_values=1,max_values=1,row=1)
        self.session=session
    async def callback(self,interaction):
        m=await self.session.service.db.fetchone("SELECT * FROM matches WHERE event_id=?",(self.values[0],))
        if not m:return await interaction.response.send_message("Match indisponible.",ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(title=f"🧩 {m['home_team']} — {m['away_team']}",description="Choisis le résultat à ajouter au combiné.",color=discord.Color.blurple()),
            view=ComboPickOutcomeView(self.session,m),ephemeral=True)


class ComboOutcomeButton(discord.ui.Button):
    def __init__(self,session,m,selection,label,odd):
        super().__init__(label=f"{label} • {odd:.2f}",style=discord.ButtonStyle.success if selection!='DRAW' else discord.ButtonStyle.primary)
        self.session=session;self.m=m;self.selection=selection;self.odd=float(odd)
    async def callback(self,interaction):
        if any(str(x['event_id'])==str(self.m['event_id']) for x in self.session.legs):
            await interaction.response.edit_message(content="⚠️ Ce match est déjà dans ton combiné.",embed=None,view=None);return
        self.session.legs.append({"event_id":str(self.m['event_id']),"selection":self.selection,"odd":self.odd,"home":self.m['home_team'],"away":self.m['away_team']})
        await interaction.response.edit_message(content=f"✅ Sélection ajoutée. Ticket : **{len(self.session.legs)} choix** • cote **{self.session.total_odd():.2f}**",embed=None,view=None)


class ComboPickOutcomeView(discord.ui.View):
    def __init__(self,session,m):
        super().__init__(timeout=120)
        self.add_item(ComboOutcomeButton(session,m,"HOME","1",m['home_odd']))
        self.add_item(ComboOutcomeButton(session,m,"DRAW","N",m['draw_odd']))
        self.add_item(ComboOutcomeButton(session,m,"AWAY","2",m['away_odd']))


class ComboStakeModal(discord.ui.Modal,title="🧩 Valider le combiné"):
    stake=discord.ui.TextInput(label="Mise",placeholder=f"Ex: 500 {SETTINGS.currency_name}",min_length=1,max_length=12)
    def __init__(self,session): super().__init__(timeout=180); self.session=session
    async def on_submit(self,interaction):
        raw=str(self.stake.value).replace(" ","").replace(",","")
        if not raw.isdigit(): return await interaction.response.send_message("❌ Mise invalide.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok,reason,data=await self.session.service.place_combo_bet(interaction.user.id,self.session.legs,int(raw))
        if not ok:
            if reason=="ODD_CHANGED": reason="Une cote a changé. Recrée le combiné pour accepter les nouvelles cotes."
            return await interaction.followup.send(f"❌ {reason}",ephemeral=True)
        e=discord.Embed(title="✅ Combiné validé",color=discord.Color.green())
        e.description=(f"🎟️ **COMBO-{data['combo_id']}**\n🧩 {len(self.session.legs)} sélections\n📈 Cote totale **{data['total_odd']:.2f}**\n"
                       f"💰 Mise **{fmt_num(int(raw))} {SETTINGS.currency_name}**\n🏆 Gain potentiel **{fmt_num(data['payout'])} {SETTINGS.currency_name}**")
        await interaction.followup.send(embed=e,ephemeral=True)


class ComboValidateButton(discord.ui.Button):
    def __init__(self,session): super().__init__(label="Valider le combiné",emoji="✅",style=discord.ButtonStyle.success,row=2);self.session=session
    async def callback(self,interaction):
        if len(self.session.legs)<2:return await interaction.response.send_message("Ajoute au moins **2 matchs** au combiné.",ephemeral=True)
        await interaction.response.send_modal(ComboStakeModal(self.session))


class ComboClearButton(discord.ui.Button):
    def __init__(self,session): super().__init__(label="Vider",emoji="🗑️",style=discord.ButtonStyle.danger,row=2);self.session=session
    async def callback(self,interaction):
        self.session.legs.clear(); v=self.view
        if isinstance(v,ComboLeagueCarouselView):
            v.rebuild()
            await interaction.response.edit_message(
                embed=await self.session.embed(),
                view=v,
                attachments=carousel_attachments(self.session.active, self.session.index),
            )


class ComboLeagueCarouselView(discord.ui.View):
    def __init__(self,session): super().__init__(timeout=600);self.session=session;self.rebuild()
    def rebuild(self):
        self.clear_items()
        if not self.session.active:return
        self.add_item(ComboNavButton(-1)); self.add_item(ComboNavButton(1))
        # Select options are hydrated lazily by button below because __init__ can't await.
        self.add_item(ComboChooseLeagueButton(self.session))
        self.add_item(ComboValidateButton(self.session)); self.add_item(ComboClearButton(self.session))


class ComboChooseLeagueButton(discord.ui.Button):
    def __init__(self,session):
        info=COMPETITIONS[session.active[session.index]]
        super().__init__(label=f"Choisir dans {info['name']}"[:80],emoji="⚽",style=discord.ButtonStyle.primary,row=1);self.session=session
    async def callback(self,interaction):
        key=self.session.active[self.session.index]; matches=await self.session.service.matches_for_window(key,"future",25)
        if not matches:return await interaction.response.send_message("Aucun match disponible dans ce championnat.",ephemeral=True)
        view=discord.ui.View(timeout=120); view.add_item(ComboAddSelect(self.session,matches))
        await interaction.response.send_message(f"🧩 **{COMPETITIONS[key]['name']}** — choisis un match :",view=view,ephemeral=True)


class MainPanelView(discord.ui.View):
    """Écran titre permanent : uniquement Voir les matchs + Parier."""
    def __init__(self,service:BettingService,active:list[str]):
        super().__init__(timeout=None); self.service=service; self.active=[k for k in active if k in COMPETITIONS]

    @discord.ui.button(label="Voir les matchs",emoji="⚽",style=discord.ButtonStyle.primary,custom_id="oddium:home:matches")
    async def matches(self,interaction:discord.Interaction,button:discord.ui.Button):
        active=carousel_keys(await self.service.active_competitions())
        if not active:return await interaction.response.send_message("Aucun championnat actif.",ephemeral=True)
        await interaction.response.send_message(embed=await build_carousel_embed(self.service,active,0),view=BrowseLeagueCarouselView(self.service,active,0),files=carousel_attachments(active, 0),ephemeral=True)

    @discord.ui.button(label="Parier",emoji="🎟️",style=discord.ButtonStyle.success,custom_id="oddium:home:bet")
    async def bet(self,interaction:discord.Interaction,button:discord.ui.Button):
        active=carousel_keys(await self.service.active_competitions())
        e=discord.Embed(title="🎟️ CHOISIS TON TYPE DE PARI",description="**Pari simple** : un seul pronostic.\n**Pari combiné** : plusieurs matchs sur un même ticket, les cotes se multiplient.",color=discord.Color.gold())
        await interaction.response.send_message(embed=e,view=BetModeView(self.service,active),ephemeral=True)

    @discord.ui.button(label="Mes paris",emoji="📋",style=discord.ButtonStyle.secondary,custom_id="oddium:home:mybets")
    async def my_bets(self,interaction:discord.Interaction,button:discord.ui.Button):
        simple = await self.service.user_bets(interaction.user.id, None, 20)
        combos = await self.service.user_combo_bets(interaction.user.id, 10)
        e=discord.Embed(title="📋 MES PARIS",description="Retrouve ici tous tes paris simples et combinés.",color=discord.Color.blurple())
        if simple:
            lines=[]
            for b in simple[:8]:
                icon=BET_STATUS_ICONS.get(b["status"],"⚪")
                sel={"HOME":b["home_team"],"DRAW":"Nul","AWAY":b["away_team"]}.get(b["selection"],b["selection"])
                gain=f" • gain **{fmt_num(b['payout'])} {SETTINGS.currency_name}**" if b["status"]=="WON" else ""
                lines.append(f"{icon} **{b['home_team']} - {b['away_team']}**\n{sel} @ {b['odd']:.2f} • mise **{fmt_num(b['stake'])}**{gain}")
            e.add_field(name="🎯 Paris simples",value="\n\n".join(lines)[:1024],inline=False)
        else:
            e.add_field(name="🎯 Paris simples",value="Aucun pari simple créé.",inline=False)
        if combos:
            lines=[]
            for c,legs in combos[:6]:
                status=str(c["status"])
                icon={"PENDING":"🟡","WON":"🟢","LOST":"🔴","VOID":"⚪"}.get(status,"⚪")
                legtxt=" • ".join(f"{l['home_team']} - {l['away_team']}" for l in legs[:3])
                if len(legs)>3: legtxt += f" +{len(legs)-3}"
                lines.append(f"{icon} **COMBO-{c['id']}** • {len(legs)} matchs • cote **{float(c['total_odd']):.2f}**\n{legtxt}\nMise **{fmt_num(c['stake'])}** • gain potentiel **{fmt_num(c['potential_payout'])} {SETTINGS.currency_name}**")
            e.add_field(name="🧩 Paris combinés",value="\n\n".join(lines)[:1024],inline=False)
        else:
            e.add_field(name="🧩 Paris combinés",value="Aucun pari combiné créé.",inline=False)
        e.set_footer(text="🟡 En attente • 🟢 Gagné • 🔴 Perdu • ⚪ Annulé")
        await interaction.response.send_message(embed=e,ephemeral=True)

# ========================= V9.0 — ODDIUM EXPERIENCE =========================

async def build_title_embed(service: BettingService) -> discord.Embed:
    active = carousel_keys(await service.active_competitions())
    # Important: on ne peut pas utiliser `await` dans une expression génératrice
    # passée à sum(). On attend chaque requête SQLite/cache l'une après l'autre.
    count = 0
    for key in active:
        count += len(await service.matches_for_window(key, "future", 25))
    live_count = len(await service.live_matches(25))
    pending = await service.db.fetchone("SELECT COUNT(*) c FROM bets WHERE status='PENDING'")
    combo_pending = await service.db.fetchone("SELECT COUNT(*) c FROM combo_bets WHERE status='PENDING'")
    open_tickets = int(pending['c'] if pending else 0) + int(combo_pending['c'] if combo_pending else 0)
    e = discord.Embed(
        title="⚽ ODDIUM • L'ARÈNE DES PARIS IV",
        description=(
            "**Bienvenue sur Oddium.** Consulte les matchs, construis ton ticket et grimpe au classement IV.\n\n"
            f"🟢 **{count}** matchs disponibles   •   🔴 **{live_count}** en direct   •   🎟️ **{open_tickets}** paris ouverts"
        ), color=discord.Color.gold())
    e.set_image(url="attachment://oddium_welcome.png")
    e.add_field(name="⚽ Matchs", value="Championnats, horaires et cotes", inline=True)
    e.add_field(name="🎟️ Parier", value="Simple ou combiné", inline=True)
    e.add_field(name="📋 Mes paris", value="Tickets et résultats", inline=True)
    e.set_footer(text="ODDIUM • Cotes réelles PropLine • Données live gratuites • Gold virtuel")
    return e

class HomeReturnView(discord.ui.View):
    def __init__(self, service):
        super().__init__(timeout=300); self.service=service
    @discord.ui.button(label="Retour à l'accueil", emoji="🏠", style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        active=carousel_keys(await self.service.active_competitions())
        await interaction.response.edit_message(embed=await build_title_embed(self.service), view=QuickHomeView(self.service,active), attachments=[])

class QuickHomeView(discord.ui.View):
    """Navigation privée cohérente après avoir ouvert un sous-écran."""
    def __init__(self, service, active):
        super().__init__(timeout=300); self.service=service; self.active=active
    @discord.ui.button(label="Voir les matchs",emoji="⚽",style=discord.ButtonStyle.primary)
    async def matches(self,interaction,button):
        if not self.active:return await interaction.response.send_message("Aucun championnat actif.",ephemeral=True)
        await interaction.response.edit_message(embed=await build_carousel_embed(self.service,self.active,0),view=BrowseLeagueCarouselView(self.service,self.active,0),attachments=carousel_attachments(self.active,0))
    @discord.ui.button(label="Parier",emoji="🎟️",style=discord.ButtonStyle.success)
    async def bet(self,interaction,button):
        e=discord.Embed(title="🎟️ CHOISIS TON TYPE DE PARI",description="🎯 **Pari simple** — un pronostic 1/N/2\n🧩 **Pari combiné** — plusieurs matchs, une cote totale",color=discord.Color.gold())
        await interaction.response.edit_message(embed=e,view=BetModeView(self.service,self.active),attachments=[])

async def _my_bets_embed(service, user_id):
    simple=await service.user_bets(user_id,None,20); combos=await service.user_combo_bets(user_id,10)
    e=discord.Embed(title="📋 MES PARIS",description="🟡 En cours  •  🟢 Gagné  •  🔴 Perdu  •  ⚪ Annulé",color=discord.Color.blurple())
    if simple:
        lines=[]
        for b in simple[:8]:
            icon=BET_STATUS_ICONS.get(b['status'],'⚪'); sel={'HOME':'1','DRAW':'N','AWAY':'2'}.get(b['selection'],b['selection'])
            lines.append(f"{icon} **BET-{b['id']}** • {b['home_team']} — {b['away_team']}\n`{sel} @ {b['odd']:.2f}` • mise **{fmt_num(b['stake'])}** • potentiel **{fmt_num(b['potential_payout'])} {SETTINGS.currency_name}**")
        e.add_field(name="🎯 Simples",value="\n\n".join(lines)[:1024],inline=False)
    else:e.add_field(name="🎯 Simples",value="Aucun ticket.",inline=False)
    if combos:
        lines=[]
        for c,legs in combos[:5]:
            icon={'PENDING':'🟡','WON':'🟢','LOST':'🔴','VOID':'⚪'}.get(str(c['status']),'⚪')
            lines.append(f"{icon} **COMBO-{c['id']}** • {len(legs)} sélections • cote **{float(c['total_odd']):.2f}**\nMise **{fmt_num(c['stake'])}** • potentiel **{fmt_num(c['potential_payout'])} {SETTINGS.currency_name}**")
        e.add_field(name="🧩 Combinés",value="\n\n".join(lines)[:1024],inline=False)
    else:e.add_field(name="🧩 Combinés",value="Aucun combiné.",inline=False)
    return e

async def _profile_embed(service,user):
    s=await service.user_stats(user.id); combos=await service.user_combo_bets(user.id,100)
    total=int(s['total'] or 0); wins=int(s['wins'] or 0); losses=int(s['losses'] or 0); settled=wins+losses
    returned=int(s['returned'] or 0); wagered=int(s['wagered'] or 0); net=returned-wagered
    combo_wins=sum(1 for c,_ in combos if c['status']=='WON'); combo_losses=sum(1 for c,_ in combos if c['status']=='LOST')
    balance=await service.economy.get_balance(user.id); rate=(wins/settled*100) if settled else 0
    e=discord.Embed(title=f"👤 PROFIL ODDIUM • {user.display_name}",color=discord.Color.gold())
    e.add_field(name="🪙 Solde",value=f"**{fmt_num(balance)} {SETTINGS.currency_name}**",inline=True)
    e.add_field(name="📈 Bénéfice net",value=f"**{net:+,} {SETTINGS.currency_name}**".replace(',', ' '),inline=True)
    e.add_field(name="🎯 Réussite",value=f"**{rate:.0f}%** ({wins}/{settled})",inline=True)
    e.add_field(name="🎟️ Paris simples",value=f"{total} créés • {wins} gagnés • {losses} perdus",inline=False)
    e.add_field(name="🧩 Combinés",value=f"{len(combos)} créés • {combo_wins} gagnés • {combo_losses} perdus",inline=False)
    e.add_field(name="🏆 Record",value=f"Plus gros gain : **{fmt_num(int(s['biggest_win'] or 0))}**\nMeilleure cote gagnée : **{float(s['biggest_odd'] or 0):.2f}**",inline=False)
    e.set_thumbnail(url=user.display_avatar.url)
    return e

async def _leaderboard_embed(service, bot):
    rows=await service.leaderboard(10); e=discord.Embed(title="🏆 CLASSEMENT ODDIUM",description="Classement selon le **bénéfice net** des paris réglés.",color=discord.Color.gold())
    if not rows:e.description += "\n\nAucun joueur classé pour le moment."; return e
    medals=['🥇','🥈','🥉']; lines=[]
    for i,r in enumerate(rows,1):
        u=bot.get_user(int(r['user_id'])); name=u.display_name if u else f"Joueur {r['user_id']}"
        rate=(int(r['wins'])/int(r['settled'])*100) if int(r['settled']) else 0
        lines.append(f"{medals[i-1] if i<=3 else f'`#{i}`'} **{name}** — **{int(r['net']):+,} {SETTINGS.currency_name}** • {rate:.0f}%".replace(',', ' '))
    e.description += "\n\n"+'\n'.join(lines); return e

async def _live_embed(service):
    rows=await service.live_matches(15); e=discord.Embed(title="🔴 ODDIUM LIVE",color=discord.Color.red())
    if not rows:e.description="⚽ Aucun match en direct actuellement.\n\nLe panneau se met à jour automatiquement dès qu'un événement live est détecté."; return e
    blocks=[]
    for m in rows:
        hs='–' if m['home_score'] is None else m['home_score']; aws='–' if m['away_score'] is None else m['away_score']; clock=str(m['live_clock'] or '')
        blocks.append(f"🔴 **{m['home_team']}  {hs} - {aws}  {m['away_team']}**\n{m['competition_name']} • **{clock or 'EN DIRECT'}**")
    e.description='\n\n'.join(blocks); e.set_footer(text="Oddium Live • données lues depuis le cache live")
    return e

class MainPanelView(discord.ui.View):
    """V9 : accueil permanent, cinq accès maximum, lisible sur mobile et PC."""
    def __init__(self,service:BettingService,active:list[str]):
        super().__init__(timeout=None); self.service=service; self.active=[k for k in active if k in COMPETITIONS]
    @discord.ui.button(label="Voir les matchs",emoji="⚽",style=discord.ButtonStyle.primary,custom_id="oddium:v9:matches",row=0)
    async def matches(self,interaction,button):
        active=carousel_keys(await self.service.active_competitions())
        if not active:return await interaction.response.send_message("Aucun championnat actif.",ephemeral=True)
        await interaction.response.send_message(embed=await build_carousel_embed(self.service,active,0),view=BrowseLeagueCarouselView(self.service,active,0),files=carousel_attachments(active,0),ephemeral=True)
    @discord.ui.button(label="Parier",emoji="🎟️",style=discord.ButtonStyle.success,custom_id="oddium:v9:bet",row=0)
    async def bet(self,interaction,button):
        active=carousel_keys(await self.service.active_competitions()); e=discord.Embed(title="🎟️ CRÉER UN PARI",description="🎯 **Pari simple** — rapide, un seul match.\n🧩 **Pari combiné** — construis ton ticket sur plusieurs championnats.",color=discord.Color.gold())
        await interaction.response.send_message(embed=e,view=BetModeView(self.service,active),ephemeral=True)
    @discord.ui.button(label="Mes paris",emoji="📋",style=discord.ButtonStyle.secondary,custom_id="oddium:v9:mybets",row=0)
    async def mybets(self,interaction,button):
        await interaction.response.send_message(embed=await _my_bets_embed(self.service,interaction.user.id),view=HomeReturnView(self.service),ephemeral=True)
    @discord.ui.button(label="Live",emoji="🔴",style=discord.ButtonStyle.danger,custom_id="oddium:v9:live",row=1)
    async def live(self,interaction,button):
        await interaction.response.send_message(embed=await _live_embed(self.service),view=HomeReturnView(self.service),ephemeral=True)
    @discord.ui.button(label="Classement / Profil",emoji="🏆",style=discord.ButtonStyle.secondary,custom_id="oddium:v9:rank",row=1)
    async def rank(self,interaction,button):
        e=await _leaderboard_embed(self.service,interaction.client); e.add_field(name="👤 Ton profil",value="Utilise le bouton ci-dessous pour afficher tes statistiques personnelles.",inline=False)
        await interaction.response.send_message(embed=e,view=RankProfileView(self.service),ephemeral=True)

class RankProfileView(discord.ui.View):
    def __init__(self,service):super().__init__(timeout=300);self.service=service
    @discord.ui.button(label="Mon profil",emoji="👤",style=discord.ButtonStyle.primary)
    async def profile(self,interaction,button):
        await interaction.response.edit_message(embed=await _profile_embed(self.service,interaction.user),view=HomeReturnView(self.service))
    @discord.ui.button(label="Accueil",emoji="🏠",style=discord.ButtonStyle.secondary)
    async def home(self,interaction,button):
        active=carousel_keys(await self.service.active_competitions()); await interaction.response.edit_message(embed=await build_title_embed(self.service),view=QuickHomeView(self.service,active))
