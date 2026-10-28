"""test_distinct_from.py - The adjudicated "these are NOT the same person" edge.

Runs against a THROWAWAY temp model; never touches the live graph/. This
replaces test_ambiguity_cache.py: the durable half of that cache (a human's ruling
that two same-named records are two real people) is now a registry-symmetric
`Distinct from` relationship on the entities themselves, so it is git-tracked,
reciprocity-enforced by commit.py, and validated like any other fact.

Covers:
  - RECIPROCITY: committing `Distinct from` on one person writes it back on the
    other automatically (symmetric in the registry -> Python owns the postcondition).
  - READ: distinct_from_pairs() surfaces the pair once, from either direction.
  - DEDUPER: analyze_database.find_ambiguities stops proposing an adjudicated pair,
    so the same judgment is never re-litigated.
  - RESOLVER: resolve_person reports an adjudicated collision as settled ambiguity
    (narrow by context) rather than a suspected duplicate.
"""
import sys
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
from naming import slug
from commit import commit_manifest


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
    # The sqlite cache is machine-local now, so rmtree'ing _index/ no longer clears
    # it — drop it explicitly or the previous test's rows leak into this one.
    import index_store
    dbp = index_store._db_path()
    if dbp.exists():
        dbp.unlink()


def _seed_two_walkers():
    """Two genuinely distinct Cordelia Wickhams, both at Halloway (the hard case: same
    institution, distinguishable only by middle initial)."""
    _commit([
        {"op": "upsert_entity", "entity_type": "person",
         "canonical_name": "Cordelia C Wickham",
         "relationships": [{"rel_type": "Member of", "target_type": "organization",
                            "source": "manual",
                            "target_name": "Halloway Institute"}]},
        {"op": "upsert_entity", "entity_type": "person",
         "canonical_name": "Cordelia E Wickham",
         "relationships": [{"rel_type": "Member of", "target_type": "organization",
                            "source": "manual",
                            "target_name": "Halloway Institute"}]},
    ])


def _adjudicate(a: str, b: str):
    """Record the human ruling that `a` and `b` are different people."""
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": a,
         "relationships": [{"rel_type": "Distinct from", "target_type": "person",
                            "source": "manual", "target_name": b}]},
    ])


# --------------------------------------------------------------------------- #

def test_commit_writes_reciprocal():
    """Symmetric in the registry -> the committer writes the back-edge itself."""
    _fresh_model()
    _seed_two_walkers()
    _adjudicate("Cordelia C Wickham", "Cordelia E Wickham")

    import entity_io as eio
    other = eio.parse_file(_people() / f"{slug('Cordelia E Wickham')}.md")
    back = [r for r in other.relationships if r["rel_type"] == "Distinct from"]
    assert back, "commit.py must write the reciprocal `Distinct from` on the target"
    assert back[0]["target_name"] == "Cordelia C Wickham", \
        f"reciprocal should point back at the source, got {back[0]['target_name']}"
    print("  ok commit writes the reciprocal Distinct from edge")


def test_distinct_from_pairs_reads_the_pair():
    _fresh_model()
    _seed_two_walkers()
    import entity_matching as em

    assert em.distinct_from_pairs() == set(), "no edges yet -> no adjudicated pairs"
    _adjudicate("Cordelia C Wickham", "Cordelia E Wickham")

    pairs = em.distinct_from_pairs()
    expected = frozenset({f"people/{slug('Cordelia C Wickham')}",
                          f"people/{slug('Cordelia E Wickham')}"})
    assert pairs == {expected}, f"expected exactly {expected}, got {pairs}"
    print("  ok distinct_from_pairs surfaces the pair once (symmetric, deduped)")


def test_deduper_stops_reproposing():
    """A settled pair must drop out of the duplicate candidates the deduper sees."""
    _fresh_model()
    # Substring-similar names ("jasper lyle" is inside "jasper lyleson") -> flagged forever
    # by find_ambiguities unless the ruling is recorded.
    _commit([
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Lyle"},
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Jasper Lyleson"},
    ])
    import analyze_database as ad
    from registry import load_registry
    reg = load_registry()

    def _flagged():
        found = ad.find_ambiguities(reg)
        return [(s["name1"], s["name2"]) for s in found["similar_names"]]

    before = _flagged()
    assert any({a, b} == {"jasper lyle", "jasper lyleson"} for a, b in before), \
        f"the pair should be proposed before adjudication, got {before}"

    _adjudicate("Jasper Lyle", "Jasper Lyleson")

    after = _flagged()
    assert not any({a, b} == {"jasper lyle", "jasper lyleson"} for a, b in after), \
        f"an adjudicated pair must NOT be re-proposed, got {after}"
    print("  ok adjudicated pair is never handed to the deduper again")


def test_resolver_reports_adjudicated():
    _fresh_model()
    _seed_two_walkers()
    import entity_matching as em

    plain = em.resolve_person("Cordelia Wickham")
    assert plain.requires_disambiguation, "two contradicting middles -> ambiguous"
    assert "ADJUDICATED" not in plain.rationale, \
        "nothing adjudicated yet, so the resolver must not claim it was"

    _adjudicate("Cordelia C Wickham", "Cordelia E Wickham")

    after = em.resolve_person("Cordelia Wickham")
    assert after.requires_disambiguation, \
        "adjudication does NOT remove the ambiguity — it confirms it is real"
    assert after.resolved is None, "the resolver must still refuse to pick silently"
    assert "ADJUDICATED" in after.rationale, \
        f"expected the settled-ambiguity note, got: {after.rationale}"
    print("  ok resolver reports settled ambiguity, still refuses to guess")


def run_tests():
    with TemporaryDirectory() as tmpdir:
        paths.ROOT = Path(tmpdir)
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.AGENTS = paths.ROOT / "agents"
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        for t in (test_commit_writes_reciprocal, test_distinct_from_pairs_reads_the_pair,
                  test_deduper_stops_reproposing, test_resolver_reports_adjudicated):
            print(f"- {t.__name__}")
            t()
        print("\nAll distinct_from tests passed")


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
