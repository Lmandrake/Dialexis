"""audit_reciprocals.py - Scan database for non-reciprocal relationships.

Reports relationships that are missing their required inverse edges, helping identify
data integrity issues before repair.
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict

import paths
import entity_io as eio
from registry import load_registry


def audit_reciprocals(registry=None, verbose=False) -> dict:
    """Audit all relationships for missing reciprocals.

    Returns:
        dict with missing_reciprocals list and statistics
    """
    reg = registry or load_registry()
    entities = {}
    missing = []

    # Load all entities
    print("Loading entities...")
    for entity_type, info in reg._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if entity_dir.exists():
            for path in entity_dir.glob("*.md"):
                try:
                    entity = eio.parse_file(path)
                    entities[path] = entity
                except Exception as e:
                    print(f"  ERROR loading {path.name}: {e}")
                    continue

    print(f"Auditing {len(entities)} entities for reciprocal relationships...")

    # Check each relationship
    checked = 0
    for path, entity in entities.items():
        for rel in entity.relationships:
            checked += 1
            rel_type = rel["rel_type"]
            target_stem = rel["target_stem"]
            target_dir = rel["target_dir"]

            # Find target entity
            target_path = paths.entity_dir(target_dir) / f"{target_stem}.md"
            if target_path not in entities:
                missing.append({
                    "source": entity.canonical_name,
                    "source_file": path.name,
                    "relation": rel_type,
                    "target": rel.get("target_name", "?"),
                    "target_file": f"{target_stem}.md",
                    "issue": "TARGET_NOT_FOUND",
                    "notes": f"Target file {target_path.name} does not exist"
                })
                continue

            target = entities[target_path]

            # Check if reciprocal exists
            inverse = reg.inverse_of(rel_type)
            inv_rel_type = rel_type if inverse is None else inverse

            if not eio.relationship_present(target.relationships, inv_rel_type, entity.stem):
                missing.append({
                    "source": entity.canonical_name,
                    "source_file": path.name,
                    "relation": rel_type,
                    "target": target.canonical_name,
                    "target_file": target_path.name,
                    "expected_inverse": inv_rel_type,
                    "issue": "MISSING_RECIPROCAL",
                    "notes": f"{target.canonical_name} should have '{inv_rel_type}' back to {entity.canonical_name}"
                })

    # Group by issue type
    by_type = defaultdict(list)
    for m in missing:
        by_type[m["issue"]].append(m)

    return {
        "total_entities": len(entities),
        "total_relationships": checked,
        "missing_reciprocals": len(missing),
        "missing_by_type": dict(by_type),
        "details": missing
    }


def print_report(results: dict, verbose: bool = False):
    """Print formatted audit report."""
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  RECIPROCAL RELATIONSHIP AUDIT{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    print(f"Total entities: {results['total_entities']}")
    print(f"Total relationships checked: {results['total_relationships']}")

    missing_count = results['missing_reciprocals']
    if missing_count == 0:
        print(f"\n{GREEN}✓ All relationships have reciprocals - database is consistent!{RESET}\n")
        return

    print(f"\n{RED}✗ Found {missing_count} missing reciprocal(s){RESET}\n")

    # Summary by issue type
    print(f"{BOLD}Issues by type:{RESET}")
    for issue_type, items in results['missing_by_type'].items():
        color = RED if issue_type == "MISSING_RECIPROCAL" else YELLOW
        print(f"  {color}{issue_type}: {len(items)}{RESET}")

    # Detailed report
    if verbose or missing_count <= 20:
        print(f"\n{BOLD}Detailed Report:{RESET}\n")
        for i, item in enumerate(results['details'], 1):
            if item['issue'] == "MISSING_RECIPROCAL":
                print(f"{RED}[{i}] MISSING RECIPROCAL{RESET}")
                print(f"    Source: {CYAN}{item['source']}{RESET} ({item['source_file']})")
                print(f"    Relation: {item['relation']}")
                print(f"    Target: {CYAN}{item['target']}{RESET} ({item['target_file']})")
                print(f"    Expected inverse: {YELLOW}{item['expected_inverse']}{RESET}")
                print(f"    Issue: {item['notes']}\n")
            else:
                print(f"{YELLOW}[{i}] TARGET NOT FOUND{RESET}")
                print(f"    Source: {CYAN}{item['source']}{RESET} ({item['source_file']})")
                print(f"    Relation: {item['relation']}")
                print(f"    Target: {item['target']} ({item['target_file']})")
                print(f"    Issue: {item['notes']}\n")
    elif missing_count > 20:
        print(f"\n{YELLOW}Showing first 20 issues (use --verbose for full list):{RESET}\n")
        for i, item in enumerate(results['details'][:20], 1):
            print(f"  [{i}] {item['source']} --{item['relation']}--> {item['target']}")
            print(f"      Missing: {item['expected_inverse']} from {item['target']} back to {item['source']}\n")

    print(f"\n{BOLD}Recommended Action:{RESET}")
    print(f"  Run: {CYAN}python3 run.py reconcile{RESET}")
    print(f"  This will automatically repair all missing reciprocals.\n")


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    try:
        reg = load_registry()
        results = audit_reciprocals(reg, verbose)
        print_report(results, verbose)

        # Exit code 1 if issues found, 0 if clean
        return 1 if results['missing_reciprocals'] > 0 else 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
