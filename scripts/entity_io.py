"""entity_io.py - Keystone: parse (tolerant) / serialize (strict canonical) / merge facts."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ASSERTION_BREAK = re.compile(r"[.!?](?:\s+|$)(?=[A-Z(])|;\s")


def assertion_count(value: Any) -> int:
    """How many separate assertions a fact value appears to carry. 1 means atomic.

    A fact line asserting several things behind one metadata bracket cannot be audited or scored
    per claim, and a single `confidence` then covers assertions whose support genuinely differs.
    Deliberately conservative — it under-counts rather than cry wolf on a long single-claim value
    such as a quoted title. Shared by `audit_provenance` (the standing metric) and `commit.py`
    (the write-time nudge) so both agree on what "bundled" means.
    """
    text = str(value).strip()
    if not text:
        return 1
    text = re.sub(r"\b[A-Z]\.", "X", text)          # initials, not sentence ends
    text = re.sub(r"\d\.\d", "0", text)             # decimals
    text = re.sub(r"https?://\S+", "URL", text)     # URLs
    return len([s for s in _ASSERTION_BREAK.split(text) if s and s.strip()]) or 1


def canonical_fact_value(key: str, value: Any) -> Any:
    """Canonicalize a fact value for storage and comparison.

    Email addresses are case-insensitive: capitalization in an email is never
    significant, so email values are normalized to lowercase everywhere -- on
    parse, on merge, and on write. This makes two emails that differ only in
    case compare equal (no spurious conflict) and keeps a single canonical
    stored form. Any key containing 'email' (e.g. 'Email', 'Work Email',
    'Contact Email') is treated as an email.
    """
    if isinstance(value, str) and "email" in key.strip().lower():
        return value.strip().lower()
    return value


@dataclass
class Entity:
    """In-memory representation of an entity file."""
    canonical_name: str
    entity_type: str
    status: str = "Unknown"
    summary: str = ""
    aliases: list[str] = field(default_factory=list)
    identifiers: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    perspectives: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    last_updated: str = ""
    path: Path | None = field(default=None, repr=False)

    @property
    def stem(self) -> str:
        """Return the filename stem (slug) if path is set."""
        from naming import slug
        return slug(self.canonical_name)


def parse_file(path: Path) -> Entity:
    """Parse an entity markdown file. Tolerant: handles legacy formats."""
    if not path.exists():
        raise FileNotFoundError(f"entity file not found: {path}")

    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")

    entity = Entity(canonical_name="", entity_type="", path=path)

    # Extract canonical name from first heading
    for line in lines:
        if line.startswith("# "):
            entity.canonical_name = line[2:].strip()
            break

    # Parse sections
    current_section = None
    section_buffer = []

    for line in lines[1:]:  # Skip first line (name heading)
        if line.startswith("## "):
            # Process previous section
            if current_section:
                _process_section(entity, current_section, section_buffer)
            current_section = line[3:].strip()
            section_buffer = []
        else:
            section_buffer.append(line)

    # Process final section
    if current_section:
        _process_section(entity, current_section, section_buffer)

    return entity


def _process_section(entity: Entity, section: str, lines: list[str]) -> None:
    """Process a single section's content into the entity."""
    content = "\n".join(lines).strip()

    if section == "Summary":
        entity.summary = content.rstrip().removesuffix("---").rstrip()
    elif section == "Identifiers":
        entity.identifiers = _parse_facts(content)
    elif section == "Attributes":
        entity.attributes = _parse_facts(content)
    elif section == "Relationships":
        entity.relationships = _parse_relationships(content)
    elif section == "Perspectives":
        entity.perspectives = _parse_perspectives(content)
    elif section == "Events and History":
        entity.events = _parse_events(content)
    elif section == "Conflicts":
        entity.conflicts = _parse_conflicts(content)
    elif section == "Notes":
        entity.notes = content
    elif section == "Sources":
        entity.sources = _parse_sources(content)
    elif section.startswith("Metadata"):
        _parse_metadata(entity, content)


def _parse_metadata(entity: Entity, content: str) -> None:
    """Extract metadata fields."""
    for line in content.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip("- ").strip()
            value = value.strip()
            if key == "Type":
                entity.entity_type = value
            elif key == "Status":
                entity.status = value
            elif key == "Last updated":
                entity.last_updated = value
            elif key == "Aliases":
                entity.aliases = [a.strip() for a in value.split(",")
                                  if a.strip() and a.strip().lower() != "none"]


def _parse_facts(content: str) -> list[dict[str, Any]]:
    """Parse identifier or attribute facts from bulleted list."""
    import re
    facts = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or not line.startswith("- "):
            continue
        line = line[2:]  # Remove bullet
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip().strip("*").strip()
        rest = rest.strip().strip("*").strip()

        metadata = {}

        # Try bracket format first: value [key=val, key=val]
        parts = rest.split(" [")
        value = parts[0].strip()

        if len(parts) > 1:
            meta_str = "[" + " [".join(parts[1:])
            for match in re.finditer(r"(\w+)=([^\],]+)", meta_str):
                metadata[match.group(1)] = match.group(2).strip()

        # Try pipe format: value | Confidence: X | Source: Y | Date: Z
        if not metadata and " | " in value:
            pipe_parts = value.split(" | ")
            value = pipe_parts[0].strip()
            for part in pipe_parts[1:]:
                if ":" in part:
                    mk, mv = part.split(":", 1)
                    mk = mk.strip().lower()
                    mv = mv.strip()
                    if mv and mv.lower() != "none":
                        metadata[mk] = mv

        # Strip trailing [] from value (empty metadata brackets)
        if value.endswith(" []"):
            value = value[:-3].strip()

        # Handle edge case: empty value with leading pipe (e.g. "| Confidence: HIGH")
        if value.startswith("|"):
            pipe_remainder = value.lstrip("| ").strip()
            if ":" in pipe_remainder:
                for part in value.split("|")[1:]:
                    part = part.strip()
                    if ":" in part:
                        mk, mv = part.split(":", 1)
                        mk = mk.strip().lower()
                        mv = mv.strip()
                        if mv and mv.lower() != "none":
                            metadata[mk] = mv
                value = ""

        if not value:
            continue

        value = canonical_fact_value(key, value)
        fact = {"key": key, "value": value, **metadata}
        if not any(f["key"] == key and f["value"] == value for f in facts):
            facts.append(fact)
    return facts


def _parse_relationships(content: str) -> list[dict[str, Any]]:
    """Parse relationships from bulleted list."""
    relationships = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or not line.startswith("- "):
            continue
        line = line[2:]

        # Format: "rel_type: [[target_dir/target_stem|target_name]] [confidence=X, date=Y]"
        import re
        match = re.match(r"([^:]+):\s*\[\[([^/]+)/([^\|]+)\|(.+?)\]\](.*)$", line)
        if not match:
            continue

        rel_type = match.group(1).strip()
        target_dir = match.group(2).strip()
        target_stem = match.group(3).strip()
        target_name = match.group(4).strip()
        rest = match.group(5).strip()

        metadata = {"confidence": "MEDIUM", "date": "unknown"}
        for m in re.finditer(r"(\w+)=([^\],]+)", rest):
            metadata[m.group(1)] = m.group(2).strip()

        relationships.append({
            "rel_type": rel_type,
            "target_name": target_name,
            "target_dir": target_dir,
            "target_stem": target_stem,
            **metadata
        })
    return relationships


def _parse_perspectives(content: str) -> list[dict[str, Any]]:
    """Parse perspectives from structured blocks."""
    perspectives = []
    lines = content.split("\n")
    current = {}

    for line in lines:
        line = line.strip()
        if line.startswith("**") and line.endswith("**"):
            if current:
                perspectives.append(current)
            current = {"title": line.strip("*")}
        elif ":" in line and current:
            key, value = line.split(":", 1)
            key = key.strip("- ").strip().lower().replace(" ", "_")
            current[key] = value.strip()

    if current:
        perspectives.append(current)

    return perspectives


def _parse_events(content: str) -> list[dict[str, Any]]:
    """Parse events from bulleted list."""
    import re
    events = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or not line.startswith("- "):
            continue
        line = line[2:]

        if ":" not in line:
            continue
        date, rest = line.split(":", 1)
        date = date.strip().strip("*").strip()
        rest = rest.strip().lstrip("*").strip()

        # Try bracket format first: desc [key=val, key=val]
        parts = rest.split(" [")
        desc = parts[0].strip()
        metadata = {}

        if len(parts) > 1:
            meta_str = "[" + " [".join(parts[1:])
            for match in re.finditer(r"(\w+)=([^\],]+)", meta_str):
                metadata[match.group(1)] = match.group(2).strip()

        # Try pipe format: desc | Confidence: X | Source: Y | Date: Z
        if not metadata and " | " in desc:
            pipe_parts = desc.split(" | ")
            desc = pipe_parts[0].strip()
            for part in pipe_parts[1:]:
                if ":" in part:
                    mk, mv = part.split(":", 1)
                    mk = mk.strip().lower()
                    mv = mv.strip()
                    if mv and mv.lower() != "none":
                        metadata[mk] = mv

        if not date or not desc:
            continue
        events.append({"date": date, "desc": desc, **metadata})
    return events


def _parse_conflicts(content: str) -> list[dict[str, Any]]:
    """Parse conflict blocks."""
    conflicts = []
    lines = content.split("\n")
    current = {}

    for line in lines:
        line = line.strip()
        if line.startswith("**") and line.endswith("**"):
            if current:
                conflicts.append(current)
            current = {"title": line.strip("*")}
        elif ":" in line and current:
            key, value = line.split(":", 1)
            key = key.strip("- ").strip().lower().replace(" ", "_")
            current[key] = value.strip()

    if current:
        conflicts.append(current)

    return conflicts


def _parse_sources(content: str) -> list[dict[str, Any]]:
    """Parse sources from bulleted list."""
    sources = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or not line.startswith("- "):
            continue
        line = line[2:]

        # Format: "name (archive=path)"
        import re
        match = re.match(r"(.+)\(archive=([^)]+)\)", line)
        if match:
            sources.append({
                "name": match.group(1).strip(),
                "archive": match.group(2).strip()
            })
    return sources


# Canonical order for metadata keys inside a fact/relationship/event bracket. Emitting in a
# fixed order (rather than dict-insertion order) keeps serialization deterministic, so
# reconcile produces no spurious diffs no matter how a manifest happened to build the dict.
# Any key not listed is appended afterward in sorted order.
_META_ORDER = ["confidence", "temporal_class", "source", "date", "last_verified"]


def _meta_str(d: dict, skip: tuple[str, ...]) -> str:
    """Render metadata keys of `d` (excluding `skip`) in canonical order as ` [k=v, ...]`.

    Returns "" when there is no metadata, so callers that had no brackets stay bracket-free.
    """
    ordered = [(k, d[k]) for k in _META_ORDER if k in d and k not in skip]
    extras = sorted((k, v) for k, v in d.items() if k not in skip and k not in _META_ORDER)
    inner = ", ".join(f"{k}={v}" for k, v in [*ordered, *extras])
    return f" [{inner}]" if inner else ""


def serialize(entity: Entity) -> str:
    """Serialize entity to canonical markdown format."""
    sections = []

    # Title
    sections.append(f"# {entity.canonical_name}\n")

    # Metadata
    meta = ["## Metadata", f"- Type: {entity.entity_type}", f"- Status: {entity.status}"]
    if entity.aliases:
        meta.append(f"- Aliases: {', '.join(entity.aliases)}")
    if entity.last_updated:
        meta.append(f"- Last updated: {entity.last_updated}")
    sections.append("\n".join(meta))

    # Summary
    sections.append(f"## Summary\n{entity.summary if entity.summary else '(none)'}")

    # Identifiers
    sections.append("## Identifiers")
    if entity.identifiers:
        for f in entity.identifiers:
            sections.append(f"- {f['key']}: {f['value']}{_meta_str(f, ('key', 'value'))}")
    else:
        sections.append("(none)")

    # Attributes
    sections.append("\n## Attributes")
    if entity.attributes:
        for f in entity.attributes:
            sections.append(f"- {f['key']}: {f['value']}{_meta_str(f, ('key', 'value'))}")
    else:
        sections.append("(none)")

    # Relationships (deduplicated by rel_type + target_stem)
    sections.append("\n## Relationships")
    if entity.relationships:
        seen = set()
        for r in entity.relationships:
            dedup_key = (r['rel_type'], r['target_stem'])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            meta_str = _meta_str(r, ("rel_type", "target_name", "target_dir", "target_stem"))
            sections.append(
                f"- {r['rel_type']}: [[{r['target_dir']}/{r['target_stem']}|{r['target_name']}]]{meta_str}"
            )
    else:
        sections.append("(none)")

    # Perspectives
    sections.append("\n## Perspectives")
    if entity.perspectives:
        for p in entity.perspectives:
            sections.append(f"\n**{p.get('title', 'Untitled')}**")
            for k, v in p.items():
                if k != "title":
                    sections.append(f"- {k.replace('_', ' ').title()}: {v}")
    else:
        sections.append("(none)")

    # Events
    sections.append("\n## Events and History")
    if entity.events:
        for e in entity.events:
            sections.append(f"- {e['date']}: {e['desc']}{_meta_str(e, ('date', 'desc'))}")
    else:
        sections.append("(none)")

    # Conflicts
    sections.append("\n## Conflicts")
    if entity.conflicts:
        for c in entity.conflicts:
            sections.append(f"\n**{c.get('title', 'Untitled')}**")
            for k, v in c.items():
                if k != "title":
                    sections.append(f"- {k.replace('_', ' ').title()}: {v}")
    else:
        sections.append("(none)")

    # Notes
    sections.append(f"\n## Notes\n{entity.notes if entity.notes else '(none)'}")

    # Sources
    sections.append("\n## Sources")
    if entity.sources:
        for s in entity.sources:
            sections.append(f"- {s['name']} (archive={s['archive']})")
    else:
        sections.append("(none)")

    return "\n".join(sections) + "\n"


def merge_facts(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Merge incoming facts into existing, detecting contradictions.

    Returns: (merged_facts, contradictions)
    where contradictions is a list of (key, old_value, new_value) tuples.
    """
    contradictions = []
    merged = {}
    for f in existing:
        f = dict(f)
        f["value"] = canonical_fact_value(f["key"], f["value"])
        merged[f["key"]] = f

    for fact in incoming:
        key = fact["key"]
        fact = dict(fact)
        fact["value"] = canonical_fact_value(key, fact["value"])
        if key in merged:
            if merged[key]["value"] != fact["value"]:
                contradictions.append((key, merged[key]["value"], fact["value"]))
            # temporal_class is a curated judgment; a re-assertion that doesn't opine on it
            # inherits the prior value rather than silently dropping it.
            if "temporal_class" in merged[key] and "temporal_class" not in fact:
                fact["temporal_class"] = merged[key]["temporal_class"]
        merged[key] = fact

    return list(merged.values()), contradictions


def relationship_present(relationships: list[dict], rel_type: str, target_stem: str) -> bool:
    """Check if a relationship to target_stem already exists."""
    return any(r["rel_type"] == rel_type and r["target_stem"] == target_stem
               for r in relationships)
