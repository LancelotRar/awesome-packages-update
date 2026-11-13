"""Detection strategies: decide whether a watch item has an update.

``latest`` watches the newest stable release and marks it by tag plus a
fingerprint of the assets the item's filter selects: a new version or a
replaced/added asset notifies, a metadata-only edit to the same release
does not. Drafts and prereleases never qualify. When a tag filter is
configured, "newest" means the newest stable release whose tag matches any
pattern. The chosen tag is then fetched in full so its asset list is
complete. ``tag`` fingerprints a named release's matched assets so in-place
asset replacement is detected even though the release itself never changes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass

from github_api import (
    fetch_release_by_tag,
    fetch_releases,
    get_matching_assets,
)
from timestamps import to_beijing_iso
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


def _latest_detection(item: WatchItem, release: dict) -> Detection:
    """Wrap a release in the detection shape ``latest`` has always used.

    The marker is the tag plus a fingerprint of the *matched* assets, so a
    new version always differs while a metadata-only edit to the same
    release does not. Replaced or added assets move the fingerprint and
    therefore still notify.
    """
    matched = get_matching_assets(
        release, item.asset_include, item.asset_exclude, log_result=False,
    )
    tag = str(release.get("tag_name", ""))
    return Detection(
        release=release,
        marker=f"{tag}@{asset_fingerprint(matched)}",
        asset_count=len(release.get("assets", [])),
    )


def _require_release(release: dict | None, tag: str, item: WatchItem) -> dict:
    """Return *release*, or fail when *tag* is absent from *item*'s repo."""
    if release is None:
        raise RuntimeError(f"Release tag '{tag}' not found in {item.repo}")
    return release


def _select_release(item: WatchItem, releases: list[dict]) -> dict:
    """Return the newest release eligible for this watch item.

    Drafts and prereleases never qualify, so ``latest`` always tracks the
    newest stable release — not whatever the feed lists first.
    ``release_tag_include``, when set, further restricts the tag with
    case-insensitive glob patterns (OR'd).

    Raises when nothing qualifies, mirroring the ``tag`` strategy's
    "tag not found" failure: a stale filter must surface, not go quiet.
    """
    patterns = item.release_tag_include

    def _matches(tag: str) -> bool:
        if not patterns:
            return True
        lowered = tag.lower()
        return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)

    candidates = [
        r for r in releases
        if isinstance(r, dict) and r.get("tag_name")
        and not r.get("draft") and not r.get("prerelease")
        and _matches(r["tag_name"])
    ]
    if not candidates:
        if patterns:
            raise RuntimeError(
                f"No stable release in {item.repo} matched: {', '.join(patterns)}"
            )
        raise RuntimeError(f"No stable release in {item.repo}")
    return max(candidates, key=lambda r: r.get("published_at") or "")


def detect(item: WatchItem, token: str = "") -> Detection | None:
    """Return the current detection for *item*, or ``None`` when the
    repository has no releases yet (``latest`` only).
    """
    if item.strategy == STRATEGY_LATEST:
        releases = fetch_releases(item.repo, token)
        if not releases:
            return None

        selected = _select_release(item, releases)
        tag = selected["tag_name"]
        release = _require_release(
            fetch_release_by_tag(item.repo, tag, token), tag, item
        )
        return _latest_detection(item, release)

    if item.strategy == STRATEGY_TAG:
        release = _require_release(
            fetch_release_by_tag(item.repo, item.release_tag, token),
            item.release_tag,
            item,
        )

        assets = get_matching_assets(release, item.asset_include,
                                     item.asset_exclude, log_result=False)
        if not assets:
            raise RuntimeError(
                "No assets matched ASSET_INCLUDE/ASSET_EXCLUDE — "
                "refusing to persist an empty fingerprint"
            )

        # A rolling release keeps its original published_at; show the
        # newest matched asset date instead (in Beijing time, like every
        # other timestamp this project surfaces).
        display = dict(release)
        display["published_at"] = to_beijing_iso(
            max((a.get("updated_at", "") for a in assets), default="")
            or release.get("published_at", "")
        )
        return Detection(
            release=display,
            marker=asset_fingerprint(assets),
            asset_count=len(assets),
        )

    raise RuntimeError(f"Unknown strategy: {item.strategy}")
