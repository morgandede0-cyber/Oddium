from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("oddium.team_logos")
ROOT = Path(__file__).resolve().parents[2]
LOGO_DIR = ROOT / "assets" / "team_logos"
INDEX_FILE = LOGO_DIR / "index.json"
MANIFEST_URL = "https://raw.githubusercontent.com/frertommy/team-logos/main/manifest.json"
ALLOWED_GROUPS = {"Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1", "Champions League"}


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\b(fc|afc|cf|ac|sc|as|ssc|calcio|football club|club de futbol)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def load_index() -> dict[str, str]:
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.get("aliases", {}).items()}
    except Exception:
        return {}


async def sync_team_logos(*, force: bool = False) -> dict[str, int]:
    """Cache les écussons localement. Purement visuel: aucune donnée football ne vient d'ici."""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=35, connect=10)
    headers = {"User-Agent": "Oddium/35 TeamIdentity"}
    downloaded = skipped = failed = 0
    aliases: dict[str, str] = load_index()
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(MANIFEST_URL) as resp:
            resp.raise_for_status()
            manifest = await resp.json(content_type=None)
        teams = [t for t in manifest.get("teams", []) if t.get("competition") == "MSI2026" and t.get("group") in ALLOWED_GROUPS]
        sem = asyncio.Semaphore(4)

        async def one(team: dict):
            nonlocal downloaded, skipped, failed
            name = str(team.get("team") or "").strip()
            source = str(team.get("source_logo") or "").strip()
            slug = str(team.get("slug") or normalize_name(name)).strip()
            if not name or not source or not slug or urlparse(source).scheme != "https":
                failed += 1; return
            target = LOGO_DIR / f"{slug}.png"
            for alias in {name, team.get("matched_as") or "", slug}:
                key = normalize_name(str(alias))
                if key: aliases[key] = target.name
            if target.exists() and target.stat().st_size > 1500 and not force:
                skipped += 1; return
            try:
                async with sem:
                    async with session.get(source) as r:
                        r.raise_for_status(); data = await r.read()
                if len(data) < 1500:
                    raise ValueError("logo payload too small")
                target.write_bytes(data); downloaded += 1
            except Exception as exc:
                failed += 1
                log.warning("Logo indisponible pour %s: %s", name, exc)

        await asyncio.gather(*(one(t) for t in teams))
    INDEX_FILE.write_text(json.dumps({"source": MANIFEST_URL, "aliases": aliases}, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Team Identity: %s téléchargés, %s déjà présents, %s échecs", downloaded, skipped, failed)
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed, "aliases": len(aliases)}
