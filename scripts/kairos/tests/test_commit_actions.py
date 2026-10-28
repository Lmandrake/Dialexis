"""Tests for commit.py ledger operations (upsert_action, archive_action)."""
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from registry import load_registry
from commit import commit_manifest, CommitError
from parse import parse_ledger, serialize_ledger, Action, TODAY_CAP


def setup_workspace(tmpdir: Path):
    """Set up a temp workspace with registry and directories."""
    paths.ROOT = tmpdir
    paths.WORKING_MODEL = tmpdir / "graph"
    paths.AGENTS = tmpdir / "agents"
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_LEDGER = paths.AGENDA / "actions.md"
    paths.AGENDA_ARCHIVE = paths.AGENDA / "archive"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"

    # Copy registry
    real_agents = Path(__file__).parent.parent.parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    # Create entity directories
    reg = load_registry()
    for entity_type, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)

    # Create agenda directories
    paths.AGENDA.mkdir(parents=True, exist_ok=True)
    paths.AGENDA_ARCHIVE.mkdir(parents=True, exist_ok=True)


def test_upsert_new_action():
    """Test: upsert_action creates a new action in the ledger."""
    print("Testing upsert new action...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        manifest = {
            "manifest_version": "2.0",
            "operations": [
                {
                    "op": "upsert_action",
                    "title": "Write proposal section 3",
                    "next_action": "Open the Google Doc and outline the three main points",
                    "project": "[[bridge]]",
                    "estimate": "short",
                    "energy": "deep",
                    "flavor": "writing",
                }
            ]
        }
        result = commit_manifest(manifest)
        assert result["actions_written"] == 1

        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].id == "act_0001"
        assert actions[0].title == "Write proposal section 3"
        assert actions[0].project == "[[bridge]]"
        assert actions[0].status == "active"
    print("✓")


def test_upsert_update_action():
    """Test: upsert_action updates an existing action."""
    print("Testing upsert update action...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # Create first
        manifest = {
            "manifest_version": "2.0",
            "operations": [
                {
                    "op": "upsert_action",
                    "title": "Draft email",
                    "next_action": "Open Gmail",
                    "estimate": "≤15m",
                    "energy": "shallow",
                    "flavor": "admin",
                }
            ]
        }
        commit_manifest(manifest)

        # Update
        manifest2 = {
            "manifest_version": "2.0",
            "operations": [
                {
                    "op": "upsert_action",
                    "id": "act_0001",
                    "status": "today",
                }
            ]
        }
        commit_manifest(manifest2)

        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert actions[0].status == "today"
    print("✓")


def test_today_cap_enforced():
    """Test: TODAY_CAP refuses a 4th today item."""
    print("Testing TODAY_CAP enforcement...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # Create TODAY_CAP actions set to today
        for i in range(TODAY_CAP):
            manifest = {
                "manifest_version": "2.0",
                "operations": [{
                    "op": "upsert_action",
                    "title": f"Task {i+1}",
                    "next_action": f"Do thing {i+1}",
                    "estimate": "short",
                    "energy": "shallow",
                    "flavor": "admin",
                    "status": "today",
                }]
            }
            commit_manifest(manifest)

        # Try to add one more
        manifest_over = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_action",
                "title": "One too many",
                "next_action": "This should fail",
                "estimate": "short",
                "energy": "shallow",
                "flavor": "admin",
                "status": "today",
            }]
        }
        try:
            commit_manifest(manifest_over)
            assert False, "Should have raised CommitError"
        except CommitError as e:
            assert "TODAY_CAP" in str(e)
    print("✓")


def test_archive_action():
    """Test: archive_action moves to quarterly archive file."""
    print("Testing archive action...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # Create action
        manifest = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_action",
                "title": "Quick task",
                "next_action": "Just do it",
                "estimate": "≤15m",
                "energy": "shallow",
                "flavor": "admin",
            }]
        }
        commit_manifest(manifest)

        # Archive it
        manifest2 = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "archive_action",
                "id": "act_0001",
                "status": "done",
            }]
        }
        result = commit_manifest(manifest2)
        assert result["actions_archived"] == 1

        # Active ledger should be empty
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 0

        # Archive file should contain the action
        archive_files = list(paths.AGENDA_ARCHIVE.glob("actions_*.md"))
        assert len(archive_files) == 1
        archived = parse_ledger(archive_files[0])
        assert len(archived) == 1
        assert archived[0].id == "act_0001"
        assert archived[0].status == "done"
    print("✓")


def test_mixed_manifest():
    """Test: manifest with entity + action ops commits atomically."""
    print("Testing mixed manifest...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        manifest = {
            "manifest_version": "2.0",
            "operations": [
                {
                    "op": "upsert_entity",
                    "entity_type": "person",
                    "canonical_name": "Test Person",
                    "status": "Active",
                    "summary": "A test person"
                },
                {
                    "op": "upsert_action",
                    "title": "Email Test Person",
                    "next_action": "Open email and draft message",
                    "project": "[[test_project]]",
                    "estimate": "≤15m",
                    "energy": "shallow",
                    "flavor": "social",
                }
            ]
        }
        result = commit_manifest(manifest)
        assert result["entities_written"] == 1
        assert result["actions_written"] == 1

        # Both should exist
        person_path = paths.entity_dir("people") / "test_person.md"
        assert person_path.exists()
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
    print("✓")


def test_rollback_on_failure():
    """Test: failed commit leaves ledger untouched."""
    print("Testing rollback on failure...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # Create an action
        manifest = {
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_action",
                "title": "Existing task",
                "next_action": "Keep doing it",
                "estimate": "short",
                "energy": "deep",
                "flavor": "analytical",
            }]
        }
        commit_manifest(manifest)

        # Try a manifest that should fail (bad entity type + action)
        manifest_bad = {
            "manifest_version": "2.0",
            "operations": [
                {
                    "op": "upsert_action",
                    "title": "New task",
                    "next_action": "Should not persist",
                    "estimate": "short",
                    "energy": "shallow",
                    "flavor": "admin",
                },
                {
                    "op": "upsert_entity",
                    "entity_type": "nonexistent_type",
                    "canonical_name": "Bad Entity",
                }
            ]
        }
        try:
            commit_manifest(manifest_bad)
            assert False, "Should have raised CommitError"
        except CommitError:
            pass

        # Ledger should still have only the original action
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].title == "Existing task"
    print("✓")


def run_tests():
    test_upsert_new_action()
    test_upsert_update_action()
    test_today_cap_enforced()
    test_archive_action()
    test_mixed_manifest()
    test_rollback_on_failure()
    print("\n✓ All commit action tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
