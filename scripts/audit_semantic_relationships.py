"""audit_semantic_relationships.py - Find entities mentioned but not linked by relationships.

Scans for cases where entity A mentions entity B in attributes, notes, or summary but
lacks a formal relationship edge to B. This is a higher-level semantic audit beyond
just checking reciprocal consistency.
"""
from __future__ import annotations
import sys
import re
from pathlib import Path
from collections import defaultdict

import paths
import entity_io as eio
from naming import slug
from registry import load_registry


def find_entity_mentions(text: str, entities_by_stem: dict) -> set[str]:
    """Find entity stems that appear to be mentioned in text."""
    if not text:
        return set()

    text_lower = text.lower()
    mentioned = set()

    for stem, canonical_name in entities_by_stem.items():
        # Try to find the canonical name in text (case insensitive)
        name_lower = canonical_name.lower()
        if name_lower in text_lower:
            mentioned.add(stem)

    return mentioned


def audit_semantic_relationships(registry=None) -> dict:
    """Audit for entities mentioned in text but not linked via relationships."""
    reg = registry or load_registry()
    entities = {}
    entities_by_stem = {}
    issues = []

    # Load all entities
    print("Loading entities...")
    for entity_type, info in reg._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if entity_dir.exists():
            for path in entity_dir.glob("*.md"):
                try:
                    entity = eio.parse_file(path)
                    entities[path] = entity
                    entities_by_stem[entity.stem] = entity.canonical_name
                except Exception as e:
                    print(f"  ERROR loading {path.name}: {e}")
                    continue

    print(f"Auditing {len(entities)} entities for semantic relationship gaps...")

    # Check each entity
    for path, entity in entities.items():
        # Get stems of entities this one is linked to
        linked_stems = {rel["target_stem"] for rel in entity.relationships}

        # Scan attributes for entity mentions
        attr_text = " ".join(
            f"{a.get('key', '')}: {a.get('value', '')}"
            for a in entity.attributes
        )

        # Scan notes
        notes_text = entity.notes if entity.notes else ""

        # Scan summary
        summary_text = entity.summary if entity.summary else ""

        # Combine all text
        all_text = f"{attr_text} {notes_text} {summary_text}"

        # Find mentions
        mentioned_stems = find_entity_mentions(all_text, entities_by_stem)

        # Remove self-reference and already-linked entities
        mentioned_stems.discard(entity.stem)
        unlinked = mentioned_stems - linked_stems

        for mentioned_stem in unlinked:
            mentioned_name = entities_by_stem[mentioned_stem]

            # Find where it's mentioned
            locations = []
            if mentioned_name.lower() in attr_text.lower():
                locations.append("attributes")
            if mentioned_name.lower() in notes_text.lower():
                locations.append("notes")
            if mentioned_name.lower() in summary_text.lower():
                locations.append("summary")

            issues.append({
                "entity": entity.canonical_name,
                "entity_file": path.name,
                "mentions": mentioned_name,
                "mentioned_file": f"{mentioned_stem}.md",
                "mentioned_in": locations,
                "issue": "MENTIONED_NOT_LINKED",
                "suggestion": f"Consider adding relationship between {entity.canonical_name} and {mentioned_name}"
            })

    return {
        "total_entities": len(entities),
        "issues_found": len(issues),
        "details": issues
    }


def print_report(results: dict, limit: int = 50):
    """Print formatted audit report."""
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  SEMANTIC RELATIONSHIP AUDIT{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    print(f"Total entities: {results['total_entities']}")

    issues_count = results['issues_found']
    if issues_count == 0:
        print(f"\n{GREEN}✓ No obvious semantic relationship gaps found!{RESET}\n")
        return

    print(f"\n{YELLOW}Found {issues_count} potential semantic relationship gap(s){RESET}\n")
    print(f"{BOLD}Note:{RESET} These are mentions in text without corresponding relationship edges.")
    print(f"Not all mentions require relationships - use judgment.\n")

    # Show limited results
    show_count = min(limit, issues_count)
    print(f"{BOLD}Showing first {show_count} issues:{RESET}\n")

    for i, item in enumerate(results['details'][:show_count], 1):
        print(f"{YELLOW}[{i}] {item['entity']}{RESET}")
        print(f"    Mentions: {CYAN}{item['mentions']}{RESET}")
        print(f"    In: {', '.join(item['mentioned_in'])}")
        print(f"    File: {item['entity_file']}")
        print(f"    {item['suggestion']}\n")

    if issues_count > limit:
        print(f"\n{YELLOW}... and {issues_count - limit} more issues.{RESET}")
        print(f"Run with --all to see complete list.\n")


def main(argv: list[str]) -> int:
    show_all = "--all" in argv
    limit = 999999 if show_all else 50

    try:
        reg = load_registry()
        results = audit_semantic_relationships(reg)
        print_report(results, limit)
        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
