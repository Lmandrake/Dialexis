"""Tests for kairos/parse.py - ledger read/write."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from parse import (
    Action, ParseError, parse_ledger, serialize_ledger,
    next_id, validate_action, ESTIMATES, ENERGIES, FLAVORS, STATUSES
)

SAMPLE_LEDGER = """\
- id:           act_0001
  title:        Send strategy draft to Rowan
  next_action:  Open the draft and write the first section
  project:      [[alpha]]
  for_event:    null
  for_dream:    null
  due:          2027-02-01
  estimate:     short
  energy:       deep
  flavor:       writing
  status:       active
  created:      2027-01-25
  carry_count:  0
  last_review:  2027-01-25
- id:           act_0002
  title:        Review panel prep
  next_action:  Draft three talking points
  project:      null
  for_event:    [[review-panel-0212]]
  for_dream:    null
  due:          2027-02-10
  estimate:     ≤15m
  energy:       shallow
  flavor:       admin
  status:       today
  created:      2026-06-24
  carry_count:  1
  last_review:  2027-01-25
"""


def test_parse_roundtrip():
    """Test: parse -> serialize -> parse yields identical data."""
    print("Testing parse round-trip...", end=" ")
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "actions.md"
        p.write_text(SAMPLE_LEDGER)
        actions = parse_ledger(p)
        assert len(actions) == 2
        assert actions[0].id == "act_0001"
        assert actions[0].title == "Send strategy draft to Rowan"
        assert actions[0].project == "[[alpha]]"
        assert actions[0].for_event is None
        assert actions[0].due == "2027-02-01"
        assert actions[1].status == "today"
        assert actions[1].carry_count == 1

        # Round-trip
        serialized = serialize_ledger(actions)
        p2 = Path(tmp) / "actions2.md"
        p2.write_text(serialized)
        actions2 = parse_ledger(p2)
        assert len(actions2) == 2
        for a, b in zip(actions, actions2):
            assert a.id == b.id
            assert a.title == b.title
            assert a.next_action == b.next_action
            assert a.project == b.project
            assert a.for_event == b.for_event
            assert a.status == b.status
            assert a.carry_count == b.carry_count
    print("✓")


def test_parse_empty():
    """Test: empty or missing file returns empty list."""
    print("Testing parse empty file...", end=" ")
    with TemporaryDirectory() as tmp:
        # Missing file
        actions = parse_ledger(Path(tmp) / "nope.md")
        assert actions == []
        # Empty file
        p = Path(tmp) / "empty.md"
        p.write_text("")
        actions = parse_ledger(p)
        assert actions == []
    print("✓")


def test_parse_malformed():
    """Test: malformed block raises ParseError."""
    print("Testing parse malformed...", end=" ")
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.md"
        p.write_text("garbage content\nwithout proper blocks\n")
        try:
            parse_ledger(p)
            assert False, "Should have raised ParseError"
        except ParseError:
            pass
    print("✓")


def test_next_id():
    """Test: next_id increments correctly."""
    print("Testing next_id...", end=" ")
    actions = [Action(id="act_0001"), Action(id="act_0005"), Action(id="act_0003")]
    assert next_id(actions) == "act_0006"
    assert next_id([]) == "act_0001"
    print("✓")


def test_validate_action_valid():
    """Test: valid action passes validation."""
    print("Testing validate valid action...", end=" ")
    a = Action(
        id="act_0001", title="Do stuff", next_action="Open the doc",
        estimate="short", energy="deep", flavor="writing",
        status="active", created="2027-01-25"
    )
    errors = validate_action(a)
    assert errors == [], f"Unexpected errors: {errors}"
    print("✓")


def test_validate_action_invalid():
    """Test: invalid enums are caught."""
    print("Testing validate invalid action...", end=" ")
    a = Action(
        id="act_0001", title="Do stuff", next_action="Open the doc",
        estimate="huge", energy="manic", flavor="vibing",
        status="limbo", created="2027-01-25"
    )
    errors = validate_action(a)
    assert len(errors) == 4
    assert any("estimate" in e for e in errors)
    assert any("energy" in e for e in errors)
    assert any("flavor" in e for e in errors)
    assert any("status" in e for e in errors)
    print("✓")


def test_validate_missing_required():
    """Test: missing required fields are caught."""
    print("Testing validate missing fields...", end=" ")
    a = Action()  # All defaults, missing id/title/next_action/created
    errors = validate_action(a)
    assert any("id" in e for e in errors)
    assert any("title" in e for e in errors)
    assert any("next_action" in e for e in errors)
    assert any("created" in e for e in errors)
    print("✓")


def test_inline_comments_stripped():
    """Test: inline comments are stripped during parse."""
    print("Testing inline comment stripping...", end=" ")
    block = """\
- id:           act_0001
  title:        My task
  next_action:  Do the thing  # this is a concrete step
  project:      null
  for_event:    null
  for_dream:    null
  due:          null
  estimate:     short
  energy:       shallow
  flavor:       admin
  status:       active
  created:      2027-01-25
  carry_count:  0
  last_review:  2027-01-25
"""
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "actions.md"
        p.write_text(block)
        actions = parse_ledger(p)
        assert actions[0].next_action == "Do the thing"
    print("✓")


def test_estimate_bucket_backward_compatible():
    """Test: the 2026-09-03 finer bottom bucket ('≤2m') is purely additive — every existing
    record using the original four values still validates, and the new value validates too."""
    print("Testing estimate bucket backward compatibility...", end=" ")
    for old_value in ("≤15m", "short", "session", "deep"):
        a = Action(id="act_0001", title="Legacy record", next_action="Still valid",
                   estimate=old_value, energy="shallow", flavor="admin",
                   status="active", created="2027-01-25")
        assert validate_action(a) == [], f"{old_value!r} should still validate"

    a_new = Action(id="act_0002", title="Two-minute reply", next_action="Send it",
                  estimate="≤2m", energy="shallow", flavor="social",
                  status="active", created="2026-09-03")
    assert validate_action(a_new) == []
    assert "≤2m" in ESTIMATES
    print("✓")


def run_tests():
    test_parse_roundtrip()
    test_parse_empty()
    test_parse_malformed()
    test_next_id()
    test_validate_action_valid()
    test_validate_action_invalid()
    test_validate_missing_required()
    test_inline_comments_stripped()
    test_estimate_bucket_backward_compatible()
    print("\n✓ All parse tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
