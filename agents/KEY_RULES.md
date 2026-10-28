# DIALEXIS — Key Rules, Commands & Web Policy (full reference)

This is the authoritative long-form operating reference for Dialexis. `CLAUDE.md` keeps
only the always-resident cardinal invariants and a router; the complete rules, the command
catalog, and the web-access policy live here so they load when needed without bloating the
per-turn context. The skills under `.claude/skills/` — owned by Claude Code, the authorized
read/write runtime — carry the same procedures task-scoped; this file is the single flat
reference the skill-less runtime falls back to.

Skill ↔ area map: **dialexis-access** (read/lookup/resolve), **dialexis-ingestion** (the
write path / harness / registry use), **dialexis-architecture** (machinery, maintenance,
performance, viz, registry evolution), **dialexis-kairos** (agenda/actions/dreams/worker
memory).

---

## The 13 Key Rules

1. **Never edit `graph/` directly** — emit manifests, let `commit.py` apply them
2. **The registry is the closed vocabulary** — see `agents/ENTITY_TYPE_REGISTRY.md`
3. **Halting is success** — if you can't place something safely, list it in the manifest's top-level `unresolved` array — `commit.py` HALTs (nothing written) while that list is non-empty
4. **Empty manifests are never acceptable** — every source must produce at least one operation
5. **Run reconcile + validate after every mutation** — these are unconditional, non-optional
6. **Manifest field names** — top-level ops use `canonical_name` (NOT `entity_name`), events use `desc`, sources use `name`+`archive`, conflicts use `title`+`value_a`+`value_b`. See `agents/SKILL_ingest.md` Quick Reference.
7. **Pre-read before manifest** — always read existing entity files before building upsert ops to avoid contradiction halts
8. **Rename via the `rename_entity` op** — never rename an entity by hand-editing files or re-upserting under a new name (that orphans inbound wikilinks). The op (`entity_type`+`old_name`+`new_name`) moves the file and repoints every inbound reference; it HALTS on a name collision (that case is a merge).
9. **Merge duplicates via the `merge_entity` op** — never merge by hand-editing files. The op (`entity_type`+`keep_name`+`merge_name`) folds the duplicate into the keeper (keeper wins on collisions), repoints every inbound reference, and deletes the duplicate.
10. **Invoke `run.py` by absolute path; never leave a persistent `cd`** — `run.py` self-anchors to the project root, so `python3 /abs/path/to/run.py ...` works from anywhere. The Bash tool keeps its cwd across calls, so a bare `cd graph && ...` leaks into later commands and breaks relative `python3 run.py` invocations. Confine any `cd` to a subshell — `(cd dir && ...)` — or avoid it entirely (use Grep/Glob instead).
11. **Subagents are DECIDE-only** — a `dialexis-extractor`/`-researcher`/`-triager`/`-deduper` subagent returns a manifest *fragment* as its final ```json block and NEVER writes `graph/` or a manifest. You (orchestrator) write fragments to `batches/<id>/fragments/`, run `merge_fragments` (folds per-entity upserts, dedups edges, auto-flags cross-source conflicts), then `commit → reconcile → validate`. Fan out only above threshold (≥3 sources, entity web-expansion, large audits); otherwise work inline. See `agents/SKILL_subagents.md`.
12. **Fast entity lookup — route explicit lookups to the zero-LLM script, don't grep.** When the user's message begins (case-insensitive) with **`lookup`**, **`look up`**, or **`tell me about`** followed by an entity name, do NOT explore with Grep/Glob/Read. Run `python3 /abs/path/to/run.py entity "<the remainder>"` in a **single** Bash call and relay its output verbatim. A dominant match prints the full entity record; an ambiguous query prints a ranked candidate list — present the list and let the user pick or re-run with a more exact name. Any other phrasing is a normal request → handle it the usual (LLM) way. Rationale: a plain entity lookup should take <1s, not a 30–60s agentic pass.
13. **One writer at a time; the fence is git.** Writers are serialized by the single user rather than assigned to a machine — several machines may ingest, and entities changing that you did not write is normal operation, not a violation (`validate` is the arbiter of soundness, not authorship). The Slack relay is also a writer; Cowork and every other consumer is read-only. Pull before mutating; commit + push after `commit → reconcile → validate` passes. Reads need no fence, and there is no lock. Drive is a one-way downstream mirror plus the shared `_inbox/` drop zone, never a sync channel. **The canonical statement of all of this — who may write, which commands mutate, and the fence discipline — is `agents/CONVENTIONS.md` ("One writer at a time"). Read it there rather than relying on this summary;** the design rationale and Google Drive history are in `DIALEXIS_SYSTEM.md` §6b.

---

## Common Commands

```bash
python3 run.py system_status          # Boot / status check
python3 run.py system_status tree     # Directory tree view
python3 run.py stage_inbox            # Stage inbox files into a batch (auto-parses .ics → entity ops)
python3 run.py commit batches/<id>/manifest.json  # Apply manifest
python3 run.py merge_fragments batches/<id>/fragments --out batches/<id>/manifest.json  # Fold subagent fragments (fan-out)
python3 run.py reconcile              # Unconditional convergence
python3 run.py validate               # Binding integrity gate (reads via the derived parse cache; re-parses any changed file from disk)
python3 run.py validate --no-cache    # Force a full live parse of every file, bypassing the cache (belt-and-suspenders audit)
python3 run.py health_check           # Full health suite
python3 run.py doctor                 # Read-only ENVIRONMENT check: interpreter, optional deps (playwright/pypdf/pytest), core, inbox, git fence, staged batches
python3 run.py analyze_database       # Read-only quality analysis
python3 run.py entity "<query>"       # Zero-LLM fuzzy entity lookup, ALL entity types (fast; names+aliases). See Key Rule 12.
python3 run.py resolve "<query>"      # Zero-LLM PERSON resolver (people only): middle-initial-insensitive identity + contextual narrowing (resolve-only)
# NOTE: `resolve` searches ONLY the person type. On a miss it now cross-checks the other types and
#   tells you if the name exists as an organization/project/system — a bare "No person resolved" was
#   being misread as "not in the model", which caused a duplicate-org upsert and a commit HALT.
#   Use `run.py entity "<name>"` whenever the target might not be a person.
python3 run.py resolve "<query>" --org CMG --email x@y --related project:GAMMA --co-mention "Name"  # Narrow an under-specified reference by context (flags ambiguous)
# NOTE: commit.py now ALSO auto-runs this resolver pre-apply on every person name (upsert canonical_name + person relationship targets),
#   building context from the op's edges + same-manifest co-mentions + an optional op "context" block. Bare/ambiguous names resolve to the
#   right stored identity or HALT with the candidate list (same first+last at the SAME top-level institution stays ambiguous under org-only
#   context). Fuller incoming names still UPGRADE the stored short form (not downgraded). See DIALEXIS_SYSTEM.md §6c.
# (No scan command for same-name collisions: they are detected LIVE on every resolve, ~60ms.
#  To record that two same-named people are genuinely DIFFERENT people, commit a symmetric
#  `Distinct from` relationship between them. That ruling then suppresses the pair for the
#  deduper and makes resolve_person report settled ambiguity instead of a suspected duplicate.
#  Retired: scan_ambiguities / _index/known_ambiguities.json. See DIALEXIS_SYSTEM.md §6c.)
python3 run.py sync_index             # Regenerate derived indices: entities.md + conflicts.md + index.sqlite (single parse pass). Authorized writer only.
python3 run.py sync_index --rebuild   # Force a cache-independent full rebuild of index.sqlite from disk, then the markdown indices.
python3 run.py index_store --status   # Report the derived cache's presence/size/staleness (read-only)
python3 run.py index_store            # Rebuild the machine-local index.sqlite from disk (~/.cache/dialexis/<repo-id>/; derived, safe to delete anytime)
python3 run.py prune_archive          # Dry-run: list archive/raw_inbox source copies >60 days old (destructive tool; safe by default)
python3 run.py prune_archive --apply  # Delete them; each deletion logged to archive/PRUNED_LOG.md (retention keyed off filename date, not mtime)
python3 run.py prune_archive --days 90 # Custom retention window
python3 <ABS>/run.py publish          # (publish host only) mirror graph/ markdown to <mirror-dir> after a clean pull + validate. Read-only consumer of the model.

# Kairos — agenda / recommendations / worker memory (see the dialexis-kairos skill for the contract)
python3 run.py kairos now [--time N] [--energy shallow|deep] [--mood FLAVOR]  # Recommend next action
python3 run.py kairos today [add|drop <id>]   # Manage the scarce today-set (max 3)
python3 run.py kairos review          # Kill-or-commit review pass
python3 run.py kairos prep            # Prep prompts for upcoming events
python3 run.py kairos done <id> [--how "…"]   # Complete an action + observation
python3 run.py kairos log "…"         # Freeform work observation
python3 run.py kairos capture <manifest.json> # Apply an actions/dreams capture manifest
python3 run.py kairos index           # Regenerate _index/agenda.md

# Web access (local scripts — use INSTEAD of cloud WebSearch/WebFetch tools)
python3 scripts/web_search.py "<query>" --num 10       # Web search via Fetcher's engine pool
python3 scripts/web_search.py --batch queries.txt      # Batch mode (one query per line)
python3 scripts/web_search.py "<query>" --delay 15     # Custom delay between requests
python3 scripts/web_fetch.py <url> --max-chars N       # Fetch and extract text from URL
python3 scripts/web_fetch.py <url> --render            # Force JS rendering (headless browser)
python3 scripts/web_fetch.py <url> --timeout 60        # Custom timeout
python3 scripts/web_fetch.py <url> --headers '{"Accept": "text/html"}'  # Custom headers

# Source tracking (light provenance — see agents/SKILL_web_research.md)
python3 scripts/web_fetch.py <url> --context "<entity>"   # Tag fetch with the entity it supports
python3 scripts/web_search.py "<q>" --context "<entity>"  # Tag search with the entity it supports
python3 scripts/web_fetch.py <url> --no-log               # Suppress ledger entry for this call
python3 run.py source_log report                          # Per-source WORKED/FAILED rollup
```

---

## Derived read cache (`~/.cache/dialexis/<repo-id>/index.sqlite`)

The read path (`entity`, `resolve`, `reconcile`, `validate`) is accelerated by a **derived SQLite
cache**: one prebuilt, pre-parsed artifact read in a single I/O instead of parsing every markdown
file per operation. It is **never a source of truth** — the markdown files ARE the database, and
every path falls back to a live parse on any `(mtime, size)` mismatch or SQLite error, so the cache
can make a read faster but never wrong. It is machine-local and gitignored, so a write on one
machine leaves the other's cache cold until its next read (slower, never wrong). Disposable:
`run.py index_store` or `sync_index --rebuild` regenerates it in ~0.5s, and
`index_store --status` reports presence and staleness.

Measurements and design rationale: `.claude/skills/dialexis-architecture/references/performance.md`
and `DIALEXIS_SYSTEM.md` §6d.

---

## Web Access Policy

> Built-in `WebSearch` is dead on the hosted gateway (400); built-in `WebFetch` works, but use
> the local scripts above so every call reaches the source ledger. **Retrieval is done by
> Fetcher's maintained modules** (`FETCHER_DIR`, default a sibling `Fetcher` checkout, via
> `scripts/fetcher_bridge.py`); if Fetcher is missing they exit 2 — there is no fallback code.
> Both scripts have `--self-test`. Engine pacing is Fetcher's governor; `--delay` (default 2s)
> only spaces batch queries.
>
> **JS-rendered pages** (React, Next.js, etc.) are auto-detected by `web_fetch.py`: if a 200
> HTML response yields <50 chars, it auto-retries with a headless browser. Use `--render` to
> force this. Requires: `pip install playwright && playwright install chromium`.
>
> **PDFs** are detected by content-type OR `%PDF-` magic bytes (servers mislabel them
> constantly) and extracted via `pypdf`. A scanned PDF with no text layer says so explicitly
> — that needs OCR, and re-fetching will not help.
>
> **Before concluding a source is BLOCKED, check capability: `run.py doctor`.** A missing
> optional dependency degrades *silently into a different-looking symptom* — no playwright
> makes JS pages look like the site blocked you (200, empty body); no pypdf makes every PDF
> look like a failed fetch (mojibake or bare title). Both have been misdiagnosed for real.
> Distinguish the four cases: **403/429** = genuinely blocked (route around it, e.g. Fetcher);
> **200 + empty** = needs `--render`; **401/SSO** = needs the user's credentials; **200 + only
> a title** = extraction miss, try an API or a different representation of the same content.
> Optional deps are declared in `requirements-optional.txt`.
>
> **Source tracking (light provenance):** Every `web_fetch.py` / `web_search.py` call
> automatically appends its outcome (WORKED / FAILED) to `archive/web_references/SOURCE_LOG.md`.
> When doing web research to **expand an entity**, pass `--context "<entity name>"` so the
> ledger records *what* the source was consulted for, and **end the session with a very
> succinct bullet report** of which sources WORKED and which FAILED (failed sources may still
> hold excellent info the user can access manually). See `agents/SKILL_web_research.md`.

