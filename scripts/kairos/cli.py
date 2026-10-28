"""cli.py - Kairos CLI dispatch. Routes subcommands to module functions.

Each mutating command: builds manifest -> commit_manifest() -> reconcile_all() -> validate_all().
All support --dry-run mode.
"""
from __future__ import annotations
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import paths
from commit import commit_manifest, CommitError
from reconcile import reconcile_all
from validate import validate_all


def _print_help() -> None:
    """Print full usage: subcommands plus the capture manifest schema and enums.

    Enum values are pulled live from kairos.parse so this help can never drift
    from the actual schema the committer enforces.
    """
    from kairos.parse import ESTIMATES, ENERGIES, FLAVORS, STATUSES, TODAY_CAP

    def _enum(vals) -> str:
        return " | ".join(sorted(vals))

    print("usage: kairos <subcommand> [args...]")
    print("\nSubcommands:")
    print("  capture <json>  Apply a capture manifest (actions/dreams/events)")
    print("  ingest          Consume the Lodestar intent queue (airlock/kairos-queue.jsonl) directly")
    print("  now             Recommend what to do next")
    print("  prep            Show prep prompts / record a prep-action")
    print("  today [add|drop <id>]  Manage the today-set")
    print("  review          Kill-or-commit review pass")
    print("  done <id>       Complete an action")
    print("  log <text>      Record a freeform observation")
    print("  profile         Show/rebuild worker profile")
    print("  index           Regenerate agenda index")
    print("\nGlobal flags:")
    print("  -h, --help, help   Show this help")
    print("  --dry-run          On mutating commands, preview without writing")
    print("\ncapture manifest schema (JSON: {\"operations\": [ ... ]}):")
    print("  op: upsert_action   Create/update an action. Omit `id` to create")
    print("                      (auto-assigned act_NNNN); include `id` to update.")
    print("    fields: title, next_action, project, for_event, for_dream, due,")
    print("            estimate, energy, flavor, status")
    print("  op: archive_action  Terminate an action. fields: id, status")
    print("                      (done | released | expired)")
    print("  Dreams/events are entity upserts (op: upsert_entity, entity_type:")
    print("  dream|event) — see agents/SKILL_ingest.md for entity manifest fields.")
    print("\naction field enums:")
    print(f"  estimate:  {_enum(ESTIMATES)}")
    print(f"  energy:    {_enum(ENERGIES)}")
    print(f"  flavor:    {_enum(FLAVORS)}")
    print(f"  status:    {_enum(STATUSES)}   (TODAY_CAP = {TODAY_CAP})")
    print("  project / for_event / for_dream / due: string or null")


def main(argv: list[str]) -> int:
    """Entry point for `python3 run.py kairos <subcommand> [args...]`."""
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        _print_help()
        return 0

    subcommand = argv[1]
    sub_argv = argv[1:]

    dispatch = {
        "capture": _cmd_capture,
        "ingest": _cmd_ingest,
        "now": _cmd_now,
        "prep": _cmd_prep,
        "today": _cmd_today,
        "review": _cmd_review,
        "done": _cmd_done,
        "log": _cmd_log,
        "profile": _cmd_profile,
        "index": _cmd_index,
    }

    if subcommand not in dispatch:
        print(f"ERROR: unknown subcommand '{subcommand}'", file=sys.stderr)
        return 1

    return dispatch[subcommand](sub_argv)


def _cmd_capture(argv: list[str]) -> int:
    """Apply a capture manifest (JSON file or stdin)."""
    if len(argv) < 2:
        print("usage: kairos capture <manifest.json>", file=sys.stderr)
        return 1

    manifest_path = Path(argv[1])
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    dry_run = "--dry-run" in argv

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if dry_run:
        print("DRY RUN — would commit:")
        for op in manifest.get("operations", []):
            print(f"  {op.get('op')}: {op.get('title') or op.get('canonical_name')}")
        return 0

    return _commit_and_finalize(manifest)


def _cmd_ingest(argv: list[str]) -> int:
    """Consume Lodestar's structured intent queue directly (no natural-language parsing;
    see kairos/ingest.py's module docstring). `--dry-run` reports what would be consumed
    without writing anything, including the receipts file."""
    from kairos.ingest import ingest_queue

    dry_run = "--dry-run" in argv
    report = ingest_queue(dry_run=dry_run)

    verb = "would consume" if dry_run else "Ingested"
    print(f"{'DRY RUN — ' if dry_run else ''}{verb} {len(report.consumed)} intent(s); "
          f"{report.already_done} already done; {report.already_refused} already refused.")
    for intent_id, reason in sorted(report.refused.items()):
        print(f"  REFUSED {intent_id}: {reason}")
    for intent_id, reason in report.skipped:
        print(f"  SKIPPED {intent_id}: {reason}", file=sys.stderr)

    # The three counts that matter, reported separately and never collapsed into one number
    # (see kairos/ingest.py's module docstring): ingested, refused permanently this run, and
    # left pending for retry this run.
    print(f"Summary — ingested: {len(report.consumed)}, "
          f"refused (permanent): {len(report.refused)}, "
          f"left for retry (transient): {len(report.skipped)}")
    return 0


def _cmd_now(argv: list[str]) -> int:
    """Recommend what to do next based on query parameters."""
    from kairos.parse import parse_ledger
    from kairos.harvest import harvest_events
    from kairos.score import score_candidates, dream_eligible, Query

    # Parse optional args
    time_available = None
    energy = None
    mood = None
    i = 1
    while i < len(argv):
        if argv[i] == "--time" and i + 1 < len(argv):
            time_available = int(argv[i + 1])
            i += 2
        elif argv[i] == "--energy" and i + 1 < len(argv):
            energy = argv[i + 1]
            i += 2
        elif argv[i] == "--mood" and i + 1 < len(argv):
            mood = argv[i + 1]
            i += 2
        else:
            i += 1

    actions = parse_ledger(paths.AGENDA_LEDGER)
    events = harvest_events(horizon_days=7)
    query = Query(time_available=time_available, energy=energy, mood=mood)
    candidates = score_candidates(actions, events, query)

    qualified = [c for c in candidates if not c.disqualified]
    if not qualified:
        print("No actions match your current constraints.")
        if dream_eligible(candidates, query):
            print("(This might be a good time for dreaming.)")
        return 0

    print("Recommendations:\n")
    for i, c in enumerate(qualified[:5], 1):
        a = c.action
        due_str = f" [due {a.due}]" if a.due else ""
        print(f"  {i}. {a.title}{due_str} ({a.estimate}, {a.energy})")
        print(f"     Next: {a.next_action}")
        print(f"     Score: {c.score:.1f}")
        print()

    if dream_eligible(candidates, query):
        print("(Light load — consider exploring a dream.)")

    return 0


def _cmd_prep(argv: list[str]) -> int:
    """Show prep prompts or record a prep-action."""
    from kairos.parse import parse_ledger
    from kairos.harvest import harvest_events
    from kairos.prep import find_prep_eligible, resolve_expired_prep

    actions = parse_ledger(paths.AGENDA_LEDGER)
    events = harvest_events(horizon_days=7)

    # Show expired prep that needs resolution
    expired = resolve_expired_prep(actions, events)
    if expired:
        print("Prep actions past their event (needs resolution):")
        for r in expired:
            print(f"  - {r.action.title} (for {r.event_name})")
        print()

    # Show prep-eligible events
    prompts = find_prep_eligible(events, actions)
    if prompts:
        print("Events that could use preparation:")
        for p in prompts:
            print(f"  - {p.event_name} ({p.kind}) — {p.days_until}d away")
    elif not expired:
        print("No events need preparation right now.")

    return 0


def _cmd_today(argv: list[str]) -> int:
    """Manage the today-set."""
    from kairos.parse import parse_ledger

    actions = parse_ledger(paths.AGENDA_LEDGER)

    if len(argv) < 2 or argv[1] in ("show", "list"):
        today_set = [a for a in actions if a.status == "today"]
        if today_set:
            print(f"Today ({len(today_set)}/3):\n")
            for a in today_set:
                print(f"  - [{a.id}] {a.title}")
                print(f"    Next: {a.next_action}")
        else:
            print("Nothing set for today. Use 'kairos today add <id>' to set items.")
        return 0

    if argv[1] == "add" and len(argv) >= 3:
        action_id = argv[2]
        manifest = {"manifest_version": "2.0", "operations": [
            {"op": "upsert_action", "id": action_id, "status": "today"}
        ]}
        return _commit_and_finalize(manifest)

    if argv[1] == "drop" and len(argv) >= 3:
        action_id = argv[2]
        manifest = {"manifest_version": "2.0", "operations": [
            {"op": "upsert_action", "id": action_id, "status": "active"}
        ]}
        return _commit_and_finalize(manifest)

    print("usage: kairos today [add|drop <id>]", file=sys.stderr)
    return 1


def _cmd_review(argv: list[str]) -> int:
    """Run the kill-or-commit review pass."""
    from kairos.parse import parse_ledger
    from kairos.review import run_review, clear_stale_today

    actions = parse_ledger(paths.AGENDA_LEDGER)

    # Clear stale today flags first
    stale_ops = clear_stale_today(actions)
    if stale_ops:
        manifest = {"manifest_version": "2.0", "operations": stale_ops}
        print(f"Clearing {len(stale_ops)} stale today flag(s)...")
        result = _commit_and_finalize(manifest, quiet=True)
        if result != 0:
            return result
        # Reload
        actions = parse_ledger(paths.AGENDA_LEDGER)

    # Run review
    review_result = run_review(actions)

    if review_result.needs_decision:
        print("NEEDS DECISION (carried too long):\n")
        for a in review_result.needs_decision:
            print(f"  [{a.id}] {a.title} (carried {a.carry_count}x)")
            print(f"    Next: {a.next_action}")
            print(f"    Options: kill | commit | reshape")
            print()

    if review_result.carried:
        print(f"Carried ({len(review_result.carried)} actions incremented):")
        for a in review_result.carried:
            print(f"  [{a.id}] {a.title} (now {a.carry_count}x)")

    if review_result.healthy:
        print(f"\nHealthy: {len(review_result.healthy)} action(s) on track.")

    if not any([review_result.needs_decision, review_result.carried]):
        print("All actions are healthy. Nothing to review.")

    # Persist carry_count updates
    updated = review_result.needs_decision + review_result.carried
    if updated:
        ops = [{"op": "upsert_action", "id": a.id,
                "carry_count": a.carry_count, "last_review": a.last_review}
               for a in updated]
        manifest = {"manifest_version": "2.0", "operations": ops}
        return _commit_and_finalize(manifest, quiet=True)

    return 0


def _cmd_done(argv: list[str]) -> int:
    """Complete an action and optionally record an observation."""
    if len(argv) < 2:
        print("usage: kairos done <id> [--how 'description']", file=sys.stderr)
        return 1

    action_id = argv[1]
    how_text = None
    if "--how" in argv:
        idx = argv.index("--how")
        if idx + 1 < len(argv):
            how_text = argv[idx + 1]

    # Archive the action
    manifest = {"manifest_version": "2.0", "operations": [
        {"op": "archive_action", "id": action_id, "status": "done"}
    ]}
    result = _commit_and_finalize(manifest)
    if result != 0:
        return result

    # Record observation if --how provided
    if how_text:
        from kairos.observe import Observation, record_observation
        obs = Observation(
            timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            kind="completion",
            text=how_text,
            action_id=action_id,
        )
        filepath = record_observation(obs)
        print(f"Observation recorded: {filepath.name}")

    print(f"Action {action_id} marked done.")
    return 0


def _cmd_log(argv: list[str]) -> int:
    """Record a freeform observation."""
    if len(argv) < 2:
        print("usage: kairos log <text>", file=sys.stderr)
        return 1

    text = " ".join(argv[1:])
    from kairos.observe import Observation, record_observation
    obs = Observation(
        timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        kind="log",
        text=text,
    )
    filepath = record_observation(obs)
    print(f"Logged: {filepath.name}")
    return 0


def _cmd_profile(argv: list[str]) -> int:
    """Show or rebuild the worker profile."""
    from kairos.profile import rebuild_profile

    rebuild = "--rebuild" in argv

    if rebuild or not paths.WORKER_PROFILE.exists():
        path = rebuild_profile()
        print(f"Profile {'rebuilt' if rebuild else 'generated'}: {path.name}")

    if paths.WORKER_PROFILE.exists():
        print(paths.WORKER_PROFILE.read_text(encoding="utf-8"))
    else:
        print("No profile yet. Record observations with 'kairos done' or 'kairos log'.")
    return 0


def _cmd_index(argv: list[str]) -> int:
    """Regenerate the agenda index."""
    from kairos.index import regenerate_index
    path = regenerate_index()
    print(f"Index regenerated: {path}")
    return 0


def _commit_and_finalize(manifest: dict, quiet: bool = False) -> int:
    """Commit a manifest and run reconcile + validate."""
    try:
        result = commit_manifest(manifest)
        if not quiet:
            entities = result.get("entities_written", 0)
            actions = result.get("actions_written", 0)
            archived = result.get("actions_archived", 0)
            parts = []
            if entities:
                parts.append(f"{entities} entit{'y' if entities == 1 else 'ies'}")
            if actions:
                parts.append(f"{actions} action(s)")
            if archived:
                parts.append(f"{archived} archived")
            if parts:
                print(f"Committed: {', '.join(parts)}")
    except CommitError as e:
        print(f"HALT: {e}", file=sys.stderr)
        return 1

    reconcile_all()
    v = validate_all()
    if v.get("errors"):
        print(f"WARNING: validation errors: {v['errors']}", file=sys.stderr)
        return 1
    return 0
