"""Tests for kairos/prep.py - event preparation lifecycle."""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from parse import Action, PREP_HORIZON
from harvest import HarvestedEvent
from prep import find_prep_eligible, create_prep_action, resolve_expired_prep, _event_slug


def test_find_prep_eligible():
    """Test: events in horizon without prep-action are flagged."""
    print("Testing find_prep_eligible...", end=" ")
    today = date(2026, 7, 1)
    events = [
        HarvestedEvent("Planning Meeting", Path("x.md"), date(2026, 7, 2), "project_meeting"),
    ]
    actions = []

    prompts = find_prep_eligible(events, actions, today=today)
    assert len(prompts) == 1
    assert prompts[0].event_name == "Planning Meeting"
    assert prompts[0].days_until == 1
    print("✓")


def test_prep_eligible_skips_prepped():
    """Test: events with existing prep-action are not flagged."""
    print("Testing prep skips already-prepped...", end=" ")
    today = date(2026, 7, 1)
    events = [
        HarvestedEvent("Planning Meeting", Path("x.md"), date(2026, 7, 2), "project_meeting"),
    ]
    slug = _event_slug("Planning Meeting")
    actions = [
        Action(id="act_0001", title="Prep for meeting", next_action="Draft agenda",
               for_event=f"[[{slug}]]", estimate="short", energy="shallow",
               flavor="admin", status="active", created="2026-06-30"),
    ]

    prompts = find_prep_eligible(events, actions, today=today)
    assert len(prompts) == 0
    print("✓")


def test_prep_eligible_skips_past():
    """Test: past events are not flagged for prep."""
    print("Testing prep skips past events...", end=" ")
    today = date(2026, 7, 5)
    events = [
        HarvestedEvent("Old Meeting", Path("x.md"), date(2026, 7, 1), "project_meeting"),
    ]

    prompts = find_prep_eligible(events, [], today=today)
    assert len(prompts) == 0
    print("✓")


def test_create_prep_action():
    """Test: prep-action has correct derived due."""
    print("Testing create_prep_action...", end=" ")
    today = date(2026, 7, 1)
    event_date = date(2026, 7, 5)
    op = create_prep_action("Review Panel", "Draft talking points",
                           lead_days=2, event_date=event_date, today=today)

    assert op["op"] == "upsert_action"
    assert op["for_event"] == "[[review_panel]]"
    assert op["due"] == "2026-07-03"  # event_date - 2 days
    assert op["next_action"] == "Draft talking points"
    print("✓")


def test_create_prep_action_clamps_due():
    """Test: if derived due is in the past, clamp to today."""
    print("Testing prep-action due clamping...", end=" ")
    today = date(2026, 7, 4)
    event_date = date(2026, 7, 5)
    op = create_prep_action("Meeting", "Prep", lead_days=3, event_date=event_date, today=today)

    # event_date - 3 = July 2, which is past. Should clamp to today.
    assert op["due"] == "2026-07-04"
    print("✓")


def test_resolve_expired_prep():
    """Test: prep-actions with past due are surfaced for resolution."""
    print("Testing resolve_expired_prep...", end=" ")
    today = date(2026, 7, 10)
    actions = [
        Action(id="act_0001", title="Prep for meeting", next_action="Do stuff",
               for_event="[[meeting]]", due="2026-07-05", estimate="short",
               energy="shallow", flavor="admin", status="active",
               created="2026-07-01", last_review="2026-07-01"),
    ]

    results = resolve_expired_prep(actions, [], today=today)
    assert len(results) == 1
    assert results[0].action.id == "act_0001"
    print("✓")


def run_tests():
    test_find_prep_eligible()
    test_prep_eligible_skips_prepped()
    test_prep_eligible_skips_past()
    test_create_prep_action()
    test_create_prep_action_clamps_due()
    test_resolve_expired_prep()
    print("\n✓ All prep tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
