from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import discord

from config import SETTINGS
from ..core.constants import BET_STATUS_ICONS, COMPETITIONS
from ..betting.service import BettingService
from ..core.time_utils import parse_iso

PARIS_TZ = ZoneInfo("Europe/Paris")
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"

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
        matches = await view.service.matches_for_window(sport_key, "future", 25)
        embed = await build_league_embed(view.service, sport_key, matches)
        await interaction.response.edit_message(
            embed=embed,
            view=MatchBrowserView(view.service, view.active, matches, sport_key),
        )




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
        await interaction.response.edit_message(embed=await build_carousel_embed(self.service,self.active,0),view=BrowseLeagueCarouselView(self.service,self.active,0),attachments=carousel_attachments(self.active,0))
    @discord.ui.button(label="Parier",emoji="🎟️",style=discord.ButtonStyle.success)
    async def bet(self,interaction,button):
        e=discord.Embed(title="🎟️ CHOISIS TON TYPE DE PARI",description="🎯 **Pari simple** — un pronostic 1/N/2\n🧩 **Pari combiné** — plusieurs matchs, une cote totale",color=discord.Color.gold())
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
    available = sum(len(await service.matches_for_window(key, "future", 25)) for key in active)
    live_count = len(await service.live_matches(25))
    pending = await service.db.fetchone("SELECT COUNT(*) c FROM bets WHERE status='PENDING'")
    combo_pending = await service.db.fetchone("SELECT COUNT(*) c FROM combo_bets WHERE status='PENDING'")
    open_tickets = int(pending["c"] if pending else 0) + int(combo_pending["c"] if combo_pending else 0)

    e = discord.Embed(
        title="✦  O D D I U M  ✦",
        description=(
            "### TON BOOKMAKER VIRTUEL\n"
            "**Paris • Direct • Statistiques • Classement**\n"
            f"{ODDIUM_DIVIDER}\n"
            f"`⚽ {available:02d}` **MATCHS**　 `🔴 {live_count:02d}` **LIVE**　 `🎟️ {open_tickets:02d}` **EN COURS**\n"
            f"{ODDIUM_DIVIDER}\n"
            "Choisis un espace ci-dessous. Les cotes sont verrouillées à la validation du ticket."
        ),
        color=ODDIUM_GOLD,
    )
    e.set_image(url="attachment://oddium_welcome.png")
    e.add_field(name="⚽ MATCH CENTER", value="Calendrier, rencontres et cotes", inline=True)
    e.add_field(name="🎟️ BET DESK", value="Simple ou combiné", inline=True)
    e.add_field(name="🏆 ODDIUM RANK", value="Profil et classement", inline=True)
    e.set_footer(text=_footer("Cotes actualisées automatiquement"))
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
        await interaction.response.send_message(embed=e, ephemeral=True)


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
    e.add_field(name="SOLDE", value=f"### {fmt_num(balance)}\n{SETTINGS.currency_name}", inline=True)
    e.add_field(name="BÉNÉFICE", value=f"### {net:+,}".replace(',', ' ') + f"\n{SETTINGS.currency_name}", inline=True)
    e.add_field(name="RÉUSSITE", value=f"### {rate:.0f}%\n{wins}/{settled}", inline=True)
    e.add_field(name="PARIS SIMPLES", value=f"**{int(s['total'] or 0)}** joués  •  🟢 {wins}  •  🔴 {losses}", inline=False)
    e.add_field(name="COMBINÉS", value=f"**{len(combos)}** tickets créés", inline=True)
    e.add_field(name="RECORD", value=f"Gain **{fmt_num(int(s['biggest_win'] or 0))}**\nCote **{float(s['biggest_odd'] or 0):.2f}**", inline=True)
    return e


async def _leaderboard_embed(service, bot):
    rows = await service.leaderboard(10)
    e = discord.Embed(title="🏆  ODDIUM • HALL OF FAME", description=f"### CLASSEMENT GÉNÉRAL\n**Bénéfice net • performance • prestige**\n{ODDIUM_DIVIDER}", color=ODDIUM_GOLD)
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
        await interaction.response.send_message("**Choisis un match pour ouvrir sa fiche :**", view=LiveDetailsSelectView(self.service, matches), ephemeral=True)

    @discord.ui.button(label="Suivre", style=discord.ButtonStyle.danger, emoji="🔔", custom_id="oddium:v13:live:follow", row=0)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches = await self.service.live_matches(25)
        if not matches:
            return await interaction.response.send_message("⚽ Aucun match live à suivre.", ephemeral=True)
        followed = await self.service.followed_event_ids(interaction.user.id)
        await interaction.response.send_message("**Choisis le match à suivre :**", view=LiveFollowSelectView(self.service, matches, followed), ephemeral=True)

    @discord.ui.button(label="Mes paris live", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="oddium:v13:live:bets", row=0)
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
        await interaction.response.send_message(embed=discord.Embed(title="🎟️ MES PARIS LIVE", description="\n\n".join(lines), color=ODDIUM_GREEN), ephemeral=True)


class MainPanelView(discord.ui.View):
    """Cinq accès maximum, aucune action redondante."""
    def __init__(self, service: BettingService, active: list[str]):
        super().__init__(timeout=None); self.service = service; self.active = [k for k in active if k in COMPETITIONS]

    @discord.ui.button(label="Matchs", emoji="⚽", style=discord.ButtonStyle.primary, custom_id="oddium:v13:matches", row=0)
    async def matches(self, interaction, button):
        active = carousel_keys(await self.service.active_competitions())
        if not active:
            return await interaction.response.send_message("Aucun championnat actif.", ephemeral=True)
        await interaction.response.send_message(embed=await build_carousel_embed(self.service, active, 0), view=BrowseLeagueCarouselView(self.service, active, 0), files=carousel_attachments(active, 0), ephemeral=True)

    @discord.ui.button(label="Parier", emoji="🎟️", style=discord.ButtonStyle.success, custom_id="oddium:v13:bet", row=0)
    async def bet(self, interaction, button):
        active = carousel_keys(await self.service.active_competitions())
        e = discord.Embed(title="🎟️  ODDIUM • BET DESK", description=f"### CRÉER UN TICKET\n{ODDIUM_DIVIDER}\n🎯 **SIMPLE**　Un match, un pronostic\n🧩 **COMBINÉ**　Plusieurs sélections, une cote totale", color=ODDIUM_GOLD)
        e.set_footer(text=_footer("Choisis ton mode de pari"))
        await interaction.response.send_message(embed=e, view=BetModeView(self.service, active), ephemeral=True)

    @discord.ui.button(label="Mes paris", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="oddium:v13:mybets", row=0)
    async def mybets(self, interaction, button):
        await interaction.response.send_message(embed=await _my_bets_embed(self.service, interaction.user.id), view=HomeReturnView(self.service), ephemeral=True)

    @discord.ui.button(label="Live", emoji="🔴", style=discord.ButtonStyle.danger, custom_id="oddium:v13:live", row=1)
    async def live(self, interaction, button):
        await interaction.response.send_message(embed=await _live_embed(self.service), view=LivePanelView(self.service), ephemeral=True)

    @discord.ui.button(label="Classement", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="oddium:v13:rank", row=1)
    async def rank(self, interaction, button):
        e = await _leaderboard_embed(self.service, interaction.client)
        await interaction.response.send_message(embed=e, view=RankProfileView(self.service), ephemeral=True)

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
            title="✅ TICKET VALIDÉ",
            description=f"**BET-{data['bet_id']}**  •  {match['competition_name']}",
            color=ODDIUM_GREEN,
        )
        e.add_field(name="MATCH", value=f"**{match['home_team']}  —  {match['away_team']}**", inline=False)
        e.add_field(name="PRONOSTIC", value=f"**{label}**\nCote `{data['odd']:.2f}`", inline=True)
        e.add_field(name="MISE", value=f"**{fmt_num(stake)}**\n{SETTINGS.currency_name}", inline=True)
        e.add_field(name="RETOUR POTENTIEL", value=f"**{fmt_num(data['payout'])}**\n{SETTINGS.currency_name}", inline=True)
        e.set_footer(text=f"Solde restant : {fmt_num(balance)} {SETTINGS.currency_name} • cote verrouillée")
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
            "HOME": discord.ButtonStyle.success,
            "DRAW": discord.ButtonStyle.primary,
            "AWAY": discord.ButtonStyle.success,
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
            title=f"{match['home_team']}  —  {match['away_team']}",
            description=f"**{match['competition_name']}**\n🕒 {fmt_dt(match['commence_time'])}",
            color=ODDIUM_GOLD,
        )
        e.add_field(name=f"1  {match['home_team']}", value=f"### `{_safe_odd(match['home_odd'])}` {trend['home']}\n📊 {probs[0]}%", inline=True)
        e.add_field(name="N  Match nul", value=f"### `{_safe_odd(match['draw_odd'])}` {trend['draw']}\n📊 {probs[1]}%", inline=True)
        e.add_field(name=f"2  {match['away_team']}", value=f"### `{_safe_odd(match['away_odd'])}` {trend['away']}\n📊 {probs[2]}%", inline=True)
        e.set_footer(text="Choisis 1 / N / 2 ci-dessous • la cote est vérifiée au moment de valider")
        await interaction.response.send_message(embed=e, view=MatchBetView(self.service, match), ephemeral=True)

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
        return discord.Embed(title="🎟️ ODDIUM • BET DESK", description="Aucun championnat actif actuellement.", color=ODDIUM_GOLD)
    index %= len(active)
    sport_key = active[index]
    info = COMPETITIONS[sport_key]
    matches = await service.matches_for_window(sport_key, "future", 25)
    e = discord.Embed(
        title=f"{info['emoji']}  ODDIUM • {info['name'].upper()}",
        description=(
            f"### BET DESK　`{index + 1:02d}/{len(active):02d}`\n"
            f"{ODDIUM_DIVIDER}\n"
            "`◀` **LIGUE PRÉCÉDENTE**　　　 **LIGUE SUIVANTE** `▶`"
        ),
        color=ODDIUM_GOLD,
    )
    if matches:
        nxt = matches[0]
        local = parse_iso(nxt["commence_time"]).astimezone(PARIS_TZ)
        e.add_field(name="MATCHS OUVERTS", value=f"### ⚽ {len(matches):02d}", inline=True)
        e.add_field(name="PROCHAIN COUP D'ENVOI", value=f"### {local.strftime('%H:%M')}\n{local.strftime('%d/%m')}", inline=True)
        e.add_field(name="MARCHÉ PRINCIPAL", value="### `1`　`N`　`2`", inline=True)
        preview = []
        for m in matches[:3]:
            dt = parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
            preview.append(
                f"`{dt.strftime('%d/%m • %H:%M')}`  **{m['home_team']} — {m['away_team']}**\n"
                f"　`1 {_safe_odd(m['home_odd'])}`　`N {_safe_odd(m['draw_odd'])}`　`2 {_safe_odd(m['away_odd'])}`"
            )
        e.add_field(name="✦ À L'AFFICHE", value="\n\n".join(preview), inline=False)
    else:
        e.add_field(name="CALENDRIER", value="📭 Aucun pari ouvert dans ce championnat pour le moment.", inline=False)
    filename = CAROUSEL_ASSETS.get(sport_key)
    if filename:
        e.set_image(url=f"attachment://{filename}")
    e.set_footer(text=_footer("Bet365 via 5Dollar • navigation carrousel"))
    return e


async def build_league_embed(service: BettingService, sport_key: str, matches) -> discord.Embed:
    info = COMPETITIONS[sport_key]
    e = discord.Embed(
        title=f"{info['emoji']}  ODDIUM • {info['name'].upper()}",
        description=f"### MATCH BOARD\nSélectionne une rencontre pour ouvrir le marché **1 / N / 2**.\n{ODDIUM_DIVIDER}",
        color=ODDIUM_GOLD,
    )
    filename = CAROUSEL_ASSETS.get(sport_key)
    if filename:
        e.set_image(url=f"attachment://{filename}")
    if not matches:
        e.add_field(name="📭 AUCUN MATCH OUVERT", value="Reviens au carrousel pour choisir une autre compétition.", inline=False)
        e.set_footer(text=_footer("Calendrier 5Dollar"))
        return e
    grouped: dict[str, list] = defaultdict(list)
    for m in matches[:25]:
        local = parse_iso(m["commence_time"]).astimezone(PARIS_TZ)
        grouped[local.strftime("%Y-%m-%d")].append((local, m))
    for _, day_matches in list(grouped.items())[:4]:
        local_day = day_matches[0][0]
        rows = []
        for local, m in day_matches[:5]:
            rows.append(
                f"`{local.strftime('%H:%M')}`　**{m['home_team']}  vs  {m['away_team']}**\n"
                f"　└ `1  {_safe_odd(m['home_odd'])}`　`N  {_safe_odd(m['draw_odd'])}`　`2  {_safe_odd(m['away_odd'])}`"
            )
        e.add_field(name=f"📅  {local_day.strftime('%A %d/%m').upper()}", value="\n\n".join(rows)[:1024], inline=False)
    e.set_footer(text=_footer(f"{len(matches)} match(s) ouverts • Bet365 via 5Dollar"))
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

