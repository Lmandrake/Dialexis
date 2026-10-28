"""test_source_log.py - the web-source ledger path follows a relocated archive/
(DIALEXIS_ARCHIVE_DIR), instead of always writing inside the repo's own archive/.

Standalone: `python3 tests/test_source_log.py`. No pytest required.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paths
import source_log


def test_log_path_defaults_to_in_repo_archive():
    # No env override active in this process: falls back to paths.ARCHIVE, which
    # (absent DIALEXIS_ARCHIVE_DIR) is the in-repo default.
    assert source_log._log_path() == paths.ARCHIVE / "web_references" / "SOURCE_LOG.md"


def test_log_path_follows_relocated_archive():
    """The ledger must be re-derived from paths.ARCHIVE at call time, not snapshotted
    from an independently-computed project root at import time.

    Checked in a subprocess with DIALEXIS_ARCHIVE_DIR actually set, for the same
    reason as test_env_model_dir_is_honoured_by_the_module_constant in
    tests/test_paths_config.py: importlib.reload inside a pytest session would lose
    conftest's isolation re-pointing, and a plain in-process run with no override
    would fall back to ROOT / "archive" anyway -- which is bit-for-bit identical to
    what the OLD hardcoded `PROJECT_ROOT / "archive" / ...` produced. Only a root
    that has actually moved can tell "reads paths.ARCHIVE" apart from "still
    computes its own project root".
    """
    import os
    import subprocess

    repo = Path(__file__).parent.parent
    archive_dir = "/tmp/dialexis-probe-source-log-archive"
    env = dict(os.environ, DIALEXIS_ARCHIVE_DIR=archive_dir)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import source_log; "
         "print(source_log._log_path())"],
        cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")

    expected = f"{archive_dir}/web_references/SOURCE_LOG.md"
    out = result.stdout.strip()
    assert out == expected, (
        f"expected {expected!r}, got {out!r} (stderr={result.stderr!r}) -- if this "
        "printed the in-repo path instead, source_log._log_path() (or a leftover "
        "module-level LOG_PATH) is not tracking a relocated archive/")


def test_no_module_level_log_path_constant():
    # Regression guard for the exact bug this test file exists to catch: a
    # module-level `LOG_PATH = ...` (or `PROJECT_ROOT = ...` still feeding one)
    # snapshots the path at import time and can never follow DIALEXIS_ARCHIVE_DIR.
    assert not hasattr(source_log, "LOG_PATH"), (
        "source_log.LOG_PATH reappeared as a module-level constant; it must be "
        "computed at call time via source_log._log_path() instead"
    )


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
