---
name: "dialexis-access"
description: "Look up people, acronyms, technologies, organizations and programs in a DIALEXIS knowledge graph. Use it FIRST, before mail search, web search or asking the user, whenever a task names a person in a work context or a bare first name, an acronym, a technology, an organization, project or program. Read-only: to add information use dialexis-ingestion."
---

# Dialexis — Access & Lookup (read-only)

Query the user's personal knowledge graph fast, and never mutate it. Dialexis is a
knowledge base where **markdown entity files in `graph/` ARE the database**.
Reading those files (directly or via the lookup scripts) is always safe. This skill covers
*reading*; it deliberately does no writes. Writes come only from an authorized Dialexis
writer — a user-driven session, or the Slack relay. When *this* skill is what fired,
you are on the read path: do not mutate, whatever runtime you are in.

## When to use — check Dialexis FIRST

Dialexis is where the user's professional world is already written down: colleagues, program
managers, funders and collaborators; the acronyms of his career (ACME, GAMMA, org
codes, awards, missions); technologies and systems (BETA); and the organizations,
projects and programs that connect them. Records carry roles, relationships and framing that a
mail scan or web search only reconstructs slowly and less accurately.

So run `run.py entity "<name>"` **before** searching mail, searching the web, or asking the user,
whenever a task touches any of those, **including when the name is incidental** to a bigger job:
drafting an email to a colleague, writing a position statement that mentions a technology, building a quad
chart for ACME, prepping for a meeting with a program manager. A first name alone is enough of a
trigger; `resolve` narrows it. Fall back to mail or the web only for what Dialexis does not hold,
and say which facts came from where.

Why this matters: on 2026-10-07 an agent resolved two people by scanning mail and researched
two more and a program framing from the web, while
Dialexis already held rich records for all of them. The lookup would have taken under a second.

> If the request is to **add, ingest, or correct** information, stop and use
> **dialexis-ingestion** — mutations go through a manifest, never a hand-edit.

## Environment / paths

- Invoke `run.py` by absolute path — `python3 <ABS>/run.py …` — from any
  directory; never leave a persistent `cd`. Details in `agents/CONVENTIONS.md`
  ("Environment and paths").
- **In Cowork the repo is not at that path.** It appears under a session mount like
  `/sessions/<session>/mnt/Dialexis`; resolve it once with `ls /sessions/*/mnt/Dialexis` and use
  what you find. In Claude Code there is no such mount — don't go looking for one.
- Read-only commands (`entity`, `resolve`, `validate`, `analyze_database`, `audit_*`,
  `system_status`) never mutate anything and never wait — lookups are always safe from anywhere,
  with no fence and no lock.

## Capability 1 — Fast entity lookup (the default path)

**Routing rule.** If the user's message begins (case-insensitive) with `lookup`,
`look up`, or `tell me about` followed by a name, do NOT grep/glob/explore. Run the
zero-LLM lookup in a **single** bash call and relay its output verbatim:

```bash
python3 <ABS>/run.py entity "<the remainder of the message>"
```

- A **dominant** match prints that entity's full markdown record — relay it.
- An **ambiguous** query prints a ranked candidate list — present the list and let the
  user pick or re-run with a more exact name. Do not silently pick one.

This is deterministic, stdlib-only, and should take <1s. It fuzzy-matches across all
canonical names + aliases with substring/token boosting. It is also middle-initial
aware: "Hollis Marlowe" resolves to the single stored "Hollis J Marlowe" record rather
than showing it as a mere candidate, unless a genuine same-name collision exists.

Any other phrasing ("who's leading X", "what do we know about Y") is a normal question
— answer it the usual way (lookup + reasoning), not the strict verbatim relay.

## Capability 2 — Resolve an under-specified or same-named person

> **`resolve` searches PEOPLE ONLY.** For an organization, project, system, or anything
> whose type you are unsure of, use `run.py entity "<name>"` (Capability 1), which searches
> every type. On a person miss, `resolve` now cross-checks the other types and prints any
> non-person match — heed that section: a bare "No person resolved" was previously misread
> as "not in the model", which produced a duplicate organization and a commit HALT.

When a person is referenced incompletely (first OR last name only) or by a name that
several people share, use the deterministic resolver. It applies middle-initial-
insensitive identity and narrows by any context you can supply:

```bash
python3 <ABS>/run.py resolve "<query>"
python3 <ABS>/run.py resolve "Odalys" --org CMG                    # narrow by organization
python3 <ABS>/run.py resolve "<name>" --email x@y.example            # narrow by email (case-insensitive)
python3 <ABS>/run.py resolve "<name>" --related project:GAMMA   # narrow by a related entity
python3 <ABS>/run.py resolve "<name>" --co-mention "Other Name"  # narrow by co-occurrence
```

Behavior:
- Exactly one candidate → resolves cleanly (e.g. "Odalys in CMG" → Odalys Ferncastle,
  the only Odalys in that group; also resolves if she's the only Odalys in the DB).
- Middle-initial variants of the same person are treated as one identity, not a clash.
- A genuine same-name collision that context cannot break is reported as
  **requires disambiguation** with the tied candidates — surface them; don't guess.

Same-name collisions are detected live on every resolve (~60ms), so there is no cache to
refresh. If the tied candidates carry a `Distinct from` edge, the resolver says the
ambiguity is **ADJUDICATED** — two real people, already ruled distinct: narrow by context
and never merge them. Recording a new such ruling is a mutation (a `Distinct from`
relationship via manifest) and belongs to dialexis-ingestion, not here.

## Capability 3 — Broader browsing & questions

For questions the fast path doesn't cover:

- Read the generated indices under `graph/_index/` — `entities.md` (full
  catalog), `agenda.md` (today/upcoming), `conflicts.md`, `perspectives.md`.
- Grep/Glob under `graph/<type>/`, or Read individual entity files directly.

Entity types → directories: `person`→people, `organization`→organizations,
`project`→projects, `program`→programs, `event`→events,
`technology_concept`→technology_concepts, `technology_system`→technology_systems,
`document`→documents, `place`→places, `dream`→dreams.

Each entity file has these sections: Metadata, Summary, Identifiers, Attributes,
Relationships (wiki-links `[[dir/stem|Name]]`), Perspectives, Events and History,
Conflicts, Notes, Sources. Relationships are the graph edges — follow them to traverse.

Two reading rules that have each produced a wrong answer:

- **"Who was involved" ≠ who attended.** Also traverse topic edges (`About`, `Works on`) one hop
  from the event: a person reached through the meeting's project or topic was involved too.
- **Two NASA centers on one person is dual posting, not a conflict.** Multi-valued fields (center,
  affiliation, org code, posting) accumulate; only single-valued facts (a title, a date) contest.

## Capability 4 — Orient / status (read-only)

```bash
python3 <ABS>/run.py system_status          # entity counts, inbox files, staged batches, agenda
python3 <ABS>/run.py system_status tree     # directory-tree view
```

Use this at the start of any Dialexis session to report state.

## Capability 5 — Record bulk consumption, so success leaves a trace

**Dialexis's own git history can only see it failing.** Read-only lookups never commit, so the
repo records misses and repairs and nothing else. Every measured success this project has
exists because some downstream agent happened to count what it got and mention it in its own
commit message. That was luck. Make it deliberate.

`entity` and `resolve` now log every lookup automatically — SERVED, DECLINED (ambiguous, no
guess) or MISS — to a gitignored `.access-log.jsonl`. You need do nothing for those.

**What needs you: consumption that did NOT go through those commands.** If you read entity
files directly, cross-reference a roster, or hand graph knowledge to another system or into a
document, record the episode before you finish:

```bash
python3 <ABS>/run.py access_log --external "<what consumed it>" \
    --served N --declined N --absent N --note "<one line>"
python3 <ABS>/run.py access_log --rollup     # refresh the tracked report
```

- `--served` — items the graph answered.
- `--declined` — items it held ambiguous rather than guessing. **This is a success**, not a
  failure: returning the wrong person's role would be a confident falsehood. Count it.
- `--absent` — items simply not in the graph. Do **not** editorialise these as defects. An
  absence usually means the owner never fed that source in, which is a scoping decision, and
  only he can tell a deliberate exclusion from an unintended gap.

The roll-up at `docs/paper/ACCESS_METRICS.md` is tracked and carries counts only, never query
text. `--rollup` regenerates it; never hand-edit it.

**Still a read-path capability** — the log and the report are instrumentation, not graph
mutations. No manifest, no `commit`, no git fence.

## Performance note (why the scripts, not a grep sweep)

The scripts answer from a derived SQLite read cache rather than parsing every entity file, so a
lookup costs ~0.3s end to end instead of the ~5s a full sweep used to take. The cache is
machine-local, purely derived, and gated on each file's `(mtime, size)` — it can only make a read
faster, never wrong.

That speed is the smaller reason to prefer them. A manual grep-and-read sweep is also *less
accurate*: it misses aliases, middle-initial identity, and contextual narrowing, all of which the
scripts handle. Use the scripts.

## Success criteria

You answered from the stored model using the fast path where applicable, relayed
ambiguous results as candidate lists rather than guessing, and made **zero** writes to
`graph/`. If the task actually needs a write, hand off to dialexis-ingestion.
