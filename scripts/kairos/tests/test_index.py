"""Tests for kairos/index.py - agenda index regeneration."""
import sys
from pathlib import Path
from datetime import date, timedelta
from tempfile import TemporaryDirectory
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from registry import load_registry
import entity_io as eio
from entity_io import Entity
from parse import Action, serialize_ledger
from index import regenerate_index


def setup_workspace(tmpdir: Path):
    paths.ROOT = tmpdir
    paths.WORKING_MODEL = tmpdir / "graph"
    paths.INDEX_DIR = paths.WORKING_MODEL / "_index"
    paths.AGENTS = tmpdir / "agents"
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_LEDGER = paths.AGENDA / "actions.md"
    paths.AGENDA_ARCHIVE = paths.AGENDA / "archive"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"

    real_agents = Path(__file__).parent.parent.parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    reg = load_registry()
    for entity_type, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)
    paths.AGENDA.mkdir(parents=True, exist_ok=True)
    paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)


def test_index_with_today():
    """Test: index includes today-set items."""
    print("Testing index with today items...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        actions = [
            Action(id="act_0001", title="Important task", next_action="Do it now",
                   estimate="short", energy="shallow", flavor="admin",
                   status="today", created="2026-06-25", last_review="2026-06-25"),
        ]
        paths.AGENDA_LEDGER.write_text(serialize_ledger(actions), encoding="utf-8")

        index_path = regenerate_index(today=date(2026, 6, 25))
        assert index_path.exists()
        content = index_path.read_text()
        assert "Important task" in content
        assert "## Today" in content
    print("✓")


def test_index_with_events():
    """Test: index includes upcoming events."""
    print("Testing index with events...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        entity = Entity(canonical_name="Team Standup", entity_type="event", status="Active")
        entity.attributes = [{"key": "Date", "value": tomorrow, "confidence": "HIGH", "source": "manual"}]
        entity.summary = "Daily standup"
        from naming import slug
        p = paths.entity_dir("events") / f"{slug('Team Standup')}.md"
        p.write_text(eio.serialize(entity), encoding="utf-8")

        index_path = regenerate_index()
        content = index_path.read_text()
        assert "Team Standup" in content
        assert "Upcoming Events" in content
    print("✓")


def test_index_idempotent():
    """Test: regenerating twice produces identical output."""
    print("Testing index idempotence...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        actions = [
            Action(id="act_0001", title="Task A", next_action="Step 1",
                   estimate="short", energy="deep", flavor="writing",
                   status="active", created="2026-06-25", last_review="2026-06-25"),
        ]
        paths.AGENDA_LEDGER.write_text(serialize_ledger(actions), encoding="utf-8")

        today = date(2026, 6, 25)
        regenerate_index(today=today)
        content1 = (paths.INDEX_DIR / "agenda.md").read_text()
        regenerate_index(today=today)
        content2 = (paths.INDEX_DIR / "agenda.md").read_text()
        assert content1 == content2
    print("✓")


def test_index_empty():
    """Test: index with no data produces empty sections gracefully."""
    print("Testing index empty state...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        index_path = regenerate_index(today=date(2026, 6, 25))
        content = index_path.read_text()
        assert "nothing set for today" in content
        assert "no active actions" in content
    print("✓")


def run_tests():
    test_index_with_today()
    test_index_with_events()
    test_index_idempotent()
    test_index_empty()
    print("\n✓ All index tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
