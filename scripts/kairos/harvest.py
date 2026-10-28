"""harvest.py - Pull upcoming events and milestones from the graph into candidates.

Contract:
- Scans graph/events/ for entities with future dates
- Scans all entities for due:/milestone: attribute markers
- Returns structured candidate lists for the index and prep system
- Read-only over the graph; never writes

Postconditions:
- Only events with start >= today within horizon are returned
- Events with missing/unparseable dates are skipped (not halted on)
- Returns empty list when nothing matches
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import paths
import entity_io as eio
from naming import slug as _slug


@dataclass
class HarvestedEvent:
    canonical_name: str
    entity_path: Path
    start_date: date
    kind: str = "unknown"


@dataclass
class HarvestedMilestone:
    entity_name: str
    entity_path: Path
    due_date: date
    label: str = ""


def harvest_events(horizon_days: int = 7) -> list[HarvestedEvent]:
    """Scan graph/events/ for events with future dates within horizon."""
    events_dir = paths.entity_dir("events")
    if not events_dir.exists():
        return []

    today = date.today()
    horizon_end = today + timedelta(days=horizon_days)
    results = []

    for entity_file in sorted(events_dir.glob("*.md")):
        entity = eio.parse_file(entity_file)
        event_date = _extract_event_date(entity)
        if event_date is None:
            continue
        if today <= event_date <= horizon_end:
            kind = _extract_event_kind(entity)
            results.append(HarvestedEvent(
                canonical_name=entity.canonical_name,
                entity_path=entity_file,
                start_date=event_date,
                kind=kind,
            ))

    return sorted(results, key=lambda e: e.start_date)


def find_event_by_name(canonical_name: str) -> HarvestedEvent | None:
    """Load exactly one event entity by name, with no horizon window.

    harvest_events() only returns events inside a horizon (default 7 days) — right for the
    prep-prompt and recommendation surfaces, wrong for due-derivation: an event-anchored
    action (KAIROS_SPEC.md §6) needs its anchor's start date whenever the anchor exists on
    disk, whether that event is tomorrow, three months out, or already past. Returns None
    when the entity file doesn't exist, or exists but carries no parseable start date (an
    event that is "declared" but not yet "confirmed" per the acceptance-fan-out design) —
    never a guessed date.
    """
    path = paths.entity_dir("events") / f"{_slug(canonical_name)}.md"
    if not path.exists():
        return None
    entity = eio.parse_file(path)
    event_date = _extract_event_date(entity)
    if event_date is None:
        return None
    return HarvestedEvent(canonical_name=entity.canonical_name, entity_path=path,
                          start_date=event_date, kind=_extract_event_kind(entity))


def harvest_milestones() -> list[HarvestedMilestone]:
    """Scan all entities for due:/milestone: attribute markers."""
    today = date.today()
    results = []

    for type_dir in paths.WORKING_MODEL.iterdir():
        if not type_dir.is_dir() or type_dir.name.startswith("_"):
            continue
        for entity_file in type_dir.glob("*.md"):
            entity = eio.parse_file(entity_file)
            for attr in entity.attributes:
                key = attr.get("key", "").lower()
                if key in ("due", "milestone", "deadline"):
                    parsed = _parse_date(attr.get("value", ""))
                    if parsed and parsed >= today:
                        results.append(HarvestedMilestone(
                            entity_name=entity.canonical_name,
                            entity_path=entity_file,
                            due_date=parsed,
                            label=attr.get("value", ""),
                        ))

    return sorted(results, key=lambda m: m.due_date)


def _extract_event_date(entity) -> date | None:
    """Extract the primary date from an event entity's attributes."""
    for attr in entity.attributes:
        key = attr.get("key", "").lower()
        if key in ("date", "start date", "event date", "start"):
            parsed = _parse_date(attr.get("value", ""))
            if parsed:
                return parsed
    # Also check events/history section for a date
    if entity.events:
        for ev in entity.events:
            parsed = _parse_date(ev.get("date", ""))
            if parsed:
                return parsed
    return None


def _extract_event_kind(entity) -> str:
    """Extract the kind/type of event from attributes."""
    for attr in entity.attributes:
        key = attr.get("key", "").lower()
        if key in ("kind", "type", "event type", "format"):
            return attr.get("value", "unknown").lower()
    # Heuristic from name
    name_lower = entity.canonical_name.lower()
    if "meeting" in name_lower:
        return "project_meeting"
    if "conference" in name_lower:
        return "conference"
    if "talk" in name_lower or "presentation" in name_lower:
        return "talk"
    return "unknown"


def _parse_date(value: str) -> date | None:
    """Try to parse a date string in ISO format (YYYY-MM-DD)."""
    if not value:
        return None
    # Match YYYY-MM-DD at the start of the string
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None
