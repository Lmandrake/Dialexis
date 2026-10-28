---
name: "dialexis-kairos"
description: "Manage KAIROS — the agenda, action-recommendation, event-prep, and worker-memory layer of the user's DIALEXIS knowledge graph at ~/dev/Dialexis. Use when the user asks 'what should I do now / next', wants to capture a brain-dump of tasks or dreams, manage today's short list, run a kill-or-commit review, get prep prompts for upcoming events/meetings, mark an action done, or log a work observation. Kairos actions and dreams are Dialexis entities, so captures go through the same commit → reconcile → validate tail. For plain entity lookups use dialexis-access; for ingesting external sources use dialexis-ingestion; for the machinery use dialexis-architecture."
---

# Dialexis — Kairos (agenda, recommendations & worker memory)

Kairos is the "what should I do, and what do I need to prepare for" layer on top of
Dialexis. It tracks **actions** (things to do), **events** (things happening), and
**dreams** (long-horizon aspirations); recommends the next action for the moment;
surfaces prep prompts before events; and quietly learns the user's work patterns. It is
part of the graph, not a side database: actions and dreams are Dialexis entities, so
every mutating Kairos command runs the same deterministic tail as ingestion.

> **Same cardinal rule as the rest of Dialexis: DECIDE, don't ENFORCE.** For a capture,
> you (the LLM) parse the brain-dump into a *manifest* and hand it to
> `kairos capture`; you never hand-edit `graph/`. The command applies it and runs
> commit → reconcile → validate. Kairos captures mutate the model, so run them only from an
> authorized writer — a user-driven session or the Slack relay; readers never capture.

## Environment / paths

Invoke `run.py` by absolute path (`python3 <repo>/run.py …`) and pass its
file arguments absolutely too. Kairos captures are mutations, so they take the git fence like any
other write: pull before, commit + push after. Full path and writer rules:
`agents/CONVENTIONS.md` ("Environment and paths", "One writer at a time").

## Commands

```bash
python3 <ABS>/run.py kairos now [--time N] [--energy shallow|deep] [--mood FLAVOR]  # recommend the next action
python3 <ABS>/run.py kairos today [add|drop <id>]     # manage the scarce today-set (max 3)
python3 <ABS>/run.py kairos review                    # kill-or-commit review pass
python3 <ABS>/run.py kairos prep                      # prep prompts for upcoming events
python3 <ABS>/run.py kairos done <id> [--how "…"]     # complete an action + optional observation
python3 <ABS>/run.py kairos log "…"                   # record a freeform work observation
python3 <ABS>/run.py kairos profile [--rebuild]       # show or rebuild the worker profile
python3 <ABS>/run.py kairos index                     # regenerate _index/agenda.md
python3 <ABS>/run.py kairos capture <manifest.json>   # apply an actions/dreams capture manifest
python3 <ABS>/run.py kairos ingest [--dry-run]        # consume the Lodestar checkout's structured intent queue directly
```

`ingest` is a narrower door than `capture`: it reads Lodestar's already-structured
`the board's queue file` and builds records straight from its fields — no brain-dump
parsing involved, because there is no brain dump. Run it to pull in board-derived
observations/actions/events; it writes its own receipts back to Lodestar
(`the board's receipts file`) so the board's pending list can clear. See
`scripts/kairos/ingest.py`'s module docstring for the field-level contract.

`now`, `today`, `prep`, `review`, `profile` (without `--rebuild`) and `index` are cheap
read/orient calls — run them and relay. `capture`, `done`, `log`, and `profile --rebuild`
mutate the graph, so they take the git fence (pull before, commit + push after). There is no lock.

## Capture — turning a brain-dump into actions and dreams

When the user dumps tasks, commitments, or aspirations in free text, do NOT hand-write
entity files. Parse the dump into a capture manifest, then apply it:

1. **Parse** the input into `upsert_action` ops (tasks) and/or `upsert_entity` ops with
   `entity_type:"dream"` (aspirations).
2. **Write** the ops to `batches/<id>/manifest.json` — always that shape, inside a batch
   directory you create. Never drop a loose manifest at `batches/` root or elsewhere in the
   repo: `reconcile`'s cleanup walks batch *directories*, so a stray file is invisible to it
   and lingers forever (a `batches/kairos_pillar_review_prep.json` survived exactly that way
   until it was removed by hand on 2026-08-20).
3. **Run** `python3 <ABS>/run.py kairos capture <manifest.json>` — it applies and then runs
   commit → reconcile → validate for you.

### Action op

```json
{
  "op": "upsert_action",
  "title": "Send strategy draft to Rowan",
  "next_action": "Open the draft and write the first section",
  "project": "[[alpha]]",
  "estimate": "short",
  "energy": "deep",
  "flavor": "writing"
}
```

Fields: `title` (required), `next_action` (required — the concrete first physical step),
`project`, `for_event`, `for_dream`, `due`, `estimate` (`≤2m`|`≤15m`|`short`|`session`|`deep`),
`energy` (`deep`|`shallow`), `flavor` (`creative`|`analytical`|`admin`|`social`|`learning`|
`writing`|`physical`), `status` (`active`|`today`). A good `next_action` is a physical
next step, not a restatement of the title — that's what makes `kairos now` recommendations
actionable.

### Dream op

Dreams are ordinary entities: `upsert_entity` with `entity_type:"dream"`. Put the four
narrative sections — **Core**, **Genesis Details**, **First Steps**, **Ultimate Version** —
as `###` subheadings inside the `notes` field. Wire them with relationships `About`,
`Sparked by`, and `Realized as` (link a dream to the projects/actions that advance it).

## Anti-plague mechanisms (why the list stays honest)

Kairos is designed so the agenda can't rot into an infinite guilt-list:

- **TODAY_CAP = 3.** Python refuses to add a 4th item to the today-set. If the user wants a
  4th, something must be dropped first — the scarcity is the point.
- **Kill-or-commit.** When an action's `carry_count` reaches 3 (carried across three
  reviews without progress), it surfaces as "needs decision" in `kairos review` — commit to
  it or kill it.
- **Dreams never have due dates.** Aspirations are not allowed to nag; only actions carry
  `due`. If the user gives a dream a deadline, that's a sign it's really an action (or has a
  first-step action) — model it accordingly.

Respect these rather than working around them; they are the mechanism that keeps the agenda
trustworthy.

## Worker memory (how it learns, gently)

- `kairos done <id> --how "…"` records a completion observation (how it actually went).
- `kairos log "…"` records a freeform observation at any time.
- Observations may carry a `felt` field (free text — how the work felt at completion, e.g.
  "energized", "draining"). Never scored or validated against an enum, per the gentleness rule.
- The worker profile rebuilds from accumulated observations (roughly every 20 or weekly);
  force it with `kairos profile --rebuild`.
- The profile is gentle by construction: it reports strengths first and frames patterns as
  experiments to try, not faults to fix. Keep that tone when you relay or act on it — don't
  turn it into a scolding productivity audit.

## Success criteria

For a capture or any mutating command, the task is done only when the deterministic tail has
run and passed (the command runs commit → reconcile → validate itself; a nonzero validate
means investigate, don't self-certify). For read commands, relay the recommendation/agenda
faithfully and honor the caps. A clean `HALT` you surfaced is a correct outcome.

## Reference files

This skill is the Kairos contract — the action/dream field lists above are the source of truth,
and `scripts/kairos*.py` is the implementation. Supporting references:

- `agents/CONVENTIONS.md` — paths, writer rules, confidence/date conventions.
- `agents/ENTITY_FILE_FORMAT.md` — entity markdown format (dreams are entities).
- `agents/ENTITY_TYPE_REGISTRY.md` — the closed vocabulary (`dream` type, relationships).
- `dialexis-ingestion` skill — the general manifest → commit → reconcile → validate harness.
