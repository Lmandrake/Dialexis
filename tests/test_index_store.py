"""test_index_store.py - Tests for the derived SQLite index + parse cache.

Runs entirely against a THROWAWAY temp model (mirrors test_entity_matching.py);
NEVER touches the live graph/. Covers the load-bearing guarantees:

  - PARITY: iter_entities() reconstructs entities byte-identical (via serialize)
    to a live eio.parse_file over the same tree.
  - LOOKUP: lookup_index() returns every entity; fuzzy_match_entities finds one.
  - STALENESS: add / delete a file -> is_stale() flips; lookup_index self-heals.
  - PARSE-CACHE SAFETY: when a file's (mtime,size) changes, iter_entities
    re-parses it from disk (never serves a stale blob) — the property validate
    depends on.
  - FALLBACK: with no DB present, lookup_index() and iter_entities() still work.
  - CACHE BYPASS: validate --no-cache reads truth even when a blob is corrupt.
"""
import sys
import time
import shutil
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
import entity_io as eio
from naming import slug
from registry import load_registry


def _write(entity_type, name, *, status="Active", summary="", aliases=None,
           relationships=None):
    ent = eio.Entity(canonical_name=name, entity_type=entity_type, status=status,
                     summary=summary, aliases=aliases or [],
                     relationships=relationships or [])
    reg = load_registry()
    d = paths.entity_dir(reg.directory_for(entity_type))
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug(name)}.md"
    p.write_text(eio.serialize(ent), encoding="utf-8")
    return p


def _fresh_model():
    reg = load_registry()
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    idx = paths.WORKING_MODEL / "_index"
    if idx.exists():
        shutil.rmtree(idx)
    # index.sqlite is machine-local now (paths.index_cache_dir), so rmtree'ing
    # _index/ no longer clears it — drop it explicitly or the previous test's rows
    # leak into this one.
    import index_store
    dbp = index_store._db_path()
    if dbp.exists():
        dbp.unlink()


def _seed():
    _write("person", "Alice Zheng", summary="A researcher.")
    _write("person", "Bob Marley Smith", aliases=["Bob Smith"])
    _write("organization", "Halloway Institute", aliases=["Halloway"])


@pytest.fixture(autouse=True)
def _seed_model(_isolate_paths):
    """Per-test fresh+seed, matching run_tests()'s loop. Depends on the conftest
    `_isolate_paths` fixture so paths are already re-pointed at the tmp tree
    before _fresh_model() clears entity dirs. (Under standalone `python3`, this
    fixture is inert — run_tests() drives setup instead.)"""
    _fresh_model()
    _seed()


# --------------------------------------------------------------------------- #

def test_parity_iter_vs_parse():
    """iter_entities (cache) must reproduce exactly what parse_file yields."""
    import index_store
    index_store.rebuild()

    cached = {e.path: eio.serialize(e) for e in index_store.iter_entities()}
    assert cached, "iter_entities returned nothing"

    # Live parse of the same tree.
    reg = load_registry()
    live = {}
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            for p in d.glob("*.md"):
                live[p] = eio.serialize(eio.parse_file(p))

    assert set(cached) == set(live), "path sets differ"
    for p in live:
        assert cached[p] == live[p], f"blob != parse for {p.name}"
    print("  ok parity iter_entities == parse_file")


def test_lookup_index_and_fuzzy():
    import index_store
    from entity_matching import fuzzy_match_entities
    index_store.rebuild()

    rows = index_store.lookup_index()
    names = {r["name"] for r in rows}
    assert {"Alice Zheng", "Bob Marley Smith", "Halloway Institute"} <= names

    # alias carried through
    jpl = next(r for r in rows if r["name"] == "Halloway Institute")
    assert "Halloway" in jpl["aliases"]

    m = fuzzy_match_entities("Alice Zheng", threshold=0.6)
    assert m and m[0]["entity"] == "Alice Zheng"
    print("  ok lookup_index + fuzzy_match_entities")


def test_staleness_add_delete():
    import index_store
    index_store.rebuild()
    assert index_store.is_stale() is False, "fresh rebuild should not be stale"

    # Add a file the cache doesn't know about -> stale.
    p = _write("person", "Carol Danvers")
    assert index_store.is_stale() is True, "new file should make cache stale"

    # lookup_index self-heals (rebuilds) and includes the new entity.
    names = {r["name"] for r in index_store.lookup_index()}
    assert "Carol Danvers" in names
    assert index_store.is_stale() is False

    # Delete a file -> stale again; self-heal drops it.
    p.unlink()
    assert index_store.is_stale() is True
    names = {r["name"] for r in index_store.lookup_index()}
    assert "Carol Danvers" not in names
    print("  ok staleness add/delete + self-heal")


def test_parse_cache_reparses_on_change():
    """The safety property: a changed file is re-read from disk, never served
    from the stale cached blob."""
    import index_store
    p = _write("person", "Dave Grohl", status="Active")
    index_store.rebuild()

    # Confirm the cache currently serves status=Active.
    got = {e.canonical_name: e for e in index_store.iter_entities()}
    assert got["Dave Grohl"].status == "Active"

    # Edit the file on disk (content + size change; mtime advances).
    time.sleep(0.01)
    ent = eio.parse_file(p)
    ent.status = "Inactive"
    ent.summary = "changed on disk to alter size"
    p.write_text(eio.serialize(ent), encoding="utf-8")

    # iter_entities must reflect the on-disk truth, NOT the cached blob.
    got2 = {e.canonical_name: e for e in index_store.iter_entities()}
    assert got2["Dave Grohl"].status == "Inactive", "stale blob served after edit!"
    assert "changed on disk" in got2["Dave Grohl"].summary
    print("  ok parse cache re-parses a changed file (no stale blob)")


def test_fallback_without_db():
    """No DB present -> lookup_index() and iter_entities() still work (slow path)."""
    import index_store
    dbp = index_store._db_path()
    if dbp.exists():
        dbp.unlink()

    rows = index_store.lookup_index()  # rebuilds since stale/missing
    assert {"Alice Zheng"} <= {r["name"] for r in rows}

    # Simulate sqlite being unavailable by pointing _connect at an unwritable dir?
    # Simpler: delete DB and ensure iter_entities parses fresh.
    if dbp.exists():
        dbp.unlink()
    n = sum(1 for _ in index_store.iter_entities())
    assert n >= 3
    print("  ok fallback works without a DB")


def test_validate_no_cache_reads_truth():
    """validate --no-cache (use_cache=False) reads disk even if a blob is corrupt."""
    import index_store
    import validate as V
    index_store.rebuild()

    # Corrupt a cached blob WITHOUT touching the file (mtime,size still match).
    con = sqlite3.connect(str(index_store._db_path()))
    bad = eio.Entity(canonical_name="Alice Zheng", entity_type="person",
                     status="BOGUS_STATUS")
    con.execute("UPDATE entities SET blob_json=? WHERE name='Alice Zheng'",
                (index_store._entity_to_blob(bad),))
    con.commit()
    con.close()

    # use_cache=True would trust the (matching mtime/size) corrupt blob and warn.
    cached = V.validate_all(use_cache=True)
    # use_cache=False re-parses from disk -> the real, valid status.
    truth = V.validate_all(use_cache=False)
    bogus_warn = [w for w in cached["warnings"] if "BOGUS_STATUS" in w]
    assert bogus_warn, "expected the corrupt cached blob to surface under use_cache"
    assert not [w for w in truth["warnings"] if "BOGUS_STATUS" in w], \
        "--no-cache must read disk truth, not the corrupt blob"
    print("  ok validate --no-cache bypasses the cache and reads truth")


def run_tests():
    with TemporaryDirectory() as tmpdir:
        paths.ROOT = Path(tmpdir)
        # Re-pointing WORKING_MODEL also relocates the derived sqlite cache into
        # this throwaway tree (paths.index_cache_dir), so the run is hermetic.
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.AGENTS = paths.ROOT / "agents"
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        for t in (test_parity_iter_vs_parse, test_lookup_index_and_fuzzy,
                  test_staleness_add_delete, test_parse_cache_reparses_on_change,
                  test_fallback_without_db, test_validate_no_cache_reads_truth):
            _fresh_model()
            _seed()
            print(f"- {t.__name__}")
            t()

    print("\nAll index_store tests passed")


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
