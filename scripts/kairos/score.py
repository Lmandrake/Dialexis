"""score.py - Deterministic recommendation scoring with hard filters.

Contract:
- Scores active actions against a query (time, energy, mood)
- Applies hard filters before scoring (estimate <= time, energy match)
- Returns sorted candidates with transparent scores
- dream_eligible signals when dreams can surface

Postconditions:
- Disqualified candidates are marked with reason, never silently dropped
- Score is deterministic given same inputs (no randomness)
- Sorted highest-first

Halt conditions:
- None (pure computation, no I/O)
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


# Scoring weights (tunable knobs)
W_IMMINENT = 3.0
W_DUE = 2.5
W_PREP = 2.0
W_TODAY = 2.0
W_STALE = 0.5

# Estimate time budgets (in minutes). "≤2m" (added 2026-09-03, parse.ESTIMATES) needs its own
# entry: without it, a 2-minute action would fall through .get()'s 60-minute default and get
# disqualified from a genuinely-available 5-minute window, which is the exact loss the finer
# bucket exists to close.
ESTIMATE_MINUTES = {
    "≤2m": 2,
    "≤15m": 15,
    "short": 60,
    "session": 180,
    "deep": 480,
}


@dataclass
class Query:
    time_available: int | None = None  # minutes
    energy: str | None = None          # "deep" or "shallow"
    mood: str | None = None            # flavor preference


@dataclass
class Candidate:
    action: Action
    score: float = 0.0
    disqualified: bool = False
    disqualified_reason: str = ""


def score_candidates(actions: list[Action], events: list[HarvestedEvent],
                     query: Query, today: date | None = None) -> list[Candidate]:
    """Score and rank active actions against a query.

    Returns candidates sorted by score (highest first).
    Disqualified candidates are included but marked.
    """
    today = today or date.today()
    candidates = []

    for action in actions:
        if action.status not in ("active", "today"):
            continue

        c = Candidate(action=action)

        # Hard filter: time budget
        if query.time_available is not None:
            action_minutes = ESTIMATE_MINUTES.get(action.estimate, 60)
            if action_minutes > query.time_available:
                c.disqualified = True
                c.disqualified_reason = f"Needs ~{action_minutes}min, you have {query.time_available}min"
                candidates.append(c)
                continue

        # Hard filter: energy
        if query.energy == "shallow" and action.energy == "deep":
            c.disqualified = True
            c.disqualified_reason = "Requires deep focus"
            candidates.append(c)
            continue

        # Score components
        c.score += _score_today(action)
        c.score += _score_due(action, today)
        c.score += _score_prep(action, events, today)
        c.score += _score_stale(action)
        c.score += _score_imminent(action, events, today)

        candidates.append(c)

    # Sort: qualified first (by score desc), then disqualified
    qualified = sorted([c for c in candidates if not c.disqualified],
                      key=lambda c: c.score, reverse=True)
    disqualified = [c for c in candidates if c.disqualified]
    return qualified + disqualified


def dream_eligible(candidates: list[Candidate], query: Query) -> bool:
    """Should dreams be surfaced? Only when candidate set is thin and time generous."""
    qualified = [c for c in candidates if not c.disqualified]
    if len(qualified) > 2:
        return False
    if query.time_available is not None and query.time_available < 60:
        return False
    return True


def _score_today(action: Action) -> float:
    """Today-flagged items get a boost."""
    return W_TODAY if action.status == "today" else 0.0


def _score_due(action: Action, today: date) -> float:
    """Due pressure: overdue > today > soon."""
    if not action.due:
        return 0.0
    due_date = _parse_date(action.due)
    if due_date is None:
        return 0.0
    days_until = (due_date - today).days
    if days_until < 0:
        return W_DUE * 1.5  # overdue bonus
    elif days_until == 0:
        return W_DUE
    elif days_until <= 3:
        return W_DUE * 0.5
    return 0.0


def _score_prep(action: Action, events: list[HarvestedEvent], today: date) -> float:
    """Prep-actions near their lead window get a boost."""
    if not action.for_event:
        return 0.0
    if action.due:
        due_date = _parse_date(action.due)
        if due_date:
            days_until = (due_date - today).days
            if 0 <= days_until <= PREP_HORIZON:
                return W_PREP
    return 0.0


def _score_stale(action: Action) -> float:
    """Carried items get a nudge toward resolution."""
    return W_STALE * action.carry_count


def _score_imminent(action: Action, events: list[HarvestedEvent], today: date) -> float:
    """Boost actions whose linked event is very close."""
    if not action.for_event:
        return 0.0
    for event in events:
        days_until = (event.start_date - today).days
        if 0 <= days_until <= 1:
            return W_IMMINENT
    return 0.0
