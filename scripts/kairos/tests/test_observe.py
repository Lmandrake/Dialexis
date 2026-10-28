"""Tests for kairos/observe.py - append-only observations."""
import sys
from pathlib import Path
from datetime import date
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from observe import Observation, ObserveError, record_observation, list_observations, count_observations


def setup_workspace(tmpdir: Path):
    paths.ROOT = tmpdir
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"
    paths.AGENDA_WORKLOG.mkdir(parents=True, exist_ok=True)


def test_record_observation():
    """Test: observation is recorded to worklog."""
    print("Testing record observation...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        obs = Observation(
            timestamp="2026-06-25T14:30:00",
            kind="completion",
            text="Finished the strategy draft in 45 minutes",
            action_id="act_0042",
            actual_minutes=45,
            focus="deep",
        )
        filepath = record_observation(obs)
        assert filepath.exists()
        content = filepath.read_text()
        assert "completion" in content
        assert "act_0042" in content
        assert "45" in content
    print("✓")


def test_immutability_refusal():
    """Test: refuses to overwrite existing observation."""
    print("Testing immutability refusal...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        obs = Observation(
            timestamp="2026-06-25T14:30:00",
            kind="log",
            text="First entry",
        )
        record_observation(obs)

        # Try to write again with same timestamp
        obs2 = Observation(
            timestamp="2026-06-25T14:30:00",
            kind="log",
            text="Should fail",
        )
        try:
            record_observation(obs2)
            assert False, "Should have raised ObserveError"
        except ObserveError:
            pass
    print("✓")


def test_list_observations():
    """Test: list_observations returns all observations."""
    print("Testing list observations...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        for i in range(3):
            obs = Observation(
                timestamp=f"2026-06-2{i+5}T10:00:00",
                kind="log",
                text=f"Entry {i+1}",
            )
            record_observation(obs)

        results = list_observations()
        assert len(results) == 3
    print("✓")


def test_list_observations_since():
    """Test: list_observations filters by date."""
    print("Testing list observations since...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        obs1 = Observation(timestamp="2026-06-20T10:00:00", kind="log", text="Old")
        obs2 = Observation(timestamp="2026-06-25T10:00:00", kind="log", text="Recent")
        record_observation(obs1)
        record_observation(obs2)

        results = list_observations(since=date(2026, 6, 24))
        assert len(results) == 1
        assert results[0].text == "Recent"
    print("✓")


def test_count_observations():
    """Test: count returns correct number."""
    print("Testing count observations...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        assert count_observations() == 0

        obs = Observation(timestamp="2026-06-25T10:00:00", kind="log", text="Entry")
        record_observation(obs)
        assert count_observations() == 1
    print("✓")


def test_round_trip():
    """Test: observation round-trips through serialize/parse."""
    print("Testing observation round-trip...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        obs = Observation(
            timestamp="2026-06-25T14:30:00",
            kind="completion",
            text="Did the thing well",
            action_id="act_0005",
            actual_minutes=30,
            focus="deep",
            what_helped="Coffee and silence",
            what_hindered="Slack notifications",
            time_of_day="morning",
        )
        record_observation(obs)

        results = list_observations()
        assert len(results) == 1
        r = results[0]
        assert r.kind == "completion"
        assert r.action_id == "act_0005"
        assert r.actual_minutes == 30
        assert r.focus == "deep"
        assert r.what_helped == "Coffee and silence"
    print("✓")


def test_felt_field_round_trips():
    """Test: the `felt` field (added 2026-09-03) persists through serialize/parse, and is
    absent from the file entirely when not given (no empty 'felt:' clutter on every record)."""
    print("Testing felt field round-trip...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        obs = Observation(
            timestamp="2026-09-03T09:00:00",
            kind="completion",
            text="Shipped the draft",
            felt="energized",
        )
        filepath = record_observation(obs)
        assert "felt: energized" in filepath.read_text()

        results = list_observations()
        assert results[0].felt == "energized"

        # A record with no felt given doesn't grow a stray field.
        obs2 = Observation(timestamp="2026-09-03T09:05:00", kind="log", text="Plain entry")
        filepath2 = record_observation(obs2)
        assert "- felt:" not in filepath2.read_text()
        assert list_observations()[1].felt is None
    print("✓")


def run_tests():
    test_record_observation()
    test_immutability_refusal()
    test_list_observations()
    test_list_observations_since()
    test_count_observations()
    test_round_trip()
    test_felt_field_round_trips()
    print("\n✓ All observe tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
