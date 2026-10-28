# Dialexis: a user's guide

This walks from an empty checkout to a graph you trust. It assumes you have read
[`WHAT_THIS_IS_NOT.md`](WHAT_THIS_IS_NOT.md) and decided to continue anyway.

---

## Three things to understand before you touch anything

**1. There is no application.** The runtime is a Claude Code session. You do not start a server;
you open the repository and talk to it. `run.py` commands are the *mechanical* half — staging,
committing, validating — and the session is the *judgment* half.

**2. The interesting step is not a command.** Between `stage_inbox` and `commit` there is a step
where the model reads your source and decides what it means. That step is a conversation, not a
script, and it is where most of the time goes. If you are looking for the one-liner that ingests a
file, it does not exist and the absence is deliberate.

**3. A refusal is the system working.** When `commit` prints `HALT`, nothing was written. Do not
look for a way around it — fix the manifest or extend the registry. The entire architecture exists
to make that halt possible.

---

## Install and verify

```bash
git clone <your fork> dialexis && cd dialexis
python3 tests/smoke_test.py     # exercises the core on a throwaway copy
python3 run.py doctor           # interpreter + optional capabilities report
```

`scripts/` is stdlib-only Python 3.12, so the core needs no installation. `doctor` will tell you
which *optional* capabilities are missing — `pypdf` for PDF extraction, `playwright` for
JS-rendered pages, `pytest` for the full suite. None are required to start.

```bash
python3 run.py system_status    # mutates nothing; prints entity counts and inbox state
```

On a fresh checkout that reports `Total entities: 0`. That is correct and not very interesting —
which is the honest problem with evaluating Dialexis today, since no public demo corpus exists
yet.

## Configure

```bash
cp .env.example .env
```

Every line ships commented out, so copying it changes nothing. Uncomment what you need.

**The one setting you must have to ingest anything** is the drop zone:

```bash
DIALEXIS_INBOX_DIR=~/some/folder/_inbox
```

There is deliberately **no repo-local fallback**. Unset, `stage_inbox` refuses to run rather than
silently accumulate sources somewhere nothing syncs.

**If you want your graph outside the checkout** — which you do, if this repository is ever going
to be public or shared:

```bash
DIALEXIS_MODEL_DIR=~/dev/my-notes/graph
DIALEXIS_AGENDA_DIR=~/dev/my-notes/agenda
DIALEXIS_ARCHIVE_DIR=~/dev/my-notes/archive
```

Set all three together. They may point anywhere, including outside any git repository. Paths
recorded in manifests come out absolute in that case and still resolve.

Read [`PRIVACY.md`](PRIVACY.md) before the first ingestion, not after.

---

## Your first ingestion

Put one source in your inbox — a meeting transcript is the easiest first case, because it names
several people and states relationships explicitly.

### Step 1 — stage it

```bash
python3 run.py stage_inbox
```

**This moves your file.** It copies the source into `archive/raw_inbox/` with a timestamp prefix,
then deletes it from the inbox, and opens a batch under `batches/<id>/`. The archive copy is the
permanent provenance record; a `prune_archive` job removes copies older than 60 days, so the
archive is not a long-term document store.

If you were expecting a non-destructive read, this is the surprise. It is why the inbox is a *drop
zone* rather than a folder you keep things in.

### Step 2 — let the session extract

In Claude Code, say `run dialexis` and then ask it to ingest the staged batch. It will read the
source, look up entities that already exist, and produce `batches/<id>/manifest.json` — a list of
intended operations. It does not write entity files.

Read the manifest before committing it. It is plain JSON, and this is the last point at which a
mistake is free.

### Step 3 — commit

```bash
python3 run.py commit batches/<id>/manifest.json
```

Either every operation applies, or none does. On success it prints the entities written. On
refusal it prints `HALT` with the reason and writes nothing.

### Step 4 — converge and verify

```bash
python3 run.py reconcile     # repairs format, reciprocals, placement; cleans the batch
python3 run.py validate      # the binding gate
```

**Both are unconditional after any mutation.** `reconcile` is idempotent, so running it twice
costs nothing and skipping it leaves the graph in a state `validate` may reject.

---

## When it halts

A halt is a diagnosis. The common ones and what they mean:

| Halt | What it means | What to do |
|---|---|---|
| `unknown entity type 'x'` | The registry has no such type | Use an existing type, or extend `agents/ENTITY_TYPE_REGISTRY.md` deliberately |
| `unknown relationship` | Same, for a relationship | Same — and check whether an existing relationship already expresses it |
| a contradicting value | The manifest asserts something that conflicts with a stored fact | Decide which is right. If both are true in different periods, add an explicit `add_conflict` op |
| target does not exist | A relationship points at an entity that is not there | Add the target in the same manifest, or fix the name |

The pattern in all four: the enforcer will not guess. It is telling you a judgment is required
that the model either skipped or got wrong.

**Never edit entity files by hand to get past a halt.** Every mutation goes through a manifest —
including merges, renames and retractions (`merge_entity`, `rename_entity`, `prune_*`). Hand-edits
bypass the reciprocity and format guarantees, and the health checks exist precisely because
hand-edits used to happen.

Refusals are now recorded to `output/halt_log.jsonl`, which is machine-local and gitignored. If
you are curious how often the enforcer catches something, that file is the answer.

---

## Keeping it healthy

```bash
python3 run.py health_check                  # the full suite; monthly is about right
python3 run.py audit_reciprocals             # one-directional relationships
python3 run.py audit_semantic_relationships  # entities that mention each other but are not linked
python3 run.py analyze_database              # duplicates and quality signals, read-only
```

The distinction that organises the whole loop:

- **Structural** problems — bad format, missing reciprocal, misplaced file — are mechanically
  detectable and `reconcile` repairs them. Safe to gate on.
- **Semantic** problems — two entities that are really one person, a relationship that should
  exist — need judgment. `audit_semantic_relationships` produces a **worklist, not a defect
  count**, and every repair goes through a manifest.

Expect duplicate people. Any system that ingests names from many sources accumulates them; the
tooling finds them and you adjudicate.

---

## Before you publish or share the repository

```bash
python3 run.py sanitize_check --tier1-only   # secrets, emails, personal paths, hostnames
python3 run.py sanitize_check                # + real names from your graph
```

Two tiers because only one can see private data. Tier 2 builds a denylist from your graph's own
entity filenames, so it only works locally, and it will flag your own documentation if you wrote
examples using real colleagues — which is exactly what it is for.

It protects the repository, not the graph, and it is a seatbelt rather than a vault. Read the diff
of a first public commit yourself.

---

## Troubleshooting

**`stage_inbox` says there is no inbox.** `DIALEXIS_INBOX_DIR` is unset. There is no fallback by
design.

**Unsetting a variable does not disable it.** `scripts/paths.py` loads `.env` with `setdefault`,
so a real environment value wins over the file — but an *absent* one lets the file supply the
value. `env -u DIALEXIS_INBOX_DIR run.py stage_inbox` therefore still reads the inbox configured
in `.env`. To actually override, set the variable to something else; to make a command inert,
point it at a temp directory. This trap has caused a real unintended ingestion.

**A command touched live data while I was experimenting.** Assume anything not obviously read-only
mutates. `system_status`, `validate`, `doctor` and `analyze_database` are safe; `stage_inbox`,
`commit`, `reconcile`, `publish` and `prune_archive` are not.

**`validate` fails after a hand-edit.** Run `reconcile` first — it repairs structural damage. If
`validate` still fails, the problem is semantic and needs a manifest.

**The index seems stale.** `index.sqlite` is a derived cache keyed by a hash of the model path.
Delete it; `reconcile` rebuilds it. Nothing is lost — it is rebuildable from the markdown by
definition.

**Entity counts differ between `find` and `git ls-files`.** `graph/_index/` holds generated
markdown that is gitignored. `git ls-files` is the honest count.

**Tests deleted my graph.** This happened once, on 2026-08-24, and is why `tests/conftest.py` and
`tests/test_isolation.py` exist. If `test_isolation.py` fails, **stop** — it means the isolation
fixture is not active and the suite is pointed at real data.

---

## Where to go next

- [`../DIALEXIS_SYSTEM.md`](../DIALEXIS_SYSTEM.md) — the architecture and why it is shaped this way
- [`../agents/KEY_RULES.md`](../agents/KEY_RULES.md) — the contract the agent operates under
- [`../agents/ENTITY_TYPE_REGISTRY.md`](../agents/ENTITY_TYPE_REGISTRY.md) — every legal type
- [`RELATED_WORK.md`](RELATED_WORK.md) — the literature, and what is not yet claimed
