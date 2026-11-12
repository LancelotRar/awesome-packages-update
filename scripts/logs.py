"""Structured, correlated logging shared by every script.

Every script used to build its own ``print`` strings, so the Actions log was
prose: no stable event names, no way to tell which watch item or which entry
point produced a line, and GitHub workflow annotations were hand-embedded in
the message text.

All logging now goes through this module:

- every line is an **event** (stable ``snake_case`` name) plus typed fields;
- every line carries the run's ``entry_point`` and ``run_id``, so the daily
  check job and the weekly audit stay distinguishable in one log stream;
- the current ``item`` is bound once and inherited by the modules it calls,
  including ``asyncio.to_thread`` workers;
- ``WARNING``/``ERROR`` become GitHub workflow annotations automatically in
  the default text format;
- fields whose *name* looks like a credential are redacted as a safety net.

Configuration is environment-only, matching the rest of the project:

``LOG_LEVEL``        ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` (default ``INFO``)
``LOG_FORMAT``       ``text`` (default) or ``json`` (one JSON object per line)
``LOG_ANNOTATIONS``  force annotations on/off; default on in Actions text mode

JSON mode never emits ``::error``-style command lines: a workflow command is
not JSON, so mixing the two would corrupt the stream.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from typing import Any, Iterator, Mapping, TextIO

from timestamps import BEIJING_TZ

_ROOT = "release_watcher"

# Envelope keys are emitted by the formatter itself and may not be shadowed
# by a caller's field of the same name.
_ENTRY_POINT = "entry_point"
_RUN_ID = "run_id"

# Defense in depth: callers should never pass credentials as fields, and this
# keeps a future mistake from landing a token in the Actions log.
_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?hash|authorization|credential)",
    re.IGNORECASE,
)
_REDACTED = "***"

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "log_context", default={}
)

# Library convention: without an explicit handler, log records are dropped
# instead of falling back to ``logging.lastResort`` (which would print bare
# event names to stderr).
logging.getLogger(_ROOT).addHandler(logging.NullHandler())


class Log:
    """Thin facade that guarantees ``event, **fields`` log calls.

    Using a facade instead of raw ``logging`` keeps event names stable and
    avoids the ``extra=`` reserved-key pitfalls of the stdlib API.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields)

    def _emit(self, level: int, event: str, fields: Mapping[str, Any]) -> None:
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(level, event, extra={"fields": dict(fields)})


def logger(name: str) -> Log:
    """Return the logger for *name* (usually the module's short name)."""
    return Log(logging.getLogger(f"{_ROOT}.{name}"))


@contextlib.contextmanager
def context(**fields: Any) -> Iterator[None]:
    """Attach *fields* to every log line emitted inside the block.

    Values bound here are visible to called modules and to
    ``asyncio.to_thread`` workers, and are scoped to the current task, so
    concurrent scans never leak one watch item into another.
    """
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def configure(
    entry_point: str,
    *,
    stream: TextIO | None = None,
    level: int | str | None = None,
    fmt: str | None = None,
    annotations: bool | None = None,
) -> None:
    """Install the process-wide handler and bind the run context.

    Call once at the top of an entry point (``main``). Repeated calls
    replace the previous handler, so tests can point ``stream`` at a buffer
    without stacking output.
    """
    level_value = _level_value(
        level if level is not None else os.environ.get("LOG_LEVEL", "INFO")
    )
    chosen = str(fmt if fmt is not None else os.environ.get("LOG_FORMAT", "text"))
    chosen = chosen.strip().lower()
    if chosen not in ("text", "json"):
        chosen = "text"
    if annotations is None:
        annotations = _annotation_default()
    if chosen == "json":
        # A workflow command line is not JSON; never corrupt the stream.
        annotations = False

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(_Renderer(fmt=chosen, annotations=annotations))

    target = logging.getLogger(_ROOT)
    for old in list(target.handlers):
        if not isinstance(old, logging.NullHandler):
            target.removeHandler(old)
    target.addHandler(handler)
    target.setLevel(level_value)
    target.propagate = False

    _context.set({_ENTRY_POINT: entry_point, _RUN_ID: _run_id()})


def _level_value(value: int | str) -> int:
    if isinstance(value, int):
        return value
    resolved = logging.getLevelName(str(value).strip().upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _annotation_default() -> bool:
    """Resolve ``LOG_ANNOTATIONS``, falling back to the Actions detection."""
    raw = os.environ.get("LOG_ANNOTATIONS", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return _flag("GITHUB_ACTIONS")


def _run_id() -> str:
    """GitHub run id + attempt when available, a short id locally."""
    run = os.environ.get("GITHUB_RUN_ID", "").strip()
    if run:
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
        return f"gh-{run}-{attempt}" if attempt else f"gh-{run}"
    return f"local-{uuid.uuid4().hex[:8]}"


class _Renderer(logging.Formatter):
    """Render one record as a text line or a JSON object."""

    def __init__(self, *, fmt: str, annotations: bool) -> None:
        super().__init__()
        self._json = fmt == "json"
        self._annotations = annotations

    def format(self, record: logging.LogRecord) -> str:
        event = record.msg if isinstance(record.msg, str) else str(record.msg)
        bound = dict(_context.get())
        fields: dict[str, Any] = {**bound, **getattr(record, "fields", {})}
        for key in (_ENTRY_POINT, _RUN_ID):
            # The envelope comes from the run context, never from a caller's
            # field of the same name.
            fields.pop(key, None)
        fields = {
            key: _REDACTED if _SECRET_KEY.search(key) else value
            for key, value in fields.items()
        }

        stamp = datetime.now(BEIJING_TZ).isoformat(timespec="milliseconds")
        if self._json:
            payload = {"ts": stamp, "level": record.levelname, "event": event}
            payload.update(bound)
            for key, value in fields.items():
                payload.setdefault(key, value)
            return json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), default=str
            )

        parts = [stamp, f"{record.levelname:<5}"]
        prefix = " ".join(str(bound[key]) for key in (_ENTRY_POINT, _RUN_ID)
                          if bound.get(key))
        if prefix:
            parts.append(f"[{prefix}]")
        parts.append(event)
        parts.extend(f"{key}={_text(value)}" for key, value in fields.items())
        line = " ".join(parts)
        if self._annotations and record.levelno >= logging.WARNING:
            command = "error" if record.levelno >= logging.ERROR else "warning"
            return f"::{command} title={event}::{_escape_command(line)}"
        return line


def _text(value: Any) -> str:
    """Render a field value so it never splits the line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = _one_line(str(value))
    if not text or any(char in text for char in ' "='):
        return json.dumps(text, ensure_ascii=False)
    return text


def _one_line(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")


def _escape_command(text: str) -> str:
    """Escape a workflow-command message per GitHub's rules."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
