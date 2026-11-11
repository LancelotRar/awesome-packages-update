"""Unit tests for ``env`` — shared environment parsing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env import chat_ids, required


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
