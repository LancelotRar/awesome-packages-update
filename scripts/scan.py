"""Resolve one watch item to its current release and matched assets.

The delivery runner and the weekly audit both cross this seam, so the
meaning of "no releases", "detection failed" and "the asset filter matched
nothing" is defined exactly once instead of once per caller. Each scan has
exactly one status and, for failures, exactly one canonical reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from detector import Detection, detect
from watchlist import WatchItem


class ScanStatus(StrEnum):
    """The four mutually exclusive outcomes of scanning one watch item."""

    READY = "ready"
    NO_RELEASES = "no_releases"
    DETECTION_FAILED = "detection_failed"
    ASSETS_UNMATCHED = "assets_unmatched"


@dataclass(frozen=True)
class ScanResult:
    """What upstream currently offers for one watch item.

    ``status`` says which outcome applies. ``reason`` is the canonical,
    caller-facing explanation for the two failure statuses and empty
    otherwise. ``matched`` is always the detection's matched set, so the
    asset filter runs once per scan instead of once per consumer.
    """

    item: WatchItem
    status: ScanStatus = ScanStatus.NO_RELEASES
    detection: Detection | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        carries_detection = self.status in (
            ScanStatus.READY, ScanStatus.ASSETS_UNMATCHED,
        )
        if carries_detection != (self.detection is not None):
            raise ValueError(f"{self.status.value} detection mismatch")
        if self.status in (ScanStatus.DETECTION_FAILED,
                           ScanStatus.ASSETS_UNMATCHED):
            if not self.reason:
                raise ValueError(f"{self.status.value} requires a reason")
        elif self.reason:
            raise ValueError(f"{self.status.value} must not carry a reason")

    @property
    def tag(self) -> str:
        if self.detection is None:
            return ""
        return str(self.detection.release.get("tag_name", "?"))

    @property
    def matched(self) -> tuple[dict, ...]:
        if self.detection is None:
            return ()
        return self.detection.matched


def scan_item(item: WatchItem, token: str = "") -> ScanResult:
    """Resolve *item* without raising: failures become ``ScanResult`` fields."""
    try:
        detection = detect(item, token)
    except Exception as e:  # one broken item must not hide the others
        return ScanResult(item, ScanStatus.DETECTION_FAILED,
                          reason=f"检测失败：{e}")

    if detection is None:
        return ScanResult(item, ScanStatus.NO_RELEASES)

    if item.asset_include.strip() and not detection.matched:
        tag = detection.release.get("tag_name", "?")
        return ScanResult(
            item, ScanStatus.ASSETS_UNMATCHED, detection,
            reason=f"{tag} 没有资产命中 {item.asset_include!r}",
        )
    return ScanResult(item, ScanStatus.READY, detection)
