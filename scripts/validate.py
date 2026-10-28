"""validate.py - Binding invariant gate, read from disk."""
from __future__ import annotations
import sys
from pathlib import Path

import paths
import entity_io as eio
import temporal
from naming import slug
from registry import load_registry


def _md_paths(reg):
    out = []
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            out.extend(d.glob("*.md"))
    return out


def _load_for_validation(reg, use_cache: bool):
    """Return (entities_by_path, parse_errors).

    Reads every entity file, surfacing parse failures (validate must report them,
    unlike the silent-skip read path). When use_cache is True, a file whose
    (mtime,size) matches its cached row is reconstructed from the cached blob;
    ANY mismatch (or --no-cache) forces a live parse of that file from disk, so
    the gate always validates on-disk truth."""
    import index_store
    cache = {}
    if use_cache:
        con = index_store._connect()
        if con is not None:
            try:
                for rp, mtime, size, blob in con.execute(
                        "SELECT path,mtime,size,blob_json FROM entities"):
                    cache[rp] = (mtime, size, blob)
            except Exception:
                cache = {}
            finally:
                con.close()

    entities, errors = {}, []
    for path in _md_paths(reg):
        rp = str(path.relative_to(paths.WORKING_MODEL))
        row = cache.get(rp)
        if row is not None:
            try:
                st = path.stat()
                if abs(st.st_mtime - row[0]) < 1e-6 and st.st_size == row[1]:
                    entities[path] = index_store._blob_to_entity(row[2], path)
                    continue
            except Exception:
                pass  # fall through to a live parse
        try:
            e = eio.parse_file(path)
            e.path = path
            entities[path] = e
        except Exception as exc:
            errors.append(f"{path.name}: parse error: {exc}")
    return entities, errors


def validate_all(registry=None, report=False, use_cache=True) -> dict:
    """Validate all entities against invariants.

    Args:
        registry: Registry instance (loaded if not provided)
        report: If True, measure drift non-fatally (don't exit on errors)
        use_cache: If True (default), read entities via the derived parse cache.
            The cache is only ever trusted for a file whose (mtime,size) is
            unchanged; any changed file is re-parsed from disk, so the gate
            reads truth and the cache can never mask a violation. Pass False to
            force a full live parse of every file (belt-and-suspenders audits).

    Returns:
        dict with validation results
    """
    reg = registry or load_registry()
    results = {
        "total": 0,
        "errors": [],
        "warnings": [],
        "dangling_refs": [],
        "missing_reciprocals": []
    }

    # Collect all entities (via parse cache, or forced live parse with --no-cache).
    # Parse failures are surfaced (not silently skipped) — this is the safety gate.
    entities, parse_errors = _load_for_validation(reg, use_cache)
    results["errors"].extend(parse_errors)

    for path, entity in entities.items():
        results["total"] += 1

        # Validate placement
        expected_slug = slug(entity.canonical_name)
        if path.stem != expected_slug:
            results["errors"].append(
                f"{path.name}: slug mismatch (expected {expected_slug}.md)"
            )

        # Validate entity type
        if not entity.entity_type:
            results["errors"].append(f"{path.name}: missing entity_type")
        elif not reg.is_entity_type(entity.entity_type):
            results["errors"].append(
                f"{path.name}: unknown entity type {entity.entity_type!r}"
            )

        # Validate status
        if entity.status not in reg.entity_statuses:
            results["warnings"].append(
                f"{path.name}: invalid status {entity.status!r}"
            )

        # Perspectives carry attributed stance. Reported rather than fatal, because legacy ones
        # predate the commit-side requirement; repair them with prune_perspective + re-add.
        for p in entity.perspectives:
            title = (p.get("title") or "").strip()
            if not title or title.lower() == "untitled":
                results["warnings"].append(
                    f"{path.name}: perspective without a distinct title "
                    f"(the dedupe key — untitled ones collide)")
            elif not (p.get("holder") or "").strip() and p.get("evidence_type") != "inferred":
                results["warnings"].append(
                    f"{path.name}: perspective {title!r} names no holder and is not marked "
                    f"inferred — an unattributed stance reads as fact")

        # Validate the temporal metadata on facts (optional: absent is fine for legacy
        # facts, but any stored value must be well-formed).
        for sect_name, facts in (("identifiers", entity.identifiers),
                                 ("attributes", entity.attributes)):
            for f in facts:
                tc = f.get("temporal_class")
                if tc is not None and not reg.is_temporal_class(tc):
                    results["errors"].append(
                        f"{path.name}: {sect_name} {f.get('key')!r}: "
                        f"invalid temporal_class {tc!r}")
                lv = f.get("last_verified")
                if lv is not None and not temporal.is_well_formed_stamp(lv):
                    results["errors"].append(
                        f"{path.name}: {sect_name} {f.get('key')!r}: "
                        f"malformed last_verified {lv!r}")
                # Source vocabulary on stored facts is reported, not fatal: ~410 legacy facts
                # predate the write-gate check in commit.py and carry values that were never
                # registered. Halting here would block every mutation until they are migrated,
                # so this surfaces the drift as a countable cleanup target instead.
                src = f.get("source")
                if src is not None and not reg.is_source_type(src):
                    results["warnings"].append(
                        f"{path.name}: {sect_name} {f.get('key')!r}: "
                        f"unregistered source {src!r}")
                drv = f.get("derived")
                if drv is not None and not reg.is_derivation_type(drv):
                    results["warnings"].append(
                        f"{path.name}: {sect_name} {f.get('key')!r}: "
                        f"unregistered derivation {drv!r}")

    # Check relationships
    entity_by_stem = {
        (reg.directory_for(e.entity_type), e.stem): (path, e)
        for path, e in entities.items()
    }

    for path, entity in entities.items():
        for rel in entity.relationships:
            rel_type = rel.get("rel_type")
            target_dir = rel.get("target_dir")
            target_stem = rel.get("target_stem")

            # Validate relationship type
            if not reg.is_relationship(rel_type):
                results["errors"].append(
                    f"{path.name}: unknown relationship {rel_type!r}"
                )
                continue

            # Validate temporal metadata on the edge (optional; well-formed if present)
            rtc = rel.get("temporal_class")
            if rtc is not None and not reg.is_temporal_class(rtc):
                results["errors"].append(
                    f"{path.name}: {rel_type} -> {target_stem}: invalid temporal_class {rtc!r}")
            rlv = rel.get("last_verified")
            if rlv is not None and not temporal.is_well_formed_stamp(rlv):
                results["errors"].append(
                    f"{path.name}: {rel_type} -> {target_stem}: malformed last_verified {rlv!r}")

            # Provenance on an edge is BINDING, unlike on a legacy fact. An edge with no source
            # cannot be audited or re-verified, and 19,539 accumulated while this was optional;
            # once backfilled to `unknown`, requiring it is what stops the population regrowing.
            rsrc = rel.get("source")
            if not rsrc:
                results["errors"].append(
                    f"{path.name}: {rel_type} -> {target_stem}: no source. Every edge must "
                    f"name its provenance ('unknown' is allowed, omission is not).")
            elif not reg.is_source_type(rsrc):
                results["errors"].append(
                    f"{path.name}: {rel_type} -> {target_stem}: unknown source type {rsrc!r}")
            rdrv = rel.get("derived")
            if rdrv is not None and not reg.is_derivation_type(rdrv):
                results["errors"].append(
                    f"{path.name}: {rel_type} -> {target_stem}: "
                    f"unknown derivation type {rdrv!r}")

            # Check target exists
            target_key = (target_dir, target_stem)
            if target_key not in entity_by_stem:
                results["dangling_refs"].append(
                    f"{path.name}: {rel_type} -> {target_dir}/{target_stem} (not found)"
                )
                continue

            # Check reciprocal
            target_path, target_entity = entity_by_stem[target_key]
            inverse = reg.inverse_of(rel_type)
            inv_rel_type = rel_type if inverse is None else inverse

            if not eio.relationship_present(target_entity.relationships, inv_rel_type, entity.stem):
                results["missing_reciprocals"].append(
                    f"{path.name}: {rel_type} -> {target_entity.canonical_name} "
                    f"(missing {inv_rel_type} back)"
                )

    return results


def main(argv: list[str]) -> int:
    report = "--report" in argv
    use_cache = "--no-cache" not in argv

    try:
        results = validate_all(report=report, use_cache=use_cache)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Validated {results['total']} entities\n")

    if results["errors"]:
        print(f"ERRORS ({len(results['errors'])}):")
        for err in results["errors"]:
            print(f"  {err}")

    if results["warnings"]:
        print(f"\nWARNINGS ({len(results['warnings'])}):")
        for warn in results["warnings"]:
            print(f"  {warn}")

    if results["dangling_refs"]:
        print(f"\nDANGLING REFERENCES ({len(results['dangling_refs'])}):")
        for ref in results["dangling_refs"]:
            print(f"  {ref}")

    if results["missing_reciprocals"]:
        print(f"\nMISSING RECIPROCALS ({len(results['missing_reciprocals'])}):")
        for rec in results["missing_reciprocals"]:
            print(f"  {rec}")

    total_issues = (len(results["errors"]) + len(results["dangling_refs"]) +
                   len(results["missing_reciprocals"]))

    if total_issues == 0:
        print("\n✓ All checks passed")
        return 0
    elif report:
        print(f"\n⚠ {total_issues} issues found (report mode, not failing)")
        return 0
    else:
        print(f"\n✗ {total_issues} issues found")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
