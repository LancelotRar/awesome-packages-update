"""Unit tests for ``notifier`` — message builder and text-notification delivery."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ``notifier`` imports its sibling ``github_api`` at module level, so the
# parent scripts directory (not just this ``tests`` dir) must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import errors as tg_errors

from notifier import TelegramConfig, _build_message, notify


RELEASE_NO_ASSETS = {
    "name": "v9.9.9",
    "tag_name": "v9.9.9",
    "published_at": "2026-09-18T00:00:00Z",
    "html_url": "https://github.com/foo/bar/releases/tag/v9.9.9",
    "assets": [],
}


def _text_cfg() -> TelegramConfig:
    return TelegramConfig(
        bot_token="TOK", chat_ids=[111, 222], api_id=1, api_hash="H",
    )


def _patched_client(send_message_side_effect):
    """Patch ``notifier.TelegramClient`` with an in-memory fake client."""
    client_cls = MagicMock()
    inst = client_cls.return_value
    inst.start = AsyncMock(return_value=None)
    inst.send_message = AsyncMock(side_effect=send_message_side_effect)
    inst.disconnect = AsyncMock(return_value=None)
    return patch("notifier.TelegramClient", client_cls)


def _notify(release: dict) -> bool:
    return asyncio.run(notify(release, _text_cfg(), "", "", ""))


# ---------------------------------------------------------------------------
# Album-path fixtures: 2 assets, fake stream/upload, all-or-nothing contract.
# ---------------------------------------------------------------------------

RELEASE_2_ASSETS = {
    "name": "v9.9.9",
    "tag_name": "v9.9.9",
    "published_at": "2026-09-18T00:00:00Z",
    "html_url": "https://github.com/foo/bar/releases/tag/v9.9.9",
    "assets": [
        {"name": "a.apk", "size": 100,
         "browser_download_url": "https://example.com/a.apk"},
        {"name": "b.apk", "size": 200,
         "browser_download_url": "https://example.com/b.apk"},
    ],
}


def _album_client(upload_side_effect):
    """Return (patch, send_file_mock) with a fake client for album tests.

    ``upload_side_effect``: async callable ``(data, file_name=…,
    file_size=…, progress_callback=…)`` — raise to simulate a failed
    upload of a specific asset.
    """
    client_cls = MagicMock()
    inst = client_cls.return_value
    inst.start = AsyncMock(return_value=None)
    inst.disconnect = AsyncMock(return_value=None)
    inst.upload_file = upload_side_effect
    inst.send_file = AsyncMock(return_value=None)

    fake_resp = MagicMock()
    fake_resp.read = MagicMock(return_value=b"APK-BYTES")

    def fake_stream(asset, token):
        return fake_resp

    album_patch = patch("notifier.TelegramClient", client_cls)
    stream_patch = patch("notifier.stream_asset", fake_stream)
    return album_patch, stream_patch, inst


def _upload(uploaded_names: list[str]):
    async def _impl(data, file_name=None, file_size=None, progress_callback=None):
        if file_name not in uploaded_names:
            raise RuntimeError("upload failed")
        return object()

    return _impl


def test_full_message():
    """Build the complete message with every field populated."""
    # Given a release with all optional fields:
    message = _build_message(
        asset_name="awesome-actions-v1.2.3.zip",
        pub_date="2026-08-29",
        html_url="https://github.com/foo/bar/releases/tag/v1.2.3",
        title="Awesome Actions v1.2.3",
        group_url="https://t.me/awesome_chat",
        channel_url="https://t.me/awesome_channel",
        project_url="https://github.com/foo/bar",
        description="Big release with fixes",
    )

    # Then the exact HTML message (including label text) is produced.
    assert message == (
        "🚀 <b>Awesome Actions v1.2.3</b>\n"
        "📝 <b>Big release with fixes</b>\n"
        '📢 <b><a href="https://t.me/awesome_chat">TG 群组</a></b>\n'
        '📣 <b><a href="https://t.me/awesome_channel">TG 频道</a></b>\n'
        '🌐 <b><a href="https://github.com/foo/bar">项目地址</a></b>\n'
        "🌀 <b>版本：</b><code>awesome-actions-v1.2.3.zip</code>\n"
        "🍾 <b>发布时间：2026-08-29</b>\n"
        '🔗 <b><a href="https://github.com/foo/bar/releases/tag/v1.2.3">'
        "查看完整 Release 日志</a></b>"
    )


def test_omits_optional_lines_when_empty():
    """Drop the title, description, and group lines when they are empty."""
    # Given empty optional fields:
    message = _build_message(
        asset_name="awesome-actions-v1.2.3.zip",
        pub_date="2026-08-29",
        html_url="https://github.com/foo/bar/releases/tag/v1.2.3",
        title="",
        group_url="",
        description="",
    )

    # Then only the three mandatory lines remain.
    assert message == (
        "🌀 <b>版本：</b><code>awesome-actions-v1.2.3.zip</code>\n"
        "🍾 <b>发布时间：2026-08-29</b>\n"
        '🔗 <b><a href="https://github.com/foo/bar/releases/tag/v1.2.3">'
        "查看完整 Release 日志</a></b>"
    )


def test_tag_appended_as_last_line():
    """A non-empty tag is appended as the final plain-text line."""
    # Given a message built with a project tag:
    message = _build_message(
        asset_name="awesome-actions-v1.2.3.zip",
        pub_date="2026-08-29",
        html_url="https://github.com/foo/bar/releases/tag/v1.2.3",
        title="",
        group_url="",
        tag="#bettbox",
    )

    # Then the hashtag is the very last line, unstyled (searchable).
    assert message.endswith("\n#bettbox")


def test_omits_tag_line_when_empty():
    """An empty tag adds no trailing line — message unchanged."""
    # Given no tag configured:
    message = _build_message(
        asset_name="awesome-actions-v1.2.3.zip",
        pub_date="2026-08-29",
        html_url="https://github.com/foo/bar/releases/tag/v1.2.3",
        title="",
        group_url="",
    )

    # Then the message does not end with a newline.
    assert not message.endswith("\n")


def test_escapes_dynamic_text_fields():
    """Escape the dynamic asset name and description fields."""
    # Given dynamic text containing HTML metacharacters:
    message = _build_message(
        asset_name='app <v1.0> & "beta"',
        pub_date="2026-08-29",
        html_url="https://example.com/releases/1",
        title="",
        group_url="",
        description='<b>notes</b> & "quoted"',
    )

    # Then the asset name and description are HTML-escaped in place.
    assert message == (
        "📝 <b>&lt;b&gt;notes&lt;/b&gt; &amp; &quot;quoted&quot;</b>\n"
        "🌀 <b>版本：</b><code>app &lt;v1.0&gt; &amp; &quot;beta&quot;</code>\n"
        "🍾 <b>发布时间：2026-08-29</b>\n"
        '🔗 <b><a href="https://example.com/releases/1">'
        "查看完整 Release 日志</a></b>"
    )


# ---------------------------------------------------------------------------
# notify() — text-only path (no matching assets)
# Contract: True only when at least one chat was actually notified.
# ---------------------------------------------------------------------------


def test_text_notification_all_success_returns_true():
    """Every chat receives the text message -> notify() reports success."""
    with _patched_client(None):
        ok = _notify(RELEASE_NO_ASSETS)

    assert ok is True


def test_text_notification_includes_tag():
    """cfg.notify_tag reaches the sent text as its last line."""
    # Given a config carrying a project tag:
    cfg = _text_cfg()
    cfg.notify_tag = "#bettbox"

    # When the text notification is sent:
    with _patched_client(None) as client_cls:
        ok = asyncio.run(notify(RELEASE_NO_ASSETS, cfg, "", "", ""))

    # Then the delivered message ends with that hashtag.
    assert ok is True
    sent_text = client_cls.return_value.send_message.await_args.args[1]
    assert sent_text.endswith("\n#bettbox")


def test_text_notification_disables_link_preview():
    """send_message must explicitly disable the web link preview."""
    # Given the standard text-notification path:
    with _patched_client(None) as client_cls:
        ok = asyncio.run(notify(RELEASE_NO_ASSETS, _text_cfg(), "", "", ""))

    # Then link_preview=False is passed so URLs stay unexpanded.
    assert ok is True
    kwargs = client_cls.return_value.send_message.await_args.kwargs
    assert kwargs.get("link_preview") is False


def test_text_notification_all_sends_fail_returns_false():
    """All sends raise -> notify() must report failure (retry on next run)."""
    with _patched_client(Exception("send failed")):
        ok = _notify(RELEASE_NO_ASSETS)

    assert ok is False


def test_text_notification_floodwait_zero_sent_returns_false():
    """FloodWait on first chat -> zero chats notified -> failure, not success."""
    def _flood(cid, *a, **k):
        raise tg_errors.FloodWaitError(request=None, capture=42)

    with _patched_client(_flood):
        ok = _notify(RELEASE_NO_ASSETS)

    assert ok is False


def test_text_notification_partial_failure_returns_false():
    """One of two chats fails -> delivery incomplete -> failure."""
    def _flaky(cid, *a, **k):
        if cid == 222:
            raise RuntimeError("send failed")
        return None

    with _patched_client(_flaky):
        ok = _notify(RELEASE_NO_ASSETS)

    assert ok is False


# ---------------------------------------------------------------------------
# notify() — album path (assets present)
# Contract: True only when EVERY asset uploaded AND every chat received.
# ---------------------------------------------------------------------------


def test_album_partial_upload_failure_returns_false_and_sends_nothing():
    """One of two uploads fails -> no incomplete album, notify() = False."""
    album_patch, stream_patch, inst = _album_client(_upload(["a.apk"]))
    with album_patch, stream_patch:
        ok = _notify(RELEASE_2_ASSETS)

    # The incomplete album must NOT be sent; failure must be reported
    # so the caller skips persistence and retries next run.
    inst.send_file.assert_not_called()
    assert ok is False


def test_album_all_upload_success_and_send_succeeds_returns_true():
    """Every asset uploads and album sends to every chat -> notify() = True."""
    album_patch, stream_patch, inst = _album_client(_upload(["a.apk", "b.apk"]))
    with album_patch, stream_patch:
        ok = _notify(RELEASE_2_ASSETS)

    assert inst.send_file.call_count == len(_text_cfg().chat_ids)
    assert ok is True


def test_album_all_uploads_fail_returns_false():
    """Every upload fails -> existing all-failed branch returns False."""
    album_patch, stream_patch, _ = _album_client(_upload([]))
    with album_patch, stream_patch:
        ok = _notify(RELEASE_2_ASSETS)

    assert ok is False


def test_album_splits_more_than_ten_assets_into_batches():
    """11 assets become a 10-item album plus a single-item message."""
    names = [f"f{i}.apk" for i in range(11)]
    release = {
        "name": "v9.9.9",
        "tag_name": "v9.9.9",
        "published_at": "2026-09-18T00:00:00Z",
        "html_url": "https://github.com/foo/bar/releases/tag/v9.9.9",
        "assets": [
            {"name": name, "size": 10,
             "browser_download_url": f"https://example.com/{name}"}
            for name in names
        ],
    }
    cfg = TelegramConfig(bot_token="TOK", chat_ids=[111], api_id=1, api_hash="H")

    album_patch, stream_patch, inst = _album_client(_upload(names))
    with album_patch, stream_patch:
        ok = asyncio.run(notify(release, cfg, "", "", ""))

    assert ok is True
    calls = inst.send_file.call_args_list
    assert len(calls) == 2
    assert len(calls[0].args[1]) == 10
    # Telegram rejects single-item albums, so the tail is sent as one media.
    assert not isinstance(calls[1].args[1], list)
