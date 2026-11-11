"""Telegram notification via Telethon — send release alerts with file assets."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from html import escape

from telethon import TelegramClient, errors as tg_errors
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

from github_api import get_matching_assets, stream_asset
from timestamps import to_beijing_date

_ALBUM_MAX = 10  # Telegram albums accept 2-10 items


@dataclass
class TelegramConfig:
    """Credentials and display settings for Telegram notification."""

    bot_token: str
    chat_ids: list[int] = field(default_factory=list)
    api_id: int = 0
    api_hash: str = ""
    notify_title: str = ""
    notify_group_url: str = ""
    notify_channel_url: str = ""
    notify_project_url: str = ""
    notify_description: str = ""
    notify_tag: str = ""


def _build_message(asset_name: str, pub_date: str, html_url: str,
                   title: str, group_url: str,
                   channel_url: str = "", project_url: str = "",
                   description: str = "", tag: str = "") -> str:
    """Build the HTML notification text.

    Optional lines (title/description/group/channel/project) are
    omitted entirely when their value is empty.  ``tag`` is appended
    as the last line, plain text so Telegram indexes it as a
    searchable hashtag.
    """
    name = escape(asset_name)
    lines = []
    if title:
        lines.append(f"🚀 <b>{title}</b>")
    if description:
        lines.append(f"📝 <b>{escape(description)}</b>")
    if group_url:
        lines.append(f'📢 <b><a href="{group_url}">TG 群组</a></b>')
    if channel_url:
        lines.append(f'📣 <b><a href="{channel_url}">TG 频道</a></b>')
    if project_url:
        lines.append(f'🌐 <b><a href="{project_url}">项目地址</a></b>')
    lines.append(f"🌀 <b>版本：</b><code>{name}</code>")
    lines.append(f"🍾 <b>发布时间：{pub_date}</b>")
    lines.append(f'🔗 <b><a href="{html_url}">查看完整 Release 日志</a></b>')
    if tag:
        lines.append(tag)
    return "\n".join(lines)


async def open_client(cfg: TelegramConfig) -> TelegramClient:
    """Start a bot Telethon client, failing fast on auth errors.

    NOTE: Do NOT use ``async with TelegramClient(…)`` — its
    ``__aenter__`` calls ``self.start()`` with *no* arguments, which
    would fall back to interactive phone/token input (impossible in CI).
    """
    print("Starting Telethon client …")
    client = TelegramClient(
        StringSession(), cfg.api_id, cfg.api_hash,
        connection_retries=3,
    )
    try:
        await client.start(bot_token=cfg.bot_token)  # type: ignore[misc]
    except tg_errors.RPCError as e:
        raise RuntimeError(
            f"Telegram auth failed — check TG_API_ID / TG_API_HASH / TG_BOT_TOKEN: {e}"
        ) from e
    print("Telethon client started")
    return client


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


async def send_text(text: str, cfg: TelegramConfig, client=None) -> bool:
    """Send a plain text message (no files) to every chat.

    Pass *client* to reuse a connection owned by the caller; otherwise one
    is opened here and closed before returning. Returns ``True`` only when
    every chat received it.
    """
    owns_client = client is None
    if client is None:
        client = await open_client(cfg)
    sent = 0
    try:
        for raw_cid in cfg.chat_ids:
            try:
                await client.send_message(raw_cid, text, parse_mode="html",
                                          link_preview=False)
                print(f"Text message sent to  {raw_cid}", flush=True)
                sent += 1
            except tg_errors.FloodWaitError as e:
                print(f"::error::Flood wait {e.seconds}s on {raw_cid} — skipped")
                break
            except Exception as e:
                print(f"::error::Failed to send text to {raw_cid}: {e}")
    finally:
        if owns_client:
            await client.disconnect()  # type: ignore[misc]
    return sent == len(cfg.chat_ids)


async def notify(release_data: dict, cfg: TelegramConfig,
                 github_token: str = "",
                 include_filter: str = "",
                 exclude_filter: str = "",
                 client=None) -> bool:
    """Send a release notification (with matching assets) to every chat.

    Files are streamed from GitHub straight to Telegram CDN (no local
    disk write).  The upload is performed **once** and the same
    ``InputFile`` handles are reused for every target chat.

    Returns ``True`` when all chats were notified successfully.
    """
    # --- build message text ---
    asset_name = release_data.get("name") or release_data["tag_name"]
    pub_date = to_beijing_date(release_data.get("published_at", ""))
    rel_url = release_data["html_url"]
    text = _build_message(asset_name, pub_date, rel_url,
                          cfg.notify_title, cfg.notify_group_url,
                          cfg.notify_channel_url, cfg.notify_project_url,
                          cfg.notify_description, cfg.notify_tag)

    # --- Decide whether this delivery has files at all ---
    matched_assets = get_matching_assets(release_data,
                                         include_filter, exclude_filter)
    if include_filter.strip() and not matched_assets:
        print(f"::error::No asset in {release_data.get('tag_name', '?')} "
              f"matched ASSET_INCLUDE={include_filter!r} — treating as a "
              "delivery failure so the marker is not advanced", flush=True)
        return False

    # --- Telethon client (reused across the run when the caller passes one) ---
    owns_client = client is None
    if client is None:
        client = await open_client(cfg)

    all_ok = True

    # --- Upload assets once (shared across all chats) ---
    uploaded_medias: list | None = None
    file_attrs: list | None = None

    if matched_assets:
        print(f"Fetching & uploading {len(matched_assets)} asset(s) …", flush=True)

        async def _fetch_and_upload(asset: dict):
            name = asset["name"]
            size = asset["size"]
            size_mb = round(size / 1_048_576, 1)

            print(f"Fetching  {name}  ({size_mb} MB) …", flush=True)
            try:
                resp = await asyncio.to_thread(stream_asset, asset, github_token)
                data = await asyncio.to_thread(resp.read)

                last_pct = [0]

                def _progress(sent: int, total: int) -> None:
                    pct = sent * 100 // total
                    if pct - last_pct[0] < 10 and sent != total:
                        return
                    last_pct[0] = pct
                    sent_mb = sent / 1_048_576
                    total_mb = total / 1_048_576
                    print(f"  Upload: {sent_mb:.1f}/{total_mb:.1f} MB ({pct}%)", flush=True)

                uploaded = await client.upload_file(
                    io.BytesIO(data), file_name=name, file_size=size,
                    progress_callback=_progress,
                )
                print(f"Uploaded  {name}", flush=True)
                return uploaded, [DocumentAttributeFilename(name)]
            except Exception as e:
                print(f"::warning::Failed to process {name}: {e}")
                return None, None

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[_fetch_and_upload(a) for a in matched_assets]),
                timeout=3600,
            )
            medias = [r[0] for r in results if r[0] is not None]
            attrs = [r[1] for r in results if r[1] is not None]
            if len(medias) == len(matched_assets):
                # All-or-nothing: an incomplete album must not be sent,
                # otherwise the caller would persist the version while
                # silently losing the failed asset(s).
                uploaded_medias = medias
                file_attrs = attrs
                print("All assets ready, sending as album …", flush=True)
            else:
                print(f"::error::Only {len(medias)}/{len(matched_assets)} "
                      f"asset(s) uploaded — will retry on next run", flush=True)
                if owns_client:
                    await client.disconnect()  # type: ignore[misc]
                return False
        except asyncio.TimeoutError:
            print("::error::Asset processing timeout — skipped", flush=True)
            if owns_client:
                await client.disconnect()  # type: ignore[misc]
            return False
    # -------------------------------------------------------------------

    if not uploaded_medias:
        # Only reachable when there were no matching assets at all;
        # any upload failure already returned False above.
        print("No matching assets — sending text notification")
        sent = 0
        for raw_cid in cfg.chat_ids:
            try:
                # link_preview=False: 消息含多个 URL, 关闭链接预览避免大段展开
                await client.send_message(raw_cid, text, parse_mode="html",
                                          link_preview=False)
                print(f"Text notification sent to  {raw_cid}", flush=True)
                sent += 1
            except tg_errors.FloodWaitError as e:
                print(f"::error::Flood wait {e.seconds}s on {raw_cid} — skipped")
                break
            except Exception as e:
                print(f"::error::Failed to notify {raw_cid}: {e}")
        if owns_client:
            await client.disconnect()  # type: ignore[misc]
        # False -> caller skips persistence and retries on the next run.
        # Contract: True only when *every* chat was notified.
        return sent == len(cfg.chat_ids)

    # A release may expose more files than one Telegram album holds, so
    # send consecutive batches of at most ``_ALBUM_MAX`` items. The
    # caption goes on the first batch only.
    batches = [uploaded_medias[i:i + _ALBUM_MAX]
               for i in range(0, len(uploaded_medias), _ALBUM_MAX)]

    try:
        for raw_cid in cfg.chat_ids:
            try:
                for index, batch in enumerate(batches):
                    start = index * _ALBUM_MAX
                    batch_attrs = (file_attrs or [])[start:start + len(batch)]
                    await _send_batch(
                        client, raw_cid, batch, batch_attrs,
                        text if index == 0 else "",
                    )
                print(f"Album ({len(batches)} batch(es)) sent to  {raw_cid}",
                      flush=True)

            except tg_errors.FloodWaitError as e:
                print(
                    f"::error::Flood wait {e.seconds}s on {raw_cid}"
                    " — skipping remaining chats"
                )
                all_ok = False
                break
            except tg_errors.RPCError as e:
                print(f"::error::Telegram RPC error for {raw_cid}: {e}")
                all_ok = False
                continue
            except ValueError as e:
                print(f"::error::Invalid chat ID '{raw_cid}': {e}")
                all_ok = False
                continue
            except Exception as e:
                print(f"::error::Failed to send to {raw_cid}: {e}")
                all_ok = False
                continue
    finally:
        if owns_client:
            await client.disconnect()  # type: ignore[misc]
            print("Telethon client disconnected")

    return all_ok
