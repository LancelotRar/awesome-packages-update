# Run every watch item from one job

At 50+ watch items, per-project workflows, a matrix, and reusable-workflow callers all cost one runner per item per day. On a private repository a standard Linux runner bills at $0.006/minute against a GitHub Free allowance of $12/month; 50 items × ~2 minutes × 30 days ≈ 3,000 minutes ≈ $18, already over budget. We use one workflow with one job that loops over the watch list, so cost stays near constant (one daily run, ~45–90 minutes/month). The trade-off is a single point of execution: failures must be isolated per watch item, checks run with bounded concurrency, and deliveries stay sequential to avoid Telegram flood limits.

## Considered Options

- **One workflow per watch item** — cheapest to understand, but the maintenance cost that motivated this change, and it scales linearly in billed minutes.
- **Matrix jobs** — keeps isolation, still one runner per item.
- **Reusable workflow + thin callers** — fewer lines per item, same runner count.
- **Generated workflows** — removes hand-copying, same runner count.
