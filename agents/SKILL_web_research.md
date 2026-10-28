# Skill: Web Research & Source Tracking

Expanding an entity from the open web, with lightweight provenance so we build a growing
understanding of where our information comes from — and which sources fail.

## Overview

Dialexis reaches the web through two local scripts. Built-in `WebSearch` is dead on the hosted
gateway; built-in `WebFetch` works, but prefer these because only they write the source ledger.
**Retrieval is done by Fetcher** (`FETCHER_DIR`, default a sibling `Fetcher` checkout), imported in-process via
`scripts/fetcher_bridge.py` — its daemon is never started. If Fetcher is absent the scripts exit 2
with a fix message (no fallback code); `run.py doctor` reports it.

- `scripts/web_search.py` — Fetcher's engine pool (rotates past soft-blocking engines, learned pacing)
- `scripts/web_fetch.py` — Fetcher's route ladder (API substitution, TLS-fingerprint escalation,
  redirect stubs, JS render, labelled archive mirror); PDFs are re-retrieved through Fetcher's FILE
  handler for pypdf text. Fetcher's Chrome-session rung is disabled here. playwright and pypdf are
  **OPTIONAL deps that fail silently into
  misleading symptoms** — run `python3 run.py doctor` before concluding a source is blocked.
  See `requirements-optional.txt`.

Every call to either script **automatically appends its outcome** to an append-only ledger at
`archive/web_references/SOURCE_LOG.md`. This is *light provenance*, not full traceability: it
records outcome + target + context, never response bodies.

This split follows the Dialexis principle: **Python guarantees the persistent postcondition**
(the ledger can't drift from what actually happened), while **the LLM does the semantic and
presentation work** (deciding a session is an entity expansion, and writing the succinct report).

## Protocol

### 1. Search — tag with the entity
```bash
python3 scripts/web_search.py "<query>" --num 10 --context "<Entity Name>"
```
`--context` records *what* the search was for. Use the same context string across all calls in
one expansion so the ledger groups cleanly.

### 2. Fetch candidate sources — tag with the entity
```bash
python3 scripts/web_fetch.py <url> --context "<Entity Name>"
python3 scripts/web_fetch.py <url> --context "<Entity Name>" --render   # force JS render
```
A fetch is recorded **WORKED** when it returns HTTP 200 with non-empty extracted text, and
**FAILED** otherwise (error, non-200, timeout, or empty render). A search is **WORKED** when it
returns ≥1 result.

Use `--no-log` only for throwaway/debug calls that should not enter the ledger.

### 3. Extract facts
Synthesize the retrieved content into entity facts. If the material is substantial, save a
per-entity research file under `archive/web_references/<slug>_web_research_<date>.md` (the
existing convention) — the ledger is a summary index, not a replacement for that.

### 4. Deliver a very succinct WORKED / FAILED bullet report — ON SCREEN
At the end of the expansion, always show the user a short bullet list, e.g.:

```
Sources — Dagmar Nightingale:
  WORKED:
    - researcher.example (personal site, full bio + publications)
    - texuslab.org (lab affiliation)
  FAILED:
    - university.example/researcher/dagmar_nightingale (Playwright timeout — worth a manual look)
```

Keep it terse. The point is so the user can **manually chase down FAILED sources** that may still
hold excellent information.

**Say WHY it failed, not just that it did — "blocked" is four different problems and only one
of them is a block.** Reporting them all as "blocked" hides the ones you can fix yourself:

| Symptom | Cause | What to do |
|---|---|---|
| **403 / 429**, or a rendered "Checking your browser..." page | genuinely refused (bot detection, IP blocklist, rate limit) | the only true block — route around it (Fetcher, a mirror, an API), or hand the URL to the user |
| **200 with an empty/tiny body** | JS-rendered page | `--render`; if that errors, `run.py doctor` (playwright may be missing) |
| **401, or a redirect to an SSO/login page** | authentication required | needs the user's credentials — do NOT keep retrying |
| **200 but only a `<title>`, or mojibake** | extraction miss, not a fetch failure | try another representation: an export/JSON API, the PDF, a mirror. Verify `doctor` shows pypdf for PDFs |

Concrete worked example, all four hit in one session (an example session):
`astrobiology.com` PDF → true 403 block; three agency.example / nas.agency.example pages → JS-render,
recovered in full once playwright was installed; a funding-portal page → institutional SSO, unrecoverable without
credentials; `arxiv.org/abs/...` → extraction miss, worked around via the arXiv export API.
Four "failures", one actual block. Reporting them undifferentiated would have buried three
fixable problems.

### 5. (Optional) Review the growing rollup
```bash
python3 run.py source_log report
```
Aggregates the whole ledger by source domain (fetches) and engine (searches): attempts, worked,
failed, success rate — plus a distinct list of FAILED targets for manual follow-up.

## Notes

- The ledger lives OUTSIDE `graph/`, so it needs no manifest and breaks no rules.
- Logging is fully guarded: a logging failure can never break a fetch or search.
- Self-tests (`--self-test`) call the internal functions directly and do **not** write to the
  ledger, so verification never pollutes it.
