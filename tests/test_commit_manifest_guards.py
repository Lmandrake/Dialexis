"""test_commit_manifest_guards.py - commit.py's manifest-level guards.

Runs under pytest only (conftest.py re-points every path into a throwaway tree).

Covers:
  - UNRESOLVED: a non-empty top-level `unresolved` list HALTS with nothing written,
    and the CLI prints each item and exits nonzero. An empty list is a no-op.
  - PATH: a relative manifest path that does not exist under the CWD falls back to
    the repo root, so `run.py commit batches/<id>/manifest.json` works from anywhere.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths  # noqa: E402
import commit  # noqa: E402
from commit import CommitError, commit_manifest  # noqa: E402
from naming import slug  # noqa: E402

_OP = {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Guard Test Person"}


def _person_file():
    return paths.entity_dir("people") / f"{slug('Guard Test Person')}.md"


def test_unresolved_halts_and_writes_nothing():
    m = {"operations": [_OP], "unresolved": ["unknown rel 'Mentors'", {"name": "X", "why": "no type"}]}
    with pytest.raises(CommitError) as ei:
        commit_manifest(m)
    msg = str(ei.value)
    assert "2 unresolved" in msg and "Mentors" in msg and "no type" in msg
    assert not _person_file().exists(), "an unresolved manifest must write nothing"


def test_unresolved_cli_exits_nonzero_and_prints_items(capsys):
    mp = paths.ROOT / "m.json"
    mp.write_text(json.dumps({"operations": [_OP], "unresolved": ["needs registry type 'lab'"]}))
    assert commit.main(["commit.py", str(mp)]) == 1
    assert "needs registry type 'lab'" in capsys.readouterr().err
    assert not _person_file().exists()


def test_empty_unresolved_is_ignored():
    commit_manifest({"operations": [_OP], "unresolved": []})
    assert _person_file().exists()


def test_relative_manifest_path_falls_back_to_repo_root(tmp_path):
    bdir = paths.ROOT / "batches" / "b1"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(json.dumps({"operations": [_OP]}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cwd = os.getcwd()
    try:
        os.chdir(elsewhere)
        assert commit.main(["commit.py", "batches/b1/manifest.json"]) == 0
    finally:
        os.chdir(cwd)
    assert _person_file().exists()


def test_cwd_relative_path_still_wins(tmp_path):
    (tmp_path / "m.json").write_text(json.dumps({"operations": [_OP]}))
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert commit.resolve_manifest_path("m.json") == Path("m.json")
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
