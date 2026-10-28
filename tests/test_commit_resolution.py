"""test_commit_resolution.py - Deterministic contextual person resolution at commit.

Runs against a THROWAWAY temp model; never touches the live graph/. Covers
the upgrade that makes commit.py resolve bare/ambiguous person names before applying:

  - CONTEXTUAL RESOLVE: bare "Jasper" as a relationship target resolves to the one
    Jasper linked to DELTA (via the op's own edge), rewriting the edge to the canonical.
  - AMBIGUOUS -> HALT: two Jaspers both on DELTA -> commit refuses (no duplicate made).
  - SAME-INSTITUTION CONFOUNDER -> HALT: two Cordelia Wickhams both at Halloway, no
    distinguishing context -> refuse.
  - CROSS-INSTITUTION RESOLVE: Cordelia Wickham at Halloway vs MIT, org context "Halloway"
    (explicit context block) -> resolves to the Halloway one.
  - FILL-IN DEDUP: "Odalys Ferncastle" -> existing "Odalys D Ferncastle".
  - NO DOWNGRADE / NEW PERSON: a genuinely new unambiguous name commits unchanged.
"""
import sys
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
from naming import slug
from commit import commit_manifest, CommitError


def _people():
    return paths.entity_dir("people")


def _commit(ops):
    return commit_manifest({"manifest_version": "2.0", "operations": ops})


def _fresh_model():
    from registry import load_registry
    reg = load_registry()
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    idx = paths.WORKING_MODEL / "_index"
    if idx.exists():
        shutil.rmtree(idx)


# --------------------------------------------------------------------------- #

def test_contextual_resolve_by_edge():
    _fresh_model()
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Lyle",
         "relationships": [{"rel_type": "Works on", "source": "manual", "target_type": "project", "target_name": "DELTA"}]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Poe",
         "relationships": [{"rel_type": "Works on", "source": "manual", "target_type": "project", "target_name": "Zeta"}]},
    ])
    # Reference bare "Jasper" as an attendee of an DELTA-linked event.
    _commit([
        {"op": "upsert_entity", "entity_type": "event", "canonical_name": "DELTA Science Team Meeting",
         "relationships": [
             {"rel_type": "Part of", "source": "manual", "target_type": "project", "target_name": "DELTA"},
             {"rel_type": "Attended by", "source": "manual", "target_type": "person", "target_name": "Jasper"}]},
    ])
    import entity_io as eio
    ev = eio.parse_file(paths.entity_dir("events") / f"{slug('DELTA Science Team Meeting')}.md")
    attendees = [r["target_name"] for r in ev.relationships if r["rel_type"] == "Attended by"]
    assert "Jasper Lyle" in attendees, f"bare 'Jasper' should resolve to Jasper Lyle, got {attendees}"
    assert not (_people() / "jasper.md").exists(), "no bare 'Jasper' entity should be created"
    print("  ok contextual resolve: bare 'Jasper' + DELTA edge -> Jasper Lyle")


def test_ambiguous_halts():
    _fresh_model()
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Lyle",
         "relationships": [{"rel_type": "Works on", "source": "manual", "target_type": "project", "target_name": "DELTA"}]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Poe",
         "relationships": [{"rel_type": "Works on", "source": "manual", "target_type": "project", "target_name": "DELTA"}]},
    ])
    halted = False
    try:
        _commit([
            {"op": "upsert_entity", "entity_type": "event", "canonical_name": "DELTA Sync",
             "relationships": [
                 {"rel_type": "Part of", "source": "manual", "target_type": "project", "target_name": "DELTA"},
                 {"rel_type": "Attended by", "source": "manual", "target_type": "person", "target_name": "Jasper"}]},
        ])
    except CommitError as e:
        halted = True
        assert "Jasper" in str(e) and "Ambiguous" in str(e)
    assert halted, "two Jaspers both on DELTA must HALT, not silently pick/create"
    print("  ok ambiguous 'Jasper' (two on DELTA) -> HALT")


def test_same_institution_confounder_halts():
    _fresh_model()
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia C Wickham",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization", "target_name": "Halloway Institute"}]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia E Wickham",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization", "target_name": "Halloway Institute"}]},
    ])
    halted = False
    try:
        _commit([
            {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia Wickham",
             "context": {"orgs": ["Halloway Institute"]},
             "attributes": [{"key": "Role", "value": "scientist", "source": "manual", "confidence": "HIGH"}]},
        ])
    except CommitError:
        halted = True
    assert halted, "two same-institution Cordelia Wickhams must HALT under only-Halloway context"
    print("  ok same-institution confounder -> HALT")


def test_cross_institution_resolves_by_org():
    _fresh_model()
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia C Wickham",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization", "target_name": "Halloway Institute"}]},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia E Wickham",
         "relationships": [{"rel_type": "Member of", "source": "manual", "target_type": "organization", "target_name": "MIT"}]},
    ])
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Cordelia Wickham",
         "context": {"orgs": ["Halloway Institute"]},
         "attributes": [{"key": "Role", "value": "scientist", "source": "manual", "confidence": "HIGH"}]},
    ])
    # Should have merged into the Halloway Cordelia (C), not created a bare 'cordelia_wickham.md'.
    assert not (_people() / "cordelia_wickham.md").exists(), "bare Cordelia Wickham should have resolved to the Halloway one"
    import entity_io as eio
    cw = eio.parse_file(_people() / f"{slug('Cordelia C Wickham')}.md")
    assert any(a["key"] == "Role" for a in cw.attributes), "the Role fact should land on Cordelia C Wickham (Halloway)"
    print("  ok cross-institution resolve by org -> Cordelia C Wickham (Halloway)")


def test_fill_in_dedup():
    _fresh_model()
    _commit([{"op": "upsert_entity", "entity_type": "person", "canonical_name": "Odalys D Ferncastle"}])
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Odalys Ferncastle",
         "attributes": [{"key": "Title", "value": "PI", "source": "manual", "confidence": "HIGH"}]},
    ])
    assert not (_people() / "odalys_ferncastle.md").exists(), "shorter name should resolve to the existing fuller record"
    import entity_io as eio
    u = eio.parse_file(_people() / f"{slug('Odalys D Ferncastle')}.md")
    assert any(a["key"] == "Title" for a in u.attributes), "fact should land on the existing Odalys D Ferncastle"
    print("  ok fill-in dedup: 'Odalys Ferncastle' -> 'Odalys D Ferncastle'")


def test_new_unambiguous_name_commits():
    _fresh_model()
    _commit([{"op": "upsert_entity", "entity_type": "person", "canonical_name": "Zaphod Beeblebrox",
              "attributes": [{"key": "Role", "value": "president", "source": "manual", "confidence": "HIGH"}]}])
    assert (_people() / f"{slug('Zaphod Beeblebrox')}.md").exists(), "a genuinely new unambiguous person must commit unchanged"
    print("  ok new unambiguous name commits unchanged")


def run_tests():
    with TemporaryDirectory() as tmpdir:
        paths.ROOT = Path(tmpdir)
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.AGENTS = paths.ROOT / "agents"
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        for t in (test_contextual_resolve_by_edge, test_ambiguous_halts,
                  test_same_institution_confounder_halts, test_cross_institution_resolves_by_org,
                  test_fill_in_dedup, test_new_unambiguous_name_commits):
            print(f"- {t.__name__}")
            t()
        print("\nAll commit_resolution tests passed")


def main(argv=None):
    try:
        run_tests()
        return 0
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
