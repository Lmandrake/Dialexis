"""health_check.py - Unified database health check suite.

Runs all structural and semantic verification checks to ensure the database is:
- Whole (all entities accessible, no orphans)
- Coherent (format compliant, registry compliant)
- Reciprocal (all relationships have inverses)
- Properly formatted (markdown structure correct)

Exit code 0 = healthy, 1 = issues found, 2 = error
"""
from __future__ import annotations
import sys
import subprocess
from pathlib import Path

# Colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

def print_header(title: str):
    """Print formatted section header."""
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

def run_check(name: str, command: list[str], description: str) -> tuple[int, str]:
    """Run a health check command and return exit code and output."""
    print(f"{CYAN}▶ {name}{RESET}")
    print(f"  {description}")
    print()

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode, result.stdout
    except Exception as e:
        print(f"{RED}ERROR: {e}{RESET}", file=sys.stderr)
        return 2, str(e)

def main(argv: list[str]) -> int:
    full_mode = "--full" in argv or "-f" in argv

    print_header("DIALEXIS DATABASE HEALTH CHECK")

    issues_found = False

    # 1. Format Validation
    print(f"{BOLD}[1/4] FORMAT VALIDATION{RESET}")
    code, output = run_check(
        "validate",
        ["python3", "scripts/validate.py"],
        "Checking markdown structure, registry compliance, format correctness"
    )
    if code != 0:
        issues_found = True
        print(f"{RED}✗ Format validation found issues{RESET}\n")
    else:
        print(f"{GREEN}✓ Format validation passed{RESET}\n")

    # 2. Reciprocal Audit
    print(f"{BOLD}[2/4] RECIPROCAL CONSISTENCY{RESET}")
    code, output = run_check(
        "audit_reciprocals",
        ["python3", "scripts/audit_reciprocals.py"],
        "Verifying all relationships have required inverse edges"
    )
    if code != 0:
        issues_found = True
        print(f"{RED}✗ Reciprocal audit found missing reciprocals{RESET}")
        print(f"{YELLOW}  → Run: python3 run.py reconcile{RESET}\n")
    else:
        print(f"{GREEN}✓ All relationships have reciprocals{RESET}\n")

    # 3. Semantic Completeness
    print(f"{BOLD}[3/4] SEMANTIC COMPLETENESS{RESET}")
    semantic_args = ["--all"] if full_mode else []
    code, output = run_check(
        "audit_semantic_relationships",
        ["python3", "scripts/audit_semantic_relationships.py"] + semantic_args,
        "Checking for entities mentioned but not formally linked"
    )
    # Note: semantic audit returns 0 even with gaps (not a failure)
    if "Found 0 potential" in output or "No obvious semantic" in output:
        print(f"{GREEN}✓ No semantic gaps found{RESET}\n")
    else:
        print(f"{YELLOW}ℹ Semantic gaps found (review recommended){RESET}\n")

    # 4. Database Statistics
    print(f"{BOLD}[4/4] DATABASE STATISTICS{RESET}")
    code, output = run_check(
        "system_status",
        ["python3", "scripts/system_status.py"],
        "Overall database health metrics"
    )

    # Summary
    print_header("HEALTH CHECK SUMMARY")

    if issues_found:
        print(f"{RED}✗ ISSUES FOUND{RESET}\n")
        print("Recommended actions:")
        print(f"  1. Review validation errors above")
        print(f"  2. Run: {CYAN}python3 run.py reconcile{RESET}")
        print(f"  3. Re-run: {CYAN}python3 run.py health_check{RESET}")
        print()
        return 1
    else:
        print(f"{GREEN}✓ DATABASE HEALTHY{RESET}\n")
        print("All structural checks passed.")
        print("Review semantic gaps if shown above.")
        print()
        if not full_mode:
            print(f"{CYAN}Tip: Run with --full for complete semantic audit{RESET}\n")
        return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
