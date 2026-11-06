"""GitHub API client — fetch releases and filter assets."""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_GITHUB_API = "https://api.github.com"
_MAX_ATTEMPTS = 3
# Rate-limit waits come from response hints (Retry-After / X-RateLimit-Reset)
# that can exceed a CI job's patience — never sleep longer than this.
_MAX_RATELIMIT_WAIT = 120


class _HttpError(RuntimeError):
    """Exhausted or permanent HTTP failure, carrying the status code."""

    def __init__(self, code: int, reason: str):
        super().__init__(f"GitHub API HTTP {code}: {reason}")
        self.code = code


def _build_headers(token: str = "") -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": "GitHub-Actions",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_download_headers(token: str = "") -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": "GitHub-Actions",
        "Accept": "application/octet-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_rate_limited(e: urllib.error.HTTPError) -> bool:
    """True when *e* is a GitHub rate-limit response, not a real failure.

    - HTTP 429: secondary rate limit (burst protection) — can trip even
      with plenty of hourly quota left (e.g. several workflows dispatching
      in the same few minutes).
    - HTTP 403 with ``X-RateLimit-Remaining: 0`` (or a ``Retry-After``
      header): primary quota exhausted — wait until reset instead of
      failing the job.
    A plain permission 403 carries neither header and must fail fast.
    """
    if e.code == 429:
        return True
    if e.code == 403:
        headers = e.headers or {}
        return (headers.get("X-RateLimit-Remaining") == "0"
                or "Retry-After" in headers)
    return False


def _rate_limit_wait(e: urllib.error.HTTPError) -> int | None:
    """Seconds to wait before retrying *e*, or *None* when no hint exists.

    Prefers the server's explicit ``Retry-After`` (seconds), then falls
    back to ``X-RateLimit-Reset`` (epoch seconds, +1s buffer). The caller
    caps the result at ``_MAX_RATELIMIT_WAIT``.
    """
    headers = e.headers or {}
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(1, math.ceil(float(retry_after)))
        except ValueError:
            pass
    reset = headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            return max(1, math.ceil(int(reset) - time.time()) + 1)
        except ValueError:
            pass
    return None


def _fetch_bytes(req: urllib.request.Request, timeout: int, what: str) -> bytes:
    """Open *req* and read the body, retrying transient failures.

    Retries (up to ``_MAX_ATTEMPTS``, exponential backoff ``2**attempt``):

    - HTTP >= 500 (server errors);
    - rate-limited responses (429 / quota-exhausted 403) — waits the
      ``Retry-After`` / ``X-RateLimit-Reset`` hint (capped at
      ``_MAX_RATELIMIT_WAIT``) when present, else plain backoff;
      without this a single 429 used to kill the whole check-release job;
    - network errors (``OSError``).

    Raises ``_HttpError`` — a ``RuntimeError`` carrying ``.code`` — on
    permanent HTTP errors and once retries are exhausted. Callers that
    special-case a status (e.g. 404) inspect ``e.code``.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            wait: int | None = None
            kind = ""
            if _is_rate_limited(e):
                hint = _rate_limit_wait(e)
                wait = min(hint if hint is not None else 2**attempt,
                           _MAX_RATELIMIT_WAIT)
                kind = " rate limited"
            elif e.code >= 500:
                wait = 2**attempt
            if wait is None or attempt >= _MAX_ATTEMPTS:
                raise _HttpError(e.code, e.reason) from e
            print(f"GitHub API HTTP {e.code}{kind} "
                  f"(attempt {attempt}/{_MAX_ATTEMPTS}), retry in {wait}s …")
            time.sleep(wait)
        except OSError as e:
            if attempt < _MAX_ATTEMPTS:
                sleep = 2**attempt
                print(f"Connection error (attempt {attempt}/{_MAX_ATTEMPTS}), retry in {sleep}s …")
                time.sleep(sleep)
                continue
            raise RuntimeError(f"Failed to fetch {what}: {e}") from e
    raise RuntimeError(f"Failed to fetch {what}: retries exhausted")  # unreachable


def _get_json(url: str, token: str = "", what: str = "data") -> Any:
    """GET *url* and decode the JSON body, with retry on transient errors."""
    req = urllib.request.Request(url, headers=_build_headers(token))
    raw = _fetch_bytes(req, timeout=300, what=what)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to fetch {what}: {e}") from e


def fetch_latest_release(repo: str, token: str = "") -> dict | None:
    """Fetch the latest release from *repo*.

    Returns the release dict, or *None* if the repository has no
    releases yet.
    """
    url = f"{_GITHUB_API}/repos/{repo}/releases?per_page=1"
    req = urllib.request.Request(url, headers=_build_headers(token))
    raw = _fetch_bytes(req, timeout=300, what="releases")
    try:
        releases = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to fetch releases: {e}") from e

    if not releases:
        return None
    return releases[0]


def fetch_releases(repo: str, token: str = "") -> list[dict]:
    """Fetch the release list of *repo*, newest first.

    Returns an empty list when the repository has no releases. Used by the
    ``latest`` strategy when a tag filter has to pick a release instead of
    accepting whatever the feed advertises first.
    """
    url = f"{_GITHUB_API}/repos/{repo}/releases?per_page=100"
    releases = _get_json(url, token, what="releases")
    if not isinstance(releases, list):
        raise RuntimeError("Failed to fetch releases: expected a JSON array")
    return releases


def fetch_release_by_tag(repo: str, tag: str, token: str = "") -> dict | None:
    """Fetch the release identified by *tag*, or *None* when absent.

    Needed for long-lived rolling releases: their assets are replaced in
    place, so the newest-release endpoint never returns them.
    """
    quoted = urllib.parse.quote(tag, safe="")
    url = f"{_GITHUB_API}/repos/{repo}/releases/tags/{quoted}"
    try:
        return _get_json(url, token, what=f"release {tag}")
    except _HttpError as e:
        if e.code == 404:
            return None
        raise


def _parse_filter(filter_str: str) -> list[list[tuple[str, str]]]:
    """Parse a filter string into a list of AND-groups (OR'd together).

    Comma separates OR groups; ``+`` within a group means AND.
    A token starting with ``.`` is a suffix match, otherwise a
    case-insensitive substring match.
    """
    if not filter_str:
        return []

    or_groups: list[list[tuple[str, str]]] = []
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        and_tokens: list[tuple[str, str]] = []
        for token in part.split("+"):
            token = token.strip()
            if not token:
                continue
            if token.startswith("."):
                and_tokens.append(("suffix", token.lower()))
            else:
                and_tokens.append(("substr", token.lower()))
        if and_tokens:
            or_groups.append(and_tokens)
    return or_groups


def _asset_matches(name: str, include_rules: list[list[tuple[str, str]]],
                   exclude_rules: list[list[tuple[str, str]]]) -> bool:
    """Return ``True`` if *name* passes include/exclude rules."""

    def _match_and_group(group: list[tuple[str, str]]) -> bool:
        n = name.lower()
        return all(
            n.endswith(token) if kind == "suffix" else token in n
            for kind, token in group
        )

    # Include: if rules exist, asset must match at least one OR-group.
    if include_rules and not any(_match_and_group(g) for g in include_rules):
        return False

    # Exclude: if any OR-group matches, asset is rejected.
    if exclude_rules and any(_match_and_group(g) for g in exclude_rules):
        return False

    return True


def get_matching_assets(release_data: dict,
                        include_filter: str = "",
                        exclude_filter: str = "") -> list[dict]:
    """Filter release assets by include/exclude rules.

    Parameters
    ----------
    release_data
        The release dict from GitHub API.
    include_filter
        Comma-separated OR groups (``+`` = AND).
        Empty means every asset is eligible.
    exclude_filter
        Same syntax; assets matching any exclude rule are dropped.

    Returns assets whose *size* > 0 and that pass the filters.
    """
    assets = release_data.get("assets", [])
    if not assets:
        return []

    include_rules = _parse_filter(include_filter)
    exclude_rules = _parse_filter(exclude_filter)

    matched = [a for a in assets
               if a["size"] > 0 and _asset_matches(a["name"], include_rules, exclude_rules)]

    # -- logging --
    if include_filter or exclude_filter:
        out = len(matched)
        total = len(assets)
        skipped = total - out
        bits = []
        if include_filter:
            bits.append(f"inc=[{include_filter}]")
        if exclude_filter:
            bits.append(f"exc=[{exclude_filter}]")
        print(f"Asset filter: {' '.join(bits)} -> {out}/{total} matched"
              + (f" (skipped {skipped})" if skipped else ""))
    else:
        print(f"No filter — including all {len(assets)} assets")

    return matched


def stream_asset(asset: dict, token: str = ""):
    """Open an HTTP connection to *asset*'s download URL.

    Returns an ``http.client.HTTPResponse`` (file-like object).
    The caller is responsible for an overall timeout (e.g. via
    ``asyncio.wait_for``).
    """
    url = asset["browser_download_url"]
    req = urllib.request.Request(url, headers=_build_download_headers(token))
    return urllib.request.urlopen(req)  # noqa: S310 — caller wraps in wait_for
