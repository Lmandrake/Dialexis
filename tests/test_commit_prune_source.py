"""test_commit_prune_source.py - the prune_source commit op.

Runs under pytest only (conftest.py re-points every path into a throwaway tree).

Covers: removes Sources entries by exact archive path, keeping the rest; HALTS, file
unchanged, on an absent archive or an empty list.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths  # noqa: E402
import entity_io as eio  # noqa: E402
from commit import CommitError, commit_manifest  # noqa: E402
from naming import slug  # noqa: E402

NAME = "Source Prune Person"
GOOD = "archive/raw_inbox/20260101_000000_note.md"
BAD = "archive/raw_inbox/20260101_000000_archive/raw_inbox/20260101_000000_note.md"


def _commit(*ops):
    return commit_manifest({"manifest_version": "2.0", "operations": list(ops)})


def _file():
    return paths.entity_dir("people") / f"{slug(NAME)}.md"


def _seed():
    _commit({"op": "upsert_entity", "entity_type": "person", "canonical_name": NAME,
             "sources": [{"name": "Note", "archive": GOOD}, {"name": "Note", "archive": BAD}]})


def _prune(archives):
    return {"op": "prune_source", "entity_type": "person", "canonical_name": NAME,
            "archives": archives}


def test_prune_source_removes_exact_archive_only():
    _seed()
    _commit(_prune([BAD]))
    assert [s["archive"] for s in eio.parse_file(_file()).sources] == [GOOD]


@pytest.mark.parametrize("archives", [["archive/raw_inbox/absent.md"], []])
def test_prune_source_halts_and_leaves_file(archives):
    _seed()
    before = _file().read_text()
    with pytest.raises(CommitError):
        _commit(_prune(archives))
    assert _file().read_text() == before
