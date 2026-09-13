from datetime import datetime, timezone
from legacy_bet.service import parse_iso


def test_parse_iso_zulu():
    value = parse_iso("2026-09-11T18:00:00Z")
    assert value.tzinfo is not None
    assert value.astimezone(timezone.utc).hour == 18
