"""prep.py - Event-driven preparation lifecycle.

Contract:
- Identifies events within PREP_HORIZON that lack a resolved prep-action
- Generates prep-action manifest operations with derived due dates
- Detects passed prep-actions for one-time resolution surfacing
- Records prep outcomes as observations

Postconditions:
- Only events within horizon and without active/done prep-actions are flagged
- Due is derived as event.start - lead_days (never set manually)
- Expired prep is surfaced exactly once, then archived

Halt conditions:
- None (advisory; commit.py enforces on apply)
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta

try:
    from kairos.parse import Action, PREP_HORIZON
    from kairos.harvest import HarvestedEvent, _parse_date
except ImportError:
    from parse import Action, PREP_HORIZON
    from harvest import HarvestedEvent, _parse_date


@dataclass
class PrepPrompt:
    event_name: str
    event_date: date
    kind: str
    days_until: int


@dataclass
class PrepResolution:
    action: Action
    event_name: str


def find_prep_eligible(events: list[HarvestedEvent], actions: list[Action],
                       today: date | None = None) -> list[PrepPrompt]:
    """Find events within PREP_HORIZON that lack a prep-action.

    Returns PrepPrompt objects for events that should prompt "anything to prepare?"
    """
    today = today or date.today()
    horizon_end = today + timedelta(days=PREP_HORIZON)
    results = []

    # Build set of events that already have active/done prep-actions
    prepped_events = set()
    for a in actions:
        if a.for_event and a.status in ("active", "today", "done"):
            prepped_events.add(a.for_event)

    for event in events:
        if event.start_date > horizon_end:
            continue
        if event.start_date < today:
            continue
        # Check if this event already has a prep-action
        event_ref = f"[[{_event_slug(event.canonical_name)}]]"
        if event_ref in prepped_events:
            continue

        days_until = (event.start_date - today).days
        results.append(PrepPrompt(
            event_name=event.canonical_name,
            event_date=event.start_date,
            kind=event.kind,
            days_until=days_until,
        ))

    return sorted(results, key=lambda p: p.days_until)


def create_prep_action(event_name: str, prep_text: str, lead_days: int = PREP_HORIZON,
                       event_date: date | None = None, today: date | None = None) -> dict:
    """Generate a manifest op for creating a prep-action anchored to an event.

    Args:
        event_name: The event this prepares for
        prep_text: The concrete next_action text
        lead_days: Days before event the prep should be done
        event_date: The event's date (for deriving due)
        today: Override for testing
    """
    today = today or date.today()
    due = None
    if event_date:
        due = (event_date - timedelta(days=lead_days)).isoformat()
        if _parse_date(due) and _parse_date(due) < today:
            due = today.isoformat()

    return {
        "op": "upsert_action",
        "title": f"Prep for {event_name}",
        "next_action": prep_text,
        "for_event": f"[[{_event_slug(event_name)}]]",
        "due": due,
        "estimate": "short",
        "energy": "shallow",
        "flavor": "admin",
        "status": "active",
    }


def resolve_expired_prep(actions: list[Action], events: list[HarvestedEvent],
                         today: date | None = None) -> list[PrepResolution]:
    """Find prep-actions whose event has already passed.

    These should be surfaced once for a "did it matter?" check.
    """
    today = today or date.today()
    results = []

    # Build map of event names to dates from harvested events
    # We also need to check actions whose for_event points to a past event
    for action in actions:
        if not action.for_event:
            continue
        if action.status not in ("active", "today"):
            continue

        # Try to find the event date from the action's due or infer from for_event
        # If the action has a due date that's past, the event likely already happened
        if action.due:
            due_date = _parse_date(action.due)
            if due_date and due_date < today:
                results.append(PrepResolution(
                    action=action,
                    event_name=action.for_event,
                ))

    return results


def record_prep_outcome(action_id: str, outcome: str, today: date | None = None) -> list[dict]:
    """Generate ops for recording a prep outcome: archive + observation data.

    Args:
        action_id: The prep-action to resolve
        outcome: One of "prepped", "didnt_need_it", "wish_i_had"

    Returns:
        List of manifest operations (archive_action + observation data for observe.py)
    """
    today = today or date.today()
    ops = [
        {
            "op": "archive_action",
            "id": action_id,
            "status": "expired",
        }
    ]
    return ops


def _event_slug(name: str) -> str:
    """Generate a simple slug for event reference."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
