"""Unit tests for ``scan`` — the shared detect-and-match policy."""

import pytest

import scan
from detector import Detection
from watchlist import NotifyCopy, WatchItem


def _item(*, include: str = ".apk") -> WatchItem:
    return WatchItem(id="a", repo="o/a", strategy="latest",
                     notify=NotifyCopy(title="a"), asset_include=include)


def _detection(*, matched: int = 1, tag: str = "v1") -> Detection:
    return Detection(
        release={"tag_name": tag},
        marker="m",
        matched=tuple({"name": f"a{i}.apk"} for i in range(matched)),
    )


def _patch(monkeypatch, detection=None, error=None):
    def fake_detect(item, token):
        if error is not None:
            raise error
        return detection

    monkeypatch.setattr(scan, "detect", fake_detect)


def test_healthy_item_is_ready_and_carries_matched_assets(monkeypatch):
    _patch(monkeypatch, _detection(tag="v2", matched=2))

    result = scan.scan_item(_item())

    assert result.status is scan.ScanStatus.READY
    assert result.reason == ""
    assert result.tag == "v2"
    assert len(result.matched) == 2


def test_detection_error_is_the_single_canonical_reason(monkeypatch):
    _patch(monkeypatch, error=RuntimeError("boom"))

    result = scan.scan_item(_item())

    assert result.status is scan.ScanStatus.DETECTION_FAILED
    assert result.reason.startswith("检测失败：")
    assert "boom" in result.reason
    assert result.detection is None
    assert result.matched == ()


def test_no_releases_is_its_own_status(monkeypatch):
    _patch(monkeypatch, detection=None)

    result = scan.scan_item(_item())

    assert result.status is scan.ScanStatus.NO_RELEASES
    assert result.reason == ""
    assert result.detection is None


def test_zero_match_with_a_filter_is_unmatched(monkeypatch):
    _patch(monkeypatch, _detection(tag="v3", matched=0))

    result = scan.scan_item(_item(include=".apk+arm64"))

    assert result.status is scan.ScanStatus.ASSETS_UNMATCHED
    assert "v3" in result.reason
    assert ".apk+arm64" in result.reason
    assert result.detection is not None


def test_zero_match_without_a_filter_is_ready(monkeypatch):
    _patch(monkeypatch, _detection(matched=0))

    result = scan.scan_item(_item(include=""))

    assert result.status is scan.ScanStatus.READY
    assert result.matched == ()


def test_ready_without_a_detection_is_rejected():
    with pytest.raises(ValueError, match="detection mismatch"):
        scan.ScanResult(_item(), scan.ScanStatus.READY)


def test_failure_without_a_reason_is_rejected():
    with pytest.raises(ValueError, match="requires a reason"):
        scan.ScanResult(_item(), scan.ScanStatus.DETECTION_FAILED)
