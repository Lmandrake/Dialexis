# Dialexis

**A personal knowledge graph for a working life, kept as plain markdown files, where a language model makes the judgment calls and deterministic Python refuses to let it corrupt anything.**

Dialexis is for one person who has a pile of transcripts, emails, decks and notes about a professional world and wants *structure* over them: who works on what, which organisation owns which programme, who was in that meeting, which two people with the same name are not the same person. You run it inside a Claude Code session. The model reads a source and decides what it means; it never writes a file. It emits a transaction manifest, and code it cannot route around validates, applies or rejects that manifest.

> **Guiding principle: never ask an LLM to guarantee a deterministic postcondition.**

```
DECIDE   →  LLM              semantic judgment only; output is a manifest
ENFORCE  →  commit.py        applies atomically, writes reciprocals, or HALTs
DETECT   →  validate.py      reads from disk, halts loudly on any violation
```

## First run

- **Environment**: no root `pyproject.toml`. This checkout's `.venv` (Python 3.12 via uv) already carries the heavy stack — flask, huggingface-hub, sentence-transformers, torch, transformers — for `viz/`; the deterministic core (`run.py`, `scripts/`) is stdlib-only and needs nothing past Python 3.9+. Optional web-fetch extras: `python3 -m pip install --break-system-packages -r requirements-optional.txt`, then `python3 -m playwright install chromium`; Slack relay extras are in `slack/requirements.txt`.
- **Console scripts**: none declared — there is no `pyproject.toml` with a `[project.scripts]` table. Every capability runs as `python3 run.py <script_name>` (e.g. `system_status`, `doctor`).
- **Login/token**: `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` (named, never valued, in `.env.example`) for the Slack relay (`slack/bot.py`); no login subcommand was found elsewhere in this repo.
- **The job**: two LaunchAgent templates, `deploy/com.dialexis.publish.plist.template` (Label `com.dialexis.publish`) and `deploy/com.dialexis.slack.plist.template` (Label `com.dialexis.slack`); both are installed on this machine at `~/Library/LaunchAgents/`. Nothing auto-installs a LaunchAgent from a fresh clone.
- 🛑 Writes only from a session the owner launched in this checkout, enforced by a PreToolUse writer-gate hook — documented in this repo at `agents/CONVENTIONS.md` ("One writer at a time"); every other session's write is diverted into the shared Drive inbox instead.

*Detected 2026-11-10, not verified by running it.*

## What is different about it

**A refused write is the feature.** `commit.py` checks the whole manifest against a closed vocabulary (the [entity registry](agents/ENTITY_TYPE_REGISTRY.md)). If any part is illegal, nothing is written and it prints `HALT`. The model is told that a halt means the design worked, so it fixes the manifest instead of hunting for a way past the check. Reciprocal edges, dates and file placement are produced by code, because a requirement written into a prompt is only a prior, not a constraint ([why v2 exists](DIALEXIS_SYSTEM.md)).

**Ambiguity is declined, not guessed.** The zero-LLM resolver returns one entity or a ranked candidate list with a "requires disambiguation" banner. Two people recorded as `Distinct from` each other are never merged, and a query that fits both comes back with the signals that would separate them (organisation, email, related entity).

**Disagreement is stored, not erased.** Conflicts (two sources disagree, neither overwrites the other) and perspectives (who said what, kept apart from what is established) are first-class sections. Every fact carries source, date, confidence and a temporal class.

**The data is readable at 2am.** Entities are markdown files with typed links. No database, no embeddings, no server. Corrections go through manifest operations (`merge_entity`, `rename_entity`, `reclassify_entity`, `prune_*`), never a hand-edit.

## What is claimed, and what is not

- **This runs.** One private graph of thousands of entity files has been in daily use for months.
- **The mechanism is tested.** `python3 -m pytest -q` runs 281 tests (checked 2026-11-04) covering halting, rollback, reciprocal writes, resolution and conflict handling, on an invented demo graph.
- **The thesis is not measured.** That the enforcement layer prevents errors a model would otherwise commit is an argument with a plausible mechanism, not a result. A pre-registered evaluation exists in the private working copy and has not been run. Treat "halting is success" accordingly.

## Is this for you?

Probably not, and [`docs/WHAT_THIS_IS_NOT.md`](docs/WHAT_THIS_IS_NOT.md) is written to tell you so in five minutes rather than three hours. It is not RAG, not a vector store, not a server, not multi-user, not automatic, not a note-taking app, and not free to run: every ingestion spends model tokens and your attention.

## Try it in two seconds

Needs Python 3.9+ and nothing else. This builds an invented 46-entity world (every person, organisation and place is fictional) through the real ingestion pipeline, then queries it:

```bash
git clone https://github.com/Lmandrake/Dialexis && cd Dialexis
sh examples/quickstart.sh
```

```
resolve "Wren Calloway"      AMBIGUOUS (2 candidates): two adjudicated-distinct people, declined not guessed
resolve "Teodor Vashenko"    Resolved: Teodor M Vashenko [HIGH]: middle-initial variant, one person
resolve Ysolde --org "Coastal Modelling Group"
                             Resolved: Ysolde Penhallow [MEDIUM-HIGH]: a bare first name, narrowed by context
```

The demo graph is plain markdown under the directory the script prints. `python3 examples/build_demo_graph.py --out DIR` rebuilds it anywhere, and `--graph-root` points any `run.py` command at any graph.

## Using it on your own material

The runtime is a Claude Code session. Open the repository in Claude Code and say `run dialexis`; it reports state and asks what you need. `run.py` is the mechanical half:

```bash
python3 tests/smoke_test.py                        # verify the machinery on a throwaway copy
python3 run.py system_status                       # report state; mutates nothing
cp .env.example .env                               # every value ships commented out
python3 run.py stage_inbox                         # 1. archive the source, open a batch
#   the agent reads the source and writes batches/<id>/manifest.json
python3 run.py commit batches/<id>/manifest.json   # 2. atomic apply, or HALT
python3 run.py reconcile                           # 3. convergence and auto-cleanup
python3 run.py validate                            # 4. the binding gate
```

Steps 3 and 4 are unconditional after any mutation. `DIALEXIS_MODEL_DIR`, `DIALEXIS_AGENDA_DIR`, `DIALEXIS_ARCHIVE_DIR` and `DIALEXIS_INBOX_DIR` relocate your graph, agenda, archive and drop zone outside the checkout, so the machinery can be public while the graph stays private. The shipped `graph/` is empty. Read [`docs/PRIVACY.md`](docs/PRIVACY.md) before ingesting anything, especially the part about how much of a personal knowledge graph is other people's data. The full walk-through, including what to do when `commit` halts, is [`docs/GUIDE.md`](docs/GUIDE.md).

## Health and maintenance

```bash
python3 run.py health_check                  # full suite; monthly is about right
python3 run.py validate                      # format and registry compliance
python3 run.py audit_reciprocals             # reciprocal consistency
python3 run.py audit_semantic_relationships  # relationship completeness (a worklist, not a defect count)
python3 run.py analyze_database              # quality analysis, read-only
```

Structural integrity is mechanically verifiable and auto-repairable, so it is safe to gate on. Semantic quality needs judgment and is only ever repaired through a manifest. See [`agents/SKILL_health.md`](agents/SKILL_health.md).

## Map of the repository

| Path | What is there |
|---|---|
| `scripts/` | The deterministic core: `commit`, `validate`, `reconcile`, `resolve`, `stage_inbox`, the registry and matching code. Standard library only. |
| `agents/` | The agent contract: [`KEY_RULES.md`](agents/KEY_RULES.md) (13 rules and the command catalogue), [`CONVENTIONS.md`](agents/CONVENTIONS.md), [`ENTITY_FILE_FORMAT.md`](agents/ENTITY_FILE_FORMAT.md), the registry, ingestion and web-research skills. |
| `.claude/skills/`, `.claude/agents/` | Claude Code skills and the DECIDE-only subagents (extractor, researcher, triager, deduper). |
| `examples/` | The invented demo graph: builder, seed sources, `quickstart.sh`. |
| `tests/` | The test suite and `smoke_test.py`. |
| `docs/` | [Scope contract](docs/WHAT_THIS_IS_NOT.md), [guide](docs/GUIDE.md), [privacy](docs/PRIVACY.md), [related work](docs/RELATED_WORK.md) with [verified literature notes](docs/literature/VERIFIED.md). |
| [`DIALEXIS_SYSTEM.md`](DIALEXIS_SYSTEM.md), [`BATCH_LIFECYCLE.md`](BATCH_LIFECYCLE.md) | Architecture, design rationale, and the life of an ingestion batch. |
| `graph/`, `batches/`, `archive/` | Created on first ingest: your entities, transient work, immutable source copies. |

## Status and limitations

A working system published as a demonstration, not a supported product. One writer at a time, with git as the only fence. The runtime is Claude Code; the contract in `agents/` is written to be harness-neutral, but no second implementation exists, so treat that as an intention rather than a tested property. Related work, including where others got there first, is in [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md).

## Licence

MIT. See [`LICENSE`](LICENSE).

Please credit me if you find this work useful.
