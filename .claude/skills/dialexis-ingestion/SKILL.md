---
name: "dialexis-ingestion"
description: "ONLY for a Claude session the owner launched in ~/dev/Dialexis. Any other session (BETA, Lodestar, a subagent, a `claude -p`) must NOT load this: write one file with the facts and their source into <shared-inbox> and stop. Turn real-world information into new DIALEXIS understanding — the write path for the user's knowledge graph at ~/dev/Dialexis. Use whenever the user wants to ingest a source (email, calendar .ics, PDF, deck, notes, transcript, web page), extract entities and facts from material, resolve/merge people and organizations, add relationships, record a meeting or decision, correct or retract a stored fact, or expand an entity from the web. Covers the full harness: stage → pre-read → manifest → commit → reconcile → validate, plus entity resolution, semantic connectivity, and fan-out. For pure read/lookup use dialexis-access; for how the machinery is built/maintained use dialexis-architecture."
---

# Dialexis — Ingestion (the write path)

> ## 🛑 STOP. Are you a session the owner launched in `~/dev/Dialexis`?
> **If not: do not activate Dialexis ingestion. Just submit an item to the inbox and be done!**
> Write ONE file with the facts and their source into `<shared-inbox>`, tell the
> user it is queued, and move on. Do not work around the writer gate, do not retry, and do not
> spawn `claude -p` or any agent inside the repo to do it for you. Reads (`run.py entity`,
> `resolve`) are fine. There is one door into Dialexis, and it is the inbox.

Everything that *changes* the graph lives here. **DECIDE, don't ENFORCE:** you make semantic
judgments and emit them as a transaction **manifest**; `commit.py` applies it atomically, writes
reciprocal edges and mechanical fields, and HALTS on anything it cannot place.

## Guardrails (full text: `agents/CONVENTIONS.md`, `agents/KEY_RULES.md`)

1. **Never edit `graph/` directly.** Manifest → `commit → reconcile → validate`. Reading is safe.
2. **Halting is success.** Fix the manifest or extend the registry and re-run; never patch files.
3. **Empty manifests are never acceptable** — every source → ≥1 op.
4. **Pre-read before you upsert** — the #1 cause of avoidable halts.
5. **`reconcile` + `validate` are unconditional** — run them; never self-certify.
6. **The registry is a closed vocabulary** (`agents/ENTITY_TYPE_REGISTRY.md`); extending it is deliberate (dialexis-architecture).
7. **Invoke `run.py` by absolute path; no persistent `cd`** (subshell only). `run.py commit` resolves a repo-relative manifest path from any CWD; pass other scripts' file arguments absolutely.
8. **Git fence:** `git pull --ff-only` before staging; after validate passes, commit the files you changed and `git push`. Never force-push past a rejection. Only user-driven sessions and the Slack relay write. See `agents/CONVENTIONS.md` "One writer at a time".
9. **The artifact you are given IS the scope** — never swap in a fuller source; ask instead. See `agents/SKILL_ingest.md`.

The inbox is not `inbox/` — `run.py system_status` shows what `stage_inbox` actually sees
(`agents/CONVENTIONS.md` "Environment and paths").

## The harness — follow it exactly, don't skip a script

Steps 1, 4, 5, 6 are deterministic scripts; steps 2–3 are your semantic work.

```
Ingestion progress:
- [ ] 1. stage_inbox — captured <batch_id> (stop if inbox empty)
- [ ] 2. Pre-read every target entity file; reconciled incoming vs stored
- [ ] 3. Wrote batches/<batch_id>/manifest.json (every source → ≥1 op)
- [ ] 4. commit — exited clean (on HALT: fix manifest/registry, re-run; never hand-edit)
- [ ] 5. reconcile — converged
- [ ] 6. validate — exited 0
```

```bash
# 1. STAGE — archive sources verbatim, open a batch, delete verified inbox originals.
python3 <ABS>/run.py stage_inbox      # capture the <batch_id>; if inbox empty, stop.
#   .ics calendar files and org-chart PDFs auto-seed a manifest.json (attendee/member
#   person ops + event/org ops). If a seed manifest exists, READ and APPEND — never overwrite.

# 2. PRE-READ — for each entity you'll touch, glob+read its existing file under
#   graph/. Reconcile incoming vs stored values BEFORE emitting:
#   same value → skip; new key → add. A different value on an existing key: if the field is
#   multi-valued by nature (center, affiliation, org code, posting, project membership) ACCUMULATE —
#   add the edge or a qualified key ("Role - ALPHA"), never a conflict; if the source is an
#   authoritative update (org chart) `prune_attribute` the old key first; only a genuinely
#   single-valued contested fact (a title, a date, a spelling) gets `add_conflict`.
#   Correction ops (all exact-match, HALT if absent): prune_attribute / prune_alias /
#   prune_relationship / prune_event / prune_perspective (titles or match spec) / prune_note
#   (exact substring, HALT if ambiguous). Identity ops (merge/prune) go before dependent upserts.
#   Resolve identity here (see "Entity resolution" below) so you upsert the RIGHT entity.

# 3. MANIFEST — write batches/<batch_id>/manifest.json. ZERO writes to graph/.
#   Every source → ≥1 op. Create RELATIONSHIPS, not just attributes (see "Semantic connectivity").

# 4. COMMIT — atomic apply; HALTS with nothing written on any violation.
python3 <ABS>/run.py commit batches/<batch_id>/manifest.json
#   On HALT: surface the message, fix the manifest or extend the registry, re-run.
#   Never hand-fix files to get past a HALT.

# 5. RECONCILE — UNCONDITIONAL convergence (reciprocal edges, placement, format, indices);
#   also auto-cleans the committed batch dir. Idempotent.
python3 <ABS>/run.py reconcile

# 6. VALIDATE — BINDING gate, reads from disk. Nonzero = NOT done; investigate & fix.
python3 <ABS>/run.py validate
```

After a clean run, give the user a terse 1–3 line digest of what was ingested.

## Where the rules live — read before a non-trivial manifest

- **`agents/SKILL_ingest.md`** — the harness contract, the complete manifest **field table** (exact
  names; `commit.py` rejects wrong ones), perspectives (holder vs `inferred` — the most under-used
  part of the model), the scope rule, entity resolution (`run.py resolve`, `Distinct from`),
  org-chart/.ics seed manifests, op ordering, and rename/merge/prune examples.
- **`agents/CONVENTIONS.md`** — confidence and dates, pre-read outcomes, relationships-not-just-
  attributes, the `document` type, mechanical fields, halting, paths, the writer fence.
- **`agents/SKILL_subagents.md`** — fan-out (≥3 sources, many web targets, large audits) via the
  DECIDE-only `dialexis-extractor`/`-researcher`/`-triager`/`-deduper` and `merge_fragments`.
  Below threshold, work inline.
- **`agents/SKILL_web_research.md`** — web expansion with `scripts/web_search.py` /
  `scripts/web_fetch.py --context "<Entity>"`, the WORKED/FAILED source ledger, and how to diagnose
  a failure before calling it "blocked" (`run.py doctor`).
- `agents/ENTITY_TYPE_REGISTRY.md` (types, inverse pairs, source→confidence floors) and
  `agents/ENTITY_FILE_FORMAT.md` (entity markdown, document subtypes).

## Success criteria

Done only when `reconcile` converged and `validate` exited 0 after the mutation, the git fence is
closed, and you gave the digest. A clean HALT you surfaced (rather than worked around) is also a
correct outcome.
