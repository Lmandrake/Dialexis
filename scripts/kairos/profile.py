"""profile.py - Regenerate worker profile from worklog observations.

Contract:
- The profile is a derived perspective, regenerated deterministically from observations
- Leads with "what works for you" (strengths-first, gentle by construction)
- Never contains value judgments, scores, or shame metrics
- Versions prior profile before overwriting

Postconditions:
- Profile is regenerated entirely from worklog (no other inputs)
- Prior version is preserved as worker_profile_YYYYMMDD.md
- Rebuild triggered every 20 observations or weekly

Halt conditions:
- None (profile generation is always safe; empty worklog produces minimal profile)
"""
from __future__ import annotations
from collections import Counter
from datetime import date
from pathlib import Path

import paths

try:
    from kairos.observe import list_observations, count_observations, Observation
except ImportError:
    from observe import list_observations, count_observations, Observation


REBUILD_THRESHOLD = 20  # observations
REBUILD_INTERVAL_DAYS = 7


def should_rebuild(obs_count: int, last_rebuild: date | None = None,
                   today: date | None = None) -> bool:
    """Determine if the profile should be rebuilt."""
    today = today or date.today()
    if obs_count >= REBUILD_THRESHOLD:
        return True
    if last_rebuild is None:
        return obs_count > 0
    days_since = (today - last_rebuild).days
    return days_since >= REBUILD_INTERVAL_DAYS and obs_count > 0


def rebuild_profile(today: date | None = None) -> Path:
    """Regenerate the worker profile from all observations.

    Versions the prior profile before overwriting.
    Returns the path to the new profile.
    """
    today = today or date.today()
    paths.AGENDA.mkdir(parents=True, exist_ok=True)

    # Version the prior profile if it exists
    if paths.WORKER_PROFILE.exists():
        versioned = paths.AGENDA / f"worker_profile_{today.isoformat()}.md"
        if not versioned.exists():
            paths.WORKER_PROFILE.rename(versioned)

    # Generate new profile
    observations = list_observations()
    content = generate_profile(observations, today)
    paths.WORKER_PROFILE.write_text(content, encoding="utf-8")
    return paths.WORKER_PROFILE


def generate_profile(observations: list[Observation], today: date | None = None) -> str:
    """Generate profile markdown from observations.

    The profile is structured as:
    1. What works for you (strengths, effective conditions)
    2. Preparation patterns (prep benefit by context)
    3. Growth experiments (one at a time, framed as experiments)
    """
    today = today or date.today()

    if not observations:
        return _minimal_profile(today)

    # Aggregate patterns
    time_of_day = Counter()
    focus_modes = Counter()
    flavors_completed = Counter()
    helpers = Counter()
    hinderers = Counter()
    durations = []
    prep_outcomes = []

    for obs in observations:
        if obs.time_of_day:
            time_of_day[obs.time_of_day] += 1
        if obs.focus:
            focus_modes[obs.focus] += 1
        if obs.what_helped:
            helpers[obs.what_helped] += 1
        if obs.what_hindered:
            hinderers[obs.what_hindered] += 1
        if obs.actual_minutes:
            durations.append(obs.actual_minutes)
        if obs.kind == "prep_outcome":
            prep_outcomes.append(obs)

    sections = [f"# Worker Profile\n\nGenerated: {today.isoformat()} | Observations: {len(observations)}\n"]

    # Section 1: What works for you
    sections.append("## What Works for You\n")
    if time_of_day:
        best_time = time_of_day.most_common(1)[0]
        sections.append(f"- Most productive time: **{best_time[0]}** ({best_time[1]} observations)")
    if focus_modes:
        best_focus = focus_modes.most_common(1)[0]
        sections.append(f"- Typical focus mode: **{best_focus[0]}** ({best_focus[1]} observations)")
    if helpers:
        top_helpers = helpers.most_common(3)
        for helper, count in top_helpers:
            sections.append(f"- What helps: {helper} ({count}x)")
    if durations:
        avg = sum(durations) // len(durations)
        sections.append(f"- Average session: ~{avg} minutes")
    if not any([time_of_day, focus_modes, helpers]):
        sections.append("- (Building profile — more observations needed)")

    # Section 2: Preparation patterns
    sections.append("\n## Preparation Patterns\n")
    if prep_outcomes:
        sections.append(f"- Prep observations recorded: {len(prep_outcomes)}")
    else:
        sections.append("- (No preparation data yet)")

    # Section 3: Growth experiments
    sections.append("\n## Growth Experiments\n")
    if hinderers:
        top_hinderer = hinderers.most_common(1)[0]
        sections.append(
            f"- Experiment: Try reducing **{top_hinderer[0]}** — "
            f"it showed up as a hindrance {top_hinderer[1]} time(s)")
    else:
        sections.append("- (No experiments yet — more data needed)")

    return "\n".join(sections) + "\n"


def _minimal_profile(today: date) -> str:
    """Generate a minimal profile when no observations exist."""
    return (
        f"# Worker Profile\n\n"
        f"Generated: {today.isoformat()} | Observations: 0\n\n"
        f"## What Works for You\n\n"
        f"- (No observations yet — use `kairos done` or `kairos log` to build your profile)\n\n"
        f"## Preparation Patterns\n\n"
        f"- (No preparation data yet)\n\n"
        f"## Growth Experiments\n\n"
        f"- (No experiments yet)\n"
    )
