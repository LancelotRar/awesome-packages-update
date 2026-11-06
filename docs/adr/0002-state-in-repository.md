# Keep detection state in the repository

A watch item's marker must survive between runs, and losing it re-sends an already-delivered release to the channel. Actions caches are evictable and artifacts expire after 90 days, so either can silently reset state. We commit a single `state.json` under the project's data directory once per run: it is auditable, never expires, and matches the one-failure-per-item isolation the runner already needs. The cost is at most one commit per day.

## Consequences

- The state file is the only mutable artifact the workflow writes back, so the retry-push logic lives in one place instead of eight.
- A failed delivery never advances the marker, so the next run retries the same release.
