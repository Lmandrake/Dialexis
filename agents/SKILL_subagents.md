# Skill: Subagent Fan-Out

Parallelizing the DECIDE layer with Claude Code subagents — without weakening a single Dialexis
guarantee.

## The invariant (read this first)

Dialexis is reliable because the LLM only **DECIDEs** (emits manifests) and Python **ENFORCEs** and
**DETECTs** (applies atomically, halts loudly). Subagents live **entirely inside the DECIDE layer.**
The rule that keeps fan-out safe:

> A subagent NEVER writes to `graph/` and NEVER writes `manifest.json`. It **returns a
> fragment** — a JSON `{"operations": [...]}` object — as the last fenced block of its reply. The
> orchestrator (you, the main session) writes fragments to `batches/<id>/fragments/`, merges them
> deterministically, and runs `commit → reconcile → validate`.

This is not a weakening of the architecture — it strengthens it. There is still exactly one
serialization point (the merged manifest) and one applier (`commit.py`). Subagents also keep raw
material (HTML, long source docs, big audit dumps, many entity-file reads) out of your context.

The subagent definitions live in `.claude/agents/` with Read/Grep/Glob/Bash and no Write/Edit.
They are **read-only by contract, not by sandbox**: Bash is kept because they need `grep` and
`run.py resolve`, and Bash could write. What holds the boundary is that the orchestrator's
`merge_fragments → commit` is the only write path — a fragment is inert until you commit it.

## When to fan out — and when NOT to

Fan-out costs tokens and coordination overhead. Use it when the work is genuinely parallel and
context-heavy; otherwise do it inline.

| Situation | Fan out? | Agent |
|---|---|---|
| Batch with **≥3 source documents** | Yes — one extractor per source | `dialexis-extractor` |
| Batch with 1–2 sources | No — extract inline | — |
| **Expanding an entity from the web** | Yes — almost always (HTML would flood your context) | `dialexis-researcher` |
| **Triaging a large semantic-relationship audit** | Yes — chunk candidates across triagers | `dialexis-triager` |
| **Adjudicating many duplicate candidates** | Yes — chunk pairs across dedupers | `dialexis-deduper` |
| A broad graph query over many entities | Optional — use the general `Explore` agent to synthesize a subgraph | `Explore` |

## The orchestration loop (identical for every pattern)

```
1. DISPATCH   Spawn subagents IN PARALLEL — one Agent tool call per work-item, all in a single
              message. Give each a self-contained prompt (it cannot see this conversation): the
              item to handle, the batch_id, and the `archive` path to cite.

2. COLLECT    Each subagent returns prose + a final ```json fragment. Extract ONLY that last json
              block. Write it verbatim to batches/<id>/fragments/<slug>.json. (You do the writing —
              subagents are read-only.) Relay any WORKED/FAILED report to the user.

3. MERGE      python3 run.py merge_fragments batches/<id>/fragments --out batches/<id>/manifest.json
              -> folds all upserts per entity, dedups edges/events/sources, and AUTO-EMITS an
                 add_conflict for any cross-source attribute/identifier disagreement (email case is
                 not a disagreement). Read its report: it names every auto-flagged conflict.

4. COMMIT     python3 run.py commit batches/<id>/manifest.json      # atomic; HALTs cleanly on violation
5. RECONCILE  python3 run.py reconcile                              # unconditional convergence
6. VALIDATE   python3 run.py validate                              # binding gate, read from disk
```

Steps 3–6 are the same deterministic tail as a normal `/ingest`. The only new step is `merge_fragments`,
and it exists precisely so that collisions between parallel subagents are resolved by **Python**,
not by an LLM eyeballing overlapping outputs.

## Why merge_fragments is the safety seam

Two subagents can legitimately touch the same entity (two sources both describe Jane Smith). If we
just concatenated their operations, `commit.py` would either HALT on the contradiction or let the
last writer win. `merge_fragments.py` instead:
- folds all upserts for one entity into a single op (union of aliases/relationships/events/sources),
- keeps the higher-confidence value on an attribute/identifier key clash (ties keep first-seen),
- and appends an `add_conflict` op so the disagreement is recorded first-class on the entity.

So a genuine cross-source disagreement becomes a **visible, resolvable conflict** — never a silent
overwrite and never a pipeline halt. (`add_conflict` also satisfies commit's no-silent-clobber gate,
so the merged manifest applies cleanly.)

## The subagents (contracts in `.claude/agents/`)

- **`dialexis-extractor`** — one source doc → fragment. Does PRE-READ + extraction + entity
  resolution. Every source must yield ≥1 operation.
- **`dialexis-researcher`** — one entity → web-sourced fragment + a terse WORKED/FAILED report. Runs
  the local `web_search.py`/`web_fetch.py` (ledger postcondition auto-enforced). Relay its report.
- **`dialexis-triager`** — a chunk of `audit_semantic_relationships` gaps → relationship-edge ops.
  Empty fragment is a valid result (most mentions are not edges).
- **`dialexis-deduper`** — a chunk of `analyze_database` duplicate candidates → `merge_entity` ops.
  Errs toward NOT merging; a wrong merge is destructive.

**Model tier** is pinned in each agent's frontmatter: extractor, researcher and triager run `sonnet`
(well-defined work with a deterministic checker behind it); deduper runs `opus` (an irreversible
judgment). Fleet ladder (owner, 2026-10-20): `opus` design and complex code; `sonnet` well-defined coding, prose
a human acts on, cross-context judgment; `haiku` (Haiku 5.5) mechanical bulk work with a checkable
outcome. No Fable; pass `model` on every spawn. What the aliases resolve to is set by
`ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_MODEL` / `_HAIKU_MODEL`, not by this file: read the
environment (or `genai-switch status`) before trusting a rung.

## Guardrails

- **Never** let a subagent's "I updated the file" substitute for the pipeline. Subagents cannot
  write `graph/`; if one claims it did, it is confused — ignore the claim and run the merge.
- **Never** run `commit`/`reconcile`/`validate` inside a subagent. Those are the orchestrator's.
- **Only the orchestrator mutates.** The `commit` and `reconcile` you run in the tail are the only
  writes; subagents do no disk writes at all — that's exactly why fan-out is safe. The orchestrator
  must itself be an authorized writer (a user-driven session, or the Slack relay) and takes
  the git fence around the tail. See `agents/CONVENTIONS.md` "One writer at a time."
- Parse only the subagent's **final** ```json block; treat its prose as advisory (uncertainties,
  unresolved items, source reports).
- A HALT at commit or a validate failure is still a successful safety outcome — investigate the
  merged manifest, fix the offending fragment or extend the registry, and re-run from step 3. The
  reconcile/validate tail is idempotent, so re-running the whole loop is cheap and safe.
- Below the fan-out thresholds, skip subagents entirely — inline extraction is simpler and cheaper.

## `reconcile` is not safe to run while another ingestion is in flight

**Discovered 2026-09-13 by two independent agents ingesting concurrently.** `reconcile` cleans *every*
integrated batch directory under `batches/`, not only the one belonging to the run that invoked it.
With two ingestions in flight, the first agent to reach `reconcile` **deletes the second agent's
manifest out from under it** — including one that has not yet been committed.

This is documented cleanup behaviour applied to an undocumented situation. It did no damage in the
case that found it, because both agents had already committed, but the exposure is real:

- **The fan-out contract above is safe** precisely because subagents are DECIDE-only: they return
  fragments and never run `reconcile` themselves. The orchestrator runs the deterministic tail once,
  after the merge. Keep it that way.
- **Do not give a subagent the commit → reconcile → validate tail.** If you need several ingestions
  applied, merge them into one manifest and run the tail once, or run them strictly in sequence.
- **If a manifest must survive the run** — to be inspected, re-run, or audited afterwards — copy it
  somewhere outside `batches/` before committing. `reconcile` will remove the original.
