"""Unit tests for ``detector`` — tag filter on the ``latest`` strategy.

``detector`` lives in the parent scripts directory (same convention as
``test_github_api.py``), so make it importable first. The GitHub-facing
functions are replaced on the ``detector`` module, which is where
``detector`` looks them up.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detector
from watchlist import STRATEGY_LATEST, WatchItem


def _item(*patterns: str) -> WatchItem:
    return WatchItem(id="bitwarden-desktop", repo="bitwarden/clients",
                     strategy=STRATEGY_LATEST,
                     release_tag_include=patterns)


def _release(tag: str, published: str, *, prerelease: bool = False,
             updated: str = "", assets: int = 0) -> dict:
    return {
        "tag_name": tag,
        "published_at": published,
        "updated_at": updated or published,
        "prerelease": prerelease,
        "assets": [{"name": f"a{i}.exe", "size": 1, "updated_at": updated or published}
                   for i in range(assets)],
    }


def _patch(monkeypatch, releases: list[dict], full: dict | None = None):
    """Serve *releases* from the list endpoint and *full* from the by-tag one."""
    monkeypatch.setattr(detector, "fetch_releases", lambda repo, token="": releases)
    monkeypatch.setattr(
        detector, "fetch_release_by_tag",
        lambda repo, tag, token="": full,
    )


def test_picks_newest_matching_release_and_counts_its_assets(monkeypatch):
    releases = [
        _release("desktop-v2026.1.0", "2026-01-01T00:00:00Z"),
        _release("desktop-v2026.3.0", "2026-03-01T00:00:00Z"),
        _release("cli-v2026.4.0", "2026-04-01T00:00:00Z"),
    ]
    full = _release("desktop-v2026.3.0", "2026-03-01T00:00:00Z",
                    updated="2026-03-02T00:00:00Z", assets=4)
    _patch(monkeypatch, releases, full)

    result = detector.detect(_item("desktop-*"))

    assert result is not None
    assert result.release["tag_name"] == "desktop-v2026.3.0"
    assert result.marker == "2026-03-02T00:00:00Z"
    assert result.asset_count == 4


def test_prerelease_is_never_selected(monkeypatch):
    releases = [
        _release("desktop-v2026.5.0", "2026-05-01T00:00:00Z", prerelease=True),
        _release("desktop-v2026.4.0", "2026-04-01T00:00:00Z"),
    ]
    full = _release("desktop-v2026.4.0", "2026-04-01T00:00:00Z", assets=2)
    _patch(monkeypatch, releases, full)

    result = detector.detect(_item("desktop-*"))

    assert result.release["tag_name"] == "desktop-v2026.4.0"


def test_pattern_matching_ignores_case(monkeypatch):
    releases = [_release("Desktop-v2026.1.0", "2026-01-01T00:00:00Z")]
    full = _release("Desktop-v2026.1.0", "2026-01-01T00:00:00Z")
    _patch(monkeypatch, releases, full)

    result = detector.detect(_item("DESKTOP-*"))

    assert result.release["tag_name"] == "Desktop-v2026.1.0"


def test_any_pattern_may_match(monkeypatch):
    releases = [
        _release("browser-v1", "2026-01-01T00:00:00Z"),
        _release("desktop-v2", "2026-02-01T00:00:00Z"),
        _release("cli-v3", "2026-03-01T00:00:00Z"),
    ]
    full = _release("desktop-v2", "2026-02-01T00:00:00Z")
    _patch(monkeypatch, releases, full)

    result = detector.detect(_item("browser-*", "desktop-*"))

    assert result.release["tag_name"] == "desktop-v2"


def test_entries_without_a_tag_are_skipped(monkeypatch):
    releases = [
        {"tag_name": "", "published_at": "2026-09-01T00:00:00Z",
         "prerelease": False, "assets": []},
        _release("desktop-v1", "2026-01-01T00:00:00Z"),
    ]
    full = _release("desktop-v1", "2026-01-01T00:00:00Z")
    _patch(monkeypatch, releases, full)

    result = detector.detect(_item("desktop-*"))

    assert result.release["tag_name"] == "desktop-v1"


def test_no_match_raises_naming_repo_and_patterns(monkeypatch):
    _patch(monkeypatch, [_release("cli-v1", "2026-01-01T00:00:00Z")])

    with pytest.raises(RuntimeError, match="bitwarden/clients") as excinfo:
        detector.detect(_item("desktop-*", "server-*"))

    assert "desktop-*" in str(excinfo.value)
    assert "server-*" in str(excinfo.value)


def test_selected_tag_vanishing_before_full_fetch_raises(monkeypatch):
    _patch(monkeypatch, [_release("desktop-v1", "2026-01-01T00:00:00Z")], None)

    with pytest.raises(RuntimeError, match="desktop-v1"):
        detector.detect(_item("desktop-*"))


def test_without_tag_filter_picks_newest_stable_release(monkeypatch):
    releases = [
        _release("v9", "2026-01-01T00:00:00Z"),
        _release("v10-beta", "2026-02-01T00:00:00Z", prerelease=True),
        _release("v10", "2026-03-01T00:00:00Z"),
    ]
    releases[2]["draft"] = False
    full = _release("v10", "2026-03-01T00:00:00Z",
                    updated="2026-03-02T00:00:00Z", assets=3)
    _patch(monkeypatch, releases, full)

    result = detector.detect(WatchItem(id="plain", repo="o/plain",
                                       strategy=STRATEGY_LATEST))

    assert result is not None
    assert result.release["tag_name"] == "v10"
    assert result.marker == "2026-03-02T00:00:00Z"
    assert result.asset_count == 3


def test_draft_release_is_never_selected(monkeypatch):
    draft = _release("v11", "2026-04-01T00:00:00Z")
    draft["draft"] = True
    stable = _release("v10", "2026-03-01T00:00:00Z")
    _patch(monkeypatch, [draft, stable], stable)

    result = detector.detect(WatchItem(id="plain", repo="o/plain",
                                       strategy=STRATEGY_LATEST))

    assert result.release["tag_name"] == "v10"


def test_no_releases_returns_none(monkeypatch):
    _patch(monkeypatch, [], None)

    assert detector.detect(WatchItem(id="plain", repo="o/plain",
                                     strategy=STRATEGY_LATEST)) is None


def test_only_prereleases_raises(monkeypatch):
    _patch(monkeypatch, [_release("v1-beta", "2026-01-01T00:00:00Z",
                                  prerelease=True)], None)

    with pytest.raises(RuntimeError, match="No stable release"):
        detector.detect(WatchItem(id="plain", repo="o/plain",
                                  strategy=STRATEGY_LATEST))
