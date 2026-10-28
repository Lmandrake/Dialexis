"""review.py - Kill-or-commit state machine for the action ledger.

Contract:
- Increments carry_count for stale actions (past due or older than STALE_DAYS)
- Surfaces actions at/above CARRY_LIMIT as needing forced decision
- Clears stale today-flags at the start of each new day
- Returns structured results the LLM uses to frame the review conversation

Postconditions:
- carry_count only increments; never decrements except on reshape
- Items at CARRY_LIMIT are surfaced; they cannot be silently carried further
- Decisions produce manifest operations (never direct file edits)

Halt conditions:
- None (review is advisory; commit.py enforces on apply)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta

try:
    from kairos.parse import Action, CARRY_LIMIT, STALE_DAYS, TODAY_CAP
except ImportError:
    from parse import Action, CARRY_LIMIT, STALE_DAYS, TODAY_CAP


@dataclass
class ReviewResult:
    needs_decision: list[Action] = field(default_factory=list)
    carried: list[Action] = field(default_factory=list)
    healthy: list[Action] = field(default_factory=list)
    stale_today_cleared: list[Action] = field(default_factory=list)


def run_review(actions: list[Action], today: date | None = None) -> ReviewResult:
    """Execute a review pass: increment carry, classify actions.

    Returns a ReviewResult with actions sorted into buckets.
    Does NOT mutate the actions list — returns ops for the caller to commit.
    """
    today = today or date.today()
    result = ReviewResult()

    for action in actions:
        if action.status not in ("active", "today"):
            continue

        if _is_stale(action, today):
            action.carry_count += 1
            action.last_review = today.isoformat()
            if action.carry_count >= CARRY_LIMIT:
                result.needs_decision.append(action)
            else:
                result.carried.append(action)
        else:
            result.healthy.append(action)

    return result


def clear_stale_today(actions: list[Action], today: date | None = None) -> list[dict]:
    """Revert yesterday's today-flagged items back to active.

    Returns manifest operations for the caller to commit.
    """
    today = today or date.today()
    ops = []

    for action in actions:
        if action.status != "today":
            continue
        if action.last_review and action.last_review < today.isoformat():
            ops.append({
                "op": "upsert_action",
                "id": action.id,
                "status": "active",
                "last_review": today.isoformat(),
            })

    return ops


def apply_decision(action_id: str, decision: str, reshape_text: str | None = None,
                   today: date | None = None) -> dict:
    """Generate a manifest operation for a kill/commit/reshape decision.

    Args:
        action_id: The action to decide on
        decision: One of "kill", "commit", "reshape"
        reshape_text: New next_action text (required for reshape)
        today: Override for testing

    Returns:
        A single manifest operation dict
    """
    today = today or date.today()

    if decision == "kill":
        return {
            "op": "archive_action",
            "id": action_id,
            "status": "released",
        }
    elif decision == "commit":
        return {
            "op": "upsert_action",
            "id": action_id,
            "status": "today",
            "last_review": today.isoformat(),
        }
    elif decision == "reshape":
        if not reshape_text:
            raise ValueError("reshape requires reshape_text")
        return {
            "op": "upsert_action",
            "id": action_id,
            "next_action": reshape_text,
            "carry_count": 0,
            "last_review": today.isoformat(),
        }
    else:
        raise ValueError(f"unknown decision: {decision!r} (must be kill/commit/reshape)")


def _is_stale(action: Action, today: date) -> bool:
    """Determine if an action is stale (past due or older than STALE_DAYS without activity)."""
    # Past due
    if action.due:
        try:
            from kairos.harvest import _parse_date
        except ImportError:
            from harvest import _parse_date
        due_date = _parse_date(action.due)
        if due_date and due_date < today:
            return True

    # Older than STALE_DAYS since last review
    if action.last_review:
        try:
            from kairos.harvest import _parse_date
        except ImportError:
            from harvest import _parse_date
        last = _parse_date(action.last_review)
        if last and (today - last).days >= STALE_DAYS:
            return True

    # Created long ago with no review
    if not action.last_review and action.created:
        try:
            from kairos.harvest import _parse_date
        except ImportError:
            from harvest import _parse_date
        created = _parse_date(action.created)
        if created and (today - created).days >= STALE_DAYS:
            return True

    return False
