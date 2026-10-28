"""resolve.py - Zero-LLM contextual person resolver (CLI over resolve_person).

    python3 run.py resolve "<query>" [--org CMG] [--email x@y] \
                                     [--related "project:GAMMA"] [--co-mention "Name"]

Resolves a person reference to an EXISTING entity using middle-initial-insensitive
identity plus optional context signals (org membership, email, same-source
co-mentions, relationships to named entities). Prints the resolved entity's raw
markdown on a single clean resolution, or a ranked candidate list with a rationale
and a "requires disambiguation" banner when the query is genuinely ambiguous.

PEOPLE ONLY. This resolver searches the person type and nothing else. On a miss it
cross-checks the other entity types and tells you if the name exists as an
organization/project/system, because "No person resolved" is otherwise easy to
misread as "not in the model" — use `run.py entity "<name>"` to search all types.

Resolve-only: this reads graph/ and reports a decision. It NEVER writes,
merges, or renames. Deterministic, stdlib-only, no model involved.

Flags may be repeated (e.g. two --org, two --related). --related accepts
"type:name" or a bare "name".
"""
from __future__ import annotations
import sys

import paths
import access_log
from entity_matching import resolve_person


def _parse_args(argv: list[str]) -> tuple[str, dict]:
    """Split argv into (query, context-dict). Query = all non-flag tokens joined."""
    query_parts: list[str] = []
    orgs: list[str] = []
    related: list[str] = []
    co_mentions: list[str] = []
    email: str | None = None

    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--org" and i + 1 < len(argv):
            orgs.append(argv[i + 1]); i += 2
        elif tok == "--email" and i + 1 < len(argv):
            email = argv[i + 1]; i += 2
        elif tok == "--related" and i + 1 < len(argv):
            related.append(argv[i + 1]); i += 2
        elif tok in ("--co-mention", "--comention") and i + 1 < len(argv):
            co_mentions.append(argv[i + 1]); i += 2
        else:
            query_parts.append(tok); i += 1

    context: dict = {}
    if orgs:
        context["orgs"] = orgs
    if email:
        context["email"] = email
    if related:
        context["related_entities"] = related
    if co_mentions:
        context["co_mentions"] = co_mentions
    return " ".join(query_parts).strip(), context


def _report_non_person_matches(query: str, limit: int = 8) -> None:
    """On a person miss, say whether the name exists as some OTHER entity type.

    `resolve` is person-only by design, but a bare "No person resolved" reads as "not in
    the model" — which has caused a real duplicate-creating upsert and a commit HALT. If
    the name matches an organization/project/system/etc, name it, its type, and its path,
    and point at `run.py entity`, which searches every type.

    Best-effort only: any failure here must not change the resolve verdict.
    """
    try:
        from entity_matching import fuzzy_match_entities
        matches = [m for m in fuzzy_match_entities(query) if m.get("type") != "person"]
    except Exception:
        return
    if not matches:
        return

    shown = matches[:limit]
    print(f"\nNote: `resolve` only searches people. "
          f"{len(matches)} non-person entit{'y' if len(matches) == 1 else 'ies'} "
          f"match this name:")
    for m in shown:
        print(f"  {m['score']:.2f}  {m['type']:<20} {m['entity']:<40} {m['path']}")
    if len(matches) > len(shown):
        print(f"  ... and {len(matches) - len(shown)} more")
    print(f'\nTo search ALL entity types, use:  python3 run.py entity "{query}"')


def main(argv: list[str]) -> int:
    query, context = _parse_args(argv[1:])
    if not query and not context.get("email"):
        print('Usage: python3 run.py resolve "<name>" '
              '[--org X] [--email x@y] [--related type:name] [--co-mention Name]',
              file=sys.stderr)
        return 2

    res = resolve_person(query or context.get("email", ""), context=context or None)

    if res.resolved and not res.requires_disambiguation:
        path = paths.WORKING_MODEL / res.resolved_path if res.resolved_path else None
        print(f"# Resolved: {res.resolved}  [{res.confidence}]")
        print(f"# {res.rationale}\n")
        if path and path.exists():
            print(path.read_text(encoding="utf-8"))
        access_log.record("resolve", query, "SERVED", n=1)
        return 0

    if not res.candidates:
        access_log.record("resolve", query, "MISS", n=0)
        print(f"No PERSON resolved for '{query}'.")
        print(res.rationale)
        _report_non_person_matches(query)
        return 1

    # Ambiguous / multiple survivors: list them all with the disambiguation banner.
    access_log.record("resolve", query, "DECLINED", n=len(res.candidates))
    print(f"AMBIGUOUS ({len(res.candidates)} candidates) — requires disambiguation")
    print(f"{res.rationale}\n")
    for c in res.candidates:
        reasons = f"  [{'; '.join(c.reasons)}]" if c.reasons else ""
        print(f"  {c.score:.2f}  {c.canonical_name:<32}  {c.path}{reasons}")
    print("\n(Re-run with --org / --email / --related / --co-mention to disambiguate.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
