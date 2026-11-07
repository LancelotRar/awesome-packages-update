"""Unit tests for ``audit_assets`` — zero-match detection without network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audit_assets
from detector import Detection
from watchlist import NotifyCopy, WatchItem


def _item(pid: str, *, enabled: bool = True, include: str = ".apk") -> WatchItem:
    return WatchItem(id=pid, repo=f"o/{pid}", strategy="latest",
                     notify=NotifyCopy(title=pid), asset_include=include,
                     enabled=enabled)


def _detection(tag: str = "v1", *, assets: int = 1) -> Detection:
    return Detection(release={"tag_name": tag,
                              "assets": [{"name": f"a{i}"} for i in range(assets)]},
                     marker="m", asset_count=assets)


def _patch(monkeypatch, items, *, detected=None, matched=None, error=None):
    monkeypatch.setenv("WATCHLIST", "unused.yaml")
    monkeypatch.setattr(audit_assets, "load_watchlist", lambda path: items)

    def fake_detect(item, token):
        if error is not None:
            raise error
        return detected

    monkeypatch.setattr(audit_assets, "detect", fake_detect)
    monkeypatch.setattr(audit_assets, "get_matching_assets",
                        lambda release, include, exclude: matched or [])


def test_all_items_matching_succeeds(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(), matched=[{"name": "x"}])

    assert audit_assets.main() == 0


def test_zero_match_fails(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(assets=2), matched=[])

    assert audit_assets.main() == 1


def test_release_without_assets_is_only_a_warning(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(assets=0), matched=[])

    assert audit_assets.main() == 0


def test_detection_error_fails(monkeypatch):
    _patch(monkeypatch, [_item("a")], error=RuntimeError("boom"))

    assert audit_assets.main() == 1


def test_disabled_items_are_skipped(monkeypatch):
    _patch(monkeypatch, [_item("a", enabled=False)], matched=[])

    assert audit_assets.main() == 0
