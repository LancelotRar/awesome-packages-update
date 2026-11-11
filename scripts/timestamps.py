"""Beijing-time (UTC+8) helpers for every timestamp this project records.

GitHub reports UTC, but the people reading ``data/state.json`` and the
Telegram notifications work in Beijing time.  Everything stored or shown
therefore uses an explicit ``+08:00`` offset, so a value is never mistaken
for UTC the way a bare ``Z`` value would be.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def parse(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, or return ``None`` when *value* isn't one.

    Naive values are treated as UTC, matching GitHub's own output.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def now_iso() -> str:
    """Current instant as ``YYYY-MM-DDTHH:MM:SS+08:00``."""
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def to_beijing_iso(value: str) -> str:
    """Convert a timestamp to Beijing time, keeping second precision.

    Non-timestamps — an empty marker or an asset fingerprint — are returned
    unchanged so callers can normalize blindly.
    """
    parsed = parse(value)
    if parsed is None:
        return value
    return parsed.astimezone(BEIJING_TZ).isoformat(timespec="seconds")


def to_beijing_date(value: str) -> str:
    """Return *value*'s Beijing calendar date as ``YYYY-MM-DD``.

    Falls back to the value's leading date when it isn't a timestamp.
    """
    parsed = parse(value)
    if parsed is None:
        return (value or "")[:10]
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
