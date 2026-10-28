"""test_isolation.py - Guard: under pytest, every test must run against a
throwaway model, never the live graph/.

Regression guard for the 2026-08-24 incident where `pytest tests/` ran the
test functions without their run_tests() harness (which pytest never calls),
leaving paths.WORKING_MODEL pointed at the real repo. Cleanup helpers then
`shutil.rmtree`'d the live entity dirs, deleting the whole knowledge base.

These tests only READ paths — they are safe to run even when isolation is
absent (that is the point: they detect the absence).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths


def test_graph_is_isolated():
    assert paths.WORKING_MODEL != paths._CONFIGURED_MODEL, (
        "paths.WORKING_MODEL still points at the configured tree; the pytest "
        "isolation fixture (tests/conftest.py) is not active. Tests could "
        "destroy the real knowledge graph."
    )


def test_graph_is_a_tempdir():
    assert paths._CONFIGURED_MODEL not in paths.WORKING_MODEL.parents, (
        "isolated WORKING_MODEL is nested under the configured model tree"
    )


def test_no_module_early_binds_a_path_constant():
    """`from paths import WORKING_MODEL` binds a snapshot at import time, which defeats
    BOTH the pytest isolation fixture and env-based configuration. Every consumer must
    reference paths.WORKING_MODEL so it resolves late.

    viz/backend/extract_embeddings.py did exactly this until 2026-09-11.
    """
    import re
    import subprocess

    # Any dotted route to the paths module, not just a bare "from paths import".
    from_import = re.compile(r"^from\s+[\w.]*\bpaths\s+import\b")
    # A MODULE-LEVEL copy snapshots at import exactly like a from-import. The same copy
    # inside a function body is fine -- it reads paths.X at call time, which is late.
    # Anchored at column zero, so indentation is what distinguishes the two.
    module_copy = re.compile(r"^[A-Za-z_]\w*\s*=\s*paths\.\w+")

    repo = Path(__file__).parent.parent
    try:
        git = subprocess.run(["git", "-C", str(repo), "ls-files", "*.py"],
                             capture_output=True, text=True)
    except FileNotFoundError:      # no git binary (slim container)
        git = None
    if git is not None and git.returncode == 0:
        tracked = [t for t in git.stdout.split() if (repo / t).exists()]
    else:   # an exported tree is not a git checkout
        tracked = [str(p.relative_to(repo)) for p in repo.rglob("*.py")]
    offenders = []
    for rel in tracked:
        text = (repo / rel).read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if from_import.match(stripped) or module_copy.match(line):
                offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "these modules early-bind a paths constant and will bypass both test isolation "
        "and DIALEXIS_MODEL_DIR:\n  " + "\n  ".join(offenders)
    )


def test_index_cache_stays_inside_a_repointed_model():
    """A re-pointed model keeps its derived cache inside the throwaway tree, so no test
    can litter the real ~/.cache/dialexis/. Guards paths.index_cache_dir()."""
    import os
    os.environ.pop("DIALEXIS_INDEX_DIR", None)
    cache = paths.index_cache_dir()
    assert paths.WORKING_MODEL.parent in cache.parents, (
        f"index cache {cache} escaped the isolated model tree {paths.WORKING_MODEL}"
    )
    assert Path.home() / ".cache" / "dialexis" not in cache.parents, (
        f"index cache {cache} points at the real per-machine cache during a test"
    )


def test_relocation_env_vars_are_cleared_during_tests():
    """If DIALEXIS_MODEL_DIR survives into a test, any call-time resolver would reach
    the owner's real private graph even though the constants were re-pointed.

    The names come from conftest._CLEARED_ENV rather than a tuple repeated here: this
    file used to carry its own list of three, which is a third place to forget. That
    tuple is in turn checked against paths.py's actual lookups by
    tests/test_paths_config.py::test_every_env_var_paths_reads_is_cleared_by_the_isolation_fixture,
    so one authority now covers all of it. Importing conftest is safe here and only here
    — this file is pytest-only by design (it detects whether the fixture is active), so
    conftest's own `import pytest` can never be the thing that breaks a standalone run.
    """
    import os

    import conftest
    assert conftest._CLEARED_ENV, "conftest._CLEARED_ENV is empty; nothing would be checked"
    for var in conftest._CLEARED_ENV:
        assert var not in os.environ, (
            f"{var} is still set inside a test; tests/conftest.py must pop it"
        )


def test_resolvers_agree_with_the_repointed_constants():
    """The isolation fixture and the resolvers must not disagree about where the model is."""
    assert paths.resolve_model_dir() == paths.WORKING_MODEL
    assert paths.resolve_agenda_dir() == paths.AGENDA
    assert paths.resolve_archive_dir() == paths.ARCHIVE


def test_env_clearing_actually_fires_when_vars_are_set():
    """The two sibling tests above only have teeth when a DIALEXIS_* relocation var is
    already set in the ambient environment (e.g. via a real .env). On a fresh clone, in
    CI, or in any worktree without .env -- as in this one -- no such var is set, so both
    siblings pass whether or not tests/conftest.py's env-clearing loop exists at all.
    They are not tautological (they do fail on a machine where the hazard is live), but
    they are conditionally inert everywhere else.

    This test supplies its own precondition instead of depending on the ambient
    environment. It launches a fresh pytest subprocess, selected by exact node id so
    only test_relocation_env_vars_are_cleared_during_tests is collected, with the four
    DIALEXIS_* relocation vars set to distinctive throwaway paths in that subprocess's
    environment. The outer (this) process's fixture has already run and cannot un-set
    anything for a not-yet-started subprocess, so this is the only way to manufacture
    the precondition at all.

    With tests/conftest.py's env-clearing loop in place, the inner autouse fixture pops
    the vars before the inner test body runs, so the inner test passes and this test's
    subprocess exits 0. Revert that loop and the vars survive into the inner test body,
    which fails, and this test fails with it -- in any environment, unconditionally.
    """
    import os
    import re
    import subprocess
    import sys as _sys

    repo = Path(__file__).parent.parent
    node_id = "tests/test_isolation.py::test_relocation_env_vars_are_cleared_during_tests"

    env = os.environ.copy()
    env["DIALEXIS_MODEL_DIR"] = "/tmp/dialexis-env-clearing-probe-model"
    env["DIALEXIS_AGENDA_DIR"] = "/tmp/dialexis-env-clearing-probe-agenda"
    env["DIALEXIS_ARCHIVE_DIR"] = "/tmp/dialexis-env-clearing-probe-archive"
    env["DIALEXIS_INDEX_DIR"] = "/tmp/dialexis-env-clearing-probe-index"

    result = subprocess.run(
        [_sys.executable, "-m", "pytest", node_id, "-v"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )
    detail = f"--- inner stdout ---\n{result.stdout}\n--- inner stderr ---\n{result.stderr}"

    # Selecting by exact node id must collect exactly this one test. If it ever collected
    # more (e.g. this very test, causing unbounded self-nesting), that is a distinct bug
    # from an env-clearing regression and must not be mistaken for one.
    collected = re.search(r"collected (\d+) item", result.stdout)
    assert collected and collected.group(1) == "1", (
        "expected the inner pytest run to collect exactly 1 test by node id; "
        f"got {collected.group(0) if collected else 'no match'}\n{detail}"
    )

    assert result.returncode == 0, (
        "inner pytest run failed with DIALEXIS_MODEL_DIR/AGENDA_DIR/ARCHIVE_DIR/"
        "INDEX_DIR set in its environment; tests/conftest.py's isolation fixture "
        f"should have popped them before the inner test body ran\n{detail}"
    )
