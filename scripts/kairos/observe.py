"""observe.py - Append-only, immutable observation records.

Contract:
- Observations are append-only (new file per observation)
- Never overwrites an existing observation file
- Observations are neutral, factual records (no value judgments)
- Records are immutable after creation

Postconditions:
- Each observation gets a unique file: obs_YYYYMMDD_HHMMSS.md
- Existing files are never modified
- list_observations returns all observations, optionally filtered

Halt conditions:
- Attempting to overwrite an existing observation file -> ObserveError
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import paths


class ObserveError(Exception):
    """Raised when an observation operation violates immutability."""


@dataclass
class Observation:
    timestamp: str
    kind: str  # "completion", "log", "prep_outcome"
    text: str
    action_id: str | None = None
    actual_minutes: int | None = None
    focus: str | None = None  # "deep" or "scattered"
    what_helped: str | None = None
    what_hindered: str | None = None
    time_of_day: str | None = None
    # How the work actually felt at completion, in the worker's own words (e.g. "energized",
    # "draining", "meh"). Added 2026-09-03: the board captures this at completion but Kairos's
    # worklog had nowhere to put it. Free text, not an enum — §9's gentleness rule is that the
    # profile never scores, so this field is never validated against a closed vocabulary, only
    # recorded neutrally like every other observation field.
    felt: str | None = None


def record_observation(obs: Observation) -> Path:
    """Write an observation to the worklog. Returns the file path.

    Refuses to overwrite existing files (immutability guarantee).
    """
    paths.AGENDA_WORKLOG.mkdir(parents=True, exist_ok=True)

    # Generate filename from timestamp
    ts = obs.timestamp.replace(":", "").replace("-", "").replace("T", "_")
    # Keep just YYYYMMDD_HHMMSS
    ts_clean = re.sub(r"[^0-9_]", "", ts)[:15]
    filename = f"obs_{ts_clean}.md"
    filepath = paths.AGENDA_WORKLOG / filename

    if filepath.exists():
        raise ObserveError(f"Observation file already exists: {filepath.name} — immutability violated")

    content = _serialize_observation(obs)
    filepath.write_text(content, encoding="utf-8")
    return filepath


def list_observations(since: date | None = None) -> list[Observation]:
    """Read all observations from the worklog, optionally filtered by date."""
    if not paths.AGENDA_WORKLOG.exists():
        return []

    results = []
    for f in sorted(paths.AGENDA_WORKLOG.glob("obs_*.md")):
        obs = _parse_observation(f)
        if obs is None:
            continue
        if since:
            obs_date = _extract_date(obs.timestamp)
            if obs_date and obs_date < since:
                continue
        results.append(obs)
    return results


def count_observations() -> int:
    """Count total observations in the worklog."""
    if not paths.AGENDA_WORKLOG.exists():
        return 0
    return len(list(paths.AGENDA_WORKLOG.glob("obs_*.md")))


def _serialize_observation(obs: Observation) -> str:
    """Serialize an observation to markdown."""
    lines = [
        f"# Observation {obs.timestamp}",
        f"- kind: {obs.kind}",
    ]
    if obs.action_id:
        lines.append(f"- action: {obs.action_id}")
    lines.append(f"- text: {obs.text}")
    if obs.actual_minutes is not None:
        lines.append(f"- actual_minutes: {obs.actual_minutes}")
    if obs.focus:
        lines.append(f"- focus: {obs.focus}")
    if obs.what_helped:
        lines.append(f"- what_helped: {obs.what_helped}")
    if obs.what_hindered:
        lines.append(f"- what_hindered: {obs.what_hindered}")
    if obs.time_of_day:
        lines.append(f"- time_of_day: {obs.time_of_day}")
    if obs.felt:
        lines.append(f"- felt: {obs.felt}")
    return "\n".join(lines) + "\n"


def _parse_observation(filepath: Path) -> Observation | None:
    """Parse an observation file back into an Observation object."""
    content = filepath.read_text(encoding="utf-8")
    fields = {}

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# Observation "):
            fields["timestamp"] = line[len("# Observation "):]
        elif line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            fields[key.strip()] = value.strip()

    if "timestamp" not in fields or "kind" not in fields:
        return None

    return Observation(
        timestamp=fields["timestamp"],
        kind=fields["kind"],
        text=fields.get("text", ""),
        action_id=fields.get("action"),
        actual_minutes=int(fields["actual_minutes"]) if "actual_minutes" in fields else None,
        focus=fields.get("focus"),
        what_helped=fields.get("what_helped"),
        what_hindered=fields.get("what_hindered"),
        time_of_day=fields.get("time_of_day"),
        felt=fields.get("felt"),
    )


def _extract_date(timestamp: str) -> date | None:
    """Extract date from a timestamp string."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", timestamp)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None
