"""Tests for kairos/ingest.py - the structured door onto Lodestar's kairos-queue.jsonl."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import paths
from registry import load_registry
from parse import parse_ledger
from observe import list_observations

import ingest


def setup_workspace(tmpdir: Path) -> Path:
    """A throwaway Dialexis workspace (mirrors test_cli.py's setup_workspace) PLUS a
    throwaway "Lodestar repo" standing in for the Lodestar checkout's airlock/. Returns the
    Lodestar root; callers write airlock/kairos-queue.jsonl under it directly."""
    paths.ROOT = tmpdir
    paths.WORKING_MODEL = tmpdir / "graph"
    paths.INDEX_DIR = paths.WORKING_MODEL / "_index"
    paths.AGENTS = tmpdir / "agents"
    paths.AGENDA = tmpdir / "agenda"
    paths.AGENDA_LEDGER = paths.AGENDA / "actions.md"
    paths.AGENDA_ARCHIVE = paths.AGENDA / "archive"
    paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"
    paths.WORKER_PROFILE = paths.AGENDA / "worker_profile.md"

    real_agents = Path(__file__).parent.parent.parent.parent / "agents"
    paths.AGENTS.mkdir(parents=True)
    shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
    paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

    reg = load_registry()
    for entity_type, info in reg._entity_types.items():
        paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)
    paths.AGENDA.mkdir(parents=True, exist_ok=True)
    paths.AGENDA_ARCHIVE.mkdir(parents=True, exist_ok=True)
    paths.AGENDA_WORKLOG.mkdir(parents=True, exist_ok=True)
    paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)

    lodestar_root = tmpdir / "fake_lodestar"
    (lodestar_root / "airlock").mkdir(parents=True, exist_ok=True)
    paths.LODESTAR_ROOT = lodestar_root
    paths.LODESTAR_QUEUE = lodestar_root / "airlock" / "kairos-queue.jsonl"
    paths.LODESTAR_RECEIPTS = lodestar_root / "airlock" / "kairos-receipts.json"
    return lodestar_root


def write_queue(root: Path, rows: list) -> None:
    lines = [json.dumps(r, sort_keys=True) for r in rows]
    (root / "airlock" / "kairos-queue.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def obs_intent(intent_id: str, **payload) -> dict:
    base = {"item_id": intent_id, "title": "Untitled", "decision": "done", "at": "",
            "by": "", "estimate": "", "kairos_estimate": "", "felt": "",
            "actual_minutes": -1, "started": "", "log": [], "note": "", "source": ""}
    base.update(payload)
    return {"intent_id": intent_id, "kind": "observation", "queued": "2026-09-03T10:00:00-07:00",
            "payload": base}


def action_intent(intent_id: str, **payload) -> dict:
    base = {"title": "Do the thing", "next_action": "Open the doc", "estimate": "short",
            "energy": "shallow", "flavor": "admin"}
    base.update(payload)
    return {"intent_id": intent_id, "kind": "action", "queued": "2026-09-03T10:00:00-07:00",
            "payload": base}


def event_intent(intent_id: str, **payload) -> dict:
    base = {"canonical_name": "Some Event"}
    base.update(payload)
    return {"intent_id": intent_id, "kind": "event", "queued": "2026-09-03T10:00:00-07:00",
            "payload": base}


def test_observation_ingest_creates_worklog_entry_and_receipt():
    print("Testing observation ingest -> worklog + receipt...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [obs_intent("int_obs1", title="Nick: call scheduled",
                                       decision="done", felt="energized", at="2026-09-03T09:50:00")])

        report = ingest.ingest_queue()
        assert report.consumed == ["int_obs1"]
        assert report.skipped == []

        obs = list_observations()
        assert len(obs) == 1
        assert obs[0].felt == "energized"
        assert "Nick: call scheduled" in obs[0].text

        receipts = json.loads(paths.LODESTAR_RECEIPTS.read_text())
        assert receipts == {"consumed": ["int_obs1"]}
    print("✓")


def test_idempotent_replay_creates_nothing_twice():
    """Pins the named failure mode: 'replaying a queue books the same flight twice.'"""
    print("Testing idempotent replay...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [
            obs_intent("int_obs1", title="First"),
            action_intent("int_act1", title="Book the flight"),
        ])

        report1 = ingest.ingest_queue()
        assert sorted(report1.consumed) == ["int_act1", "int_obs1"]
        assert report1.already_done == 0

        # Replay the identical queue (as a re-run, a re-synced machine, or a rebase replay
        # would do) without ever calling ingest a third way — same file, same call.
        report2 = ingest.ingest_queue()
        assert report2.consumed == []
        assert report2.already_done == 2

        # Exactly one worklog file and one action — not two "flights booked."
        assert len(list_observations()) == 1
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].title == "Book the flight"
    print("✓")


def test_action_ingest_creates_ledger_entry():
    print("Testing action ingest -> ledger entry...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [action_intent("int_act1", title="Book lodging",
                                          next_action="Reserve the block",
                                          estimate="15m", energy="shallow", flavor="admin")])

        report = ingest.ingest_queue()
        assert report.consumed == ["int_act1"]

        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].title == "Book lodging"
        assert actions[0].next_action == "Reserve the block"
        assert actions[0].estimate == "≤15m"
    print("✓")


def test_action_next_action_falls_back_to_title():
    """Lodestar's fan-out proposals (acceptance-fanout design §3) don't carry next_action;
    Kairos's schema requires one. The fallback keeps the record valid instead of halting."""
    print("Testing action next_action fallback...", end=" ")
    with TemporaryDirectory() as tmp:
        setup_workspace(Path(tmp))
        op = ingest.action_op_from_payload({"title": "Book flight for Harbor City",
                                             "estimate": "15m"})
        assert op["next_action"] == "Book flight for Harbor City"
    print("✓")


def test_for_event_action_derives_due_from_event_start():
    print("Testing for_event due derivation...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [
            event_intent("int_evt1", canonical_name="Harbor Kickoff", start="2027-03-10"),
            action_intent("int_act1", title="Prep slides", for_event="Harbor Kickoff",
                          lead_days=3),
        ])

        report = ingest.ingest_queue()
        assert sorted(report.consumed) == ["int_act1", "int_evt1"]

        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].for_event == "[[harbor_kickoff]]"
        assert actions[0].due == "2027-03-07"   # 2027-03-10 - 3 days
    print("✓")


def test_for_event_action_no_due_when_event_unknown():
    """The event hasn't been sensed yet ('declared', not 'confirmed') — due must stay
    unknown rather than a guessed date."""
    print("Testing for_event with unknown event start...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [action_intent("int_act1", title="Prep slides",
                                          for_event="Some Future Trip", lead_days=3,
                                          due="2099-01-01")])   # payload due MUST be ignored

        ingest.ingest_queue()
        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].for_event == "[[some_future_trip]]"
        assert actions[0].due is None
    print("✓")


def test_event_ingest_creates_event_entity_with_start():
    print("Testing event ingest creates entity...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [event_intent("int_evt1", canonical_name="Atlas Kickoff",
                                         start="2027-03-10", kind="conference")])

        report = ingest.ingest_queue()
        assert report.consumed == ["int_evt1"]

        path = paths.entity_dir("events") / "atlas_kickoff.md"
        assert path.exists()
        content = path.read_text()
        assert "2027-03-10" in content
    print("✓")


def test_event_ingest_no_start_no_guessed_date():
    print("Testing event ingest with unknown start writes no date...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [event_intent("int_evt1", canonical_name="Vague Trip")])

        ingest.ingest_queue()
        from harvest import find_event_by_name
        assert find_event_by_name("Vague Trip") is None   # entity exists but has no start
        path = paths.entity_dir("events") / "vague_trip.md"
        assert path.exists()
    print("✓")


def test_malformed_queue_line_skipped_not_fatal():
    print("Testing malformed line is skipped, not fatal...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        good = obs_intent("int_obs1", title="Good one")
        (root / "airlock" / "kairos-queue.jsonl").write_text(
            "not json at all\n" + json.dumps(good) + "\n{\"missing\": \"kind\"}\n",
            encoding="utf-8")

        report = ingest.ingest_queue()
        assert report.consumed == ["int_obs1"]
        assert len(list_observations()) == 1
    print("✓")


def test_unknown_kind_skipped():
    print("Testing unknown kind is skipped...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [{"intent_id": "int_x1", "kind": "mystery", "payload": {}}])

        report = ingest.ingest_queue()
        assert report.consumed == []
        assert report.skipped == [("int_x1", "unknown kind 'mystery'")]
    print("✓")


def test_dry_run_writes_nothing():
    print("Testing dry-run writes nothing...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [obs_intent("int_obs1"), action_intent("int_act1")])

        report = ingest.ingest_queue(dry_run=True)
        assert sorted(report.consumed) == ["int_act1", "int_obs1"]
        assert not paths.LODESTAR_RECEIPTS.exists()
        assert list_observations() == []
        assert parse_ledger(paths.AGENDA_LEDGER) == []
    print("✓")


def test_estimate_finer_bucket_mapping():
    print("Testing estimate mapping uses the finer bucket...", end=" ")
    assert ingest.estimate_for({"estimate": "2m"}) == "≤2m"
    assert ingest.estimate_for({"estimate": "15m"}) == "≤15m"
    # Stale Lodestar-side kairos_estimate (pre-dates the finer bucket) is ignored in favor
    # of recomputing from the verbatim board estimate.
    assert ingest.estimate_for({"estimate": "2m", "kairos_estimate": "≤15m"}) == "≤2m"
    # No usable estimate at all -> the same default parse.Action itself uses.
    assert ingest.estimate_for({}) == "short"
    print("✓")


def test_one_bad_intent_never_blocks_a_good_one():
    """A poison intent in the same run must not block a good one, and must not get
    silently marked done either."""
    print("Testing one bad intent doesn't block a good one...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [
            action_intent("int_good", title="Fine task"),
            # Deliberately halts commit.py: an unknown estimate value would be caught by
            # validate_action inside commit_manifest.
        ])
        # Sabotage the queue with a line whose payload will make commit_manifest halt.
        rows = [json.loads(l) for l in paths.LODESTAR_QUEUE.read_text().splitlines()]
        rows.append({"intent_id": "int_bad", "kind": "action",
                     "payload": {"title": "", "next_action": "", "estimate": "nope"}})
        write_queue(root, rows)

        report = ingest.ingest_queue()
        assert "int_good" in report.consumed
        bad_reasons = [r for i, r in report.skipped if i == "int_bad"]
        assert bad_reasons and "CommitError" in bad_reasons[0]

        actions = parse_ledger(paths.AGENDA_LEDGER)
        assert len(actions) == 1
        assert actions[0].title == "Fine task"

        # Re-running must not re-create the good one, and must keep retrying the bad one.
        report2 = ingest.ingest_queue()
        assert report2.already_done == 1
        assert any(i == "int_bad" for i, _ in report2.skipped)
        assert len(parse_ledger(paths.AGENDA_LEDGER)) == 1
    print("✓")


def test_event_with_no_name_is_permanently_refused_not_retried():
    """The real worked example: an event intent with no canonical_name and no title can
    never be ingested (upsert_entity correctly refuses it), so re-running must not keep
    retrying it forever — it goes into receipts' `refused` map instead, distinctly from
    `consumed`, and a second run must not re-attempt it or double-write anything."""
    print("Testing nameless event is permanently refused...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [
            {"intent_id": "int_nameless", "kind": "event",
             "payload": {"event_key": "mail-abc123", "location": "Harbor City",
                         "start": "2027-03-10", "state": "declared"}},
            action_intent("int_good", title="Fine task"),
        ])

        report = ingest.ingest_queue()
        assert report.consumed == ["int_good"]
        assert "int_nameless" in report.refused
        assert "canonical_name" in report.refused["int_nameless"]
        assert report.skipped == []   # a permanent refusal is not a transient skip

        receipts = json.loads(paths.LODESTAR_RECEIPTS.read_text())
        assert receipts["consumed"] == ["int_good"]
        assert set(receipts["refused"].keys()) == {"int_nameless"}

        # Second run: neither re-attempted, nor consumed, nor refused a second time.
        report2 = ingest.ingest_queue()
        assert report2.consumed == []
        assert report2.refused == {}
        assert report2.already_done == 1
        assert report2.already_refused == 1

        receipts2 = json.loads(paths.LODESTAR_RECEIPTS.read_text())
        assert receipts2 == receipts   # byte-identical: no double-write

        from harvest import find_event_by_name
        # Nothing was ever committed for the refused intent.
        assert not (paths.entity_dir("events") / "mail-abc123.md").exists()
    print("✓")


def test_receipts_shape_unchanged_when_nothing_refused():
    """A run that never refuses anything must still write the exact same
    `{"consumed": [...]}` shape as before this feature existed — no stray empty `"refused"`
    key for an old-style Lodestar reader that only understands `consumed`."""
    print("Testing receipts shape is unchanged with no refusals...", end=" ")
    with TemporaryDirectory() as tmp:
        root = setup_workspace(Path(tmp))
        write_queue(root, [obs_intent("int_obs1")])
        ingest.ingest_queue()
        receipts = json.loads(paths.LODESTAR_RECEIPTS.read_text())
        assert receipts == {"consumed": ["int_obs1"]}
    print("✓")


def run_tests():
    test_observation_ingest_creates_worklog_entry_and_receipt()
    test_idempotent_replay_creates_nothing_twice()
    test_action_ingest_creates_ledger_entry()
    test_action_next_action_falls_back_to_title()
    test_for_event_action_derives_due_from_event_start()
    test_for_event_action_no_due_when_event_unknown()
    test_event_ingest_creates_event_entity_with_start()
    test_event_ingest_no_start_no_guessed_date()
    test_malformed_queue_line_skipped_not_fatal()
    test_unknown_kind_skipped()
    test_dry_run_writes_nothing()
    test_estimate_finer_bucket_mapping()
    test_one_bad_intent_never_blocks_a_good_one()
    test_event_with_no_name_is_permanently_refused_not_retried()
    test_receipts_shape_unchanged_when_nothing_refused()
    print("\n✓ All ingest tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
