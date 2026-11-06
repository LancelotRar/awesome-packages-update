# Host the release watcher in its own public repository

The watcher started inside `awesome-actions`, a private repository, where Actions minutes bill against the Free allowance. That billing constraint was the main argument for one job looping over the watch list (ADR 0001) and for keeping the watch list small.

The watcher is now its own public repository. Public repositories run standard GitHub-hosted runners without consuming the paid allowance, so run count and run duration stop being budget decisions, and the cost premise in ADR 0001 no longer applies. The single-job design is kept anyway: it still carries every watch item's configuration in one file and keeps the retry-push and failure-isolation logic in one place.

## Consequences

- Every runtime secret (`TG_BOT_TOKEN`, `TG_CHAT_ID`, `TG_API_ID`, `TG_API_HASH`, and the optional admin chat) is provisioned in this repository, not inherited from `awesome-actions`.
- `data/state.json` commits, workflow logs, and the watch list are public. Nothing secret is stored in the repository.
- The eight per-project workflows in `awesome-actions` are retired into `.github/disabled-workflows/`; this repository becomes the only place that delivers release notifications, so the two never notify the same release twice.
- Watch items begin with the markers carried over from the retired workflows, so switching over does not re-deliver the current releases.
- Adding a watch item is a one-file change with no workflow cost, so the list can grow without revisiting this decision.
