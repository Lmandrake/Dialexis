"""Tests for kairos/cli.py - CLI dispatch and integration."""
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from registry import load_registry


def setup_workspace(tmpdir: Path):
    paths.ROOT = tmpdir
    paths.WORKING_MODEL = tmpdir / "graph"
    paths.INDEX_DIR = paths.WORKING_MODEL / "_index"
    paths.AGENTS = tmpdir / "agents"
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_LEDGER = paths.AGENDA / "actions.md"
    paths.AGENDA_ARCHIVE = paths.AGENDA / "archive"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"
    paths.WORKER_PROFILE = paths.AGENDA / "worker_profile.md"

    real_agents = Path(__file__).parent.parent.parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    reg = load_registry()
    for entity_type, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)
    paths.AGENDA.mkdir(parents=True, exist_ok=True)
    paths.AGENDA_ARCHIVE.mkdir(parents=True, exist_ok=True)
    paths.AGENDA_WORKLOG.mkdir(parents=True, exist_ok=True)
    paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)


def test_dispatch_unknown():
    """Test: unknown subcommand returns error."""
    print("Testing unknown subcommand...", end=" ")
    from cli import main
    result = main(["kairos", "nonexistent"])
    assert result == 1
    print("✓")


def test_dispatch_index():
    """Test: index subcommand runs successfully."""
    print("Testing index dispatch...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        from cli import main
        result = main(["kairos", "index"])
        assert result == 0
        assert (paths.INDEX_DIR / "agenda.md").exists()
    print("✓")


def test_capture_dry_run():
    """Test: capture --dry-run produces output but no file changes."""
    print("Testing capture dry-run...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        manifest = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_action",
                "title": "Test task",
                "next_action": "Do the thing",
                "estimate": "short",
                "energy": "shallow",
                "flavor": "admin",
            }]
        }
        manifest_file = Path(tmp) / "test_manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        from cli import main
        result = main(["kairos", "capture", str(manifest_file), "--dry-run"])
        assert result == 0
        assert not paths.AGENDA_LEDGER.exists()
    print("✓")


def test_today_add_and_show():
    """Test: today add/show workflow."""
    print("Testing today add/show...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # First create an action
        manifest = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_action",
                "title": "My task",
                "next_action": "Step 1",
                "estimate": "short",
                "energy": "shallow",
                "flavor": "admin",
            }]
        }
        from commit import commit_manifest
        commit_manifest(manifest)

        # Add to today
        from cli import main
        result = main(["kairos", "today", "add", "act_0001"])
        assert result == 0

        # Verify it's in today
        from parse import parse_ledger
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert actions[0].status == "today"
    print("✓")


def run_tests():
    test_dispatch_unknown()
    test_dispatch_index()
    test_capture_dry_run()
    test_today_add_and_show()
    print("\n✓ All CLI tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
