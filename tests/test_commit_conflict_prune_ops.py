"""test_commit_conflict_prune_ops.py - the resolve_conflict and prune_attribute commit ops.

Runs under pytest only (conftest.py re-points every path into a throwaway tree).

Covers:
  - resolve_conflict: flips an add_conflict entry to RESOLVED with its note, persisted
    to disk; HALTS (nothing written) when no conflict carries that title.
  - prune_attribute: removes exact keys from attributes or identifiers, leaving the
    rest; HALTS on an absent key, an absent entity, or a bad section, with the file
    unchanged.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths  # noqa: E402
import entity_io as eio  # noqa: E402
from commit import CommitError, commit_manifest  # noqa: E402
from naming import slug  # noqa: E402

NAME = "Op Test Person"


def _commit(*ops):
    return commit_manifest({"manifest_version": "2.0", "operations": list(ops)})


def _file():
    return paths.entity_dir("people") / f"{slug(NAME)}.md"


def _load():
    return eio.parse_file(_file())


def _seed():
    _commit({
        "op": "upsert_entity", "entity_type": "person", "canonical_name": NAME,
        "attributes": [{"key": "Role", "value": "Engineer", "source": "email"},
                       {"key": "Office", "value": "180-201", "source": "email"}],
        "identifiers": [{"key": "Email", "value": "op@example.org", "source": "email"},
                        {"key": "Phone", "value": "555-0100", "source": "email"}],
    })


# ---- resolve_conflict ---------------------------------------------------------------

def test_resolve_conflict_marks_resolved_with_note():
    _seed()
    _commit({"op": "add_conflict", "entity_type": "person", "canonical_name": NAME,
             "title": "Role disagreement", "value_a": "Engineer", "value_b": "Scientist"})
    [c] = _load().conflicts
    assert c["status"] == "UNRESOLVED"

    _commit({"op": "resolve_conflict", "entity_type": "person", "canonical_name": NAME,
             "title": "Role disagreement", "resolution_note": "Engineer per 2026 org chart"})
    [c] = _load().conflicts
    assert c["status"] == "RESOLVED"
    assert c["resolution_note"] == "Engineer per 2026 org chart"


def test_resolve_conflict_unknown_title_halts_and_writes_nothing():
    _seed()
    before = _file().read_text()
    with pytest.raises(CommitError, match="no conflict titled"):
        _commit({"op": "resolve_conflict", "entity_type": "person", "canonical_name": NAME,
                 "title": "No such conflict"})
    assert _file().read_text() == before


# ---- prune_attribute ----------------------------------------------------------------

def test_prune_attribute_removes_only_named_keys():
    _seed()
    _commit({"op": "prune_attribute", "entity_type": "person", "canonical_name": NAME,
             "keys": ["Office"]})
    e = _load()
    assert [f["key"] for f in e.attributes] == ["Role"]
    assert {f["key"] for f in e.identifiers} == {"Email", "Phone"}, "identifiers untouched"


def test_prune_attribute_identifiers_section():
    _seed()
    _commit({"op": "prune_attribute", "entity_type": "person", "canonical_name": NAME,
             "section": "identifiers", "keys": ["Phone"]})
    e = _load()
    assert [f["key"] for f in e.identifiers] == ["Email"]
    assert {f["key"] for f in e.attributes} == {"Role", "Office"}, "attributes untouched"


def test_prune_then_upsert_applies_new_value_in_one_manifest():
    """The org-chart pattern: prune the stale key, then upsert the new value, no HALT."""
    _seed()
    _commit(
        {"op": "prune_attribute", "entity_type": "person", "canonical_name": NAME,
         "keys": ["Role"]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": NAME,
         "attributes": [{"key": "Role", "value": "Group Supervisor", "source": "org_chart"}]},
    )
    role = [f for f in _load().attributes if f["key"] == "Role"]
    assert len(role) == 1 and role[0]["value"] == "Group Supervisor"


@pytest.mark.parametrize("op, match", [
    ({"keys": ["Nope"]}, "has no key"),
    ({"keys": ["Role"], "section": "events"}, "section must be"),
    ({"keys": []}, "non-empty list"),
    ({"keys": ["Role"], "canonical_name": "Nobody Here"}, "no entity named"),
])
def test_prune_attribute_guards_halt_and_write_nothing(op, match):
    _seed()
    before = _file().read_text()
    full = {"op": "prune_attribute", "entity_type": "person", "canonical_name": NAME, **op}
    with pytest.raises(CommitError, match=match):
        _commit(full)
    assert _file().read_text() == before


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
