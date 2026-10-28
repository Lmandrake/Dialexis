# DIALEXIS — System Documentation (v2.0)

A personal knowledge management system. All knowledge lives in human-readable markdown entity
files; Claude Code is the runtime; Python does only the mechanical, deterministic work. This
document describes the v2.0 architecture, which exists to fix one specific, recurring failure of
v1.0.

---

## 1. The problem v2.0 solves

v1.0 was correct in design and *mostly* correct in practice. The defect was reliability on the
hard path: on complex ingestion, the agent sometimes failed to perform the terminal bookkeeping —
moving fact records, finalizing the batch, leaving files in their canonical place — **even though
the agent spec required it.**

The root cause is structural, not a matter of better prompting: **a requirement written into an
LLM spec is a prior, not a constraint.** "Be atomic," "validate before finishing," "clean up at
the end" raise the probability of the right behavior; they do not guarantee it, and they fail
precisely on the long, complicated batches where it matters most.

v2.0's fix: **stop asking the LLM to guarantee deterministic postconditions.** Shrink the agent
to the semantic decision only, have it emit that decision as a verifiable artifact, and let
deterministic Python — which the agent cannot route around — enforce every postcondition.

---

## 2. The three layers of canonicalization

Every operation that could corrupt the store is split into three layers:

| Layer | Who | What it does |
|---|---|---|
| **DECIDE** | LLM agent | The semantic judgment only: *is this the same person? what fact is asserted? is this a real conflict?* Emitted as a manifest — never as file edits. |
| **ENFORCE** | Python (pure functions) | Given a decision, makes every artifact agree: slugs names, writes reciprocal edges, sets dates, places files, regenerates indices. Atomic, with rollback. |
| **DETECT & HALT** | Python | Notices anything the rules can't cover (unknown type, contradicting value, dangling link) and **halts loudly** rather than guessing. |

The guiding sentence: **never ask an LLM to guarantee a deterministic postcondition.**

---

## 3. Data flow

```
inbox/  ──stage_inbox.py──▶  archive/raw_inbox/ (verbatim, immutable)
                            batches/<id>/ (_manifest.json, status STAGED)
                            + if .ics files: auto-generates seed manifest.json
                              (all attendees → person entities, event entity)
                                  │
                  [ingestion_agent]  ── writes fact records ──▶ batches/<id>/
                                  │
        [database_integration_agent] ── emits ONE manifest ──▶ batches/<id>/manifest.json
                                  │
         commit.py  ── atomic apply: validate ▸ write entities ▸ reciprocal edges ▸ mechanical fields
                                  │           (HALT on any violation, nothing written)
         reconcile.py ── UNCONDITIONAL idempotent convergence (reciprocity, placement, format, indices)
                                  │           also auto-cleans completed batch directories
         validate.py ── BINDING gate, read from disk (agent cannot self-certify)
```

The sequence is owned by the `/ingest` harness (`agents/SKILL_ingest.md`), not by any agent. Steps
that must happen always (stage, commit, reconcile, validate) are scripts the harness runs every
time. The agent literally cannot leave the store half-finalized, because it no longer performs
finalization.

---

## 4. Directory layout

The tree is **generated** by `system_status.py` (`python scripts/system_status.py tree`), so this
documentation cannot drift from disk:

```
dialexis/
├── agents/                     # specs + registry + conventions + /ingest harness (machinery, regenerable)
├── archive/
│   ├── raw_inbox/              # verbatim immutable source copies — the safety floor
│   ├── docs/                   # historical reports and superseded documentation
│   └── retired/                # compressed/retired detail (archive-before-delete)
├── batches/                    # temporary work during ingestion (auto-cleaned by reconcile)
├── deploy/                     # launchd plist templates + runbook for the host jobs (§6d)
├── inbox/                      # repo-local drop zone; on the publish host DIALEXIS_INBOX_DIR points
│                               #   ingestion at <shared-inbox> instead (§6d)
├── scripts/                    # the deterministic core (see §6)
├── slack/                      # the Slack relay (bot.py) — a WRITER on its host (§6d)
├── tests/                      # smoke_test.py + fixtures
├── viz/                        # SEPARATE visualization tool (React + Flask) — not the agent
└── graph/
    ├── _index/                 # generated indices (entities.md, conflicts.md) — derived, disposable, gitignored, do not hand-edit
    │                           #   (the index.sqlite read cache is MACHINE-LOCAL, in ~/.cache/dialexis/ — never committed)
    ├── documents/  events/  organizations/  people/  places/
    ├── programs/  projects/  technology_concepts/  technology_systems/
```

Changes from v1.0: `incorporated/` **eliminated** (integration state lives only in the batch
manifest — killing the dual source of truth behind the cleanup-desync bug); `processed_inbox/`
**dropped** (the verbatim archive copy makes it redundant; the inbox original is deleted after the
copy is verified); `_batches/` manifests **merged** into each batch dir; `do_not_query.md`
**folded** into the conflicts index as a flagged subsection; active batch state **lifted** to
top-level `batches/` so `archive/` means cold and immutable only. **Batch directories are
automatically removed** by `reconcile.py` once there is positive evidence of integration — either a
legacy `INTEGRATED` status, or a commit manifest whose upserted entities are all confirmed present
in `graph/`. A still-`STAGED` batch is never removed. Source files remain in
`archive/raw_inbox/`. See `BATCH_LIFECYCLE.md` for details.

---

## 5. The entity file

One markdown file per entity, nine sections in fixed order: Summary, Identifiers, Attributes,
Relationships, Perspectives, Events and History, Conflicts, Notes, Sources. The parser is tolerant
(legacy files read fine); the serializer is strict and canonical (so normalization is just
parse-then-serialize). Relationships are stored one-directional in the source file; the inverse is
guaranteed present on the target by the committer and the reconciler, so the graph is always
symmetric and the dialogue agent can trust any single entity's relationship list.

Conflicts live **inline** on the entity as first-class blocks with status `UNRESOLVED`/`RESOLVED`
and a `Do Not Query` flag. The system never silently resolves a contradiction: an incoming value
that disagrees with an existing one halts the commit unless an explicit `add_conflict` op
accompanies it. (This is why v1.0's "0 conflicts" was misleading — it reflected silent
last-writer-wins, not genuine agreement.)

One value class is exempt from case sensitivity: **email addresses**. Capitalization in an email is
never significant, so email identifier values (any key containing "email") are canonicalized to
lowercase everywhere — on parse, on merge, and on write (`entity_io.canonical_fact_value`). Two
emails differing only in case are the same identifier and never trigger a conflict halt.

---

## 6. The deterministic core (`scripts/`)

| Module | Responsibility |
|---|---|
| `paths.py` | Canonical filesystem layout; the one definition of where things live. |
| `registry.py` | Loads the closed vocabulary from `ENTITY_TYPE_REGISTRY.md`; all type/relationship/confidence lookups. |
| `naming.py` | ENFORCE (slugging) + DETECT (name-upgrade / placement escalations). |
| `entity_io.py` | Keystone: parse (tolerant) / serialize (strict canonical) / merge facts. |
| `commit.py` | **The fix.** Applies a manifest atomically with rollback; enforces reciprocity, mechanical fields, and the no-silent-clobber rule. Also carries the `rename_entity` op (moves the file and repoints every inbound wikilink across the KB; refuses a name collision as a merge) and the `merge_entity` op (folds a duplicate into a keeper, repoints every inbound reference, deletes the duplicate). The `prune_*` family (`prune_attribute`, `prune_alias`, `prune_relationship`, `prune_event`) are the only sanctioned deletions — each matches exactly and HALTs (typo guard) if the target is absent, since upsert can only ever add. |
| `reconcile.py` | Idempotent convergence to invariants; auto-cleans batches once verifiably integrated (explicit INTEGRATED status or a commit manifest whose entities all exist on disk — it never removes a still-STAGED, uncommitted batch); also the migration engine (`--migrate`). |
| `validate.py` | Binding invariant gate, read from disk (`--report` to measure drift non-fatally; `--no-cache` forces a full live parse). Reads via the derived parse cache but re-parses any file whose `(mtime,size)` changed, so it always validates on-disk truth. |
| `index_store.py` | The **derived read cache** (`~/.cache/dialexis/<repo-id>/index.sqlite`, machine-local via `paths.index_cache_dir`; stdlib `sqlite3`). One pre-parsed artifact that turns "glob + parse ~4,500 files per op" (~650ms) into a single query (~3ms lookup). Two roles from one table: a name/alias lookup index (`lookup_index`) and a parse cache of full `Entity` blobs keyed on `(mtime,size)` (`iter_entities`). Never a source of truth: disposable, rebuildable (`rebuild`), gitignored, never synced to Drive. Refreshed in the `commit` tail, rebuilt in the `reconcile → sync_index` tail; readers auto-rebuild on drift and fall back to live parse if the DB is unavailable. See §6d. |
| `finalize.py` | Marks a batch INTEGRATED and removes its directory (optional — reconcile self-heals). |
| `audit_reciprocals.py` | Verifies every relationship has its required inverse edge. |
| `audit_semantic_relationships.py` | Identifies entities mentioned but not formally linked. |
| `health_check.py` | Unified health check suite: runs validate, reciprocal audit, semantic audit, and stats (`--full` for complete semantic audit). |
| `analyze_database.py` | Read-only quality analysis: stubs, isolates, duplicates, naming ambiguities. |
| `normalize.py` | Bulk cleanup of auto-generated boilerplate in notes/summaries (`--dry-run` available). |
| `sync_index.py` | Regenerates the derived indices in one parse pass: `entities.md`, `conflicts.md`, and the `index.sqlite` cache (`--rebuild` forces a cache-independent full rebuild). |
| `stage_inbox.py` | Archives sources verbatim, opens a batch, deletes verified inbox originals. Auto-parses .ics files into entity operations (attendees + event). |
| `entity_matching.py` | Fuzzy + contextual matching to support (not make) identity decisions. Carries the **resolve-only person resolver**: middle-initial-insensitive identity (`normalize_name` / `names_match` / `emails_match`), dynamic same-name collision detection (`find_name_collisions`), and contextual narrowing (`resolve_person`, returning a `ResolutionResult`) by org membership, email, co-mentions, and related entities. `resolve_person` detects collisions live on every call, and reports a pair already marked `Distinct from` (`distinct_from_pairs`) as settled ambiguity rather than a suspected duplicate. Maps names to *existing* canonicals to prevent duplicates; never writes/merges/renames. See §6c. |
| `resolve.py` | CLI over `resolve_person` (`run.py resolve "<query>" [--org X] [--email x@y] [--related type:name] [--co-mention Name]`). Prints the resolved record on a clean resolution, or a flagged candidate list when ambiguous. Read-only. |
| `system_status.py` | Metrics + the generated directory tree. |
| `web_search.py` | Local web search via DuckDuckGo (`--batch`, `--delay`, `--num`, `--context`, `--no-log`, `--self-test`). Auto-records each search outcome to the source ledger. |
| `web_fetch.py` | Local URL fetcher with HTML extraction, JS rendering, and PDF text extraction (`--raw`, `--render`, `--max-chars`, `--timeout`, `--headers`, `--context`, `--no-log`, `--self-test`). Auto-records each fetch outcome to the source ledger. |
| `source_log.py` | Light provenance ledger. `record()` appends WORKED/FAILED outcomes (written by the two web scripts) to `archive/web_references/SOURCE_LOG.md`; `report` aggregates by source. Outcome + target + context only — never response bodies. |
| `doctor.py` | Read-only ENVIRONMENT check (as opposed to `validate`'s graph check): interpreter identity, optional-dependency availability, registry + graph + index cache, resolved inbox, git-fence state (dirty / ahead / behind), and staged batches never integrated. |

**The core is stdlib-only** — `json`, `re`, `difflib`, `pathlib`, `urllib`, `socket`, `os`,
`sqlite3`. Everything that enforces a postcondition (commit / reconcile / validate / resolve /
entity / the audit family) must stay that way, so it runs anywhere with a bare `python3`.

**`web_fetch.py` alone has OPTIONAL third-party deps**, declared in
`requirements-optional.txt` and reported by `run.py doctor`:

| Dep | Unlocks | Silent-failure symptom if absent |
|---|---|---|
| `playwright` (+ `playwright install chromium`) | JS-rendered pages (`--render`, and auto-retry on a thin 200) | page returns HTTP 200 with an empty body — **reads as "the site blocked us"** |
| `pypdf` | PDF text extraction (by content-type OR `%PDF-` magic bytes) | PDF bytes get decoded and fed to the HTML parser — **reads as "the fetch failed"** |
| `pytest` | `tests/test_*.py` (`smoke_test.py` runs standalone) | part of the suite cannot run at all |

That silent-failure column is the whole reason `doctor` exists. Neither missing dep can
corrupt the graph; both cause a *retrievable* source to be written off as unreachable, which
is worse than a crash because it looks like someone else's fault. Check capability before
concluding a source is blocked.

---

## 6b. Concurrency: one writer at a time, many readers

Dialexis is single-writer **in time, not by designation.** There is exactly one human user, and that
user drives one Claude Code session at a time, so two writers never overlap. **Any** of the user's
machines may be the writer: the repo lives on local disk (`$DIALEXIS_ROOT`) on each machine and
is shared through git, and more than one machine may ingest. Discovering that another machine
committed since you last read is **normal operation, not a violation.** Every other *consumer*
(Cowork, any non-user-driven session) uses Dialexis read-only: it looks up and
answers from the stored files but never stages, commits, reconciles, or otherwise writes.

**The one exception, as of 2026-08-25: the Slack relay writes.** It runs on one host under
launchd (`com.dialexis.slack`, see `deploy/README.md`) against the same working copy as the
interactive session, relaying chat DMs to `claude -p`. It mutates through the normal manifest
pipeline and honors the same git fence, so its writes are structurally sound — but it is
genuinely a second writer, and it is not scheduled by the user. **This weakens the premise the
next paragraph rests on:** a relayed request can land while an interactive session is
mid-`commit`. The exposure is small, but it is real, and it is the reason to reach for a lock if concurrent-write symptoms ever appear.

Because writes are otherwise serialized by the single user, there is no cross-instance race to
defend against and no lock to manage. The failure the deterministic core once guarded with a lockfile — two writers
overlapping, torn files, half-applied manifests — cannot arise while the user is the scheduler. This
removes the overhead and the cloud-sync fragility of an advisory lock on the Google-Drive mount.

### The sync fence

Serializing writers eliminates concurrency but *not* staleness. The residual hazard is **writing on
top of a stale read**: each machine holds its own checkout, so it can hold an older copy of an entity
file than the one last committed elsewhere.

**The fence is git** (since 2026-08-24, when the repo moved to local disk at `$DIALEXIS_ROOT`).
The discipline: **`git pull --ff-only` before mutating**, and **commit + push after**
`commit → reconcile → validate` passes. Read-only commands need no fence — a stale read is merely
stale, never destructive.

What makes git the right fence is not that it prevents the collision but that it **cannot hide
one**. Writing on a stale checkout produces a divergent branch, and git refuses to push until you
resolve it explicitly. That is loud and recoverable. It replaced a mechanism that failed silently:
Drive resolved divergence by creating a **conflict copy** (`name (1)`) that nothing in the pipeline
would ever notice.

That failure was not theoretical. On 2026-08-20, while the repo still lived in Google Drive, Drive
renamed the git object directory `95` to `95 (1)` and lost the tree object for `.claude/skills`,
leaving a *ref-reachable* commit that `git ls-tree -r HEAD` could not walk. Both were recoverable
(the blobs survived; the tree was rebuilt with `git mktree` and hashed identically to the SHA the
history expected), but the lesson generalized: **a cloud-synced `.git` is the most fragile thing you
can build on**, because git's object store assumes exclusive, atomic local writes. Syncing the
*markdown* is safe by design — plain text, one file per entity, and `reconcile`/`validate` converge
it. Syncing `.git` is not. Hence the current split: `.git` never leaves local disk, and Drive is a
one-way *downstream mirror* of the markdown (`run.py publish`, see "Deployed background jobs"),
never a sync channel and never read back into the model.

**What this means in practice** — who may write, which commands mutate, and the exact fence
discipline — is the operating contract, so it lives in one place rather than here:
**`agents/CONVENTIONS.md`, "One writer at a time."** This section is the *why*; that one is the
*what to do*.

---

## 6c. Person resolution: middle-initial identity + contextual narrowing

Entity resolution is a DECIDE-side judgment, so it stays out of the deterministic writers. But two
recurring name problems are mechanical enough to resolve *deterministically* and cheaply, and
getting them wrong is expensive (a false merge is destructive; a missed match spawns a duplicate).
`entity_matching.py` carries a **resolve-only** resolver for both. It reads `graph/` and
returns a decision; it **never** writes, merges, or renames. Its whole job is to map an incoming
reference onto an *existing* canonical so a duplicate is never created in the first place.

**The middle-initial identity rule.** Two PERSON names are the **same identity by default** when
first and last names match and the middle tokens are *compatible* — compatible meaning one side is
absent, or one is an initial that prefixes the other ("J" ⊆ "James"). So `Odalys Ferncastle`,
`Odalys D Ferncastle`, and `O Ferncastle` are one person; the middle initial is optional
information, never a distinguishing one. The identical logic runs on email local-parts once the
**domain matches** (`odalys.f.ferncastle@halloway.example` ≡ `odalys.ferncastle@halloway.example`; the `d`
is optional), with a hard domain guard so `…@halloway.example` and `…@mail.example` are never equated.
The one case that is **not** auto-merged is a genuine *contradiction*: middles that are both
present and incompatible (`Tobias J Marlowe` vs `Tobias R Marlowe`). That is held **ambiguous**, not
merged — `names_match` returns exactly one of `"same"` / `"ambiguous"` / `"different"`. Same-name
collisions are discovered **dynamically** by `find_name_collisions`, which clusters everyone in
`people/` by normalized (first, last) key and flags a key only when it holds two or more genuinely
distinct clusters; redundant middle-initial variants of one person fold together and are *not*
treated as a collision.

**Contextual narrowing.** `resolve_person(query, *, context, registry)` resolves under-specified
references (first-only, last-only, a nickname) by intersecting the name candidates with context
signals: organization / group membership (with org-alias expansion, so "Odalys in CMG" works),
email or domain, same-source co-mentions, and relationships to named entities. Exactly one survivor
yields a clean resolution; a name that is unique in the DB resolves cleanly even with no context.
When more than one candidate survives, the resolver returns **all** of them at LOW confidence with
`requires_disambiguation` set and each candidate noted "requires disambiguation" — it never picks
silently. The result is a structured `ResolutionResult` (query, resolved-canonical-or-None, a
four-level confidence HIGH / MEDIUM-HIGH / MEDIUM / LOW, the candidate list, a human rationale, and
the disambiguation flag). The design bias is deliberate: **when the rules are genuinely unclear,
err toward "ambiguous,"** because a wrong auto-merge cannot be undone by the deterministic core.

**Adjudicated distinctness (`Distinct from`).** `find_name_collisions` rediscovers every same-name
collision by a live scan on each call — cheap now that the SQLite index backs it (~60 ms across the
whole model). What a scan cannot recover is the human effort of telling the collided people apart:
that *this* Cordelia Wickham is Group A passive optics and *that* one is Group B. That judgment is
recorded as a **`Distinct from`** relationship on the entities themselves. It is symmetric in the
registry, so `commit.py` writes the reciprocal edge automatically — the postcondition is Python's, not
the LLM's. Being an ordinary edge it is git-tracked, reciprocity-enforced, `validate`-checked, and
authored through a manifest like every other fact: no side-channel file, no hand-editing.

Two consumers make the ruling stick:
- `analyze_database.find_ambiguities` drops any candidate pair already marked `Distinct from`, so the
  deduper is never handed the same pair twice (`entity_matching.distinct_from_pairs`).
- `resolve_person` reports an adjudicated collision as **settled ambiguity** — narrow by context, never
  merge — rather than a suspected duplicate. It still refuses to pick silently.

*Superseded.* An earlier `graph/_index/known_ambiguities.json` cache (with
`scan_ambiguities.py`) persisted the same intent in a gitignored derived directory. It was retired for
three reasons: the ~60 ms it saved no longer justified a staleness risk once the SQLite index landed;
its auto-computed signals (middle tokens, orgs, emails) were fully recomputable from the entity files;
and the one irreplaceable field — the human's `disambiguation_hint` — had no write path at all, while a
rescan could drop the entry that carried it. Hand-authored judgment belongs on the entities, under the
same commit → reconcile → validate discipline as everything else.

**Where it is wired in** (all read-only / resolve-only):
- `run.py entity "<query>"` — a person query that resolves cleanly to a single record (via the
  middle-initial rule) opens that record directly, so `entity "Tobias Marlowe"` opens the one
  `Tobias J Marlowe` file instead of listing it as a near-miss. Guarded to only fire when the
  resolved person is also the top fuzzy hit across all entity types.
- `run.py resolve "<query>" [--org …] [--email …] [--related …] [--co-mention …]` — the explicit
  CLI (`scripts/resolve.py`) for contextual resolution; prints the resolved record or the flagged
  candidate list.
- `stage_inbox.py` .ics attendee seeding — a parsed attendee name that resolves cleanly to an
  existing canonical has its seed op's `canonical_name` (and the event's "Attended by" target)
  rewritten to that canonical, preventing a duplicate person from every calendar invite. An
  ambiguous or no-match attendee keeps its parsed name (becomes a new entity). This is
  resolve-only: it rewrites the *seed op in memory*, never a file on disk, and the seed schema is
  preserved exactly.
- **`commit.py` pre-apply resolution (deterministic, automatic)** — before any op is applied,
  `_resolve_names_in_manifest` runs `resolve_person` on every person `upsert_entity`
  `canonical_name` and every person relationship `target_name`, so bare/under-specified names
  resolve to the right stored identity even when the extractor did not call `resolve` itself.
  Context is built automatically from the op's own edges (target orgs → org context;
  projects/programs/tech/events → related-entity context), every entity co-mentioned in the same
  manifest, an optional explicit `context` block on the op (`{orgs, email, related_entities,
  co_mentions}`), and the candidate people's own memberships. A unique confident match rewrites
  the name in place; a reference that cannot be narrowed to exactly one identity **HALTs** with
  the candidate list (so a bare "Jasper" with two Jaspers on DELTA never silently forks or mis-attaches).
  It deliberately does **not** downgrade a fuller incoming name onto a shorter stored canonical —
  that superset case stays the name-upgrade path (`_find_existing_variant`), which keeps the fuller
  form. Fail-open on unexpected resolver errors (falls back to prior behavior); a genuine ambiguity
  still HALTs.

*Institution-keyed confounders.* `top_level_institution()` rolls any Halloway sub-unit ("Halloway Group
100 …") up to its parent institution, and context/membership org matching compares at that
top level. So two same-`(first,last)` people at the **same** institution stay ambiguous under an
org-only context (→ HALT), while the same name split **across** institutions resolves by org.
A non-blocking safeguard also warns (never halts) when the middle-initial auto-collapse folds a
variant onto a base that has a same-institution same-name sibling — a possible wrong merge made
visible for review.

---

## 6d. The derived read cache (`index.sqlite`)

Every whole-database read used to `glob("*.md")` and regex-parse all ~4,500 entity
files — the shared cost behind `entity`, `resolve`, `reconcile`, and `validate`.
Profiling (`profile_readpath.py`) put a lookup at ~650ms warm, split ~48% disk
I/O / ~50% markdown parse / ~2% scoring, and confirmed that the working tree is
already on **local** disk (relocating it off the Google-Drive path changed
timings by ~8%, i.e. noise). The cost was the design, not the mount: parsing the
whole DB on every read.

`index_store.py` fixes this with a **derived, rebuildable** SQLite cache at
`~/.cache/dialexis/<repo-id>/index.sqlite` (machine-local — see the Drive note below). One table serves two roles: `lookup_index()`
returns cheap `(name, aliases, type, status, path)` rows for fuzzy matching (one
~3ms query, ~200× faster), and `iter_entities()` reconstructs full `Entity`
objects from a stored `blob_json`, keyed on the file's `(mtime, size)`, so
`reconcile`/`validate` skip re-parsing unchanged files.

*Markdown is still the database.* The cache is never a source of truth: it is
gitignored, never synced to Drive (other agents/platforms keep reading pure
markdown), and can be deleted at any moment with zero semantic effect — the next
read rebuilds it (~0.5s). Correctness rests on three guards, mirroring the
ambiguity cache's philosophy (cache accelerates, disk is truth): (1) the writer
refreshes touched rows in the `commit` tail and fully rebuilds the cache in the
`reconcile → sync_index` tail (`reconcile` also skips no-op normalize writes so
unchanged files keep their mtime and stay cache-valid); (2) `iter_entities`
trusts a cached blob **only** when the file's `(mtime, size)` still matches and
re-parses from disk otherwise, so `validate` — the safety gate — can never be
fooled by a stale blob (`--no-cache` forces a full live parse regardless); (3)
any missing/corrupt DB or `sqlite3` error transparently falls back to the
original `glob + parse_file` behaviour. `is_stale()` (a cheap path-set
comparison) lets readers auto-rebuild after an add/delete/rename. Like the other
`_index/` residents, it survives reconcile because nothing wipes that directory.

---

## 6e. Deployed background jobs (one host only)

Two launchd agents run on one host, installed from `__REPO__`-templated plists in
`deploy/`. Neither belongs on a portable machine.

| Job | Script | Cadence | Role |
|-----|--------|---------|------|
| `com.dialexis.publish` | `run.py publish` | `StartInterval` 1523s (~25 min) | **Consumer.** Mirrors `graph/` markdown one-way to `<mirror-dir>` for non-Dialexis readers. Gates on right-host + clean fast-forward pull + passing `validate`; change detection is content-hash, never mtime (Drive restamps it). Prune is manifest-driven, so the `_inbox/` drop zone is never touched. |
| `com.dialexis.slack` | `slack/bot.py` | `KeepAlive` | **Writer.** Holds a persistent Socket Mode websocket, relaying chat DMs to `claude -p` and back. Mutates through the normal manifest pipeline and honors the git fence. |

The design point is the asymmetry: the publisher is a pure *consumer* of the model that writes only
outside the repo, while the relay is a genuine second *writer* the user does not schedule (§6b).
Only one machine may run the relay — two Socket Mode connections make Slack load-balance events
between them, so a DM would land nondeterministically, possibly on a host with a stale checkout.

The runbook and verification commands are in **`deploy/README.md`**; the three environment traps
that have actually broken these jobs (KeepAlive vs StartInterval, the venv interpreter, and
launchd's `PATH` silently breaking Claude Code auth) are in
**`.claude/skills/dialexis-architecture/references/deployment.md`**.

---

## 7. Database health checks

The suite ensures the database is whole, coherent, reciprocal, and properly formatted. The design
distinction that organizes it: **structural integrity** is mechanically verifiable and
auto-repairable by `reconcile` (`validate`, `audit_reciprocals`, placement), while **semantic
quality** requires judgment and is repaired only through manifests
(`audit_semantic_relationships`, duplicate adjudication, conflict resolution). The first kind is
safe to gate on; the second is a worklist, not a defect count.

The tool contracts, measured timings, cadence, and repair decision tree are in
**`agents/SKILL_health.md`** — that file is the reference, and this section is only the rationale.

### Guarantees vs. verification

The ingestion workflow **provides guarantees** that health checks **verify**:

| Ingestion Guarantee | Health Check Verifies |
|---------------------|----------------------|
| Atomic commits | `validate.py` |
| Reciprocals written automatically | `audit_reciprocals.py` |
| Format mechanically correct | `validate.py` |
| Registry compliance enforced | `validate.py` |

Manual edits **bypass guarantees**, making health checks essential after manual work.

---

## 8. Reliability guarantees (verified)

`tests/smoke_test.py` exercises the core on a throwaway copy and checks: registry load; commit
writes reciprocal edges; validate passes clean; a contradicting value halts without clobbering;
unknown types halt; a valid-then-invalid manifest leaves no partial write (atomic rollback); the
conflict path succeeds when `add_conflict` is present; `rename_entity` moves a file, repoints an
inbound relationship and a prose wikilink, preserves reciprocity, and refuses a colliding-name
merge; `merge_entity` folds a duplicate into a keeper, repoints inbound references, and preserves
reciprocity; the `prune_*` ops each remove exactly the targeted relationship/event/alias (removing
a relationship's reciprocal too) and HALT on a target that is not present; reconcile leaves an
uncommitted STAGED batch untouched while still cleaning an INTEGRATED one; reconcile is idempotent;
and migration normalizes a legacy file while repairing a missing reciprocal. All pass.

The property that matters: **a clean halt never corrupts state, and finalization is not contingent
on an agent remembering to finalize.**

---

## 9. Why not RDF, OWL, or SHACL?

The first question a reader from the semantic-web tradition asks, and it deserves a straight answer
rather than silence. The honest position: Dialexis reimplements a narrower version of machinery
that already exists as a standard, and does so on purpose.

**The standards are real and mature.** [SHACL](https://www.w3.org/TR/shacl/) has been a W3C
Recommendation since 20 July 2017 and is a declarative language for exactly what `validate.py`
does by hand — constraining the shape of a graph.
[PROV](https://www.w3.org/TR/prov-overview/) is a provenance vocabulary that this system's
confidence levels and source ledger informally approximate. And the alternative is not
hypothetical: **PKG API** ([arXiv:2402.07540](https://arxiv.org/abs/2402.07540)), from the same
group that wrote the field's defining survey, is an RDF-based personal-knowledge-graph
implementation with vocabulary for access rights and provenance. Someone has already built the RDF
version of roughly this idea.

So this is a trade, not an oversight.

**What markdown buys.** An entity is one file with frontmatter and prose. You can open it in any
editor, understand it without tooling, fix a typo, and read the change in `git diff` as a line of
English. A triple store's contents are legible through a query interface and its diffs as triples.
When the graph is the record of a professional life and the tooling is a personal project,
"readable without the tooling" outweighs formal expressivity.

**What it buys the runtime.** The LLM *is* the runtime, and it was trained on markdown and prose.
Entity files go in with no serialisation layer and come out the same shape. An RDF pipeline adds a
translation step on both sides of every operation, and translation steps are where meaning goes
missing.

**The substantive difference is not the format at all — it is where the check runs.** SHACL
validates a graph: you run it against a state and it reports conformance. Dialexis's enforcer runs
*inside the write*. The manifest is validated, applied to an in-memory model, and then either
journalled to disk whole or rolled back entirely. A validate-after-the-fact design means an
invalid state existed and something must decide what to do about it. All-or-nothing is this
architecture's entire claim, and it lives in the transaction rather than in the constraint
language. You could build that on RDF; you would be building it *around* SHACL rather than with it.

**What it costs, plainly:**

- **No interoperability.** Nothing else reads this graph without bespoke code. RDF federates; this
  does not.
- **A hand-rolled validator.** `validate.py` is narrower than SHACL and its correctness rests on
  tests rather than a specification. Every constraint is code someone maintains.
- **A fixed, small ontology.** The registry admits a closed set of types with declared inverses —
  deliberate, because it is what lets the enforcer refuse, but strictly less expressive than OWL,
  and extending it is a code change rather than a data change.
- **No reasoning.** No inference, no subsumption, no entailment. What is written is what is there.

**Use RDF instead if** your graph must federate, you need arbitrary predicates or SPARQL, you want
a reasoner, or you need a formal specification to point an auditor at. Start from PKG API rather
than from scratch. Dialexis optimises for one human's trust in one graph and trades the rest for
it.

Surrounding literature: [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md).
