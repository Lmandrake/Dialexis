"""ingest.py - Structured ingest door for Lodestar's kairos-queue.jsonl.

Contract, halt conditions, and postconditions are documented per-function below; the
short version:

- Reads `airlock/kairos-queue.jsonl` in the Lodestar repo (see its
  `bin/lib/intents.py`) and builds Kairos records DIRECTLY from its already-structured
  fields. Nothing here parses natural language or calls a model: the queue already carries
  typed fields (title/next_action/estimate/... for an action; text/felt for an observation;
  canonical_name/start for an event), so doing an LLM pass over it would spend a model call
  to make clean data worse.
- Writes through the SAME manifest -> commit_manifest -> reconcile -> validate tail as
  every other Kairos mutation (KAIROS_SPEC.md §7-8). Kairos remains the only writer of
  Kairos records; the only thing this module writes outside `graph/`/`agenda/` is
  the receipts file below, and a receipt records completion, it never asserts a fact.
- IDEMPOTENT by intent_id: consuming the same queue twice creates nothing the second time.
- Never executes, fetches, or sends anything derived from queue content. A payload is read
  as inert data end to end and mapped onto known manifest ops; there is no code path from a
  queue line to a subprocess, a network call, or an outbound message.

PERMANENT vs TRANSIENT failures. Most failures here are transient (a lock, an unrelated
graph-state validator complaint, an I/O hiccup) and MUST stay un-receipted so the intent
keeps retrying — that is the existing `skipped` behavior, unchanged. But a small class of
failure is PERMANENT: the intent's own payload is structurally incapable of ever succeeding
(the worked example: an EVENT intent with neither `canonical_name` nor `title`, so
`upsert_entity` can never be given a name). Retrying a permanent refusal can never help, and
because the queue is append-only, the offending line can never be edited away — so it would
retry forever and Lodestar's board would show it as "queued for Kairos" indefinitely.

Permanent refusals are recorded in the receipts file as a sibling map, `{"refused":
{intent_id: reason}}`, alongside the existing `{"consumed": [...]}` list — see
`_write_receipts`/`read_refused`. A refused id is never re-attempted (checked the same way
`consumed` ids are), so idempotency holds for refusals exactly as it does for successes. The
classification is deliberately narrow and evidence-based, not a broad exception-message
match: when it is unclear whether a failure is permanent, it is treated as TRANSIENT, because
a wrongly-permanent refusal silently drops a real intent — the safe direction is always
"leave it pending, let it retry."

See `ingest_queue()` for the entry point.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import paths
from naming import slug as _slug
from commit import commit_manifest, CommitError

try:
    from kairos.parse import ESTIMATES, PREP_HORIZON
    from kairos.harvest import find_event_by_name
    from kairos.observe import Observation, ObserveError, record_observation
except ImportError:
    from parse import ESTIMATES, PREP_HORIZON
    from harvest import find_event_by_name
    from observe import Observation, ObserveError, record_observation


OBSERVATION = "observation"
ACTION = "action"
EVENT = "event"


@dataclass
class IngestReport:
    """What one ingest run did, so a caller can report a checkable contact surface rather
    than a bare exit code.

    Three run-scoped counts must always be reported separately, never collapsed into one
    number (see module docstring's PERMANENT vs TRANSIENT section): `consumed` (ingested),
    `refused` (permanently refused this run), and `skipped` (left pending for retry)."""
    consumed: list = field(default_factory=list)        # intent_ids newly marked done
    skipped: list = field(default_factory=list)         # (intent_id, reason) tuples — TRANSIENT;
                                                          # these stay pending and will retry
    refused: dict = field(default_factory=dict)          # intent_id -> reason, PERMANENT this run
    already_done: int = 0                                # intents the receipts already covered
    already_refused: int = 0                             # intents already recorded as refused


def _queue_path(root) -> Path:
    return Path(root) / "airlock" / "kairos-queue.jsonl" if root else paths.LODESTAR_QUEUE


def _receipts_path(root) -> Path:
    return Path(root) / "airlock" / "kairos-receipts.json" if root else paths.LODESTAR_RECEIPTS


def read_queue(root=None) -> list:
    """Every well-formed line on the queue, oldest first. A malformed line is SKIPPED, not
    fatal — mirrors Lodestar's own `intents.read_queue` exactly: the file is append-only and
    syncs across two machines, so one transport artifact (a half-written or conflict-marked
    line) must never strand every good intent behind it."""
    path = _queue_path(root)
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("intent_id") and row.get("kind"):
                    out.append(row)
    except (IOError, OSError):
        return []
    return out


def read_receipts(root=None) -> set:
    """Intent ids already consumed. Empty on anything unreadable — absent/corrupt means
    "nothing ingested yet", the same fail-safe direction Lodestar's own reader uses, so a
    missing receipts file can never be misread as "everything is done"."""
    try:
        with open(_receipts_path(root), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (IOError, OSError, ValueError):
        return set()
    ids = doc.get("consumed") if isinstance(doc, dict) else None
    return set(i for i in (ids or []) if isinstance(i, str))


def read_refused(root=None) -> dict:
    """Intent ids already PERMANENTLY refused, mapped to their human-readable reason. Empty
    on anything unreadable — same fail-safe direction as `read_receipts`: a missing/corrupt
    file means "nothing refused yet", never "everything is refused". A reader (like this
    module's own second run, or Lodestar's board) that only understands `consumed` and knows
    nothing of this sibling key keeps working unchanged, because this key is additive."""
    try:
        with open(_receipts_path(root), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    refused = doc.get("refused") if isinstance(doc, dict) else None
    if not isinstance(refused, dict):
        return {}
    return {k: v for k, v in refused.items() if isinstance(k, str) and isinstance(v, str)}


def _write_receipts(root, consumed: set, refused: dict | None = None) -> None:
    """Write `{"consumed": [...]}` exactly as before, plus a sibling `"refused"` map only
    when it is non-empty — so a run that never refuses anything writes byte-for-byte the
    same shape it always has, and a Lodestar reader that only knows `consumed` never sees a
    key it doesn't expect until there is actually something to tell it."""
    path = _receipts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"consumed": sorted(consumed)}
    if refused:
        doc["refused"] = dict(refused)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


#: The board's raw estimate bucket -> Kairos's own vocabulary (parse.ESTIMATES). Computed
#: fresh here rather than trusting the queue's `kairos_estimate` field, because that field
#: was computed by Lodestar against ITS OWN mapping table (bin/lib/intents.KAIROS_ESTIMATE),
#: which — as of 2026-09-03, before this change — still collapses "2m" into "≤15m". Kairos
#: now has a finer bucket (parse.ESTIMATES gained "≤2m"); recomputing the mapping here closes
#: the loss immediately without waiting for Lodestar to ship an updated table. If Lodestar's
#: `kairos_estimate` is ever the only field present (an older/unrecognized raw bucket), it is
#: trusted verbatim as long as it is already valid Kairos vocabulary.
BOARD_ESTIMATE = {
    "2m": "≤2m",
    "15m": "≤15m",
    "1h": "short",
    "3h": "session",
    "deep": "deep",
}

DEFAULT_ESTIMATE = "short"    # parse.Action's own default; used only when nothing usable is given
DEFAULT_ENERGY = "shallow"
DEFAULT_FLAVOR = "admin"


def estimate_for(payload: dict) -> str:
    """Board estimate bucket -> Kairos estimate, the finest mapping Kairos can offer today."""
    raw = (payload.get("estimate") or "").strip()
    if raw in BOARD_ESTIMATE:
        return BOARD_ESTIMATE[raw]
    kairos_val = (payload.get("kairos_estimate") or "").strip()
    if kairos_val in ESTIMATES:
        return kairos_val
    return DEFAULT_ESTIMATE


def observation_from_payload(payload: dict, queued_at: str = "") -> Observation:
    """Map a Lodestar OBSERVATION intent payload directly onto a Kairos Observation.

    Field provenance (see Lodestar bin/kairos_sync.py `observation_for()`): `title`,
    `decision` (done|working|blocked|dropped), `at`, `felt`, `actual_minutes` (-1 means
    genuinely unknown — Lodestar's own honesty convention, carried through rather than
    guessed at), `note`, `source`. No field here is inferred by an LLM; every value is
    already typed on the wire.
    """
    timestamp = (payload.get("at") or "").strip() or (queued_at or "").strip()
    title = (payload.get("title") or payload.get("item_id") or "(untitled)").strip()
    decision = (payload.get("decision") or "").strip()
    text = f"[{decision}] {title}" if decision else title
    note = (payload.get("note") or "").strip()
    if note:
        text = f"{text} — {note}"
    source = (payload.get("source") or "").strip()
    if source:
        text = f"{text} (source: {source})"

    actual_minutes = payload.get("actual_minutes")
    if not isinstance(actual_minutes, int) or actual_minutes < 0:
        actual_minutes = None   # -1/absent means unknown; never reported as a real duration

    return Observation(
        timestamp=timestamp,
        kind="completion" if decision == "done" else "log",
        text=text,
        actual_minutes=actual_minutes,
        felt=(payload.get("felt") or "").strip() or None,
    )


def action_op_from_payload(payload: dict) -> dict:
    """One `upsert_action` manifest op, built directly from an ACTION intent's fields.

    for_event/lead_days honesty (KAIROS_SPEC.md §6; acceptance-fanout design §5-6): `due` is
    NEVER read from the payload for an event-anchored action. It is derived here from
    `event.start - lead_days` by looking up the anchor event already on disk
    (`harvest.find_event_by_name`). If the event entity doesn't exist yet, or exists with no
    parseable start date (a "declared", not yet "confirmed" event), `due` stays None —
    reporting a due Kairos cannot back up would be exactly the kind of number this project's
    rule forbids ("never report a number you cannot mean").
    """
    for_event_name = (payload.get("for_event") or "").strip()
    for_event_ref = None
    due = payload.get("due") or None   # plain (non-event) actions may carry a soft due directly

    if for_event_name:
        for_event_ref = f"[[{_slug(for_event_name)}]]"
        due = None   # never trust a payload-supplied due once an event anchor is present
        lead_days = payload.get("lead_days")
        if not isinstance(lead_days, int):
            lead_days = PREP_HORIZON
        found = find_event_by_name(for_event_name)
        if found is not None:
            due = (found.start_date - timedelta(days=lead_days)).isoformat()

    title = (payload.get("title") or payload.get("item_id") or "").strip()
    next_action = (payload.get("next_action") or "").strip()
    if not next_action:
        # Lodestar's fan-out proposals (acceptance-fanout design §3) carry a title, lead_days,
        # estimate/energy/flavor — but no next_action; Kairos's ledger schema REQUIRES one
        # (KAIROS_SPEC.md §4, parse.validate_action). Falling back to the title keeps the
        # record valid rather than halting the whole ingest on a schema gap that is
        # Lodestar's proposal shape to close, not this queue line's fault. See this task's
        # report for the "spec gap" note.
        next_action = title

    op = {
        "op": "upsert_action",
        "title": title,
        "next_action": next_action,
        "for_event": for_event_ref,
        "due": due,
        "estimate": estimate_for(payload),
        "energy": (payload.get("energy") or DEFAULT_ENERGY),
        "flavor": (payload.get("flavor") or DEFAULT_FLAVOR),
        "status": "active",
    }
    if payload.get("project"):
        op["project"] = payload["project"]
    if payload.get("for_dream"):
        op["for_dream"] = payload["for_dream"]
    return op


def event_op_from_payload(payload: dict) -> dict:
    """One `upsert_entity` (entity_type=event) op, built directly from an EVENT intent.

    Honesty mirrors the action side: if `start` is absent (a "declared" event whose calendar
    sense hasn't landed yet — acceptance-fanout design §5), no start attribute is written at
    all. That is the deliberate absence of a fact, not a guess at one; `harvest.py`/`prep.py`
    already treat an event with no parseable start date as "cannot offer a due date yet",
    which is the correct downstream behavior.
    """
    canonical_name = (payload.get("canonical_name") or payload.get("title") or "").strip()
    op = {"op": "upsert_entity", "entity_type": "event", "canonical_name": canonical_name,
          "attributes": []}
    source_type = (payload.get("source_type") or "manual").strip()
    start = (payload.get("start") or "").strip()
    if start:
        fact = {"key": "start", "value": start, "source": source_type}
        if (payload.get("basis") or "").strip() == "declared":
            fact["confidence"] = "LOW"   # an inferred date is never presented as a stated one
        op["attributes"].append(fact)
    kind = (payload.get("kind") or "").strip()
    if kind:
        op["attributes"].append({"key": "kind", "value": kind, "source": source_type})
    return op


def event_permanent_refusal_reason(payload: dict, op: dict) -> str | None:
    """Return a human-readable reason if this EVENT intent can NEVER be ingested no matter
    how many times it is retried, else None.

    The one evidenced case: neither `canonical_name` nor `title` was ever set on the
    payload (`event_op_from_payload` already folds both into `op["canonical_name"]`, so an
    empty result here means both were empty/missing). `commit.py`'s `upsert_entity`
    correctly refuses that with "missing or empty 'canonical_name'" — and refuses it
    identically every time, because the queue line's payload cannot change. This check runs
    BEFORE `commit_manifest` is ever called: it is a pure read of the intent's own already-
    typed fields, not a parse of the CommitError's message, so it stays true even if that
    message's wording changes later.

    Deliberately narrow: this function does not attempt to classify any other failure shape
    (a validator complaint tied to unrelated graph state, an event whose `start` failed to
    parse, an action with an empty title — see `tests/test_ingest.py`'s
    `test_one_bad_intent_never_blocks_a_good_one`, which pins that an empty-title ACTION
    stays a TRANSIENT, retried failure). Anything not explicitly recognized here is left for
    the caller to treat as transient, per this module's "unsure -> retry" rule.
    """
    if not (op.get("canonical_name") or "").strip():
        return ("this event intent's payload has neither 'canonical_name' nor 'title' set, "
                "so Kairos has no name to give the entity. The queue is append-only and this "
                "payload can never change, so retrying it would fail the exact same way "
                "forever — refusing it now instead of leaving it stuck as permanently "
                "'pending'.")
    return None


def ingest_queue(root=None, dry_run: bool = False) -> IngestReport:
    """Read Lodestar's `kairos-queue.jsonl` and create Kairos records for every intent not
    already in the receipts file; record each success back to receipts.

    Args:
        root: the Lodestar repo root (queue/receipts live under `<root>/airlock/`). Defaults
            to `paths.LODESTAR_ROOT`. Tests pass a temp dir standing in for a Lodestar
            checkout; production callers omit it.
        dry_run: report what WOULD happen; write nothing (no observation files, no commits,
            no receipts). Mirrors every other mutating Kairos command's `--dry-run`.

    Idempotency (the load-bearing property; see the module docstring and KAIROS_SPEC.md
    §7's "verifiable" bar): an intent_id already present in receipts is never re-processed,
    so running this twice over an unchanged queue creates nothing the second time —
    "replaying a queue books the same flight twice" is exactly the failure this guards
    against. Idempotency holds even within a single run: each ACTION/EVENT intent is
    committed with ITS OWN `commit_manifest()` call rather than batched into one manifest,
    so one malformed or halting intent can only ever cost itself, never its neighbors on the
    same queue — a poisoned line is reported and left un-consumed (it will be retried, and
    can keep failing forever without blocking anything else), never silently dropped and
    never allowed to block a good line that happened to queue near it.

    A malformed line, an unrecognized `kind`, a non-object `payload`, or a per-intent
    CommitError/ObserveError is reported in `IngestReport.skipped` and leaves that intent_id
    NOT in receipts — it stays visible as still-pending, matching this codebase's "halting is
    success" stance rather than silently discarding a real problem. The one exception is a
    PERMANENT refusal (see module docstring and `event_permanent_refusal_reason`): that
    intent_id IS written to receipts, under `"refused"` rather than `"consumed"`, so it stops
    being retried forever without ever being reported as done.
    """
    rows = read_queue(root)
    done = read_receipts(root)
    refused_map = read_refused(root)
    report = IngestReport()
    newly_done = set()
    newly_refused = {}
    # Ids whose success depends on the graph/ledger passing validate — tracked separately
    # from observation ids, which are already durably written the moment record_observation
    # returns and must never be un-consumed by a LATER validate failure on unrelated
    # action/event ops. Un-consuming an already-written observation would make it retry into
    # observe.py's own immutability guard forever (same timestamp -> ObserveError), which is
    # worse than just leaving its receipt written.
    graph_ids = set()

    for row in rows:
        intent_id = row.get("intent_id")
        if intent_id in done:
            report.already_done += 1
            continue
        if intent_id in refused_map:
            # Idempotency for refusals mirrors idempotency for successes: never re-attempt,
            # never re-derive, never write twice.
            report.already_refused += 1
            continue

        kind = row.get("kind")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            report.skipped.append((intent_id, "payload is not an object"))
            continue

        try:
            if kind == OBSERVATION:
                obs = observation_from_payload(payload, row.get("queued", ""))
                if not dry_run:
                    record_observation(obs)
                report.consumed.append(intent_id)
                newly_done.add(intent_id)

            elif kind == ACTION:
                op = action_op_from_payload(payload)
                if dry_run:
                    report.consumed.append(intent_id)
                else:
                    commit_manifest({"manifest_version": "2.0", "operations": [op]})
                    report.consumed.append(intent_id)
                    newly_done.add(intent_id)
                    graph_ids.add(intent_id)

            elif kind == EVENT:
                op = event_op_from_payload(payload)
                refusal = event_permanent_refusal_reason(payload, op)
                if refusal is not None:
                    # Never attempted: nothing was committed, so there is nothing to
                    # validate or roll back. Checked (and recorded) even under dry_run,
                    # since this is a pure read of the payload, not a mutation preview.
                    report.refused[intent_id] = refusal
                    newly_refused[intent_id] = refusal
                elif dry_run:
                    report.consumed.append(intent_id)
                else:
                    commit_manifest({"manifest_version": "2.0", "operations": [op]})
                    report.consumed.append(intent_id)
                    newly_done.add(intent_id)
                    graph_ids.add(intent_id)

            else:
                report.skipped.append((intent_id, "unknown kind %r" % (kind,)))

        except ObserveError as e:
            report.skipped.append((intent_id, "observation collision: %s" % e))
        except CommitError as e:
            report.skipped.append((intent_id, "CommitError: %s" % e))
        except Exception as e:   # a single bad line must never abort the whole batch
            report.skipped.append((intent_id, "%s: %s" % (type(e).__name__, e)))

    if graph_ids and not dry_run:
        # Mandatory tail (KAIROS_SPEC.md §7/§8): every mutating Kairos command runs
        # commit -> reconcile -> validate. Observation-only runs never reach here — the
        # worklog is outside reconcile/validate's scope, so skipping this keeps an
        # observation-only ingest cheap.
        from reconcile import reconcile_all
        from validate import validate_all
        reconcile_all()
        v = validate_all()
        if v.get("errors"):
            # Same stance as cli.py's _commit_and_finalize: a nonzero validate means
            # investigate, not self-certify. The commits already landed on disk (that part
            # is unconditional, same as every other Kairos mutation), but receipts is OUR
            # bookkeeping and we withhold it so these intents stay honestly "pending" rather
            # than being marked done against a graph that just failed its own invariant gate.
            # Scoped to graph_ids ONLY — an observation consumed earlier in this same run is
            # already durably written and must not be un-consumed by an unrelated failure
            # (see the graph_ids comment above).
            report.skipped.extend((iid, "post-commit validate reported errors: %s" % v["errors"])
                                  for iid in graph_ids)
            report.consumed = [i for i in report.consumed if i not in graph_ids]
            newly_done -= graph_ids

    if (newly_done or newly_refused) and not dry_run:
        _write_receipts(root, done | newly_done, {**refused_map, **newly_refused})

    return report
