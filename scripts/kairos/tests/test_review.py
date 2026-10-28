"""Tests for kairos/review.py - kill-or-commit state machine."""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from parse import Action, CARRY_LIMIT, STALE_DAYS
from review import run_review, clear_stale_today, apply_decision


def _make_action(id="act_0001", status="active", due=None, carry_count=0,
                 last_review=None, created=None):
    return Action(
        id=id, title="Test", next_action="Do thing",
        estimate="short", energy="shallow", flavor="admin",
        status=status, due=due, carry_count=carry_count,
        last_review=last_review or date.today().isoformat(),
        created=created or date.today().isoformat(),
    )


def test_carry_count_increments():
    """Test: stale actions get carry_count incremented."""
    print("Testing carry_count increment...", end=" ")
    stale_date = (date.today() - timedelta(days=STALE_DAYS + 1)).isoformat()
    action = _make_action(last_review=stale_date, created=stale_date)
    assert action.carry_count == 0

    result = run_review([action])
    assert action.carry_count == 1
    assert len(result.carried) == 1
    print("✓")


def test_carry_limit_surfaces():
    """Test: actions at CARRY_LIMIT appear in needs_decision."""
    print("Testing CARRY_LIMIT surfacing...", end=" ")
    stale_date = (date.today() - timedelta(days=STALE_DAYS + 1)).isoformat()
    action = _make_action(carry_count=CARRY_LIMIT - 1, last_review=stale_date, created=stale_date)

    result = run_review([action])
    assert action.carry_count == CARRY_LIMIT
    assert len(result.needs_decision) == 1
    assert len(result.carried) == 0
    print("✓")


def test_past_due_is_stale():
    """Test: action past its due date is stale."""
    print("Testing past-due staleness...", end=" ")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    action = _make_action(due=yesterday, last_review=date.today().isoformat())

    result = run_review([action])
    assert action.carry_count == 1
    assert len(result.carried) == 1
    print("✓")


def test_healthy_action_not_carried():
    """Test: fresh actions are not carried."""
    print("Testing healthy action...", end=" ")
    action = _make_action(last_review=date.today().isoformat())

    result = run_review([action])
    assert action.carry_count == 0
    assert len(result.healthy) == 1
    print("✓")


def test_clear_stale_today():
    """Test: yesterday's today items revert to active."""
    print("Testing clear stale today...", end=" ")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    action = _make_action(status="today", last_review=yesterday)

    ops = clear_stale_today([action])
    assert len(ops) == 1
    assert ops[0]["op"] == "upsert_action"
    assert ops[0]["status"] == "active"
    print("✓")


def test_clear_stale_today_keeps_current():
    """Test: today's today items are not cleared."""
    print("Testing today items kept...", end=" ")
    action = _make_action(status="today", last_review=date.today().isoformat())

    ops = clear_stale_today([action])
    assert len(ops) == 0
    print("✓")


def test_apply_decision_kill():
    """Test: kill decision produces archive_action op."""
    print("Testing apply_decision kill...", end=" ")
    op = apply_decision("act_0001", "kill")
    assert op["op"] == "archive_action"
    assert op["status"] == "released"
    print("✓")


def test_apply_decision_commit():
    """Test: commit decision produces upsert_action with today status."""
    print("Testing apply_decision commit...", end=" ")
    op = apply_decision("act_0001", "commit")
    assert op["op"] == "upsert_action"
    assert op["status"] == "today"
    print("✓")


def test_apply_decision_reshape():
    """Test: reshape resets carry_count and updates next_action."""
    print("Testing apply_decision reshape...", end=" ")
    op = apply_decision("act_0001", "reshape", reshape_text="New concrete step")
    assert op["op"] == "upsert_action"
    assert op["next_action"] == "New concrete step"
    assert op["carry_count"] == 0
    print("✓")


def run_tests():
    test_carry_count_increments()
    test_carry_limit_surfaces()
    test_past_due_is_stale()
    test_healthy_action_not_carried()
    test_clear_stale_today()
    test_clear_stale_today_keeps_current()
    test_apply_decision_kill()
    test_apply_decision_commit()
    test_apply_decision_reshape()
    print("\n✓ All review tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
