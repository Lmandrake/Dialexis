# Skill: /ingest

The ingestion harness. This is the single most important behavioral guarantee in Dialexis: the
**sequence is owned here, not by any agent**, and the deterministic finalization steps run
**unconditionally** — they are not contingent on an agent remembering to do them.

This file is the orchestration contract. Follow it exactly. Do not let an agent's summary stand in
for a step that is supposed to run a script.

## Sequence

```
1. STAGE        python3 run.py stage_inbox
                -> copies inbox -> archive/raw_inbox/ (verbatim), creates batches/<id>/,
                   writes _manifest.json (status STAGED), deletes inbox originals after
                   verifying the archived copy. Capture <batch_id>.
                -> if inbox empty, stop here.
                -> ICS AUTO-PARSE: if any staged files are .ics (iCalendar), the script
                   automatically parses them and writes a seed manifest.json with
                   upsert_entity operations for ALL attendees (person entities with
                   Email identifiers + Attends relationships) and the event itself.
                   This is deterministic — every calendar invitee becomes an entity.

2. PRE-READ     [agent reads existing entities before building manifest]
                -> For each entity you plan to upsert, glob for its file under graph/
                -> Read existing attributes, identifiers, relationships, and events
                -> Purpose: avoid contradiction halts by knowing current state BEFORE emitting
                -> If an incoming attribute key already exists with a DIFFERENT value:
                   (a) MULTI-VALUED by nature (center, affiliation, org code, posting, project
                       membership): accumulate — add the edge, or use a qualified key
                       (e.g., "Role - ALPHA" vs existing "Role"). Two NASA centers on one
                       person is dual posting, not a conflict.
                   (b) AUTHORITATIVE UPDATE (org chart = current truth): emit prune_attribute
                       for the old key before the upsert (see "Org-chart auto-parse").
                   (c) SINGLE-VALUED and genuinely contested (a title, a date, a spelling):
                       emit an add_conflict op alongside the upsert.
                   (d) skip the attribute if the existing value is already correct/more precise.
                -> This step is REQUIRED, not optional. Skipping it is the #1 cause of
                   avoidable commit halts.

3. MANIFEST     [agent reads source documents, emits manifest]
                -> produces batches/<batch_id>/manifest.json with upsert_entity operations
                -> agent performs ZERO file operations on graph/.
                -> CRITICAL: every source must produce at least one operation.
                -> ICS NOTE: if stage_inbox already wrote a seed manifest.json (from .ics
                   files), READ it first and APPEND your operations. Do not overwrite.
                   The seed contains attendee stubs — you may enrich them (add attributes,
                   fix canonical names to match existing entities, add richer summaries)
                   but do not remove attendees. Entity resolution (matching "Dunstan E
                   Waterhouse" to existing "Dunstan Waterhouse") is your job here.

4. COMMIT       python3 run.py commit batches/<batch_id>/manifest.json
                -> applies the manifest atomically: validates against registry, writes entities,
                   writes reciprocal edges, sets mechanical fields. HALTS (nonzero) on any
                   violation with nothing written.
                -> SAFETY CHECK: if the manifest contains ZERO operations, the commit will
                   HALT with a prominent RED error (extraction failure).
                -> if HALT: stop, surface the message, route the cause to clarification/resolution.
                   Do NOT hand-fix files. Fix the manifest or extend the registry, then re-run.

5. RECONCILE    python3 run.py reconcile
                -> UNCONDITIONAL. Converges the whole working model to invariants: reciprocal
                   edges, canonical placement, normalized format, regenerated indices.
                   Also auto-cleans the batch directory (source files remain in archive/).
                   Idempotent — safe even if step 4 already left things consistent.

6. VALIDATE     python3 run.py validate
                -> BINDING gate, read from disk. The agent does not get to self-certify.
                   Nonzero exit = the batch is NOT done; investigate before declaring success.
```

**Note:** There is no separate "finalize" step. Reconcile auto-cleans committed batches whose
entities all exist in graph/. The `finalize` script exists as an optional manual tool
but is not required in the standard flow.

## Org-chart auto-parse (org-chart PDFs)

`stage_inbox` auto-detects a org-chart PDF (`js.halloway.example_orgs__uid=…`) and, like the .ics
path, writes a **seed `manifest.json`** of `upsert_entity` ops — one per org unit (directorate /
division / section / group) and one per person, wiring `Part of` / `Member of` edges and a
`Org Code` attribute. The parser handles: hyphenated / lowercase-particle names
("Odile-Rue de la Mer", "Wren Ostrand da Lune"); a person listed under **two groups**
(folded into one op with a comma-joined org code and both membership edges); and **stacked STAFF
blocks** (the first STAFF block after a group is section staff, a second consecutive block is the
parent-division staff). Read the seed and APPEND — do not overwrite.

Two things the parser cannot do for you — handle them in PRE-READ:

1. **Org-unit name collisions / duplicates.** The parser names units by bare code
   (`Halloway Section 4120`), but the KB may already hold the same unit under a longer canonical
   name (`Halloway Section 4120 Coastal Optics`) from a prior ingest. Committing the bare name
   would create a **duplicate**. Before committing, glob `graph/organizations/` for the
   code; if a longer-named entity exists, rewrite the seed op's `canonical_name` (and every edge
   `target_name` that points at it) to the existing name, or fold with `merge_entity` afterward.
2. **Authoritative attribute updates.** An org chart is "current and correct": a person who moved
   groups will have a **new** `Org Code` that *contradicts* the stored one and HALTs the commit.
   When the chart is the newer truth, emit a `prune_attribute` (key `Org Code`) for that person
   **before** their upsert so the new value applies cleanly — do not `add_conflict` (that records a
   disagreement rather than an update). Note `prune_attribute` resolves by exact canonical name, so
   target the person's *existing* file name (e.g. `Elspeth Tavistock`), not the chart's fuller form.

## The artifact you are given IS the scope — do not go looking for a fuller one

**Corrected 2026-09-14. An earlier version of this section told you to prefer the raw transcript
over its summary. That was wrong, and it inverted the owner's intent.**

**Choosing what to feed Dialexis is the owner's scope decision, and it is the product, not a loss.**
When a summary is handed over, its boundary *is* the specification: the owner wants the graph to
know what is in the summary. Reaching past it to the transcript silently widens the scope of the
knowledge base beyond what was asked for.

So:

1. **Ingest the artifact you were given.** Do not substitute a longer or "more original" version
   you found elsewhere on disk because it looks more complete. If you think a fuller source exists
   and would be wanted, **say so and ask** — never swap the input unilaterally.
2. **`derived=` is a provenance disclosure, not a demotion.** Record `source=transcript,
   derived=meeting_summary` because it is *true* and keeps the derivation chain visible. It is not
   a signal that the wrong artifact was used.
3. **A digest the project wrote itself is still never a source.** `archive/web_references/` holds
   Dialexis's own extracted-fact files; ingesting from those is circular, and they remain a
   declared NON-INPUT. This is unaffected by the correction above — it is about circularity, not
   about scope.

**What survives from the retracted rule, in weaker form.** A summary can be *wrong*, not merely
brief: a polarity inversion introduced upstream — advice recorded as its own opposite — was
reproduced verbatim by an independent extraction (`docs/paper/pilot/REMEASUREMENT.md` §10). That is
a defect in the summarizing step, **not** a reason to change the input. The consequence for you is
narrow: when a summary states something evaluative, attributed, or surprising, flag it as worth a
spot-check rather than silently promoting it to `confidence=HIGH`.

**Do not cite the "26–36 points of recall" figure as a reason to prefer transcripts.** It scored
entities against facts enumerated from the *transcript* — an artifact the system was never given —
so it measures fidelity to a target nobody was aiming at. See `docs/paper/HISTORY.md`, "The owner's
definition of valid capture".

## Entity resolution — upsert the RIGHT entity

Before creating a person/org, resolve against what already exists (this prevents duplicates and
mis-merges): `python3 <ABS>/run.py resolve "<name>" [--org X --email a@b --related project:Y --co-mention "Z"]`.
Emails are case-insensitive keys and the most reliable person disambiguator.

- Middle-initial variants ("Hollis Marlowe" vs "Hollis J Marlowe") are ONE identity — merge, don't
  fork. Only treat as ambiguous if a KNOWN same-name person with a *different* identity exists that
  context can't separate (rare at Halloway, but real).
- An under-specified reference ("Odalys in CMG") resolves to the unique match given the context in
  the same source.
- If a genuine same-name collision survives all available context, add the fact to **both**
  candidates with **LOW** confidence and a note that it **requires disambiguation** — do not guess.
- When you confirm two same-named people are genuinely DIFFERENT, commit a symmetric `Distinct from`
  relationship between them (plus a one-line Note giving the discriminator, e.g. "Section 100
  passive microwave — not the Group B Cordelia E Wickham"). That ruling is permanent and reciprocal,
  stops the deduper re-proposing the pair, and makes future resolves report settled ambiguity.
  Collisions are detected live — there is no cache to refresh.

## Optional FAN-OUT PATH (multi-source batches)

When a staged batch contains **≥3 source documents**, steps 2–3 (PRE-READ + MANIFEST) can be
parallelized across `dialexis-extractor` subagents instead of done inline. This keeps each source's
raw text out of your context and runs the semantic extraction concurrently. The deterministic tail
is unchanged.

```
2–3. FAN-OUT   Spawn one dialexis-extractor per source IN PARALLEL (one Agent call per source, all
               in a single message). Each returns a ```json fragment. Write each fragment verbatim
               to batches/<batch_id>/fragments/<source_slug>.json — YOU do the writing; extractors
               are read-only and never touch graph/.

3b. MERGE      python3 run.py merge_fragments batches/<batch_id>/fragments \
                   --out batches/<batch_id>/manifest.json
               -> folds per-entity upserts, dedups edges, and auto-emits add_conflict for any
                  cross-source attribute disagreement. Read its report before committing.
```

Then continue with step 4 (COMMIT) exactly as below. For 1–2 sources, do NOT fan out — inline
extraction is simpler and cheaper. The full contract, the other three subagents (researcher,
triager, deduper), and the safety rationale live in `agents/SKILL_subagents.md`.

## Why this shape

The recurring old failure was an agent skipping cleanup/finalization on complex batches —
because "finalize at the end" was a *prior expressed in a spec*, not an enforced step. Here,
steps 1, 4, 5, 6 are deterministic scripts the harness runs every time. The agent (steps 2–3)
is confined to reading and semantic work and cannot leave the store half-written, because it no
longer does the writing. A correct halt at step 4 or a failure at step 6 leaves state clean and
tells you exactly what to fix.

Reconcile.py (step 5) contains self-healing cleanup: it verifies whether a batch's entities all
exist in graph/ and deletes batches that are fully integrated. This guarantees no stale
batches accumulate regardless of agent behavior.

## Operator notes
- Never substitute an agent's "I cleaned up and validated" for actually running steps 5 and 6.
- A HALT is a successful outcome of the safety design, not an error to work around by editing
  files by hand.
- Re-running the whole sequence after fixing a manifest is cheap and safe (idempotent reconcile,
  binding validate).
- **Authorized writers only, and take the git fence:** ingestion mutates `graph/`, so run
  it only from a user-driven session or the Slack relay — pull before, commit + push after.
  Cowork and other readers never ingest. See `agents/CONVENTIONS.md` "One writer at a time."

---

## Quick Reference: Manifest Field Names

Commit.py **rejects** incorrect field names. Use these exact keys:

| Section | Field | NOT this | Notes |
|---------|-------|----------|-------|
| manifest (top-level) | `unresolved` | — | Optional list of items you could not place safely (each a string or object with a reason). **Non-empty HALTs the whole commit, nothing written**, printing each item — resolve them and empty the list |
| operation (top-level) | `op` | — | `upsert_entity`, `add_conflict`, `resolve_conflict`, `rename_entity`, `merge_entity`, `reclassify_entity`, `prune_attribute`, `prune_alias`, `prune_relationship`, `prune_event`, `prune_perspective`, or `prune_note` |
| operation (top-level) | `entity_type` | — | Must exist in registry |
| operation (top-level) | `canonical_name` | ~~entity_name~~ | The entity's canonical display name |
| events | `desc` | ~~description~~ | Short text of what happened |
| events | `date` | — | ISO 8601 or `unknown` |
| events | `confidence` | — | Optional (defaults from source type) |
| events | `source` | — | Source type (pdf, email, calendar, preprint, website, etc.) |
| sources | `name` | ~~description~~ | Human-readable title |
| sources | `archive` | — | Path to archived file (**required**) |
| attributes | `key` | ~~type~~ | The attribute name |
| attributes | `value` | ~~description~~ | The attribute value |
| identifiers | `key` | ~~type~~ | The identifier type (Email, Phone, etc.) |
| identifiers | `value` | ~~description~~ | The identifier value |
| relationships | `rel_type` | ~~type~~ | Must exist in registry |
| relationships | `target_type` | — | Entity type of target |
| relationships | `target_name` | ~~target~~ | Canonical name of target |
| relationships | `source` | — | **REQUIRED.** Source type that asserted the edge, or `unknown` if it genuinely cannot be established. Omitting it HALTS — an edge with no provenance cannot be audited, and 19,539 accumulated while this was optional |
| attributes / identifiers / relationships / events | `derived` | — | Optional. `source` names the original *medium*, `derived` names the artifact actually *read* (`meeting_summary`, `transcript_summary`, `web_digest`, `document_summary`). Absent = read the original. Confidence is discounted one step automatically; do not discount it yourself |
| upsert_entity | `summary` | — | **REPLACES** the stored summary outright — it is not merged and there is no contradiction guard, unlike an attribute. Safe under the single-writer doctrine; a silent-loss path if that is ever violated. `notes` appends instead |
| perspectives | `title` | — | **REQUIRED**, and the dedupe key — an "Untitled" perspective silently swallows the next one |
| perspectives | `holder` | — | **REQUIRED unless `evidence_type` is `inferred`.** Whose view it is. An unattributed stance reads as a claim about the world |
| perspectives | (optional) | — | `summary`, `claim`, `quote`, `context`, `text`, `status`, `evidence_type`, `confidence`, `source`, `date` |
| add_conflict | `title` | ~~description~~ | Short heading for the conflict |
| add_conflict | `value_a` | — | First contradicting value |
| add_conflict | `value_b` | — | Second contradicting value |
| rename_entity | `entity_type` | — | Type of the entity being renamed (unchanged by the op) |
| rename_entity | `old_name` | ~~canonical_name~~ | Current canonical name (its file must exist) |
| rename_entity | `new_name` | — | New canonical name (its file must NOT already exist) |
| merge_entity | `entity_type` | — | Type shared by both entities |
| merge_entity | `keep_name` | — | Canonical name of the keeper (authoritative on collisions) |
| merge_entity | `merge_name` | — | Canonical name of the duplicate to fold in and delete |
| reclassify_entity | `from_type` | ~~entity_type~~ | Current entity type (must differ from `to_type`; same type = rename/merge, and HALTS) |
| reclassify_entity | `from_name` | ~~canonical_name~~ | Current canonical name (its file must exist) |
| reclassify_entity | `to_type` | — | Destination entity type |
| reclassify_entity | `to_name` | — | Optional; defaults to `from_name`. If `(to_type, to_name)` already exists this becomes a cross-type MERGE with the destination as keeper |
| prune_attribute | `entity_type` | — | **Required.** Type of the entity whose fact(s) to remove |
| prune_attribute | `canonical_name` | — | Entity whose fact(s) to remove (its file must exist) |
| prune_attribute | `section` | — | `attributes` (default) or `identifiers` |
| prune_attribute | `keys` | — | Non-empty list of exact keys to delete; HALTS if any key is absent |
| prune_alias | `entity_type` | — | Type of the entity whose alias(es) to remove |
| prune_alias | `canonical_name` | — | Entity whose alias(es) to remove (its file must exist) |
| prune_alias | `aliases` | — | Non-empty list of exact alias strings to delete; HALTS if any alias is absent |
| prune_relationship | `entity_type` | — | Type of the source entity carrying the edge |
| prune_relationship | `canonical_name` | — | Source entity (its file must exist) |
| prune_relationship | `rel_type` | — | Relationship to remove; matched on `(rel_type, target_stem)` |
| prune_relationship | `target_type` | — | Entity type of the edge target |
| prune_relationship | `target_name` | — | Canonical name of the edge target; HALTS if the edge is absent. Reciprocal removed too |
| prune_event | `entity_type` | — | Type of the entity whose event(s) to remove |
| prune_event | `canonical_name` | — | Entity (its file must exist) |
| prune_event | `events` | — | Non-empty list of `{date, desc}` match specs; matched exactly, HALTS if any spec matches no event |
| prune_perspective | `entity_type`, `canonical_name` | — | Entity (its file must exist) |
| prune_perspective | `titles` / `match` | — | Exact titles and/or `{field: exact value}` specs (e.g. `{"title": "Untitled", "claim": "..."}` for duplicated untitled ones); each must match exactly one perspective, else HALT |
| prune_note | `entity_type`, `canonical_name` | — | Entity (its file must exist) |
| prune_note | `contains` | — | Exact substrings; each removes the one Notes paragraph/bullet containing it; HALTS on no match or >1 match |

### Perspectives: holder vs `evidence_type: "inferred"`

A perspective needs `title` plus either `holder` (whose view it is) **or** `evidence_type: "inferred"`
to mark it as your own reading. Evidence types: `stated`, `written`, `attributed`, `inferred`, `hearsay`;
status: `Active` / `Evolved` / `Superseded`.

**Use perspectives — this is the most under-used part of the model.** A 2026-09-12 measurement
against independently enumerated ground truth found **attributed stance and judgment was the
largest single category of content Dialexis dropped**, while perspectives were carried by 14 of
4,742 entities. Attributes hold what *is*; perspectives hold what someone *thinks*, and a
conversation is mostly the latter.

So when a source says someone believes, fears, doubts, prefers, or predicts something, that is a
perspective — not a discarded aside and not an attribute. "X holds role Y" is an attribute;
"X thinks the schedule will slip, said 2026-09-02" is a perspective with `holder: X`. Recording it
as a bare attribute strips the attribution and turns a judgment into a claim about the world,
which is the failure the `holder` requirement now blocks.

### Operation order matters

Operations apply **in manifest order**, against the state left by the ops before them. Two
consequences worth internalizing:

- **After a `rename_entity` or `reclassify_entity`, later ops must address the entity by its
  NEW name/type.** A `prune_relationship` on `{"entity_type": "project", "canonical_name":
  "HELM"}` placed *after* a reclassify to `technology_concept` will not find it.
- **Either order works around a reclassify** (dirtying the source first is fine), but the
  clearer reading is usually: structural ops (`rename`/`reclassify`/`merge`) first, then
  `prune_*`, then `upsert_entity` to add the new state.

### Minimal valid examples

```json
"events": [{"date": "2026-06-29", "desc": "Presented at conference", "confidence": "HIGH", "source": "pdf"}]
"sources": [{"name": "Meeting notes (2026-06-29)", "archive": "archive/raw_inbox/20260629_notes.pdf"}]
"attributes": [{"key": "Role", "value": "PI", "confidence": "HIGH", "source": "email", "date": "2026-06-29"}]
"relationships": [{"rel_type": "Member of", "target_type": "organization", "target_name": "NASA", "source": "email", "confidence": "HIGH", "date": "2026-06-29"}]
```

### Renaming an entity

Use a dedicated `rename_entity` op — never rename by editing files or by upserting under a new
name (that leaves a duplicate and orphans every inbound wikilink). The op moves the entity file,
updates its heading/slug, and repoints **every** inbound reference across the KB to the new stem,
preserving reciprocity. It **refuses** (HALT) if the target name already exists — that case is a
*merge*, not a rename.

```json
{"op": "rename_entity", "entity_type": "technology_system",
 "old_name": "Acme Planning Guide (APG)", "new_name": "Acme Price Tool (APT)"}
```

### Merging duplicates

When two entity files describe the same real-world thing, fold them with `merge_entity` — never
by hand-editing (which does not repoint inbound wikilinks). Choose which record is canonical: `keep_name`
is authoritative, so on any attribute/identifier **key collision the keeper's value wins** and the
duplicate's is dropped. Everything non-colliding is folded into the keeper (the duplicate's name
and aliases become aliases; new relationships, events, perspectives, conflicts, sources, and notes
are appended). Every inbound reference to the duplicate is repointed to the keeper, any resulting
keep↔merge self-loop is dropped, and the duplicate file is deleted. Both entities must exist.

```json
{"op": "merge_entity", "entity_type": "project",
 "keep_name": "ALPHA", "merge_name": "ALPHA Emulation Framework"}
```

### Removing a relationship or an event

`upsert_entity` only ever *adds* facts — it can never retract one. When a relationship or an
event is simply **wrong** (not contradicted by a newer value, just false), use the dedicated
prune ops. Both are typo-guarded: they HALT if the thing you asked to remove is not actually
present, so a prune can never silently no-op.

`prune_relationship` removes one edge, matched on `(rel_type, target_stem)`, **and** its
reciprocal on the target — so you never leave a one-sided edge for reconcile to resurrect.
Pre-read the entity to confirm the edge exists first.

```json
{"op": "prune_relationship", "entity_type": "person", "canonical_name": "Hollis Oakenshaw",
 "rel_type": "Member of", "target_type": "organization", "target_name": "Halloway Institute", "source": "org_chart"}
```

`prune_event` removes events matched **exactly** on both `date` and `desc` (the same identity
key upsert uses to dedup). Pre-read the entity and copy the `date`/`desc` verbatim — a mismatch
HALTs rather than removing the wrong line.

```json
{"op": "prune_event", "entity_type": "person", "canonical_name": "Fay Lin",
 "events": [{"date": "2025", "desc": "Stale claim to remove"}]}
```

### Removing an alias

`upsert_entity` only ever *appends* aliases — it can never retract one. When an alias is
corrupted or stale (the classic case: a `merge_entity` folded a duplicate whose own alias list
was junk, and those junk aliases are now on the keeper), use `prune_alias`. Like the other prune
ops it is typo-guarded: it matches each alias **exactly** and HALTs if any requested alias is not
present, so the removal can never silently no-op. Pre-read the entity's `Aliases` line and copy
the strings to remove verbatim. This is the only sanctioned way to delete an alias — never
hand-edit the `Aliases` metadata line in `graph/`.

```json
{"op": "prune_alias", "entity_type": "project", "canonical_name": "Foundation Model Evaluation",
 "aliases": ["G", "e", "o", "F", "M", "s", "p", "a", "t", "i", "l", "u", "n", "d", "k"]}
```

### Op order and person-name resolution

Person names are resolved against disk for every op up to the first identity-changing op
(`merge_entity`, `rename_entity`, `reclassify_entity`, `prune_alias`, `prune_attribute`); upserts
after it are resolved just before they apply, against the post-op state. So merge-or-prune-then-upsert
works in one manifest — put the identity op FIRST. To retitle a perspective, `prune_perspective`
it and re-add it in the same manifest with identical fields and a new title.
