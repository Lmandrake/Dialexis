"""test_halt_log.py - a refused manifest must leave a durable record.

Standalone: `python3 tests/test_halt_log.py`. No pytest required.

These tests re-point paths.OUTPUT themselves rather than relying on the pytest isolation
fixture, so they behave identically in both invocation modes and never write into the real
output/ directory.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paths
import commit

# An entity type the registry does not define. Rejected before anything touches the model,
# so these tests need no working graph.
_ILLEGAL_MANIFEST = {
    "manifest_version": "2.0",
    "operations": [{
        "op": "upsert_entity",
        "entity_type": "not_a_real_entity_type",
        "canonical_name": "Nobody At All",
        "confidence": "HIGH",
        "source": "manual",
    }],
}


def _halt_once(tmp: Path, name: str) -> int:
    manifest = tmp / name
    manifest.write_text(json.dumps(_ILLEGAL_MANIFEST), encoding="utf-8")
    return commit.main(["commit", str(manifest)])


def test_halt_is_recorded_to_a_durable_log():
    """Nothing else in the system records a HALT: commit exits nonzero and prints to stderr,
    batches/ is gitignored and reconcile auto-cleans it. Without this record the only signal
    saying how often the enforcer actually catches something is discarded on every run, which
    is why a retrospective measurement of the enforcement layer was impossible."""
    saved = paths.OUTPUT
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths.OUTPUT = tmp / "output"
            rc = _halt_once(tmp, "bad.json")
            assert rc == 1, "an unregistered entity type must HALT"

            log = paths.OUTPUT / "halt_log.jsonl"
            assert log.exists(), "the HALT left no record"
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1, f"expected one record, got {len(lines)}"

            rec = json.loads(lines[0])
            assert rec["violation_class"] in ("CommitError", "RegistryError", "NamingError"), rec
            assert "not_a_real_entity_type" in rec["message"], rec
            assert rec["at"], "no timestamp"
            assert rec["manifest"].endswith("bad.json"), rec
    finally:
        paths.OUTPUT = saved


def test_halt_log_appends_rather_than_overwrites():
    """The log is the evidence base for a rate, so a second refusal must not erase the first."""
    saved = paths.OUTPUT
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths.OUTPUT = tmp / "output"
            _halt_once(tmp, "first.json")
            _halt_once(tmp, "second.json")

            log = paths.OUTPUT / "halt_log.jsonl"
            # Assert existence before reading, so a missing log reports as one FAIL rather
            # than raising FileNotFoundError past the harness's AssertionError handler.
            assert log.exists(), "the HALTs left no record"
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 2, f"expected two records, got {len(lines)}"
            names = [json.loads(x)["manifest"] for x in lines]
            assert names[0].endswith("first.json") and names[1].endswith("second.json"), names
    finally:
        paths.OUTPUT = saved


def test_recording_failure_cannot_change_the_exit_code():
    """Logging is best-effort: a refusal must still report 1 even if the log cannot be
    written, or an observability feature would start deciding commit outcomes."""
    saved = paths.OUTPUT
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # A file where the directory should be, so mkdir/open both fail.
            blocked = tmp / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            paths.OUTPUT = blocked
            rc = _halt_once(tmp, "bad.json")
            assert rc == 1, "a logging failure must not mask or alter the HALT"
    finally:
        paths.OUTPUT = saved


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
