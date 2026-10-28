"""test_resolve_cross_type.py - `resolve` must not imply an entity is absent when it
merely isn't a person.

Runs against a THROWAWAY temp model; never touches the live graph/.

Regression this covers: `run.py resolve "Goddard Space Flight Center"` printed

    No person resolved for 'Goddard Space Flight Center'.

for an organization that had been in the model for months. Read as "doesn't exist",
that message led straight to an upsert of a duplicate org and a commit HALT on
contradicting attributes. `resolve` is person-only by design (Capability 2 of the
access skill); the bug was that its miss message did not SAY so, and did not look.

Covers:
  - A name that exists only as a non-person is reported as such, with its type and path.
  - The output names the right tool to use instead (`run.py entity`).
  - The exit code still signals "no person resolved" (1), since that IS what happened.
  - A name that exists nowhere still reports a plain miss with no misleading hint.
  - A real person still resolves normally (no regression on the happy path).
"""
import io
import sys
import shutil
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
from commit import commit_manifest


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
    import index_store
    dbp = index_store._db_path()
    if dbp.exists():
        dbp.unlink()


def _resolve_output(query: str) -> tuple[str, int]:
    """Run the resolve CLI, capturing stdout and the exit code."""
    import resolve
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = resolve.main(["resolve", query])
    return buf.getvalue(), code


def _setup(tmpdir: str):
    paths.ROOT = Path(tmpdir)
    paths.WORKING_MODEL = paths.ROOT / "graph"
    paths.AGENTS = paths.ROOT / "agents"
    real_agents = Path(__file__).parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"
    _fresh_model()


def test_non_person_match_is_reported():
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        _commit([
            {"op": "upsert_entity", "entity_type": "organization",
             "canonical_name": "NASA Goddard Space Flight Center",
             "aliases": ["GSFC"], "status": "Active", "summary": "A NASA center"},
        ])

        out, code = _resolve_output("Goddard Space Flight Center")

        # Still "no person" -- that is the truth, and the exit code must keep saying it.
        assert code == 1, f"expected exit 1, got {code}"
        assert "person" in out.lower(), out

        # ...but it must surface the org rather than implying absence.
        assert "NASA Goddard Space Flight Center" in out, out
        assert "organization" in out, out
        # And point at the tool that searches every type.
        assert "entity" in out, out


def test_alias_match_is_reported():
    """The cross-check must see aliases, not just canonical names."""
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        _commit([
            {"op": "upsert_entity", "entity_type": "technology_system",
             "canonical_name": "QUARKLATE",
             "aliases": ["Quantized Relay Kernel for Lattice Encoding"],
             "status": "Active", "summary": "framework"},
        ])
        out, code = _resolve_output("Quantized Relay Kernel for Lattice Encoding")
        assert code == 1, out
        assert "QUARKLATE" in out, out
        assert "technology_system" in out, out


def test_true_miss_has_no_misleading_hint():
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        _commit([
            {"op": "upsert_entity", "entity_type": "organization",
             "canonical_name": "Some Unrelated Lab", "status": "Active", "summary": "x"},
        ])
        out, code = _resolve_output("Zzyzx Nonexistent Qqq")
        assert code == 1, out
        # No non-person section at all when genuinely nothing matches.
        assert "Some Unrelated Lab" not in out, out
        assert "non-person" not in out.lower(), out


def test_person_still_resolves():
    """Happy path must be untouched."""
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        _commit([
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Beatrix Thorncombe", "status": "Active",
             "summary": "fixture person"},
        ])
        out, code = _resolve_output("Beatrix Thorncombe")
        assert code == 0, f"expected clean resolve, got {code}: {out}"
        assert "Resolved" in out, out
        assert "Beatrix Thorncombe" in out, out


if __name__ == "__main__":
    test_non_person_match_is_reported()
    test_alias_match_is_reported()
    test_true_miss_has_no_misleading_hint()
    test_person_still_resolves()
    print("✓ resolve cross-type tests passed")
