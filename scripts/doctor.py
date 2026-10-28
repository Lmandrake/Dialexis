#!/usr/bin/env python3
"""
doctor.py — Report the health of the Dialexis *environment* (not the graph).

    python3 run.py doctor
    python3 run.py doctor --strict     # exit 1 if any optional capability is missing

READ-ONLY. Mutates nothing, never touches graph/, safe to run anywhere.

Why this exists: the deterministic core is stdlib-only, but web retrieval is delegated to
Fetcher (FETCHER_DIR, default a sibling Fetcher checkout) and two of its capabilities are optional
third-party imports in THIS interpreter — and when they are absent they degrade
*silently into something that looks like a different problem*:

  - no playwright  -> every JS-rendered page (most of agency.example) returns HTTP 200 with an
                      empty body, which reads as "the site blocked us"
  - no pypdf       -> every PDF returns mojibake or a bare <title>, which reads as
                      "the fetch failed"

Both misdiagnoses cost real time. `doctor` makes the environment legible in one command,
so you check capability BEFORE concluding a source is unreachable.

For graph health use `run.py validate` / `health_check`; for the read-path timing profile
use `run.py profile_readpath`.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import paths

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
OK, BAD, WARN = f"{GREEN}✓{RESET}", f"{RED}✗{RESET}", f"{YELLOW}⚠{RESET}"

# (import name, pip name, what it unlocks, what breaks without it)
OPTIONAL_DEPS = [
    ("playwright", "playwright",
     "JS-rendered page fetching (web_fetch --render)",
     "JS pages return HTTP 200 with an empty body — looks like the site blocked you"),
    ("pypdf", "pypdf",
     "PDF text extraction (web_fetch on any .pdf)",
     "PDFs return mojibake or a bare title — looks like the fetch failed"),
    ("pytest", "pytest",
     "the full test suite (tests/test_index_store.py and friends)",
     "part of tests/ cannot run at all"),
]


def _probe(mod_name: str) -> tuple[bool, str]:
    """Import a module without blowing up. Returns (available, version-or-error)."""
    try:
        m = importlib.import_module(mod_name)
    except Exception as e:  # ImportError, but also broken installs
        return False, f"{type(e).__name__}: {e}"
    return True, str(getattr(m, "__version__", "") or "installed")


def _playwright_browser_present() -> tuple[bool, str]:
    """Playwright's Python binding is useless without a downloaded browser."""
    for base in (Path.home() / "Library/Caches/ms-playwright",      # macOS
                 Path.home() / ".cache/ms-playwright"):             # Linux
        if base.is_dir():
            hits = sorted(p.name for p in base.iterdir()
                          if p.is_dir() and p.name.startswith("chromium"))
            if hits:
                return True, f"{base} ({', '.join(hits)})"
            return False, f"{base} exists but holds no chromium build"
    return False, "no ms-playwright cache directory found"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=paths.ROOT, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    problems = 0
    warnings = 0

    print("Dialexis — environment doctor")
    print("=" * 62)

    # ---- Interpreter -----------------------------------------------------
    # Which python3 you are on decides whether the optional deps are even visible.
    # Background jobs may deliberately run under a different interpreter than your shell.
    print("\n[Interpreter]")
    exe = Path(sys.executable)
    print(f"  {DIM}executable{RESET}  {exe}")
    print(f"  {DIM}version{RESET}     {sys.version.split()[0]}")
    is_brew = "/opt/homebrew" in str(exe) or "/usr/local/Cellar" in str(exe)
    if is_brew:
        print(f"  {OK} Homebrew Python — optional deps installed here are visible to run.py")
    else:
        print(f"  {WARN} not Homebrew Python. Optional deps installed into the Homebrew "
              f"interpreter will NOT be importable here.")
        warnings += 1
    resolved = shutil.which("python3")
    if resolved and Path(resolved).resolve() != exe.resolve():
        print(f"  {DIM}note{RESET}        `python3` on PATH resolves to {resolved}")

    # ---- Optional capabilities ------------------------------------------
    print("\n[Optional capabilities]")
    for mod, pip_name, unlocks, breakage in OPTIONAL_DEPS:
        available, detail = _probe(mod)
        if available:
            print(f"  {OK} {mod:<12} {detail:<12} {DIM}{unlocks}{RESET}")
        else:
            problems += 1
            print(f"  {BAD} {mod:<12} {'MISSING':<12} {DIM}{unlocks}{RESET}")
            print(f"       without it: {breakage}")
            print(f"       fix: python3 -m pip install --break-system-packages {pip_name}")

    if _probe("playwright")[0]:
        ok, detail = _playwright_browser_present()
        if ok:
            print(f"  {OK} chromium     {DIM}{detail}{RESET}")
        else:
            problems += 1
            print(f"  {BAD} chromium     MISSING — {detail}")
            print(f"       fix: python3 -m playwright install chromium")

    # ---- Fetcher (all web retrieval) ------------------------------------
    print("\n[Web retrieval — Fetcher]")
    try:
        import fetcher_bridge
        ok, detail = fetcher_bridge.status()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    if ok:
        print(f"  {OK} Fetcher     {DIM}{detail}{RESET}")
    else:
        problems += 1
        print(f"  {BAD} Fetcher     MISSING — web_fetch/web_search cannot run")
        print(f"       {detail}")

    # ---- Core (stdlib-only, must always work) ---------------------------
    print("\n[Deterministic core]")
    try:
        from registry import load_registry
        reg = load_registry()
        print(f"  {OK} registry loads ({len(reg._entity_types)} entity types)")
    except Exception as e:
        problems += 1
        print(f"  {BAD} registry FAILED to load: {e}")

    wm = paths.WORKING_MODEL
    if wm.is_dir():
        n = sum(1 for _ in wm.glob("*/*.md"))
        print(f"  {OK} graph present ({n} entity files)")
    else:
        problems += 1
        print(f"  {BAD} graph MISSING at {wm}")

    try:
        import index_store
        dbp = index_store._db_path()
        if dbp.exists():
            size_mb = dbp.stat().st_size / 1_048_576
            print(f"  {OK} index cache {size_mb:.1f} MB at {dbp}")
        else:
            print(f"  {WARN} index cache absent (cold) — first read rebuilds it: {dbp}")
    except Exception as e:
        print(f"  {WARN} index cache unavailable: {e}")

    # ---- Inbox ----------------------------------------------------------
    print("\n[Inbox]")
    try:
        inbox = paths.resolve_inbox()
        env_override = os.environ.get("DIALEXIS_INBOX_DIR")
        label = f" {DIM}(via DIALEXIS_INBOX_DIR){RESET}" if env_override else ""
        if Path(inbox).is_dir():
            files = [p for p in Path(inbox).iterdir() if p.is_file()
                     and not p.name.startswith(".")]
            print(f"  {OK} {inbox}{label} — {len(files)} file(s)")
        else:
            print(f"  {WARN} {inbox}{label} does not exist")
            warnings += 1
    except Exception as e:
        print(f"  {WARN} could not resolve inbox: {e}")

    # ---- Git fence ------------------------------------------------------
    # Writes must happen on a fresh checkout; a stale one produces a divergent branch.
    print("\n[Git fence]")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        print(f"  {WARN} not a git repo (or git unavailable) — the write fence is git")
        warnings += 1
    else:
        print(f"  {DIM}branch{RESET}      {branch}")
        dirty = _git("status", "--porcelain")
        if dirty:
            n = len(dirty.splitlines())
            print(f"  {WARN} {n} uncommitted change(s) — commit + push after "
                  f"commit/reconcile/validate")
            warnings += 1
        else:
            print(f"  {OK} working tree clean")
        counts = _git("rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if counts:
            try:
                behind, ahead = (int(x) for x in counts.split())
                if behind:
                    print(f"  {WARN} {behind} commit(s) BEHIND upstream — pull before "
                          f"mutating (writing on a stale checkout diverges)")
                    warnings += 1
                if ahead:
                    print(f"  {WARN} {ahead} commit(s) ahead — push so other machines see it")
                    warnings += 1
                if not behind and not ahead:
                    print(f"  {OK} in sync with upstream")
            except ValueError:
                pass

    # ---- Staged batches -------------------------------------------------
    print("\n[Staged batches]")
    batches = paths.ROOT / "batches"
    staged = []
    if batches.is_dir():
        for d in sorted(batches.iterdir()):
            mf = d / "_manifest.json"
            if d.is_dir() and mf.exists():
                try:
                    import json
                    meta = json.loads(mf.read_text(encoding="utf-8"))
                    if meta.get("status") in ("STAGED", "EXTRACTED"):
                        staged.append((d.name, meta.get("status"),
                                       len(meta.get("files", []))))
                except Exception:
                    pass
    if staged:
        for name, status, nfiles in staged:
            print(f"  {WARN} {name} — {status}, {nfiles} source(s) never integrated")
        warnings += 1
    else:
        print(f"  {OK} no batches awaiting integration")

    # ---- Summary --------------------------------------------------------
    print("\n" + "=" * 62)
    if problems == 0 and warnings == 0:
        print(f"{GREEN}All capabilities present, environment clean.{RESET}")
        return 0
    bits = []
    if problems:
        bits.append(f"{RED}{problems} missing capability/ies{RESET}")
    if warnings:
        bits.append(f"{YELLOW}{warnings} warning(s){RESET}")
    print(", ".join(bits))
    if problems:
        print(f"{DIM}Missing capabilities do not corrupt anything — they degrade quietly, "
              f"which is the danger. Install them before concluding a source is "
              f"unreachable.{RESET}")
    return 1 if (problems and strict) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
