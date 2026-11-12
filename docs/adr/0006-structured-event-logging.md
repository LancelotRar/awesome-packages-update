# Log one structured event per line

The scripts printed prose — `print(f"{item.id}: update detected …")` — and hand-wrote `::error::` / `::warning::` prefixes into the same strings. Two entry points (the daily check job and the weekly asset audit) and eight concurrent scans all write into one Actions log, so a line could not say which entry point or which watch item produced it; attributing a line meant reading its neighbours. Nothing was addressable by name either: `grep` matched message wording, so rewording a message silently broke the runbook's queries.

Every script now logs through `scripts/logs.py`: one event name plus typed fields per line, a `[<entry point> <run id>]` prefix, level-driven GitHub annotations, and a contextvar-carried `item` that library modules and `asyncio.to_thread` workers inherit. The implementation is stdlib `logging`; the only sink is the Actions log, so no backend or shipper is introduced.

## Considered Options

- **stdlib `logging` + one formatter** — chosen: zero new dependencies, standard levels, and one place to decide text versus JSON.
- **structlog / loguru** — rejected: another dependency for a formatter this project implements in ~250 lines, and the runtime surface stays at `telethon` + `pyyaml`.
- **JSON only** — rejected as the default: the Actions log is the operating surface, and an annotated human line is what on-call reads. `LOG_FORMAT=json` remains available for machine parsing, at the cost of dropping annotations there (a workflow command line is not JSON).
- **Keep `print` and add the prefix by hand** — rejected: every new call site has to remember it, and nothing enforces an event name.

## Consequences

- Event names are a contract: runbook greps and future alerting key off `run_finished`, `detection_failed`, `assets_unmatched`, and friends. Rename them only together with the runbook.
- `run_id` comes from `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT` when present, so a re-run stays distinguishable from the original; local runs use a random short id.
- Annotations are emitted only in the default text format. JSON mode keeps the stream parseable and leaves the step's exit code as the failure signal.
- Timestamps use Beijing time with milliseconds, consistent with ADR 0005.
- Field values are collapsed to one line and quoted when they contain spaces. Fields whose names look like credentials are redacted as defense in depth, but callers must still never pass secrets.
- Debug lines (`scan_started`, `state_saved`, client start-up) are off by default and cost nothing unless `LOG_LEVEL=DEBUG` is set.
