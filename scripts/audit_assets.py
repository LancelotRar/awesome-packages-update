#!/usr/bin/env python3
"""Report watch items whose asset filter no longer matches anything.

Upstream renames its assets, and a filter that matches nothing used to
degrade into a text-only notification. This audit resolves every enabled
item the same way the runner does and fails loudly on a zero match. When
Telegram credentials are configured, the failure is also pushed to the
admin chat, naming every item that needs attention.

Environment variables
---------------------
WATCHLIST           Path to projects.yaml (default: repository root)
GITHUB_TOKEN        GitHub API token (optional, raises rate limits)
TG_BOT_TOKEN        Telegram bot token; without it the report is skipped
TG_API_ID           Telegram API ID
TG_API_HASH         Telegram API hash
TG_CHAT_ID_ADMIN    Chat ID(s) receiving the failure report
"""

from __future__ import annotations

import asyncio
import os
import sys
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from env import chat_ids as parse_chat_ids
from scan import scan_item
from watchlist import load_watchlist

if TYPE_CHECKING:
    from notifier import TelegramConfig

_HERE = Path(__file__).resolve().parent

# Telegram caps a message at 4096 characters; a broad upstream rename could
# break many items at once, so the report lists at most this many.
_MAX_REPORTED = 25

# One item's detail is clipped to this many characters so a single long
# exception cannot push the whole report past Telegram's limit. The full
# text stays in the Actions log.
_MAX_DETAIL = 160


def _summary(problems: list[tuple[str, str]], total: int) -> str:
    """Build the HTML failure report pushed to Telegram.

    *problems* holds ``(watch item id, plain-text detail)`` pairs; both
    halves are HTML-escaped here so callers never build markup.
    """
    shown = problems[:_MAX_REPORTED]
    lines = [
        "🚨 <b>资产过滤审计失败</b>",
        f"共 {len(problems)} 项需要处理（已检查 {total} 条）：",
        "",
    ]
    for pid, detail in shown:
        # Keep one long upstream error from pushing the report past
        # Telegram's 4096-character cap; the Actions log keeps it whole.
        short = detail if len(detail) <= _MAX_DETAIL else detail[:_MAX_DETAIL - 1] + "…"
        lines.append(f"• <b>{escape(pid)}</b>：{escape(short)}")
    if len(problems) > len(shown):
        lines.append(f"…另有 {len(problems) - len(shown)} 项，完整列表见"
                     " Actions 日志。")
    lines += ["", "请更新 projects.yaml 的 asset_include / asset_exclude 后重跑。"]
    return "\n".join(lines)


def _send(texts: list[str], chat_ids: list[int]) -> bool:
    """Deliver *texts* over ONE Telegram session for the whole run.

    The notifier is imported lazily so a local audit run needs neither
    Telegram credentials nor Telethon.
    """
    from notifier import TelegramConfig

    cfg = TelegramConfig(
        bot_token=os.environ["TG_BOT_TOKEN"].strip(),
        chat_ids=chat_ids,
        api_id=int(os.environ["TG_API_ID"]),
        api_hash=os.environ["TG_API_HASH"].strip(),
    )
    return asyncio.run(_deliver(cfg, texts))


async def _deliver(cfg: TelegramConfig, texts: list[str]) -> bool:
    """Send every text through a single session, closing it at the end.

    Opening once per run — rather than once per message — avoids repeated
    Telegram logins when several reports have to go out.
    """
    from notifier import send_text, telegram_session

    async with telegram_session(cfg) as client:
        all_ok = True
        for text in texts:
            all_ok = await send_text(text, cfg, client) and all_ok
        return all_ok


def _notify_failure(text: str) -> None:
    """Push *text* to the admin chat, tolerating missing credentials.

    A missing or broken Telegram configuration must not change the audit's
    exit code — the red run is still the primary signal.
    """
    chat_raw = os.environ.get("TG_CHAT_ID_ADMIN", "").strip()
    missing = [name for name in ("TG_BOT_TOKEN", "TG_API_ID", "TG_API_HASH")
               if not os.environ.get(name, "").strip()]
    if not chat_raw or missing:
        print("::warning::Telegram failure report skipped — missing "
              f"{'TG_CHAT_ID_ADMIN' if not chat_raw else ', '.join(missing)}")
        return
    try:
        ids = parse_chat_ids(chat_raw, "TG_CHAT_ID_ADMIN")
        sent = _send([text], ids)
    except Exception as e:  # a failed report must never mask the audit result
        print(f"::warning::Telegram failure report failed: {e}")
        return
    if sent:
        print(f"Failure report sent to {len(ids)} chat(s)", flush=True)
    else:
        print("::warning::Telegram failure report was not delivered to "
              "every chat")


def main() -> int:
    watchlist = Path(os.environ.get("WATCHLIST") or _HERE.parent / "projects.yaml")
    token = os.environ.get("GITHUB_TOKEN", "")

    items = [item for item in load_watchlist(watchlist) if item.enabled]
    problems: list[tuple[str, str]] = []

    for item in items:
        result = scan_item(item, token)
        if result.error:
            print(f"::error::{item.id}: detection failed: {result.error}",
                  flush=True)
            problems.append((item.id, f"检测失败：{result.error}"))
            continue

        if result.detection is None:
            print(f"::warning::{item.id}: no releases yet", flush=True)
            continue

        if result.problem:
            print(f"::error::{item.id}: {result.tag} — 0 assets matched "
                  f"{item.asset_include!r}", flush=True)
            problems.append((item.id, result.problem))
            continue

        if not result.asset_count:
            print(f"::warning::{item.id}: {result.tag} has no assets yet",
                  flush=True)
            continue

        print(f"{item.id}: {result.tag} — {len(result.matched)} asset(s)",
              flush=True)

    print(f"Audited {len(items)} item(s), {len(problems)} problem(s)")
    if problems:
        _notify_failure(_summary(problems, len(items)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
