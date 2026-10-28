"""conftest.py - pytest isolation for the Dialexis test suite.

The test files were written as standalone scripts: each one sets up a throwaway
temp model inside its own run_tests()/main(), reached only via
`if __name__ == "__main__"`. pytest imports the modules and calls the test_*
functions directly, so run_tests() never runs and paths.WORKING_MODEL stays
pointed at the LIVE repo. The cleanup helpers then shutil.rmtree the real entity
dirs. On 2026-08-24 a single `pytest tests/` run deleted all 4550 live entities.

This autouse fixture applies the same tmp-tree re-pointing that run_tests() does,
uniformly to every pytest-collected test, so a pytest run is hermetic. Standalone
`python3 tests/test_x.py` runs are unaffected — pytest is the only thing that
loads conftest.py.
"""
import os
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths  # noqa: E402

_REAL_AGENTS = Path(__file__).parent.parent / "agents"

# Every data path re-pointed into the tmp tree. SCRIPTS/TESTS are code dirs and stay
# put. Saved/restored around each test so module state can't leak between tests or out to
# the real repo.
#
# The LODESTAR_* entries are not ROOT-derived — they address a separate, configured repo,
# and kairos/ingest.py is the sole writer of the receipts file there. Left un-repointed, a
# test that exercised the airlock would write outside the test sandbox. Anything in paths.py naming a directory belongs here, whatever repo it names.
_REPOINTED = (
    "ROOT", "AGENTS", "ARCHIVE", "RAW_INBOX", "RETIRED", "BATCHES", "INBOX",
    "OUTPUT", "WORKING_MODEL", "INDEX_DIR", "AGENDA", "AGENDA_LEDGER",
    "AGENDA_ARCHIVE", "AGENDA_WORKLOG", "WORKER_PROFILE", "REGISTRY_FILE",
    "LODESTAR_ROOT", "LODESTAR_QUEUE", "LODESTAR_RECEIPTS",
)

#: Every DIALEXIS_* variable paths.py reads, cleared inside each test so a call-time
#: resolver cannot reach real data even though the constants above were re-pointed.
#: tests/test_paths_config.py asserts this equals the set paths.py actually reads, so the
#: next variable added there fails loudly here instead of silently escaping isolation.
_CLEARED_ENV = (
    "DIALEXIS_MODEL_DIR", "DIALEXIS_AGENDA_DIR", "DIALEXIS_ARCHIVE_DIR",
    "DIALEXIS_INBOX_DIR", "DIALEXIS_INDEX_DIR", "DIALEXIS_LODESTAR_DIR",
    "DIALEXIS_PUBLISH_HOST", "DIALEXIS_PUBLISH_DIR", "DIALEXIS_PUBLISH_DIRS",
    "DIALEXIS_GRAPH_ROOT",
)


@pytest.fixture(autouse=True)
def _isolate_paths():
    saved = {k: getattr(paths, k) for k in _REPOINTED}
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths.ROOT = root
        paths.AGENTS = root / "agents"
        paths.ARCHIVE = root / "archive"
        paths.RAW_INBOX = paths.ARCHIVE / "raw_inbox"
        paths.RETIRED = paths.ARCHIVE / "retired"
        paths.BATCHES = root / "batches"
        paths.INBOX = root / "inbox"
        paths.OUTPUT = root / "output"
        paths.WORKING_MODEL = root / "graph"
        paths.INDEX_DIR = paths.WORKING_MODEL / "_index"
        paths.AGENDA = root / "agenda"
        paths.AGENDA_LEDGER = paths.AGENDA / "actions.md"
        paths.AGENDA_ARCHIVE = paths.AGENDA / "archive"
        paths.AGENDA_WORKLOG = paths.AGENDA / "worklog"
        paths.WORKER_PROFILE = paths.AGENDA / "worker_profile.md"
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"
        # Stand-in for the separate task-board checkout, so the airlock queue/receipts a
        # test touches are throwaway files, never the real board's.
        paths.LODESTAR_ROOT = root / "lodestar"
        paths.LODESTAR_QUEUE = paths.LODESTAR_ROOT / "airlock" / "kairos-queue.jsonl"
        paths.LODESTAR_RECEIPTS = paths.LODESTAR_ROOT / "airlock" / "kairos-receipts.json"

        # NOTE: the resolve_*_dir() fallbacks read paths.ROOT, which is re-pointed above, so
        # clearing the env vars is sufficient to make resolvers and constants agree. If a
        # resolver is ever changed to derive from __file__ instead, this fixture breaks silently.
        for _k in _CLEARED_ENV:
            os.environ.pop(_k, None)

        paths.AGENTS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REAL_AGENTS / "ENTITY_TYPE_REGISTRY.md", paths.REGISTRY_FILE)
        paths.WORKING_MODEL.mkdir(parents=True, exist_ok=True)
        paths.BATCHES.mkdir(parents=True, exist_ok=True)

        # Pre-create entity-type dirs; smoke_test.py assumes they exist.
        from registry import load_registry
        reg = load_registry()
        for _etype, info in reg._entity_types.items():
            paths.entity_dir(info["directory"]).mkdir(parents=True, exist_ok=True)

        # Redirect the read-path access log into the temp tree. Tests exercise `entity`
        # and `resolve`, which now log every lookup — and on 2026-09-14 the first full
        # suite run after wiring them added 4 phantom lookups to the REAL log, inflating
        # a metric whose whole purpose is to be an honest count of real usage.
        import access_log
        _saved_log = access_log.LOG_PATH
        access_log.LOG_PATH = root / ".access-log.jsonl"

        try:
            yield root
        finally:
            access_log.LOG_PATH = _saved_log
            for k, v in saved.items():
                setattr(paths, k, v)
