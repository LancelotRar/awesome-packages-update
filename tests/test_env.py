"""Unit tests for ``env`` — shared environment parsing."""

import pytest

from env import ConfigError, chat_ids, required, telegram_config


def test_required_returns_the_value(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "value")

    assert required("SOME_VAR") == "value"


def test_required_rejects_missing_or_empty(monkeypatch):
    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var: SOME_VAR"):
        required("SOME_VAR")

    monkeypatch.setenv("SOME_VAR", "")
    with pytest.raises(RuntimeError, match="SOME_VAR"):
        required("SOME_VAR")


def test_chat_ids_parses_and_strips():
    assert chat_ids("111, 222 ,333", "TG_CHAT_ID") == [111, 222, 333]


def test_chat_ids_rejects_empty_input():
    with pytest.raises(RuntimeError, match="empty after splitting"):
        chat_ids(" , ", "TG_CHAT_ID")


def test_chat_ids_rejects_non_numeric():
    with pytest.raises(RuntimeError, match="non-numeric"):
        chat_ids("111,abc", "TG_CHAT_ID")

def test_telegram_config_reads_connection_env(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TG_API_ID", "42")
    monkeypatch.setenv("TG_API_HASH", "HASH")

    cfg = telegram_config([111, 222])

    assert cfg.bot_token == "TOK"
    assert cfg.api_id == 42
    assert cfg.api_hash == "HASH"
    assert cfg.chat_ids == [111, 222]

def test_telegram_config_rejects_missing_credentials(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TG_API_ID", "42")
    monkeypatch.setenv("TG_API_HASH", "HASH")

    with pytest.raises(ConfigError, match="TG_BOT_TOKEN") as excinfo:
        telegram_config([111])
    assert excinfo.value.variable == "TG_BOT_TOKEN"

def test_telegram_config_rejects_non_numeric_api_id(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TG_API_ID", "abc")
    monkeypatch.setenv("TG_API_HASH", "HASH")

    with pytest.raises(ConfigError, match="TG_API_ID must be numeric"):
        telegram_config([111])
