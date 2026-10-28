# Entity Type Registry

**Purpose:** This file is the single authoritative source of truth for the *shape* of
the Dialexis knowledge base. It defines the closed vocabularies that deterministic
Python enforces and that the semantic agents must stay inside:

- the allowed **entity types** (and where their files live)
- the allowed **relationship types** and their **inverses** (for reciprocal-edge enforcement)
- the **source-type → default-confidence** mapping
- the allowed **enumerations** (confidence levels, statuses, evidence types)
- the **naming policy** per entity type

**Contract.** Nothing outside these vocabularies may be created *silently*. When an agent
encounters something the registry does not cover (a new entity type, an unknown
relationship), the deterministic layer **halts and escalates** rather than guessing. Extending
the registry is a deliberate, recorded edit to this file — never an accident on disk.

**For humans:** read the prose. **For machines:** `registry.py` parses the single fenced
```json block below. The prose and the JSON must agree; the JSON is authoritative.

---

## Entity Types

Eleven first-class types. Each entity file lives in `graph/<directory>/`.

| Type | Directory | Notes |
|---|---|---|
| `person` | `people` | Individuals. Naming prefers fuller forms (middle initials). |
| `organization` | `organizations` | Companies, institutions, institutional sections/groups, agencies. |
| `project` | `projects` | Initiatives with defined scope and timeline. |
| `program` | `programs` | Funding or organizational programs (Program A, Program B). |
| `event` | `events` | Meetings, workshops, launches, milestones. |
| `technology_concept` | `technology_concepts` | Ideas, approaches, algorithms, methods. |
| `technology_system` | `technology_systems` | Implemented systems, platforms, tools, instruments. |
| `document` | `documents` | Publications (papers, preprints, decks, conference videos), websites (root-level domains), technical reports, SOWs, specifications. Use Notes for content summaries. |
| `place` | `places` | Physical or named locations. |
| `dream` | `dreams` | Long-term aspirations tracked via Kairos. |

---

## Relationship Types and Inverses

Every relationship is a directed edge `source --rel--> target`. The committer writes the
**inverse** edge on the target automatically, so the graph is never asymmetric. A `null`
inverse means the relationship is **symmetric** (the same relationship type is written back).

Unknown relationship types are **rejected** — the committer halts. Add new ones here first.

| Relationship | Inverse | Symmetric |
|---|---|---|
| `Member of` | `Has member` | no |
| `Has member` | `Member of` | no |
| `Reports to` | `Supervises` | no |
| `Supervises` | `Reports to` | no |
| `Advises` | `Advised by` | no |
| `Advised by` | `Advises` | no |
| `Works with` | `Works with` | yes |
| `Collaborates with` | `Collaborates with` | yes |
| `Partners with` | `Partners with` | yes |
| `Distinct from` | `Distinct from` | yes |
| `Attends` | `Attended by` | no |
| `Attended by` | `Attends` | no |
| `Works on` | `Has contributor` | no |
| `Has contributor` | `Works on` | no |
| `Contributes to` | `Has contributor` | no |
| `Led by` | `Leads` | no |
| `Leads` | `Led by` | no |
| `Funded by` | `Funds` | no |
| `Funds` | `Funded by` | no |
| `Part of` | `Has part` | no |
| `Has part` | `Part of` | no |
| `Uses` | `Used by` | no |
| `Used by` | `Uses` | no |
| `Develops` | `Developed by` | no |
| `Developed by` | `Develops` | no |
| `Hosts` | `Hosted by` | no |
| `Hosted by` | `Hosts` | no |
| `Mission liaison for` | `Has mission liaison` | no |
| `Has mission liaison` | `Mission liaison for` | no |
| `Mission focus` | `Focus of` | no |
| `Focus of` | `Mission focus` | no |
| `Authored by` | `Authors` | no |
| `Authors` | `Authored by` | no |
| `Cites` | `Cited by` | no |
| `Cited by` | `Cites` | no |

---

## Source Types and Default Confidence

When a fact does not carry an explicit confidence, the default is derived **mechanically**
from the source type. Confidence is never invented by Python beyond this lookup; agents may
*lower* confidence with justification but the default floor is fixed here.

| Source type | Default confidence |
|---|---|
| `calendar` | HIGH |
| `org_chart` | HIGH |
| `manual` | HIGH |
| `email` | MEDIUM-HIGH |
| `preprint` | MEDIUM-HIGH |
| `transcript` | MEDIUM |
| `presentation` | MEDIUM |
| `pdf` | MEDIUM |
| `document` | MEDIUM |
| `website` | MEDIUM |
| `notes` | LOW |
| `unknown` | LOW |

`unknown` is a **deliberate declaration, not a default.** It exists so an edge or fact whose
provenance genuinely cannot be established can say so explicitly, instead of provenance being
absent and therefore invisible. Omitting `source` is an error; asserting `source=unknown` is an
honest statement. The count of `unknown` in the graph is a health metric that should only fall.

## Derivation — `source` names the medium, `derived` names what was actually read

A fact read from a *summary of* a transcript is not a fact read from the transcript. Recording only
the last hop makes second-hand content wear a primary-source label, and inherit a primary-source
confidence floor.

So `source` always names the **original medium**, and the optional `derived` field names the
**artifact actually read**. `derived` absent means the original was read directly.

| Derivation type | Meaning |
|---|---|
| `meeting_summary` | A written summary of a meeting or its transcript |
| `transcript_summary` | A summary generated from a verbatim transcript |
| `web_digest` | A distilled/extracted digest of web pages, not the pages themselves |
| `document_summary` | A summary or abstract standing in for a full document |

**Confidence is discounted one step when `derived` is present** — mechanically, in `commit.py`, not
by agent judgment. A `transcript` fact floors at MEDIUM; the same fact read from a
`meeting_summary` floors at LOW. Discounting stops at LOW.

Example:

```
- Coaching - what to omit: ... [confidence=LOW, temporal_class=mutable, source=transcript, derived=meeting_summary, date=2026-01-02, last_verified=2026-01-12]
```

---

## Enumerations

- **Confidence:** `HIGH`, `MEDIUM-HIGH`, `MEDIUM`, `LOW`
- **Temporal class (facts & relationships):** `permanent`, `mutable`, `volatile`
  - `permanent` — never goes stale: identity, dated intervals (`worked 2001–2006`), closed/past
    events, credentials. Never surfaces as an update candidate.
  - `mutable` — true now but drifts over ~years; the **default** for present-tense state
    (employment, role, org code, location, interests). Update candidate after ~18 months
    unverified.
  - `volatile` — churns fast; explicit temporariness markers (`acting`, `interim`,
    `currently`, `in review`, `until <near date>`). Update candidate after ~5 months.
  - Semantic (DECIDE): the LLM may set it in the manifest; if omitted, `commit.py` infers a
    default. `validate.py` enforces only that any stored value is one of the three.
- **Entity status:** `Active`, `Inactive`, `Unknown`
- **Evidence type (perspectives):** `stated`, `written`, `attributed`, `inferred`, `hearsay`
- **Perspective status:** `Active`, `Evolved`, `Superseded`
- **Conflict status:** `UNRESOLVED`, `RESOLVED`

---

## Naming Policy

The LLM **decides** the canonical form once (identity judgment). Python **enforces** that
every artifact agrees with the decision, and **halts** on cases the rules cannot cover.

Slug rules (all types): lowercase; every run of non-`[a-z0-9]` becomes a single `_`;
strip leading/trailing `_`. The slug is the filename stem; it is never hand-typed by an agent.

Per-type policy beyond the slug:

- `person`: prefer the fuller name form. If a fact introduces a strict superset of the
  current canonical name (e.g. canonical `John Smith`, incoming `John Q. Smith`), Python
  **does not rename silently** — it raises an `upgrade_candidate` for confirmation.
- All types: a non-empty `Aliases` field that is a strict superset of the canonical name is
  flagged as an `upgrade_candidate`, never auto-applied.

---

```json
{
  "registry_version": "2.0",
  "entity_types": {
    "person":             {"directory": "people"},
    "organization":       {"directory": "organizations"},
    "project":            {"directory": "projects"},
    "program":            {"directory": "programs"},
    "event":              {"directory": "events"},
    "technology_concept": {"directory": "technology_concepts"},
    "technology_system":  {"directory": "technology_systems"},
    "document":           {"directory": "documents"},
    "place":              {"directory": "places"},
    "dream":              {"directory": "dreams"}
  },
  "relationship_inverses": {
    "Member of": "Has member",
    "Has member": "Member of",
    "Reports to": "Supervises",
    "Supervises": "Reports to",
    "Advises": "Advised by",
    "Advised by": "Advises",
    "Works with": null,
    "Collaborates with": null,
    "Partners with": null,
    "Distinct from": null,
    "Attends": "Attended by",
    "Attended by": "Attends",
    "Works on": "Has contributor",
    "Has contributor": "Works on",
    "Contributes to": "Has contributor",
    "Led by": "Leads",
    "Leads": "Led by",
    "Funded by": "Funds",
    "Funds": "Funded by",
    "Part of": "Has part",
    "Has part": "Part of",
    "Uses": "Used by",
    "Used by": "Uses",
    "Develops": "Developed by",
    "Developed by": "Develops",
    "Hosts": "Hosted by",
    "Hosted by": "Hosts",
    "Mission liaison for": "Has mission liaison",
    "Has mission liaison": "Mission liaison for",
    "Mission focus": "Focus of",
    "Focus of": "Mission focus",
    "Authored by": "Authors",
    "Authors": "Authored by",
    "Cites": "Cited by",
    "Cited by": "Cites",
    "About": "Subject of",
    "Subject of": "About",
    "Sparked by": "Sparked",
    "Sparked": "Sparked by",
    "Realized as": "Realized from",
    "Realized from": "Realized as"
  },
  "source_confidence": {
    "calendar": "HIGH",
    "org_chart": "HIGH",
    "manual": "HIGH",
    "email": "MEDIUM-HIGH",
    "preprint": "MEDIUM-HIGH",
    "transcript": "MEDIUM",
    "presentation": "MEDIUM",
    "pdf": "MEDIUM",
    "document": "MEDIUM",
    "website": "MEDIUM",
    "notes": "LOW",
    "unknown": "LOW"
  },
  "derivation_types": ["meeting_summary", "transcript_summary", "web_digest", "document_summary"],
  "confidence_levels": ["HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW"],
  "temporal_classes": ["permanent", "mutable", "volatile"],
  "entity_statuses": ["Active", "Inactive", "Unknown"],
  "evidence_types": ["stated", "written", "attributed", "inferred", "hearsay"],
  "perspective_statuses": ["Active", "Evolved", "Superseded"],
  "conflict_statuses": ["UNRESOLVED", "RESOLVED"]
}
```
