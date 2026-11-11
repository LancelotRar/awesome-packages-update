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
from pathlib import Path

from env import chat_ids, required
from scan import ScanResult, scan_item
from notifier import TelegramConfig, notify, open_client, send_text
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
from timestamps import now_iso
from watchlist import WatchItem, load_watchlist

CHECK_CONCURRENCY = 8
FAILURE_ALERT_THRESHOLD = 3

_HERE = Path(__file__).resolve().parent


def _now() -> str:
    return now_iso()


def _short(marker: str) -> str:
    return marker[:12] if marker else "(none)"


def _parse_chat_ids(raw: str, var: str) -> list[int]:
    try:
        return chat_ids(raw, var)
    except RuntimeError as e:
        print(f"::error::{e}")
        sys.exit(1)


def _require_env(var: str) -> str:
    try:
        return required(var)
    except RuntimeError as e:
        print(f"::error::{e}")
        sys.exit(1)


def _telegram(item: WatchItem, targets: list[int]) -> TelegramConfig:
    copy = item.notify
    cfg = _credentials(targets)
    cfg.notify_title = copy.title
    cfg.notify_group_url = copy.group_url
    cfg.notify_channel_url = copy.channel_url
    cfg.notify_project_url = copy.project_url
    cfg.notify_description = copy.description
    cfg.notify_tag = copy.tag
    return cfg


def _credentials(targets: list[int]) -> TelegramConfig:
    """Build a client-only config; the same connection serves every item."""
    return TelegramConfig(
        bot_token=_require_env("TG_BOT_TOKEN"),
        chat_ids=targets,
        api_id=int(_require_env("TG_API_ID")),
        api_hash=_require_env("TG_API_HASH"),
    )


def _select(items: list[WatchItem], raw: str) -> list[WatchItem]:
    if raw == "all":
        return items
    selected = [item for item in items if item.id == raw]
    if not selected:
        known = ", ".join(item.id for item in items)
        raise RuntimeError(f"Unknown project '{raw}'. Known ids: {known}")
    if not selected[0].enabled:
        raise RuntimeError(f"Watch item '{raw}' is disabled")
    return selected


async def _scan_all(items: list[WatchItem], token: str) -> list[ScanResult]:
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def one(item: WatchItem) -> ScanResult:
        async with sem:
            return await asyncio.to_thread(scan_item, item, token)

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

    active = [item for item in items if item.enabled]
    skipped = [item for item in items if not item.enabled]

    force = os.environ.get("FORCE", "").lower() == "true"
    token = os.environ.get("GITHUB_TOKEN", "")
    targets = (_parse_chat_ids(_require_env("TG_CHAT_ID"), "TG_CHAT_ID")
               if active else [])

    print(f"Checking {len(active)} watch item(s), skipping {len(skipped)} "
          f"disabled; force={force}")

    scans = await _scan_all(active, token)

    row_by_id: dict[str, tuple[str, str, str]] = {}
    failed: list[str] = []
    delivered: list[str] = []
    client = None  # opened lazily, reused for every delivery and alert
    client_error = ""  # first failure to connect; later items fail fast

    for result in scans:
        item = result.item
        now = _now()

        if result.error:
            print(f"::error::{item.id}: {result.error}", flush=True)
            record_failure(state, item.id, now, result.error)
            failed.append(item.id)
            row_by_id[item.id] = (item.id, "❌ detection", result.error)
            continue

        detection = result.detection
        if detection is None:
            print(f"{item.id}: no releases yet — nothing to do")
            mark_healthy(state, item.id, now)
            row_by_id[item.id] = (item.id, "➖ no releases", "")
            continue

        previous = marker_of(state, item.id)
        if not force and detection.marker == previous:
            print(f"{item.id}: unchanged ({_short(detection.marker)})")
            mark_healthy(state, item.id, now)
            row_by_id[item.id] = (item.id, "➖ unchanged", _short(detection.marker))
            continue

        print(f"{item.id}: update detected "
              f"({_short(previous)} -> {_short(detection.marker)}), "
              f"assets={detection.asset_count}", flush=True)

        if result.problem:
            record_failure(state, item.id, now, "no assets matched")
            failed.append(item.id)
            row_by_id[item.id] = (item.id, "❌ no assets", item.asset_include)
            continue

        error = "delivery failed"
        try:
            if client is None and not client_error:
                try:
                    client = await open_client(_credentials(targets))
                except Exception as e:  # one failure must not retry per item
                    client_error = str(e)
                    print(f"::error::Telegram client unavailable: {e}")
            if client is None:
                raise RuntimeError(client_error or "Telegram client unavailable")
            ok = await notify(detection.release, _telegram(item, targets),
                              client, result.matched, token)
        except Exception as e:  # delivery errors are per-item, not fatal
            ok = False
            error = str(e)
            print(f"::error::{item.id}: {e}", flush=True)

        if ok:
            record_delivery(state, item.id, detection.marker, now)
            delivered.append(item.id)
            row_by_id[item.id] = (item.id, "✅ delivered", _short(detection.marker))
        else:
            record_failure(state, item.id, now, error)
            failed.append(item.id)
            row_by_id[item.id] = (item.id, "❌ delivery", error)

    # Persist before alerting so a crash during alerts cannot lose markers.
    save_state(state_path, state)

    rows = [row_by_id.get(item.id, (item.id, "⏭ disabled", ""))
            for item in items]

    admin_raw = os.environ.get("TG_CHAT_ID_ADMIN", "").strip()
    if admin_raw:
        admin_ids = _parse_chat_ids(admin_raw, "TG_CHAT_ID_ADMIN")
        alerted = False
        for item in active:
            if not needs_alert(state, item.id, FAILURE_ALERT_THRESHOLD):
                continue
            entry = state[item.id]
            text = _alert_text(item, int(entry.get("failures", 0)),
                               str(entry.get("last_error", "")))
            try:
                if client is None and not client_error:
                    try:
                        client = await open_client(_credentials(admin_ids))
                    except Exception as e:
                        client_error = str(e)
                        print(f"::error::Telegram client unavailable: {e}")
                if client is None:
                    raise RuntimeError(client_error or "client unavailable")
                if await send_text(text, _telegram(item, admin_ids), client):
                    mark_alerted(state, item.id)
                    alerted = True
            except Exception as e:
                print(f"::error::Admin alert failed for {item.id}: {e}")
        if alerted:
            save_state(state_path, state)
    else:
        pending = [i.id for i in active
                   if needs_alert(state, i.id, FAILURE_ALERT_THRESHOLD)]
        if pending:
            print("::warning::Failure alerts pending but TG_CHAT_ID_ADMIN "
                  f"is unset: {', '.join(pending)}")

    if client is not None:
        await client.disconnect()

    _write_summary(rows)
    unchanged = len(active) - len(delivered) - len(failed)
    print(f"Done: delivered={len(delivered)} failed={len(failed)} "
          f"unchanged={unchanged} skipped={len(skipped)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
