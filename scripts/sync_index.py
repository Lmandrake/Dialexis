"""sync_index.py - Regenerates the derived indices (markdown + SQLite cache)."""
from __future__ import annotations
import sys

import paths
from registry import load_registry


def sync_indices(registry=None) -> None:
    """Regenerate all derived indices under graph/_index/.

    Parses every entity file ONCE (via index_store.iter_entities, which itself
    reuses the parse cache for unchanged files) and from that single set emits:
      - entities.md      (human/agent-readable browse index)
      - conflicts.md     (unresolved/resolved conflict roll-up)
      - index.sqlite     (the derived lookup + parse cache; fully rebuilt here)
    index.sqlite is a disposable cache; the markdown files remain canonical."""
    import index_store
    reg = registry or load_registry()

    paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # Single parse pass — reused for all three artifacts below.
    all_entities = list(index_store.iter_entities(reg))

    # Rebuild the SQLite cache from this exact set (no extra parse).
    index_store.write_rows(all_entities)

    # Generate entities index
    entities = sorted(
        ({"type": e.entity_type, "name": e.canonical_name, "status": e.status,
          "path": str(e.path.relative_to(paths.WORKING_MODEL))}
         for e in all_entities if e.path is not None),
        key=lambda e: (e["type"], e["name"]))

    entities_md = ["# Entity Index", f"\nTotal: {len(entities)} entities\n"]
    current_type = None
    for e in entities:
        if e["type"] != current_type:
            current_type = e["type"]
            entities_md.append(f"\n## {current_type}\n")
        entities_md.append(f"- [{e['name']}]({e['path']}) - {e['status']}")

    (paths.INDEX_DIR / "entities.md").write_text("\n".join(entities_md), encoding="utf-8")

    # Generate conflicts index
    conflicts = []
    for entity in all_entities:
        if entity.path is None:
            continue
        for conflict in entity.conflicts:
            conflicts.append({
                "entity": entity.canonical_name,
                "title": conflict.get("title", "Untitled"),
                "status": conflict.get("status", "UNRESOLVED"),
                "do_not_query": conflict.get("do_not_query", "false"),
                "path": str(entity.path.relative_to(paths.WORKING_MODEL))
            })

    conflicts_md = ["# Conflicts Index", f"\nTotal: {len(conflicts)} conflicts\n"]

    unresolved = [c for c in conflicts if c["status"] == "UNRESOLVED"]
    resolved = [c for c in conflicts if c["status"] == "RESOLVED"]

    conflicts_md.append(f"## Unresolved ({len(unresolved)})\n")
    for c in unresolved:
        dnq = " 🚫 DO NOT QUERY" if c["do_not_query"] == "true" else ""
        conflicts_md.append(f"- [{c['entity']}]({c['path']}): {c['title']}{dnq}")

    conflicts_md.append(f"\n## Resolved ({len(resolved)})\n")
    for c in resolved:
        conflicts_md.append(f"- [{c['entity']}]({c['path']}): {c['title']}")

    (paths.INDEX_DIR / "conflicts.md").write_text("\n".join(conflicts_md), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    try:
        if "--rebuild" in argv:
            # Force a cache-independent full parse of the SQLite index from disk,
            # then regenerate the markdown indices from the same truth.
            import index_store
            stats = index_store.rebuild()
            print(f"index.sqlite rebuilt from disk: {stats['rebuilt']} entities")
        sync_indices()
        print("Indices regenerated successfully")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
