"""index.py - Regenerate _index/agenda.md as a derived read-model.

Contract:
- Unions harvested events, active ledger actions, today-set, and prep prompts
- Output is never authoritative; always regenerated from source data
- Idempotent — running twice produces identical output

Postconditions:
- _index/agenda.md reflects current state of all sources
- Never hand-edited; always regenerated

Halt conditions:
- None (index generation is always safe; missing data produces empty sections)
"""
from __future__ import annotations
from datetime import date
from pathlib import Path

import paths

try:
    from kairos.parse import parse_ledger, Action
    from kairos.harvest import harvest_events, harvest_milestones
    from kairos.prep import find_prep_eligible
except ImportError:
    from parse import parse_ledger, Action
    from harvest import harvest_events, harvest_milestones
    from prep import find_prep_eligible


def regenerate_index(today: date | None = None) -> Path:
    """Regenerate _index/agenda.md from all sources.

    Returns the path to the generated index file.
    """
    today = today or date.today()
    paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    index_path = paths.INDEX_DIR / "agenda.md"

    # Gather data
    actions = parse_ledger(paths.AGENDA_LEDGER) if paths.AGENDA_LEDGER.exists() else []
    events = harvest_events(horizon_days=7)
    milestones = harvest_milestones()

    today_set = [a for a in actions if a.status == "today"]
    active_actions = [a for a in actions if a.status == "active"]
    prep_prompts = find_prep_eligible(events, actions, today=today)

    # Generate content
    sections = [f"# Agenda Index\n\nGenerated: {today.isoformat()}\n"]

    # Today section
    sections.append("## Today\n")
    if today_set:
        for a in today_set:
            due_str = f" (due {a.due})" if a.due else ""
            sections.append(f"- **{a.title}**{due_str}")
            sections.append(f"  Next: {a.next_action}")
    else:
        sections.append("(nothing set for today)")

    # Upcoming Events
    sections.append("\n## Upcoming Events\n")
    if events:
        for e in events:
            days = (e.start_date - today).days
            day_str = "today" if days == 0 else f"in {days}d"
            sections.append(f"- **{e.canonical_name}** — {e.start_date.isoformat()} ({day_str})")
    else:
        sections.append("(no upcoming events within 7 days)")

    # Prep Needed
    if prep_prompts:
        sections.append("\n## Prep Needed\n")
        for p in prep_prompts:
            sections.append(f"- {p.event_name} ({p.kind}) — {p.days_until}d away, no prep yet")

    # Active Actions
    sections.append("\n## Active Actions\n")
    if active_actions:
        for a in active_actions:
            due_str = f" [due {a.due}]" if a.due else ""
            carry_str = f" (carried {a.carry_count}x)" if a.carry_count > 0 else ""
            sections.append(f"- {a.title}{due_str}{carry_str}")
            sections.append(f"  Next: {a.next_action}")
    else:
        sections.append("(no active actions)")

    # Milestones
    if milestones:
        sections.append("\n## Upcoming Milestones\n")
        for m in milestones:
            days = (m.due_date - today).days
            sections.append(f"- {m.entity_name}: {m.label} (in {days}d)")

    content = "\n".join(sections) + "\n"
    index_path.write_text(content, encoding="utf-8")
    return index_path
