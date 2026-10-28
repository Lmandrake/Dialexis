"""test_commit_perspective_note_prune.py - prune_perspective, prune_note, and order-aware
person-name resolution.

Runs under pytest only (conftest.py re-points every path into a throwaway tree).

Covers:
  - prune_perspective: removes by exact title or by a {field: value} match spec (the route
    for duplicated "Untitled" ones); HALTS, file unchanged, on absent, ambiguous, or a
    spec naming the same perspective twice.
  - prune_note: removes exactly one paragraph or bullet per substring, keeping the rest
    byte-for-byte; HALTS, file unchanged, on absent or ambiguous substrings.
  - prune then re-add in one manifest (the retitle path).
  - name resolution after a merge_entity in the same manifest resolves against the
    post-merge state instead of halting on an ambiguity the merge removed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths  # noqa: E402
import entity_io as eio  # noqa: E402
from commit import CommitError, commit_manifest  # noqa: E402
from naming import slug  # noqa: E402

NAME = "Prune Test Person"


def _commit(*ops):
    return commit_manifest({"manifest_version": "2.0", "operations": list(ops)})


def _file(name=NAME):
    return paths.entity_dir("people") / f"{slug(name)}.md"


def _load(name=NAME):
    return eio.parse_file(_file(name))


def _p(title, claim, holder="Someone"):
    return {"title": title, "holder": holder, "claim": claim, "evidence_type": "stated",
            "confidence": "HIGH", "source": "manual", "date": "2026-06-30"}


def _seed(notes=None):
    _commit({"op": "upsert_entity", "entity_type": "person", "canonical_name": NAME,
             "perspectives": [_p("Alpha view", "A is good"), _p("Beta view", "B is bad")],
             **({"notes": notes} if notes else {})})


def _seed_untitled_pair():
    """Two stored 'Untitled' perspectives, as legacy files carry them (commit refuses to
    create these, so write the file directly in the throwaway tree)."""
    _seed()
    text = _file().read_text().replace("**Alpha view**", "**Untitled**").replace(
        "**Beta view**", "**Untitled**")
    _file().write_text(text)


# ---- prune_perspective ----------------------------------------------------------------

def test_prune_perspective_by_title_removes_only_that_one():
    _seed()
    _commit({"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
             "titles": ["Alpha view"]})
    assert [p["title"] for p in _load().perspectives] == ["Beta view"]


def test_prune_perspective_match_spec_targets_one_of_two_untitled():
    _seed_untitled_pair()
    _commit({"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
             "match": [{"Title": "Untitled", "Claim": "B is bad"}]})
    [left] = _load().perspectives
    assert left["claim"] == "A is good"


def test_prune_perspective_ambiguous_title_halts_and_writes_nothing():
    _seed_untitled_pair()
    before = _file().read_text()
    with pytest.raises(CommitError, match="matches 2 perspectives"):
        _commit({"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
                 "titles": ["Untitled"]})
    assert _file().read_text() == before


def test_prune_perspective_absent_halts_and_writes_nothing():
    _seed()
    before = _file().read_text()
    with pytest.raises(CommitError, match="no perspective matching"):
        _commit({"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
                 "titles": ["Alpha view", "Gamma view"]})
    assert _file().read_text() == before


def test_prune_perspective_same_target_twice_halts():
    _seed()
    with pytest.raises(CommitError, match="already names"):
        _commit({"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
                 "titles": ["Alpha view"], "match": [{"claim": "A is good"}]})


def test_prune_then_readd_retitles_in_one_manifest():
    _seed_untitled_pair()
    _commit(
        {"op": "prune_perspective", "entity_type": "person", "canonical_name": NAME,
         "match": [{"title": "Untitled", "claim": "A is good"},
                   {"title": "Untitled", "claim": "B is bad"}]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": NAME,
         "perspectives": [_p("A is good", "A is good"), _p("B is bad", "B is bad")]})
    assert sorted(p["title"] for p in _load().perspectives) == ["A is good", "B is bad"]


# ---- prune_note -----------------------------------------------------------------------

NOTES = ("First paragraph about apples.\n\n"
         "Lead line\n- bullet one about pears\n- bullet two about plums\n  continued\n\n"
         "Last paragraph about apples and figs.")


def test_prune_note_removes_one_paragraph_keeps_rest_exact():
    _seed(NOTES)
    _commit({"op": "prune_note", "entity_type": "person", "canonical_name": NAME,
             "contains": ["about apples and figs"]})
    assert _load().notes == ("First paragraph about apples.\n\n"
                             "Lead line\n- bullet one about pears\n- bullet two about plums\n"
                             "  continued")


def test_prune_note_removes_one_bullet_with_continuation():
    _seed(NOTES)
    _commit({"op": "prune_note", "entity_type": "person", "canonical_name": NAME,
             "contains": ["plums"]})
    assert _load().notes == ("First paragraph about apples.\n\n"
                             "Lead line\n- bullet one about pears\n\n"
                             "Last paragraph about apples and figs.")


def test_prune_note_last_unit_leaves_none():
    _seed("Only note here.")
    _commit({"op": "prune_note", "entity_type": "person", "canonical_name": NAME,
             "contains": ["Only note"]})
    assert _load().notes.strip() in ("", "(none)")


def test_prune_note_ambiguous_halts_and_writes_nothing():
    _seed(NOTES)
    before = _file().read_text()
    with pytest.raises(CommitError, match="occurs in 2"):
        _commit({"op": "prune_note", "entity_type": "person", "canonical_name": NAME,
                 "contains": ["apples"]})
    assert _file().read_text() == before


def test_prune_note_absent_halts_and_writes_nothing():
    _seed(NOTES)
    before = _file().read_text()
    with pytest.raises(CommitError, match="contains 'kiwis'"):
        _commit({"op": "prune_note", "entity_type": "person", "canonical_name": NAME,
                 "contains": ["pears", "kiwis"]})
    assert _file().read_text() == before


# ---- order-aware name resolution ------------------------------------------------------

def test_upsert_after_merge_resolves_against_post_merge_state():
    for n in ("Pat Q Tester", "Pat R Tester"):
        _commit({"op": "upsert_entity", "entity_type": "person", "canonical_name": n,
                 "summary": n})
    bare = {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Pat Tester",
            "attributes": [{"key": "Role", "value": "Engineer", "source": "manual"}]}
    with pytest.raises(CommitError, match="Ambiguous person reference"):
        _commit(dict(bare))
    _commit({"op": "merge_entity", "entity_type": "person",
             "keep_name": "Pat Q Tester", "merge_name": "Pat R Tester"}, dict(bare))
    assert not _file("Pat R Tester").exists()
    assert not _file("Pat Tester").exists()
    assert any(f["key"] == "Role" for f in _load("Pat Q Tester").attributes)
