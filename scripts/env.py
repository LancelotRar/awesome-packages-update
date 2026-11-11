"""Environment-variable helpers shared by the runner and the audit."""

from __future__ import annotations

import os


def required(name: str) -> str:
    """Return *name*'s value, or raise when it is missing or empty."""
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def chat_ids(raw: str, var: str) -> list[int]:
    """Parse a comma-separated chat-id list, raising on an empty/bad value.

    Callers decide whether a bad value is fatal (the runner) or merely
    disables the optional notification (the audit).
    """
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise RuntimeError(f"{var} is empty after splitting")
    try:
        return [int(part) for part in parts]
    except ValueError as e:
        raise RuntimeError(f"{var} contains a non-numeric value: {e}") from e
