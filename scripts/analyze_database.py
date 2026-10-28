"""analyze_database.py - Comprehensive database integrity analysis.

PURPOSE
    Scan the entire Dialexis knowledge base for data quality issues that require human review:
    - Empty or minimal stub entities (< 3 meaningful fields)
    - One-way relationships (missing reciprocal edges - should be caught by reconcile)
    - Isolated entities (no relationships to other entities)
    - Naming ambiguities (same normalized name across different types)
    - Potential duplicates (similar names that may represent the same entity)

WHEN TO USE
    - After major database imports or migrations
    - Before committing large batches of changes
    - Periodically (monthly/quarterly) as part of database maintenance
    - When you suspect data quality issues

USAGE
    python3 scripts/analyze_database.py

    The script produces a comprehensive report with sections:
    1. EMPTY OR MINIMAL STUBS - entities with < 3 fields populated
    2. RELATIONSHIP ISSUES - one-way edges and isolated entities
    3. AMBIGUITIES AND POTENTIAL DUPLICATES - naming conflicts

OUTPUT
    Prints to stdout. The report is designed to be human-readable and actionable.
    Each issue includes file names and specific problems found.

LIMITATIONS
    - Output is limited (first 20-50 items per category) to keep report manageable
    - Similar name detection is simple substring matching, not edit distance
    - Does not automatically fix issues - emit a merge_entity manifest and commit it

MAINTENANCE
    This script is read-only and safe to run at any time. It never modifies files.

RELATED SCRIPTS
    - commit.py - applies a merge_entity manifest to fold duplicates found here
    - reconcile.py - fixes relationship symmetry issues
    - validate.py - validates against registry constraints
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict
import re

import paths
import temporal
from registry import load_registry
from entity_io import parse_file


def find_empty_stubs(registry) -> list[dict]:
    """Find entities with minimal or empty content.

    An entity is flagged as a stub if it has 3+ of these issues:
    - Summary < 20 characters
    - No attributes
    - No relationships
    - Notes marked as "(none)"
    - Total content < 50 characters

    Returns:
        List of dicts with keys: file, type, issues
    """
    stubs = []

    for entity_type, info in registry._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if not entity_dir.exists():
            continue

        for entity_file in entity_dir.glob("*.md"):
            entity = parse_file(entity_file)

            # Check for empty or minimal sections
            issues = []

            if not entity.summary or len(entity.summary.strip()) < 20:
                issues.append("minimal/empty summary")

            if not entity.attributes:
                issues.append("no attributes")

            if not entity.relationships:
                issues.append("no relationships")

            if entity.notes and entity.notes.strip() == "(none)":
                issues.append("notes marked as none")

            # Count total meaningful content
            content_length = len(entity.summary) + len(str(entity.attributes))
            if content_length < 50:
                issues.append("very minimal content")

            if len(issues) >= 3:  # Flag if multiple issues
                stubs.append({
                    "file": entity_file.name,
                    "type": entity_type,
                    "issues": issues
                })

    return stubs


def find_relationship_issues(registry) -> dict:
    """Find one-way relationships and orphaned entities.

    Checks:
    - One-way relationships: A → B exists but B → A does not
    - Isolated entities: entities with no relationships at all

    Note: One-way relationships should be caught by reconcile.py.
    If this reports any, run reconcile to fix them.

    Returns:
        Dict with keys: one_way (list), isolated (list of filenames)
    """
    relationship_map = defaultdict(set)  # entity -> set of related entities
    entity_files = {}  # entity_id -> file path

    # Build relationship map
    for entity_type, info in registry._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if not entity_dir.exists():
            continue

        for entity_file in entity_dir.glob("*.md"):
            entity = parse_file(entity_file)
            entity_id = entity_file.stem
            entity_files[entity_id] = entity_file

            # Extract relationships - entity.relationships is a list of dicts
            for rel in entity.relationships:
                if isinstance(rel, dict) and "target_stem" in rel:
                    target_id = rel["target_stem"]
                    relationship_map[entity_id].add(target_id)

    # Find one-way relationships
    one_way = []
    for entity_id, targets in relationship_map.items():
        for target_id in targets:
            if entity_id not in relationship_map.get(target_id, set()):
                one_way.append({
                    "from": entity_files[entity_id].name,
                    "to": entity_files.get(target_id, Path(target_id + ".md")).name,
                    "note": "one-way relationship (may be intentional)"
                })

    # Find isolated entities (no relationships at all)
    all_entities = set(entity_files.keys())
    connected_entities = set(relationship_map.keys()) | set(e for targets in relationship_map.values() for e in targets)
    isolated = all_entities - connected_entities

    return {
        "one_way": one_way[:50],  # Limit output
        "isolated": [entity_files[e].name for e in sorted(isolated)]  # full list; display truncates
    }


def find_ambiguities(registry) -> dict:
    """Find potential naming ambiguities and duplicates.

    Checks:
    - Exact matches: same normalized name across different entity types
      (e.g., "dorian_rowland" as both person and technology_system)
    - Similar names: one name is substring of another
      (e.g., "edmund_j" and "edmund_j_ridley")

    Normalization: lowercase, replace hyphens and underscores with spaces

    Returns:
        Dict with keys: exact_matches (list), similar_names (list)
    """
    ambiguities = []
    entity_names = defaultdict(list)  # normalized_name -> [(file, display_name, type, key)]

    for entity_type, info in registry._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if not entity_dir.exists():
            continue

        for entity_file in entity_dir.glob("*.md"):
            # Get the display name from first line
            first_line = entity_file.read_text().split('\n')[0]
            if first_line.startswith('# '):
                display_name = first_line[2:].strip()
                normalized = display_name.lower().replace('-', ' ').replace('_', ' ')
                key = f"{info['directory']}/{entity_file.stem}"
                entity_names[normalized].append(
                    (entity_file.name, display_name, entity_type, key))

    # Pairs a human has already ruled DISTINCT are settled: never hand them to the
    # deduper again. This is what makes "yes, I already know" stick — see
    # entity_matching.distinct_from_pairs.
    import entity_matching as em
    adjudicated = em.distinct_from_pairs(registry=registry)

    def _settled(key_a: str, key_b: str) -> bool:
        return frozenset((key_a, key_b)) in adjudicated

    # Find potential duplicates
    for normalized_name, occurrences in entity_names.items():
        if len(occurrences) > 1:
            keys = [o[3] for o in occurrences]
            # Drop the group only when EVERY pair in it has been adjudicated; if any
            # pair is still open, the group still needs review.
            all_settled = all(_settled(a, b)
                              for i, a in enumerate(keys) for b in keys[i + 1:])
            if all_settled:
                continue
            ambiguities.append({
                "normalized_name": normalized_name,
                "files": [f"{f} ({t}): {d}" for f, d, t, _k in occurrences]
            })

    # Check for very similar names (edit distance)
    similar_names = []
    names_list = list(entity_names.keys())
    for i, name1 in enumerate(names_list):
        for name2 in names_list[i+1:]:
            # Simple similarity check
            if len(name1) > 5 and len(name2) > 5:
                if name1 in name2 or name2 in name1:
                    if _settled(entity_names[name1][0][3], entity_names[name2][0][3]):
                        continue
                    similar_names.append({
                        "name1": name1,
                        "files1": entity_names[name1][0][0],
                        "name2": name2,
                        "files2": entity_names[name2][0][0]
                    })

    return {
        "exact_matches": ambiguities[:20],  # Limit output
        "similar_names": similar_names[:20]  # Limit output
    }


def find_update_candidates(registry, now=None) -> list[dict]:
    """Find facts/relationships whose last_verified age exceeds their temporal_class
    threshold (mutable ~18mo, volatile ~5mo; permanent never). These are candidates to
    RE-VERIFY, not to delete — carrying a stale fact is cheap, so this is advisory. Prune
    candidacy is a separate, higher, entity-level bar (see isolated/stub detection above).
    """
    from datetime import date
    now = now or date.today()
    candidates = []

    for _entity_type, info in registry._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if not entity_dir.exists():
            continue
        for entity_file in entity_dir.glob("*.md"):
            entity = parse_file(entity_file)
            stale = []
            for f in [*entity.identifiers, *entity.attributes]:
                tc = f.get("temporal_class")
                lv = f.get("last_verified")
                if tc and lv and temporal.is_stale(lv, tc, now):
                    stale.append(f"{f.get('key')} [{tc}, last_verified={lv}]")
            for r in entity.relationships:
                tc = r.get("temporal_class")
                lv = r.get("last_verified")
                if tc and lv and temporal.is_stale(lv, tc, now):
                    stale.append(f"{r.get('rel_type')} -> {r.get('target_stem')} "
                                 f"[{tc}, last_verified={lv}]")
            if stale:
                candidates.append({
                    "file": entity_file.name,
                    "type": _entity_type,
                    "count": len(stale),
                    "stale": stale,
                })

    candidates.sort(key=lambda c: c["count"], reverse=True)
    return candidates


def main() -> int:
    registry = load_registry()

    print("=" * 80)
    print("DIALEXIS DATABASE ANALYSIS")
    print("=" * 80)
    print()

    # Find empty stubs
    print("🔍 EMPTY OR MINIMAL STUBS")
    print("-" * 80)
    stubs = find_empty_stubs(registry)
    if stubs:
        print(f"Found {len(stubs)} entities with minimal content:\n")
        for stub in stubs[:30]:  # Show first 30
            print(f"  {stub['file']} ({stub['type']})")
            print(f"    Issues: {', '.join(stub['issues'])}")
            print()
    else:
        print("✓ No significant stubs found")
    print()

    # Find relationship issues
    print("🔗 RELATIONSHIP ISSUES")
    print("-" * 80)
    rel_issues = find_relationship_issues(registry)

    if rel_issues["one_way"]:
        print(f"Found {len(rel_issues['one_way'])} one-way relationships (showing first 50):\n")
        for rel in rel_issues["one_way"][:20]:
            print(f"  {rel['from']} → {rel['to']}")
        if len(rel_issues["one_way"]) > 20:
            print(f"  ... and {len(rel_issues['one_way']) - 20} more")
        print()
    else:
        print("✓ No one-way relationships found")

    if rel_issues["isolated"]:
        print(f"\nFound {len(rel_issues['isolated'])} isolated entities (no relationships):\n")
        for iso in rel_issues["isolated"][:15]:
            print(f"  {iso}")
        if len(rel_issues["isolated"]) > 15:
            print(f"  ... and {len(rel_issues['isolated']) - 15} more")
        print()
    else:
        print("✓ No isolated entities found")
    print()

    # Find ambiguities
    print("⚠️  AMBIGUITIES AND POTENTIAL DUPLICATES")
    print("-" * 80)
    ambig = find_ambiguities(registry)

    if ambig["exact_matches"]:
        print(f"Found {len(ambig['exact_matches'])} potential naming conflicts:\n")
        for amb in ambig["exact_matches"][:10]:
            print(f"  '{amb['normalized_name']}':")
            for file in amb['files']:
                print(f"    - {file}")
            print()
    else:
        print("✓ No exact naming conflicts found")

    if ambig["similar_names"]:
        print(f"\nFound {len(ambig['similar_names'])} similar names (one contains the other):\n")
        for sim in ambig["similar_names"][:10]:
            print(f"  '{sim['name1']}' ({sim['files1']})")
            print(f"  '{sim['name2']}' ({sim['files2']})")
            print()
    else:
        print("✓ No similar names found")

    # Find update candidates (stale facts to re-verify)
    print()
    print("🕒 UPDATE CANDIDATES (stale facts to re-verify, not delete)")
    print("-" * 80)
    updates = find_update_candidates(registry)
    if updates:
        total_stale = sum(c["count"] for c in updates)
        print(f"Found {total_stale} stale fact(s)/edge(s) across {len(updates)} entities "
              f"(showing top 20 entities):\n")
        for cand in updates[:20]:
            print(f"  {cand['file']} ({cand['type']}) — {cand['count']} stale:")
            for item in cand["stale"][:5]:
                print(f"    - {item}")
            if cand["count"] > 5:
                print(f"    ... and {cand['count'] - 5} more")
            print()
    else:
        print("✓ No stale facts past their temporal_class threshold")

    print()
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
