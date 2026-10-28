"""parse.py - Deterministic read/write for the action ledger (agenda/actions.md).

Contract:
- Reads and writes the YAML-like block format from the Kairos spec §4
- Validates schema fields against closed enumerations
- Never performs semantic judgments — that's the LLM's job

Postconditions:
- parse_ledger -> serialize_ledger -> parse_ledger is identity (round-trip stable)
- validate_action returns empty list iff all fields are schema-valid
- next_id always produces a unique, monotonically increasing ID

Halt conditions:
- Malformed block (missing required indent structure) -> ParseError
- File contains unparseable content between valid blocks -> ParseError
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


class ParseError(Exception):
    """Raised when ledger content is malformed."""


#: "≤2m" was added 2026-09-03 as a finer bottom bucket (KAIROS_SPEC.md §4/§11): the board this
#: graph's Kairos layer feeds from captures a "2m" bucket for the two-minute-reply case, and
#: until this bucket existed both "2m" and "15m" collapsed onto "≤15m" — a stated, deliberate
#: loss (see Lodestar bin/lib/intents.py's KAIROS_ESTIMATE comment). Adding a bucket is
#: purely additive: every existing record using the original four values is still valid, so no
#: migration of on-disk ledger/archive files is required. New/updated records may use "≤2m".
ESTIMATES = frozenset({"≤2m", "≤15m", "short", "session", "deep"})
ENERGIES = frozenset({"deep", "shallow"})
FLAVORS = frozenset({"creative", "analytical", "admin", "social", "learning", "writing", "physical"})
STATUSES = frozenset({"active", "today", "done", "released", "expired"})

CARRY_LIMIT = 3
STALE_DAYS = 14
TODAY_CAP = 3
PREP_HORIZON = 2


@dataclass
class Action:
    id: str = ""
    title: str = ""
    next_action: str = ""
    project: str | None = None
    for_event: str | None = None
    for_dream: str | None = None
    due: str | None = None
    estimate: str = "short"
    energy: str = "shallow"
    flavor: str = "admin"
    status: str = "active"
    created: str = ""
    carry_count: int = 0
    last_review: str = ""


def parse_ledger(path: Path) -> list[Action]:
    """Parse the action ledger file into a list of Action objects."""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return _parse_blocks(content)


def _parse_blocks(content: str) -> list[Action]:
    """Parse YAML-like blocks starting with '- id:'."""
    actions = []
    blocks = re.split(r"\n(?=- id:)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if not block.startswith("- id:"):
            raise ParseError(f"Block does not start with '- id:': {block[:60]!r}")
        actions.append(_parse_block(block))
    return actions


def _parse_block(block: str) -> Action:
    """Parse a single action block into an Action dataclass."""
    action = Action()
    for line in block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip leading '- ' for the first line
        if line.startswith("- "):
            line = line[2:]

        # Strip inline comments
        if "  #" in line:
            line = line[:line.index("  #")]

        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key == "id":
            action.id = value
        elif key == "title":
            action.title = value
        elif key == "next_action":
            action.next_action = value
        elif key == "project":
            action.project = _parse_nullable(value)
        elif key == "for_event":
            action.for_event = _parse_nullable(value)
        elif key == "for_dream":
            action.for_dream = _parse_nullable(value)
        elif key == "due":
            action.due = _parse_nullable(value)
        elif key == "estimate":
            action.estimate = value
        elif key == "energy":
            action.energy = value
        elif key == "flavor":
            action.flavor = value
        elif key == "status":
            action.status = value
        elif key == "created":
            action.created = value
        elif key == "carry_count":
            action.carry_count = int(value)
        elif key == "last_review":
            action.last_review = value

    return action


def _parse_nullable(value: str) -> str | None:
    """Parse a value that might be null/None."""
    if value.lower() in ("null", "none", ""):
        return None
    return value


def serialize_ledger(actions: list[Action]) -> str:
    """Serialize a list of Actions to the canonical ledger format."""
    blocks = []
    for a in actions:
        blocks.append(_serialize_action(a))
    return "\n".join(blocks) + "\n" if blocks else ""


def _serialize_action(a: Action) -> str:
    """Serialize a single Action to its block format."""
    lines = [
        f"- id:           {a.id}",
        f"  title:        {a.title}",
        f"  next_action:  {a.next_action}",
        f"  project:      {_fmt_nullable(a.project)}",
        f"  for_event:    {_fmt_nullable(a.for_event)}",
        f"  for_dream:    {_fmt_nullable(a.for_dream)}",
        f"  due:          {_fmt_nullable(a.due)}",
        f"  estimate:     {a.estimate}",
        f"  energy:       {a.energy}",
        f"  flavor:       {a.flavor}",
        f"  status:       {a.status}",
        f"  created:      {a.created}",
        f"  carry_count:  {a.carry_count}",
        f"  last_review:  {a.last_review}",
    ]
    return "\n".join(lines)


def _fmt_nullable(value: str | None) -> str:
    """Format a nullable value for serialization."""
    return value if value is not None else "null"


def next_id(actions: list[Action]) -> str:
    """Generate the next action ID (act_NNNN)."""
    max_num = 0
    for a in actions:
        m = re.match(r"act_(\d+)", a.id)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"act_{max_num + 1:04d}"


def validate_action(action: Action) -> list[str]:
    """Validate an action against the schema. Returns list of error strings (empty = valid)."""
    errors = []
    if not action.id:
        errors.append("id is required")
    elif not re.match(r"act_\d{4,}", action.id):
        errors.append(f"id must match act_NNNN format, got: {action.id!r}")
    if not action.title:
        errors.append("title is required")
    if not action.next_action:
        errors.append("next_action is required")
    if action.estimate not in ESTIMATES:
        errors.append(f"estimate must be one of {sorted(ESTIMATES)}, got: {action.estimate!r}")
    if action.energy not in ENERGIES:
        errors.append(f"energy must be one of {sorted(ENERGIES)}, got: {action.energy!r}")
    if action.flavor not in FLAVORS:
        errors.append(f"flavor must be one of {sorted(FLAVORS)}, got: {action.flavor!r}")
    if action.status not in STATUSES:
        errors.append(f"status must be one of {sorted(STATUSES)}, got: {action.status!r}")
    if not action.created:
        errors.append("created date is required")
    return errors
