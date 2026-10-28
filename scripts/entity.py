"""entity.py - Zero-LLM fuzzy entity lookup.

    python3 run.py entity "<query>"

Fuzzy-matches a name/partial across all entities (canonical names + aliases, with
substring/token boosting). A dominant single hit prints that entity's raw markdown
verbatim; otherwise a ranked candidate list is printed (no auto-disambiguation).
Deterministic, stdlib-only, no model involved.

Middle-initial awareness: before falling back to raw fuzzy scoring, a person query
is run through `resolve_person`, which treats middle-initial variants as one
identity ("Tobias Marlowe" dominates to the single "Tobias J Marlowe" record) and
holds genuine same-name collisions ambiguous instead of guessing. When that clean
person resolution exists, its record is printed directly; otherwise the original
fuzzy path runs unchanged.
"""
from __future__ import annotations
import sys

import paths
import access_log
from entity_matching import fuzzy_match_entities, resolve_person

GATHER_THRESHOLD = 0.55   # minimum score to be considered a candidate
LIST_MARGIN = 0.15        # in ambiguous mode, show candidates within this of the top
LIST_CAP = 15             # max rows in a candidate list
WIN_ABS = 0.9             # a dominant winner must score at least this
WIN_GAP = 0.12            # ...and lead the runner-up by at least this


def _print_candidates(query: str, matches: list[dict]) -> None:
    top = matches[0]["score"]
    shown = [m for m in matches if m["score"] >= top - LIST_MARGIN][:LIST_CAP]
    print(f"{len(shown)} candidate(s) for '{query}':\n")
    for m in shown:
        print(f"  {m['score']:.2f}  {m['entity']:<38}  {m['type']:<12}  {m['path']}")
    print("\n(Re-run with a more exact name to open a single record.)")


def main(argv: list[str]) -> int:
    query = " ".join(argv[1:]).strip()
    if not query:
        print('Usage: python3 run.py entity "<name or partial>"', file=sys.stderr)
        return 2

    matches = fuzzy_match_entities(query, threshold=GATHER_THRESHOLD)

    # Middle-initial-aware fast path: if the query resolves cleanly to a single
    # person (e.g. "Tobias Marlowe" -> "Tobias J Marlowe", with no same-name
    # collision), print that record directly — the fuller stored form dominates
    # the shorter query instead of appearing as a mere candidate. Guard: only take
    # this path when the resolved person is also the top fuzzy hit across ALL
    # entity types, so a query the user meant as an org/project/etc. is never
    # hijacked by a same-spelled person. Any ambiguity, non-person top hit, or
    # exception falls through to the generic fuzzy path unchanged.
    try:
        res = resolve_person(query)
    except Exception:
        res = None
    if (res and res.resolved and not res.requires_disambiguation
            and res.resolved_path and matches):
        top_match = matches[0]
        if top_match["type"] == "person" and top_match["entity"] == res.resolved:
            path = paths.WORKING_MODEL / res.resolved_path
            if path.exists():
                print(f"# {res.resolved_path}  ({res.confidence}; {res.rationale})\n")
                print(path.read_text(encoding="utf-8"))
                access_log.record("entity", query, "SERVED", n=1)
                return 0

    if not matches:
        access_log.record("entity", query, "MISS", n=0)
        print(f"No entity found matching '{query}'.")
        nearest = fuzzy_match_entities(query, threshold=0.3)[:3]
        if nearest:
            print("\nDid you mean:")
            for m in nearest:
                print(f"  {m['score']:.2f}  {m['entity']} ({m['type']})")
        return 1

    top = matches[0]
    second = matches[1]["score"] if len(matches) > 1 else 0.0
    dominant = (top["score"] >= WIN_ABS and (len(matches) == 1 or top["score"] - second >= WIN_GAP)) \
        or (top["score"] == 1.0 and second < 1.0)

    if dominant:
        path = paths.WORKING_MODEL / top["path"]
        print(f"# {top['path']}  (score {top['score']:.2f})\n")
        print(path.read_text(encoding="utf-8"))
        access_log.record("entity", query, "SERVED", n=1)
        return 0

    access_log.record("entity", query, "DECLINED", n=len(matches))
    _print_candidates(query, matches)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
