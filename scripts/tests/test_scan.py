"""Unit tests for ``scan`` — the shared detect-and-match policy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scan
from detector import Detection
from watchlist import NotifyCopy, WatchItem


def _item(*, include: str = ".apk") -> WatchItem:
    return WatchItem(id="a", repo="o/a", strategy="latest",
                     notify=NotifyCopy(title="a"), asset_include=include)


def _detection(*, assets: int = 1, tag: str = "v1") -> Detection:
    return Detection(
        release={"tag_name": tag,
                 "assets": [{"name": f"a{i}"} for i in range(assets)]},
        marker="m",
        asset_count=assets,
    )


def _patch(monkeypatch, detection=None, matched=None, error=None):
    if error is not None:
        def fake_detect(item, token):
            raise error
    else:
        def fake_detect(item, token):
            return detection

    monkeypatch.setattr(scan, "detect", fake_detect)
    monkeypatch.setattr(scan, "get_matching_assets",
                        lambda release, include, exclude: matched or [])


def test_healthy_item_carries_detection_and_assets(monkeypatch):
    _patch(monkeypatch, _detection(tag="v2", assets=2),
           matched=[{"name": "a.apk"}])

    result = scan.scan_item(_item())

    assert result.error == ""
    assert result.problem == ""
    assert result.tag == "v2"
    assert result.asset_count == 2
    assert result.matched == ({"name": "a.apk"},)


def test_detection_error_becomes_a_problem(monkeypatch):
    _patch(monkeypatch, error=RuntimeError("boom"))

    result = scan.scan_item(_item())

    assert result.error == "boom"
    assert "boom" in result.problem
    assert result.detection is None


def test_no_releases_is_not_a_problem(monkeypatch):
    _patch(monkeypatch, detection=None)

    result = scan.scan_item(_item())

    assert result.detection is None
    assert result.error == ""
    assert result.problem == ""


def test_zero_match_with_a_filter_is_a_problem(monkeypatch):
    _patch(monkeypatch, _detection(assets=2, tag="v3"), matched=[])

    result = scan.scan_item(_item(include=".apk+arm64"))

    assert result.problem
    assert "v3" in result.problem
    assert ".apk+arm64" in result.problem


def test_zero_match_without_a_filter_is_not_a_problem(monkeypatch):
    _patch(monkeypatch, _detection(assets=2), matched=[])

    result = scan.scan_item(_item(include=""))

    assert result.problem == ""
