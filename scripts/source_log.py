#!/usr/bin/env python3
"""
source_log.py — Lightweight web source ledger for Dialexis.

A growing, append-only record of which web sources WORKED and which FAILED
during entity expansion and research. This is *light provenance*, not full
traceability: it captures outcome + target + context only, never response
bodies (those live in the per-entity archive/web_references/*_web_research_*.md
files when the agent chooses to save them).

The ledger is written deterministically by scripts/web_fetch.py and
scripts/web_search.py on every invocation, so it can never silently drift from
what actually happened. The agent (LLM) is responsible only for the succinct
on-screen WORKED/FAILED bullet report at the end of a web-expansion session.

Ledger location: archive/web_references/SOURCE_LOG.md

Usage:
    python3 scripts/source_log.py report          # Aggregate rollup by source
    python3 run.py source_log report              # (same, via runner)
    python3 scripts/source_log.py add --kind fetch --target URL \\
        --status 200 --ok --context "Entity" [--engine http] [--note "..."]
    python3 scripts/source_log.py --self-test     # Run embedded unit tests

Output (report):
    Per-domain (fetch) / per-engine (search) attempts, worked, failed, rate.
    Plus a distinct list of FAILED targets for manual follow-up.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import paths

# ---------------------------------------------------------------------------
# Ledger location
# ---------------------------------------------------------------------------


def _log_path() -> Path:
    """Read at call time, not import time: paths.ARCHIVE is re-pointed by the pytest
    isolation fixture and by DIALEXIS_ARCHIVE_DIR, so a module-level snapshot would
    write the ledger to the wrong (real, or stale) archive."""
    return paths.ARCHIVE / "web_references" / "SOURCE_LOG.md"


_COLUMNS = ["Date (UTC)", "Context", "Kind", "Target", "Engine", "Status", "Outcome", "Note"]

_PREAMBLE = (
    "# DIALEXIS Source Log\n"
    "\n"
    "Append-only ledger of web sources consulted during entity expansion and research.\n"
    "Auto-maintained by `scripts/web_fetch.py` and `scripts/web_search.py`. Light provenance\n"
    "only — outcome + target + context, never response bodies.\n"
    "\n"
    "`✓ WORKED` = usable content/results returned. `✗ FAILED` = blocked, errored, or\n"
    "empty (the target may STILL hold good information worth accessing manually).\n"
    "\n"
    "Run `python3 run.py source_log report` for a per-source rollup.\n"
    "\n"
)

WORKED = "✓ WORKED"
FAILED = "✗ FAILED"


# ---------------------------------------------------------------------------
# Field sanitization
# ---------------------------------------------------------------------------

def _cell(value) -> str:
    """Sanitize a value for safe inclusion in a markdown table cell."""
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    s = s.replace("|", "\\|")
    s = " ".join(s.split())  # collapse runs of whitespace
    return s.strip()


def domain_of(url: str) -> str:
    """Extract the hostname (domain) from a URL; fall back to the raw string."""
    try:
        netloc = urlparse(url).netloc
        if netloc:
            return netloc.lower()
    except Exception:
        pass
    return (url or "").strip() or "(unknown)"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _ensure_header(path: Path) -> None:
    """Create the ledger file with preamble + table header if it does not exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _PREAMBLE
    header += "| " + " | ".join(_COLUMNS) + " |\n"
    header += "|" + "|".join(["---"] * len(_COLUMNS)) + "|\n"
    path.write_text(header, encoding="utf-8")


def record(kind: str, target: str, *, status: "str | int" = "", ok: bool = False,
           context: str = "", engine: str = "", note: str = "",
           path: Path | None = None) -> None:
    """
    Append one outcome row to the source ledger.

    kind    : "fetch" | "search" (free-form, but those two are conventional)
    target  : the URL (fetch) or query string (search)
    status  : HTTP status for fetch, "-" for search
    ok      : True if the source produced usable content/results
    context : the entity/purpose this source was consulted for
    engine  : "http" | "render" (fetch) or search engine name
    note    : short free text (error message, result count, etc.)
    """
    log_path = path or _log_path()
    _ensure_header(log_path)

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        _cell(context) or "-",
        _cell(kind),
        _cell(target),
        _cell(engine) or "-",
        _cell(status) or "-",
        WORKED if ok else FAILED,
        _cell(note),
    ]
    line = "| " + " | ".join(row) + " |\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Reading / reporting
# ---------------------------------------------------------------------------

def _parse_rows(path: Path | None = None) -> list[dict]:
    """Parse ledger data rows into dicts. Skips preamble and header rows."""
    log_path = path or _log_path()
    if not log_path.exists():
        return []

    rows: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        inner = line.strip("|")
        # Split on unescaped pipes only, then restore any escaped pipes.
        cells = [re.sub(r"\\\|", "|", c).strip()
                 for c in re.split(r"(?<!\\)\|", inner)]
        if len(cells) != len(_COLUMNS):
            continue
        # Skip the header row and its separator
        if cells[0] == _COLUMNS[0] or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(dict(zip(_COLUMNS, cells)))
    return rows


def summarize(path: Path | None = None) -> dict:
    """Aggregate ledger rows by source key (domain for fetch, engine for search)."""
    rows = _parse_rows(path)
    sources: dict[str, dict] = {}
    failed_targets: list[dict] = []

    for r in rows:
        kind = r["Kind"]
        if kind == "search":
            key = f"search:{r['Engine']}"
        else:
            key = domain_of(r["Target"])

        s = sources.setdefault(key, {"attempts": 0, "worked": 0, "failed": 0})
        s["attempts"] += 1
        if r["Outcome"] == WORKED:
            s["worked"] += 1
        else:
            s["failed"] += 1
            failed_targets.append(r)

    return {"total": len(rows), "sources": sources, "failed_targets": failed_targets}


def report(path: Path | None = None) -> str:
    """Render a compact human-readable rollup of the ledger."""
    data = summarize(path)
    if data["total"] == 0:
        return "Source log is empty (no web sources recorded yet)."

    lines = [
        "=== DIALEXIS SOURCE LOG — ROLLUP ===",
        f"Total records: {data['total']}",
        "",
        f"{'Source':<45} {'Att':>4} {'OK':>4} {'Fail':>5} {'Rate':>6}",
        f"{'-' * 45} {'-' * 4} {'-' * 4} {'-' * 5} {'-' * 6}",
    ]
    for key in sorted(data["sources"]):
        s = data["sources"][key]
        rate = (s["worked"] / s["attempts"] * 100) if s["attempts"] else 0.0
        lines.append(f"{key[:45]:<45} {s['attempts']:>4} {s['worked']:>4} "
                     f"{s['failed']:>5} {rate:>5.0f}%")

    failed = data["failed_targets"]
    lines.append("")
    if failed:
        lines.append(f"FAILED sources for manual follow-up ({len(failed)}):")
        seen = set()
        for r in failed:
            tgt = r["Target"]
            if tgt in seen:
                continue
            seen.add(tgt)
            note = f" — {r['Note']}" if r["Note"] else ""
            lines.append(f"  ✗ [{r['Context']}] {tgt}{note}")
    else:
        lines.append("No FAILED sources recorded.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test suite
# ---------------------------------------------------------------------------

def _run_self_tests() -> int:
    """Run embedded unit tests against a temp ledger. Returns 0 if all pass."""
    import tempfile

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            print(f"  \033[32m✓\033[0m {name}")
            passed += 1
        else:
            print(f"  \033[31m✗\033[0m {name}: {detail}")
            failed += 1

    print("source_log.py — self-test")
    print("=" * 50)

    # Test 1: _cell sanitization
    print("\n[_cell sanitization]")
    check("escapes pipes", _cell("a|b") == "a\\|b", f"got: {_cell('a|b')!r}")
    check("collapses newlines", _cell("a\nb\nc") == "a b c", f"got: {_cell(chr(10).join('abc'))!r}")
    check("collapses whitespace", _cell("  x   y  ") == "x y", f"got: {_cell('  x   y  ')!r}")
    check("handles None", _cell(None) == "", f"got: {_cell(None)!r}")

    # Test 2: domain_of
    print("\n[domain_of]")
    check("extracts host", domain_of("https://www.university.example/x/y") == "www.university.example",
          f"got: {domain_of('https://www.university.example/x/y')!r}")
    check("lowercases host", domain_of("https://EXAMPLE.com") == "example.com",
          f"got: {domain_of('https://EXAMPLE.com')!r}")
    check("falls back on non-url", domain_of("not a url") == "not a url",
          f"got: {domain_of('not a url')!r}")

    # Tests 3-6 use a temp ledger (never touches the real one)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "SOURCE_LOG.md"

        # Test 3: header creation
        print("\n[header creation]")
        record("fetch", "https://researcher.example/", status=200, ok=True,
               context="Dagmar Nightingale", engine="http", path=tmp)
        content = tmp.read_text(encoding="utf-8")
        check("file created", tmp.exists())
        check("has title", "# DIALEXIS Source Log" in content)
        check("has table header", "| Date (UTC) |" in content)
        check("has separator", "|---|" in content.replace(" ", ""))

        # Test 4: row content
        print("\n[row content]")
        check("logs target", "https://researcher.example/" in content)
        check("logs context", "Dagmar Nightingale" in content)
        check("worked marker present", WORKED in content, "missing WORKED glyph")

        # Test 5: failed row + pipe escaping
        print("\n[failed row]")
        record("fetch", "https://www.university.example/x", status=0, ok=False,
               context="Dagmar Nightingale", engine="render",
               note="Playwright render failed: Timeout | goto", path=tmp)
        content2 = tmp.read_text(encoding="utf-8")
        check("failed marker present", FAILED in content2, "missing FAILED glyph")
        check("pipe in note escaped", "Timeout \\| goto" in content2,
              "note pipe not escaped")

        # Test 6: search row
        record("search", "Dagmar Nightingale urban heat", status="-", ok=True,
               context="Dagmar Nightingale", engine="duckduckgo", note="8 results", path=tmp)

        # Test 7: parse + summarize
        print("\n[parse + summarize]")
        rows = _parse_rows(tmp)
        check("parses 3 data rows", len(rows) == 3, f"got {len(rows)}")
        data = summarize(tmp)
        check("total is 3", data["total"] == 3, f"got {data['total']}")
        check("counts one failed", len(data["failed_targets"]) == 1,
              f"got {len(data['failed_targets'])}")
        check("researcher.example worked", data["sources"].get("researcher.example", {}).get("worked") == 1,
              f"got: {data['sources'].get('researcher.example')}")
        check("search bucket present", "search:duckduckgo" in data["sources"],
              f"buckets: {list(data['sources'])}")

        # Test 8: report renders
        print("\n[report rendering]")
        rep = report(tmp)
        check("report has header", "ROLLUP" in rep)
        check("report lists failed", "manual follow-up" in rep)
        check("report shows domain", "researcher.example" in rep)

        # Test 9: empty ledger report
        print("\n[empty ledger]")
        empty = Path(td) / "EMPTY.md"
        check("empty report message", "empty" in report(empty).lower(),
              f"got: {report(empty)!r}")

    # Summary
    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f", \033[31m{failed} FAILED\033[0m")
        return 1
    print(f" \033[32mALL PASSED\033[0m")
    return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Lightweight web source ledger for Dialexis")
    parser.add_argument("command", nargs="?", default="report",
                        choices=["report", "add"],
                        help="report (default) or add")
    parser.add_argument("--self-test", action="store_true",
                        help="Run embedded unit tests")
    # 'add' options (manual entry; scripts normally call record() directly)
    parser.add_argument("--kind", default="fetch", help="fetch or search")
    parser.add_argument("--target", help="URL (fetch) or query (search)")
    parser.add_argument("--status", default="", help="HTTP status or '-'")
    parser.add_argument("--ok", action="store_true", help="Mark source as WORKED")
    parser.add_argument("--context", default="", help="Entity/purpose")
    parser.add_argument("--engine", default="", help="http/render or search engine")
    parser.add_argument("--note", default="", help="Short free text")

    args = parser.parse_args(argv[1:] if argv else [])

    if args.self_test:
        return _run_self_tests()

    if args.command == "add":
        if not args.target:
            parser.error("add requires --target")
            return 1
        record(args.kind, args.target, status=args.status, ok=args.ok,
               context=args.context, engine=args.engine, note=args.note)
        print(f"Recorded: {WORKED if args.ok else FAILED} {args.target}")
        return 0

    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
