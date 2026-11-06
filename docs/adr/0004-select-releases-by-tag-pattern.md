# Pick releases by tag pattern instead of one fixed tag

Some repositories publish several product lines as separate releases from a single repo. `bitwarden/clients` ships `desktop-*`, `cli-*`, `browser-*` and `web-*`, so `latest` follows whichever release the feed advertises first and mixes product lines in the channel.

We added an optional `release_tag_include` list to `latest`: it lists the repository's releases, drops prereleases and entries without a tag, keeps tags matching any glob pattern case-insensitively, and takes the newest by `published_at`. The marker stays that release's `updated_at`, so state and delivery keep their existing shape. `tag` is untouched: the field is rejected on `tag` because `tag` already names exactly one release, and an explicitly empty list is rejected because the field's contract is a non-empty list.

## Considered Options

- **Glob pattern list on `latest` (`release_tag_include`)** — chosen: one entry keeps following its product line with no per-release config edit, and the marker stays `latest`'s `updated_at`, so state and delivery keep their existing shape.
- **Pin one tag (`strategy: tag`)** — each desktop release carries a new tag (`desktop-v2026.10.4`), so the entry needs a config edit per release, and `tag`'s marker is an asset fingerprint meant for in-place replacement rather than a version marker. Rejected: it turns automatic following into manual maintenance and uses the wrong marker.
- **Skip these repositories** — cheapest, but the channel exists to surface new versions; skipping hides wanted software to avoid a selection problem. Rejected.

## Consequences

- Every `latest` entry now costs two requests (release list, then the selected tag): the list endpoint truncates each release's `assets`, so the full release has to be fetched before filtering files.
- Maintainers must know upstream tag conventions. Patterns are OR'd and matching is case-insensitive, so `desktop-*` is enough to exclude `cli-*`; a prerelease is never selected, even when only a prerelease matches.
- No match is a failure (marker not advanced, retried next run) rather than a skip, so a stale pattern surfaces instead of silently dropping the project.
- The list is one page (100 releases), so selection only sees the newest 100 releases — for filtered and unfiltered entries alike.
