"""reconcile.py - Idempotent convergence to invariants; also the migration engine."""
from __future__ import annotations
import json
import shutil
import sys

import paths
import entity_io as eio
from naming import slug
from registry import load_registry


def reconcile_all(registry=None, migrate=False) -> dict:
    """Reconcile all entities: normalize format, repair reciprocals, regenerate indices.

    Args:
        registry: Registry instance (loaded if not provided)
        migrate: If True, run in migration mode (verbose reporting)

    Returns:
        dict with statistics
    """
    reg = registry or load_registry()
    import index_store
    stats = {"normalized": 0, "reciprocals_added": 0, "relocated": 0, "errors": []}

    # Iterate via the parse cache (unchanged files skip re-parse).
    entities = list(index_store.iter_entities(reg))
    print(f"Reconciling {len(entities)} entities...")

    for entity in entities:
        path = entity.path
        if path is None:
            continue
        try:
            # Check if entity_type is set
            if not entity.entity_type:
                stats["errors"].append(f"{path.name}: missing entity_type")
                continue

            # Verify correct placement
            expected_dir = paths.entity_dir(reg.directory_for(entity.entity_type))
            expected_slug = slug(entity.canonical_name)
            expected_path = expected_dir / f"{expected_slug}.md"

            if path != expected_path:
                if migrate:
                    print(f"  Relocating: {path.name} -> {expected_path}")
                expected_dir.mkdir(parents=True, exist_ok=True)
                path.rename(expected_path)
                path = expected_path
                stats["relocated"] += 1

            # Normalize by reserializing — but only write when the canonical form
            # actually differs. Skipping no-op writes preserves file mtimes, which
            # keeps the derived parse cache valid across reconcile runs.
            normalized = eio.serialize(entity)
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                current = None
            if normalized != current:
                path.write_text(normalized, encoding="utf-8")
            stats["normalized"] += 1

        except Exception as e:
            stats["errors"].append(f"{path.name}: {e}")

    # Repair reciprocal edges (second pass after all entities normalized)
    print("Repairing reciprocal edges...")
    stats["reciprocals_added"] = _repair_reciprocals(reg)

    # Regenerate indices
    print("Regenerating indices...")
    from sync_index import sync_indices
    sync_indices(reg)

    # Regenerate Kairos agenda index (if agenda/ exists)
    if paths.AGENDA.exists():
        try:
            from kairos.index import regenerate_index
            regenerate_index()
        except Exception:
            pass  # Kairos not yet set up or no data — skip silently

    # Clean up INTEGRATED batches
    print("Cleaning up integrated batches...")
    stats["batches_cleaned"] = _cleanup_integrated_batches()

    return stats


def _repair_reciprocals(registry) -> int:
    """Ensure all relationships have reciprocal edges. Returns count of edges added."""
    added = 0
    import index_store
    entities = {e.path: e for e in index_store.iter_entities(registry)
                if e.path is not None}

    # Check reciprocals
    for entity in entities.values():
        for rel in entity.relationships:
            rel_type = rel["rel_type"]
            target_stem = rel["target_stem"]
            target_dir = rel["target_dir"]

            # Find target entity
            target_path = paths.entity_dir(target_dir) / f"{target_stem}.md"
            if target_path not in entities:
                continue

            target = entities[target_path]

            # Check if reciprocal exists
            inverse = registry.inverse_of(rel_type)
            inv_rel_type = rel_type if inverse is None else inverse

            if not eio.relationship_present(target.relationships, inv_rel_type, entity.stem):
                # Add reciprocal. Provenance is copied from the forward edge: the inverse is
                # the same claim seen from the other end, so it has the same source. Without
                # this, reconcile would manufacture sourceless edges that validate then
                # rejects — repair creating the very defect it exists to remove.
                reciprocal = {
                    "rel_type": inv_rel_type,
                    "target_name": entity.canonical_name,
                    "target_dir": registry.directory_for(entity.entity_type),
                    "target_stem": entity.stem,
                    "confidence": rel.get("confidence", "MEDIUM"),
                    "source": rel.get("source", "unknown"),
                    "date": rel.get("date", "unknown")
                }
                if rel.get("derived"):
                    reciprocal["derived"] = rel["derived"]
                target.relationships.append(reciprocal)
                target_path.write_text(eio.serialize(target), encoding="utf-8")
                added += 1

    return added


def _cleanup_integrated_batches() -> int:
    """Remove batch directories whose operations are verified present in graph.

    Self-healing: does not rely on any prior step having set a status field. Verifies
    that every entity in the batch's commit manifest exists on disk. This guarantees
    cleanup even for batches where status was never updated.

    Also sweeps EMPTY orphan directories — no `_manifest.json`, no `manifest.json`, and no
    files at all. Those cannot be staged batches (`stage_inbox` always writes
    `_manifest.json`) and hold nothing, so removing them is lossless. Everything else
    without positive evidence of integration is left for the user; see the Path-3 comment
    on why this is emptiness-gated rather than age-gated.

    Source files are permanently archived in archive/raw_inbox/.
    """
    removed = 0

    if not paths.BATCHES.exists():
        return 0

    for batch_dir in paths.BATCHES.iterdir():
        if not batch_dir.is_dir() or batch_dir.name.startswith('.'):
            continue

        meta_path = batch_dir / "_manifest.json"
        commit_manifest_path = batch_dir / "manifest.json"

        # Path 1: Already marked INTEGRATED (explicit finalize or legacy)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("status") == "INTEGRATED":
                    shutil.rmtree(batch_dir)
                    removed += 1
                    continue
            except Exception:
                continue

        # Path 2: Has a commit manifest — verify all entities exist on disk
        if commit_manifest_path.exists():
            try:
                manifest = json.loads(commit_manifest_path.read_text())
                if _batch_is_integrated(manifest):
                    shutil.rmtree(batch_dir)
                    removed += 1
            except Exception:
                continue
            continue

        # A STAGED batch with no commit manifest is NEVER removed here. Staging archives
        # every source file to archive/raw_inbox/ *immediately*, so "all sources archived"
        # is true the moment a batch is staged — long before it is committed. A prior version
        # deleted staged batches on that condition and silently destroyed uncommitted work
        # (an in-flight batch vanished on the next reconcile). With no commit manifest there
        # is nothing to verify against the working model, so cleanup requires positive
        # evidence of integration: explicit INTEGRATED status (Path 1) or a verified commit
        # manifest (Path 2). Anything else is left in place for the user to resolve.

        # Path 3: EMPTY ORPHAN — neither manifest, and nothing else inside. `stage_inbox`
        # writes _manifest.json unconditionally, so a directory lacking it cannot be a
        # legitimately staged batch; with no files at all there is provably nothing to
        # lose. This is deliberately narrower than an age check: a manifest-less dir that
        # still HAS content may be an ingestion agent's in-flight work (fact records
        # written before its manifest), so that case is left alone rather than swept on a
        # timer. Age-based deletion is what destroyed work before; provable emptiness
        # cannot.
        if not meta_path.exists() and not commit_manifest_path.exists():
            leftovers = [p for p in batch_dir.iterdir() if not p.name.startswith('.')]
            if not leftovers:
                try:
                    shutil.rmtree(batch_dir)
                    removed += 1
                except OSError:
                    pass

    return removed


def _resolves_to_existing(name: str, entity_type: str, reg) -> bool:
    """True if `name` resolves onto an entity that DOES exist on disk under another slug.

    Used only as the fallback in `_batch_is_integrated`. Deliberately conservative: a
    false positive here would sweep a batch that was never committed, so anything short
    of a confident match returns False and the directory is kept for the user.
    """
    try:
        import entity_matching as em
        if entity_type == "person":
            res = em.resolve_person(name, registry=reg)
            if res.requires_disambiguation or not res.resolved:
                return False
            target = paths.entity_dir(reg.directory_for(entity_type)) / f"{slug(res.resolved)}.md"
            return target.exists()
        # Non-person types: accept only a near-exact match (alias or punctuation
        # variant), never a loose one.
        for m in em.fuzzy_match_entities(name, entity_type=entity_type, registry=reg):
            if m.get("score", 0) >= 0.95:
                return (paths.ROOT / m["path"]).exists() or (
                    paths.WORKING_MODEL / m["path"]).exists()
        return False
    except Exception:
        return False


def _batch_is_integrated(manifest: dict) -> bool:
    """Check whether all entities from a commit manifest exist in graph.

    The literal slug is checked first, then the resolver is consulted. That fallback is
    load-bearing, not belt-and-braces: `commit.py` RESOLVES names before writing, so a
    manifest saying "Kellan Mortimer" lands in `people/kellan_l_mortimer.md`. The on-disk
    manifest still says "Kellan Mortimer" (commit rewrites its in-memory copy, not the
    file), so a literal-only check declares such a batch un-integrated forever and its
    directory is never swept. Nothing is lost, which is why it went unnoticed — but batch
    dirs accumulate silently and "Batches cleaned: 0" gives no hint why.
    """
    operations = manifest.get("operations", [])
    if not operations:
        return False

    reg = load_registry()
    for op in operations:
        if op.get("op") != "upsert_entity":
            continue
        entity_type = op.get("entity_type")
        canonical_name = op.get("canonical_name")
        if not entity_type or not canonical_name:
            continue
        try:
            directory = reg.directory_for(entity_type)
            entity_path = paths.entity_dir(directory) / f"{slug(canonical_name)}.md"
            if entity_path.exists():
                continue
            # Not at the literal slug — the name was probably resolved onto a fuller
            # stored record. Mirror the SAME resolution commit.py applied: resolve_person
            # for people, near-exact fuzzy match (alias/punctuation variants) otherwise.
            if not _resolves_to_existing(canonical_name, entity_type, reg):
                return False
        except Exception:
            return False

    return True


def main(argv: list[str]) -> int:
    migrate = "--migrate" in argv

    try:
        stats = reconcile_all(migrate=migrate)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"\nReconciliation complete:")
    print(f"  Normalized: {stats['normalized']}")
    print(f"  Reciprocals added: {stats['reciprocals_added']}")
    print(f"  Relocated: {stats['relocated']}")
    print(f"  Batches cleaned: {stats.get('batches_cleaned', 0)}")

    if stats["errors"]:
        print(f"\nErrors ({len(stats['errors'])}):")
        for err in stats["errors"]:
            print(f"  {err}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
