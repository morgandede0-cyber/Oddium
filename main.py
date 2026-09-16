from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import SETTINGS
from legacy_bet.storage.database import Database
from legacy_bet.betting.economy import EconomyAdapter
from legacy_bet.providers.gateway import OddsAPI
from legacy_bet.discord_ui.panel import PanelManager
from legacy_bet.betting.service import BettingService
from legacy_bet.discord_ui.ui import AdminPanelView, MainPanelView, LivePanelView, LIVE_EVENT_LABELS, fmt_num
from legacy_bet.live.websocket import LocalLiveWebSocket, consume_local_live
from legacy_bet.live.identity import event_fingerprint
from legacy_bet.discord_ui.team_logos import sync_team_logos

os.makedirs(SETTINGS.log_dir, exist_ok=True)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
root = logging.getLogger()
root.setLevel(logging.INFO)
console = logging.StreamHandler()
console.setFormatter(formatter)
file_handler = RotatingFileHandler(os.path.join(SETTINGS.log_dir, "oddium.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
file_handler.setFormatter(formatter)
root.handlers.clear()
root.addHandler(console)
root.addHandler(file_handler)
log = logging.getLogger("oddium")

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()
economy = EconomyAdapter(db)
odds_api = OddsAPI(db)
service = BettingService(db, economy, odds_api)
panel = PanelManager(bot, service)
live_ws = LocalLiveWebSocket()
live_ws_consumer_task: asyncio.Task | None = None
live_collector_task: asyncio.Task | None = None


async def on_live_ws_event(payload: dict):
    # IMPORTANT: the collector already performs ONE canonical Discord repaint
    # after a changed cycle. WebSocket events can be numerous for the same cycle
    # (clock, phase, goal, card, odds...), so repainting here as well created a
    # PATCH storm and Discord 429s. Keep this consumer for follower notifications
    # only; the panel is refreshed once by live_collector_supervisor.
    try:
        await notify_live_followers(payload)
    except Exception:
        log.exception("Impossible d'appliquer l'événement live aux abonnés")


async def notify_live_followers(event: dict):
    """DM only meaningful live events; clock ticks stay on the panel without spam."""
    etype = str(event.get("type") or "")
    if etype in {"clock_update", "live_update", "phase_change"}:
        return
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    followers = await service.followers_for_event(event_id)
    if not followers:
        return
    label = LIVE_EVENT_LABELS.get(etype, "🔴 Événement live")
    home = event.get("home_team") or "Domicile"; away = event.get("away_team") or "Extérieur"
    hs = event.get("home_score"); aws = event.get("away_score"); clock = event.get("clock")
    score = f"{hs} - {aws}" if hs is not None and aws is not None else "– - –"
    text = f"{label} **{clock or ''}**\n**{home} {score} {away}**"
    fp = event_fingerprint({"type": etype, "clock": clock, "detail": event.get("detail")})
    reference = f"{event_id}:{fp}"
    for row in followers:
        uid=int(row["user_id"])
        # A retry/reconnect/redeploy must never send the same live alert twice.
        # notification_log is UNIQUE(user, kind, reference), so this remains safe
        # across process restarts as well.
        if not await service.mark_notification_sent(uid, "LIVE_FOLLOW", reference):
            continue
        try:
            user=bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(text)
        except discord.Forbidden:
            pass
        except Exception:
            log.exception("Notification live impossible pour %s", uid)


async def sync_commands():
    if SETTINGS.guild_id:
        guild = discord.Object(id=SETTINGS.guild_id)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        log.info("Commandes synchronisées sur le serveur %s", SETTINGS.guild_id)
    else:
        await bot.tree.sync()
        log.info("Commandes globales synchronisées")


@bot.event
async def on_ready():
    log.info("Connecté en tant que %s (%s)", bot.user, bot.user.id if bot.user else "?")
    # Panels are installed manually by the administrator with /setup and
    # /setup_live. A reconnect/redeploy must never create or reinstall them.


@bot.event
async def setup_hook():
    await db.init()
    await odds_api.start()
    await live_ws.start()
    # V35: les logos sont mis en cache en arrière-plan; cela ne crée aucun panneau Discord.
    asyncio.create_task(sync_team_logos(), name="oddium-team-logo-sync")
    global live_ws_consumer_task, live_collector_task
    live_ws_consumer_task = asyncio.create_task(consume_local_live(on_live_ws_event), name="oddium-live-ws-consumer")
    # Collecteur autonome démarré immédiatement. Il ne dépend ni de on_ready,
    # ni d'un panneau Discord, ni de discord.ext.tasks.
    live_collector_task = asyncio.create_task(live_collector_supervisor(), name="oddium-live-supervisor")
    active = await service.active_competitions()
    bot.add_view(MainPanelView(service, active))
    bot.add_view(LivePanelView(service))
    await sync_commands()
    engine_loop.start()
    panel_loop.start()
    notification_loop.start()
    backup_loop.start()


@bot.tree.command(name="setup", description="Installe le panneau permanent Oddium dans ce salon")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Utilise cette commande dans un salon texte.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await service.refresh_odds()
    msg = await panel.ensure_panel(interaction.channel)
    await db.log_admin(interaction.user.id, "SETUP_PANEL", f"channel={interaction.channel.id} message={msg.id}")
    await interaction.followup.send(
        f"✅ Panneau installé et épinglé : {msg.jump_url}\nLes joueurs n'ont plus besoin de commandes `/`.",
        ephemeral=True,
    )


@bot.tree.command(name="setup_live", description="Installe le panneau permanent des matchs en direct")
@app_commands.checks.has_permissions(administrator=True)
async def setup_live(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Utilise cette commande dans un salon texte.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    # Crée d'abord le panneau pour activer le rafraîchissement des scores même sans paris.
    msg = await panel.ensure_live_panel(interaction.channel)
    scores, settlements, errors = await service.refresh_scores_and_settle(force_live_panel=True)
    await panel.refresh_existing_live_panel()
    await db.log_admin(interaction.user.id, "SETUP_LIVE_PANEL", f"channel={interaction.channel.id} message={msg.id}")
    detail = "" if not errors else f"\n⚠️ {len(errors)} source(s) n'ont pas pu être actualisées immédiatement."
    await interaction.followup.send(
        f"✅ Panneau **Matchs en direct** installé et épinglé : {msg.jump_url}\n"
        f"⚡ **Oddium Live actif** : le panneau reçoit tous les événements via notre WebSocket local : coup d’envoi, chrono, score, mi-temps, reprise, prolongations, tirs au but et fin de match.{detail}",
        ephemeral=True,
    )


@bot.tree.command(name="admin_paris", description="Ouvre la gestion interactive des paris")
@app_commands.checks.has_permissions(administrator=True)
async def admin_paris(interaction: discord.Interaction):
    active = await service.active_competitions()
    status = await service.api_status()
    paused = bool(await db.get_setting("betting_paused"))
    embed = discord.Embed(
        title="⚙️ Administration Oddium",
        description="Centre de contrôle interactif. Les actions sensibles sont journalisées.",
        color=discord.Color.dark_gold(),
    )
    embed.add_field(name="État", value="🔒 Suspendu" if paused else "🟢 Ouvert", inline=True)
    embed.add_field(name="Cotes", value="Bet365 • 5DollarFootballAPI Pro", inline=True)
    embed.add_field(name="Mises", value=f"{SETTINGS.min_stake} → {SETTINGS.max_stake} {SETTINGS.currency_name}", inline=True)
    embed.add_field(name="Matchs futurs", value=str(status["future_matches"]), inline=True)
    embed.add_field(name="Paris actifs", value=str(status["pending_bets"]), inline=True)
    embed.add_field(name="Moteur", value="🟢 5Dollar-first", inline=True)
    await interaction.response.send_message(embed=embed, view=AdminPanelView(service, active), ephemeral=True)


@bot.tree.command(name="diagnostic_oddium", description="Affiche la santé des sources et du moteur Oddium")
@app_commands.checks.has_permissions(administrator=True)
async def diagnostic_oddium(interaction: discord.Interaction):
    status = await service.api_status()
    live_count = int(await db.get_setting("live_engine_visible_count") or 0)
    last_scan = await db.get_setting("live_engine_last_scan")
    embed = discord.Embed(
        title="🩺 Diagnostic Oddium",
        description="Moteur football 100 % natif 5DollarFootballAPI • aucune fusion de fournisseur",
        color=discord.Color.green() if not status.get("last_error") else discord.Color.orange(),
    )
    embed.add_field(name="Live", value=f"Matchs visibles : **{live_count}**\nDernier scan : `{last_scan or '—'}`", inline=False)
    five_enabled = bool(SETTINGS.five_dollar_api_key)
    quota = status.get("five_dollar_remaining") or "?"
    limit = status.get("five_dollar_limit") or "?"
    embed.add_field(name="5DollarFootballAPI Pro", value=f"{'🟢 activée' if five_enabled else '⚪ clé absente'} • polling {SETTINGS.five_dollar_poll_seconds}s • quota {quota}/{limit}", inline=False)
    lines = []
    for d in status.get("diagnostics", []):
        lines.append(
            f"**{d['name']}** • fixtures `{d['events']}` • cotes `{d['parsed']}` • live `{d.get('live_rows', 0)}`\n"
            f"↳ {d.get('live_source', '—')}"
        )
    embed.add_field(name="Compétitions", value=("\n".join(lines) or "Aucune donnée")[:1024], inline=False)
    embed.add_field(name="Dernière erreur", value=(status.get("last_error") or "Aucune")[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


FIVE_DOLLAR_ACTIONS = [
    app_commands.Choice(name="Live complet", value="live"),
    app_commands.Choice(name="Fixtures du jour", value="fixtures"),
    app_commands.Choice(name="Fixture complète", value="fixture"),
    app_commands.Choice(name="Cotes Bet365", value="odds"),
    app_commands.Choice(name="Bookmakers", value="bookmakers"),
    app_commands.Choice(name="Historique des cotes", value="odds_history"),
    app_commands.Choice(name="Événements", value="events"),
    app_commands.Choice(name="Statistiques", value="statistics"),
    app_commands.Choice(name="Classement", value="standings"),
    app_commands.Choice(name="Pays", value="countries"),
    app_commands.Choice(name="Ligues + saisons", value="leagues"),
    app_commands.Choice(name="Détail ligue", value="league"),
    app_commands.Choice(name="Matchs ligue", value="league_fixtures"),
    app_commands.Choice(name="Équipe", value="team"),
    app_commands.Choice(name="Matchs équipe", value="team_fixtures"),
    app_commands.Choice(name="Compte / quota", value="status"),
    app_commands.Choice(name="Moteur 5Dollar Ultimate", value="engine"),
]

@bot.tree.command(name="5dollar", description="Explore directement toutes les fonctions natives 5Dollar")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=FIVE_DOLLAR_ACTIONS)
async def five_dollar_explorer(interaction: discord.Interaction, action: app_commands.Choice[str], id: int | None = None, saison: str | None = None, marche: str = "1x2"):
    needs_id = {"fixture","odds","odds_history","events","statistics","standings","league","league_fixtures","team","team_fixtures"}
    if action.value in needs_id and id is None:
        await interaction.response.send_message("❌ Cette fonction demande un `id` 5Dollar (fixture, ligue ou équipe selon l'action).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        data = await odds_api.five_dollar_call(action.value, object_id=id, league_id=id, season=saison, market=marche)
        import json
        raw = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        if len(raw) <= 3800:
            await interaction.followup.send(f"**5Dollar • {action.name}**\n```json\n{raw}\n```", ephemeral=True)
        else:
            from io import BytesIO
            await interaction.followup.send(
                content=f"**5Dollar • {action.name}** — réponse complète ({len(raw)} caractères)",
                file=discord.File(BytesIO(raw.encode("utf-8")), filename=f"5dollar_{action.value}.json"),
                ephemeral=True,
            )
    except Exception as exc:
        await interaction.followup.send(f"❌ 5Dollar : `{type(exc).__name__}: {exc}`", ephemeral=True)



@setup.error
@setup_live.error
@admin_paris.error
@diagnostic_oddium.error
@five_dollar_explorer.error
async def admin_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ Réservé aux administrateurs."
    else:
        msg = "❌ Une erreur est survenue. Consulte les logs."
        log.exception("Erreur commande", exc_info=error)
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@tasks.loop(seconds=SETTINGS.engine_tick_seconds)
async def engine_loop():
    try:
        result = await service.engine_refresh_odds_if_due()
        if result:
            updated, errors = result
            log.info("Cotes 5Dollar/Bet365 actualisées: %s matchs, %s erreurs", updated, len(errors))
            for err in errors:
                log.error("Données football: %s", err)
    except Exception:
        log.exception("Échec moteur de cotes")


@engine_loop.before_loop
async def before_engine_loop():
    await bot.wait_until_ready()


async def _live_display_signature():
    """Compact signature of what the Discord panel should show."""
    rows = await service.live_matches(100)
    return tuple(
        (str(r["event_id"]), str(r["live_phase"] or r["match_status"] or ""),
         r["home_score"], r["away_score"], str(r["live_clock"] or ""))
        for r in rows
    )


async def live_collector_supervisor():
    """Always-on Live engine with immediate startup scan and self recovery.

    One provider, one malformed match, a missing Discord panel or a gateway reconnect
    can no longer stop the collector. Public-source caches in OddsAPI bound network use.
    """
    await bot.wait_until_ready()
    log.info("Oddium • 5Dollar Pro • Match Center • Live robuste")
    previous_signature = None
    failures = 0
    while not bot.is_closed():
        started = asyncio.get_running_loop().time()
        try:
            changes, settlements, errors = await asyncio.wait_for(
                service.refresh_scores_and_settle(force_live_panel=True),
                timeout=max(20.0, float(SETTINGS.live_poll_seconds) * 4.0),
            )
            current_signature = await _live_display_signature()
            display_changed = current_signature != previous_signature
            previous_signature = current_signature
            failures = 0

            if changes or settlements or display_changed:
                log.info(
                    "Oddium Live: %s match(s) visible(s), %s changement(s), %s pari(s) réglé(s)",
                    len(current_signature), changes, len(settlements),
                )
                events = service.last_live_events or [{"type": "live_update", "changes": changes}]
                for event in events:
                    event["settlements"] = len(settlements)
                    await live_ws.publish(event)
                # Safety net: even a newly discovered match with no timeline event
                # repaints the panel immediately.
                try:
                    await panel.refresh_existing_live_panel()
                except Exception:
                    log.exception("Rafraîchissement panneau Live impossible")

            for event in settlements:
                await notify_settlement(event)
            for err in errors:
                log.warning("Live source: %s", err)
        except asyncio.TimeoutError:
            failures += 1
            log.warning("Oddium Live: cycle dépassé, reprise automatique (%s)", failures)
        except asyncio.CancelledError:
            raise
        except Exception:
            failures += 1
            log.exception("Oddium Live: erreur cycle, reprise automatique (%s)", failures)

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(1.0, float(SETTINGS.live_poll_seconds) - elapsed))


@tasks.loop(seconds=SETTINGS.live_poll_seconds)
async def scores_loop():
    try:
        scores, settlements, errors = await service.refresh_scores_and_settle()
        if scores or settlements:
            log.info("Oddium Live: %s changement(s) détecté(s), %s pari(s) réglé(s)", scores, len(settlements))
            # Every lifecycle event is pushed: kickoff, clock, score, half-time,
            # second half, extra time, penalties, suspension and full-time.
            events = service.last_live_events or [{"type": "live_update", "changes": scores}]
            for event in events:
                event["settlements"] = len(settlements)
                await live_ws.publish(event)
        for event in settlements:
            await notify_settlement(event)
    except Exception:
        log.exception("Échec collecteur Oddium Live")


@scores_loop.before_loop
async def before_scores_loop():
    await bot.wait_until_ready()


async def notify_settlement(event: dict):
    prefs = await service.ensure_preferences(event["user_id"])
    if not prefs["dm_notifications"] or not prefs["notify_result"]:
        return

    is_combo = bool(event.get("is_combo"))
    ref = f"COMBO-{event['combo_id']}" if is_combo else f"BET-{event['bet_id']}"
    if not await service.mark_notification_sent(event["user_id"], "BET_RESULT", ref):
        return
    user = bot.get_user(event["user_id"]) or await bot.fetch_user(event["user_id"])
    try:
        if is_combo:
            if event["status"] == "WON":
                text = f"🏆 **Combiné gagné — {ref}**\nGain : **+{fmt_num(event['payout'])} {SETTINGS.currency_name}**"
            elif event["status"] == "VOID":
                text = f"♻️ **Combiné remboursé — {ref}**\nRemboursement : **{fmt_num(event['payout'])} {SETTINGS.currency_name}**"
            else:
                text = f"❌ **Combiné perdu — {ref}**\nMise : **{fmt_num(event['stake'])} {SETTINGS.currency_name}**"
        elif event["status"] == "WON":
            text = (
                f"🏆 **Pari gagné — {ref}**\n"
                f"{event['home_team']} {event['home_score']} - {event['away_score']} {event['away_team']}\n"
                f"Gain : **+{fmt_num(event['payout'])} {SETTINGS.currency_name}**"
            )
        else:
            text = (
                f"❌ **Pari perdu — {ref}**\n"
                f"{event['home_team']} {event['home_score']} - {event['away_score']} {event['away_team']}\n"
                f"Mise : **{fmt_num(event['stake'])} {SETTINGS.currency_name}**"
            )
        await user.send(text)
    except discord.Forbidden:
        log.info("DM fermé pour user %s", event["user_id"])


@tasks.loop(minutes=5)
async def notification_loop():
    try:
        reminders = await service.pending_match_reminders()
        for r in reminders:
            if not await service.mark_notification_sent(int(r["user_id"]), "MATCH_REMINDER", r["event_id"]):
                continue
            user = bot.get_user(int(r["user_id"])) or await bot.fetch_user(int(r["user_id"]))
            try:
                await user.send(
                    f"⏰ **Ton match commence bientôt !**\n⚽ {r['home_team']} — {r['away_team']}\n"
                    f"Tu as un pari actif sur cette rencontre."
                )
            except discord.Forbidden:
                pass

        odds_changes = await service.pending_odds_change_notifications()
        for r in odds_changes:
            if not await service.mark_notification_sent(int(r["user_id"]), "ODDS_CHANGE", r["reference"]):
                continue
            user = bot.get_user(int(r["user_id"])) or await bot.fetch_user(int(r["user_id"]))
            try:
                direction = "📈" if float(r["current_odd"]) > float(r["bet_odd"]) else "📉"
                await user.send(
                    f"{direction} **Cote modifiée**\n⚽ {r['home_team']} — {r['away_team']}\n"
                    f"Ta cote placée reste **{float(r['bet_odd']):.2f}** ; la cote actuelle est **{float(r['current_odd']):.2f}**.\n"
                    "Ton pari existant n'est pas modifié."
                )
            except discord.Forbidden:
                pass
    except Exception:
        log.exception("Échec notifications")


@notification_loop.before_loop
async def before_notification_loop():
    await bot.wait_until_ready()


@tasks.loop(seconds=SETTINGS.panel_refresh_seconds)
async def panel_loop():
    try:
        await panel.refresh_existing_panel()
    except Exception:
        log.exception("Échec rafraîchissement panneau")


@panel_loop.before_loop
async def before_panel_loop():
    await bot.wait_until_ready()


@tasks.loop(hours=SETTINGS.backup_every_hours)
async def backup_loop():
    try:
        path = await db.backup()
        log.info("Sauvegarde automatique créée: %s", path)
    except Exception:
        log.exception("Échec sauvegarde automatique")


@backup_loop.before_loop
async def before_backup_loop():
    await bot.wait_until_ready()


async def shutdown():
    await odds_api.close()


if __name__ == "__main__":
    if not SETTINGS.discord_token:
        raise SystemExit("DISCORD_TOKEN manquant dans .env")
    try:
        bot.run(SETTINGS.discord_token, log_handler=None)
    finally:
        try:
            asyncio.run(shutdown())
        except RuntimeError:
            pass
