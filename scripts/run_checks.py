#!/usr/bin/env python3
"""Run every selected watch item: detect, deliver, persist state.

One job runs the whole watch list. Checks run with bounded concurrency;
deliveries run one item at a time so Telegram flood limits are not
tripped. A failure in one watch item never aborts the others.

Environment variables
---------------------
PROJECT             Watch item id, or "all" (default)
FORCE               "true" re-delivers even when the marker is unchanged
WATCHLIST           Path to projects.yaml (default: repository root)
STATE_FILE          Path to state.json (default: data/state.json at the repository root)
GITHUB_TOKEN        GitHub API token (optional, raises rate limits)
TG_BOT_TOKEN        Telegram bot token (from @BotFather)
TG_CHAT_ID          Target chat ID(s), comma-separated (numeric only)
TG_API_ID           Telegram API ID
TG_API_HASH         Telegram API hash
TG_CHAT_ID_ADMIN    Optional admin chat for consecutive-failure alerts
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from detector import Detection, detect
from notifier import TelegramConfig, notify, send_text
from state import (
    load_state,
    mark_alerted,
    mark_healthy,
    marker_of,
    needs_alert,
    record_delivery,
    record_failure,
    save_state,
)
from watchlist import WatchItem, load_watchlist

CHECK_CONCURRENCY = 8
FAILURE_ALERT_THRESHOLD = 3

_HERE = Path(__file__).resolve().parent


@dataclass
class Outcome:
    """Result of the detection phase for one watch item."""

    item: WatchItem
    detection: Detection | None = None
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(marker: str) -> str:
    return marker[:12] if marker else "(none)"


def _parse_chat_ids(raw: str, var: str) -> list[int]:
    parts = [c.strip() for c in raw.split(",") if c.strip()]
    if not parts:
        print(f"::error::{var} is empty after splitting")
        sys.exit(1)
    try:
        return [int(c) for c in parts]
    except ValueError as e:
        print(f"::error::{var} contains a non-numeric value: {e}")
        sys.exit(1)


def _require_env(var: str) -> str:
    value = os.environ.get(var, "")
    if not value:
        print(f"::error::Missing required env var: {var}")
        sys.exit(1)
    return value


def _telegram(item: WatchItem, chat_ids: list[int]) -> TelegramConfig:
    copy = item.notify
    return TelegramConfig(
        bot_token=_require_env("TG_BOT_TOKEN"),
        chat_ids=chat_ids,
        api_id=int(_require_env("TG_API_ID")),
        api_hash=_require_env("TG_API_HASH"),
        notify_title=copy.title,
        notify_group_url=copy.group_url,
        notify_channel_url=copy.channel_url,
        notify_project_url=copy.project_url,
        notify_description=copy.description,
        notify_tag=copy.tag,
    )


def _select(items: list[WatchItem], raw: str) -> list[WatchItem]:
    if raw == "all":
        return items
    selected = [item for item in items if item.id == raw]
    if not selected:
        known = ", ".join(item.id for item in items)
        raise RuntimeError(f"Unknown project '{raw}'. Known ids: {known}")
    return selected


async def _detect_all(items: list[WatchItem], token: str) -> list[Outcome]:
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def one(item: WatchItem) -> Outcome:
        async with sem:
            try:
                detection = await asyncio.to_thread(detect, item, token)
                return Outcome(item=item, detection=detection)
            except Exception as e:  # one item must never abort the rest
                print(f"::error::{item.id}: {e}", flush=True)
                return Outcome(item=item, error=str(e))

    return list(await asyncio.gather(*(one(item) for item in items)))


def _write_summary(rows: list[tuple[str, str, str]]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    lines = [
        "## Release checks",
        "",
        "| Watch item | Result | Detail |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {pid} | {result} | {detail} |" for pid, result, detail in rows]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _alert_text(item: WatchItem, failures: int, last_error: str) -> str:
    return (
        f"🚨 <b>Release watcher: {item.id}</b>\n"
        f"连续 {failures} 次失败（{item.repo}）\n"
        f"最后错误：<code>{last_error or 'unknown'}</code>"
    )


async def main() -> int:
    watchlist_path = Path(os.environ.get("WATCHLIST") or _HERE.parent / "projects.yaml")
    state_path = Path(os.environ.get("STATE_FILE") or _HERE.parent / "data" / "state.json")

    try:
        items = _select(load_watchlist(watchlist_path), 
                        (os.environ.get("PROJECT") or "all").strip())
        state = load_state(state_path)
    except RuntimeError as e:
        print(f"::error::{e}")
        return 1

    force = os.environ.get("FORCE", "").lower() == "true"
    token = os.environ.get("GITHUB_TOKEN", "")
    chat_ids = _parse_chat_ids(_require_env("TG_CHAT_ID"), "TG_CHAT_ID")

    print(f"Checking {len(items)} watch item(s); force={force}")

    outcomes = await _detect_all(items, token)

    rows: list[tuple[str, str, str]] = []
    failed: list[str] = []
    delivered: list[str] = []

    for outcome in outcomes:
        item = outcome.item
        now = _now()

        if outcome.error:
            record_failure(state, item.id, now, outcome.error)
            failed.append(item.id)
            rows.append((item.id, "❌ detection", outcome.error))
            continue

        detection = outcome.detection
        if detection is None:
            print(f"{item.id}: no releases yet — nothing to do")
            mark_healthy(state, item.id, now)
            rows.append((item.id, "➖ no releases", ""))
            continue

        previous = marker_of(state, item.id)
        if not force and detection.marker == previous:
            print(f"{item.id}: unchanged ({_short(detection.marker)})")
            mark_healthy(state, item.id, now)
            rows.append((item.id, "➖ unchanged", _short(detection.marker)))
            continue

        print(f"{item.id}: update detected "
              f"({_short(previous)} -> {_short(detection.marker)}), "
              f"assets={detection.asset_count}", flush=True)

        error = "delivery failed"
        try:
            ok = await notify(detection.release, _telegram(item, chat_ids),
                              token, item.asset_include, item.asset_exclude)
        except Exception as e:  # delivery errors are per-item, not fatal
            ok = False
            error = str(e)
            print(f"::error::{item.id}: {e}", flush=True)

        if ok:
            record_delivery(state, item.id, detection.marker, now)
            delivered.append(item.id)
            rows.append((item.id, "✅ delivered", _short(detection.marker)))
        else:
            record_failure(state, item.id, now, error)
            failed.append(item.id)
            rows.append((item.id, "❌ delivery", error))

    # Persist before alerting so a crash during alerts cannot lose markers.
    save_state(state_path, state)

    admin_raw = os.environ.get("TG_CHAT_ID_ADMIN", "").strip()
    if admin_raw:
        admin_ids = _parse_chat_ids(admin_raw, "TG_CHAT_ID_ADMIN")
        alerted = False
        for item in items:
            if not needs_alert(state, item.id, FAILURE_ALERT_THRESHOLD):
                continue
            entry = state[item.id]
            text = _alert_text(item, int(entry.get("failures", 0)),
                               str(entry.get("last_error", "")))
            try:
                if await send_text(text, _telegram(item, admin_ids)):
                    mark_alerted(state, item.id)
                    alerted = True
            except Exception as e:
                print(f"::error::Admin alert failed for {item.id}: {e}")
        if alerted:
            save_state(state_path, state)
    else:
        pending = [i.id for i in items
                   if needs_alert(state, i.id, FAILURE_ALERT_THRESHOLD)]
        if pending:
            print("::warning::Failure alerts pending but TG_CHAT_ID_ADMIN "
                  f"is unset: {', '.join(pending)}")

    _write_summary(rows)
    print(f"Done: delivered={len(delivered)} failed={len(failed)} "
          f"unchanged={len(items) - len(delivered) - len(failed)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
