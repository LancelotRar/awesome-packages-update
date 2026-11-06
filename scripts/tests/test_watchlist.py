"""Unit tests for ``watchlist`` — manifest loading and validation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchlist import STRATEGY_LATEST, STRATEGY_TAG, load_watchlist


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_items_in_file_order(tmp_path):
    path = _write(tmp_path, """
projects:
  - id: webhtv
    repo: webhtv/webhtv
    strategy: latest
    asset_include: ".apk"
    notify:
      title: "WebHomeTV"
  - id: fongmi-module
    repo: FongMi/Release
    strategy: tag
    release_tag: module
    asset_exclude: "thunder"
""")

    items = load_watchlist(path)

    assert [i.id for i in items] == ["webhtv", "fongmi-module"]
    assert items[0].strategy == STRATEGY_LATEST
    assert items[0].notify.title == "WebHomeTV"
    assert items[1].strategy == STRATEGY_TAG
    assert items[1].release_tag == "module"
    assert items[1].asset_exclude == "thunder"


def test_duplicate_id_rejected(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: dup, repo: a/b, strategy: latest}
  - {id: dup, repo: c/d, strategy: latest}
""")

    with pytest.raises(RuntimeError, match="duplicate id 'dup'"):
        load_watchlist(path)


def test_unknown_strategy_rejected(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: rss}
""")

    with pytest.raises(RuntimeError, match="strategy 'rss'"):
        load_watchlist(path)


def test_tag_strategy_requires_release_tag(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: tag}
""")

    with pytest.raises(RuntimeError, match="requires 'release_tag'"):
        load_watchlist(path)


def test_latest_strategy_rejects_release_tag(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest, release_tag: module}
""")

    with pytest.raises(RuntimeError, match="only valid with strategy 'tag'"):
        load_watchlist(path)


def test_release_tag_include_parsed_on_latest(tmp_path):
    path = _write(tmp_path, """
projects:
  - id: bitwarden-desktop
    repo: bitwarden/clients
    strategy: latest
    release_tag_include: ["desktop-*", "*-desktop"]
""")

    items = load_watchlist(path)

    assert items[0].release_tag_include == ("desktop-*", "*-desktop")


def test_release_tag_include_defaults_to_empty(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest}
""")

    assert load_watchlist(path)[0].release_tag_include == ()


def test_release_tag_include_rejected_on_tag_strategy(tmp_path):
    path = _write(tmp_path, """
projects:
  - id: x
    repo: a/b
    strategy: tag
    release_tag: module
    release_tag_include: ["desktop-*"]
""")

    with pytest.raises(
        RuntimeError,
        match="'release_tag_include' is only valid with strategy 'latest'",
    ):
        load_watchlist(path)


def test_release_tag_include_rejects_explicit_empty_list(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest, release_tag_include: []}
""")

    with pytest.raises(RuntimeError,
                       match="'release_tag_include' must not be an empty list"):
        load_watchlist(path)


def test_release_tag_include_must_be_a_list(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest, release_tag_include: "desktop-*"}
""")

    with pytest.raises(RuntimeError,
                       match="'release_tag_include' must be a list"):
        load_watchlist(path)


def test_release_tag_include_rejects_non_string_entries(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest, release_tag_include: ["desktop-*", 1]}
""")

    with pytest.raises(RuntimeError,
                       match="'release_tag_include' entries must be non-empty"):
        load_watchlist(path)


def test_unknown_project_key_rejected(tmp_path):
    path = _write(tmp_path, """
projects:
  - {id: x, repo: a/b, strategy: latest, asset_incldue: ".apk"}
""")

    with pytest.raises(RuntimeError, match="unknown key"):
        load_watchlist(path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="Watch list not found"):
        load_watchlist(tmp_path / "absent.yaml")


def test_empty_projects_rejected(tmp_path):
    path = _write(tmp_path, "projects: []\n")

    with pytest.raises(RuntimeError, match="non-empty list"):
        load_watchlist(path)
