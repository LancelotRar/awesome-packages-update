"""Unit tests for ``run_checks`` — one job, many watch items."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_checks
from detector import Detection
from state import load_state, save_state
from watchlist import NotifyCopy, WatchItem


def _items():
    return [
        WatchItem(id="a", repo="o/a", strategy="latest",
                  notify=NotifyCopy(title="a")),
        WatchItem(id="b", repo="o/b", strategy="latest",
                  notify=NotifyCopy(title="b")),
    ]


def _detection(marker: str) -> Detection:
    return Detection(
        release={
            "name": "v2",
            "tag_name": "v2",
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://example.com/releases/v2",
            "assets": [],
        },
        marker=marker,
        asset_count=0,
    )


def _clear_env(monkeypatch):
    """Remove every env var that changes how ``main()`` resolves paths."""
    for var in ("PROJECT", "FORCE", "TG_CHAT_ID_ADMIN", "GITHUB_STEP_SUMMARY",
                "WATCHLIST", "STATE_FILE"):
        monkeypatch.delenv(var, raising=False)


def _run(monkeypatch, tmp_path, *, markers=None, notify_ok=True,
         items=None, project=None, force=False, admin=None,
         detected=None, summary=None, telegram_env=True):
    items = items or _items()
    markers = markers or {}

    _clear_env(monkeypatch)
    monkeypatch.setenv("TG_CHAT_ID", "111")
    if not telegram_env:
        monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "H")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    if summary is not None:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(run_checks, "load_watchlist", lambda path: items)

    if project is not None:
        monkeypatch.setenv("PROJECT", project)
    if force:
        monkeypatch.setenv("FORCE", "true")
    if admin is not None:
        monkeypatch.setenv("TG_CHAT_ID_ADMIN", admin)

    def fake_detect(item, token):
        if detected is not None:
            detected.append(item.id)
        outcome = markers.get(item.id, _detection("m2"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(run_checks, "detect", fake_detect)

    delivered: list[str] = []
    alerts: list[str] = []

    async def fake_notify(release, cfg, token, include, exclude):
        delivered.append(cfg.notify_title)
        return notify_ok

    async def fake_send_text(text, cfg):
        alerts.append(text)
        return True

    monkeypatch.setattr(run_checks, "notify", fake_notify)
    monkeypatch.setattr(run_checks, "send_text", fake_send_text)

    code = asyncio.run(run_checks.main())
    return code, delivered, alerts, load_state(tmp_path / "state.json")


def test_delivers_each_item_and_persists_marker(monkeypatch, tmp_path):
    code, delivered, _alerts, state = _run(monkeypatch, tmp_path)

    assert code == 0
    assert delivered == ["a", "b"]
    assert state["a"]["marker"] == "m2"
    assert state["a"]["failures"] == 0


def test_unchanged_marker_skips_delivery(monkeypatch, tmp_path):
    save_state(tmp_path / "state.json", {
        "a": {"marker": "m2"},
        "b": {"marker": "m2"},
    })

    code, delivered, _alerts, state = _run(monkeypatch, tmp_path)

    assert code == 0
    assert delivered == []
    assert state["a"]["last_checked"]


def test_failed_delivery_keeps_marker_and_counts(monkeypatch, tmp_path):
    code, delivered, _alerts, state = _run(
        monkeypatch, tmp_path, notify_ok=False,
    )

    assert code == 1
    assert len(delivered) == 2
    assert state["a"]["marker"] == ""
    assert state["a"]["failures"] == 1


def test_one_detection_failure_does_not_block_the_other(monkeypatch, tmp_path):
    code, delivered, _alerts, state = _run(
        monkeypatch, tmp_path,
        markers={"a": _detection("m2"), "b": RuntimeError("api down")},
    )

    assert code == 1
    assert delivered == ["a"]
    assert state["a"]["marker"] == "m2"
    assert state["b"]["failures"] == 1
    assert "api down" in state["b"]["last_error"]


def test_force_delivers_even_when_marker_matches(monkeypatch, tmp_path):
    save_state(tmp_path / "state.json", {"a": {"marker": "m2"},
                                         "b": {"marker": "m2"}})

    code, delivered, _alerts, _state = _run(
        monkeypatch, tmp_path, force=True,
    )

    assert code == 0
    assert delivered == ["a", "b"]


def test_unknown_project_fails_without_delivery(monkeypatch, tmp_path):
    code, delivered, _alerts, _state = _run(
        monkeypatch, tmp_path, project="nope",
    )

    assert code == 1
    assert delivered == []


def test_alert_after_three_consecutive_failures(monkeypatch, tmp_path):
    save_state(tmp_path / "state.json", {
        "a": {"marker": "", "failures": 2},
        "b": {"marker": "m2"},
    })

    code, _delivered, alerts, state = _run(
        monkeypatch, tmp_path,
        markers={"a": RuntimeError("still down")},
        admin="999",
    )

    assert code == 1
    assert len(alerts) == 1
    assert "a" in alerts[0]
    assert state["a"]["failures"] == 3
    assert state["a"]["alerted"] is True


def _capture_default_paths(monkeypatch) -> tuple[Path, Path]:
    """Run main() without WATCHLIST/STATE_FILE, returning resolved paths.

    Stubs the file-touching collaborators so the defaults can be asserted
    without reading or writing the real watch list and state file.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("TG_CHAT_ID", "111")
    seen: list[Path] = []

    def fake_load_watchlist(path):
        seen.append(Path(path))
        return []

    def fake_load_state(path):
        seen.append(Path(path))
        return {}

    monkeypatch.setattr(run_checks, "load_watchlist", fake_load_watchlist)
    monkeypatch.setattr(run_checks, "load_state", fake_load_state)
    monkeypatch.setattr(run_checks, "save_state", lambda path, state: None)

    assert asyncio.run(run_checks.main()) == 0
    return seen[0], seen[1]


def test_default_watchlist_resolves_to_repository_root(monkeypatch):
    watchlist, _state = _capture_default_paths(monkeypatch)

    repo_root = Path(run_checks.__file__).resolve().parent.parent
    assert watchlist == repo_root / "projects.yaml"


def test_default_state_file_resolves_to_repository_root(monkeypatch):
    _watchlist, state = _capture_default_paths(monkeypatch)

    repo_root = Path(run_checks.__file__).resolve().parent.parent
    assert state == repo_root / "data" / "state.json"


def _mixed_items():
    return [
        WatchItem(id="a", repo="o/a", strategy="latest",
                  notify=NotifyCopy(title="a"), enabled=False),
        WatchItem(id="b", repo="o/b", strategy="latest",
                  notify=NotifyCopy(title="b")),
    ]


def test_disabled_item_is_not_detected_or_delivered(monkeypatch, tmp_path):
    detected: list[str] = []

    code, delivered, _alerts, state = _run(
        monkeypatch, tmp_path, items=_mixed_items(), detected=detected,
    )

    assert code == 0
    assert detected == ["b"]
    assert delivered == ["b"]
    assert "a" not in state


def test_selecting_a_disabled_item_fails(monkeypatch, tmp_path):
    code, delivered, _alerts, state = _run(
        monkeypatch, tmp_path, items=_mixed_items(), project="a",
    )

    assert code == 1
    assert delivered == []
    assert "a" not in state


def test_force_does_not_reenable_a_disabled_item(monkeypatch, tmp_path):
    _code, delivered, _alerts, _state = _run(
        monkeypatch, tmp_path, items=_mixed_items(), force=True,
    )

    assert delivered == ["b"]


def test_summary_follows_watch_list_order(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    items = [
        WatchItem(id="a", repo="o/a", strategy="latest",
                  notify=NotifyCopy(title="a")),
        WatchItem(id="b", repo="o/b", strategy="latest",
                  notify=NotifyCopy(title="b"), enabled=False),
        WatchItem(id="c", repo="o/c", strategy="latest",
                  notify=NotifyCopy(title="c")),
    ]

    _run(monkeypatch, tmp_path, items=items, summary=summary)

    text = summary.read_text(encoding="utf-8")
    assert text.index("| a |") < text.index("| b | ⏭ disabled |") < text.index("| c |")


def test_all_items_disabled_succeeds_without_telegram_env(monkeypatch, tmp_path):
    items = [
        WatchItem(id="a", repo="o/a", strategy="latest",
                  notify=NotifyCopy(title="a"), enabled=False),
    ]

    code, delivered, _alerts, state = _run(
        monkeypatch, tmp_path, items=items, telegram_env=False,
    )

    assert code == 0
    assert delivered == []
    assert state == {}
