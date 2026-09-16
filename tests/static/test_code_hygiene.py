from __future__ import annotations
import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = [ROOT / "main.py", ROOT / "config.py", *sorted((ROOT / "legacy_bet").rglob("*.py"))]


def test_active_python_parses():
    for path in ACTIVE:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_shadowed_top_level_definitions():
    offenders = []
    for path in ACTIVE:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = Counter(
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        offenders += [(str(path.relative_to(ROOT)), name, count) for name, count in names.items() if count > 1]
    assert offenders == []


def test_removed_provider_modules_are_not_imported():
    forbidden = {"api_football", "market_model"}
    offenders = []
    for path in ACTIVE:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(part in forbidden for part in name.split(".")):
                    offenders.append((str(path.relative_to(ROOT)), name))
    assert offenders == []


def test_carousel_assets_exist_after_package_reorganization():
    import ast

    ui_path = ROOT / "legacy_bet" / "discord_ui" / "ui.py"
    tree = ast.parse(ui_path.read_text(encoding="utf-8"))
    mapping = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CAROUSEL_ASSETS" for t in node.targets):
            mapping = ast.literal_eval(node.value)
            break
    assert mapping, "CAROUSEL_ASSETS absent ou vide"
    asset_dir = ROOT / "assets"
    missing = [name for name in mapping.values() if not (asset_dir / name).is_file()]
    assert not missing, f"Assets de carrousel manquants: {missing}"
    assert (asset_dir / "oddium_welcome.png").is_file()


def test_runtime_has_no_retired_provider_identifiers():
    retired = ("api_football_fixture_id", "Oddium Fusion", "sofascore", "fotmob", "thesportsdb", "football-data.org", "api-football")
    offenders = []
    for path in ACTIVE:
        text = path.read_text(encoding="utf-8").lower()
        for token in retired:
            if token.lower() in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert not offenders, "Traces de fournisseurs retirés dans le runtime: " + ", ".join(offenders)


def test_png_assets_have_real_png_signature():
    for path in (ROOT / "assets").glob("*.png"):
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} porte une extension .png mais son contenu n'est pas PNG"
