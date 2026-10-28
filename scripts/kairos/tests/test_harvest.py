"""Tests for kairos/harvest.py - event and milestone harvesting."""
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
from harvest import harvest_events, harvest_milestones, _parse_date


def setup_workspace(tmpdir: Path):
    """Set up temp workspace with registry and event entities."""
    paths.ROOT = tmpdir
    paths.WORKING_MODEL = tmpdir / "graph"
    paths.AGENTS = tmpdir / "agents"

    real_agents = Path(__file__).parent.parent.parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    reg = load_registry()
    for entity_type, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)


def create_event(name: str, event_date: str, kind: str = ""):
    """Create a minimal event entity file."""
    entity = Entity(canonical_name=name, entity_type="event", status="Active")
    entity.attributes = [{"key": "Date", "value": event_date, "confidence": "HIGH", "source": "manual"}]
    if kind:
        entity.attributes.append({"key": "Kind", "value": kind, "confidence": "HIGH", "source": "manual"})
    entity.summary = f"Test event: {name}"
    from naming import slug
    p = paths.entity_dir("events") / f"{slug(name)}.md"
    p.write_text(eio.serialize(entity), encoding="utf-8")
    return p


def test_harvest_future_events():
    """Test: harvests events within horizon."""
    print("Testing harvest future events...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        create_event("Planning Meeting", tomorrow, "project_meeting")

        results = harvest_events(horizon_days=7)
        assert len(results) == 1
        assert results[0].canonical_name == "Planning Meeting"
        assert results[0].kind == "project_meeting"
    print("✓")


def test_harvest_ignores_past():
    """Test: past events are not harvested."""
    print("Testing harvest ignores past...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        create_event("Old Meeting", yesterday)

        results = harvest_events(horizon_days=7)
        assert len(results) == 0
    print("✓")


def test_harvest_ignores_beyond_horizon():
    """Test: events beyond horizon are not harvested."""
    print("Testing harvest ignores beyond horizon...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        far_future = (date.today() + timedelta(days=30)).isoformat()
        create_event("Far Away Event", far_future)

        results = harvest_events(horizon_days=7)
        assert len(results) == 0
    print("✓")


def test_harvest_missing_date():
    """Test: events without parseable dates are skipped."""
    print("Testing harvest missing date...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        entity = Entity(canonical_name="Undated Event", entity_type="event", status="Active")
        entity.summary = "No date on this one"
        from naming import slug
        p = paths.entity_dir("events") / f"{slug('Undated Event')}.md"
        p.write_text(eio.serialize(entity), encoding="utf-8")

        results = harvest_events(horizon_days=7)
        assert len(results) == 0
    print("✓")


def test_harvest_milestones():
    """Test: harvests milestone attributes from any entity type."""
    print("Testing harvest milestones...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        future = (date.today() + timedelta(days=5)).isoformat()
        entity = Entity(canonical_name="Test Project", entity_type="project", status="Active")
        entity.attributes = [{"key": "Milestone", "value": future, "confidence": "HIGH", "source": "manual"}]
        entity.summary = "A project"
        from naming import slug
        p = paths.entity_dir("projects") / f"{slug('Test Project')}.md"
        p.write_text(eio.serialize(entity), encoding="utf-8")

        results = harvest_milestones()
        assert len(results) == 1
        assert results[0].entity_name == "Test Project"
    print("✓")


def test_parse_date():
    """Test: date parser handles various formats."""
    print("Testing date parser...", end=" ")
    assert _parse_date("2026-07-01") == date(2026, 7, 1)
    assert _parse_date("2026-07-01 (tentative)") == date(2026, 7, 1)
    assert _parse_date("unknown") is None
    assert _parse_date("") is None
    assert _parse_date("not a date") is None
    print("✓")


def run_tests():
    test_harvest_future_events()
    test_harvest_ignores_past()
    test_harvest_ignores_beyond_horizon()
    test_harvest_missing_date()
    test_harvest_milestones()
    test_parse_date()
    print("\n✓ All harvest tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
