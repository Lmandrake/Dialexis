# Agent Conventions (shared)

Every agent reads this file first. It defines the boundary that makes Dialexis reliable.

**IMPORTANT**: See `ENTITY_FILE_FORMAT.md` for the exact markdown format required for entity files. DO NOT use YAML frontmatter (`---` delimited headers). Use the Dialexis markdown format with `## Metadata` sections.

## The one rule that matters: decide, don't enforce

You (the agent) are the **semantic** layer. You make judgments a program cannot: *is this the
same person? what fact is being asserted? is this a real conflict? what does this entity value?*

You do **not** perform deterministic bookkeeping. You never move, rename, or delete files; you
never write entity files directly; you never set dates from the clock; you never decide a
filename. Those are Python's job, and Python does them atomically and verifiably.

Concretely: **your output is a transaction manifest, not a set of file edits.** You describe the
mutations you intend; `commit.py` validates them against the registry and applies them
all-or-nothing, writing reciprocal edges and mechanical fields itself. If you find yourself about
to run `mv`, `cp`, or to edit a file under `graph/`, stop — emit a manifest operation
instead.

Why: an instruction to "remember to clean up" or "be atomic" is a *probability*, not a
guarantee, and it fails exactly on the hard cases. Moving finalization out of your hands and into
deterministic code that the harness runs unconditionally is what turns those properties from
aspirations into facts.

## Environment and paths

- The repo is on local disk (`<repo>`) wherever it is checked out —
  but its location is not a guarantee: `paths.ROOT` self-anchors to
  `run.py`'s own location, so a checkout elsewhere (a worktree, a fresh clone) works unchanged.
  Invoke `run.py` by its **absolute** path; it then works from any directory.
- **The content roots are configurable and need not be inside the repo.** `paths.py` is the
  authority; never assume a layout. Each is a call-time resolver with an in-repo fallback, so an
  unconfigured checkout still works:

  | Variable | Resolves | Default |
  |---|---|---|
  | `DIALEXIS_MODEL_DIR` | the knowledge graph (`paths.WORKING_MODEL`, `paths.entity_dir()`) | `<repo>/graph` |
  | `DIALEXIS_AGENDA_DIR` | Kairos ledger, worklog, quarterly archive, worker profile | `<repo>/agenda` |
  | `DIALEXIS_ARCHIVE_DIR` | source copies, retired entities, source ledger | `<repo>/archive` |

  Consequence for anything that RECORDS a path (a commit result, a batch `_manifest.json`): use
  `paths.display_path()`, never `Path.relative_to(paths.ROOT)` — `relative_to` **raises** for a
  relocated root, and in `commit.py` such a raise once landed past the rollback window and left the
  store half-written. `display_path` emits repo-relative inside `ROOT` and absolute outside it, and
  both forms re-resolve through `paths.ROOT / recorded`. `batches/` and `output/` are
  intentionally repo-local. `.env.example` lists every `DIALEXIS_*` variable with its default.
- **`run.py commit` accepts a repo-relative manifest path from any CWD.** A relative path is tried
  against the CWD first and, if absent there, against the repo root — so
  `run.py commit batches/<id>/manifest.json` works anywhere. Other scripts' file arguments are
  still CWD-relative; pass those absolutely.
- **Never leave a persistent `cd`.** The Bash tool keeps its CWD across calls, so a bare
  `cd graph && …` leaks into later commands. Confine any `cd` to a subshell `(cd d && …)`,
  or use Grep/Glob instead.
- **The inbox is not `inbox/`.** `paths.resolve_inbox()` is the authority; on the publish host
  `DIALEXIS_INBOX_DIR` points `stage_inbox` at `<shared-inbox>`, the shared Drive
  drop zone. `run.py system_status` prints what it actually sees. A file left in a repo-local
  `inbox/` is invisible to the pipeline and will never be ingested.
  Files arriving over Drive get a quiescence check (content hash across a pause, never mtime —
  Drive restamps it), so a mid-sync partial file is never staged.
- In **Cowork** (not Claude Code) the repo appears under a session mount like
  `/sessions/<session>/mnt/Dialexis`; resolve it once with `ls /sessions/*/mnt/Dialexis`. In Claude
  Code there is no such mount — don't go looking for one.

## One writer at a time — the canonical rule

**This section is the single source of truth for the writer/fence doctrine.** Other files state the
invariant in a line or two and point here; the design rationale and the Google Drive history that
motivated it live in `DIALEXIS_SYSTEM.md` §6b.

🔒 **Enforced, not just stated (owner ruling 2026-10-07):** only a Claude session *launched* in this
repo may run a mutating command (`stage_inbox`, `commit`, `reconcile`, `merge_fragments`, …) or
write `batches/`; and no session may hand-edit `graph/`. A PreToolUse writer-gate hook refuses anything else. It also refuses shell writes naming `graph/` (redirects, `sed -i`, `cp`/`mv`/
`rm`, python file writes) from every session; git is exempt. From a foreign session, a Write/Edit
aimed at `graph/` or `batches/`, and any manifest a refused `commit` names, is **diverted** into the
Drive inbox as a `*_diverted_*.md` file for a real Dialexis ingestion — ingest those like any source.
Text matching can misfire on a command that merely *mentions* `graph/`; use the Write tool then.
Twice a foreign session ingested a batch a Dialexis session was mid-way through (`LESSONS.md`).

Dialexis is single-writer **in time, not by designation.** One human user drives one session at a
time, so two writers never overlap. **Any** of the user's machines may be that writer — several machines may ingest, each from its own local checkout, and git carries the results between
them. Drive is only a one-way downstream mirror of the markdown, never a sync channel.

**Who may write:**

- A session the **user** is driving, on any of their machines.
- The **Slack relay** (`com.dialexis.slack`), as of 2026-08-25. It relays chat DMs to
  `claude -p` against the same working copy, mutates through the normal manifest pipeline, and
  honors the same fence. It is a real second writer that the user does not schedule, so an
  unexplained concurrent change may be a relayed request rather than corruption — check
  `output/slack.log` before diagnosing damage.
  Only Slack users in `DIALEXIS_SLACK_OWNERS` get this writer path. Users in
  `DIALEXIS_SLACK_CONTRIBUTORS` (a list of non-owner Slack user ids) get a `--restricted`
  Read/Grep/Glob run on the graph, and anything they contribute is written by `slack/access.py`
  into the inbox as an UNVERIFIED note, never into the graph. Everyone else is refused.
- Nobody else. Cowork and every other *consumer* is read-only: it looks things up and answers from
  stored files, but never stages, commits, reconciles, or writes.

**Mutating commands** — `commit` (and the `rename_entity` / `merge_entity` / `prune_*` ops it
carries), `reconcile`, `stage_inbox`, `finalize`, `prune_archive --apply`, `normalize`, `sync_index`,
and the Kairos captures — run only from those two writers. **Read-only commands** — `validate`,
`analyze_database`, `audit_*`, `entity`, `resolve`, `system_status`, and any dry run — are safe from
anywhere and never wait on anything.

Because the user serializes writes there is **no lock**: no `.dialexis.lock`, no TTL, no
`HALT (locked)`. Don't go looking for one, and don't describe a command as "taking" one.

### The sync fence (the hazard that replaces concurrency)

Serializing writers removes concurrency but not **staleness** — the residual hazard is mutating on
top of a stale read. The fence is git:

- **Before mutating:** `git pull --ff-only`.
- **After** `commit → reconcile → validate` passes: commit the entity files you changed and
  `git push`, so the other machine is not left stale.
- A **rejected push** means real work exists that you have not seen. Rebase (incoming commits
  usually touch different entity files), re-run `validate`, then push. **Never force-push** past a
  rejection.
- Reads need no fence. A stale read is merely stale, never destructive.

**Entities changing without your having written them is expected, not a violation.** `validate` is
the arbiter of soundness, not authorship — if it passes, the other machine's work is sound. Only
investigate when validation actually fails.

## The registry is the closed vocabulary

`agents/ENTITY_TYPE_REGISTRY.md` lists every allowed entity type, relationship type, source
type, and enumeration. You must stay inside it. If you encounter something it does not cover (a
new kind of entity, a relationship with no listed inverse), **do not invent it** — record it in
the manifest's top-level `unresolved` list with a reason. `commit.py` HALTs with nothing written
while that list is non-empty, printing each item — so flagging it forces a deliberate decision
(extend the registry, or drop the item and remove it from the list) before anything lands.

## The `document` type is broad

The `document` entity type covers all citable or referenceable artifacts: papers, preprints,
presentation decks, conference videos, technical reports, SOWs, specifications, AND websites
(root-level domains — one entity per site, not per page). When ingesting a publication, use
`Authored by` relationships to link to person entities. Use the Notes section for extended
content summaries that go beyond what structured attributes can capture. See
`ENTITY_FILE_FORMAT.md` for detailed subtype guidance.

## Mechanical fields you must NOT set

The committer sets these; anything you provide is ignored:
- `ingestion_date` / entity `Last updated` (clock)
- filenames / slugs (derived from canonical name)
- reciprocal relationship edges (derived from the inverse table)
- default confidence when you omit it (derived from source type)

## Pre-read before manifest construction

Before constructing your manifest, read the existing entity file for every entity you plan to
upsert. `commit.py` halts when an incoming attribute contradicts a stored one, and it cannot know
which value you meant to win — so a manifest written blind halts on contradictions you could have
resolved in advance. This is the single largest source of avoidable commit failures, which is why
the step is part of the harness rather than a suggestion.

**How:** Glob for `graph/<type>/<slug>.md`, read it, note existing attribute keys and
values. Then when building your manifest:
- **Same key, same value** → skip the attribute (already correct)
- **Same key, different value** → multi-valued field (center, affiliation, org code, posting,
  project membership): accumulate via the edge or a qualified key (`"Role - ALPHA"`); authoritative
  update (org chart): `prune_attribute` first; single-valued contested fact: `add_conflict`; or
  skip if the existing value is more precise. Full rule: `SKILL_ingest.md` step 2.
- **New key** → safe to emit normally

This step is documented in `SKILL_ingest.md` as step 2 (PRE-READ) and is not optional.

## What a manifest looks like

A single JSON file, `batches/<id>/manifest.json`, shaped `{"batch_id": "<id>", "operations": [...], "unresolved": [...]}` (`unresolved` optional;
non-empty HALTs).
The operation types, their exact field names (`commit.py` rejects wrong ones), and worked examples
are defined in one place: **`agents/SKILL_ingest.md`, "Quick Reference: Manifest Field Names"**.

## Confidence

Four levels: `HIGH`, `MEDIUM-HIGH`, `MEDIUM`, `LOW`. Defaults come from source type (see
registry). You may *lower* below the default with justification; prefer the lower level when torn.
Confidence is a real epistemic claim, not decoration — corroboration across independent sources is
grounds to raise it, and you should say so in the fact's note rather than silently bumping it.

## Dates

ISO 8601 (`YYYY-MM-DD`) or the literal `unknown`. Never guess a date. `unknown` is correct and
preferred over a plausible fabrication.

## Empty manifests are never acceptable

Every ingested source must produce at least one operation. `commit.py` checks this before any
processing and HALTs loudly on zero operations, so nothing is written.

The check exists because a zero-operation manifest is never a *finding* — it is a symptom, and
always one of four failures: extraction missed the content, the source file was empty or corrupt,
the task was misunderstood, or the wrong document got ingested. None of those should pass silently.

So the zero-op case is reserved for failures, not for "nothing found." If a source genuinely holds
nothing relevant (rare), say so explicitly in your report **and** emit one minimal operation
recording that it was reviewed — e.g. a note on a related entity, "reviewed <source> on <date>, no
relevant content." That way the review itself is durable and the halt stays meaningful.

## Create relationships, not just attributes

When team information, organizational affiliations, or dependencies are mentioned, create
**formal relationship edges**, not just attributes:

❌ **Wrong** (attribute only):
```json
"attributes": [
  {"key": "Effort allocation", "value": "20% on BETA"}
]
```

✅ **Correct** (attribute AND relationship):
```json
"attributes": [
  {"key": "Effort allocation", "value": "20% on BETA"}
],
"relationships": [
  {"rel_type": "Works on", "target_type": "project", "target_name": "BETA", "source": "transcript"}
]
```

**Why:** The attribute provides detailed context; the relationship enables graph queries.
Both serve different purposes. The health check suite (`audit_semantic_relationships.py`)
will flag cases where relationships are missing.

## Halting is success, not failure

If you cannot place something safely, halting with a clear `unresolved` entry (which `commit.py`
enforces) is the *correct* outcome. The system is designed so that a clean halt never corrupts state. Do not paper over
uncertainty to keep the pipeline moving.
