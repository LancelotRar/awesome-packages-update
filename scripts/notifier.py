"""Telegram notification via Telethon — send release alerts with file assets."""

from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from html import escape
from typing import Sequence

from telethon import TelegramClient, errors as tg_errors
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

from github_api import stream_asset
from logs import logger
from timestamps import to_beijing_date
from watchlist import NotifyCopy

_ALBUM_MAX = 10  # Telegram albums accept 2-10 items
_UPLOAD_TIMEOUT = 3600  # seconds for the whole asset fetch/upload phase

_LOGIN_ATTEMPTS = 3  # total login attempts, the first one included
_LOGIN_BACKOFF_INITIAL = 15  # seconds before the first retry; doubles each time

# Failures worth retrying: Telegram-side incidents (``ServerError`` covers
# ``RpcCallFailError``/``RpcMcgetFailError``) and transport errors
# (``OSError`` covers ``ConnectionError`` and ``TimeoutError``).
_TRANSIENT_LOGIN_ERRORS = (
    tg_errors.ServerError,
    tg_errors.TimedOutError,
    OSError,
)

log = logger("notifier")


@dataclass
class TelegramConfig:
    """Telegram connection credentials and target chats."""

    bot_token: str
    chat_ids: list[int] = field(default_factory=list)
    api_id: int = 0
    api_hash: str = ""


def _build_message(asset_name: str, pub_date: str, html_url: str,
                   copy: NotifyCopy) -> str:
    """Build the HTML notification text.

    Optional lines (title/description/group/channel/project) are
    omitted entirely when their value is empty.  ``copy.tag`` is appended
    as the last line, plain text so Telegram indexes it as a
    searchable hashtag.
    """
    name = escape(asset_name)
    lines = []
    if copy.title:
        lines.append(f"🚀 <b>{copy.title}</b>")
    if copy.description:
        lines.append(f"📝 <b>{escape(copy.description)}</b>")
    if copy.group_url:
        lines.append(f'📢 <b><a href="{copy.group_url}">TG 群组</a></b>')
    if copy.channel_url:
        lines.append(f'📣 <b><a href="{copy.channel_url}">TG 频道</a></b>')
    if copy.project_url:
        lines.append(f'🌐 <b><a href="{copy.project_url}">项目地址</a></b>')
    lines.append(f"🌀 <b>版本：</b><code>{name}</code>")
    lines.append(f"🍾 <b>发布时间：{pub_date}</b>")
    lines.append(f'🔗 <b><a href="{html_url}">查看完整 Release 日志</a></b>')
    if copy.tag:
        lines.append(copy.tag)
    return "\n".join(lines)


async def _disconnect_quietly(client: TelegramClient) -> None:
    """Close a client whose login failed, without hiding that failure."""
    try:
        await client.disconnect()  # type: ignore[misc]
    except Exception as e:  # cleanup must never replace the login error
        log.debug("telegram_client_close_failed", error=str(e))


async def open_client(cfg: TelegramConfig) -> TelegramClient:
    """Start a bot Telethon client, retrying transient login failures.

    Telethon retries a request internally only a handful of times at a
    fixed 2s interval, so a ~15s Telegram outage still fails the whole
    run. Login is therefore retried ``_LOGIN_ATTEMPTS`` times with
    exponential backoff, starting at ``_LOGIN_BACKOFF_INITIAL`` seconds and
    doubling per retry.

    Only ``_TRANSIENT_LOGIN_ERRORS`` are retried; any other ``RPCError``
    (bad credential, invalid api id/hash) fails on the first attempt,
    because retrying it only delays the inevitable.

    ``raise_last_call_error`` makes Telethon re-raise the last RPC error
    instead of a bare ``ValueError('Request was unsuccessful ...')``, so
    the cause survives into the log and the retry decision above can tell
    a server incident from a credential problem.

    NOTE: Do NOT use ``async with TelegramClient(…)`` — its
    ``__aenter__`` calls ``self.start()`` with *no* arguments, which
    would fall back to interactive phone/token input (impossible in CI).
    """
    log.debug("telegram_client_starting")
    delay = _LOGIN_BACKOFF_INITIAL
    last_error: Exception | None = None
    for attempt in range(1, _LOGIN_ATTEMPTS + 1):
        client: TelegramClient | None = None
        try:
            client = TelegramClient(
                StringSession(), cfg.api_id, cfg.api_hash,
                connection_retries=3,
                raise_last_call_error=True,
            )
            await client.start(bot_token=cfg.bot_token)  # type: ignore[misc]
        except _TRANSIENT_LOGIN_ERRORS as e:
            last_error = e
            if client is not None:
                await _disconnect_quietly(client)
        except tg_errors.RPCError as e:
            if client is not None:
                await _disconnect_quietly(client)
            raise RuntimeError(
                f"Telegram auth failed — check TG_API_ID / TG_API_HASH / TG_BOT_TOKEN: {e}"
            ) from e
        else:
            log.debug("telegram_client_started")
            return client

        if attempt < _LOGIN_ATTEMPTS:
            log.warning("telegram_login_retry", attempt=attempt,
                        max_attempts=_LOGIN_ATTEMPTS,
                        delay_seconds=delay, error=str(last_error))
            await asyncio.sleep(delay)
            delay *= 2

    assert last_error is not None  # the loop runs at least once
    raise RuntimeError(
        f"Telegram login failed after {_LOGIN_ATTEMPTS} attempts: {last_error}"
    ) from last_error


@asynccontextmanager
async def telegram_session(cfg: TelegramConfig):
    """One Telegram session for the caller's whole unit of work.

    Callers that send several messages should do all of them inside a
    single ``async with`` block, so a run never logs in and out per
    message.
    """
    client = await open_client(cfg)
    try:
        yield client
    finally:
        await client.disconnect()  # type: ignore[misc]


async def _send_batch(client: TelegramClient, chat_id: int, batch: list,
                      attrs: list, caption: str) -> None:
    """Send one album batch (1-10 media) with *caption* on its last item."""
    if len(batch) == 1:
        await client.send_file(
            chat_id, batch[0], caption=caption, parse_mode="html",
            force_document=True, attributes=attrs[0] if attrs else [],
        )
        return

    captions = [""] * (len(batch) - 1) + [caption]
    await client.send_file(
        chat_id, list(batch), caption=captions, parse_mode="html",
        force_document=True, attributes=attrs,
    )


async def send_text(text: str, cfg: TelegramConfig, client) -> bool:
    """Send a plain text message (no files) to every chat.

    *client* is owned by the caller (see ``telegram_session``), so a run
    keeps one session instead of logging in per message. Returns ``True``
    only when every chat received it.
    """
    sent = 0
    for raw_cid in cfg.chat_ids:
        try:
            await client.send_message(raw_cid, text, parse_mode="html",
                                      link_preview=False)
            log.info("text_sent", chat=raw_cid)
            sent += 1
        except tg_errors.FloodWaitError as e:
            log.error("flood_wait", chat=raw_cid, seconds=e.seconds)
            break
        except Exception as e:
            log.error("text_send_failed", chat=raw_cid, error=str(e))
    return sent == len(cfg.chat_ids)


async def notify(release_data: dict, copy: NotifyCopy, cfg: TelegramConfig,
                 client, assets: Sequence[dict], github_token: str = "") -> bool:
    """Send a release notification (with pre-matched assets) to every chat.

    ``copy`` is the watch item's channel-facing wording; ``cfg`` carries the
    connection credentials and target chats. ``assets`` is the already-matched
    set from the scan phase, so the filter runs once per item instead of once
    per check and again per delivery. *client* is owned by the caller (see
    ``telegram_session``).

    Files are streamed from GitHub straight to Telegram CDN (no local
    disk write).  The upload is performed **once** and the same
    ``InputFile`` handles are reused for every target chat.

    Returns ``True`` when all chats were notified successfully.
    """
    # --- build message text ---
    asset_name = release_data.get("name") or release_data["tag_name"]
    pub_date = to_beijing_date(release_data.get("published_at", ""))
    rel_url = release_data["html_url"]
    text = _build_message(asset_name, pub_date, rel_url, copy)

    matched_assets = list(assets)
    all_ok = True

    # --- Upload assets once (shared across all chats) ---
    uploaded_medias: list | None = None
    file_attrs: list | None = None

    if matched_assets:
        log.info("assets_upload_started", count=len(matched_assets))

        async def _fetch_and_upload(asset: dict):
            name = asset["name"]
            size = asset["size"]

            log.info("asset_fetch_started", name=name, size_bytes=size)
            try:
                resp = await asyncio.to_thread(stream_asset, asset, github_token)
                data = await asyncio.to_thread(resp.read)

                last_pct = [0]

                def _progress(sent: int, total: int) -> None:
                    pct = sent * 100 // total
                    if pct - last_pct[0] < 10 and sent != total:
                        return
                    last_pct[0] = pct
                    log.info("asset_upload_progress", name=name, sent_bytes=sent,
                             total_bytes=total, percent=pct)

                uploaded = await client.upload_file(
                    io.BytesIO(data), file_name=name, file_size=size,
                    progress_callback=_progress,
                )
                log.info("asset_uploaded", name=name, size_bytes=size)
                return uploaded, [DocumentAttributeFilename(name)]
            except Exception as e:
                log.warning("asset_upload_failed", name=name, error=str(e))
                return None, None

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[_fetch_and_upload(a) for a in matched_assets]),
                timeout=_UPLOAD_TIMEOUT,
            )
            medias = [r[0] for r in results if r[0] is not None]
            attrs = [r[1] for r in results if r[1] is not None]
            if len(medias) == len(matched_assets):
                # All-or-nothing: an incomplete album must not be sent,
                # otherwise the caller would persist the version while
                # silently losing the failed asset(s).
                uploaded_medias = medias
                file_attrs = attrs
                log.info("album_ready", count=len(medias))
            else:
                log.error("assets_incomplete", uploaded=len(medias),
                          expected=len(matched_assets))
                return False
        except asyncio.TimeoutError:
            log.error("asset_upload_timeout", timeout_seconds=_UPLOAD_TIMEOUT)
            return False
    # -------------------------------------------------------------------

    if not uploaded_medias:
        # Only reachable when there were no matching assets at all;
        # any upload failure already returned False above.
        log.info("text_only_notification", chats=len(cfg.chat_ids))
        # False -> caller skips persistence and retries on the next run.
        # Contract: True only when *every* chat was notified.
        return await send_text(text, cfg, client)

    # A release may expose more files than one Telegram album holds, so
    # send consecutive batches of at most ``_ALBUM_MAX`` items. The
    # caption goes on the first batch only.
    batches = [uploaded_medias[i:i + _ALBUM_MAX]
               for i in range(0, len(uploaded_medias), _ALBUM_MAX)]

    for raw_cid in cfg.chat_ids:
        try:
            for index, batch in enumerate(batches):
                start = index * _ALBUM_MAX
                batch_attrs = (file_attrs or [])[start:start + len(batch)]
                await _send_batch(
                    client, raw_cid, batch, batch_attrs,
                    text if index == 0 else "",
                )
            log.info("album_sent", chat=raw_cid, batches=len(batches),
                     files=len(uploaded_medias))

        except tg_errors.FloodWaitError as e:
            log.error("flood_wait", chat=raw_cid, seconds=e.seconds)
            all_ok = False
            break
        except tg_errors.RPCError as e:
            log.error("telegram_rpc_error", chat=raw_cid, error=str(e))
            all_ok = False
            continue
        except ValueError as e:
            log.error("invalid_chat_id", chat=raw_cid, error=str(e))
            all_ok = False
            continue
        except Exception as e:
            log.error("album_send_failed", chat=raw_cid, error=str(e))
            all_ok = False
            continue

    return all_ok
