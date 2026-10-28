"""system_status.py - Metrics + the generated directory tree."""
from __future__ import annotations
import sys
from pathlib import Path

import paths
from registry import load_registry


def get_status(registry=None) -> dict:
    """Get system status metrics."""
    reg = registry or load_registry()

    stats = {
        "entities_by_type": {},
        "total_entities": 0,
        "batches": [],
        "inbox_files": 0
    }

    # Count entities by type
    for entity_type, info in reg._entity_types.items():
        entity_dir = paths.entity_dir(info["directory"])
        if entity_dir.exists():
            count = len(list(entity_dir.glob("*.md")))
            stats["entities_by_type"][entity_type] = count
            stats["total_entities"] += count

    # List batches
    if paths.BATCHES.exists():
        for batch_dir in paths.BATCHES.iterdir():
            if batch_dir.is_dir():
                manifest_path = batch_dir / "_manifest.json"
                if manifest_path.exists():
                    import json
                    manifest = json.loads(manifest_path.read_text())
                    stats["batches"].append({
                        "id": batch_dir.name,
                        "status": manifest.get("status", "UNKNOWN")
                    })

    # Count inbox files (exclude hidden/dot files only).
    # Report WHERE, not just how many: the inbox is an external Drive drop zone set by
    # DIALEXIS_INBOX_DIR (via ROOT/.env), with no repo-local fallback. Unconfigured and
    # empty are different states that must never render identically — see print_status.
    stats["inbox_all_files"] = []
    stats["inbox_skipped"] = []
    stats["inbox_dir"] = str(paths.INBOX) if paths.INBOX is not None else None
    stats["inbox_configured"] = paths.INBOX is not None
    stats["inbox_is_remote"] = paths.inbox_is_remote()
    stats["inbox_exists"] = paths.INBOX is not None and paths.INBOX.is_dir()
    if paths.INBOX is not None and paths.INBOX.exists():
        for f in paths.INBOX.iterdir():
            if not f.is_file():
                continue
            if f.name.startswith('.'):
                stats["inbox_skipped"].append(f.name)
            else:
                stats["inbox_all_files"].append(f.name)
        stats["inbox_files"] = len(stats["inbox_all_files"])

    return stats


def print_tree() -> None:
    """Print the directory tree structure."""
    print("dialexis/")
    _print_tree_recursive(paths.ROOT, prefix="", max_depth=3, current_depth=0)


def _print_tree_recursive(path: Path, prefix: str, max_depth: int, current_depth: int) -> None:
    """Recursively print directory tree."""
    if current_depth >= max_depth:
        return

    try:
        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return

    # Filter out hidden and unimportant files
    items = [i for i in items if not i.name.startswith(".") and i.name != "__pycache__"]

    for i, item in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        # Add annotation for key directories
        annotation = ""
        if item.name == "_index":
            annotation = " (generated indices)"
        elif item.name == "raw_inbox":
            annotation = " (verbatim immutable source copies)"
        elif item.name == "batches" and item.is_dir():
            count = len([d for d in item.iterdir() if d.is_dir()])
            annotation = f" ({count} active batches)" if count else " (empty)"

        print(f"{prefix}{connector}{item.name}{'/' if item.is_dir() else ''}{annotation}")

        if item.is_dir() and item.name not in ["__pycache__", ".git"]:
            _print_tree_recursive(item, prefix + extension, max_depth, current_depth + 1)


def print_banner() -> None:
    """Print the DIALEXIS v2 banner."""
    print("=" * 60)
    # Rainbow colors: Red, Yellow, Green, Cyan, Blue, Magenta
    text = "  D I A L E X I S   v 2.0"
    colors = [
        '\033[91m',  # Red
        '\033[93m',  # Yellow
        '\033[92m',  # Green
        '\033[96m',  # Cyan
        '\033[94m',  # Blue
        '\033[95m',  # Magenta
    ]
    reset = '\033[0m'

    # Apply rainbow colors across the text
    colored_text = ""
    color_index = 0
    for char in text:
        if char != ' ':
            colored_text += colors[color_index % len(colors)] + char
            color_index += 1
        else:
            colored_text += char
    colored_text += reset

    print(colored_text)
    print("  Personal Knowledge Management System")
    print("=" * 60)
    print()


def main(argv: list[str]) -> int:
    # Always show the banner first
    print_banner()

    if len(argv) > 1 and argv[1] == "tree":
        print_tree()
        return 0

    try:
        stats = get_status()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("System Status\n")
    print(f"Total entities: {stats['total_entities']}")
    print("\nEntities by type:")
    for etype, count in sorted(stats['entities_by_type'].items()):
        print(f"  {etype}: {count}")

    # Always name the inbox location. A bare count can't distinguish "nothing dropped yet"
    # from "this machine has no inbox configured", and staging silently no-ops in the
    # second case.
    if not stats.get('inbox_configured'):
        print("\nInbox: NOT CONFIGURED — DIALEXIS_INBOX_DIR is unset, and there is no "
              "repo-local fallback by design.")
        print(f"  Set it in {paths.ROOT / '.env'} (e.g. "
              "DIALEXIS_INBOX_DIR=<shared-inbox>); until then stage_inbox "
              "refuses to run.")
    else:
        # inbox_is_remote() means "external — apply the quiescence gate", NOT "is Drive",
        # so name Drive only when the path actually is one.
        _d = stats['inbox_dir'] or ""
        if "GDrive" in _d or "My Drive" in _d:
            origin = "Google Drive drop zone"
        elif stats.get('inbox_is_remote'):
            origin = "external drop zone"
        else:
            origin = "local path"
        noun = "file" if stats['inbox_files'] == 1 else "files"
        print(f"\nInbox: {stats['inbox_files']} {noun} in {stats['inbox_dir']}")
        print(f"  ({origin}, set by DIALEXIS_INBOX_DIR)")
        if not stats.get('inbox_exists'):
            print("  WARNING: that directory does not exist — nothing can be staged from it.")
    if stats['inbox_all_files']:
        for fname in stats['inbox_all_files']:
            print(f"  + {fname}")
    if stats['inbox_skipped']:
        for fname in stats['inbox_skipped']:
            print(f"  . (skipped dotfile: {fname})")
    print(f"Batches: {len(stats['batches'])}")
    if stats['batches']:
        for batch in stats['batches']:
            print(f"  {batch['id']}: {batch['status']}")

    # Kairos agenda status
    if paths.AGENDA.exists():
        try:
            from kairos.parse import parse_ledger
            from kairos.harvest import harvest_events
            actions = parse_ledger(paths.AGENDA_LEDGER)
            today_count = sum(1 for a in actions if a.status == "today")
            active_count = sum(1 for a in actions if a.status == "active")
            events = harvest_events(horizon_days=7)
            print(f"\nKairos Agenda:")
            print(f"  Active actions: {active_count}")
            print(f"  Today set: {today_count}/3")
            print(f"  Upcoming events (7d): {len(events)}")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
