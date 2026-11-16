"""Unit tests for ``timestamps`` — Beijing-time helpers."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timestamps import BEIJING_TZ, now_iso, to_beijing_date, to_beijing_iso


def test_now_iso_is_beijing_time():
    value = now_iso()

    parsed = datetime.fromisoformat(value)

    assert parsed.utcoffset() == BEIJING_TZ.utcoffset(None)
    assert value.endswith("+08:00")


def test_to_beijing_iso_shifts_utc_into_the_next_day():
    assert to_beijing_iso("2026-11-11T17:52:43Z") == "2026-11-12T01:52:43+08:00"


def test_to_beijing_iso_is_idempotent():
    value = "2026-11-12T01:52:43+08:00"

    assert to_beijing_iso(value) == value


def test_to_beijing_iso_passes_non_timestamps_through():
    assert to_beijing_iso("") == ""
    assert to_beijing_iso("97619b4e") == "97619b4e"


def test_to_beijing_date_uses_the_local_calendar_day():
    assert to_beijing_date("2026-11-11T17:52:43Z") == "2026-11-12"
    assert to_beijing_date("2026-11-11T15:59:59Z") == "2026-11-11"


def test_to_beijing_date_falls_back_when_unparseable():
    assert to_beijing_date("2026-13-45?") == "2026-13-45"
    assert to_beijing_date("") == ""
