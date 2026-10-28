# Dialexis

**A personal knowledge graph where the markdown files *are* the database, a language model makes
the judgment calls, and deterministic code refuses to let it corrupt anything.**

> **Guiding principle: never ask an LLM to guarantee a deterministic postcondition.**

The model decides *what is true* — is this the same person, is that relationship real, does this
contradict what we already believed. It never writes a file. Instead it emits a **transaction
manifest**: a description of intended changes. Deterministic Python it cannot route around then
validates that manifest against a registry, applies every change atomically, writes the reciprocal
edges itself, sets the mechanical fields, and **refuses the entire transaction** if any part of it
is illegal.

The refusal is the feature. A halt means the safety design worked.

```
DECIDE   →  LLM              semantic judgment only; output is a manifest
ENFORCE  →  commit.py        applies atomically, writes reciprocals, or HALTs
DETECT   →  validate.py      reads from disk, halts loudly on any violation
```

---

## Is this for you?

Probably not, and [**`docs/WHAT_THIS_IS_NOT.md`**](docs/WHAT_THIS_IS_NOT.md) is written to tell
you so in five minutes rather than three hours. The short version: it is **not** RAG, not a vector
store, not a server, not multi-user, not automatic, not a note-taking app, and not free to run.

It might be for you if you have a pile of transcripts, emails and documents about a professional
world; what you lack is *structure* over them rather than search; and you would rather a write be
refused loudly than accepted approximately.

## What is actually claimed

- **This runs.** One graph of thousands of entity files, in daily use for months, with a test suite that builds and queries an invented demo graph.
- **The mechanism is verified.** The test suite proves the enforcer halts, rolls back, writes
  reciprocals, and never leaves a partial write.
- **The thesis is *not* yet measured.** That the enforcement layer prevents errors a model would
  otherwise commit is an argument with a plausible mechanism — not a result. The evaluation that
  would settle it is
  [pre-registered with its refutation condition fixed in advance](docs/superpowers/specs/2026-09-11-evaluation-protocol.md),
  and has not been run. Treat "halting is success" accordingly.

That distinction is the whole reason this repository is worth reading rather than just using.

---

## Try it in 60 seconds

Needs only Python 3.9+. This builds an invented 46-entity world (every person, organisation and
place is fictional) through the real ingestion pipeline, then consults it:

```bash
sh examples/quickstart.sh
```

```
$ run.py --graph-root <demo> resolve "Wren Calloway"
AMBIGUOUS (2 candidates) -- requires disambiguation        # two adjudicated-distinct people: declined, not guessed
$ run.py --graph-root <demo> resolve "Teodor Vashenko"
# Resolved: Teodor M Vashenko  [HIGH]                       # middle-initial variant: one person
$ run.py --graph-root <demo> resolve Ysolde --org "Coastal Modelling Group"
# Resolved: Ysolde Penhallow  [MEDIUM-HIGH]                 # a bare first name, narrowed by context
```

The demo graph also carries a recorded conflict (two sources disagree, neither overwrites the
other) and perspectives (who said what, kept apart from what is established). Browse it as plain
markdown under the directory the script prints. `python3 examples/build_demo_graph.py --out DIR`
rebuilds it anywhere; `--graph-root` points every command at any graph.

## How an agent uses it

The graph is consulted **before** an agent touches a person. A task that names "Ysolde" or "Wren
Calloway" starts with a lookup, not a guess: the resolver (`run.py resolve`, zero-LLM,
deterministic) returns one entity, or a ranked candidate list with a "requires disambiguation"
banner. **Ambiguity is declined, not guessed.** Two people recorded as `Distinct from` each other
are never merged, and a query that fits both is handed back with the signals that would separate
them (org, email, related entity, co-mention).

## Architecture

- **Entities** are markdown files with typed relationships; reciprocal edges are written by code.
- **Manifests** are the only way in: the model emits a transaction manifest, `commit.py`
  validates it against the registry and applies it atomically, or halts the whole transaction.
- **Resolution** is middle-initial-insensitive and context-aware; adjudications persist as data.
- **Conflicts** and **perspectives** are first-class sections, so disagreement is stored, not erased.
- **Provenance**: every fact carries source, date, confidence and a temporal class.

## Requirements

**Claude Code.** The runtime *is* a Claude Code session — there is no server, no daemon, and
nothing to deploy. `scripts/` is stdlib-only Python 3.9+ with no third-party dependencies; the
optional Slack relay and visualiser have their own.

The agent-facing contract in `agents/` is written to be harness-neutral, but no second
implementation exists, so treat that neutrality as an intention rather than a tested property.

## Quick start

```bash
python3 tests/smoke_test.py        # verify the machinery on a throwaway copy
python3 run.py system_status       # report state; mutates nothing
```

Then open the repository in Claude Code and say `run dialexis`. The session boots by reporting
state and asking what you need.

The shipped `graph/` is empty; the invented demo lives in `examples/` (see above).

Your own content lives wherever you point it:

```bash
cp .env.example .env      # every value ships commented out; uncomment what you need
```

`DIALEXIS_MODEL_DIR`, `DIALEXIS_AGENDA_DIR` and `DIALEXIS_ARCHIVE_DIR` relocate your graph
outside the checkout entirely, which is what lets this machinery be public while a graph stays
private. See [`docs/PRIVACY.md`](docs/PRIVACY.md) before you ingest anything — especially the part
about how much of a personal knowledge graph is *other people's* data.

## The ingestion cycle

```bash
python3 run.py stage_inbox                        # 1. archive the source, open a batch
#   the agent reads the source and produces batches/<id>/manifest.json
python3 run.py commit batches/<id>/manifest.json  # 2. atomic apply, or HALT
python3 run.py reconcile                          # 3. convergence + auto-cleanup
python3 run.py validate                           # 4. the binding gate
```

Steps 3 and 4 are unconditional after any mutation. Duplicates, renames and retractions all go
through manifest operations (`merge_entity`, `rename_entity`, `prune_*`) — never a hand-edit.

---

## Documentation

| | |
|---|---|
| [`docs/WHAT_THIS_IS_NOT.md`](docs/WHAT_THIS_IS_NOT.md) | Scope contract. Read before anything else. |
| [`docs/STATUS.md`](docs/STATUS.md) | Where the work stands, decisions already made, and what is blocked on what. |
| [`docs/GUIDE.md`](docs/GUIDE.md) | From empty checkout to a graph you trust, including what to do when it halts. |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Every egress path, and the third-party-data problem. |
| [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md) | Where this sits in the literature, and who got there first. |
| [`DIALEXIS_SYSTEM.md`](DIALEXIS_SYSTEM.md) | Full architecture and design rationale. |
| [`agents/KEY_RULES.md`](agents/KEY_RULES.md) | The agent contract: 13 rules, command catalog, web policy. |
| [`agents/ENTITY_TYPE_REGISTRY.md`](agents/ENTITY_TYPE_REGISTRY.md) | Every legal entity type, relationship, and enumeration. |
| [`agents/SKILL_health.md`](agents/SKILL_health.md) | The health and maintenance loop. |
| [`CLAUDE.md`](CLAUDE.md) | Resident instructions for the agent runtime. |

## Layout

```
agents/       the agent contract — registry, conventions, manifest schema, skills
scripts/      the deterministic core. stdlib only, no third-party imports
graph/        your knowledge base (not shipped; created on first ingest)
examples/     the invented demo graph builder, seeds and quickstart
archive/      immutable source copies, retired entities, source ledger
batches/      transient ingestion work, auto-cleaned after integration
tests/        smoke test + the suite
docs/         public documentation and design records
.claude/      skills and subagents for the Claude Code runtime
viz/          a SEPARATE React + Flask visualiser. Never the answer to "run dialexis"
slack/        optional relay: DMs to `claude -p` and back. A writer. Off by default
deploy/       launchd templates for the two background jobs
```

## Health and maintenance

```bash
python3 run.py health_check                  # full suite; monthly is about right
python3 run.py validate                      # format and registry compliance
python3 run.py audit_reciprocals             # reciprocal consistency
python3 run.py audit_semantic_relationships  # relationship completeness (a worklist, not a defect count)
python3 run.py reconcile                     # repair format, reciprocals, placement
python3 run.py analyze_database              # quality analysis, read-only
```

The design distinction that organises all of it: **structural** integrity is mechanically
verifiable and auto-repairable, so it is safe to gate on. **Semantic** quality needs judgment and
is only ever repaired through a manifest. See [`agents/SKILL_health.md`](agents/SKILL_health.md).

## Publication safety

```bash
python3 run.py sanitize_check --tier1-only   # patterns only; needs no private data
python3 run.py sanitize_check                # + identity check against your private graph
```

Two tiers because only one of them can see private data. Tier 1 finds secrets, emails, personal
paths and hostnames and can run in CI. Tier 2 builds a denylist from your graph's own entity
filenames and therefore runs locally. It protects *the repository*, not the graph — see
[`docs/PRIVACY.md`](docs/PRIVACY.md).

## Web expansion

```bash
python3 scripts/web_search.py "query" --num 10        # DuckDuckGo
python3 scripts/web_fetch.py <url> --context "Entity" # fetch + tag with its purpose
python3 run.py source_log report                      # per-source WORKED/FAILED rollup
```

Never automatic. Every fetch and search is logged to the source ledger.

---

## Licence

MIT. See [`LICENSE`](LICENSE).

This is a working system published as a demonstration, not a supported product.

## Credit

Please credit me if you find this work useful.
