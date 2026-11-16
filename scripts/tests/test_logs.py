"""Unit tests for ``logs`` — one structured, correlated event per line."""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logs


def _capture(monkeypatch, **kwargs):
    """Configure logging into a buffer with a predictable environment."""
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_ACTIONS",
                 "LOG_LEVEL", "LOG_FORMAT", "LOG_ANNOTATIONS"):
        monkeypatch.delenv(name, raising=False)
    buf = io.StringIO()
    kwargs.setdefault("level", "INFO")
    logs.configure("run_checks", stream=buf, **kwargs)
    return buf, logs.logger("test")


def test_text_line_carries_entry_point_event_and_fields(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=False)

    log.info("update_detected", item="a", assets=2)

    line = buf.getvalue().strip()
    assert "INFO" in line
    assert "[run_checks local-" in line
    assert " update_detected " in line
    assert "item=a" in line
    assert "assets=2" in line


def test_json_line_is_one_object(monkeypatch):
    buf, log = _capture(monkeypatch, fmt="json")

    log.info("delivered", item="a", marker="m1", ok=True)

    payload = json.loads(buf.getvalue().strip())
    assert payload["level"] == "INFO"
    assert payload["event"] == "delivered"
    assert payload["entry_point"] == "run_checks"
    assert payload["run_id"].startswith("local-")
    assert payload["ts"].endswith("+08:00")
    assert payload["item"] == "a"
    assert payload["ok"] is True


def test_context_fields_are_scoped_to_the_block(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=False)

    with logs.context(item="a"):
        log.info("inside")
    log.info("outside")

    inside, outside = buf.getvalue().splitlines()
    assert "item=a" in inside
    assert "item=a" not in outside


def test_debug_is_hidden_at_info(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=False)

    log.debug("scan_started", item="a")
    log.info("run_started")

    assert "scan_started" not in buf.getvalue()
    assert "run_started" in buf.getvalue()


def test_warning_and_error_become_workflow_annotations(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=True)

    log.error("detection_failed", item="a", error="boom")
    log.warning("no_assets", item="b")
    log.info("unchanged", item="c")

    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("::error title=detection_failed::")
    assert lines[1].startswith("::warning title=no_assets::")
    assert lines[2].startswith("20")


def test_json_mode_never_emits_workflow_commands(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    buf = io.StringIO()
    logs.configure("audit_assets", stream=buf, level="INFO", fmt="json")

    logs.logger("test").error("boom")

    line = buf.getvalue().strip()
    assert not line.startswith("::")
    assert json.loads(line)["event"] == "boom"


def test_annotations_can_be_disabled_from_the_environment(monkeypatch):
    for name in ("LOG_LEVEL", "LOG_FORMAT", "LOG_ANNOTATIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("LOG_ANNOTATIONS", "0")
    buf = io.StringIO()
    logs.configure("run_checks", stream=buf, level="INFO", annotations=None)

    logs.logger("test").error("boom")

    assert not buf.getvalue().startswith("::error")


def test_credential_like_fields_are_redacted(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=False)

    log.error("auth_failed", bot_token="s3cret", api_hash="deadbeef",
              error="401")

    line = buf.getvalue()
    assert "s3cret" not in line
    assert "deadbeef" not in line
    assert "bot_token=***" in line
    assert "error=401" in line


def test_run_id_prefers_the_github_run_context(monkeypatch):
    for name in ("LOG_LEVEL", "LOG_FORMAT", "LOG_ANNOTATIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    buf = io.StringIO()
    logs.configure("run_checks", stream=buf, level="INFO", annotations=False)

    logs.logger("test").info("run_started")

    assert "[run_checks gh-123-2]" in buf.getvalue()


def test_field_values_never_split_a_line(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=False)

    log.error("delivery_failed", error="line one\nline two")

    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    assert 'error="line one line two"' in lines[0]


def test_annotation_escapes_workflow_command_syntax(monkeypatch):
    buf, log = _capture(monkeypatch, annotations=True)

    log.warning("flood_wait", error="50% done")

    line = buf.getvalue().strip()
    assert line.startswith("::warning title=flood_wait::")
    assert "50%25 done" in line
