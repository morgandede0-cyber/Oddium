from __future__ import annotations

from datetime import datetime


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, including the common trailing ``Z`` form."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
