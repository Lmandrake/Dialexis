"""Read-path access log — the only instrument that can see Dialexis SUCCEEDING.

Why this exists
---------------
Dialexis's own git history records only its failures. Read-only lookups never commit, so
the 45 repair commits in `graph/`'s history are misses and corrections; a lookup that
worked left no trace anywhere. That asymmetry made the owner doubt the project was
publishable at all — success looked unmeasurable.

It turned out the successes were being recorded, just in the *consumers'* repositories:
a downstream tool counts what it got and says so in its own commit message. Four such
records exist across ~40 searched locations (see `docs/paper/HISTORY.md`). This module makes
that counting automatic instead of accidental.

The verdict is not inferred here. `entity.py` and `resolve.py` already decide, on
distinct code paths, whether a lookup was SERVED, DECLINED or MISSED — they just printed
it and threw it away. This records what they already knew.

DECLINED is a success, not a failure
------------------------------------
An ambiguous lookup that returns a candidate list rather than its top guess is the system
working: "a wrong attribution is worse than a declined lookup." Two
independent consumers wrote that down unprompted. Do not aggregate DECLINED with MISS.

Two artifacts, deliberately
---------------------------
- `.access-log.jsonl` — raw, one line per event, **gitignored**: one line per lookup would
  spam the history, and the sync job commits untracked files ~127 s after they settle.
- `docs/paper/ACCESS_METRICS.md` — the roll-up, **tracked**, counts only and no query
  text, regenerable with `run.py access_log --rollup`. This is the citable artifact; an
  unversioned evidence file would contradict the freeze-and-hash discipline used
  everywhere else in this project.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import paths

VERDICTS = ("SERVED", "DECLINED", "MISS")

# Overridable so a test run cannot contaminate the real metric: tests/conftest.py points
# this at a temp file. Discovered the hard way — the first full suite run after wiring the
# call sites added 4 phantom lookups to the production log.
LOG_PATH = Path(os.environ.get("DIALEXIS_ACCESS_LOG")
                or Path(paths.graph_root() or paths.ROOT) / ".access-log.jsonl")
REPORT_PATH = Path(paths.ROOT) / "docs" / "paper" / "ACCESS_METRICS.md"


def _append(row: dict) -> None:
    """Append one JSON line. Never raises: instrumentation must not break the read path."""
    try:
        row["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record(cmd: str, query: str, verdict: str, n: int = 0) -> None:
    """Record one CLI lookup. `n` is the candidate count (0 for a miss, 1 for a clean hit)."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    _append({"via": "cli", "cmd": cmd, "q": query, "verdict": verdict, "n": n})


def record_external(consumer: str, served: int = 0, declined: int = 0,
                    absent: int = 0, note: str = "") -> None:
    """Record consumption that happened OUTSIDE the CLI.

    For a consumer that read `graph/` directly, or a roster cross-referenced by hand —
    the episodes that produced the only real denominators available (a conference roster
    resolving 32 speakers, declining 7, and finding 25 absent). Episode-level by nature,
    so it is summed separately from per-lookup CLI events and never pooled with them.
    """
    if not consumer.strip():
        raise ValueError("record_external needs a consumer name")
    if served <= 0 and declined <= 0 and absent <= 0:
        raise ValueError("an episode with no counts records nothing")
    _append({"via": "external", "consumer": consumer.strip(), "served": served,
             "declined": declined, "absent": absent, "note": note})


def rollup(log_path: Path | None = None) -> dict:
    """Aggregate the raw log. A corrupt line is skipped and counted, never fatal."""
    p = log_path or LOG_PATH
    cli: Counter = Counter()
    ext = {"episodes": 0, "served": 0, "declined": 0, "absent": 0}
    skipped = 0
    consumers: Counter = Counter()

    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                skipped += 1
                continue
            if row.get("via") == "external":
                ext["episodes"] += 1
                for k in ("served", "declined", "absent"):
                    ext[k] += int(row.get(k, 0) or 0)
                consumers[row.get("consumer", "?")] += 1
            else:
                v = row.get("verdict")
                if v in VERDICTS:
                    cli[v] += 1
                else:
                    skipped += 1

    cli_out = {v: cli.get(v, 0) for v in VERDICTS}
    cli_out["asked"] = sum(cli_out[v] for v in VERDICTS)
    return {"cli": cli_out, "external": ext, "consumers": dict(consumers),
            "skipped": skipped}


def render_report(r: dict) -> str:
    """Render the tracked roll-up. Carries counts only — never query text."""
    c, e = r["cli"], r["external"]
    asked = c["asked"]

    def pct(x: int) -> str:
        return f"{100.0 * x / asked:.1f}%" if asked else "—"

    lines = [
        "# Access metrics — how often the graph answers",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by "
        "`run.py access_log --rollup`. Regenerate rather than edit.",
        "",
        "**This file carries counts only. No query text, by construction** — the raw log "
        "(`.access-log.jsonl`) is gitignored because it is a day-by-day trace of what its "
        "owner was thinking about.",
        "",
        "**`DECLINED` is a success, not a failure.** An ambiguous lookup that returns a "
        "candidate list instead of its top guess is the system refusing to fabricate. Two "
        "independent downstream consumers recorded that behaviour unprompted and valued it "
        "(`docs/paper/HISTORY.md`). Never pool `DECLINED` with `MISS`.",
        "",
        "## Command-line lookups (per-lookup)",
        "",
        "| Verdict | Meaning | n | Share |",
        "|---|---|---|---|",
        f"| SERVED | a record was returned | {c['SERVED']} | {pct(c['SERVED'])} |",
        f"| DECLINED | ambiguous; candidates listed, no guess | {c['DECLINED']} | {pct(c['DECLINED'])} |",
        f"| MISS | nothing matched | {c['MISS']} | {pct(c['MISS'])} |",
        f"| **asked** | total lookups | **{asked}** | |",
        "",
    ]

    if e["episodes"]:
        lines += [
            "## External consumption (episode-level)",
            "",
            "Consumption that did not go through the CLI — a consumer reading `graph/` "
            "directly, or a roster cross-referenced by hand. **Summed separately and never "
            "pooled with the per-lookup counts above**, because these are episodes carrying "
            "their own denominators; averaging the two would invent a rate.",
            "",
            f"- episodes recorded: **{e['episodes']}**",
            f"- items served: **{e['served']}**",
            f"- declined as ambiguous: **{e['declined']}**",
            f"- absent from the graph: **{e['absent']}**",
            "",
        ]
        if r["consumers"]:
            lines.append("Consumers: " + ", ".join(
                f"{k} ({v})" for k, v in sorted(r["consumers"].items())) + ".")
            lines.append("")
    else:
        lines += [
            "## External consumption (episode-level)",
            "",
            "None recorded yet. Append one with:",
            "",
            "```",
            'python3 run.py access_log --external "Roster A" \\',
            "    --served 32 --declined 7 --absent 25 --note \"conference roster cross-ref\"",
            "```",
            "",
        ]

    if r["skipped"]:
        lines += [f"_{r['skipped']} unparseable log line(s) skipped._", ""]

    lines += [
        "## What these numbers can and cannot support",
        "",
        "**Can:** a served/declined/missed split over real lookups, with a denominator — "
        "the thing no amount of git archaeology could produce, because success leaves no "
        "trace in this repository.",
        "",
        "**Cannot:** a claim that the graph is complete, or that a MISS is a defect. A miss "
        "may mean the owner deliberately never fed that source in, which is a scoping "
        "decision and not an error (`docs/paper/HISTORY.md`, failure class 3a). Only the owner can "
        "tell a deliberate exclusion from an unintended gap.",
        "",
        "**Also not captured:** lookups made by reading an entity file directly rather than "
        "through `entity`/`resolve`. Those are invisible here — though a direct read implies "
        "the caller already knew the filename, so the resolution had succeeded upstream.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    args = argv[1:]

    if "--rollup" in args or not args:
        r = rollup()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(render_report(r), encoding="utf-8")
        c = r["cli"]
        print(f"asked={c['asked']}  SERVED={c['SERVED']}  DECLINED={c['DECLINED']}  "
              f"MISS={c['MISS']}  external_episodes={r['external']['episodes']}")
        print(f"Wrote {REPORT_PATH.relative_to(paths.ROOT)}")
        return 0

    if "--external" in args:
        i = args.index("--external")
        if i + 1 >= len(args):
            print("ERROR: --external needs a consumer name", file=sys.stderr)
            return 2
        consumer = args[i + 1]

        def opt(flag: str) -> int:
            if flag in args:
                j = args.index(flag)
                if j + 1 < len(args):
                    try:
                        return int(args[j + 1])
                    except ValueError:
                        pass
            return 0

        note = ""
        if "--note" in args:
            j = args.index("--note")
            if j + 1 < len(args):
                note = args[j + 1]
        try:
            record_external(consumer, served=opt("--served"),
                            declined=opt("--declined"), absent=opt("--absent"),
                            note=note)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Recorded external consumption by {consumer!r}. "
              "Run `run.py access_log --rollup` to refresh the tracked report.")
        return 0

    print(__doc__)
    print("Usage:\n  run.py access_log --rollup\n"
          '  run.py access_log --external "<consumer>" '
          "[--served N] [--declined N] [--absent N] [--note \"...\"]",
          file=sys.stderr)
    return 2
