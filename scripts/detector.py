"""Detection strategies: decide whether a watch item has an update.

``latest`` compares the newest release's ``updated_at``. When a tag
filter is configured, "newest" means the newest release whose tag matches
any pattern. ``tag`` fingerprints a named release's matched assets so
in-place asset replacement is detected even though the release itself
never changes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass

from github_api import (
    fetch_latest_release,
    fetch_release_by_tag,
    fetch_releases,
    get_matching_assets,
)
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


def _latest_detection(release: dict) -> Detection:
    """Wrap a release in the detection shape ``latest`` has always used."""
    return Detection(
        release=release,
        marker=release.get("updated_at", ""),
        asset_count=len(release.get("assets", [])),
    )


def _select_tag_included(item: WatchItem, releases: list[dict]) -> dict:
    """Return the newest non-prerelease release whose tag matches a pattern.

    Matching is glob-style and case-insensitive; patterns are OR'd. An
    entry without a tag can never be selected. Raises when nothing
    matches, mirroring the ``tag`` strategy's "tag not found" failure.
    """
    def _matches(tag: str) -> bool:
        lowered = tag.lower()
        return any(fnmatch.fnmatchcase(lowered, p.lower())
                   for p in item.release_tag_include)

    candidates = [
        r for r in releases
        if isinstance(r, dict) and r.get("tag_name")
        and not r.get("prerelease") and _matches(r["tag_name"])
    ]
    if not candidates:
        patterns = ", ".join(item.release_tag_include)
        raise RuntimeError(
            f"No release tag in {item.repo} matched: {patterns}"
        )
    return max(candidates, key=lambda r: r.get("published_at") or "")


def detect(item: WatchItem, token: str = "") -> Detection | None:
    """Return the current detection for *item*, or ``None`` when the
    repository has no releases yet (``latest`` only).
    """
    if item.strategy == STRATEGY_LATEST:
        if not item.release_tag_include:
            release = fetch_latest_release(item.repo, token)
            if release is None:
                return None
            return _latest_detection(release)

        selected = _select_tag_included(item, fetch_releases(item.repo, token))
        tag = selected["tag_name"]
        release = fetch_release_by_tag(item.repo, tag, token)
        if release is None:
            raise RuntimeError(f"Release tag '{tag}' not found in {item.repo}")
        return _latest_detection(release)

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
