#!/usr/bin/env python3
"""run.py - Unified script runner that ensures correct working directory context.

Usage:
    python3 run.py <script_name> [args...]
    python3 run.py system_status
    python3 run.py stage_inbox
    python3 run.py validate --report
    python3 run.py --graph-root examples/demo-graph entity "Ada"   # any content world

STARTUP / BOOT COMMAND:
    python3 run.py system_status
    This is the entry point for "run dialexis" — it displays the system banner,
    entity counts, inbox status, and batch state. Run this first when starting
    a Dialexis session. See CLAUDE.md for full LLM agent instructions.

This runner:
- Works from any directory (finds project root automatically)
- Provides clear error messages if script doesn't exist
- Ensures consistent invocation pattern
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Find project root (where this file lives)
PROJECT_ROOT = Path(__file__).parent.resolve()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

def _take_graph_root(argv: list[str]) -> list[str]:
    """Strip a leading `--graph-root PATH` (or `--graph-root=PATH`) and export it as
    DIALEXIS_GRAPH_ROOT before any script imports paths. See paths.graph_root()."""
    if len(argv) > 1 and argv[1].startswith("--graph-root"):
        flag = argv[1]
        if "=" in flag:
            value, rest = flag.split("=", 1)[1], argv[2:]
        elif len(argv) > 2:
            value, rest = argv[2], argv[3:]
        else:
            print("ERROR: --graph-root needs a directory", file=sys.stderr)
            sys.exit(2)
        if not Path(value).expanduser().is_dir():
            print(f"ERROR: --graph-root {value}: not a directory", file=sys.stderr)
            sys.exit(2)
        os.environ["DIALEXIS_GRAPH_ROOT"] = str(Path(value).expanduser().resolve())
        return [argv[0], *rest]
    return argv


def main(argv: list[str]) -> int:
    argv = _take_graph_root(argv)
    if len(argv) < 2:
        print("Usage: python3 run.py <script_name> [args...]", file=sys.stderr)
        print("\nAvailable scripts:", file=sys.stderr)
        if SCRIPTS_DIR.exists():
            scripts = sorted(p.stem for p in SCRIPTS_DIR.glob("*.py")
                           if not p.name.startswith("_") and p.stem != "paths")
            for script in scripts:
                print(f"  - {script}", file=sys.stderr)
        return 0

    script_name = argv[1]
    if not script_name.endswith(".py"):
        script_name += ".py"

    script_path = SCRIPTS_DIR / script_name

    if not script_path.exists():
        # Fallback: check for a package (scripts/<name>/__init__.py)
        package_init = SCRIPTS_DIR / script_name.removesuffix(".py") / "__init__.py"
        if package_init.exists():
            script_path = package_init
        else:
            print(f"ERROR: Script not found: {script_path}", file=sys.stderr)
            print("\nAvailable scripts:", file=sys.stderr)
            if SCRIPTS_DIR.exists():
                scripts = sorted(p.stem for p in SCRIPTS_DIR.glob("*.py")
                               if not p.name.startswith("_") and p.stem != "paths")
                for script in scripts:
                    print(f"  - {script}", file=sys.stderr)
            return 1

    # Import and run the script's main function
    sys.path.insert(0, str(SCRIPTS_DIR))

    try:
        import importlib
        import importlib.util

        # For packages (__init__.py), use import_module so relative imports work
        pkg_name = script_name.removesuffix(".py")
        is_package = script_path.name == "__init__.py"

        if is_package:
            module = importlib.import_module(pkg_name)
        else:
            spec = importlib.util.spec_from_file_location(pkg_name, script_path)
            if spec is None or spec.loader is None:
                print(f"ERROR: Could not load script: {script_path}", file=sys.stderr)
                return 1
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        if hasattr(module, "main"):
            # Try to pass args, but handle scripts that don't accept them
            import inspect
            sig = inspect.signature(module.main)
            if len(sig.parameters) > 0:
                return module.main(argv[1:])
            else:
                return module.main()
        else:
            print(f"ERROR: Script {script_name} has no main() function", file=sys.stderr)
            return 1

    except Exception as e:
        print(f"ERROR running {script_name}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
