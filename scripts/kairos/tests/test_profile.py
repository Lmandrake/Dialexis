"""Tests for kairos/profile.py - worker profile generation."""
import sys
from pathlib import Path
from datetime import date
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from observe import Observation, record_observation
from profile import generate_profile, should_rebuild, rebuild_profile, REBUILD_THRESHOLD


def setup_workspace(tmpdir: Path):
    paths.ROOT = tmpdir
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"
    paths.AGENDA_WORKLOG.mkdir(parents=True, exist_ok=True)
    paths.WORKER_PROFILE = paths.AGENDA / "worker_profile.md"


def test_empty_worklog_minimal():
    """Test: empty worklog produces minimal profile."""
    print("Testing empty worklog profile...", end=" ")
    content = generate_profile([], today=date(2026, 6, 25))
    assert "No observations yet" in content
    assert "Worker Profile" in content
    print("✓")


def test_profile_with_observations():
    """Test: profile reflects observed patterns."""
    print("Testing profile with data...", end=" ")
    observations = [
        Observation(timestamp="2026-06-20T09:00:00", kind="completion", text="Task 1",
                   actual_minutes=45, focus="deep", time_of_day="morning",
                   what_helped="Coffee"),
        Observation(timestamp="2026-06-21T09:30:00", kind="completion", text="Task 2",
                   actual_minutes=30, focus="deep", time_of_day="morning",
                   what_helped="Coffee"),
        Observation(timestamp="2026-06-22T14:00:00", kind="completion", text="Task 3",
                   actual_minutes=60, focus="scattered", time_of_day="afternoon",
                   what_hindered="Slack"),
    ]
    content = generate_profile(observations, today=date(2026, 6, 25))
    assert "morning" in content
    assert "deep" in content
    assert "Coffee" in content
    assert "Slack" in content
    print("✓")


def test_should_rebuild_threshold():
    """Test: rebuild triggers at REBUILD_THRESHOLD observations."""
    print("Testing should_rebuild threshold...", end=" ")
    assert should_rebuild(REBUILD_THRESHOLD) is True
    assert should_rebuild(REBUILD_THRESHOLD - 1, last_rebuild=date(2026, 6, 25),
                         today=date(2026, 6, 25)) is False
    print("✓")


def test_should_rebuild_weekly():
    """Test: rebuild triggers after 7 days."""
    print("Testing should_rebuild weekly...", end=" ")
    assert should_rebuild(5, last_rebuild=date(2026, 6, 1), today=date(2026, 6, 25)) is True
    assert should_rebuild(5, last_rebuild=date(2026, 6, 24), today=date(2026, 6, 25)) is False
    print("✓")


def test_rebuild_versions_prior():
    """Test: rebuild preserves prior profile as versioned file."""
    print("Testing rebuild versioning...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))

        # Create initial profile
        paths.WORKER_PROFILE.write_text("# Old Profile\n")

        # Record an observation so there's data
        obs = Observation(timestamp="2026-06-25T10:00:00", kind="log", text="Test work")
        record_observation(obs)

        # Rebuild
        today = date(2026, 6, 25)
        result = rebuild_profile(today=today)
        assert result.exists()

        # Prior version should be saved
        versioned = paths.AGENDA / "worker_profile_2026-06-25.md"
        assert versioned.exists()
        assert versioned.read_text() == "# Old Profile\n"
    print("✓")


def run_tests():
    test_empty_worklog_minimal()
    test_profile_with_observations()
    test_should_rebuild_threshold()
    test_should_rebuild_weekly()
    test_rebuild_versions_prior()
    print("\n✓ All profile tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
