#!/usr/bin/env python3
"""Report watch items whose asset filter no longer matches anything.

Upstream renames its assets, and a filter that matches nothing used to
degrade into a text-only notification. This audit resolves every enabled
item the same way the runner does and fails loudly on a zero match.

Environment variables
---------------------
WATCHLIST    Path to projects.yaml (default: repository root)
GITHUB_TOKEN GitHub API token (optional, raises rate limits)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from detector import detect
from github_api import get_matching_assets
from watchlist import load_watchlist

_HERE = Path(__file__).resolve().parent


def main() -> int:
    watchlist = Path(os.environ.get("WATCHLIST") or _HERE.parent / "projects.yaml")
    token = os.environ.get("GITHUB_TOKEN", "")

    items = [item for item in load_watchlist(watchlist) if item.enabled]
    problems = 0

    for item in items:
        try:
            detection = detect(item, token)
        except Exception as e:  # one broken item must not hide the others
            print(f"::error::{item.id}: detection failed: {e}", flush=True)
            problems += 1
            continue

        if detection is None:
            print(f"::warning::{item.id}: no releases yet", flush=True)
            continue

        assets = detection.release.get("assets") or []
        matched = get_matching_assets(detection.release, item.asset_include,
                                      item.asset_exclude)
        tag = detection.release.get("tag_name", "?")
        if not assets:
            print(f"::warning::{item.id}: {tag} has no assets yet", flush=True)
            continue
        if item.asset_include.strip() and not matched:
            print(f"::error::{item.id}: {tag} — 0 assets matched "
                  f"{item.asset_include!r}", flush=True)
            problems += 1
        else:
            print(f"{item.id}: {tag} — {len(matched)} asset(s)", flush=True)

    print(f"Audited {len(items)} item(s), {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
