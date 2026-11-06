"""Detection strategies: decide whether a watch item has an update.

``latest`` compares the newest release's ``updated_at``.
``tag`` fingerprints a named release's matched assets so in-place asset
replacement is detected even though the release itself never changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from github_api import fetch_latest_release, fetch_release_by_tag, get_matching_assets
from watchlist import STRATEGY_LATEST, STRATEGY_TAG, WatchItem


@dataclass(frozen=True)
class Detection:
    """What the upstream currently advertises for one watch item."""

    release: dict
    marker: str
    asset_count: int


def asset_fingerprint(assets: list[dict]) -> str:
    """Return a stable SHA-256 digest of *assets*.

    Covers name, size and updated_at, so adding, removing, renaming,
    re-uploading or replacing an asset changes the digest. Asset order
    from the API is irrelevant.
    """
    parts = sorted(
        (a["name"], a["size"], a.get("updated_at", ""))
        for a in assets
    )
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect(item: WatchItem, token: str = "") -> Detection | None:
    """Return the current detection for *item*, or ``None`` when the
    repository has no releases yet (``latest`` only).
    """
    if item.strategy == STRATEGY_LATEST:
        release = fetch_latest_release(item.repo, token)
        if release is None:
            return None
        return Detection(
            release=release,
            marker=release.get("updated_at", ""),
            asset_count=len(release.get("assets", [])),
        )

    if item.strategy == STRATEGY_TAG:
        release = fetch_release_by_tag(item.repo, item.release_tag, token)
        if release is None:
            raise RuntimeError(
                f"Release tag '{item.release_tag}' not found in {item.repo}"
            )

        assets = get_matching_assets(release, item.asset_include,
                                     item.asset_exclude)
        if not assets:
            raise RuntimeError(
                "No assets matched ASSET_INCLUDE/ASSET_EXCLUDE — "
                "refusing to persist an empty fingerprint"
            )

        # A rolling release keeps its original published_at; show the
        # newest matched asset date instead.
        display = dict(release)
        display["published_at"] = (
            max((a.get("updated_at", "") for a in assets), default="")
            or release.get("published_at", "")
        )
        return Detection(
            release=display,
            marker=asset_fingerprint(assets),
            asset_count=len(assets),
        )

    raise RuntimeError(f"Unknown strategy: {item.strategy}")
