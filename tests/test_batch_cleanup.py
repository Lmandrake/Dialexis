"""test_batch_cleanup.py - What reconcile's batch sweep may and may NOT delete.

Runs against a THROWAWAY temp model; never touches the live graph/.

This function calls shutil.rmtree, and a prior version of it silently destroyed
uncommitted work: it deleted STAGED batches because "all sources are archived" is true
the moment a batch is staged, long before it is committed. These tests pin the safety
boundary so that cannot regress.

  - INTEGRATED status        -> removed
  - verified commit manifest -> removed (entities confirmed on disk)
  - UNVERIFIABLE manifest    -> KEPT (entities absent = not actually integrated)
  - staged, no commit manifest -> KEPT (the historical data-loss case)
  - empty orphan, no manifests -> removed (provably nothing to lose)
  - manifest-less but NON-empty -> KEPT (may be an agent's in-flight fact records)
"""
import sys
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
import entity_io as eio
from naming import slug
from registry import load_registry


def _fresh():
    reg = load_registry()
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    if paths.BATCHES.exists():
        shutil.rmtree(paths.BATCHES)
    paths.BATCHES.mkdir(parents=True, exist_ok=True)


def _write_person(name):
    ent = eio.Entity(canonical_name=name, entity_type="person")
    d = paths.entity_dir("people")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug(name)}.md").write_text(eio.serialize(ent), encoding="utf-8")


def _batch(name, *, meta=None, commit=None, extra_files=None):
    b = paths.BATCHES / name
    b.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (b / "_manifest.json").write_text(json.dumps(meta), encoding="utf-8")
    if commit is not None:
        (b / "manifest.json").write_text(json.dumps(commit), encoding="utf-8")
    for fn in (extra_files or []):
        (b / fn).write_text("in-flight work", encoding="utf-8")
    return b


def _sweep():
    import reconcile
    return reconcile._cleanup_integrated_batches()


# --------------------------------------------------------------------------- #

def test_removes_integrated_and_verified():
    _fresh()
    _write_person("Ada Lovelace")
    done = _batch("b_status", meta={"status": "INTEGRATED"})
    verified = _batch("b_verified", commit={"operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Ada Lovelace"}]})

    n = _sweep()
    assert not done.exists(), "explicit INTEGRATED status must be cleaned up"
    assert not verified.exists(), "a commit manifest whose entities exist must be cleaned up"
    assert n == 2, f"expected 2 removals, got {n}"
    print("  ok removes INTEGRATED + verified-on-disk batches")


def test_keeps_unverifiable_and_staged():
    _fresh()
    # Manifest references an entity that does NOT exist -> not integrated -> keep.
    unverifiable = _batch("b_unverified", commit={"operations": [
        {"op": "upsert_entity", "entity_type": "person", "canonical_name": "Nobody At All"}]})
    # The historical data-loss case: staged, sources archived, no commit manifest yet.
    staged = _batch("b_staged", meta={"status": "STAGED", "sources": ["x.pdf"]})

    n = _sweep()
    assert unverifiable.exists(), "a manifest whose entities are absent must NOT be deleted"
    assert staged.exists(), \
        "REGRESSION: a STAGED batch with no commit manifest must never be deleted"
    assert n == 0, f"expected 0 removals, got {n}"
    print("  ok keeps unverifiable + staged batches (the data-loss guard)")


def test_removes_empty_orphan_keeps_nonempty():
    _fresh()
    # Empty, no manifests: cannot be a staged batch (stage_inbox always writes
    # _manifest.json) and holds nothing -> lossless to remove.
    empty = _batch("b_empty_orphan")
    # A dotfile alone should not count as content (Finder litter).
    dotted = _batch("b_dotfile_only")
    (dotted / ".DS_Store").write_text("x", encoding="utf-8")
    # No manifest but real content -> possibly in-flight agent work -> keep.
    nonempty = _batch("b_orphan_with_work", extra_files=["fact_records.md"])

    n = _sweep()
    assert not empty.exists(), "an empty manifest-less dir should be swept"
    assert not dotted.exists(), "a dir holding only dotfiles counts as empty"
    assert nonempty.exists(), \
        "a manifest-less dir that still holds files must be KEPT (may be in-flight work)"
    assert n == 2, f"expected 2 removals, got {n}"
    print("  ok sweeps empty orphans, spares manifest-less dirs that hold work")


def run_tests():
    with TemporaryDirectory() as tmpdir:
        paths.ROOT = Path(tmpdir)
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.BATCHES = paths.ROOT / "batches"
        paths.AGENTS = paths.ROOT / "agents"
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        for t in (test_removes_integrated_and_verified,
                  test_keeps_unverifiable_and_staged,
                  test_removes_empty_orphan_keeps_nonempty):
            print(f"- {t.__name__}")
            t()
        print("\nAll batch_cleanup tests passed")


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
