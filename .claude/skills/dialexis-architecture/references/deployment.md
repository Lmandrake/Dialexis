# Deployed background jobs (one host only)

Read this before touching either launchd job. The full runbook, verification commands, and
symptom→cause tables live in **`deploy/README.md`**; this file is the orientation plus the traps
that have actually bitten. Both jobs are installed from `__REPO__`-templated plists in `deploy/`.
Neither belongs on a second machine — `run.py publish` self-gates on `DIALEXIS_PUBLISH_HOST` so it no-ops
elsewhere.

| Job | Runs | Cadence | Role |
|-----|------|---------|------|
| `com.dialexis.publish` | `run.py publish` | `StartInterval` 1523s (~25 min) | **Consumer** — mirrors `graph/` markdown one-way to `<mirror-dir>` for non-Dialexis readers. Gates on right-host + clean ff-pull + passing `validate`. Content-hash change detection (never mtime — Drive restamps it). Manifest-driven prune, so `_inbox/` is never touched. |
| `com.dialexis.slack` | `slack/bot.py` | `KeepAlive` | **Writer** — persistent Socket Mode websocket relaying chat DMs to `claude -p`. Mutates through the normal manifest pipeline and honors the git fence. |

Logs: `output/publish.log|.err`, `output/slack.log|.err` (all gitignored).

## Three traps

**1. `KeepAlive`, not `StartInterval`, for the relay.** It is a long-lived websocket, not a periodic
task. If its host sleeps the connection drops, and because Socket Mode has no
delivery target when nothing is connected, the symptom is *total silence* in Slack with nothing
written to any log. Diagnose that with `ps` / `launchctl print`, not by reading code.

**2. The relay needs `.venv/bin/python`.** Unlike the stdlib-only core, `bot.py` imports
`slack_bolt` and `dotenv`, which live only in the uv-managed venv. `/usr/bin/python3` — correct for
the publisher — fails on import here.

**3. launchd's `PATH` is minimal.** launchd supplies only
`/usr/bin:/bin:/usr/sbin:/sbin`, so any tool `claude -p` depends on that lives elsewhere is not
found, and the failure can surface as an unrelated-looking API error rather than a missing-command
message. The plist extends `PATH` for this reason; compare against an interactive shell's `PATH`
when a job fails that works by hand.

## Constraints

- **Only one machine may run the relay.** Two Socket Mode connections make Slack load-balance
  events between them, so DMs land nondeterministically on whichever host caught them — possibly
  one with a stale checkout.
- **The relay is a real writer** (as of 2026-08-25), which is the one crack in the no-lock
  rationale: a relayed request can arrive mid-mutation. Small exposure — one machine, low traffic — but not zero. Reach for a lock only if
  concurrent-write symptoms actually appear, and check `output/slack.log` before diagnosing an
  unexplained change as corruption. See `agents/CONVENTIONS.md`, "One writer at a time."
- **To reproduce a launchd-only failure, don't guess at the environment — run a throwaway job.**
  That is how trap 3 was found; the bug is invisible from any interactive shell.
