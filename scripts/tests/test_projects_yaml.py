"""Invariants for the shipped watch list.

These catch manifest mistakes that no unit test would otherwise see: a tag
that no longer matches its id, an empty description, an accidental duplicate
repository, or a file that grew trailing whitespace. They run offline.
"""

import re
from collections import Counter
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MAX_DESCRIPTION = 40

# Two entries may track one repository when they deliver different assets.
_ALLOWED_DUPLICATE_REPOS = {"FongMi/Release"}


def _items():
    data = yaml.safe_load((_ROOT / "projects.yaml").read_text(encoding="utf-8"))
    return data["projects"]


def test_ids_are_unique_and_well_formed():
    ids = [item["id"] for item in _items()]

    assert len(ids) == len(set(ids)), "duplicate id"
    assert all(_ID_RE.match(pid) for pid in ids), ids


def test_tags_are_derived_from_the_id():
    bad = [
        (item["id"], item["notify"]["tag"])
        for item in _items()
        if item["notify"]["tag"] != "#" + item["id"].replace("-", "_")
    ]

    assert bad == [], f"tag must be '#' + id with '-' -> '_': {bad}"


def test_descriptions_are_present_and_short():
    bad = [
        (item["id"], len(item["notify"]["description"]))
        for item in _items()
        if not item["notify"]["description"].strip()
        or len(item["notify"]["description"]) > _MAX_DESCRIPTION
    ]

    assert bad == [], f"description must be 1..{_MAX_DESCRIPTION} chars: {bad}"


def test_only_known_repositories_appear_twice():
    dupes = {
        repo for repo, count in Counter(i["repo"] for i in _items()).items()
        if count > 1
    }

    assert dupes <= _ALLOWED_DUPLICATE_REPOS, dupes - _ALLOWED_DUPLICATE_REPOS


def test_project_urls_point_at_their_repository():
    bad = [
        item["id"] for item in _items()
        if item["notify"]["project_url"]
        != "https://github.com/" + item["repo"]
    ]

    assert bad == []


def test_file_has_no_trailing_whitespace():
    text = (_ROOT / "projects.yaml").read_text(encoding="utf-8")

    assert text.endswith("\n") and not text.endswith("\n\n")
    assert all(line == line.rstrip() for line in text.splitlines())
