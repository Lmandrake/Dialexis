# Skill: Health & Maintenance

Keeping the graph whole, coherent, reciprocal, and correctly formatted — and repairing it when it
isn't. This file consolidates what used to be three overlapping specs (`/health`, `/maintain`,
`/audit`). The task-scoped entry point is the **dialexis-architecture** skill; this is its deep
reference.

## The one distinction that organizes everything

| | **Structural health** | **Semantic quality** |
|---|---|---|
| Verifiable? | Mechanically | Needs judgment |
| Repaired by | `reconcile` (automatic, idempotent) | A manifest you author |
| Safe to gate on? | Yes — deterministic pass/fail | No — most flagged items are fine |
| Tools | `validate`, `audit_reciprocals`, `reconcile` | `audit_semantic_relationships`, `analyze_database` |

Structural issues are `reconcile`'s job and you should simply run it. Semantic findings are a
*worklist*, not a defect count — a 6,800-line semantic gap report is normal and mostly correct.
Treating the second kind like the first is the classic way to waste an afternoon.

## The tools

Timings measured 2026-08-31 at 4,654 entities. All read from the derived SQLite cache where it
applies, so they are far faster than the pre-cache figures older notes quoted.

| Tool | Asks | Speed | Repairs? | Exit codes |
|---|---|---|---|---|
| `validate` | Is the graph sound? (format, registry, placement, naming, status enum) | 0.5s | No | 0 ok / 1 violations |
| `audit_reciprocals` | Does every edge have its inverse? | 0.6s | No — `reconcile` does | 0 ok / 1 missing / 2 error |
| `reconcile` | *Converge everything.* Reciprocals, placement, format, indices, batch cleanup | 1.5s | **Yes**, idempotent | 0 ok / 1 error |
| `analyze_database` | What looks wrong? (stubs, isolates, duplicate candidates, name clashes) | 1.8s | No — read-only | 0 |
| `audit_semantic_relationships` | What's mentioned in prose but not linked as an edge? | 5.1s | No — judgment | 0 ok / 2 error |
| `audit_provenance` | How traceable, how second-hand, how bundled, how attributed? | 3s | No — trends, not invariants | 0 |
| `health_check` | All of the above as one suite + stats | 6s | No | 0 healthy / 1 issues |
| `doctor` | Can *this machine* do the work? (deps, cache, inbox, git fence) | fast | No | — |

```bash
python3 <ABS>/run.py health_check          # standard suite (semantic audit truncated to top 20)
python3 <ABS>/run.py health_check --full   # same, with the complete semantic audit
python3 <ABS>/run.py validate              # fast binding gate
python3 <ABS>/run.py validate --report     # non-fatal: show drift without failing
python3 <ABS>/run.py validate --no-cache   # full live parse, bypassing the cache (belt-and-braces)
python3 <ABS>/run.py audit_reciprocals [--verbose]
python3 <ABS>/run.py audit_semantic_relationships [--all]
python3 <ABS>/run.py analyze_database
python3 <ABS>/run.py audit_provenance [--detail]   # provenance/granularity/stance trends
python3 <ABS>/run.py reconcile             # the repair
```

## The four provenance metrics (`audit_provenance`)

Added 2026-09-12 after the extraction measurement in `docs/paper/pilot/`. These are **trends, not
invariants** — `validate` owns what must be true, this owns what should be improving. Baselines as
of 2026-09-12:

| Metric | Baseline | Direction | Why it matters |
|---|---|---|---|
| Unsourced edges (`source=unknown`) | 19,539 / 19,539 | **must fall** | Backfilled placeholder; `commit` upgrades it whenever an edge is re-asserted, so this drains through normal use |
| Second-hand facts (`derived=`) | 234 / 11,233 | watch | A disclosure, not a defect. A mostly-second-hand graph is a different artifact from a mostly-primary one |
| Bundled facts | 2,098 / 11,233 (~6,587 assertions) | should fall | One `confidence` covering several claims of differing support. **Both precision defects found on 2026-09-12 were hiding inside bundles.** Not enforced — splitting needs judgment per line, and halting would wedge every ingestion |
| Attributed stance (perspectives) | 34 on 14 / 4,742 entities | **must rise** | Attributed judgment was the largest category of content the recall measurement found *dropped*. The mechanism existed and was unused |

**`validate` vs `doctor`** — two different questions. `validate` asks whether the *graph* is sound;
`doctor` asks whether this *machine* can do the work. Reach for `doctor` when something retrieves
wrong rather than validates wrong, and always before writing a source off as blocked: a missing
optional dep degrades silently into a symptom that looks like someone else's fault.

## Cadence

| When | Run |
|---|---|
| After any mutation (part of the harness) | `reconcile` then `validate` — unconditional, never optional |
| After a `git pull` that brought in others' work | `validate` |
| After a bulk ingestion | `validate`, `audit_reciprocals`, then skim `audit_semantic_relationships` |
| Monthly | `health_check` |
| Quarterly | `audit_semantic_relationships --all > review.txt`, triage the top gaps |
| Something retrieves wrong | `doctor` |

## Repairing what you find

```
Format violations, misplacement, or missing reciprocals?
  └─ reconcile → re-run validate.  That's the whole procedure.

analyze_database flags a duplicate pair?
  ├─ Genuinely the same thing → merge_entity op
  └─ Genuinely two things (two Cordelia Wickhams) → symmetric `Distinct from` op, NOT a merge

Wrong canonical name?            → rename_entity op
A fact / edge / event / alias is simply false?  → the matching prune_* op
Semantic gap that deserves an edge?             → upsert_entity with the relationship
```

Every one of those repairs is a **manifest**, never a hand-edit — hand-editing orphans inbound
wikilinks, which is exactly what the ops exist to prevent. Then the usual tail:

```bash
python3 <ABS>/run.py commit <ABS>/batches/<id>/manifest.json
python3 <ABS>/run.py reconcile
python3 <ABS>/run.py validate
```

Field references for each op are in `agents/SKILL_ingest.md`.

### `Distinct from` — recording that a look-alike pair is not a duplicate

When two similar records are genuinely different real-world things, don't just leave the pair
un-merged; **record the ruling** as a symmetric `Distinct from` relationship plus a one-line note
giving the discriminator ("Group A passive optics — not the Group B Cordelia E Wickham").
That ruling is permanent and reciprocal: `analyze_database.find_ambiguities` drops the pair
thereafter, so the deduper is never handed it twice, and `resolve` reports the collision as
*adjudicated* — narrow by context, never merge — instead of a suspected duplicate. This replaced an
`_index/known_ambiguities.json` cache, on the principle that hand-authored judgment belongs on the
entities under the same commit → reconcile → validate discipline as every other fact.

### Reading the semantic audit

Not every mention deserves an edge. The audit is case-insensitive substring matching over
attributes, notes, and summaries — it finds *mentions*, and cannot know which ones are relational.

**Usually should be an edge:** a person named in a project's Effort allocation (`Works on`); a named
project lead (`Led by`); a stated employer (`Member of`); a manager or report (`Reports to` /
`Supervises`); a technology a project actually uses (`Uses`).

**Usually should not:** historical comparisons ("similar to project X"), illustrative examples
("like NASA's approach"), field-level context ("in climate science"), and passing narrative
mentions.

Prioritize project teams, org affiliations, and leadership — those are what make graph queries
work. Leave the rest; a gap report that never reaches zero is the expected steady state.

## Archive hygiene

```bash
python3 <ABS>/run.py prune_archive           # dry run: aged source copies in archive/raw_inbox/
python3 <ABS>/run.py prune_archive --apply   # delete them; each logged to archive/PRUNED_LOG.md
python3 <ABS>/run.py prune_archive --days 90 # custom window (default 60)
```

Retention is keyed off the filename date, not mtime. Sources are otherwise immutable: every
original stays in `archive/raw_inbox/`, so re-derivation is always available.

## Committing maintenance work to git

Maintenance mutates the model, so it takes the git fence like any other write — pull before,
commit + push after `validate` passes (see `agents/CONVENTIONS.md`, "One writer at a time").

Review with `git diff graph/` first, and **stage only the paths you actually changed.**
Never `git add -A` here: a maintenance pass runs alongside untracked batch scratch, logs, and
caches, and sweeping them in is how unrelated debris ends up in history.

## Why the guarantees hold

Ingestion *provides* guarantees; health checks *verify* them. `commit.py` writes reciprocal edges
and mechanical fields itself and halts rather than half-writing, so a graph built entirely through
the harness should pass `validate` and `audit_reciprocals` at all times. Failures therefore point
at something that bypassed the harness — which is precisely why hand-editing `graph/` is
forbidden rather than merely discouraged.

## See also

- `agents/CONVENTIONS.md` — the agent behavioral contract (paths, writers, confidence, halting).
- `agents/SKILL_ingest.md` — the harness and the complete manifest field table.
- `agents/ENTITY_TYPE_REGISTRY.md` — the closed vocabulary `validate` enforces.
- `DIALEXIS_SYSTEM.md` §7–8 — health-check design rationale and the verified reliability guarantees.
- `.claude/skills/dialexis-architecture/` — the task-scoped skill that routes here.
