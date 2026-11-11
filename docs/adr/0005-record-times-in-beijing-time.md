# Record and display timestamps in Beijing time

The channel's readers are on UTC+8, but `state.json` stored GitHub's raw UTC `Z` values and the notification date was sliced from `published_at`. Bettbox v1.19.8 was published at `2026-11-11T17:52:43Z` — already `2026-11-12 01:52` in Beijing — so the channel announced 11-11. Reading a UTC date as a local one is one release away from a wrong day every evening.

Every timestamp the project records now uses an explicit Beijing offset: `marker` and `last_checked` in `state.json`, the notification date, and the audit workflow's cron. `scripts/timestamps.py` owns the conversion so the three call sites cannot drift apart.

## Considered Options

- **Store Beijing time with `+08:00`** — chosen: self-describing, no reader has to know the project's convention, and a value can never be confused with UTC the way a naive local time would be.
- **Keep UTC and convert only at display time** — rejected: the state file is read by hand during incidents, so the mismatch just moves to whoever runs `cat data/state.json`.
- **Write naive local time without an offset** — rejected: another machine or a future DST-sensitive locale would silently reinterpret it.

## Consequences

- `state.json` markers from before this change are still `...Z`. `load_state` converts them in memory before comparison, so the first run after the change does not see every marker as "changed" and re-deliver the whole watch list.
- Marker equality now depends on that normalization. A hand-edited UTC marker still compares correctly because the load path converts it, but once the next run saves state the file holds the Beijing form; write `YYYY-MM-DDTHH:MM:SS+08:00` when editing by hand to avoid a one-run conversion step.
- `tag` strategy fingerprints hash raw upstream values and are not timestamps, so they are deliberately left untouched.
- `release_tag_include` still picks the newest release by the raw `published_at`; only what is recorded and shown is converted.
