"""merge_fragments.py - Deterministically merge subagent manifest fragments into one manifest.

Subagents (dialexis-extractor / -researcher / -triager / -deduper) live in the DECIDE
layer: each returns a *fragment* — a list of manifest operations for the slice of the batch
it handled. They never write to graph/ and never write the batch manifest.

This script is the ENFORCE+DETECT seam between many fragments and the single manifest that
commit.py applies:

  * ENFORCE: it concatenates every fragment's operations and folds all upserts that target the
    same entity into ONE upsert op (union of aliases / relationships / events / sources, and a
    per-key merge of attributes & identifiers). commit.py stays the sole applier.
  * DETECT:  when two fragments assert DIFFERENT values for the same attribute/identifier key on
    the same entity, that is a genuine cross-source disagreement. Rather than silently letting
    last-writer-win (or letting commit.py HALT), it keeps the higher-confidence value and
    auto-emits an `add_conflict` op so the contradiction is recorded on the entity, first-class.

The guiding principle is unchanged: never ask an LLM to guarantee a deterministic postcondition.
Collision detection across parallel subagents is deterministic, so it lives here in Python.

Usage:
    python3 run.py merge_fragments <fragments_dir> [--out manifest.json] [--batch-id ID]
    python3 run.py merge_fragments frag1.json frag2.json --out manifest.json --batch-id 20260716_101112

A fragment file is JSON shaped either as {"operations": [...]} or as a bare [...] list of ops.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import entity_io as eio
from naming import slug

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM-HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _rank(fact: dict) -> int:
    return _CONFIDENCE_RANK.get((fact.get("confidence") or "").strip().upper(), -1)


def load_fragments(sources: list[Path]) -> list[dict]:
    """Read every fragment file and return the flat list of operations, in a stable order.

    `sources` may mix directories (globbed for *.json, sorted) and individual .json files.
    """
    files: list[Path] = []
    for src in sources:
        if src.is_dir():
            files.extend(sorted(src.glob("*.json")))
        elif src.is_file():
            files.append(src)
        else:
            raise FileNotFoundError(f"fragment source not found: {src}")

    operations: list[dict] = []
    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            ops = data.get("operations", [])
        elif isinstance(data, list):
            ops = data
        else:
            raise ValueError(f"{fp}: fragment must be an object or a list, got {type(data).__name__}")
        if not isinstance(ops, list):
            raise ValueError(f"{fp}: 'operations' must be a list")
        operations.extend(ops)
    return operations


def _merge_fact_list(section_key: str, existing: list[dict], incoming: list[dict],
                     entity_type: str, canonical_name: str, conflicts: list[dict]) -> list[dict]:
    """Merge two attribute/identifier lists by key, recording cross-source disagreements.

    Keeps the higher-confidence value on a key clash (ties keep the first-seen value) and appends
    a conflict record so the caller can emit an `add_conflict` op.
    """
    by_key: dict[str, dict] = {}
    order: list[str] = []
    for fact in list(existing) + list(incoming):
        fact = dict(fact)
        key = fact["key"]
        fact["value"] = eio.canonical_fact_value(key, fact["value"])
        if key not in by_key:
            by_key[key] = fact
            order.append(key)
            continue
        kept = by_key[key]
        if kept["value"] == fact["value"]:
            # same value; keep the higher-confidence copy for its note/source
            if _rank(fact) > _rank(kept):
                by_key[key] = fact
            continue
        # genuine disagreement across fragments
        winner, loser = (fact, kept) if _rank(fact) > _rank(kept) else (kept, fact)
        by_key[key] = winner
        conflicts.append({
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "section": section_key,
            "key": key,
            "value_a": winner["value"],
            "value_b": loser["value"],
        })
    return [by_key[k] for k in order]


def _dedup(items: list[dict], sig) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        s = sig(it)
        if s in seen:
            continue
        seen.add(s)
        out.append(it)
    return out


def merge_operations(operations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fold many fragments' operations into a minimal, collision-free operation list.

    Returns (merged_operations, conflict_records). Every conflict record also becomes an
    `add_conflict` op appended to merged_operations.
    """
    groups: dict[tuple[str, str], dict] = {}
    group_order: list[tuple[str, str]] = []
    passthrough: list[dict] = []
    conflicts: list[dict] = []

    for op in operations:
        kind = op.get("op")
        if kind != "upsert_entity":
            passthrough.append(op)
            continue

        etype = op.get("entity_type")
        name = op.get("canonical_name")
        if not etype or not name:
            raise ValueError(f"upsert_entity missing entity_type/canonical_name: {op}")
        key = (etype, name)

        if key not in groups:
            groups[key] = {
                "op": "upsert_entity",
                "entity_type": etype,
                "canonical_name": name,
                "aliases": list(op.get("aliases", [])),
                "identifiers": list(op.get("identifiers", [])),
                "attributes": list(op.get("attributes", [])),
                "relationships": list(op.get("relationships", [])),
                "events": list(op.get("events", [])),
                "sources": list(op.get("sources", [])),
                "_summaries": [op["summary"]] if op.get("summary") else [],
                "_statuses": [op["status"]] if op.get("status") else [],
                "_notes": [op["notes"]] if op.get("notes") else [],
                "perspectives": list(op.get("perspectives", [])),
            }
            group_order.append(key)
            continue

        g = groups[key]
        g["aliases"] += op.get("aliases", [])
        g["relationships"] += op.get("relationships", [])
        g["events"] += op.get("events", [])
        g["sources"] += op.get("sources", [])
        g["perspectives"] += op.get("perspectives", [])
        if op.get("summary"):
            g["_summaries"].append(op["summary"])
        if op.get("status"):
            g["_statuses"].append(op["status"])
        if op.get("notes"):
            g["_notes"].append(op["notes"])
        g["identifiers"] = _merge_fact_list(
            "identifiers", g["identifiers"], op.get("identifiers", []), etype, name, conflicts)
        g["attributes"] = _merge_fact_list(
            "attributes", g["attributes"], op.get("attributes", []), etype, name, conflicts)

    merged: list[dict] = []
    for key in group_order:
        g = groups[key]
        etype, name = key
        out: dict = {"op": "upsert_entity", "entity_type": etype, "canonical_name": name}
        if g["_statuses"]:
            out["status"] = g["_statuses"][0]
        if g["_summaries"]:
            out["summary"] = max(g["_summaries"], key=len)
        if g["_notes"]:
            out["notes"] = "\n\n".join(dict.fromkeys(g["_notes"]))
        aliases = list(dict.fromkeys(a for a in g["aliases"] if a and a != name))
        if aliases:
            out["aliases"] = aliases
        if g["identifiers"]:
            out["identifiers"] = g["identifiers"]
        if g["attributes"]:
            out["attributes"] = g["attributes"]
        rels = _dedup(g["relationships"],
                      lambda r: (r["rel_type"], r["target_type"], slug(r["target_name"])))
        if rels:
            out["relationships"] = rels
        events = _dedup(g["events"], lambda e: (e.get("date"), e.get("desc")))
        if events:
            out["events"] = events
        sources = _dedup(g["sources"], lambda s: (s.get("name"), s.get("archive")))
        if sources:
            out["sources"] = sources
        perspectives = _dedup(g["perspectives"], lambda p: json.dumps(p, sort_keys=True))
        if perspectives:
            out["perspectives"] = perspectives
        merged.append(out)

    # passthrough ops (merge_entity, rename_entity, prune_*, add_conflict, resolve_conflict, ...)
    merged.extend(_dedup(passthrough, lambda o: json.dumps(o, sort_keys=True)))

    # one auto add_conflict per detected cross-source disagreement
    for c in conflicts:
        merged.append({
            "op": "add_conflict",
            "entity_type": c["entity_type"],
            "canonical_name": c["canonical_name"],
            "title": f"{c['key']} disagreement (auto-detected across sources)",
            "value_a": c["value_a"],
            "value_b": c["value_b"],
        })

    return merged, conflicts


def _print_report(n_fragment_ops: int, merged: list[dict], conflicts: list[dict], out_path: Path) -> None:
    upserts = sum(1 for o in merged if o["op"] == "upsert_entity")
    rels = sum(len(o.get("relationships", [])) for o in merged if o["op"] == "upsert_entity")
    others = sum(1 for o in merged if o["op"] not in ("upsert_entity", "add_conflict"))
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  FRAGMENT MERGE{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    print(f"  {n_fragment_ops} operation(s) across fragments")
    print(f"  -> {CYAN}{upserts}{RESET} entity upsert(s), {rels} relationship edge(s), {others} other op(s)")
    if conflicts:
        print(f"\n  {YELLOW}{len(conflicts)} cross-source disagreement(s) auto-flagged as conflicts:{RESET}")
        for c in conflicts:
            print(f"    - {c['canonical_name']} [{c['section']}/{c['key']}]: "
                  f"{c['value_a']!r} vs {c['value_b']!r}")
    else:
        print(f"\n  {GREEN}no cross-source disagreements{RESET}")
    print(f"\n  wrote {BOLD}{out_path}{RESET}\n")


def main(argv: list[str]) -> int:
    args = argv[1:]  # drop the "merge_fragments" runner token
    out_arg: str | None = None
    batch_id: str | None = None
    positionals: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--out":
            i += 1
            out_arg = args[i] if i < len(args) else None
        elif a == "--batch-id":
            i += 1
            batch_id = args[i] if i < len(args) else None
        else:
            positionals.append(a)
        i += 1

    if not positionals:
        print("usage: python3 run.py merge_fragments <fragments_dir|files...> "
              "[--out manifest.json] [--batch-id ID]", file=sys.stderr)
        return 2

    sources = [Path(p) for p in positionals]

    # default output: <dir>/../manifest.json when a single directory was given
    if out_arg:
        out_path = Path(out_arg)
    elif len(sources) == 1 and sources[0].is_dir():
        out_path = sources[0].parent / "manifest.json"
    else:
        print(f"{RED}ERROR: --out is required unless a single fragments directory is given{RESET}",
              file=sys.stderr)
        return 2

    if batch_id is None:
        batch_id = out_path.parent.name

    try:
        operations = load_fragments(sources)
    except (FileNotFoundError, ValueError) as e:
        print(f"{RED}ERROR: {e}{RESET}", file=sys.stderr)
        return 1

    if not operations:
        print(f"{RED}HALT: fragments contained ZERO operations — extraction failure upstream.{RESET}",
              file=sys.stderr)
        return 1

    merged, conflicts = merge_operations(operations)
    manifest = {"manifest_version": "2.0", "batch_id": batch_id, "operations": merged}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _print_report(len(operations), merged, conflicts, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
