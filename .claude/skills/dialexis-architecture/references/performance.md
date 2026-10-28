# Dialexis — Performance reference

Read this when asked to make Dialexis faster or cheaper, or to justify a design choice on
performance grounds. **Measure before optimizing** — every rejected idea below was rejected because
a measurement said so, not because it sounded wrong.

```bash
python3 <ABS>/run.py profile_readpath "<query>" --repeat 5   # splits a full-DB lookup into I/O, parse, score
```

## Current, verified (2026-08-31, 4,654 entities)

End-to-end process time, i.e. what the user actually waits for:

| Operation | Time |
|---|---|
| `entity "<name>"` (warm cache) | 0.34s |
| `resolve "<name>"` | 0.17s |
| `validate` | 0.5s |
| `audit_reciprocals` | 0.6s |
| `reconcile` (no-op convergence) | 1.5s |
| `analyze_database` | 1.8s |
| `audit_semantic_relationships` | 5.1s |
| `health_check` (full suite) | 6.2s |

`index.sqlite` at this scale is ~13.5 MB.

**Don't conflate the cache query with the command.** The SQLite name/alias query is ~3ms and a
whole-model pass ~60ms, but a *command* also pays interpreter start plus registry parse (~10ms) and
output formatting. Quote ~3ms for the lookup mechanism and ~0.2–0.35s for the user-visible call;
older notes blurred the two.

## Historical baseline (2026-08-17, ~4,388 entities) — pre-cache, pre-local-disk

Kept because the *conclusions* below rest on it, not because the numbers still describe the system.
Both bottlenecks it identified have since been removed, so **re-run `profile_readpath` before citing
any of this as current.**

- Read path ~4.9s per full-DB call ≈ 48% disk I/O + 50% markdown parse + 2% score. Cold ≈ warm, so
  it was CPU-bound work over all files, not first-touch disk latency.
- Interpreter + registry startup ~10.7ms (negligible).
- Keys-only pre-read (name + aliases + identifiers + attribute keys/values + rel targets, dropping
  prose) is ~80% smaller than full markdown (people median 722B → 112B).
- Source sizes entering context on ingest: pptx ~17MB, pdf ~4MB, png ~1.5MB, eml ~354KB, ics ~5KB,
  md ~7KB, docx ~34KB, txt ~3KB.

## Upgrade verdicts

**1. Derived lookup index — the fix. ✅ Shipped (`index_store.py`).** Avoiding a parse of every file
to answer about one was the whole win. One table serves two roles: `lookup_index()` (cheap
name/alias/type/status rows) and `iter_entities()` (full `Entity` from `blob_json`, gated on each
file's `(mtime, size)`). The governing principle is **"cache accelerates, live model is truth"** —
every path falls back to a live parse on any mismatch or SQLite error, so the cache can make a read
faster but never wrong. It lives machine-local at `~/.cache/dialexis/<repo-id>/index.sqlite`
(`paths.index_cache_dir`, relocatable via `DIALEXIS_INDEX_DIR`) and is never committed: a ~13MB
fully-rewritten derived file costs more to share than to regenerate. Consequence: a write on one
machine leaves the other's cache cold until its next read — slower, never wrong.

**2. Keys-only pre-read (`--keys`) + terse default output** with a short human-readable digest.
Composes with the index; the big remaining ingest-context win.

**3. Slim `CLAUDE.md` / `MEMORY.md` + skill extraction.** Reduces per-turn resident tokens, which
are paid on *every* turn regardless of task.

**4. Per-source context clear** for heavy binary sources (pptx/pdf/image), not light text
(ics/txt) — break-even is keyed to source size.

**5. Batch independent tool calls** into one turn.

### Rejected by evidence

- **Faster matcher / prefilter.** Score was 2% of cost; optimizing it saves nothing.
- **Resident daemon.** Startup is ~10ms — there is nothing to amortize, and `resolve.py`/`entity.py`
  already give fast deterministic lookup.

## Hardware / layout history

The move to local disk on 2026-08-24 removed the ~48% I/O tax and the 0-byte sync-lag reads. The
out-flow half shipped the same day: Drive became a periodic one-way *mirror* of the raw markdown via
the `com.dialexis.publish` launchd job (see `deployment.md`).

Drive originally forced the index cache out of the repo tree — it restamped mtimes on download,
defeating the `(mtime, size)` gate entirely, so a synced copy cost a 10MB round-trip per mutation
for a 0% hit rate. That hazard is gone, but the per-machine choice stands on its own merits.
