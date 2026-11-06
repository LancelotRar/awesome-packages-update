"""Unit tests for ``state`` — persistence and failure bookkeeping."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import (
    load_state,
    mark_alerted,
    mark_healthy,
    marker_of,
    needs_alert,
    record_delivery,
    record_failure,
    save_state,
)


def test_missing_file_loads_empty(tmp_path):
    assert load_state(tmp_path / "state.json") == {}


def test_corrupt_file_raises_instead_of_resetting(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Corrupt state file"):
        load_state(path)


def test_roundtrip_preserves_markers(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path)
    record_delivery(state, "webhtv", "m-1", "2026-11-06T00:00:00Z")

    save_state(path, state)

    assert marker_of(load_state(path), "webhtv") == "m-1"


def test_delivery_resets_failures_and_alert_flag(tmp_path):
    state = {}
    record_failure(state, "x", "t1", "boom")
    record_failure(state, "x", "t2", "boom")
    mark_alerted(state, "x")
    assert state["x"]["failures"] == 2

    record_delivery(state, "x", "m", "t3")

    assert state["x"]["failures"] == 0
    assert state["x"]["alerted"] is False
    assert state["x"]["last_error"] == ""


def test_failure_increments_and_truncates_error():
    state = {}
    record_failure(state, "x", "t1", "e" * 2000)

    assert state["x"]["failures"] == 1
    assert len(state["x"]["last_error"]) == 500


def test_healthy_run_keeps_marker_but_clears_failures():
    state = {"x": {"marker": "keep", "failures": 2, "alerted": True}}

    mark_healthy(state, "x", "t")

    assert state["x"]["marker"] == "keep"
    assert state["x"]["failures"] == 0
    assert state["x"]["alerted"] is False


def test_alert_only_after_threshold_and_only_once():
    state = {}
    record_failure(state, "x", "t1", "boom")
    record_failure(state, "x", "t2", "boom")
    assert not needs_alert(state, "x", 3)

    record_failure(state, "x", "t3", "boom")
    assert needs_alert(state, "x", 3)

    mark_alerted(state, "x")
    assert not needs_alert(state, "x", 3)


def test_save_is_atomic_and_sorted(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"b": {"marker": "2"}, "a": {"marker": "1"}})

    raw = path.read_text(encoding="utf-8")
    assert raw.index('"a"') < raw.index('"b"')
    assert json.loads(raw)["a"]["marker"] == "1"
