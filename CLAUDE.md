# DIALEXIS v2.0 — LLM Agent Instructions

Dialexis is a personal knowledge base where **markdown entity files in `graph/`
ARE the database**, an LLM is the semantic runtime, and stdlib-only Python enforces
deterministic postconditions. **The Claude session IS the runtime** — there is no server or
binary to launch. The `viz/` directory is a SEPARATE React/Flask visualizer; never start it
when asked to "run dialexis."

Three layers, one guiding principle:

| Layer | Who | Does what |
|-------|-----|-----------|
| DECIDE | You (LLM) | Semantic judgments only. Output = a transaction *manifest*. |
| ENFORCE | Python (`commit.py`) | Applies the manifest atomically; writes reciprocal edges + mechanical fields. |
| DETECT & HALT | Python (`validate.py`) | Reads from disk; halts loudly on any violation. |

> **Guiding principle: never ask an LLM to guarantee a deterministic postcondition.**

---

## STARTUP PROTOCOL — "run dialexis"

When the user says **"run dialexis"**, **"start dialexis"**, or any variation:

1. Run `python3 run.py system_status` (read-only; report entity count, inbox, staged batches)
2. If batches are staged: mention them, ask whether to process
3. If the inbox has files: mention them, ask whether to ingest
4. Otherwise: ask what's needed (ingest, health check, maintenance, query, Kairos, etc.)

This is the boot/orient sequence — it mutates nothing. `viz/` is never the answer to "run dialexis."

---

## Cardinal invariants (always in force)

These few rules never bend. The full 13 Key Rules, command catalog, and web policy live in
**`agents/KEY_RULES.md`** — read it whenever you're doing real work.

1. **Never hand-edit `graph/`.** All mutations go through a manifest →
   `commit → reconcile → validate`. Reading entity files is always safe.
2. **Halting is success.** A `HALT` is the safety design working — fix the manifest or
   extend the registry and re-run; never patch files to get past it. Empty manifests are
   never acceptable (every source → ≥1 op). Pre-read targets before you upsert.
3. **`reconcile` + `validate` are unconditional after every mutation** — actually run the
   scripts; never self-certify in their place.
4. **Invoke `run.py` by absolute path; no persistent `cd`** (confine any `cd` to a subshell).
5. **One writer at a time, and the fence is git.** Writers are serialized by the single user,
   not assigned to a machine — several machines may ingest, and finding that another
   machine committed since your last read is normal, not a violation. The Slack relay
   is also a writer; every other consumer (Cowork included) is read-only. **Pull before
   mutating; commit + push after `commit → reconcile → validate` passes.** Reads need no
   fence, and there is no lock. Drive is a one-way downstream mirror of the markdown plus a
   shared `_inbox/` drop zone — never a sync channel, never a writer target, and never the
   answer to "run dialexis". Full doctrine: **`agents/CONVENTIONS.md`** ("One writer at a
   time"); rationale and history: `DIALEXIS_SYSTEM.md` §6b.

---

## Skills router (task → skill)

Four skills under `.claude/skills/`, owned by Claude Code (the read/write runtime; Cowork is
read-only and does not own them). Route by intent:

| The user wants to… | Skill | Deep reference |
|--------------------|-------|----------------|
| Look up / resolve / query the model (read-only) | **dialexis-access** | Key Rule 12 |
| Ingest a source, extract facts, add/merge/correct, web-expand | **dialexis-ingestion** | `agents/SKILL_ingest.md`, `SKILL_subagents.md`, `SKILL_web_research.md` |
| Understand/maintain/profile/visualize/evolve the machinery | **dialexis-architecture** | `agents/SKILL_health.md`, `DIALEXIS_SYSTEM.md` |
| Agenda / "what should I do now" / actions / dreams / event prep | **dialexis-kairos** | the skill is self-contained |

If a skill doesn't fire, fall back to **`agents/KEY_RULES.md`** (rules, commands, web policy).

---

## Where things are

- **There is NO repo-local `inbox/`.** The drop zone is `paths.resolve_inbox()`; on the publish host
  `DIALEXIS_INBOX_DIR` points at `<shared-inbox>`. A file left in a repo-local
  `inbox/` is invisible to `system_status` and `stage_inbox` and will never be ingested.
- Layout: `README.md`. Paths/fence/confidence/dates: `agents/CONVENTIONS.md`. Vocabulary:
  `agents/ENTITY_TYPE_REGISTRY.md`. Architecture: `DIALEXIS_SYSTEM.md`.
