#!/usr/bin/env python3
"""normalize.py - Bulk mechanical cleanup of entity files.

Applies deterministic cleanup rules to notes and summaries without changing
semantic content. Designed to remove auto-generated boilerplate, standardize
empty markers, and strip meta-commentary that accumulated from multiple
ingestion passes.

Usage:
    python3 run.py normalize              # Apply all fixes
    python3 run.py normalize --dry-run    # Preview only (no writes)
    python3 run.py normalize --verbose    # Show per-file details
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from entity_io import parse_file, serialize
import paths

NOTES_EMPTY_VARIANTS = {
    "_none._",
    "_none recorded._",
    "_none_",
    "none.",
    "none",
    "n/a",
    "no notes.",
    "no notes",
}

NOTES_STRIP_PATTERNS = [
    re.compile(r"Auto-created during database integration for batch \S+\.?"),
    re.compile(r"Entity created from (?:\w+ )?organizational chart ingestion\."
               r"(?:\s*Specific group/section membership will be added during"
               r" database integration\.)?"),
    re.compile(r"Hierarchical relationships and member lists will be added"
               r" during database integration\."),
    re.compile(r"Entity created from.*?ingestion\.?\s*"),
    re.compile(r"Auto-created stub for.*?ingestion\.?"
               r"(?:\s*To be expanded with full project details\.)?"),
    re.compile(r"Limited information available\.?\s*Full name not confirmed"
               r" in source material\.?"),
]

SUMMARY_NUKE_PATTERNS = [
    re.compile(r"^Information about .+ extracted from .+ (?:document|reference"
               r" document)\.?$", re.IGNORECASE),
]


def clean_notes(notes: str) -> tuple[str, list[str]]:
    """Apply cleanup rules to notes. Returns (cleaned, list_of_rules_applied)."""
    if not notes:
        return "", []

    rules = []
    cleaned = notes

    # Strip trailing horizontal rules and trailing empty markers
    before = cleaned
    cleaned = re.sub(r"\n*-{3,}\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\n+_None[. ]*_?\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned != before.strip():
        rules.append("strip_trailing_noise")

    # Strip leading "_None._" prefix (with or without --- separator, keep content after)
    before = cleaned
    cleaned = re.sub(r"^_None[. ]*_?\s*\n*(?:-{3,}\n*)?", "", cleaned, flags=re.IGNORECASE).strip()
    # Also strip leading --- separator without _None_ prefix
    cleaned = re.sub(r"^-{3,}\n+", "", cleaned).strip()
    if cleaned != before:
        rules.append("strip_none_prefix")

    # Check for empty-equivalent markers
    test = cleaned.lower().rstrip(".").strip("_* ")
    if test in NOTES_EMPTY_VARIANTS or cleaned.lower() in NOTES_EMPTY_VARIANTS:
        return "", ["empty_marker"]

    for pat in NOTES_STRIP_PATTERNS:
        if pat.search(cleaned):
            cleaned = pat.sub("", cleaned)
            rules.append(f"strip:{pat.pattern[:40]}")

    # Collapse multiple blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        rules.append("now_empty")
        return "", rules

    return cleaned, rules


def clean_summary(summary: str) -> tuple[str, list[str]]:
    """Apply cleanup rules to summary. Returns (cleaned, list_of_rules_applied)."""
    if not summary:
        return "", []

    rules = []
    for pat in SUMMARY_NUKE_PATTERNS:
        if pat.match(summary.strip()):
            return "", [f"vague_summary:{pat.pattern[:40]}"]

    return summary, rules


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    verbose = "--verbose" in argv
    return _run_normalize(dry_run, verbose)


def _run_normalize(dry_run: bool, verbose: bool) -> int:
    model_dir = paths.WORKING_MODEL
    entity_files = sorted(model_dir.rglob("*.md"))
    # Exclude archive and index
    entity_files = [f for f in entity_files
                    if "_archive" not in f.parts and "_index" not in f.parts]

    stats = {
        "total": 0,
        "modified": 0,
        "notes_cleaned": 0,
        "summary_cleaned": 0,
        "errors": 0,
        "rules": {},
    }

    for path in entity_files:
        stats["total"] += 1
        try:
            entity = parse_file(path)
        except Exception as e:
            if verbose:
                print(f"  ERROR parsing {path.name}: {e}")
            stats["errors"] += 1
            continue

        changed = False

        # Clean notes
        new_notes, note_rules = clean_notes(entity.notes)
        if note_rules:
            if verbose:
                print(f"  NOTES {path.name}: {note_rules}")
            entity.notes = new_notes
            changed = True
            stats["notes_cleaned"] += 1
            for r in note_rules:
                stats["rules"][r] = stats["rules"].get(r, 0) + 1

        # Clean empty-value attributes (value is "[]", "()", or empty)
        EMPTY_VALUES = {"[]", "()", "", "none", "n/a", "unknown"}
        orig_attr_count = len(entity.attributes)
        entity.attributes = [a for a in entity.attributes
                             if a.get("value", "").strip().lower() not in EMPTY_VALUES]
        orig_id_count = len(entity.identifiers)
        entity.identifiers = [i for i in entity.identifiers
                              if i.get("value", "").strip().lower() not in EMPTY_VALUES]
        removed = (orig_attr_count - len(entity.attributes)) + \
                  (orig_id_count - len(entity.identifiers))
        if removed:
            if verbose:
                print(f"  ATTRS {path.name}: removed {removed} empty-value facts")
            changed = True
            stats["rules"]["empty_value_attr"] = \
                stats["rules"].get("empty_value_attr", 0) + removed

        # Clean summary
        new_summary, sum_rules = clean_summary(entity.summary)
        if sum_rules:
            if verbose:
                print(f"  SUMMARY {path.name}: {sum_rules}")
            entity.summary = new_summary
            changed = True
            stats["summary_cleaned"] += 1
            for r in sum_rules:
                stats["rules"][r] = stats["rules"].get(r, 0) + 1

        if changed:
            stats["modified"] += 1
            if not dry_run:
                path.write_text(serialize(entity), encoding="utf-8")

    # Report
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\nNormalization complete ({mode}):")
    print(f"  Scanned: {stats['total']}")
    print(f"  Modified: {stats['modified']}")
    print(f"  Notes cleaned: {stats['notes_cleaned']}")
    print(f"  Summaries cleaned: {stats['summary_cleaned']}")
    print(f"  Parse errors: {stats['errors']}")

    if stats["rules"]:
        print(f"\n  Rules triggered:")
        for rule, count in sorted(stats["rules"].items(), key=lambda x: -x[1]):
            print(f"    {rule}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
