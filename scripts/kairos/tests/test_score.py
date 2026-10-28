"""Tests for kairos/score.py - recommendation scoring."""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from parse import Action
from harvest import HarvestedEvent
from score import score_candidates, dream_eligible, Query


def _action(id="act_0001", status="active", estimate="short", energy="shallow",
            flavor="admin", due=None, for_event=None, carry_count=0):
    return Action(
        id=id, title=f"Task {id}", next_action="Do it",
        estimate=estimate, energy=energy, flavor=flavor,
        status=status, due=due, for_event=for_event,
        carry_count=carry_count, created="2026-06-25",
        last_review="2026-06-25",
    )


def test_hard_filter_time():
    """Test: actions exceeding time budget are disqualified."""
    print("Testing hard filter: time...", end=" ")
    actions = [
        _action(id="act_0001", estimate="session"),  # 180 min
        _action(id="act_0002", estimate="≤15m"),     # 15 min
    ]
    query = Query(time_available=30)
    candidates = score_candidates(actions, [], query)

    qualified = [c for c in candidates if not c.disqualified]
    disqualified = [c for c in candidates if c.disqualified]
    assert len(qualified) == 1
    assert qualified[0].action.id == "act_0002"
    assert len(disqualified) == 1
    assert disqualified[0].action.id == "act_0001"
    print("✓")


def test_hard_filter_energy():
    """Test: deep actions fail shallow energy query."""
    print("Testing hard filter: energy...", end=" ")
    actions = [
        _action(id="act_0001", energy="deep"),
        _action(id="act_0002", energy="shallow"),
    ]
    query = Query(energy="shallow")
    candidates = score_candidates(actions, [], query)

    qualified = [c for c in candidates if not c.disqualified]
    assert len(qualified) == 1
    assert qualified[0].action.id == "act_0002"
    print("✓")


def test_energy_deep_passes_all():
    """Test: deep energy query passes both deep and shallow actions."""
    print("Testing deep energy passes all...", end=" ")
    actions = [
        _action(id="act_0001", energy="deep"),
        _action(id="act_0002", energy="shallow"),
    ]
    query = Query(energy="deep")
    candidates = score_candidates(actions, [], query)

    qualified = [c for c in candidates if not c.disqualified]
    assert len(qualified) == 2
    print("✓")


def test_today_scores_higher():
    """Test: today-flagged items score higher."""
    print("Testing today scores higher...", end=" ")
    actions = [
        _action(id="act_0001", status="active"),
        _action(id="act_0002", status="today"),
    ]
    query = Query()
    candidates = score_candidates(actions, [], query)

    qualified = [c for c in candidates if not c.disqualified]
    assert qualified[0].action.id == "act_0002"
    assert qualified[0].score > qualified[1].score
    print("✓")


def test_overdue_highest_pressure():
    """Test: overdue items score highest due_pressure."""
    print("Testing overdue highest pressure...", end=" ")
    today = date(2026, 7, 5)
    actions = [
        _action(id="act_0001", due="2026-07-10"),  # future
        _action(id="act_0002", due="2026-07-01"),  # overdue
    ]
    query = Query()
    candidates = score_candidates(actions, [], query, today=today)

    qualified = [c for c in candidates if not c.disqualified]
    assert qualified[0].action.id == "act_0002"
    print("✓")


def test_stale_nudge():
    """Test: high carry_count nudges score up."""
    print("Testing stale nudge...", end=" ")
    actions = [
        _action(id="act_0001", carry_count=0),
        _action(id="act_0002", carry_count=3),
    ]
    query = Query()
    candidates = score_candidates(actions, [], query)

    qualified = [c for c in candidates if not c.disqualified]
    assert qualified[0].action.id == "act_0002"
    print("✓")


def test_dream_eligible_thin_set():
    """Test: dream_eligible when few candidates and generous time."""
    print("Testing dream_eligible...", end=" ")
    candidates_few = [
        type("C", (), {"disqualified": False})()
    ]
    query = Query(time_available=120)
    assert dream_eligible(candidates_few, query) is True
    print("✓")


def test_dream_not_eligible_many():
    """Test: dream_eligible False when many candidates."""
    print("Testing dream not eligible...", end=" ")
    candidates_many = [
        type("C", (), {"disqualified": False})() for _ in range(5)
    ]
    query = Query(time_available=120)
    assert dream_eligible(candidates_many, query) is False
    print("✓")


def test_finer_bucket_fits_a_short_window():
    """Test: a '≤2m' action fits a 5-minute window instead of falling through to the
    60-minute default (which would wrongly disqualify it)."""
    print("Testing ≤2m estimate fits a short window...", end=" ")
    actions = [_action(id="act_0001", estimate="≤2m")]
    query = Query(time_available=5)
    candidates = score_candidates(actions, [], query)
    qualified = [c for c in candidates if not c.disqualified]
    assert len(qualified) == 1
    print("✓")


def run_tests():
    test_hard_filter_time()
    test_finer_bucket_fits_a_short_window()
    test_hard_filter_energy()
    test_energy_deep_passes_all()
    test_today_scores_higher()
    test_overdue_highest_pressure()
    test_stale_nudge()
    test_dream_eligible_thin_set()
    test_dream_not_eligible_many()
    print("\n✓ All score tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
