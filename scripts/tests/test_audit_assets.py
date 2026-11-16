"""Unit tests for ``audit_assets`` — zero-match detection without network."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audit_assets
import scan as scan_module
from detector import Detection
from watchlist import NotifyCopy, WatchItem


_TG_VALUES = {
    "TG_BOT_TOKEN": "TOK",
    "TG_API_ID": "1",
    "TG_API_HASH": "H",
    "TG_CHAT_ID_ADMIN": "999",
}


def _item(pid: str, *, enabled: bool = True, include: str = ".apk") -> WatchItem:
    return WatchItem(id=pid, repo=f"o/{pid}", strategy="latest",
                     notify=NotifyCopy(title=pid), asset_include=include,
                     enabled=enabled)


def _detection(tag: str = "v1", *, assets: int = 1) -> Detection:
    return Detection(release={"tag_name": tag,
                              "assets": [{"name": f"a{i}"} for i in range(assets)]},
                     marker="m", asset_count=assets)


def _patch(monkeypatch, items, *, detected=None, matched=None, error=None):
    monkeypatch.setenv("WATCHLIST", "unused.yaml")
    # A developer shell may export real Telegram credentials; never let a
    # unit test reach the network.
    for name in _TG_VALUES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audit_assets, "load_watchlist", lambda path: items)

    def fake_detect(item, token):
        if error is not None:
            raise error
        return detected

    monkeypatch.setattr(scan_module, "detect", fake_detect)
    monkeypatch.setattr(scan_module, "get_matching_assets",
                        lambda release, include, exclude: matched or [])


def test_all_items_matching_succeeds(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(), matched=[{"name": "x"}])

    assert audit_assets.main() == 0


def test_zero_match_fails(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(assets=2), matched=[])

    assert audit_assets.main() == 1


def test_release_without_assets_fails_when_a_filter_is_set(monkeypatch):
    """0 assets + include filter is a failure, same policy as the runner."""
    _patch(monkeypatch, [_item("a")], detected=_detection(assets=0), matched=[])

    assert audit_assets.main() == 1


def test_release_without_assets_is_a_warning_without_a_filter(monkeypatch):
    _patch(monkeypatch, [_item("a", include="")],
           detected=_detection(assets=0), matched=[])

    assert audit_assets.main() == 0


def test_detection_error_fails(monkeypatch):
    _patch(monkeypatch, [_item("a")], error=RuntimeError("boom"))

    assert audit_assets.main() == 1


def test_disabled_items_are_skipped(monkeypatch):
    _patch(monkeypatch, [_item("a", enabled=False)], matched=[])

    assert audit_assets.main() == 0


# ---------------------------------------------------------------------------
# Telegram failure report
# ---------------------------------------------------------------------------


def _telegram_env(monkeypatch, *, present: bool) -> None:
    for name, value in _TG_VALUES.items():
        if present:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)


def _capture_send(monkeypatch, ok: bool = True) -> list[tuple[str, list[int]]]:
    sent: list[tuple[list[str], list[int]]] = []

    def fake_send(texts, chat_ids):
        sent.append((texts, chat_ids))
        return ok

    monkeypatch.setattr(audit_assets, "_send", fake_send)
    return sent


def test_zero_match_pushes_report_naming_the_item(monkeypatch):
    """A failing audit sends the admin chat the item and its filter."""
    _patch(monkeypatch, [_item("a", include=".apk+arm64")],
           detected=_detection(tag="v1.2.3", assets=2), matched=[])
    _telegram_env(monkeypatch, present=True)
    sent = _capture_send(monkeypatch)

    assert audit_assets.main() == 1
    assert len(sent) == 1
    texts, chat_ids = sent[0]
    assert len(texts) == 1
    text = texts[0]
    assert "a" in text
    assert "v1.2.3" in text
    assert ".apk+arm64" in text
    assert chat_ids == [999]


def test_multiple_problems_send_one_aggregated_message(monkeypatch):
    """Three broken items -> a single message listing all of them."""
    _patch(monkeypatch, [
        _item("a"), _item("b"), _item("c"),
    ], detected=_detection(assets=2), matched=[])
    _telegram_env(monkeypatch, present=True)
    sent = _capture_send(monkeypatch)

    assert audit_assets.main() == 1
    assert len(sent) == 1
    texts, _chat_ids = sent[0]
    assert len(texts) == 1
    text = texts[0]
    assert "3 项需要处理" in text
    for pid in ("a", "b", "c"):
        assert f"<b>{pid}</b>" in text


def test_detection_error_pushes_report(monkeypatch):
    _patch(monkeypatch, [_item("a")], error=RuntimeError("boom"))
    _telegram_env(monkeypatch, present=True)
    sent = _capture_send(monkeypatch)

    assert audit_assets.main() == 1
    assert len(sent) == 1
    assert "boom" in sent[0][0][0]


def test_no_report_when_telegram_is_not_configured(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(assets=2), matched=[])
    _telegram_env(monkeypatch, present=False)
    sent = _capture_send(monkeypatch)

    assert audit_assets.main() == 1
    assert sent == []


def test_success_sends_no_report(monkeypatch):
    _patch(monkeypatch, [_item("a")], detected=_detection(), matched=[{"name": "x"}])
    _telegram_env(monkeypatch, present=True)
    sent = _capture_send(monkeypatch)

    assert audit_assets.main() == 0
    assert sent == []


def test_report_is_capped_and_mentions_the_remainder(monkeypatch):
    problems = [(f"p{i}", "0 assets matched") for i in range(30)]

    text = audit_assets._summary(problems, total=35)

    assert text.count("• ") == audit_assets._MAX_REPORTED
    assert "另有 5 项" in text


def test_report_escapes_html_and_clips_long_details():
    long_detail = "x" * 500

    text = audit_assets._summary(
        [("a<b>", '<script> & "quoted"'), ("c", long_detail)], total=2,
    )

    assert "a&lt;b&gt;" in text
    assert "<script>" not in text
    assert "…" in text
    assert max(len(line) for line in text.splitlines()) < 220


def test_send_uses_the_environment_credentials(monkeypatch):
    """``_send`` reads the TG_* env vars and delivers through the notifier."""
    _telegram_env(monkeypatch, present=True)
    client_cls = MagicMock()
    client = client_cls.return_value
    client.start = AsyncMock(return_value=None)
    client.send_message = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)

    with patch("notifier.TelegramClient", client_cls):
        assert audit_assets._send(["hi"], [999]) is True

    assert client.send_message.await_args.args == (999, "hi")


def test_deliver_reuses_one_client_for_all_messages():
    """Several messages share one session, closed only after the last send."""
    from notifier import TelegramConfig

    client = MagicMock()
    client.disconnect = AsyncMock(return_value=None)
    opened: list = []
    sent: list[tuple[str, object]] = []

    async def fake_open(cfg):
        opened.append(cfg)
        return client

    async def fake_send(text, cfg, client=None):
        sent.append((text, client))
        return True

    cfg = TelegramConfig(bot_token="T", chat_ids=[1], api_id=1, api_hash="H")
    with patch("notifier.open_client", fake_open), \
            patch("notifier.send_text", fake_send):
        ok = asyncio.run(audit_assets._deliver(cfg, ["one", "two"]))

    assert ok is True
    assert len(opened) == 1
    assert [text for text, _ in sent] == ["one", "two"]
    assert all(session is client for _, session in sent)
    client.disconnect.assert_awaited_once()

