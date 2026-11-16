#!/usr/bin/env python3
"""Run every selected watch item: detect, deliver, persist state.

One job runs the whole watch list. Checks run with bounded concurrency;
deliveries run one item at a time so Telegram flood limits are not
tripped. A failure in one watch item never aborts the others, and each
item's state is persisted as soon as it settles so a crash, cancel or
timeout cannot re-deliver an already-notified release.

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
import time
from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from env import ConfigError, chat_ids, required, telegram_config
from logs import configure, context, logger
from notifier import TelegramConfig, notify, open_client, send_text
from scan import ScanResult, ScanStatus, scan_item
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

log = logger("run_checks")


class OutcomeKind(StrEnum):
    """Per-item result; the value is also the run-summary cell text."""

    DELIVERED = "✅ delivered"
    UNCHANGED = "➖ unchanged"
    NO_RELEASES = "➖ no releases"
    DISABLED = "⏭ disabled"
    NO_ASSETS = "❌ no assets"
    DETECTION_FAILED = "❌ detection"
    DELIVERY_FAILED = "❌ delivery"


@dataclass(frozen=True)
class Outcome:
    """What one watch item did during this run."""

    item_id: str
    kind: OutcomeKind
    detail: str = ""


class _Telegram:
    """One client, opened on first use and reused for every send."""

    def __init__(self, targets: list[int]) -> None:
        self._targets = targets
        self._cfg: TelegramConfig | None = None
        self._client = None
        self._error = ""

    @property
    def client(self):
        return self._client

    async def connect(self) -> None:
        """Open the client once; later calls reuse it or fail fast."""
        if self._client is not None or self._error:
            return
        try:
            cfg = telegram_config(self._targets)
            self._client = await open_client(cfg)
        except Exception as e:
            self._error = str(e)
            log.error("telegram_client_unavailable", error=str(e))
            return
        self._cfg = cfg

    def config(self, chat_ids: list[int] | None = None) -> TelegramConfig:
        """Connection config for *chat_ids* (default: delivery targets)."""
        if self._cfg is None or self._client is None:
            raise RuntimeError(self._error or "Telegram client unavailable")
        if chat_ids is None:
            return self._cfg
        return replace(self._cfg, chat_ids=chat_ids)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _now() -> str:
    return now_iso()


def _short(marker: str) -> str:
    return marker[:12] if marker else "(none)"


def _admin_ids(raw: str) -> list[int]:
    """Parse the optional admin chat list, logging a bad value instead of exiting."""
    if not raw:
        return []
    try:
        return chat_ids(raw, "TG_CHAT_ID_ADMIN")
    except ConfigError as e:
        log.error("config_error", error=str(e))
        return []


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
        # Bound to this task's context, so logs from ``scan_item`` and the
        # GitHub client carry the item they belong to.
        with context(item=item.id):
            async with sem:
                log.debug("scan_started", repo=item.repo)
                return await asyncio.to_thread(scan_item, item, token)

    return list(await asyncio.gather(*(one(item) for item in items)))


def _summary_rows(items: list[WatchItem],
                  outcomes: list[Outcome]) -> list[tuple[str, str, str]]:
    """Render one summary row per watch item, in watch-list order.

    A missing outcome for an enabled item is a bug and raises, rather than
    being mislabelled as a disabled item.
    """
    by_id = {outcome.item_id: outcome for outcome in outcomes}
    rows: list[tuple[str, str, str]] = []
    for item in items:
        if not item.enabled:
            rows.append((item.id, OutcomeKind.DISABLED.value, ""))
            continue
        outcome = by_id[item.id]
        rows.append((item.id, outcome.kind.value, outcome.detail))
    return rows


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


async def _deliver_one(result: ScanResult, state, telegram: _Telegram,
                       force: bool, token: str) -> Outcome:
    """Settle one watch item into state and return its outcome."""
    item = result.item
    now = _now()

    with context(item=item.id):
        if result.status is ScanStatus.DETECTION_FAILED:
            log.error("detection_failed", repo=item.repo, error=result.reason)
            record_failure(state, item.id, now, result.reason)
            return Outcome(item.id, OutcomeKind.DETECTION_FAILED, result.reason)

        if result.status is ScanStatus.NO_RELEASES:
            log.info("no_releases", repo=item.repo)
            mark_healthy(state, item.id, now)
            return Outcome(item.id, OutcomeKind.NO_RELEASES)

        detection = result.detection
        assert detection is not None  # guaranteed by ScanResult

        previous = marker_of(state, item.id)
        if not force and detection.marker == previous:
            log.info("unchanged", repo=item.repo, marker=detection.marker)
            mark_healthy(state, item.id, now)
            return Outcome(item.id, OutcomeKind.UNCHANGED,
                           _short(detection.marker))

        log.info("update_detected", repo=item.repo, previous=previous,
                 marker=detection.marker, assets=len(detection.matched))

        if result.status is ScanStatus.ASSETS_UNMATCHED:
            log.error("assets_unmatched", repo=item.repo,
                      include=item.asset_include)
            record_failure(state, item.id, now, result.reason)
            return Outcome(item.id, OutcomeKind.NO_ASSETS, result.reason)

        await telegram.connect()
        error = "delivery failed"
        try:
            ok = await notify(detection.release, item.notify, telegram.config(),
                              telegram.client, result.matched, token)
        except Exception as e:  # delivery errors are per-item, not fatal
            ok = False
            error = str(e)
            log.error("delivery_failed", repo=item.repo, error=str(e))

        if ok:
            record_delivery(state, item.id, detection.marker, now)
            return Outcome(item.id, OutcomeKind.DELIVERED,
                           _short(detection.marker))

        record_failure(state, item.id, now, error)
        return Outcome(item.id, OutcomeKind.DELIVERY_FAILED, error)


async def _alert_failures(items: list[WatchItem], state,
                          telegram: _Telegram, admin_raw: str) -> bool:
    """Alert the admin chat about items past the failure threshold."""
    admin_ids = _admin_ids(admin_raw)
    if not admin_ids:
        if not admin_raw:
            pending = [item.id for item in items
                       if needs_alert(state, item.id, FAILURE_ALERT_THRESHOLD)]
            if pending:
                log.warning("admin_chat_unset", pending=",".join(pending),
                            threshold=FAILURE_ALERT_THRESHOLD)
        return False

    alerted = False
    for item in items:
        if not needs_alert(state, item.id, FAILURE_ALERT_THRESHOLD):
            continue
        entry = state[item.id]
        text = _alert_text(item, int(entry.get("failures", 0)),
                           str(entry.get("last_error", "")))
        with context(item=item.id):
            try:
                await telegram.connect()
                if await send_text(text, telegram.config(admin_ids),
                                   telegram.client):
                    mark_alerted(state, item.id)
                    alerted = True
                    log.info("admin_alert_sent")
            except Exception as e:
                log.error("admin_alert_failed", error=str(e))
    return alerted


async def main() -> int:
    configure("run_checks")
    started = time.monotonic()
    watchlist_path = Path(os.environ.get("WATCHLIST") or _HERE.parent / "projects.yaml")
    state_path = Path(os.environ.get("STATE_FILE") or _HERE.parent / "data" / "state.json")

    try:
        items = _select(load_watchlist(watchlist_path),
                        (os.environ.get("PROJECT") or "all").strip())
        state = load_state(state_path)
    except RuntimeError as e:
        log.error("config_error", error=str(e))
        return 1

    active = [item for item in items if item.enabled]
    skipped = [item for item in items if not item.enabled]

    force = os.environ.get("FORCE", "").lower() == "true"
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        targets = (chat_ids(required("TG_CHAT_ID"), "TG_CHAT_ID")
                   if active else [])
    except ConfigError as e:
        log.error("config_error", error=str(e))
        return 1

    log.info("run_started", items=len(active), skipped=len(skipped), force=force)

    scans = await _scan_all(active, token)
    telegram = _Telegram(targets)
    outcomes: list[Outcome] = []
    try:
        for result in scans:
            outcomes.append(
                await _deliver_one(result, state, telegram, force, token)
            )
            # Persist as each item settles: a timeout, cancel or crash must
            # not re-deliver an already-notified release on the next run.
            save_state(state_path, state)
        log.debug("state_saved", path=str(state_path))

        rows = _summary_rows(items, outcomes)
        alerted = await _alert_failures(
            active, state, telegram,
            os.environ.get("TG_CHAT_ID_ADMIN", "").strip(),
        )
        if alerted:
            save_state(state_path, state)
    finally:
        await telegram.close()

    _write_summary(rows)

    counts = Counter(outcome.kind for outcome in outcomes)
    failed = sum(counts[kind] for kind in (
        OutcomeKind.DETECTION_FAILED,
        OutcomeKind.NO_ASSETS,
        OutcomeKind.DELIVERY_FAILED,
    ))
    unchanged = counts[OutcomeKind.UNCHANGED] + counts[OutcomeKind.NO_RELEASES]
    log.info("run_finished", delivered=counts[OutcomeKind.DELIVERED],
             failed=failed, unchanged=unchanged, skipped=len(skipped),
             duration_ms=round((time.monotonic() - started) * 1000))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
