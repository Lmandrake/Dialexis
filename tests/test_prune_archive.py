"""test_prune_archive.py - the ledger display path degrades gracefully once
archive/ can be relocated outside the repo (DIALEXIS_ARCHIVE_DIR).

The helper under test now lives in paths.display_path -- prune_archive had a private
copy, and commit.py/stage_inbox.py needed the same thing, so there is one definition.

Standalone: `python3 tests/test_prune_archive.py`. No pytest required.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paths
import prune_archive


def test_display_path_relative_when_inside_root():
    p = paths.ROOT / "archive" / "PRUNED_LOG.md"
    assert paths.display_path(p) == str(Path("archive") / "PRUNED_LOG.md")


def test_display_path_full_when_outside_root():
    # The new case: ARCHIVE relocated outside ROOT via DIALEXIS_ARCHIVE_DIR.
    # relative_to(paths.ROOT) would raise ValueError here; the helper must not.
    p = Path("/tmp/dialexis-data-probe/archive/PRUNED_LOG.md")
    assert paths.display_path(p) == str(p)


def test_display_path_output_round_trips_through_root():
    """Both forms must satisfy `paths.ROOT / recorded`, because that is exactly how
    reconcile.py and stage_inbox.py re-resolve a recorded path. pathlib absorbs an
    absolute right operand, which is what makes the absolute form safe."""
    inside = paths.ROOT / "archive" / "PRUNED_LOG.md"
    assert paths.ROOT / paths.display_path(inside) == inside
    outside = Path("/tmp/dialexis-data-probe/archive/PRUNED_LOG.md")
    assert paths.ROOT / paths.display_path(outside) == outside


def test_prune_archive_uses_the_shared_helper():
    """No private copy left behind: two definitions of the same rule is how the
    recorded-path format drifts between a writer and its reader."""
    assert not hasattr(prune_archive, "_display_path"), (
        "prune_archive._display_path is back; use paths.display_path")


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
