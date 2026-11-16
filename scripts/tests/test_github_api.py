"""Unit tests for ``github_api`` — retry / rate-limit hardening (#5).

``github_api`` lives in the parent scripts directory (same convention as
``test_notifier.py``), so make it importable first.
"""

import io
import sys
import time
import urllib.error
from pathlib import Path
from typing import Self

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import github_api


class _Resp:
    """Minimal context-managed HTTP response stub."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _http_error(code: int, headers: dict[str, str] | None = None,
                reason: str = "error") -> urllib.error.HTTPError:
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    return urllib.error.HTTPError(
        "https://api.github.com/repos/foo/bar/releases?per_page=1",
        code, reason, hdrs, io.BytesIO(b""),
    )


def _install(monkeypatch, *effects):
    """Patch urlopen/sleep with *effects* replayed in order.

    Returns (calls, sleeps) recorders: ``calls`` grows once per urlopen,
    ``sleeps`` collects every value passed to ``time.sleep``.
    """
    calls: list[int] = []
    sleeps: list[float] = []
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        i = state["n"]
        state["n"] += 1
        calls.append(i)
        effect = effects[i] if i < len(effects) else effects[-1]
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(github_api.time, "sleep", sleeps.append)
    return calls, sleeps


# --- new behavior: rate-limit responses are retried ---------------------

def test_get_json_retries_429_when_retry_after_present(monkeypatch):
    calls, sleeps = _install(
        monkeypatch,
        _http_error(429, {"Retry-After": "7"}, reason="rate limited"),
        _Resp(b'{"ok": 1}'),
    )
    assert github_api._get_json("https://api.github.com/x") == {"ok": 1}
    assert len(calls) == 2
    assert sleeps == [7]


def test_get_json_caps_rate_limit_sleep_at_two_minutes(monkeypatch):
    _, sleeps = _install(
        monkeypatch,
        _http_error(429, {"Retry-After": "9999"}, reason="rate limited"),
        _Resp(b"[]"),
    )
    assert github_api._get_json("https://api.github.com/x") == []
    assert sleeps == [120]


def test_get_json_waits_for_rate_limit_reset_on_403(monkeypatch):
    reset = str(int(time.time()) + 300)
    calls, sleeps = _install(
        monkeypatch,
        _http_error(403, {"X-RateLimit-Remaining": "0",
                          "X-RateLimit-Reset": reset},
                    reason="API rate limit exceeded"),
        _Resp(b"[]"),
    )
    github_api._get_json("https://api.github.com/x")
    # ~300s until reset, capped at the 120s ceiling.
    assert sleeps == [120]
    assert len(calls) == 2


def test_get_json_429_without_header_falls_back_to_backoff(monkeypatch):
    _, sleeps = _install(
        monkeypatch,
        _http_error(429, reason="rate limited"),
        _Resp(b"[]"),
    )
    github_api._get_json("https://api.github.com/x")
    assert sleeps == [2]  # 2**attempt fallback


def test_get_json_raises_after_429_retries_exhausted(monkeypatch):
    calls, sleeps = _install(
        monkeypatch,
        *[_http_error(429, {"Retry-After": "1"}, reason="rate limited")] * 3,
    )
    with pytest.raises(RuntimeError, match="GitHub API HTTP 429"):
        github_api._get_json("https://api.github.com/x")
    assert len(calls) == 3
    assert sleeps == [1, 1]


# --- preserved behavior: guards on existing paths -----------------------

def test_get_json_permission_403_fails_without_retry(monkeypatch):
    """A plain 403 (no rate-limit headers) is permanent — fail fast."""
    calls, sleeps = _install(monkeypatch, _http_error(403, reason="Forbidden"))
    with pytest.raises(RuntimeError, match="GitHub API HTTP 403"):
        github_api._get_json("https://api.github.com/x")
    assert len(calls) == 1
    assert sleeps == []


def test_get_json_still_retries_server_errors(monkeypatch):
    _, sleeps = _install(monkeypatch, _http_error(500), _Resp(b'{"n": 1}'))
    assert github_api._get_json("https://api.github.com/x") == {"n": 1}
    assert sleeps == [2]


def test_get_json_retries_connection_errors(monkeypatch):
    _, sleeps = _install(monkeypatch, OSError("boom"), _Resp(b"[]"))
    assert github_api._get_json("https://api.github.com/x") == []
    assert sleeps == [2]


def test_get_json_invalid_json_raises(monkeypatch):
    _install(monkeypatch, _Resp(b"not json"))
    with pytest.raises(RuntimeError, match="Failed to fetch data"):
        github_api._get_json("https://api.github.com/x")


# --- release list --------------------------------------------------------

def test_fetch_releases_requests_one_full_page(monkeypatch):
    urls: list[str] = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return _Resp(b"[]")

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_urlopen)
    assert github_api.fetch_releases("foo/bar") == []
    assert urls == ["https://api.github.com/repos/foo/bar/releases?per_page=100"]


def test_fetch_releases_returns_json_array_as_is(monkeypatch):
    payload = b'[{"tag_name": "desktop-v1"}, {"tag_name": "cli-v1"}]'
    _install(monkeypatch, _Resp(payload))
    assert github_api.fetch_releases("foo/bar") == [
        {"tag_name": "desktop-v1"},
        {"tag_name": "cli-v1"},
    ]


def test_fetch_releases_rejects_non_array_payload(monkeypatch):
    _install(monkeypatch, _Resp(b'{"message": "Not Found"}'))
    with pytest.raises(RuntimeError, match="expected a JSON array"):
        github_api.fetch_releases("foo/bar")
