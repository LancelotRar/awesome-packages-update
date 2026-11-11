"""Resolve one watch item to its current release and matched assets.

The delivery runner and the weekly audit both cross this seam, so the
meaning of "no releases", "detection failed" and "the asset filter matched
nothing" is defined exactly once instead of once per caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from detector import Detection, detect
from github_api import get_matching_assets
from watchlist import WatchItem


@dataclass(frozen=True)
class ScanResult:
    """What upstream currently offers for one watch item.

    ``error`` means detection itself failed. ``problem`` is a caller-facing
    reason the item cannot be delivered (empty when the item is healthy);
    a zero match only counts when the item declares an include filter.
    """

    item: WatchItem
    detection: Detection | None = None
    matched: tuple[dict, ...] = ()
    error: str = ""
    problem: str = ""

    @property
    def tag(self) -> str:
        if self.detection is None:
            return ""
        return str(self.detection.release.get("tag_name", "?"))

    @property
    def asset_count(self) -> int:
        return self.detection.asset_count if self.detection else 0


def scan_item(item: WatchItem, token: str = "") -> ScanResult:
    """Resolve *item* without raising: failures become ``ScanResult`` fields."""
    try:
        detection = detect(item, token)
    except Exception as e:  # one broken item must not hide the others
        return ScanResult(item, error=str(e), problem=f"检测失败：{e}")

    if detection is None:
        return ScanResult(item)

    matched = tuple(get_matching_assets(
        detection.release, item.asset_include, item.asset_exclude,
    ))
    if item.asset_include.strip() and not matched:
        return ScanResult(
            item, detection, matched,
            problem=f"{detection.release.get('tag_name', '?')} 没有资产命中 "
                    f"{item.asset_include!r}",
        )
    return ScanResult(item, detection, matched)
