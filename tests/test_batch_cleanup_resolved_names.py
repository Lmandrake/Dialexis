"""test_batch_cleanup_resolved_names.py - reconcile must sweep a committed batch even
when commit.py RESOLVED a name in the manifest to a fuller stored form.

Runs against a THROWAWAY temp model; never touches the live graph/.

Regression this covers: `_batch_is_integrated()` verified integration by testing
`slug(canonical_name)` literally. But commit.py resolves person names first — a manifest
saying "Kellan Mortimer" writes `people/kellan_l_mortimer.md`. The literal check then looks for
`people/kellan_mortimer.md`, finds nothing, declares the batch un-integrated, and the
directory is never swept. Silent and cumulative: every batch containing a name the
resolver upgraded (common) leaks a directory, and "Batches cleaned: 0" gives no hint why.

Nothing is lost when this happens, so it is a hygiene bug rather than a data bug — which
is exactly why it went unnoticed.
"""
import sys
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths


def _setup(tmpdir: str):
    paths.ROOT = Path(tmpdir)
    paths.WORKING_MODEL = paths.ROOT / "graph"
    paths.AGENTS = paths.ROOT / "agents"
    paths.BATCHES = paths.ROOT / "batches"
    real_agents = Path(__file__).parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    from registry import load_registry
    reg = load_registry()
    for _etype, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)
    paths.BATCHES.mkdir(parents=True, exist_ok=True)
    import index_store
    dbp = index_store._db_path()
    if dbp.exists():
        dbp.unlink()


def test_batch_with_resolved_name_is_swept():
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        from commit import commit_manifest
        import reconcile

        # Seed the fuller stored form the resolver will match against.
        commit_manifest({"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Kellan L Mortimer", "status": "Active", "summary": "seeded"},
        ]})
        assert (paths.entity_dir("people") / "kellan_l_mortimer.md").exists()

        # A batch whose manifest uses the SHORTER name. commit resolves it onto the
        # stored fuller record, so no people/kellan_mortimer.md is ever written.
        batch = paths.BATCHES / "20260101_000000"
        batch.mkdir(parents=True)
        manifest = {"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Kellan Mortimer", "status": "Active",
             "attributes": [{"key": "Role - Advisor", "value": "Example contact",
                             "confidence": "MEDIUM", "source": "pdf"}]},
        ]}
        (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (batch / "_manifest.json").write_text(
            json.dumps({"batch_id": "20260101_000000", "status": "STAGED", "files": []}),
            encoding="utf-8")

        # NOTE: pass a COPY. commit.py rewrites op["canonical_name"] in place when it
        # resolves a name, so reusing the same dict would hide the bug — reconcile reads
        # manifest.json back from DISK, where the unresolved short name is still written.
        commit_manifest(json.loads(json.dumps(manifest)))

        # Sanity: the short slug genuinely does not exist; the fuller one absorbed it.
        assert not (paths.entity_dir("people") / "kellan_mortimer.md").exists()
        assert (paths.entity_dir("people") / "kellan_l_mortimer.md").exists()

        # The batch IS integrated and must be recognized as such, judged from the
        # on-disk manifest exactly as reconcile does it.
        on_disk = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk["operations"][0]["canonical_name"] == "Kellan Mortimer", \
            "the on-disk manifest must still hold the unresolved name"
        assert reconcile._batch_is_integrated(on_disk) is True, \
            "resolved-name batch should count as integrated"

        removed = reconcile._cleanup_integrated_batches()
        assert removed == 1, f"expected the batch to be swept, removed={removed}"
        assert not batch.exists(), "batch directory should be gone"


def test_genuinely_unintegrated_batch_is_kept():
    """The guard must not become a rubber stamp: a batch whose entity was never
    committed must still be preserved."""
    with TemporaryDirectory() as tmpdir:
        _setup(tmpdir)
        import reconcile

        batch = paths.BATCHES / "20260102_000000"
        batch.mkdir(parents=True)
        manifest = {"manifest_version": "2.0", "operations": [
            {"op": "upsert_entity", "entity_type": "person",
             "canonical_name": "Nobody Uncommitted Qqq", "status": "Active"},
        ]}
        (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        assert reconcile._batch_is_integrated(manifest) is False
        removed = reconcile._cleanup_integrated_batches()
        assert removed == 0, "must not sweep a batch that was never committed"
        assert batch.exists(), "uncommitted batch must be preserved"


if __name__ == "__main__":
    test_batch_with_resolved_name_is_swept()
    test_genuinely_unintegrated_batch_is_kept()
    print("✓ batch cleanup resolved-name tests passed")
