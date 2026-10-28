# Entity File Format Specification

## Overview

Dialexis entity files use a specific markdown format. **DO NOT use YAML frontmatter** (the `---` delimited headers). The format is plain markdown with a specific section structure.

## Required Structure

This is the exact format produced by `entity_io.serialize()`:

```markdown
# Entity Name

## Metadata
- Type: <entity_type>
- Status: <status>
- Aliases: <comma-separated aliases>
- Last updated: YYYY-MM-DD
## Summary
<Brief summary paragraph>
## Identifiers
- Key: Value [confidence=HIGH, source=pdf, date=2026]

## Attributes
- Key: Value [confidence=HIGH, source=pdf, date=2026]

## Relationships
- Rel type: [[directory/file_stem|Display Name]] [confidence=HIGH, date=2026]

## Perspectives
(none)

## Events and History
- YYYY-MM-DD: Description of event [confidence=HIGH, source=pdf]

## Conflicts
(none)

## Notes
(none)

## Sources
- Source document title (archive=archive/raw_inbox/file.pdf)
```

## Detailed Format Rules

### 1. Entity Name (Heading 1)
- First line of file
- Format: `# Entity Name`
- Use the canonical name

### 2. Metadata Section
- Format: `## Metadata` (heading 2)
- Required fields as bulleted list:
  ```markdown
  - Type: event
  - Status: Active
  - Aliases: Alias One, Alias Two
  - Last updated: 2026-06-22
  ```
- Aliases line is omitted if empty (not "None")

### 3. Summary Section
- Format: `## Summary` (heading 2)
- Free-form paragraph text
- No bullets or lists

### 4. Data Sections
Each of these is a `## Heading` (no horizontal rules between them):
- **Identifiers**: Key-value pairs with metadata in brackets. Email values are
  case-insensitive: capitalization in an email is never significant, so email
  identifiers (any key containing "email") are canonicalized to lowercase on
  parse, merge, and write. Two emails that differ only in case are the same
  identifier and never raise a conflict.
- **Attributes**: Fact records with confidence/source/date in brackets
- **Relationships**: Wiki-style links to other entities
- **Perspectives**: Viewpoint-dependent information
- **Events and History**: Timeline entries
- **Conflicts**: Contradictions requiring resolution
- **Notes**: Additional context (free text)
- **Sources**: Archive references

Format for facts (Identifiers, Attributes):
```markdown
- Key: Value [confidence=HIGH, source=pdf, date=2026-01-15]
```

Format for relationships:
```markdown
- Relationship type: [[directory/file_stem|Display Name]] [confidence=HIGH, date=2026]
```

Format for events:
```markdown
- 2026-01-15: Description of what happened [confidence=HIGH, source=pdf]
```

Format for sources:
```markdown
- Document title or description (archive=archive/raw_inbox/20260101_file.pdf)
```

If a section is empty, use `(none)` (or free text for Notes).

### 5. Sources Section
- Format: `## Sources` (heading 2)
- Each source has both a name and an archive path
- The archive path points to the immutable copy in `archive/raw_inbox/`

## Common Mistakes to Avoid

❌ **WRONG - YAML Frontmatter:**
```markdown
---
entity_type: Event
event_name: Meeting
date: 2026-06-23
---
```

✅ **CORRECT - Dialexis Format:**
```markdown
# Meeting Name

## Metadata
- Type: event
- Status: Active
- Aliases: None
- Last updated: 2026-06-23
```

❌ **WRONG - Missing Metadata Dashes:**
```markdown
## Metadata
Type: event
Status: Active
```

✅ **CORRECT - Bulleted Metadata:**
```markdown
## Metadata
- Type: event
- Status: Active
```

## Why This Format?

This format is parsed by `entity_io.py` which expects:
1. First heading (`# ...`) as canonical name
2. Section headings (`## ...`) to demarcate sections
3. Metadata as bulleted key-value pairs
4. Specific section names (case-sensitive)

The parser is tolerant of legacy formats but the serializer always outputs this canonical format.

## For Agents

When creating entity files:
1. Use this format EXACTLY
2. Do NOT use YAML frontmatter
3. Do NOT invent new section names
4. Follow the manifest-based workflow (don't write entity files directly)
5. When in doubt, look at existing entity files in graph/

The commit script (`commit.py`) reads manifests and writes entity files in this format automatically. Agents should emit manifests, not entity files.

---

## Document Subtype Guidance

The `document` entity type covers a broad range of citable/referenceable artifacts. Use the
following attribute patterns depending on the subtype:

### Publications (papers, preprints, decks, conference talks/videos)

Suggested attributes:
- **Document type**: paper, preprint, presentation, conference_video, technical_report
- **Authors**: Comma-separated author list (also create `Authored by` relationships to person entities)
- **Venue**: Journal name, conference name, or platform (e.g., "arXiv", "Nature", "AGU Fall Meeting")
- **Date published**: ISO date of publication/posting
- **DOI**: Digital Object Identifier if available
- **arXiv ID**: e.g., "2606.02610v2"
- **URL**: Canonical link to the publication
- **Abstract**: One-paragraph abstract or elevator pitch
- **Keywords**: Comma-separated topic keywords
- **Funding**: Grant or program that supported the work

Use **Notes** for an extended content summary: key findings, methods, results, and relevance
to the user's work. This goes beyond what fits in structured attributes.

Use **Sources** to point to the archived copy (e.g., `archive/raw_inbox/20260701_paper.pdf`).

### Websites (root-level domains, not one entity per page)

Suggested attributes:
- **Document type**: website
- **URL**: Root URL of the site (e.g., "https://nasa.github.io/VITALS/")
- **Owner**: Organization or person who maintains it
- **Purpose**: One-line description of what the site provides
- **Last accessed**: Date the site was last fetched/reviewed
- **Technology stack**: If relevant (e.g., "React, Python Flask")

Use **Notes** for a summary of key pages/sections and their content — roll up the entire
site's information into one entity rather than creating separate entities per page.

### Internal Documents (SOWs, specs, memos)

Suggested attributes:
- **Document type**: sow, specification, memo, policy
- **Authors**: Who wrote it
- **Date**: When it was produced
- **Version**: If versioned
- **Status**: Draft, Final, Superseded

Use **Notes** for a summary of key content and decisions captured in the document.
