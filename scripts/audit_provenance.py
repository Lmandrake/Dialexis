"""audit_provenance.py - Standing metrics for provenance depth, granularity and stance.

    python3 run.py audit_provenance             # the four numbers
    python3 run.py audit_provenance --detail    # plus the worst offenders

Read-only. Reports the three defects the 2026-09-12 extraction measurement found, so each one
is a number that can be watched instead of an impression. None of these halts anything:
`validate` owns the binding invariants, this owns the trends.

1. **Unsourced edges.** Every relationship must name a source; 19,539 were backfilled to
   `unknown` because they predated the requirement. `commit.py` upgrades `unknown` whenever an
   edge is re-asserted, so this figure should only ever fall.

2. **Second-hand facts.** Facts carrying `derived=` were read from something standing in for the
   source. Not a defect — a disclosure. Worth watching because a graph that is mostly second-hand
   is a different kind of artifact from one that is mostly primary.

3. **Bundled facts.** A fact line asserting several things behind one metadata bracket cannot be
   audited or scored per claim, and one `confidence=HIGH` covers assertions whose support
   genuinely differs. Both precision defects found on 2026-09-12 were hiding inside bundles. This
   is NOT enforced: splitting thousands of stored facts needs judgment per line, and halting on
   it would wedge every ingestion. `commit.py` reports newly-written bundles at write time, which
   is the feedback that was missing when the rate rose from 18.7% to 46.5% on fresh extraction.

4. **Attributed stance.** Perspectives are where a held view belongs. The recall measurement found
   attributed judgment was the largest category of dropped content, and the mechanism was being
   used on 14 of 4,742 entities — so this counts whether that is changing.
"""
from __future__ import annotations
import sys
from collections import Counter

import paths
import entity_io as eio
import registry as reg_mod


def _assertion_count(value: str) -> int:
    """Delegates to entity_io so the audit and the write-time nudge in commit.py cannot drift
    apart on what counts as bundled."""
    return eio.assertion_count(value)


def collect() -> dict:
    registry = reg_mod.load_registry()
    out = {
        "entities": 0, "edges": 0, "edges_unknown": 0,
        "facts": 0, "facts_derived": 0, "facts_bundled": 0,
        "assertions_in_bundles": 0,
        "entities_with_perspective": 0, "perspectives": 0,
        "perspectives_unattributed": 0,
        "derived_kinds": Counter(), "worst": [],
    }
    for et in registry._entity_types:
        directory = paths.entity_dir(registry.directory_for(et))
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            entity = eio.parse_file(path)
            out["entities"] += 1
            for rel in entity.relationships:
                out["edges"] += 1
                if rel.get("source", "unknown") == "unknown":
                    out["edges_unknown"] += 1
            for facts in (entity.identifiers, entity.attributes):
                for f in facts:
                    out["facts"] += 1
                    if f.get("derived"):
                        out["facts_derived"] += 1
                        out["derived_kinds"][f["derived"]] += 1
                    n = _assertion_count(f.get("value", ""))
                    if n > 1:
                        out["facts_bundled"] += 1
                        out["assertions_in_bundles"] += n
                        out["worst"].append((n, path.stem, f.get("key")))
            if entity.perspectives:
                out["entities_with_perspective"] += 1
            for p in entity.perspectives:
                out["perspectives"] += 1
                title = (p.get("title") or "").strip()
                if (not title or title.lower() == "untitled"
                        or (not (p.get("holder") or "").strip()
                            and p.get("evidence_type") != "inferred")):
                    out["perspectives_unattributed"] += 1
    out["worst"].sort(reverse=True)
    return out


def main(argv: list[str]) -> int:
    detail = "--detail" in argv[1:]
    m = collect()

    def pct(n, d):
        return f"{100.0 * n / d:.1f}%" if d else "n/a"

    print("Provenance, granularity and stance — audit\n")
    print(f"Entities: {m['entities']}   edges: {m['edges']}   facts: {m['facts']}\n")

    print("1. Unsourced edges (target: 0; falls as edges are re-asserted)")
    print(f"     {m['edges_unknown']} of {m['edges']}  ({pct(m['edges_unknown'], m['edges'])})\n")

    print("2. Second-hand facts (a disclosure, not a defect)")
    print(f"     {m['facts_derived']} of {m['facts']}  ({pct(m['facts_derived'], m['facts'])})")
    for kind, n in m["derived_kinds"].most_common():
        print(f"       {n:>6}  derived={kind}")
    print()

    print("3. Bundled facts (several assertions behind one metadata bracket)")
    print(f"     {m['facts_bundled']} of {m['facts']}  ({pct(m['facts_bundled'], m['facts'])})"
          f"  carrying ~{m['assertions_in_bundles']} assertions")
    print("     Not enforced: splitting needs judgment per line. Counted so it is finite.\n")

    print("4. Attributed stance (perspectives)")
    print(f"     {m['perspectives']} perspectives on {m['entities_with_perspective']} of "
          f"{m['entities']} entities  ({pct(m['entities_with_perspective'], m['entities'])})")
    print(f"     {m['perspectives_unattributed']} name no holder and are not marked inferred")

    if detail and m["worst"]:
        print("\nMost heavily bundled facts:")
        for n, stem, key in m["worst"][:25]:
            print(f"  ~{n:>2} assertions  {stem}: {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
