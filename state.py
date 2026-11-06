"""Persistent per-watch-item detection state (``data/state.json``)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

State = dict[str, dict[str, Any]]

_EMPTY = {
    "marker": "",
    "last_checked": "",
    "failures": 0,
    "alerted": False,
    "last_error": "",
}


def _entry(state: State, project_id: str) -> dict[str, Any]:
    return state.setdefault(project_id, dict(_EMPTY))


def load_state(path: str | Path) -> State:
    """Read *path*, returning ``{}`` when it does not exist yet.

    A present-but-corrupt file is an error rather than an empty state:
    silently resetting markers would re-send already-delivered releases.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupt state file {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"Corrupt state file {path}: expected a JSON object")
    for project_id, entry in data.items():
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"Corrupt state file {path}: entry '{project_id}' must be an object"
            )
    return data


def save_state(path: str | Path, state: State) -> None:
    """Write *state* atomically, keeping keys in a stable order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def marker_of(state: State, project_id: str) -> str:
    return str(_entry(state, project_id).get("marker", ""))


def mark_healthy(state: State, project_id: str, when: str) -> None:
    """Record a run that detected no change (or nothing to detect)."""
    entry = _entry(state, project_id)
    entry["last_checked"] = when
    entry["failures"] = 0
    entry["alerted"] = False
    entry["last_error"] = ""


def record_delivery(state: State, project_id: str, marker: str, when: str) -> None:
    """Record a successful delivery and advance the marker."""
    entry = _entry(state, project_id)
    entry["marker"] = marker
    entry["last_checked"] = when
    entry["failures"] = 0
    entry["alerted"] = False
    entry["last_error"] = ""


def record_failure(state: State, project_id: str, when: str, error: str) -> None:
    entry = _entry(state, project_id)
    entry["last_checked"] = when
    entry["failures"] = int(entry.get("failures", 0)) + 1
    entry["last_error"] = error[:500]


def needs_alert(state: State, project_id: str, threshold: int) -> bool:
    entry = _entry(state, project_id)
    return int(entry.get("failures", 0)) >= threshold and not entry.get("alerted", False)


def mark_alerted(state: State, project_id: str) -> None:
    _entry(state, project_id)["alerted"] = True
