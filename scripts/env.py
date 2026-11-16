"""Environment-variable helpers shared by the runner and the audit."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notifier import TelegramConfig


class ConfigError(RuntimeError):
    """Missing or malformed configuration."""


def required(name: str) -> str:
    """Return *name*'s value, or raise when it is missing or empty."""
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(f"Missing required env var: {name}")
    return value


def chat_ids(raw: str, var: str) -> list[int]:
    """Parse a comma-separated chat-id list, raising on an empty/bad value.

    Callers decide whether a bad value is fatal (the runner) or merely
    disables the optional notification (the audit).
    """
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise ConfigError(f"{var} is empty after splitting")
    try:
        return [int(part) for part in parts]
    except ValueError as e:
        raise ConfigError(f"{var} contains a non-numeric value: {e}") from e


def telegram_config(targets: list[int]) -> TelegramConfig:
    """Build Telegram connection credentials for *targets*.

    Telethon is imported lazily so that parsing chat ids, linting, and
    running the audit without credentials never require it.
    """
    from notifier import TelegramConfig

    raw_api_id = required("TG_API_ID")
    try:
        api_id = int(raw_api_id)
    except ValueError as e:
        raise ConfigError(f"TG_API_ID must be numeric: {e}") from e
    return TelegramConfig(
        bot_token=required("TG_BOT_TOKEN"),
        chat_ids=targets,
        api_id=api_id,
        api_hash=required("TG_API_HASH"),
    )
