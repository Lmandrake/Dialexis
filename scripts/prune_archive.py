"""prune_archive.py - Rolling die-off for archived source copies.

    python3 run.py prune_archive              # dry-run: list raw_inbox files > 60 days old
    python3 run.py prune_archive --apply      # actually delete them (logs to PRUNED_LOG.md)
    python3 run.py prune_archive --days 90    # custom retention window

Retention age is read from each file's leading YYYYMMDD name prefix (the archive date stamped
by stage_inbox), NOT mtime — original mtimes are unreliable. Files without a parseable date
prefix are skipped. Scope is archive/raw_inbox/ only. Destructive; dry-run is the default and
--apply is required to delete. Every deletion is recorded in archive/PRUNED_LOG.md.
"""
from __future__ import annotations
import re
import sys
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import paths

DATE_PREFIX = re.compile(r"(\d{8})")
LEDGER_HEADER = (
    "# Pruned Source Archive Ledger\n\n"
    "Audit trail for source copies deleted from `archive/raw_inbox/` by the 60-day die-off.\n"
    "A dangling `archive=` provenance pointer can be traced to a row here.\n\n"
    "| pruned_on | archived_on | size | sha256_16 | filename |\n"
    "|---|---|---|---|---|\n"
)


def _ledger() -> Path:
    """Read at call time, not import time: paths.ARCHIVE is re-pointed by the pytest
    isolation fixture and (soon) by DIALEXIS_ARCHIVE_DIR, so a module-level snapshot
    would write to the real archive."""
    return paths.ARCHIVE / "PRUNED_LOG.md"


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f}{unit}" if unit != "B" else f"{int(f)}B"
        f /= 1024
    return f"{f:.1f}GB"


def main(argv: list[str]) -> int:
    args = argv[1:]
    days = 60
    apply = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--apply":
            apply = True
        elif a == "--days":
            i += 1
            if i >= len(args) or not args[i].isdigit():
                print("ERROR: --days requires an integer", file=sys.stderr)
                return 2
            days = int(args[i])
        else:
            print(f"ERROR: unknown argument: {a}", file=sys.stderr)
            return 2
        i += 1

    cutoff = date.today() - timedelta(days=days)

    candidates: list[tuple[date, int, Path]] = []  # (archived_date, size, path)
    skipped = 0
    for path in sorted(paths.RAW_INBOX.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        m = DATE_PREFIX.match(path.name)
        if not m:
            skipped += 1
            continue
        try:
            archived = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            skipped += 1
            continue
        if archived < cutoff:
            candidates.append((archived, path.stat().st_size, path))

    total = sum(sz for _, sz, _ in candidates)
    print(f"Retention: delete raw_inbox source copies archived before {cutoff.isoformat()} "
          f"(> {days} days old)")
    print(f"Candidates: {len(candidates)} file(s), {_human(total)} reclaimable"
          + (f"  |  {skipped} file(s) skipped (no date prefix)" if skipped else ""))

    if not candidates:
        print("Nothing to prune.")
        return 0

    for archived, sz, path in candidates:
        print(f"  {archived.isoformat()}  {_human(sz):>8}  {path.name}")

    if not apply:
        print("\nDry-run only. Re-run with --apply to delete (logged to archive/PRUNED_LOG.md).")
        return 0

    ledger = _ledger()
    if not ledger.exists():
        ledger.write_text(LEDGER_HEADER, encoding="utf-8")
    pruned_on = date.today().isoformat()
    rows = []
    deleted = 0
    for archived, sz, path in candidates:
        digest = sha256(path.read_bytes()).hexdigest()[:16]
        rows.append(f"| {pruned_on} | {archived.isoformat()} | {_human(sz)} | {digest} | {path.name} |\n")
        path.unlink()
        deleted += 1
    with ledger.open("a", encoding="utf-8") as fh:
        fh.writelines(rows)

    print(f"\nDeleted {deleted} file(s), reclaimed {_human(total)}. "
          f"Logged to {paths.display_path(ledger)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
