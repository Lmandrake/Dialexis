---
name: "dialexis-architecture"
description: "Understand, maintain, profile, visualize, and evolve the MACHINERY of DIALEXIS — the user's personal knowledge graph at ~/dev/Dialexis. Use when the user asks how Dialexis is built or why it's reliable; wants to keep the graph healthy (health/maintain/audit, fix duplicates, reciprocity, semantic gaps); extend the registry (new entity/relationship types); profile or optimize speed/token cost/response time; run or discuss the viz/ visualizer; or plan upgrades to the system itself. For everyday lookups use dialexis-access; for ingesting new sources use dialexis-ingestion."
---

# Dialexis — Architecture, Maintenance, Performance & Evolution

Everything about the *machinery* of Dialexis: how it's built and why it's reliable, how to keep it
healthy, how to measure and speed it up, how to visualize it, and how to extend it deliberately. For
daily use, defer to dialexis-access (reads) and dialexis-ingestion (writes) — this skill is for
working *on* the system.

Two bundled references, read on demand:

- **`references/performance.md`** — profiler, current + historical measurements, shipped and
  rejected optimizations. Read before any speed/token-cost work.
- **`references/deployment.md`** — the two launchd jobs and three traps.
  Read before touching either job.

## What Dialexis is (the design in one breath)

A personal knowledge base where **markdown entity files in `graph/` ARE the database**, an
LLM is the semantic runtime, and stdlib-only Python enforces deterministic postconditions. There is
no server or app to launch — **the Claude session IS the runtime.** The `viz/` directory is a
SEPARATE React/Flask visualizer; never start it when asked to "run dialexis."

### Three layers, one guiding principle

| Layer | Who | Does what |
|-------|-----|-----------|
| DECIDE | LLM | Semantic judgments only. Output = a transaction *manifest*. |
| ENFORCE | Python (`commit.py`) | Applies the manifest atomically; writes reciprocal edges + mechanical fields. |
| DETECT & HALT | Python (`validate.py`) | Reads from disk; halts loudly on any violation. |

> **Guiding principle: never ask an LLM to guarantee a deterministic postcondition.**
> This is *why* v2.0 is reliable where v1.0 silently skipped cleanup on complex ingestions:
> finalization is moved out of the agent's hands into code it can't bypass.

### The deterministic core (`scripts/`, no third-party deps)

`registry.py` (parses the closed vocabulary), `entity_io.py` (tolerant parse / strict canonical
serialize / fact merge with contradiction detection), `naming.py` (slug rules), `commit.py` (atomic
manifest apply + reciprocals + rename/merge/reclassify/prune ops), `reconcile.py` (unconditional
convergence: reciprocal edges, placement, format, index regen, batch cleanup), `validate.py`
(binding integrity gate), `entity_matching.py` + `resolve.py` (fuzzy match + person resolution +
live same-name collision detection + `Distinct from` adjudication), the
`audit_*`/`analyze_database`/`health_check` family, `sync_index.py`/`normalize.py`/`finalize.py`,
`stage_inbox.py`, `merge_fragments.py`, `web_search.py`/`web_fetch.py`/`source_log.py`,
`system_status.py`, `prune_archive.py`, `index_store.py`, and `profile_readpath.py`.

`run.py` auto-discovers any `scripts/<name>.py` exposing a `main(argv)` — no registration needed, so
a new tool is just a new file.

Full rationale in `DIALEXIS_SYSTEM.md` (§2 layers, §5 entity format, §6 core modules, §6b
single-writer concurrency, §6c person resolution, §6d read cache, §7 health checks, §8 reliability
guarantees).

### The derived read cache

The read path is accelerated by a machine-local SQLite cache at
`~/.cache/dialexis/<repo-id>/index.sqlite` — one prebuilt, pre-parsed artifact read in a single I/O
instead of globbing and regex-parsing every markdown file per operation. It is **never a source of
truth**: the markdown files ARE the database, and every path falls back to a live parse on any
`(mtime, size)` mismatch or SQLite error, so the cache can make a read faster but never wrong.
Delete it and the next read rebuilds it (~0.5s). Details and numbers in `references/performance.md`.

## One writer at a time (why there's no lock)

Dialexis is single-writer **in time, not by designation.** One user drives one session at a time, so
two writers can never overlap — that, not a designated machine, is what makes a lock unnecessary.
The Slack relay is a second, un-scheduled writer and therefore the one real crack in that
rationale; the exposure is small but not zero. Consumers (Cowork, any session the user is not
driving) stay read-only.

What replaces the lock is **git**: serialized writers eliminate concurrency but not *staleness*, so
the fence is pull-before-mutate, commit-and-push-after. Writing on a stale checkout produces a
divergent branch that git makes you resolve explicitly — noisily and recoverably, unlike the silent
Drive conflict copies (`name (1)`) it replaced.

Corollary for you: **entities changing without your having written them is expected.** `validate` is
the arbiter of soundness, not authorship.

The canonical rules (who may write, which commands mutate, the fence discipline) are in
**`agents/CONVENTIONS.md`**, "One writer at a time"; the design rationale and the Google Drive
history that motivated it are in `DIALEXIS_SYSTEM.md` §6b.

## Maintenance — keeping the graph healthy

Two kinds of work: **structural health** (mechanically verifiable, auto-repairable by `reconcile`)
and **semantic quality** (needs LLM judgment, repaired only via manifests). `validate` is the
objective judge — if it exits nonzero, fix via manifest and re-run; don't proceed until it passes.

```bash
python3 <ABS>/run.py health_check        # full suite: validate + reciprocals + semantic + stats
python3 <ABS>/run.py analyze_database    # read-only: stubs, isolates, duplicates, name clashes
python3 <ABS>/run.py reconcile           # auto-repairs structural issues (idempotent)
python3 <ABS>/run.py validate            # binding gate
```

Repairs are manifest ops — `merge_entity` for a real duplicate, `rename_entity` for a wrong name,
`prune_*` for a false fact — then `commit → reconcile → validate`.

**Not every look-alike pair is a duplicate.** When two similar records are genuinely different
things (two same-named people at one institution in different divisions), record that ruling as a
symmetric **`Distinct from`** relationship instead of merging. `analyze_database.find_ambiguities`
then drops the pair permanently and `resolve_person` reports the collision as *adjudicated* rather
than a suspected duplicate.

**The full reference — every tool's contract and exit codes, measured timings, cadence, the repair
decision tree, how to read a semantic-gap report, and archive hygiene — is
`agents/SKILL_health.md`.** Read it for any real maintenance pass.

## Registry evolution (extending the closed vocabulary)

When the graph genuinely needs a new entity type or relationship — the sanctioned way past a
"type/relationship rejected" halt:

1. Confirm it's a true vocabulary gap, not a mis-typed existing term. Read
   `agents/ENTITY_TYPE_REGISTRY.md` (prose table + the authoritative fenced `json` block).
2. Add the term in BOTH the prose table AND the JSON — they must agree; the JSON is authoritative.
   **Relationships must be added with their inverse** (or `null` for symmetric), because `commit.py`
   writes reciprocal edges from that mapping. An added relationship without its inverse breaks the
   reciprocal invariant. The most recent addition, `Distinct from`, is the symmetric case — inverse
   `null`, so marking A distinct from B is automatically mutual.
3. Save, then re-run the manifest: `commit → reconcile → validate`.

## Visualization (`viz/` — a separate tool, never "run dialexis")

`viz/` is a React + deck.gl frontend over a Flask backend that renders the graph as a 2D semantic
map (entities positioned by embedding similarity via UMAP), with type filters, search, a Halloway-person
filter, and click-through entity detail. `backend/extract_embeddings.py` extracts entities, generates
embeddings, and computes projections into `viz/cache/`; `placement.py` does parametric placement of
incoming entities. Start it via its own `start_backend.sh` / `start_frontend.sh` (see
`viz/README.md`, `viz/QUICKSTART.md`) — only when the user explicitly wants the visualizer.

## Verifying the machinery

```bash
python3 <ABS>/tests/smoke_test.py       # exercises the core on a throwaway copy (stdlib only)
python3 -m pytest <ABS>/tests/ -q       # full suite (needs pytest; see requirements-optional.txt)
python3 <ABS>/run.py validate           # binding integrity of the live MODEL
python3 <ABS>/run.py doctor             # health of the ENVIRONMENT, not the graph
python3 <ABS>/scripts/web_fetch.py --self-test   # web retrieval capabilities
```

When changing scripts, run the smoke test and `validate` before trusting a mutation. Stage only the
files you edited, by path — never `git add -A`.

Past regressions and measured traps: `LESSONS.md` (read on demand).

## Reference files

- `references/performance.md` / `references/deployment.md` — bundled, above.
- `DIALEXIS_SYSTEM.md` — full v2.0 architecture and design rationale.
- `agents/CONVENTIONS.md` — the agent behavioral contract (paths, writers, confidence, halting).
- `agents/SKILL_health.md` — the health & maintenance reference.
- `agents/ENTITY_TYPE_REGISTRY.md` — the closed vocabulary (authoritative JSON block).
- `agents/ENTITY_FILE_FORMAT.md` — exact entity markdown format.
- `deploy/README.md` — the launchd runbook.
