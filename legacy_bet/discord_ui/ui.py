from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import discord

from config import SETTINGS
from ..core.constants import BET_STATUS_ICONS, COMPETITIONS
from ..betting.service import BettingService
from ..core.time_utils import parse_iso
from .visuals import match_card

PARIS_TZ = ZoneInfo("Europe/Paris")
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"
log = logging.getLogger(__name__)


from .private_pages import show as open_private_page

CAROUSEL_ASSETS = {
    "soccer_france_ligue_one": "carousel_ligue1.png",
    "soccer_epl": "carousel_premier.png",
    "soccer_spain_la_liga": "carousel_laliga.png",
    "soccer_germany_bundesliga": "carousel_bundesliga.png",
    "soccer_italy_serie_a": "carousel_seriea.png",
    "soccer_uefa_champs_league": "carousel_champions.png",
    "soccer_uefa_europa_league": "carousel_europa.png",
}


def carousel_keys(active: list[str]) -> list[str]:
    preferred = [
        "soccer_france_ligue_one",
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
    ]
    return [k for k in preferred if k in active and k in COMPETITIONS]


def carousel_file(sport_key: str) -> discord.File | None:
    """Retourne l'image locale du championnat pour les sélecteurs Discord."""
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
        await interaction.response.defer(ephemeral=True, thinking=False)
        matches = await view.service.matches_for_window(sport_key, "future", 25)
        embed = await build_league_embed(view.service, sport_key, matches)
        await interaction.edit_original_response(
            embed=embed,
            view=MatchBrowserView(view.service, view.active, matches, sport_key),
        )




class LeagueCarouselView(discord.ui.View):
    """Vrai sélecteur Discord : une ligue à la fois + flèches précédent/suivant."""

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
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed = await build_carousel_embed(self.service, self.active, self.index)
        await interaction.edit_original_response(embed=embed, view=self, attachments=carousel_attachments(self.active, self.index))


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
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed = await league_hub_embed(self.service, self.active, "browse")
        await interaction.edit_original_response(embed=embed, view=LeagueGridView(self.service, self.active, "browse"), attachments=[])


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
        embed.add_field(name="Données football", value=f"5Dollar HTTP {s['last_status'] or '?'}\nCotes : Bet365 via 5Dollar\nLive : 5Dollar natif uniquement", inline=True)
        embed.add_field(name="Activité", value=f"Matchs futurs: {s['future_matches']}\nParis en cours: {s['pending_bets']}", inline=True)
        diag_lines = [
            f"**{d['name']}** — fixtures `{d['events']}` • cotes `{d['parsed']}` • live `{d.get('live_rows',0)}`\n↳ {d.get('live_source','—')}"
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

def _dedupe_live_highlights(events):
    """Keep one human-visible copy of each highlight.

    Old databases may already contain repeated 5Dollar period snapshots, so the
    renderer must be defensive even after the ingestion fix.
    """
    cleaned = []
    seen = set()
    for ev in events or []:
        etype = str(ev["event_type"] or "").strip().lower()
        detail = " ".join(str(ev["detail"] or "").strip().lower().split())
        clock = "" if etype == "period_score" else str(ev["clock"] or "").strip().lower()
        key = (etype, clock, detail)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ev)
    return cleaned

LIVE_EVENT_LABELS = {
    "match_started": "🟢 Coup d’envoi", "goal_or_score": "⚽ Score modifié",
    "goal": "⚽ But", "var": "📺 VAR", "yellow_card": "🟨 Carton jaune",
    "red_card": "🟥 Carton rouge", "card": "🟨 Carton",
    "penalty_missed": "❌ Penalty manqué", "substitution": "🔁 Remplacement",
    "corner": "🚩 Corner", "period_score": "📌 Score de période",
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






# ========================= ÉCRAN TITRE / MODES DE PARI =========================





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
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed=await build_carousel_embed(self.service,self.active,idx)
        await interaction.edit_original_response(embed=embed, view=BrowseLeagueCarouselView(self.service,self.active,idx), attachments=carousel_attachments(self.active, idx))


class BrowseOpenLeagueButton(discord.ui.Button):
    def __init__(self,label):
        super().__init__(label=label[:80],emoji="⚽",style=discord.ButtonStyle.success,row=0)
    async def callback(self, interaction):
        view=self.view
        if not isinstance(view,BrowseLeagueCarouselView): return
        key=view.active[view.index]
        await interaction.response.defer(ephemeral=True, thinking=False)
        matches=await view.service.matches_for_window(key,"future",25)
        embed=await build_league_embed(view.service,key,matches)
        embed.set_footer(text=f"{len(matches)} match(s) • Consultation • pour miser utilise 🎟️ Parier")
        await interaction.edit_original_response(embed=embed,view=BrowseMatchBrowserView(view.service,view.active,matches,key),attachments=[])


class BrowseNavButton(discord.ui.Button):
    def __init__(self,direction:int):
        self.direction=direction
        super().__init__(emoji="◀️" if direction<0 else "▶️",style=discord.ButtonStyle.secondary,row=0)
    async def callback(self,interaction):
        view=self.view
        if not isinstance(view,BrowseLeagueCarouselView): return
        view.index=(view.index+self.direction)%len(view.active)
        view._rebuild()
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed=await build_carousel_embed(view.service,view.active,view.index)
        await interaction.edit_original_response(embed=embed,view=view,attachments=carousel_attachments(view.active, view.index))


class BrowseLeagueCarouselView(discord.ui.View):
    def __init__(self,service,active,index=0):
        super().__init__(timeout=300); self.service=service; self.active=[k for k in active if k in COMPETITIONS]; self.index=index if self.active else 0; self._rebuild()
    def _rebuild(self):
        self.clear_items()
        if not self.active:return
        info=COMPETITIONS[self.active[self.index]]
        self.add_item(BrowseNavButton(-1)); self.add_item(BrowseOpenLeagueButton(f"Voir {info['name']}")); self.add_item(BrowseNavButton(1))



class LeagueGridButton(discord.ui.Button):
    def __init__(self, key: str, mode: str, row: int):
        info = COMPETITIONS[key]
        super().__init__(label=info["name"][:70], emoji=info.get("emoji", "⚽"),
                         style=discord.ButtonStyle.primary if mode == "browse" else discord.ButtonStyle.success, row=row)
        self.key, self.mode = key, mode

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, LeagueGridView): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        matches = await view.service.matches_for_window(self.key, "future", 25)
        matches = sorted({str(m["event_id"]): m for m in matches}.values(), key=lambda m: str(m["commence_time"]))
        if self.mode == "browse":
            embed = await build_league_embed(view.service, self.key, matches)
            return await interaction.edit_original_response(embed=embed, view=MatchBrowserView(view.service, view.active, matches, self.key), attachments=[])
        if not matches:
            return await interaction.edit_original_response(embed=discord.Embed(title="⚽ AUCUN MATCH", description=f"**{COMPETITIONS[self.key]['name']}** n'a aucun match disponible pour le moment.", color=ODDIUM_MUTED), view=view, attachments=[])
        session = BetCarouselSession(view.service, view.active, matches, self.mode)
        await session.show(interaction)

class LeagueGridView(discord.ui.View):
    """V65: mur de compétitions, sans menu déroulant ni sélecteur de ligues."""
    def __init__(self, service: BettingService, active: list[str], mode: str = "browse"):
        super().__init__(timeout=600)
        self.service, self.active, self.mode = service, [k for k in active if k in COMPETITIONS], mode
        for i, key in enumerate(self.active[:20]):
            self.add_item(LeagueGridButton(key, mode, i // 4))

async def league_hub_embed(service: BettingService, active: list[str], mode: str = "browse") -> discord.Embed:
    counts=[]; total=0
    for key in active:
        n=len(await service.matches_for_window(key,"future",25)); total+=n
        counts.append(f"{COMPETITIONS[key].get('emoji','⚽')} **{COMPETITIONS[key]['name']}** · `{n:02d}` match(s)")
    title = "🎟️ ODDIUM • ARÈNE DES PARIS" if mode != "browse" else "⚽ ODDIUM • MATCH CENTER"
    subtitle = "Choisis ton terrain. Le prochain ticket commence ici." if mode != "browse" else "Tout le football d'Oddium, sans détour."
    e=discord.Embed(title=title, description=f"### {total:02d} MATCHS DISPONIBLES\n*{subtitle}*\n{ODDIUM_DIVIDER}\n"+"\n".join(counts), color=ODDIUM_GOLD)
    e.set_footer(text=_footer("Choisis directement une compétition"))
    return e

class BetModeView(discord.ui.View):
    """Sportsbook entry: choose the bet mode, then choose the competition in a visual carousel."""
    def __init__(self, service: BettingService, active: list[str]):
        super().__init__(timeout=180)
        self.service = service
        self.active = [k for k in active if k in COMPETITIONS]

    async def _open_leagues(self, interaction: discord.Interaction, mode: str):
        if not self.active:
            return await interaction.response.edit_message(
                embed=_empty_bet_carousel("DUEL SIMPLE" if mode == "simple" else "COMBO ROYAL"),
                view=HomeReturnView(self.service), attachments=[])
        view = LeagueGridView(self.service, self.active, mode)
        await interaction.response.edit_message(embed=await league_hub_embed(self.service, self.active, mode), view=view, attachments=[])

    @discord.ui.button(label="SIMPLE", emoji="🎯", style=discord.ButtonStyle.success)
    async def simple(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_leagues(interaction, "simple")

    @discord.ui.button(label="COMBINÉ", emoji="🧩", style=discord.ButtonStyle.primary)
    async def combo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_leagues(interaction, "combo")


class BetLeagueNavButton(discord.ui.Button):
    def __init__(self, direction: int):
        self.direction = direction
        super().__init__(emoji="◀️" if direction < 0 else "▶️", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, BetLeagueCarouselView) or not view.active:
            return
        view.index = (view.index + self.direction) % len(view.active)
        view._rebuild()
        await view.render(interaction)


class BetLeagueOpenButton(discord.ui.Button):
    def __init__(self, label: str):
        super().__init__(label=label, emoji="⚽", style=discord.ButtonStyle.primary, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, BetLeagueCarouselView) or not view.active:
            return
        key = view.active[view.index]
        # Acknowledge Discord before any provider/network work. 5Dollar may take
        # longer than Discord's interaction deadline on a cold cache.
        await interaction.response.defer(ephemeral=True, thinking=False)
        matches = await view.service.matches_for_window(key, "future", 25)
        unique = {str(m["event_id"]): m for m in matches}
        matches = sorted(unique.values(), key=lambda m: str(m["commence_time"]))
        if not matches:
            mode_name = "DUEL SIMPLE" if view.mode == "simple" else "COMBO ROYAL"
            embed = discord.Embed(
                title=f"◈ ODDIUM • {mode_name}",
                description=f"**{COMPETITIONS[key]['name']}**\n\nAucun match disponible pour le moment.",
                color=ODDIUM_GOLD,
            )
            return await interaction.edit_original_response(embed=embed, view=view, attachments=carousel_attachments(view.active, view.index))
        session = BetCarouselSession(view.service, view.active, matches, view.mode)
        await session.show(interaction)


class BetLeagueCarouselView(discord.ui.View):
    """Visual competition selector dedicated to betting; stays inside the user's single private page."""
    def __init__(self, service: BettingService, active: list[str], mode: str, index: int = 0):
        super().__init__(timeout=600)
        self.service = service
        self.active = [k for k in active if k in COMPETITIONS]
        self.mode = mode
        self.index = index % len(self.active) if self.active else 0
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        if not self.active:
            return
        info = COMPETITIONS[self.active[self.index]]
        self.add_item(BetLeagueNavButton(-1))
        self.add_item(BetLeagueOpenButton(f"Choisir {info['name']}"))
        self.add_item(BetLeagueNavButton(1))

    async def render(self, interaction: discord.Interaction):
        key = self.active[self.index]
        info = COMPETITIONS[key]
        mode_name = "DUEL SIMPLE" if self.mode == "simple" else "COMBO ROYAL"
        embed = discord.Embed(
            title=f"◈ ODDIUM • {mode_name}",
            description=(f"### CHOISIS TON CHAMPIONNAT\n"
                         f"**{info['name']}**\n"
                         f"Championnat **{self.index + 1}/{len(self.active)}**\n\n"
                         "Utilise `◀` `▶` puis ouvre le championnat."),
            color=ODDIUM_GOLD,
        )
        embed.set_footer(text=_footer("Sportsbook • sélection du championnat"))
        await interaction.response.edit_message(
            embed=embed, view=self, attachments=carousel_attachments(self.active, self.index))

def _empty_bet_carousel(mode: str):
    return discord.Embed(title=f"◈ ODDIUM • {mode}",description="Aucun match disponible pour le moment.",color=ODDIUM_GOLD)


class BetCarouselSession:
    """One private betting page, grouped by local match day.

    The carousel has two navigation levels:
    - day arrows switch the selected calendar day;
    - match arrows browse only fixtures scheduled on that day.
    """
    def __init__(self,service,active,matches,mode):
        self.service=service; self.active=active; self.all_matches=list(matches); self.mode=mode
        self.legs=[]; self.slip_message=None
        self.matches_by_day={}
        for match in self.all_matches:
            day=self._day_key(match)
            self.matches_by_day.setdefault(day,[]).append(match)
        for day_matches in self.matches_by_day.values():
            day_matches.sort(key=lambda x: parse_iso(x["commence_time"]))
        self.days=sorted(self.matches_by_day)
        self.day_index=0; self.index=0
        self.matches=self.matches_by_day[self.days[0]] if self.days else []

    @staticmethod
    def _day_key(match):
        return parse_iso(match["commence_time"]).astimezone(PARIS_TZ).date()

    @property
    def selected_day(self): return self.days[self.day_index]

    @property
    def match(self): return self.matches[self.index]

    def set_day(self,direction):
        if len(self.days)<=1:return
        self.day_index=(self.day_index+direction)%len(self.days)
        self.matches=self.matches_by_day[self.selected_day]
        self.index=0

    def move_match(self,direction):
        if self.matches:self.index=(self.index+direction)%len(self.matches)

    def total_odd(self):
        total=1.0
        for leg in self.legs: total*=float(leg["odd"])
        return total

    def match_text(self):
        """Text block used by the Components V2 betting page."""
        m=self.match; mode="DUEL SIMPLE" if self.mode=="simple" else "COMBO ROYAL"
        lines=[
            f"## ◈ ODDIUM • {mode}",
            f"**{m['competition_name']}**　•　`{fmt_dt(m['commence_time'])}`",
            f"### {m['home_team']}　　VS　　{m['away_team']}",
        ]
        if self.mode=="combo":
            lines += ["", f"**TICKET COMBINÉ** • {len(self.legs)} sélection(s) • cote `{self.total_odd():.2f}`"]
        return "\n".join(lines)

    def build_page(self):
        # Components V2 galleries reference message attachments by attachment:// URL.
        # Keep the File alive and pass it with the edit; putting discord.File directly
        # inside MediaGallery does not upload it and leaves the interaction unchanged.
        image = discord.File(match_card(dict(self.match), "prematch"), filename="oddium_match.png")
        return BetMatchCarouselLayout(self), image

    def build_layout(self):
        # Compatibility helper for callers that only need the component tree.
        return BetMatchCarouselLayout(self)

    async def show(self,interaction):
        # Components V2: DATE TOP -> content -> market -> MATCH BOTTOM.
        layout, image = self.build_page()
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=None, attachments=[image], view=layout)
        else:
            await interaction.response.edit_message(content=None, embed=None, attachments=[image], view=layout)

    async def refresh(self,interaction):
        layout, image = self.build_page()
        await interaction.response.edit_message(content=None, embed=None, attachments=[image], view=layout)


    async def ensure_slip(self,interaction):
        """The combo ticket is the single allowed extra ephemeral page and is then edited in place."""
        embed=combo_text_embed(self)
        view=ComboSlipView(self)
        if self.slip_message is not None:
            try:
                await self.slip_message.edit(embed=embed,view=view,attachments=[])
                return self.slip_message
            except (discord.NotFound,discord.HTTPException):
                self.slip_message=None
        self.slip_message=await interaction.followup.send(embed=embed,view=view,ephemeral=True,wait=True)
        return self.slip_message


class BetDayNav(discord.ui.Button):
    def __init__(self,direction):
        self.direction=direction
        super().__init__(label="←" if direction < 0 else "→", style=discord.ButtonStyle.secondary, row=0)
    async def callback(self,interaction):
        s=self.view.session; s.set_day(self.direction); await s.refresh(interaction)


class BetDateDisplay(discord.ui.Button):
    """Read-only centre button: visually separates day navigation from match navigation."""
    def __init__(self,session):
        super().__init__(label=session.selected_day.strftime("%d/%m/%y"), style=discord.ButtonStyle.secondary, disabled=True, row=0)


class BetCarouselNav(discord.ui.Button):
    def __init__(self,direction):
        self.direction=direction
        super().__init__(label="←" if direction < 0 else "→", style=discord.ButtonStyle.secondary, row=2)
    async def callback(self,interaction):
        s=self.view.session; s.move_match(self.direction); await s.refresh(interaction)


class BetMatchDisplay(discord.ui.Button):
    """Read-only match counter displayed between the bottom navigation arrows."""
    def __init__(self,session):
        super().__init__(label=f"MATCH {session.index + 1}/{len(session.matches)}", style=discord.ButtonStyle.secondary, disabled=True, row=2)


class CarouselOutcomeButton(discord.ui.Button):
    def __init__(self,session,selection,label,odd):
        style=discord.ButtonStyle.secondary if selection=="DRAW" else discord.ButtonStyle.primary
        super().__init__(label=f"{label}  {float(odd):.2f}",style=style,row=1)
        self.session=session; self.selection=selection; self.odd=float(odd)
    async def callback(self,interaction):
        s=self.session; m=s.match
        if s.mode=="simple":
            return await interaction.response.send_modal(StakeModal(s.service,str(m["event_id"]),self.selection,self.odd))
        if any(str(x["event_id"])==str(m["event_id"]) for x in s.legs):
            return await interaction.response.send_message("⚠️ Ce match est déjà présent dans ton combiné.",ephemeral=True)
        if len(s.legs)>=10:return await interaction.response.send_message("Maximum **10 sélections** par combiné.",ephemeral=True)
        s.legs.append({"event_id":str(m["event_id"]),"selection":self.selection,"odd":self.odd,"home":m["home_team"],"away":m["away_team"]})
        await interaction.response.defer(ephemeral=True)
        await s.ensure_slip(interaction)
        try:
            layout, image = s.build_page()
            await interaction.message.edit(content=None, embed=None, attachments=[image], view=layout)
        except (discord.NotFound,discord.HTTPException):
            pass


class BetMatchCarouselLayout(discord.ui.LayoutView):
    """Components V2 layout: DATE TOP -> CONTENT -> MARKET -> MATCH BOTTOM."""
    def __init__(self,session):
        super().__init__(timeout=600)
        self.session=session
        m=session.match

        # 1) DATE — physically first component in the Discord message.
        self.add_item(discord.ui.ActionRow(BetDayNav(-1), BetDateDisplay(session), BetDayNav(1)))

        # 2) Match information.
        self.add_item(discord.ui.TextDisplay(session.match_text()))

        # 3) Dynamic logo VS logo graphic. The actual file is attached by
        # BetCarouselSession.build_page(); MediaGallery only references it.
        gallery=discord.ui.MediaGallery()
        gallery.add_item(media="attachment://oddium_match.png", description=f"{m['home_team']} VS {m['away_team']}")
        self.add_item(gallery)

        # 4) 1 / N / 2 choices.
        self.add_item(discord.ui.ActionRow(
            CarouselOutcomeButton(session,"HOME","1",m["home_odd"]),
            CarouselOutcomeButton(session,"DRAW","N",m["draw_odd"]),
            CarouselOutcomeButton(session,"AWAY","2",m["away_odd"]),
        ))

        # 5) MATCH — physically last component in the Discord message.
        self.add_item(discord.ui.ActionRow(BetCarouselNav(-1), BetMatchDisplay(session), BetCarouselNav(1)))


BetMatchCarouselView = BetMatchCarouselLayout


def combo_text_embed(session):
    e=discord.Embed(title="🎟️ ODDIUM • COMBO ROYAL",color=ODDIUM_GOLD)
    if not session.legs:
        e.description=f"{ODDIUM_DIVIDER}\n**TICKET VIDE**\nAjoute au minimum **2 sélections** depuis le sélecteur.\n{ODDIUM_DIVIDER}"
    else:
        lines=[]
        for i,l in enumerate(session.legs,1):
            lab={"HOME":"1","DRAW":"N","AWAY":"2"}[l["selection"]]
            pick={"HOME":l["home"],"DRAW":"Match nul","AWAY":l["away"]}[l["selection"]]
            lines.append(f"`{i:02d}` **{l['home']} — {l['away']}**\n　└ `{lab}` **{pick}**　@ `{float(l['odd']):.2f}`")
        e.description=f"{ODDIUM_DIVIDER}\n"+"\n\n".join(lines)+f"\n{ODDIUM_DIVIDER}"
    e.add_field(name="SÉLECTIONS",value=f"**{len(session.legs)}/10**",inline=True)
    e.add_field(name="COTE TOTALE",value=f"**{session.total_odd():.2f}**",inline=True)
    e.add_field(name="STATUT",value="**PRÊT À VALIDER**" if len(session.legs)>=2 else "**EN CONSTRUCTION**",inline=True)
    e.set_footer(text=_footer("Ticket combiné • cette fenêtre s'actualise sans se dupliquer"))
    return e


class ComboSlipView(discord.ui.View):
    """Only ticket management lives here: clear and validate."""
    def __init__(self,session):
        super().__init__(timeout=900); self.session=session

    @discord.ui.button(label="Vider",emoji="🗑️",style=discord.ButtonStyle.danger)
    async def clear(self,interaction,button):
        self.session.legs.clear()
        await interaction.response.edit_message(embed=combo_text_embed(self.session),view=ComboSlipView(self.session),attachments=[])

    @discord.ui.button(label="Valider",emoji="✅",style=discord.ButtonStyle.success)
    async def validate(self,interaction,button):
        if len(self.session.legs)<2:return await interaction.response.send_message("Ajoute au moins **2 matchs** au combiné.",ephemeral=True)
        await interaction.response.send_modal(ComboStakeModal(self.session))


class ComboStakeModal(discord.ui.Modal,title="🧩 Valider le combiné"):
    stake=discord.ui.TextInput(label="Mise",placeholder=f"Ex: 500 {SETTINGS.currency_name}",min_length=1,max_length=12)
    def __init__(self,session): super().__init__(timeout=180); self.session=session
    async def on_submit(self,interaction):
        raw=str(self.stake.value).replace(" ","").replace(",","")
        if not raw.isdigit():return await interaction.response.send_message("❌ Mise invalide.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok,reason,data=await self.session.service.place_combo_bet(interaction.user.id,self.session.legs,int(raw))
        if not ok:
            if reason=="ODD_CHANGED":reason="Une cote a changé. Recompose le combiné pour accepter les nouvelles cotes."
            return await interaction.followup.send(f"❌ {reason}",ephemeral=True)
        accepted=discord.Embed(title="✅ ODDIUM • COMBINÉ VALIDÉ",color=ODDIUM_GREEN)
        accepted.description=(f"🎟️ **COMBO-{data['combo_id']}**\n🧩 **{len(self.session.legs)} sélections**\n📈 Cote **{data['total_odd']:.2f}**\n"
                              f"💰 Mise **{fmt_num(int(raw))} {SETTINGS.currency_name}**\n🏆 Gain potentiel **{fmt_num(data['payout'])} {SETTINGS.currency_name}**")
        if self.session.slip_message:
            try: await self.session.slip_message.edit(embed=accepted,view=None,attachments=[])
            except (discord.NotFound,discord.HTTPException): await interaction.followup.send(embed=accepted,ephemeral=True)
        else: await interaction.followup.send(embed=accepted,ephemeral=True)
        self.session.legs.clear()


# ========================= ODDIUM EXPERIENCE =========================


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
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed=await build_carousel_embed(self.service,self.active,0)
        await interaction.edit_original_response(embed=await league_hub_embed(self.service,self.active,"browse"),view=LeagueGridView(self.service,self.active,"browse"),attachments=[])
    @discord.ui.button(label="Parier",emoji="🎟️",style=discord.ButtonStyle.success)
    async def bet(self,interaction,button):
        e=discord.Embed(title="🎟️ CHOISIS TON STYLE DE JEU",description="🎯 **Pari simple** — un pronostic 1/N/2\n🧩 **Pari combiné** — plusieurs matchs, une cote totale",color=discord.Color.gold())
        await interaction.response.edit_message(embed=e,view=BetModeView(self.service,self.active),attachments=[])






class RankProfileView(discord.ui.View):
    def __init__(self,service):super().__init__(timeout=300);self.service=service
    @discord.ui.button(label="Mon profil",emoji="👤",style=discord.ButtonStyle.primary)
    async def profile(self,interaction,button):
        await interaction.response.edit_message(embed=await _profile_embed(self.service,interaction.user),view=HomeReturnView(self.service))
    @discord.ui.button(label="Accueil",emoji="🏠",style=discord.ButtonStyle.secondary)
    async def home(self,interaction,button):
        active=carousel_keys(await self.service.active_competitions()); await interaction.response.edit_message(embed=await build_title_embed(self.service),view=QuickHomeView(self.service,active))

# ========================= PREMIUM CLEAN UI =========================
# Direction artistique inspirée des meilleures pratiques de PronoBot :
# hiérarchie courte, états lisibles, une action = un écran, live sans image lourde.

ODDIUM_GOLD = 0xE9B949
ODDIUM_DARK = 0x171A21
ODDIUM_RED = 0xED4245
ODDIUM_GREEN = 0x3BA55D
ODDIUM_BLUE = 0x5865F2
ODDIUM_MUTED = 0x99AAB5
ODDIUM_DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ODDIUM_SIGNATURE = "ODDIUM • 5Dollar Football • Jeu virtuel"


def _footer(text: str = "") -> str:
    return f"{ODDIUM_SIGNATURE} • {text}" if text else ODDIUM_SIGNATURE



def _safe_odd(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _bookmaker_header(section: str, subtitle: str = "") -> str:
    line = f"### ◈ {section.upper()} ◈\n{ODDIUM_DIVIDER}"
    return line + (f"\n{subtitle}" if subtitle else "")

def _market_triplet(match) -> str:
    return (
        f"` 1  {_safe_odd(match['home_odd'])} `　"
        f"` N  {_safe_odd(match['draw_odd'])} `　"
        f"` 2  {_safe_odd(match['away_odd'])} `"
    )

def _selection_name(selection: str, match) -> str:
    return {
        "HOME": str(match["home_team"]),
        "DRAW": "Match nul",
        "AWAY": str(match["away_team"]),
    }.get(selection, selection)


def _phase_badge(row) -> tuple[str, int]:
    phase = str(row["live_phase"] or row["match_status"] or "live").lower()
    clock = str(row["live_clock"] or "").strip()
    if phase == "kickoff_wait":
        return "🟠 DÉMARRAGE", ODDIUM_GOLD
    if phase in {"first_half", "live", "second_half"}:
        return f"🔴 {clock or 'DIRECT'}", ODDIUM_RED
    if phase == "halftime":
        return "⏸️ MI-TEMPS", ODDIUM_GOLD
    if phase == "extra_time":
        return f"⏱️ PROLONG. {clock}".strip(), ODDIUM_RED
    if phase == "penalties":
        return "🎯 TIRS AU BUT", ODDIUM_RED
    if phase == "suspended":
        return "⏸️ SUSPENDU", ODDIUM_GOLD
    if phase == "finished":
        return "✅ TERMINÉ", ODDIUM_GREEN
    if phase == "postponed":
        return "📅 REPORTÉ", ODDIUM_MUTED
    if phase == "cancelled":
        return "❌ ANNULÉ", ODDIUM_MUTED
    return "🔴 DIRECT", ODDIUM_RED


def _score_line(row) -> str:
    hs = "–" if row["home_score"] is None else str(row["home_score"])
    aws = "–" if row["away_score"] is None else str(row["away_score"])
    return f"**{row['home_team']}**   `{hs}  —  {aws}`   **{row['away_team']}**"


async def build_title_embed(service: BettingService) -> discord.Embed:
    active = carousel_keys(await service.active_competitions())
    available = sum(len(await service.matches_for_window(k, "future", 25)) for k in active)
    live_count = len(await service.live_matches(25))
    pending = await service.db.fetchone("SELECT COUNT(*) c FROM bets WHERE status='PENDING'")
    combo_pending = await service.db.fetchone("SELECT COUNT(*) c FROM combo_bets WHERE status='PENDING'")
    open_tickets = int(pending["c"] if pending else 0) + int(combo_pending["c"] if combo_pending else 0)
    e=discord.Embed(title="✦  O D D I U M  ✦", description=(
        "### LE STADE T'ATTEND.\n**Analyse. Ose. Mise. Entre dans la légende.**\n"
        f"{ODDIUM_DIVIDER}\n"
        f"⚽ **{available}** matchs　 🔴 **{live_count}** en direct　 🎟️ **{open_tickets}** tickets ouverts\n"
        f"{ODDIUM_DIVIDER}\n"
        "**⚔️ MATCH CENTER** — explore les affiches\n"
        "**🎟️ ARÈNE DES PARIS** — simple ou combo\n"
        "**🏦 COFFRE** — fortune, carrière et prestige\n"
        "**👑 HALL OF FAME** — grave ton nom au sommet"), color=ODDIUM_GOLD)
    e.set_image(url="attachment://oddium_welcome.png")
    e.set_footer(text=_footer("Le jeu commence ici"))
    return e


class BrowseMatchSelect(discord.ui.Select):
    def __init__(self, service: BettingService, matches):
        opts = []
        for m in matches[:25]:
            opts.append(discord.SelectOption(
                label=f"{m['home_team']} — {m['away_team']}"[:100],
                description=f"{fmt_dt(m['commence_time'])}  •  1 {_safe_odd(m['home_odd'])}  N {_safe_odd(m['draw_odd'])}  2 {_safe_odd(m['away_odd'])}"[:100],
                value=str(m["event_id"]), emoji="⚽",
            ))
        super().__init__(placeholder="Choisir un match…", options=opts, min_values=1, max_values=1, row=1)
        self.service = service

    async def callback(self, interaction: discord.Interaction):
        m = await self.service.db.fetchone("SELECT * FROM matches WHERE event_id=?", (self.values[0],))
        if not m:
            await interaction.response.send_message("Ce match n'est plus disponible.", ephemeral=True)
            return
        trend = await self.service.odds_trend(m["event_id"])
        e = discord.Embed(
            title=f"{m['home_team']}  —  {m['away_team']}",
            description=f"**{m['competition_name']}**\n🕒 {fmt_dt(m['commence_time'])}",
            color=ODDIUM_GOLD,
        )
        e.add_field(name=f"1  {m['home_team']}", value=f"### `{_safe_odd(m['home_odd'])}` {trend['home']}", inline=True)
        e.add_field(name="N  Match nul", value=f"### `{_safe_odd(m['draw_odd'])}` {trend['draw']}", inline=True)
        e.add_field(name=f"2  {m['away_team']}", value=f"### `{_safe_odd(m['away_odd'])}` {trend['away']}", inline=True)
        e.set_footer(text="Consultation • pour miser, utilise 🎟️ Parier")
        await interaction.response.edit_message(content=None, embed=e, view=HomeReturnView(self.service), attachments=[])


async def _my_bets_embed(service, user_id):
    # Opening Mes tickets also repairs stale PENDING results immediately.
    try:
        await service.reconcile_open_tickets()
    except Exception:
        pass
    simple = await service.user_bets(user_id, None, 20)
    combos = await service.user_combo_bets(user_id, 10)
    pending_count = sum(1 for b in simple if str(b["status"]) == "PENDING") + sum(1 for c, _ in combos if str(c["status"]) == "PENDING")
    e = discord.Embed(
        title="🎟️  ODDIUM • MES TICKETS",
        description=f"### PORTEFEUILLE DE PARIS\n`{pending_count:02d}` **EN COURS**　•　🟢 GAGNÉ　•　🔴 PERDU　•　⚪ REMBOURSÉ\n{ODDIUM_DIVIDER}",
        color=ODDIUM_BLUE,
    )
    if simple:
        lines = []
        for b in simple[:6]:
            icon = BET_STATUS_ICONS.get(b["status"], "⚪")
            sel = {"HOME": "1", "DRAW": "N", "AWAY": "2"}.get(b["selection"], b["selection"])
            lines.append(
                f"{icon} **BET-{b['id']}**  ·  {b['home_team']} — {b['away_team']}\n"
                f"　`{sel} @ {_safe_odd(b['odd'])}`  •  mise **{fmt_num(b['stake'])}**  •  retour **{fmt_num(b['potential_payout'])} {SETTINGS.currency_name}**"
            )
        e.add_field(name="SIMPLES", value="\n\n".join(lines)[:1024], inline=False)
    if combos:
        lines = []
        for c, legs in combos[:4]:
            icon = {"PENDING": "🟡", "WON": "🟢", "LOST": "🔴", "VOID": "⚪"}.get(str(c["status"]), "⚪")
            lines.append(
                f"{icon} **COMBO-{c['id']}**  ·  {len(legs)} sélections  ·  cote **{float(c['total_odd']):.2f}**\n"
                f"　Mise **{fmt_num(c['stake'])}**  •  retour **{fmt_num(c['potential_payout'])} {SETTINGS.currency_name}**"
            )
        e.add_field(name="COMBINÉS", value="\n\n".join(lines)[:1024], inline=False)
    if not simple and not combos:
        e.description = "### Aucun ticket pour le moment\nCrée ton premier pari depuis l'accueil."
    e.set_footer(text=_footer("Cotes verrouillées à la validation"))
    return e


async def _profile_embed(service, user):
    s = await service.user_stats(user.id)
    combos = await service.user_combo_bets(user.id, 100)
    wins = int(s["wins"] or 0); losses = int(s["losses"] or 0); settled = wins + losses
    returned = int(s["returned"] or 0); wagered = int(s["wagered"] or 0); net = returned - wagered
    rate = (wins / settled * 100) if settled else 0
    balance = await service.economy.get_balance(user.id)
    e = discord.Embed(
        title=f"👤  ODDIUM • {user.display_name}",
        description=f"### PROFIL JOUEUR\nPerformances sur les paris réglés.\n{ODDIUM_DIVIDER}",
        color=ODDIUM_GOLD,
    )
    e.set_thumbnail(url=user.display_avatar.url)
    e.add_field(name="COFFRE", value=f"### {fmt_num(balance)}\n{SETTINGS.currency_name}", inline=True)
    e.add_field(name="BÉNÉFICE", value=f"### {net:+,}".replace(',', ' ') + f"\n{SETTINGS.currency_name}", inline=True)
    e.add_field(name="RÉUSSITE", value=f"### {rate:.0f}%\n{wins}/{settled}", inline=True)
    e.add_field(name="PARIS SIMPLES", value=f"**{int(s['total'] or 0)}** joués  •  🟢 {wins}  •  🔴 {losses}", inline=False)
    e.add_field(name="COMBINÉS", value=f"**{len(combos)}** tickets créés", inline=True)
    e.add_field(name="RECORD", value=f"Gain **{fmt_num(int(s['biggest_win'] or 0))}**\nCote **{float(s['biggest_odd'] or 0):.2f}**", inline=True)
    return e


async def _leaderboard_embed(service, bot):
    rows = await service.leaderboard(10)
    e = discord.Embed(title="🏆  ODDIUM • HALL OF FAME", description=f"### HALL OF FAME GÉNÉRAL\n**Bénéfice net • performance • prestige**\n{ODDIUM_DIVIDER}", color=ODDIUM_GOLD)
    if not rows:
        e.description += "\n\nAucun joueur classé pour le moment."
        return e
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows, 1):
        u = bot.get_user(int(r["user_id"])); name = u.display_name if u else f"Joueur {r['user_id']}"
        rate = (int(r["wins"]) / int(r["settled"]) * 100) if int(r["settled"]) else 0
        rank = medals[i - 1] if i <= 3 else f"`#{i:02d}`"
        lines.append(f"{rank} **{name}**\n　**{int(r['net']):+,} {SETTINGS.currency_name}**  •  {rate:.0f}% réussite".replace(',', ' '))
    e.description += "\n\n" + "\n".join(lines)
    return e




class LiveDetailsSelect(discord.ui.Select):
    def __init__(self, service: BettingService, matches):
        options = []
        for m in matches[:25]:
            hs = "–" if m["home_score"] is None else str(m["home_score"])
            aws = "–" if m["away_score"] is None else str(m["away_score"])
            badge, _ = _phase_badge(m)
            options.append(discord.SelectOption(
                label=f"{m['home_team']} — {m['away_team']}"[:100],
                description=f"{hs}-{aws} • {badge.replace('🔴 ','').replace('🟠 ','')}"[:100],
                value=str(m["event_id"]), emoji="📊",
            ))
        super().__init__(placeholder="Ouvrir la fiche d'un match…", options=options, min_values=1, max_values=1)
        self.service = service

    @staticmethod
    def _stat_value(block, *names):
        vals = block.get("values") or {}
        lowered = {str(k).lower(): v for k, v in vals.items()}
        for name in names:
            if name.lower() in lowered:
                value = lowered[name.lower()]
                return "—" if value is None else str(value)
        return "—"

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.service.live_match_details(self.values[0])
        m = data.get("match")
        if not m:
            await interaction.followup.send("Ce match n'est plus disponible.", ephemeral=True)
            return
        badge, color = _phase_badge(m)
        hs = "–" if m["home_score"] is None else str(m["home_score"])
        aws = "–" if m["away_score"] is None else str(m["away_score"])
        e = discord.Embed(
            title=f"{m['home_team']}   {hs} — {aws}   {m['away_team']}",
            description=f"**{m['competition_name']}**  •  {badge}",
            color=color,
        )

        external = data.get("external") or {}
        stats = external.get("statistics") or []
        if len(stats) >= 2:
            left, right = stats[0], stats[1]
            metrics = [
                ("Possession", ("Ball Possession", "Possession")),
                ("Tirs", ("Total Shots", "Shots")),
                ("Cadrés", ("Shots on Goal", "Shots on Target")),
                ("Corners", ("Corner Kicks", "Corners")),
                ("Fautes", ("Fouls",)),
                ("Hors-jeu", ("Offsides",)),
            ]
            left_name = str(left.get("team") or m["home_team"])
            right_name = str(right.get("team") or m["away_team"])
            stat_lines = [f"**{left_name}**　　　**{right_name}**"]
            for label, names in metrics:
                lv = self._stat_value(left, *names); rv = self._stat_value(right, *names)
                stat_lines.append(f"`{lv:>4}`  **{label}**  `{rv:<4}`")
            e.add_field(name="📊 STATISTIQUES", value="\n".join(stat_lines)[:1024], inline=False)

        events = _dedupe_live_highlights(data.get("events") or [])
        useful = [ev for ev in events if str(ev["event_type"]) not in {"live_update", "clock_update", "phase_change"}]
        if useful:
            lines = []
            for ev in useful[-8:]:
                label = LIVE_EVENT_LABELS.get(str(ev["event_type"]), "•")
                when = str(ev["clock"] or "").strip() or "•"
                detail = str(ev["detail"] or "").strip()
                lines.append(f"`{when:>3}`  {label}" + (f"  **{detail}**" if detail else ""))
            e.add_field(name="📜 TEMPS FORTS", value="\n".join(lines)[-1024:], inline=False)
        else:
            e.add_field(name="📜 TEMPS FORTS", value="Aucun événement majeur signalé pour le moment.", inline=False)

        e.set_footer(text="ODDIUM LIVE • ouvre le Match Center ci-dessous")
        # Keep the first response compact, then let the user switch between
        # summary/stats/highlights/market without spawning new Discord messages.
        await interaction.followup.send(
            embed=e,
            view=MatchCenterView(self.service, str(m["event_id"]), interaction.user.id),
            ephemeral=True,
        )


class LiveDetailsSelectView(discord.ui.View):
    def __init__(self, service, matches):
        super().__init__(timeout=90)
        self.add_item(LiveDetailsSelect(service, matches))


class LivePanelView(discord.ui.View):
    def __init__(self, service: BettingService):
        super().__init__(timeout=None); self.service = service

    @discord.ui.button(label="Détails", style=discord.ButtonStyle.primary, emoji="📊", custom_id="oddium:v13:live:details", row=0)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches = await self.service.live_matches(25)
        if not matches:
            return await interaction.response.send_message("⚽ Aucun match live actuellement.", ephemeral=True)
        await interaction.response.edit_message(content="**Choisis un match pour ouvrir sa fiche :**", embed=None, view=LiveDetailsSelectView(self.service, matches), attachments=[])

    @discord.ui.button(label="Suivre", style=discord.ButtonStyle.danger, emoji="🔔", custom_id="oddium:v13:live:follow", row=0)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches = await self.service.live_matches(25)
        if not matches:
            return await interaction.response.send_message("⚽ Aucun match live à suivre.", ephemeral=True)
        followed = await self.service.followed_event_ids(interaction.user.id)
        await interaction.response.edit_message(content="**Choisis le match à suivre :**", embed=None, view=LiveFollowSelectView(self.service, matches, followed), attachments=[])

    @discord.ui.button(label="Mes tickets live", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="oddium:v13:live:bets", row=0)
    async def bets(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await self.service.user_live_bets(interaction.user.id)
        if not rows:
            return await interaction.response.send_message("🎟️ Aucun de tes paris n'est actuellement en direct.", ephemeral=True)
        lines = []
        for r in rows[:8]:
            hs = r["home_score"] if r["home_score"] is not None else "–"; aws = r["away_score"] if r["away_score"] is not None else "–"
            winning = (r["selection"] == "HOME" and isinstance(hs, int) and isinstance(aws, int) and hs > aws) or (r["selection"] == "AWAY" and isinstance(hs, int) and isinstance(aws, int) and aws > hs) or (r["selection"] == "DRAW" and isinstance(hs, int) and hs == aws)
            state = "🟢 EN POSITION" if winning else "⚪ EN COURS"
            lines.append(f"{state}  **{r['home_team']} `{hs}-{aws}` {r['away_team']}**\n　BET-{r['id']} • mise **{fmt_num(r['stake'])}** • retour **{fmt_num(r['potential_payout'])} {SETTINGS.currency_name}**")
        await interaction.response.edit_message(content=None, embed=discord.Embed(title="🎟️ MES TICKETS LIVE", description="\n\n".join(lines), color=ODDIUM_GREEN), view=HomeReturnView(self.service), attachments=[])


async def _balance_embed(service: BettingService, user: discord.abc.User) -> discord.Embed:
    """V64 — tableau de bord joueur : spectaculaire, lisible et 100 % Discord natif."""
    try:
        balance = await service.economy.get_balance(user.id)
        stats = await service.user_stats(user.id)
        combos = await service.user_combo_bets(user.id, 100)
    except Exception:
        e = discord.Embed(
            title="💰  ODDIUM • COFFRE JOUEUR",
            description="⚠️ **Le coffre est momentanément inaccessible.**\nRéessaie dans quelques instants.",
            color=ODDIUM_RED,
        )
        e.set_thumbnail(url=user.display_avatar.url)
        e.set_footer(text=_footer("Espace joueur"))
        return e

    wins = int(stats["wins"] or 0)
    losses = int(stats["losses"] or 0)
    settled = wins + losses
    total = int(stats["total"] or 0)
    wagered = int(stats["wagered"] or 0)
    returned = int(stats["returned"] or 0)
    net = returned - wagered
    rate = (wins / settled * 100) if settled else 0.0
    roi = (net / wagered * 100) if wagered else 0.0
    biggest_win = int(stats["biggest_win"] or 0)
    biggest_odd = float(stats["biggest_odd"] or 0)

    # Progression purement joueur : 1 niveau tous les 10 paris réglés.
    # Aucun nouveau stockage : elle suit automatiquement l'historique existant.
    level = max(1, settled // 10 + 1)
    level_progress = settled % 10
    filled = level_progress
    bar = "▰" * filled + "▱" * (10 - filled)

    if settled == 0:
        title, aura = "🌱 ESPOIR", "Premier ticket, première histoire."
    elif settled < 10:
        title, aura = "🥉 CHALLENGER", "La réputation commence ici."
    elif settled < 25:
        title, aura = "🥈 PRONOSTIQUEUR", "Tu commences à faire parler les cotes."
    elif settled < 50:
        title, aura = "🥇 EXPERT", "Ton nom s'installe dans le sportsbook."
    elif settled < 100:
        title, aura = "💎 ÉLITE", "Les gros tickets ne te font plus peur."
    else:
        title, aura = "👑 LÉGENDE ODDIUM", "Une carrière que le serveur connaît."

    if settled == 0:
        streak_text = "🌱 **NOUVEAU JOUEUR**"
    elif rate >= 70:
        streak_text = "🔥 **EN FEU**"
    elif rate >= 50:
        streak_text = "⚡ **EN FORME**"
    else:
        streak_text = "🎯 **EN CHASSE**"

    net_icon = "📈" if net >= 0 else "📉"
    roi_sign = "+" if roi > 0 else ""

    e = discord.Embed(
        title=f"🏦  COFFRE D'ODDIUM • {user.display_name.upper()}",
        description=(
            f"### 🪙  {fmt_num(balance)} {SETTINGS.currency_name}\n"
            f"**FORTUNE DISPONIBLE**\n"
            f"{ODDIUM_DIVIDER}\n"
            f"{title}　•　**NIVEAU {level}**\n"
            f"*{aura}*"
        ),
        color=ODDIUM_GOLD,
    )
    e.set_thumbnail(url=user.display_avatar.url)

    e.add_field(
        name="🔥 ÉTAT DE FORME",
        value=f"{streak_text}\n**{rate:.0f}%** de réussite",
        inline=True,
    )
    e.add_field(
        name="🎟️ CARRIÈRE",
        value=f"**{settled}** paris réglés\n🟢 {wins}　🔴 {losses}",
        inline=True,
    )
    e.add_field(
        name=f"{net_icon} PERFORMANCE",
        value=f"**{net:+,} {SETTINGS.currency_name}**\nROI **{roi_sign}{roi:.1f}%**".replace(',', ' '),
        inline=True,
    )

    e.add_field(
        name="⭐ PROGRESSION",
        value=f"`{bar}`  **{level_progress}/10**\nEncore **{10-level_progress}** pari(s) réglé(s) avant le niveau {level + 1}.",
        inline=False,
    )

    e.add_field(
        name="👑 PLUS GROS COUP",
        value=f"### +{fmt_num(biggest_win)}\n{SETTINGS.currency_name}",
        inline=True,
    )
    e.add_field(
        name="🎯 COTE RECORD",
        value=f"### {biggest_odd:.2f}\nréussie",
        inline=True,
    )
    e.add_field(
        name="🧩 COMBINÉS",
        value=f"### {len(combos)}\ntickets créés",
        inline=True,
    )

    e.add_field(
        name="💸 EMPIRE DU JOUEUR",
        value=(
            f"**{fmt_num(wagered)} {SETTINGS.currency_name}** engagés depuis tes débuts\n"
            f"**{total}** ticket(s) enregistré(s) • Chaque pari écrit ta légende."
        ),
        inline=False,
    )
    e.set_footer(text=_footer("Fortune • Carrière • Prestige"))
    return e


class BalanceView(discord.ui.View):
    """Navigation du coffre V64 : les actions utiles sont accessibles sans revenir au menu."""
    def __init__(self, service: BettingService):
        super().__init__(timeout=300)
        self.service = service

    @discord.ui.button(label="Parier", emoji="🎟️", style=discord.ButtonStyle.success, row=0)
    async def bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        active = carousel_keys(await self.service.active_competitions())
        if not active:
            return await interaction.response.send_message("⚽ Aucun championnat actif pour le moment.", ephemeral=True)
        e = discord.Embed(
            title="🎟️  ODDIUM • SPORTSBOOK",
            description="### À TOI DE JOUER\n**Simple ou combiné : choisis ton terrain.**\n" + ODDIUM_DIVIDER,
            color=ODDIUM_GOLD,
        )
        e.add_field(name="🎯 DUEL SIMPLE", value="Un match • une sélection • une cote", inline=True)
        e.add_field(name="🧩 COMBINÉ", value="Plusieurs matchs • une cote cumulée", inline=True)
        await interaction.response.edit_message(embed=e, view=BetModeView(self.service, active), attachments=[])

    @discord.ui.button(label="Mes tickets", emoji="📜", style=discord.ButtonStyle.primary, row=0)
    async def my_bets(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=await _my_bets_embed(self.service, interaction.user.id),
            view=HomeReturnView(self.service),
            attachments=[],
        )

    @discord.ui.button(label="Hall of Fame", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=await _leaderboard_embed(self.service, interaction.client),
            view=RankProfileView(self.service),
            attachments=[],
        )

    @discord.ui.button(label="Actualiser", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=False)
        await interaction.edit_original_response(embed=await _balance_embed(self.service, interaction.user), view=self, attachments=[])

    @discord.ui.button(label="Accueil", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        active = carousel_keys(await self.service.active_competitions())
        await interaction.response.edit_message(embed=await build_title_embed(self.service), view=QuickHomeView(self.service, active), attachments=[])


class MainPanelView(discord.ui.View):
    """Cinq accès maximum, aucune action redondante."""
    def __init__(self, service: BettingService, active: list[str]):
        super().__init__(timeout=None); self.service = service; self.active = [k for k in active if k in COMPETITIONS]

    @discord.ui.button(label="Matchs", emoji="⚽", style=discord.ButtonStyle.primary, custom_id="oddium:v13:matches", row=0)
    async def matches(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=False)
        active = carousel_keys(await self.service.active_competitions())
        if not active:
            return await interaction.followup.send("Aucun championnat actif.", ephemeral=True)
        await open_private_page(interaction, embed=await league_hub_embed(self.service, active, "browse"), view=LeagueGridView(self.service, active, "browse"), replace_existing=True)

    @discord.ui.button(label="Parier", emoji="🎟️", style=discord.ButtonStyle.success, custom_id="oddium:v13:bet", row=0)
    async def bet(self, interaction, button):
        # Acknowledge the permanent-panel click before DB/provider work.
        # Without this, a cold request can make Discord show a dead button.
        await interaction.response.defer(ephemeral=True, thinking=False)
        active = carousel_keys(await self.service.active_competitions())
        if not active:
            return await interaction.followup.send("Aucun championnat actif.", ephemeral=True)
        e = discord.Embed(
            title="✦ ODDIUM • ARÈNE DES PARIS",
            description=(
                _bookmaker_header("BET DESK", "Compose ton ticket comme sur un vrai bookmaker.")
                + "\n\n`SIMPLE`  1 sélection • cote fixe à validation"
                + "\n`COMBINÉ` plusieurs sélections • cote cumulée"
                + "\n\n**Marché principal**　`1` Domicile　`N` Nul　`2` Extérieur"
            ),
            color=ODDIUM_GOLD,
        )
        e.add_field(name="◆ SOURCE DES COTES", value="**Bet365 via 5Dollar**\nContrôle de la cote au moment de la validation.", inline=False)
        e.set_footer(text=_footer("Sportsbook • choisis SIMPLE ou COMBINÉ"))
        await open_private_page(interaction, embed=e, view=BetModeView(self.service, active), replace_existing=True)

    @discord.ui.button(label="Mes tickets", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="oddium:v13:mybets", row=0)
    async def mybets(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=False)
        await open_private_page(interaction, embed=await _my_bets_embed(self.service, interaction.user.id), view=HomeReturnView(self.service), replace_existing=True)

    @discord.ui.button(label="Coffre", emoji="💰", style=discord.ButtonStyle.secondary, custom_id="oddium:v56:balance", row=1)
    async def balance(self, interaction, button):
        # ACK Discord *before* any database/private-page operation.  The previous
        # defer + followup path could occasionally arrive after Discord's component
        # acknowledgement window and raise 10062 (Unknown interaction).
        loading = discord.Embed(
            title="💰  ODDIUM • MON COFFRE",
            description="🔐 **Ouverture de ton coffre…**",
            color=ODDIUM_GOLD,
        )
        try:
            await interaction.response.send_message(embed=loading, ephemeral=True)
        except discord.NotFound:
            log.warning(
                "[COFFRE] interaction expirée avant ACK • user_id=%s • interaction_id=%s",
                interaction.user.id, interaction.id,
            )
            return

        # Once acknowledged, PostgreSQL can take as long as needed without losing
        # the Discord interaction token. Edit the already-created ephemeral message
        # instead of creating a followup.
        try:
            embed = await _balance_embed(self.service, interaction.user)
            await interaction.edit_original_response(embed=embed, view=BalanceView(self.service))
        except discord.NotFound:
            log.warning(
                "[COFFRE] réponse éphémère introuvable après ACK • user_id=%s",
                interaction.user.id,
            )
        except Exception:
            log.exception("[COFFRE] erreur affichage • user_id=%s", interaction.user.id)
            try:
                error_embed = discord.Embed(
                    title="💰  ODDIUM • MON COFFRE",
                    description="⚠️ **Impossible de charger le solde pour le moment.**\nRéessaie dans quelques instants.",
                    color=ODDIUM_RED,
                )
                await interaction.edit_original_response(embed=error_embed, view=BalanceView(self.service))
            except Exception:
                pass

    @discord.ui.button(label="Live", emoji="🔴", style=discord.ButtonStyle.danger, custom_id="oddium:v13:live", row=1)
    async def live(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=False)
        await open_private_page(interaction, embed=await _live_embed(self.service), view=LivePanelView(self.service), replace_existing=True)

    @discord.ui.button(label="Hall of Fame", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="oddium:v13:rank", row=1)
    async def rank(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=False)
        e = await _leaderboard_embed(self.service, interaction.client)
        await open_private_page(interaction, embed=e, view=RankProfileView(self.service), replace_existing=True)

# --- Premium betting flow ---------------------------------------------------

class StakeModal(discord.ui.Modal, title="🎟️ Confirmer la mise"):
    stake = discord.ui.TextInput(
        label=f"Mise en {SETTINGS.currency_name}",
        placeholder=f"Exemple : 500",
        min_length=1,
        max_length=12,
    )

    def __init__(self, service: BettingService, event_id: str, selection: str, displayed_odd: float):
        super().__init__(timeout=180)
        self.service = service
        self.event_id = event_id
        self.selection = selection
        self.displayed_odd = displayed_odd

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.stake.value).replace(" ", "").replace(",", "")
        if not raw.isdigit():
            return await interaction.response.send_message("❌ Entre une mise entière valide.", ephemeral=True)
        stake = int(raw)
        await interaction.response.defer(ephemeral=True)
        async with GUARD.locks[interaction.user.id]:
            ok, reason, data = await self.service.place_bet(
                interaction.user.id, self.event_id, self.selection, stake, self.displayed_odd
            )
        if not ok and reason == "ODD_CHANGED" and data:
            current = float(data["current_odd"])
            payout = int(stake * current)
            e = discord.Embed(
                title="⚠️ La cote a bougé",
                description=(
                    f"Ancienne cote  ~~{self.displayed_odd:.2f}~~   →   **{current:.2f}**\n\n"
                    f"Mise **{fmt_num(stake)} {SETTINGS.currency_name}**\n"
                    f"Nouveau retour potentiel **{fmt_num(payout)} {SETTINGS.currency_name}**"
                ),
                color=ODDIUM_GOLD,
            )
            e.set_footer(text="Aucun débit n'a encore été effectué")
            return await interaction.followup.send(
                embed=e,
                view=ChangedOddView(self.service, self.event_id, self.selection, current, stake),
                ephemeral=True,
            )
        if not ok:
            return await interaction.followup.send(f"❌ {reason}", ephemeral=True)

        match = data["match"]
        balance = await self.service.economy.get_balance(interaction.user.id)
        label = {"HOME": match["home_team"], "DRAW": "Match nul", "AWAY": match["away_team"]}[self.selection]
        e = discord.Embed(
            title="◆  ODDIUM • TICKET ACCEPTÉ",
            description=f"**BET-{data['bet_id']}**  •  {match['competition_name']}",
            color=ODDIUM_GREEN,
        )
        e.add_field(name="MATCH", value=f"**{match['home_team']}  —  {match['away_team']}**", inline=False)
        e.add_field(name="PRONOSTIC", value=f"**{label}**\nCote `{data['odd']:.2f}`", inline=True)
        e.add_field(name="MISE", value=f"**{fmt_num(stake)}**\n{SETTINGS.currency_name}", inline=True)
        e.add_field(name="RETOUR POTENTIEL", value=f"**{fmt_num(data['payout'])}**\n{SETTINGS.currency_name}", inline=True)
        e.set_footer(text=f"Coffre restant : {fmt_num(balance)} {SETTINGS.currency_name} • cote verrouillée")
        await interaction.followup.send(embed=e, ephemeral=True)


class ChangedOddView(discord.ui.View):
    def __init__(self, service: BettingService, event_id: str, selection: str, odd: float, stake: int):
        super().__init__(timeout=90)
        self.service = service; self.event_id = event_id; self.selection = selection; self.odd = odd; self.stake = stake

    @discord.ui.button(label="Accepter la nouvelle cote", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with GUARD.locks[interaction.user.id]:
            ok, reason, data = await self.service.place_bet(interaction.user.id, self.event_id, self.selection, self.stake, self.odd)
        if not ok:
            return await interaction.followup.send(
                "❌ La cote a encore changé. Rouvre le match pour voir la cote actuelle." if reason == "ODD_CHANGED" else f"❌ {reason}",
                ephemeral=True,
            )
        e = discord.Embed(
            title="✅ TICKET VALIDÉ",
            description=f"**BET-{data['bet_id']}**\nCote **{data['odd']:.2f}** • mise **{fmt_num(self.stake)}** • retour **{fmt_num(data['payout'])} {SETTINGS.currency_name}**",
            color=ODDIUM_GREEN,
        )
        await interaction.followup.send(embed=e, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Pari annulé.", embed=None, view=None)
        self.stop()


class BetChoiceButton(discord.ui.Button):
    def __init__(self, service: BettingService, event_id: str, selection: str, label: str, odd: float, row: int = 0):
        style = {
            "HOME": discord.ButtonStyle.primary,
            "DRAW": discord.ButtonStyle.secondary,
            "AWAY": discord.ButtonStyle.primary,
        }.get(selection, discord.ButtonStyle.secondary)
        super().__init__(label=f"{label}   {odd:.2f}"[:80], style=style, row=row)
        self.service = service; self.event_id = event_id; self.selection = selection; self.odd = odd

    async def callback(self, interaction: discord.Interaction):
        if await GUARD.allow(interaction):
            await interaction.response.send_modal(StakeModal(self.service, self.event_id, self.selection, self.odd))


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
                label=f"{m['home_team']} — {m['away_team']}"[:100],
                description=f"{fmt_dt(m['commence_time'])}  •  1 {_safe_odd(m['home_odd'])}  N {_safe_odd(m['draw_odd'])}  2 {_safe_odd(m['away_odd'])}"[:100],
                value=m["event_id"], emoji="🎟️",
            ))
        super().__init__(placeholder="Choisir le match à parier…", options=options, min_values=1, max_values=1)
        self.service = service

    async def callback(self, interaction: discord.Interaction):
        match = await self.service.db.fetchone("SELECT * FROM matches WHERE event_id=?", (self.values[0],))
        if not match:
            return await interaction.response.send_message("Ce match n'est plus disponible.", ephemeral=True)
        now = datetime.now(timezone.utc)
        kickoff = parse_iso(match["commence_time"])
        if now >= kickoff:
            return await interaction.response.send_message("🔒 Les paris pré-match sont fermés pour cette rencontre.", ephemeral=True)
        trend = await self.service.odds_trend(match["event_id"])
        inv = [1.0 / float(match["home_odd"]), 1.0 / float(match["draw_odd"]), 1.0 / float(match["away_odd"])]
        total_inv = sum(inv) or 1.0
        probs = [round(100.0 * x / total_inv) for x in inv]
        e = discord.Embed(
            title="◈  ODDIUM • MARKET",
            description=(
                _bookmaker_header(str(match['competition_name']), f"`{fmt_dt(match['commence_time'])}`")
                + f"\n\n### {match['home_team']}\n## VS\n### {match['away_team']}"
            ),
            color=ODDIUM_GOLD,
        )
        e.add_field(name=f"1 • {match['home_team']}", value=f"### `{_safe_odd(match['home_odd'])}`　{trend['home']}\nMarché normalisé　**{probs[0]}%**", inline=True)
        e.add_field(name="N • MATCH NUL", value=f"### `{_safe_odd(match['draw_odd'])}`　{trend['draw']}\nMarché normalisé　**{probs[1]}%**", inline=True)
        e.add_field(name=f"2 • {match['away_team']}", value=f"### `{_safe_odd(match['away_odd'])}`　{trend['away']}\nMarché normalisé　**{probs[2]}%**", inline=True)
        e.add_field(name="◆ MARKET PULSE", value="Les flèches indiquent le mouvement récent de la cote. Les pourcentages sont dérivés des cotes normalisées, pas un pronostic Oddium.", inline=False)
        e.set_footer(text=_footer("Market • clique 1 / N / 2 • cote revérifiée avant débit"))
        # V34: the market is now carried by a dynamically rendered Oddium card.
        card = discord.File(match_card(dict(match), "prematch"), filename="oddium_match.png")
        e.set_image(url="attachment://oddium_match.png")
        await interaction.response.send_message(embed=e, view=MatchBetView(self.service, match), file=card, ephemeral=True)

# --- Match Center -----------------------------------------------------------

def _v15_highlight_detail(detail: str) -> str:
    text = str(detail or "").strip()
    low = text.lower()
    if "score de période first half" in low or "period score first half" in low:
        score = text.split(":", 1)[-1].strip() if ":" in text else ""
        return f"Score à la mi-temps : {score}" if score else "Score à la mi-temps"
    if "score de période second half" in low or "period score second half" in low:
        score = text.split(":", 1)[-1].strip() if ":" in text else ""
        return f"Score de la 2e période : {score}" if score else "Score de la 2e période"
    return text


def _v15_stat_value(block, *names):
    vals = block.get("values") or {}
    lowered = {str(k).lower(): v for k, v in vals.items()}
    for name in names:
        if name.lower() in lowered:
            value = lowered[name.lower()]
            return "—" if value is None else str(value)
    return "—"


async def _v15_match_center_embed(service: BettingService, event_id: str, tab: str = "summary"):
    data = await service.live_match_details(event_id)
    m = data.get("match")
    if not m:
        return discord.Embed(title="Match indisponible", color=ODDIUM_RED)
    badge, color = _phase_badge(m)
    hs = "–" if m["home_score"] is None else str(m["home_score"])
    aws = "–" if m["away_score"] is None else str(m["away_score"])
    title = f"⚽  {m['home_team']}   {hs} ━ {aws}   {m['away_team']}"
    e = discord.Embed(
        title=title,
        description=f"### MATCH CENTER\n**{m['competition_name']}**　•　{badge}\n{ODDIUM_DIVIDER}",
        color=color,
    )
    external = data.get("external") or {}
    stats = external.get("statistics") or []
    events = _dedupe_live_highlights(data.get("events") or [])
    useful = [ev for ev in events if str(ev["event_type"]) not in {"live_update", "clock_update", "phase_change"}]

    if tab == "stats":
        if len(stats) >= 2:
            left, right = stats[0], stats[1]
            metrics = [
                ("Possession", ("Ball Possession", "Possession")),
                ("Tirs", ("Total Shots", "Shots")),
                ("Cadrés", ("Shots on Goal", "Shots on Target")),
                ("Corners", ("Corner Kicks", "Corners")),
                ("Fautes", ("Fouls",)),
                ("Hors-jeu", ("Offsides",)),
            ]
            left_name = str(left.get("team") or m["home_team"])
            right_name = str(right.get("team") or m["away_team"])
            lines = [f"**{left_name}**　　　**{right_name}**"]
            for label, names in metrics:
                lv = _v15_stat_value(left, *names); rv = _v15_stat_value(right, *names)
                lines.append(f"`{lv:>4}`  **{label}**  `{rv:<4}`")
            e.add_field(name="📊 STATISTIQUES", value="\n".join(lines)[:1024], inline=False)
        else:
            e.add_field(name="📊 STATISTIQUES", value="Données détaillées indisponibles pour le moment.", inline=False)
        e.set_footer(text=_footer("Match Center • Statistiques"))
        return e

    if tab == "highlights":
        if useful:
            lines = []
            for ev in useful[-12:]:
                label = LIVE_EVENT_LABELS.get(str(ev["event_type"]), "•")
                when = str(ev["clock"] or "").strip() or "•"
                detail = _v15_highlight_detail(str(ev["detail"] or ""))
                lines.append(f"`{when:>3}`  {label}" + (f"  **{detail}**" if detail else ""))
            e.add_field(name="📜 TEMPS FORTS", value="\n".join(lines)[-1024:], inline=False)
        else:
            e.add_field(name="📜 TEMPS FORTS", value="Aucun événement majeur signalé pour le moment.", inline=False)
        e.set_footer(text=_footer("Match Center • Timeline dédupliquée"))
        return e

    if tab == "market":
        snap = await service.odds_market_snapshot(event_id)
        if not snap:
            e.add_field(name="📈 MARCHÉ", value="Aucune cote disponible.", inline=False)
        else:
            opening = snap.get("opening") or {}; current = snap.get("current") or {}
            move = snap.get("movement") or {}; fair = snap.get("fair_probability") or {}
            labels = [("1", "home"), ("N", "draw"), ("2", "away")]
            lines = []
            for lab, key in labels:
                op = opening.get(key); cur = current.get(key); mv = float(move.get(key) or 0)
                arrow = "⬆️" if mv > 0 else "⬇️" if mv < 0 else "➡️"
                op_txt = _safe_odd(op); cur_txt = _safe_odd(cur)
                lines.append(f"**{lab}**  `{op_txt}` → **`{cur_txt}`**  {arrow} `{mv:+.1f}%`  •  {float(fair.get(key) or 0):.1f}%")
            e.add_field(name="📈 OUVERTURE → ACTUEL", value="\n".join(lines), inline=False)
            e.add_field(name="BOOKMAKER", value=str(current.get("bookmaker") or "5DollarFootballAPI")[:1024], inline=False)
            e.set_footer(text=_footer(f"Market • {int(snap.get('samples') or 0)} snapshot(s) • probabilités normalisées"))
        return e

    # Summary: concise live-score card, inspired by score trackers.
    clock = str(m["live_clock"] or "").strip()
    source = str(m["live_source"] or "").strip()
    summary = []
    if clock and str(m["live_phase"] or "") not in {"halftime", "finished", "cancelled", "postponed", "suspended"}:
        summary.append(f"⏱️ **{clock}**")
    if useful:
        last = useful[-1]
        lab = LIVE_EVENT_LABELS.get(str(last["event_type"]), "•")
        detail = _v15_highlight_detail(str(last["detail"] or ""))
        summary.append(f"Dernier fait : {lab} **{detail or 'Événement live'}**")
    if source:
        summary.append("🟢 Données live confirmées")
    e.add_field(name="⚡ RÉSUMÉ", value="\n".join(summary) if summary else "Match en cours de synchronisation.", inline=False)
    if len(stats) >= 2:
        left, right = stats[0], stats[1]
        poss_l = _v15_stat_value(left, "Ball Possession", "Possession")
        poss_r = _v15_stat_value(right, "Ball Possession", "Possession")
        shots_l = _v15_stat_value(left, "Shots on Goal", "Shots on Target")
        shots_r = _v15_stat_value(right, "Shots on Goal", "Shots on Target")
        e.add_field(name="EN UN COUP D’ŒIL", value=f"Possession `{poss_l} — {poss_r}`\nTirs cadrés `{shots_l} — {shots_r}`", inline=False)
    e.set_footer(text=_footer("Match Center • Résumé • Stats • Temps forts • Marché"))
    return e


class MatchCenterView(discord.ui.View):
    def __init__(self, service: BettingService, event_id: str, user_id: int | None = None):
        super().__init__(timeout=180)
        self.service = service
        self.event_id = str(event_id)
        self.user_id = user_id

    async def _show(self, interaction: discord.Interaction, tab: str):
        await interaction.response.defer()
        embed = await _v15_match_center_embed(self.service, self.event_id, tab)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Résumé", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def summary(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "summary")

    @discord.ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "stats")

    @discord.ui.button(label="Temps forts", emoji="📜", style=discord.ButtonStyle.secondary, row=0)
    async def highlights(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "highlights")

    @discord.ui.button(label="Marché", emoji="📈", style=discord.ButtonStyle.secondary, row=0)
    async def market(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "market")

    @discord.ui.button(label="Suivre", emoji="🔔", style=discord.ButtonStyle.danger, row=1)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = await self.service.toggle_match_follow(interaction.user.id, self.event_id)
        button.label = "Suivi activé" if state else "Suivre"
        button.style = discord.ButtonStyle.success if state else discord.ButtonStyle.danger
        embed = await _v15_match_center_embed(self.service, self.event_id, "summary")
        await interaction.response.edit_message(embed=embed, view=self)

# --- DESIGN MAX -------------------------------------------------------------
# Final presentation overrides. Betting keeps the league carousel and the
# official local artwork stored in /assets. No remote image dependency.

async def build_carousel_embed(service: BettingService, active: list[str], index: int) -> discord.Embed:
    if not active:
        return discord.Embed(title="✦ ODDIUM • ARÈNE DES PARIS", description=_bookmaker_header("BET DESK", "Aucun championnat actif actuellement."), color=ODDIUM_GOLD)
    index %= len(active)
    sport_key = active[index]
    info = COMPETITIONS[sport_key]
    matches = await service.matches_for_window(sport_key, "future", 25)
    e = discord.Embed(
        title="✦ ODDIUM • ARÈNE DES PARIS",
        description=(
            _bookmaker_header("LEAGUE LOBBY", f"{info['emoji']}  **{info['name'].upper()}**")
            + f"\n\n`{index + 1:02d} / {len(active):02d}`　◀  NAVIGATION CHAMPIONNATS  ▶"
        ),
        color=ODDIUM_GOLD,
    )
    if matches:
        nxt = matches[0]
        local = parse_iso(nxt["commence_time"]).astimezone(PARIS_TZ)
        e.add_field(name="◆ MATCHS OUVERTS", value=f"### {len(matches):02d}", inline=True)
        e.add_field(name="◆ PROCHAIN KICK-OFF", value=f"### {local.strftime('%H:%M')}\n`{local.strftime('%d/%m/%Y')}`", inline=True)
        e.add_field(name="◆ MARCHÉ", value="### `1 • N • 2`", inline=True)
        preview=[]
        for i,m in enumerate(matches[:3],1):
            dt=parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
            preview.append(
                f"`{i:02d}`　**{m['home_team']}**  vs  **{m['away_team']}**\n"
                f"　　`{dt.strftime('%d/%m • %H:%M')}`　{_market_triplet(m)}"
            )
        e.add_field(name="✦ FEATURED MARKETS", value="\n\n".join(preview)[:1024], inline=False)
        e.add_field(name="COMMENT PARIER", value="**Ouvrir le championnat** → choisir un match → sélectionner `1`, `N` ou `2` → saisir la mise.", inline=False)
    else:
        e.add_field(name="◆ MARCHÉ FERMÉ", value="Aucun pari pré-match ouvert dans cette compétition.", inline=False)
    filename = CAROUSEL_ASSETS.get(sport_key)
    if filename and (ASSET_DIR / filename).is_file():
        e.set_image(url=f"attachment://{filename}")
    e.set_footer(text=_footer("Sportsbook • Bet365 via 5Dollar • cotes contrôlées à validation"))
    return e


async def build_league_embed(service: BettingService, sport_key: str, matches) -> discord.Embed:
    info=COMPETITIONS[sport_key]
    e=discord.Embed(
        title="◈  ODDIUM • MATCH BOARD",
        description=_bookmaker_header(info['name'], "Choisis une affiche pour ouvrir sa fiche bookmaker."),
        color=ODDIUM_GOLD,
    )
    if not matches:
        e.add_field(name="◆ AUCUN MARCHÉ OUVERT", value="Reviens au League Lobby pour choisir une autre compétition.", inline=False)
        e.set_footer(text=_footer("Match Board • calendrier 5Dollar")); return e
    grouped=defaultdict(list)
    for m in matches[:25]:
        local=parse_iso(m['commence_time']).astimezone(PARIS_TZ); grouped[local.strftime('%Y-%m-%d')].append((local,m))
    shown=0
    for _, day_matches in list(grouped.items())[:4]:
        day=day_matches[0][0]; rows=[]
        for local,m in day_matches[:5]:
            shown+=1
            rows.append(f"`{shown:02d}` `{local.strftime('%H:%M')}`　**{m['home_team']}  —  {m['away_team']}**\n　　{_market_triplet(m)}")
        e.add_field(name=f"◆ {day.strftime('%d/%m/%Y')}", value="\n\n".join(rows)[:1024], inline=False)
    e.add_field(name="LÉGENDE", value="`1` Domicile　•　`N` Match nul　•　`2` Extérieur", inline=False)
    e.set_footer(text=_footer(f"Match Board • {len(matches)} marché(s) ouverts"))
    return e

async def _live_embed(service):
    rows = await service.live_matches(25)
    e = discord.Embed(
        title="🔴  ODDIUM • LIVE ARENA",
        description=(
            f"### MATCHS EN DIRECT\n{ODDIUM_DIVIDER}\n"
            "Scores confirmés • chronomètre fournisseur • actualisation automatique"
            if rows else
            f"### LIVE ARENA\n{ODDIUM_DIVIDER}\n⚫ **AUCUN MATCH EN DIRECT**\nLe radar Oddium reste actif jusqu'au prochain coup d'envoi."
        ),
        color=ODDIUM_RED if rows else ODDIUM_DARK,
    )
    if not rows:
        e.set_footer(text=_footer("Radar live actif"))
        return e
    groups = defaultdict(list)
    for m in rows:
        league = COMPETITIONS.get(m["sport_key"], {}).get("name", m["competition_name"])
        emoji = COMPETITIONS.get(m["sport_key"], {}).get("emoji", "⚽")
        groups[(emoji, league)].append(m)
    shown = 0
    for (emoji, league), games in list(groups.items())[:6]:
        lines = []
        for m in games:
            if shown >= 12:
                break
            badge, _ = _phase_badge(m)
            hs = "–" if m["home_score"] is None else str(m["home_score"])
            aws = "–" if m["away_score"] is None else str(m["away_score"])
            lines.append(f"{badge}\n**{m['home_team']}**　` {hs}  ━  {aws} `　**{m['away_team']}**")
            shown += 1
        if lines:
            e.add_field(name=f"{emoji}  {league.upper()}", value="\n\n".join(lines)[:1024], inline=False)
        if shown >= 12:
            break
    hidden = max(0, len(rows) - shown)
    tail = f"{len(rows)} live • 📊 Match Center • 🔔 Alertes • 🎟️ Tickets live"
    if hidden:
        tail += f" • +{hidden} masqué(s)"
    e.set_footer(text=_footer(tail))
    return e

