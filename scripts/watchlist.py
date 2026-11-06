"""Load and validate the watch list (``projects.yaml``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

STRATEGY_LATEST = "latest"
STRATEGY_TAG = "tag"
STRATEGIES = (STRATEGY_LATEST, STRATEGY_TAG)

_PROJECT_KEYS = {
    "id",
    "repo",
    "strategy",
    "release_tag",
    "release_tag_include",
    "asset_include",
    "asset_exclude",
    "notify",
}
_NOTIFY_KEYS = {
    "title",
    "description",
    "group_url",
    "channel_url",
    "project_url",
    "tag",
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class NotifyCopy:
    """Channel-facing wording for one watch item."""

    title: str = ""
    description: str = ""
    group_url: str = ""
    channel_url: str = ""
    project_url: str = ""
    tag: str = ""


@dataclass(frozen=True)
class WatchItem:
    """One software project tracked for new builds."""

    id: str
    repo: str
    strategy: str
    notify: NotifyCopy = field(default_factory=NotifyCopy)
    release_tag: str = ""
    release_tag_include: tuple[str, ...] = ()
    asset_include: str = ""
    asset_exclude: str = ""


def _require_str(entry: dict, key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{where}: '{key}' must be a non-empty string")
    return value.strip()


def _optional_str(entry: dict, key: str, where: str) -> str:
    value = entry.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RuntimeError(f"{where}: '{key}' must be a string")
    return value.strip()


def _optional_str_list(entry: dict, key: str, where: str) -> tuple[str, ...]:
    """Parse an optional list of non-empty strings into a tuple."""
    value = entry.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeError(f"{where}: '{key}' must be a list of non-empty strings")
    patterns = tuple(p.strip() if isinstance(p, str) else "" for p in value)
    if not all(patterns):
        raise RuntimeError(f"{where}: '{key}' must be a list of non-empty strings")
    return patterns


def _parse_item(raw: object, index: int, seen: set[str]) -> WatchItem:
    where = f"projects[{index}]"
    if not isinstance(raw, dict):
        raise RuntimeError(f"{where}: each project must be a mapping")

    unknown = set(raw) - _PROJECT_KEYS
    if unknown:
        raise RuntimeError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    project_id = _require_str(raw, "id", where)
    if not _ID_RE.match(project_id):
        raise RuntimeError(
            f"{where}: id '{project_id}' must be lowercase letters, digits or '-'"
        )
    if project_id in seen:
        raise RuntimeError(f"{where}: duplicate id '{project_id}'")
    seen.add(project_id)

    repo = _require_str(raw, "repo", where)
    if repo.count("/") != 1 or " " in repo:
        raise RuntimeError(f"{where}: repo '{repo}' must look like 'owner/name'")

    strategy = _require_str(raw, "strategy", where)
    if strategy not in STRATEGIES:
        raise RuntimeError(
            f"{where}: strategy '{strategy}' must be one of {', '.join(STRATEGIES)}"
        )

    release_tag = _optional_str(raw, "release_tag", where)
    if strategy == STRATEGY_TAG and not release_tag:
        raise RuntimeError(f"{where}: strategy 'tag' requires 'release_tag'")
    if strategy == STRATEGY_LATEST and release_tag:
        raise RuntimeError(f"{where}: 'release_tag' is only valid with strategy 'tag'")

    release_tag_include = _optional_str_list(raw, "release_tag_include", where)
    if strategy == STRATEGY_TAG and release_tag_include:
        raise RuntimeError(
            f"{where}: 'release_tag_include' is only valid with strategy 'latest'"
        )

    notify_raw = raw.get("notify", {})
    if not isinstance(notify_raw, dict):
        raise RuntimeError(f"{where}: 'notify' must be a mapping")
    unknown_notify = set(notify_raw) - _NOTIFY_KEYS
    if unknown_notify:
        raise RuntimeError(
            f"{where}: unknown notify key(s): {', '.join(sorted(unknown_notify))}"
        )
    notify = NotifyCopy(
        **{key: _optional_str(notify_raw, key, f"{where}.notify")
           for key in _NOTIFY_KEYS}
    )

    return WatchItem(
        id=project_id,
        repo=repo,
        strategy=strategy,
        notify=notify,
        release_tag=release_tag,
        release_tag_include=release_tag_include,
        asset_include=_optional_str(raw, "asset_include", where),
        asset_exclude=_optional_str(raw, "asset_exclude", where),
    )


def load_watchlist(path: str | Path) -> list[WatchItem]:
    """Read *path* and return its watch items, preserving file order."""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Watch list not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict) or "projects" not in data:
        raise RuntimeError(f"{path}: expected a top-level 'projects' list")
    unknown = set(data) - {"projects"}
    if unknown:
        raise RuntimeError(f"{path}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    raw_items = data["projects"]
    if not isinstance(raw_items, list) or not raw_items:
        raise RuntimeError(f"{path}: 'projects' must be a non-empty list")

    seen: set[str] = set()
    return [_parse_item(raw, i, seen) for i, raw in enumerate(raw_items)]
