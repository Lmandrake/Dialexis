"""smoke_test.py - Basic system verification on a throwaway copy."""
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
from registry import load_registry
from commit import commit_manifest
from reconcile import reconcile_all
from validate import validate_all
import entity_io as eio


def test_registry_load():
    """Test: registry loads and contains expected types."""
    print("Testing registry load...", end=" ")
    reg = load_registry()
    assert "person" in reg._entity_types
    assert "organization" in reg._entity_types
    assert reg.inverse_of("Member of") == "Has member"
    assert reg.default_confidence("manual") == "HIGH"
    print("✓")


def test_commit_writes_reciprocal():
    """Test: commit writes reciprocal edges automatically."""
    print("Testing reciprocal edge creation...", end=" ")

    manifest = {
        "manifest_version": "2.0",
        "operations": [
            {
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Alice Smith",
                "status": "Active",
                "summary": "Test person"
            },
            {
                "op": "upsert_entity",
                "entity_type": "organization",
                "canonical_name": "Test Org",
                "status": "Active",
                "summary": "Test organization"
            },
            {
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Alice Smith",
                "relationships": [
                    {
                        "rel_type": "Member of",
                        "target_type": "organization",
                        "target_name": "Test Org",
                        "source": "manual"
                    }
                ]
            }
        ]
    }

    commit_manifest(manifest)

    # Check Alice has relationship
    alice_path = paths.entity_dir("people") / "alice_smith.md"
    alice = eio.parse_file(alice_path)
    assert len(alice.relationships) == 1
    assert alice.relationships[0]["rel_type"] == "Member of"

    # Check Org has reciprocal
    org_path = paths.entity_dir("organizations") / "test_org.md"
    org = eio.parse_file(org_path)
    assert len(org.relationships) == 1
    assert org.relationships[0]["rel_type"] == "Has member"
    assert org.relationships[0]["target_name"] == "Alice Smith"

    print("✓")


def test_relationship_provenance():
    """Test: edges must name a source; `derived` discounts confidence and rides the reciprocal.

    19,539 edges accumulated with no provenance at all because this was silently optional, so
    the requirement is enforced rather than documented.
    """
    print("Testing relationship provenance...", end=" ")
    from commit import CommitError

    def rel(**kw):
        base = {"rel_type": "Member of", "target_type": "organization",
                "target_name": "Prov Org"}
        base.update(kw)
        return {"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": kw.pop("who", "Prov Person"), "relationships": [base]}]}

    # A missing source HALTS, and the message says what to do about it.
    try:
        commit_manifest(rel())
        assert False, "an edge with no source must HALT"
    except CommitError as e:
        assert "has no 'source'" in str(e) and "unknown" in str(e), str(e)

    # An unregistered source type HALTS.
    try:
        commit_manifest(rel(source="telepathy"))
        assert False, "an unregistered source type must HALT"
    except CommitError as e:
        assert "unknown source type" in str(e), str(e)

    # An unregistered derivation HALTS, even when confidence is stated explicitly (otherwise a
    # manifest could smuggle one past the gate by naming its own confidence).
    try:
        commit_manifest(rel(source="transcript", derived="hearsay", confidence="HIGH"))
        assert False, "an unregistered derivation must HALT"
    except CommitError as e:
        assert "unknown derivation type" in str(e), str(e)

    # `derived` drops the floor one step: transcript is MEDIUM, so via a summary it is LOW.
    commit_manifest(rel(who="Derived Person", source="transcript",
                        derived="meeting_summary"))
    person = eio.parse_file(paths.entity_dir("people") / "derived_person.md")
    edge = next(r for r in person.relationships if r["rel_type"] == "Member of")
    assert edge["source"] == "transcript", edge
    assert edge["derived"] == "meeting_summary", edge
    assert edge["confidence"] == "LOW", f"derived should discount MEDIUM to LOW, got {edge}"

    # Provenance travels to the reciprocal, or half of every pair stays unauditable.
    org = eio.parse_file(paths.entity_dir("organizations") / "prov_org.md")
    back = next(r for r in org.relationships if r["target_stem"] == "derived_person")
    assert back["source"] == "transcript" and back["derived"] == "meeting_summary", back

    # `unknown` upgrades when a real source is re-asserted, so the backfilled population drains.
    commit_manifest(rel(who="Upgrade Person", source="unknown"))
    commit_manifest(rel(who="Upgrade Person", source="email"))
    up = eio.parse_file(paths.entity_dir("people") / "upgrade_person.md")
    edge = next(r for r in up.relationships if r["rel_type"] == "Member of")
    assert edge["source"] == "email", f"unknown should upgrade to a real source, got {edge}"

    # Events take the same path. They were missed when `derived` was added, so an event read from
    # a summary kept the undiscounted floor — the overstatement the field exists to prevent,
    # surviving in one code path.
    def ev(**kw):
        return {"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Event Person", "events": [kw]}]}

    try:
        commit_manifest(ev(date="2026-09-13", desc="bad derivation", source="transcript",
                           derived="not_a_real_derivation"))
        assert False, "an unregistered derivation on an EVENT must HALT"
    except CommitError as e:
        assert "unknown derivation type" in str(e), str(e)

    commit_manifest(ev(date="2026-09-13", desc="read via a summary", source="transcript",
                       derived="meeting_summary"))
    ep = eio.parse_file(paths.entity_dir("people") / "event_person.md")
    got = next(x for x in ep.events if x["desc"] == "read via a summary")
    assert got["confidence"] == "LOW", f"event derivation must discount MEDIUM to LOW, got {got}"

    print("✓")


def test_perspective_must_be_attributed():
    """Test: a stance needs a distinct title and must say whose view it is.

    Perspectives are where attributed judgment belongs, and the 2026-09-12 recall measurement
    found attributed judgment was the largest category of dropped content. An untitled one
    silently swallows the next (title is the dedupe key); an unattributed one reads as fact.
    """
    print("Testing perspective attribution...", end=" ")
    from commit import CommitError

    def persp(**kw):
        return {"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Stance Holder", "perspectives": [kw]}]}

    for bad, expect in (
            ({"summary": "no title at all", "holder": "Stance Holder"}, "distinct 'title'"),
            ({"title": "Untitled", "holder": "Stance Holder"}, "distinct 'title'"),
            ({"title": "A real view", "summary": "but nobody holds it"}, "no 'holder'"),
    ):
        try:
            commit_manifest(persp(**bad))
            assert False, f"should have halted: {bad}"
        except CommitError as e:
            assert expect in str(e), f"wrong reason for {bad}: {e}"

    # Attributed to a person: accepted.
    commit_manifest(persp(title="Emulation is not one decision", holder="Stance Holder",
                          summary="Choosing the target is a science decision.",
                          evidence_type="stated", status="Active"))
    # Our own reading, declared as such rather than attributed: also accepted.
    commit_manifest(persp(title="Relevance to the programme",
                          text="Overlaps the emulator pipeline.", evidence_type="inferred"))
    e = eio.parse_file(paths.entity_dir("people") / "stance_holder.md")
    assert len(e.perspectives) == 2, e.perspectives
    titles = {p["title"] for p in e.perspectives}
    assert titles == {"Emulation is not one decision", "Relevance to the programme"}, titles

    # Re-asserting the same title does not duplicate it.
    commit_manifest(persp(title="Relevance to the programme",
                          text="Same title again.", evidence_type="inferred"))
    e = eio.parse_file(paths.entity_dir("people") / "stance_holder.md")
    assert len(e.perspectives) == 2, f"title is the dedupe key: {e.perspectives}"

    print("✓")


def test_validate_passes_clean():
    """Test: validate passes on a clean database."""
    print("Testing validation...", end=" ")
    results = validate_all()
    assert len(results["errors"]) == 0
    assert len(results["dangling_refs"]) == 0
    assert len(results["missing_reciprocals"]) == 0
    print("✓")


def test_contradiction_halts():
    """Test: contradicting value halts without add_conflict."""
    print("Testing contradiction detection...", end=" ")

    manifest = {
        "manifest_version": "2.0",
        "operations": [
            {
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Bob Jones",
                "attributes": [{"key": "role", "value": "Engineer"}]
            }
        ]
    }
    commit_manifest(manifest)

    # Try to contradict
    manifest2 = {
        "manifest_version": "2.0",
        "operations": [
            {
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Bob Jones",
                "attributes": [{"key": "role", "value": "Manager"}]
            }
        ]
    }

    from commit import CommitError
    try:
        commit_manifest(manifest2)
        assert False, "Should have raised CommitError"
    except CommitError as e:
        assert "contradicting values" in str(e)

    print("✓")


def test_email_case_insensitive():
    """Test: email identifiers compare case-insensitively and store lowercase."""
    print("Testing email case-insensitivity...", end=" ")

    commit_manifest({
        "manifest_version": "2.0",
        "operations": [{
            "op": "upsert_entity",
            "entity_type": "person",
            "canonical_name": "Case Tester",
            "identifiers": [{"key": "Email", "value": "Case.Tester@Example.COM"}],
        }],
    })

    path = paths.entity_dir("people") / "case_tester.md"
    emails = [f["value"] for f in eio.parse_file(path).identifiers if f["key"] == "Email"]
    assert emails == ["case.tester@example.com"], emails

    # Re-upserting the same address in a different case must NOT halt and must
    # NOT create a duplicate identifier (capitalization is never significant).
    from commit import CommitError
    try:
        commit_manifest({
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Case Tester",
                "identifiers": [{"key": "Email", "value": "CASE.TESTER@example.com"}],
            }],
        })
    except CommitError as e:
        assert False, f"case-only email difference must not halt, got: {e}"

    emails2 = [f["value"] for f in eio.parse_file(path).identifiers if f["key"] == "Email"]
    assert emails2 == ["case.tester@example.com"], emails2

    print("✓")


def test_rename_entity():
    """Test: rename_entity moves the file and repoints inbound references."""
    print("Testing entity rename...", end=" ")

    # Create an org and a person who is a Member of it.
    commit_manifest({
        "manifest_version": "2.0",
        "operations": [
            {"op": "upsert_entity", "entity_type": "organization",
             "canonical_name": "Old Lab Name", "status": "Active", "summary": "A lab"},
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Carol Ng", "status": "Active", "summary": "Works at [[organizations/old_lab_name|Old Lab Name]]",
             "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                                "target_name": "Old Lab Name"}]},
        ]
    })

    # Rename the org.
    commit_manifest({
        "manifest_version": "2.0",
        "operations": [
            {"op": "rename_entity", "entity_type": "organization",
             "old_name": "Old Lab Name", "new_name": "New Lab Name"},
        ]
    })

    orgs = paths.entity_dir("organizations")
    assert not (orgs / "old_lab_name.md").exists(), "old file should be deleted"
    assert (orgs / "new_lab_name.md").exists(), "new file should exist"

    # Carol's relationship must now point at the new stem/name.
    carol = eio.parse_file(paths.entity_dir("people") / "carol_ng.md")
    rel = next(r for r in carol.relationships if r["rel_type"] == "Member of")
    assert rel["target_stem"] == "new_lab_name", rel
    assert rel["target_name"] == "New Lab Name", rel
    # Inline prose wikilink repointed too, with label upgraded.
    assert "[[organizations/new_lab_name|New Lab Name]]" in carol.summary, carol.summary

    # Reciprocal on the renamed org still resolves (Has member -> Carol Ng).
    org = eio.parse_file(orgs / "new_lab_name.md")
    assert any(r["rel_type"] == "Has member" and r["target_stem"] == "carol_ng"
               for r in org.relationships), org.relationships

    # No dangling refs / missing reciprocals introduced.
    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]
    assert not results["missing_reciprocals"], results["missing_reciprocals"]

    # Renaming onto an existing entity must be refused (that's a merge).
    from commit import CommitError
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "organization",
         "canonical_name": "Collision Org", "status": "Active", "summary": "x"}]})
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "rename_entity", "entity_type": "organization",
             "old_name": "New Lab Name", "new_name": "Collision Org"}]})
        assert False, "should refuse rename onto existing entity"
    except CommitError as e:
        assert "MERGE" in str(e), str(e)

    print("✓")


def test_name_upgrade_repoints_inbound():
    """Test: upserting a fuller name (Bob Q Lee) over an existing shorter variant
    (Bob Lee) upgrades the file AND repoints inbound references, leaving no dangling
    edge behind. Regression for the implicit-upgrade path skipping _repoint_refs."""
    print("Testing implicit name-upgrade repoint...", end=" ")

    people = paths.entity_dir("people")

    # Short-name person, referenced by an org (creates a reciprocal inbound edge).
    commit_manifest({
        "manifest_version": "2.0",
        "operations": [
            {"op": "upsert_entity", "entity_type": "organization",
             "canonical_name": "Zeta Lab", "status": "Active", "summary": "A lab"},
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Bob Lee", "status": "Active",
             "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                                "target_name": "Zeta Lab"}]},
        ]
    })
    assert (people / "bob_lee.md").exists()

    # Upsert the fuller name -> triggers _find_existing_variant -> upgrade bob_lee.
    commit_manifest({
        "manifest_version": "2.0",
        "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Bob Q Lee", "status": "Active"},
        ]
    })

    assert not (people / "bob_lee.md").exists(), "old short-name file should be deleted"
    assert (people / "bob_q_lee.md").exists(), "upgraded file should exist"

    # The org's inbound edge must now point at the upgraded stem, not the deleted one.
    org = eio.parse_file(paths.entity_dir("organizations") / "zeta_lab.md")
    rel = next(r for r in org.relationships if r["rel_type"] == "Has member")
    assert rel["target_stem"] == "bob_q_lee", rel
    assert rel["target_name"] == "Bob Q Lee", rel

    # No orphaned edges introduced by the upgrade.
    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]
    assert not results["missing_reciprocals"], results["missing_reciprocals"]

    print("✓")


def test_merge_entity():
    """Test: merge_entity folds a duplicate into a keeper and repoints inbound refs."""
    print("Testing entity merge...", end=" ")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "organization", "canonical_name": "Keep Org",
         "status": "Active", "summary": "The canonical org",
         "attributes": [{"key": "Focus", "value": "Science"}]},
        {"op": "upsert_entity", "entity_type": "organization", "canonical_name": "Dup Org",
         "status": "Active", "summary": "A duplicate", "aliases": ["DO"],
         "attributes": [{"key": "Founded", "value": "1990"}]},
        # A person is a Member of the DUPLICATE — this edge must survive the merge.
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Dave Kim",
         "status": "Active", "summary": "Cites [[organizations/dup_org|Dup Org]]",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                            "target_name": "Dup Org"}]},
    ]})

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "merge_entity", "entity_type": "organization",
         "keep_name": "Keep Org", "merge_name": "Dup Org"},
    ]})

    orgs = paths.entity_dir("organizations")
    assert not (orgs / "dup_org.md").exists(), "duplicate file should be deleted"
    keep = eio.parse_file(orgs / "keep_org.md")
    # Duplicate name + alias folded in.
    assert "Dup Org" in keep.aliases and "DO" in keep.aliases, keep.aliases
    # Non-colliding attribute folded in.
    assert any(a["key"] == "Founded" for a in keep.attributes), keep.attributes

    # Dave now points at the keeper (structured + prose), and reciprocity holds.
    dave = eio.parse_file(paths.entity_dir("people") / "dave_kim.md")
    rel = next(r for r in dave.relationships if r["rel_type"] == "Member of")
    assert rel["target_stem"] == "keep_org", rel
    assert "[[organizations/keep_org|Keep Org]]" in dave.summary, dave.summary
    assert any(r["rel_type"] == "Has member" and r["target_stem"] == "dave_kim"
               for r in keep.relationships), keep.relationships

    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]
    assert not results["missing_reciprocals"], results["missing_reciprocals"]

    print("✓")


def test_prune_relationship():
    """Test: prune_relationship removes an edge AND its reciprocal, and HALTs on a missing edge."""
    print("Testing relationship prune...", end=" ")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "organization", "canonical_name": "Prune Org",
         "status": "Active", "summary": "org"},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Erin Fox",
         "status": "Active", "summary": "person",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                            "target_name": "Prune Org"}]},
    ]})

    # Sanity: edge and reciprocal both exist before the prune.
    people = paths.entity_dir("people")
    orgs = paths.entity_dir("organizations")
    erin = eio.parse_file(people / "erin_fox.md")
    assert eio.relationship_present(erin.relationships, "Member of", "prune_org")
    org = eio.parse_file(orgs / "prune_org.md")
    assert eio.relationship_present(org.relationships, "Has member", "erin_fox")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "prune_relationship", "entity_type": "person", "canonical_name": "Erin Fox",
         "rel_type": "Member of", "target_type": "organization", "target_name": "Prune Org"},
    ]})

    # Both sides gone; no dangling refs / missing reciprocals introduced.
    erin = eio.parse_file(people / "erin_fox.md")
    assert not eio.relationship_present(erin.relationships, "Member of", "prune_org"), erin.relationships
    org = eio.parse_file(orgs / "prune_org.md")
    assert not eio.relationship_present(org.relationships, "Has member", "erin_fox"), org.relationships
    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]
    assert not results["missing_reciprocals"], results["missing_reciprocals"]

    # Pruning an absent edge must HALT (typo guard).
    from commit import CommitError
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "prune_relationship", "entity_type": "person", "canonical_name": "Erin Fox",
             "rel_type": "Member of", "target_type": "organization", "target_name": "Prune Org"}]})
        assert False, "should HALT pruning a non-existent edge"
    except CommitError as e:
        assert "has no" in str(e), str(e)

    print("✓")


def test_reclassify_after_dirtying_source():
    """Test: reclassify_entity works when an EARLIER op in the same manifest already
    modified the source entity.

    Regression: reclassify_entity's retype-in-place branch popped the source path from
    _cache but only discarded it from _dirty in the merge branch. A manifest that dirtied
    the entity first (e.g. prune_relationship, then reclassify) left a path in _dirty with
    no _cache entry, and the write loop died with a bare KeyError -- surfacing to the user
    as "commit failed, rolled back cleanly: PosixPath(...)" with no usable diagnosis.
    """
    print("Testing reclassify after dirtying source...", end=" ")

    projects = paths.entity_dir("projects")
    concepts = paths.entity_dir("technology_concepts")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "organization", "canonical_name": "Host Org",
         "status": "Active", "summary": "the host"},
        {"op": "upsert_entity", "entity_type": "project", "canonical_name": "Retype Me",
         "status": "Active", "summary": "a thing mistyped as a project",
         "relationships": [{"rel_type": "Hosted by", "source": "manual", "target_type": "organization",
                            "target_name": "Host Org"}]},
    ]})
    assert (projects / "retype_me.md").exists()

    # The regression case: prune an edge (dirties projects/retype_me.md) and reclassify it
    # to another type in the SAME manifest.
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "prune_relationship", "entity_type": "project", "canonical_name": "Retype Me",
         "rel_type": "Hosted by", "target_type": "organization", "target_name": "Host Org"},
        {"op": "reclassify_entity", "from_type": "project", "from_name": "Retype Me",
         "to_type": "technology_concept", "to_name": "Retype Me"},
    ]})

    assert not (projects / "retype_me.md").exists(), "old typed file should be gone"
    assert (concepts / "retype_me.md").exists(), "retyped file should exist"

    moved = eio.parse_file(concepts / "retype_me.md")
    assert moved.entity_type == "technology_concept", moved.entity_type
    assert not eio.relationship_present(moved.relationships, "Hosted by", "host_org"), \
        moved.relationships

    # The reciprocal must be gone from the org too, and nothing left dangling.
    org = eio.parse_file(paths.entity_dir("organizations") / "host_org.md")
    assert not eio.relationship_present(org.relationships, "Hosts", "retype_me"), org.relationships
    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]
    assert not results["missing_reciprocals"], results["missing_reciprocals"]

    # Same guard for rename_entity, which shares the pop-from-_cache shape.
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "organization", "canonical_name": "Rename Host",
         "status": "Active", "summary": "host"},
        {"op": "upsert_entity", "entity_type": "project", "canonical_name": "Rename Me",
         "status": "Active", "summary": "p",
         "relationships": [{"rel_type": "Hosted by", "source": "manual", "target_type": "organization",
                            "target_name": "Rename Host"}]},
    ]})
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "prune_relationship", "entity_type": "project", "canonical_name": "Rename Me",
         "rel_type": "Hosted by", "target_type": "organization", "target_name": "Rename Host"},
        {"op": "rename_entity", "entity_type": "project",
         "old_name": "Rename Me", "new_name": "Renamed Already"},
    ]})
    assert not (projects / "rename_me.md").exists(), "old name should be gone"
    assert (projects / "renamed_already.md").exists(), "new name should exist"
    results = validate_all()
    assert not results["dangling_refs"], results["dangling_refs"]

    print("✓")


def test_prune_event():
    """Test: prune_event removes matched events by (date, desc) and HALTs on any non-match."""
    print("Testing event prune...", end=" ")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Fay Lin",
         "status": "Active", "summary": "person",
         "events": [
             {"date": "2025", "desc": "Stale claim to remove", "source": "manual"},
             {"date": "2026", "desc": "Real event to keep", "source": "manual"},
         ]},
    ]})

    people = paths.entity_dir("people")
    fay = eio.parse_file(people / "fay_lin.md")
    assert {(e["date"], e["desc"]) for e in fay.events} == {
        ("2025", "Stale claim to remove"), ("2026", "Real event to keep")}

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "prune_event", "entity_type": "person", "canonical_name": "Fay Lin",
         "events": [{"date": "2025", "desc": "Stale claim to remove"}]},
    ]})

    fay = eio.parse_file(people / "fay_lin.md")
    keys = {(e["date"], e["desc"]) for e in fay.events}
    assert ("2025", "Stale claim to remove") not in keys, keys
    assert ("2026", "Real event to keep") in keys, keys

    # A spec that matches no event must HALT (typo guard) — nothing else removed.
    from commit import CommitError
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "prune_event", "entity_type": "person", "canonical_name": "Fay Lin",
             "events": [{"date": "2026", "desc": "Wrong description"}]}]})
        assert False, "should HALT pruning a non-matching event"
    except CommitError as e:
        assert "no event" in str(e), str(e)
    fay = eio.parse_file(people / "fay_lin.md")
    assert ("2026", "Real event to keep") in {(e["date"], e["desc"]) for e in fay.events}

    print("✓")


def test_prune_alias():
    """Test: prune_alias removes exact aliases and HALTs on any alias that is not present."""
    print("Testing alias prune...", end=" ")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "project", "canonical_name": "Alias Proj",
         "status": "Active", "summary": "project",
         "aliases": ["Real Alias", "Junk1", "Junk2"]},
    ]})

    projects = paths.entity_dir("projects")
    proj = eio.parse_file(projects / "alias_proj.md")
    assert set(proj.aliases) == {"Real Alias", "Junk1", "Junk2"}, proj.aliases

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "prune_alias", "entity_type": "project", "canonical_name": "Alias Proj",
         "aliases": ["Junk1", "Junk2"]},
    ]})

    proj = eio.parse_file(projects / "alias_proj.md")
    assert set(proj.aliases) == {"Real Alias"}, proj.aliases

    # An alias that is not present must HALT (typo guard) — nothing else removed.
    from commit import CommitError
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "prune_alias", "entity_type": "project", "canonical_name": "Alias Proj",
             "aliases": ["Nonexistent"]}]})
        assert False, "should HALT pruning a non-present alias"
    except CommitError as e:
        assert "no alias" in str(e), str(e)
    proj = eio.parse_file(projects / "alias_proj.md")
    assert set(proj.aliases) == {"Real Alias"}, proj.aliases

    print("✓")


def test_reconcile_preserves_staged_batch():
    """Test: reconcile never deletes an uncommitted STAGED batch, but still cleans INTEGRATED."""
    print("Testing staged-batch preservation...", end=" ")
    from reconcile import _cleanup_integrated_batches

    orig_batches = paths.BATCHES
    paths.BATCHES = paths.ROOT / "batches"
    try:
        paths.BATCHES.mkdir(parents=True, exist_ok=True)

        # A freshly staged batch: sources already archived (the always-true trap), no manifest.
        staged = paths.BATCHES / "20260101_000000"
        staged.mkdir()
        archived = paths.ROOT / "archive" / "raw_inbox" / "src.pdf"
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text("x")
        (staged / "_manifest.json").write_text(json.dumps(
            {"status": "STAGED", "files": [{"archive": "archive/raw_inbox/src.pdf"}]}))

        # An explicitly finalized batch: should be cleaned.
        integrated = paths.BATCHES / "20260101_111111"
        integrated.mkdir()
        (integrated / "_manifest.json").write_text(json.dumps({"status": "INTEGRATED"}))

        removed = _cleanup_integrated_batches()

        assert staged.exists(), "STAGED uncommitted batch must NOT be deleted"
        assert not integrated.exists(), "INTEGRATED batch should be cleaned"
        assert removed == 1, removed
    finally:
        paths.BATCHES = orig_batches

    print("✓")


def test_merge_fragments():
    """Test: merge_fragments folds parallel subagent fragments and auto-flags disagreements."""
    print("Testing fragment merge...", end=" ")
    from merge_fragments import merge_operations

    # Two fragments touching the same entity: agree on the org edge, disagree on Role,
    # and differ only by case on Email (must NOT conflict).
    fragment_a = [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Merge Target",
         "summary": "Short.", "status": "Active",
         "attributes": [{"key": "Role", "value": "Engineer", "confidence": "HIGH", "source": "pdf"}],
         "identifiers": [{"key": "Email", "value": "MT@example.com", "confidence": "HIGH"}],
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                            "target_name": "Merge Org", "confidence": "HIGH"}]},
    ]
    fragment_b = [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Merge Target",
         "summary": "A distinctly longer and more informative summary.",
         "attributes": [{"key": "Role", "value": "Scientist", "confidence": "MEDIUM", "source": "email"}],
         "identifiers": [{"key": "Email", "value": "mt@example.com", "confidence": "MEDIUM"}],
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization",
                            "target_name": "Merge Org", "confidence": "HIGH"}]},
        {"op": "merge_entity", "entity_type": "project", "keep_name": "Keep", "merge_name": "Dup"},
    ]

    merged, conflicts = merge_operations(fragment_a + fragment_b)

    upserts = [o for o in merged if o["op"] == "upsert_entity"]
    assert len(upserts) == 1, "same entity across fragments folds into one upsert"
    person = upserts[0]

    # duplicate identical relationship deduped to a single edge
    assert len(person["relationships"]) == 1, person["relationships"]
    # longer summary wins
    assert person["summary"].startswith("A distinctly longer"), person["summary"]
    # email case-only difference is NOT a conflict; one canonicalized identifier survives
    assert len(person["identifiers"]) == 1, person["identifiers"]
    assert person["identifiers"][0]["value"] == "mt@example.com", person["identifiers"]
    # Role divergence keeps the HIGH-confidence value...
    role = [a for a in person["attributes"] if a["key"] == "Role"][0]
    assert role["value"] == "Engineer", role
    # ...and emits exactly one add_conflict documenting it
    assert len(conflicts) == 1, conflicts
    auto = [o for o in merged if o["op"] == "add_conflict"]
    assert len(auto) == 1 and auto[0]["value_a"] == "Engineer" and auto[0]["value_b"] == "Scientist", auto
    # non-upsert ops pass through
    assert any(o["op"] == "merge_entity" for o in merged), merged

    # The merged manifest must commit cleanly end-to-end (the org and both entities are real).
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "project", "canonical_name": "Keep",
         "status": "Active", "summary": "keeper"},
        {"op": "upsert_entity", "entity_type": "project", "canonical_name": "Dup",
         "status": "Active", "summary": "duplicate"},
    ]})
    commit_manifest({"manifest_version": "2.0", "operations": merged})
    person_file = paths.entity_dir("people") / "merge_target.md"
    assert person_file.exists(), "merged upsert wrote the entity"
    reloaded = eio.parse_file(person_file)
    assert any(c.get("title", "").startswith("Role disagreement") for c in reloaded.conflicts), \
        "auto-flagged conflict is recorded on the entity"

    print("✓")


def test_reconcile_idempotent():
    """Test: reconcile is idempotent."""
    print("Testing reconcile idempotence...", end=" ")
    stats1 = reconcile_all()
    stats2 = reconcile_all()
    # Second run should normalize same count but add no reciprocals
    assert stats2["reciprocals_added"] == 0
    print("✓")


def test_temporal_module():
    """Test: temporal.py default-inference + staleness are correct pure functions."""
    print("Testing temporal module...", end=" ")
    import temporal
    from datetime import date

    # Fact inference: present-tense state -> mutable; dated interval / identity -> permanent;
    # explicit temporariness -> volatile.
    assert temporal.infer_temporal_class("Employment", "Works at Halloway") == "mutable"
    assert temporal.infer_temporal_class("Tenure", "worked 2001-2006") == "permanent"
    assert temporal.infer_temporal_class("Birth date", "1980-01-01") == "permanent"
    assert temporal.infer_temporal_class("Assignment", "Acting lead this quarter") == "volatile"
    # Relationship inference: authorship/citation fixed; membership drifts.
    assert temporal.infer_rel_temporal_class("Authored by") == "permanent"
    assert temporal.infer_rel_temporal_class("Member of") == "mutable"

    # Staleness thresholds (mutable ~18mo, volatile ~5mo, permanent never).
    now = date(2026, 1, 1)
    assert temporal.is_stale("2020-01-01", "mutable", now) is True
    assert temporal.is_stale("2020-01-01", "permanent", now) is False
    assert temporal.is_stale("2025-12-01", "mutable", now) is False
    assert temporal.is_stale("2025-01-01", "volatile", now) is True   # >150d
    # Partial + malformed stamps.
    assert temporal.parse_stamp("2026") == date(2026, 1, 1)
    assert temporal.is_well_formed_stamp("2026-08-28") is True
    assert temporal.is_well_formed_stamp("not-a-date") is False
    # Missing stamp is never a candidate (we can't honestly judge its age).
    assert temporal.is_stale("", "mutable", now) is False
    print("✓")


def test_temporal_commit_stamps_and_infers():
    """Test: commit stamps last_verified, infers temporal_class, honors explicit values,
    and refuses a manifest-supplied last_verified or an illegal temporal_class."""
    print("Testing temporal stamping + inference...", end=" ")
    from datetime import date
    from commit import CommitError

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Tanya Vol",
         "status": "Active", "summary": "t",
         "attributes": [
             {"key": "Employment", "value": "Works at Halloway", "source": "manual"},
             {"key": "Tenure", "value": "worked 2001-2006", "source": "manual"},
             # explicit permanent must override the 'currently' volatile inference
             {"key": "Override", "value": "Works here currently", "source": "manual",
              "temporal_class": "permanent"},
         ]},
    ]})

    e = eio.parse_file(paths.entity_dir("people") / "tanya_vol.md")
    by = {f["key"]: f for f in e.attributes}
    today = date.today().isoformat()
    assert all(f.get("last_verified") == today for f in e.attributes), e.attributes
    assert by["Employment"]["temporal_class"] == "mutable", by["Employment"]
    assert by["Tenure"]["temporal_class"] == "permanent", by["Tenure"]
    assert by["Override"]["temporal_class"] == "permanent", by["Override"]

    # A manifest may not set last_verified (it is mechanical).
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Tanya Vol",
             "attributes": [{"key": "X", "value": "y", "last_verified": "2020-01-01"}]}]})
        assert False, "should reject manifest-supplied last_verified"
    except CommitError as ex:
        assert "last_verified" in str(ex), ex

    # An illegal temporal_class halts.
    try:
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Tanya Vol",
             "attributes": [{"key": "Z", "value": "w", "temporal_class": "eternal"}]}]})
        assert False, "should reject an unknown temporal_class"
    except CommitError as ex:
        assert "temporal_class" in str(ex), ex

    # A relationship also gets stamped + classified.
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "document", "canonical_name": "Paper Q",
         "status": "Active", "summary": "a paper"},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Tanya Vol",
         "relationships": [{"rel_type": "Authored by", "source": "manual", "target_type": "document",
                            "target_name": "Paper Q"}]},
    ]})
    e = eio.parse_file(paths.entity_dir("people") / "tanya_vol.md")
    rel = next(r for r in e.relationships if r["rel_type"] == "Authored by")
    assert rel["temporal_class"] == "permanent", rel   # authorship is fixed
    assert rel["last_verified"] == today, rel
    print("✓")


def test_temporal_reinforcement_sticky():
    """Test: re-asserting a fact without a temporal_class keeps the curated one (does not
    silently reset it to the inferred default)."""
    print("Testing temporal stickiness on reinforcement...", end=" ")

    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Ray Sticky",
         "status": "Active", "summary": "r",
         # 'Engineer' would infer mutable; curate it volatile explicitly.
         "attributes": [{"key": "Role", "value": "Engineer", "source": "manual",
                         "temporal_class": "volatile"}]},
    ]})
    # Re-assert the same value, silent on temporal_class.
    commit_manifest({"manifest_version": "2.0", "operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Ray Sticky",
         "attributes": [{"key": "Role", "value": "Engineer", "source": "manual"}]},
    ]})
    e = eio.parse_file(paths.entity_dir("people") / "ray_sticky.md")
    role = next(f for f in e.attributes if f["key"] == "Role")
    assert role["temporal_class"] == "volatile", role   # curated value preserved
    print("✓")


def test_serialize_canonical_and_idempotent():
    """Test: legacy facts (no new fields) serialize byte-identically; new fields serialize
    in canonical order; and serialize(parse(...)) is idempotent."""
    print("Testing canonical serialization...", end=" ")
    e = eio.Entity(canonical_name="Legacy One", entity_type="person",
                   status="Active", summary="s")
    e.last_updated = "2026-01-01"
    e.attributes = [{"key": "Role", "value": "Engineer",
                     "confidence": "HIGH", "source": "pdf", "date": "2026"}]
    text = eio.serialize(e)
    # No churn for a legacy fact already in canonical order.
    assert "- Role: Engineer [confidence=HIGH, source=pdf, date=2026]" in text, text

    # Idempotence: parse then re-serialize is byte-identical.
    p = paths.WORKING_MODEL / "people" / "legacy_one.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    assert eio.serialize(eio.parse_file(p)) == text, "serialize must be idempotent"

    # New fields render in canonical order regardless of dict insertion order.
    e.attributes = [{"key": "Loc", "value": "Pasadena", "date": "2026", "source": "manual",
                     "last_verified": "2026-08-28", "confidence": "HIGH",
                     "temporal_class": "mutable"}]
    line = next(l for l in eio.serialize(e).splitlines() if l.startswith("- Loc:"))
    assert line == ("- Loc: Pasadena [confidence=HIGH, temporal_class=mutable, "
                    "source=manual, date=2026, last_verified=2026-08-28]"), line
    print("✓")


def run_tests():
    """Run all tests in a temporary workspace."""
    with TemporaryDirectory() as tmpdir:
        # Set up paths to use temp directory
        paths.ROOT = Path(tmpdir)
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.AGENTS = paths.ROOT / "agents"

        # Copy registry to temp location
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        # Create entity directories
        reg = load_registry()
        for entity_type, info in reg._entity_types.items():
            paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)

        # Run tests
        test_registry_load()
        test_commit_writes_reciprocal()
        test_relationship_provenance()
        test_perspective_must_be_attributed()
        test_validate_passes_clean()
        test_contradiction_halts()
        test_email_case_insensitive()
        test_rename_entity()
        test_name_upgrade_repoints_inbound()
        test_merge_entity()
        test_prune_relationship()
        test_reclassify_after_dirtying_source()
        test_prune_event()
        test_prune_alias()
        test_merge_fragments()
        test_reconcile_preserves_staged_batch()
        test_reconcile_idempotent()
        test_temporal_module()
        test_temporal_commit_stamps_and_infers()
        test_temporal_reinforcement_sticky()
        test_serialize_canonical_and_idempotent()

        print("\n✓ All tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
