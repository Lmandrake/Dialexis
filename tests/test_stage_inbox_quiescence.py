"""test_stage_inbox_quiescence.py - the Drive-inbox mid-sync guard.

Standalone: `python3 tests/test_stage_inbox_quiescence.py`. No pytest required.
"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paths
import stage_inbox


def test_snapshot_reflects_content():
    with TemporaryDirectory() as tmp:
        f = Path(tmp) / "a.txt"
        f.write_bytes(b"hello")
        size, h = stage_inbox._snapshot(f)
        assert size == 5
        assert len(h) == 64  # sha256 hex


def test_quiescent_all_stable():
    with TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.txt"; a.write_bytes(b"aaa")
        b = Path(tmp) / "b.txt"; b.write_bytes(b"bbbb")
        stable, skipped = stage_inbox._quiescent_files([a, b], recheck_s=0)
        assert set(stable) == {a, b}
        assert skipped == []


def test_quiescent_skips_growing_file():
    # Simulate a mid-sync file by patching _snapshot to change between the two reads.
    with TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.txt"; good.write_bytes(b"stable")
        growing = Path(tmp) / "growing.txt"; growing.write_bytes(b"partial")

        real_snapshot = stage_inbox._snapshot
        calls = {"growing.txt": 0}

        def fake_snapshot(p: Path):
            if p.name == "growing.txt":
                calls["growing.txt"] += 1
                return (calls["growing.txt"] * 10, f"hash{calls['growing.txt']}")
            return real_snapshot(p)

        stage_inbox._snapshot = fake_snapshot
        try:
            stable, skipped = stage_inbox._quiescent_files([good, growing], recheck_s=0)
        finally:
            stage_inbox._snapshot = real_snapshot

        assert good in stable
        assert growing in skipped


def test_stage_inbox_applies_gate_only_when_remote():
    # With a remote inbox, a "growing" file is skipped (left in place), a stable file stages.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        inbox = root / "_inbox"; inbox.mkdir()
        (inbox / "stable.txt").write_bytes(b"done")
        (inbox / "growing.txt").write_bytes(b"partial")

        # Point paths at the temp tree (mirrors conftest's monkeypatching).
        paths.ROOT = root
        paths.INBOX = inbox
        paths.ARCHIVE = root / "archive"
        paths.RAW_INBOX = paths.ARCHIVE / "raw_inbox"
        paths.BATCHES = root / "batches"
        os.environ["DIALEXIS_INBOX_DIR"] = str(inbox)  # inbox_is_remote() -> True

        real_snapshot = stage_inbox._snapshot
        n = {"growing.txt": 0}

        def fake_snapshot(p: Path):
            if p.name == "growing.txt":
                n["growing.txt"] += 1
                return (n["growing.txt"], f"h{n['growing.txt']}")
            return real_snapshot(p)

        stage_inbox._snapshot = fake_snapshot
        try:
            result = stage_inbox.stage_inbox()
        finally:
            stage_inbox._snapshot = real_snapshot
            os.environ.pop("DIALEXIS_INBOX_DIR", None)

        assert result is not None
        assert result["files_staged"] == 1                 # only stable.txt
        assert not (inbox / "stable.txt").exists()          # consumed + deleted
        assert (inbox / "growing.txt").exists()             # left for next run


def test_stage_inbox_all_files_still_syncing():
    # When ALL non-dotfiles are still-syncing, stage_inbox returns None, nothing is staged,
    # both files remain on disk, and the "dotfiles" message is NOT emitted.
    import io
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        inbox = root / "_inbox"; inbox.mkdir()
        (inbox / "a.txt").write_bytes(b"partial1")
        (inbox / "b.txt").write_bytes(b"partial2")

        paths.ROOT = root
        paths.INBOX = inbox
        paths.ARCHIVE = root / "archive"
        paths.RAW_INBOX = paths.ARCHIVE / "raw_inbox"
        paths.BATCHES = root / "batches"
        os.environ["DIALEXIS_INBOX_DIR"] = str(inbox)  # inbox_is_remote() -> True

        real_snapshot = stage_inbox._snapshot
        calls = {}

        def fake_snapshot(p: Path):
            calls[p.name] = calls.get(p.name, 0) + 1
            return (calls[p.name], f"h{calls[p.name]}")

        stage_inbox._snapshot = fake_snapshot
        captured = io.StringIO()
        import contextlib
        try:
            with contextlib.redirect_stdout(captured):
                result = stage_inbox.stage_inbox()
        finally:
            stage_inbox._snapshot = real_snapshot
            os.environ.pop("DIALEXIS_INBOX_DIR", None)

        assert result is None
        assert (inbox / "a.txt").exists()
        assert (inbox / "b.txt").exists()
        output = captured.getvalue()
        assert "dotfile" not in output, f"Misleading dotfile message emitted: {output!r}"


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
