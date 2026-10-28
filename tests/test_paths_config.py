"""test_paths_config.py - env-driven config resolvers in paths.py.

Standalone: `python3 tests/test_paths_config.py`. No pytest required.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paths


def test_default_inbox_is_none_no_local_fallback():
    os.environ.pop("DIALEXIS_INBOX_DIR", None)
    # No repo-local fallback: unset means no inbox at all, so stage_inbox refuses.
    assert paths.resolve_inbox() is None
    assert paths.inbox_is_remote() is False


def test_inbox_override_from_env():
    os.environ["DIALEXIS_INBOX_DIR"] = "~/somewhere/_inbox"
    try:
        assert paths.resolve_inbox() == Path("~/somewhere/_inbox").expanduser()
        assert paths.inbox_is_remote() is True
    finally:
        os.environ.pop("DIALEXIS_INBOX_DIR", None)


def test_publish_resolvers_read_env():
    os.environ["DIALEXIS_PUBLISH_HOST"] = "my-host"
    os.environ["DIALEXIS_PUBLISH_DIR"] = "<mirror-dir>"
    try:
        assert paths.publish_host() == "my-host"
        assert paths.publish_dir() == Path("<mirror-dir>").expanduser()
    finally:
        os.environ.pop("DIALEXIS_PUBLISH_HOST", None)
        os.environ.pop("DIALEXIS_PUBLISH_DIR", None)


def test_publish_dir_none_when_unset():
    os.environ.pop("DIALEXIS_PUBLISH_DIR", None)
    assert paths.publish_dir() is None


def test_publish_entity_dirs_default_excludes_index():
    os.environ.pop("DIALEXIS_PUBLISH_DIRS", None)
    dirs = paths.publish_entity_dirs()
    assert "people" in dirs and "organizations" in dirs
    assert "_index" not in dirs
    assert len(dirs) == 10


def test_publish_entity_dirs_override():
    os.environ["DIALEXIS_PUBLISH_DIRS"] = "people, projects"
    try:
        assert paths.publish_entity_dirs() == ["people", "projects"]
    finally:
        os.environ.pop("DIALEXIS_PUBLISH_DIRS", None)


def test_model_dir_defaults_to_in_repo_graph():
    os.environ.pop("DIALEXIS_MODEL_DIR", None)
    assert paths.resolve_model_dir() == paths.ROOT / "graph"


def test_model_dir_override_from_env():
    os.environ["DIALEXIS_MODEL_DIR"] = "~/example-data/graph"
    try:
        assert paths.resolve_model_dir() == Path("~/example-data/graph").expanduser()
    finally:
        os.environ.pop("DIALEXIS_MODEL_DIR", None)


def test_configured_model_constant_exists_for_isolation_checks():
    # index_cache_dir() and tests/test_isolation.py compare the live value against the
    # import-time value to tell "real tree" from "throwaway tree". Renamed from
    # _REPO_WORKING_MODEL because it is no longer necessarily in the repo.
    assert isinstance(paths._CONFIGURED_MODEL, Path)
    assert not hasattr(paths, "_REPO_WORKING_MODEL"), "stale alias left behind"


def test_env_model_dir_is_honoured_by_the_module_constant():
    """The resolver is only useful if the constant actually uses it. Checked in a
    subprocess because reloading paths inside a pytest session would discard the
    conftest isolation fixture's re-pointing."""
    import subprocess
    repo = Path(__file__).parent.parent
    env = dict(os.environ, DIALEXIS_MODEL_DIR="/tmp/dialexis-probe-model")
    # returncode asserted, not check=True: a CalledProcessError is not an AssertionError,
    # so it would escape main()'s handler and abort the whole standalone run instead of
    # reporting one FAIL. The two subprocess tests below already do it this way.
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import paths; print(paths.WORKING_MODEL)"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    assert result.stdout.strip() == "/tmp/dialexis-probe-model", result.stdout


def test_env_loader_setdefault_does_not_override():
    # A real environ value wins over a .env line for the same key.
    os.environ["DIALEXIS_PUBLISH_HOST"] = "already-set"
    try:
        paths._load_env_file()  # even if .env names DIALEXIS_PUBLISH_HOST, setdefault keeps ours
        assert os.environ["DIALEXIS_PUBLISH_HOST"] == "already-set"
    finally:
        os.environ.pop("DIALEXIS_PUBLISH_HOST", None)


def test_agenda_and_archive_default_in_repo():
    os.environ.pop("DIALEXIS_AGENDA_DIR", None)
    os.environ.pop("DIALEXIS_ARCHIVE_DIR", None)
    assert paths.resolve_agenda_dir() == paths.ROOT / "agenda"
    assert paths.resolve_archive_dir() == paths.ROOT / "archive"


def test_agenda_and_archive_override_from_env():
    os.environ["DIALEXIS_AGENDA_DIR"] = "~/example-data/agenda"
    os.environ["DIALEXIS_ARCHIVE_DIR"] = "~/example-data/archive"
    try:
        assert paths.resolve_agenda_dir() == Path("~/example-data/agenda").expanduser()
        assert paths.resolve_archive_dir() == Path("~/example-data/archive").expanduser()
    finally:
        os.environ.pop("DIALEXIS_AGENDA_DIR", None)
        os.environ.pop("DIALEXIS_ARCHIVE_DIR", None)


def test_archive_children_follow_the_configured_archive():
    """RAW_INBOX and RETIRED are derived, so they must track a RELOCATED ARCHIVE.

    Checked in a subprocess with DIALEXIS_ARCHIVE_DIR actually set, for the same
    reason as test_env_model_dir_is_honoured_by_the_module_constant:
    importlib.reload(paths) inside a pytest session would discard conftest's
    re-pointing and re-expose the live tree. A relocated root is required here:
    under pytest's autouse fixture the children are hand-derived from the same tmp
    root the fixture already built, so the assertion would hold by construction of
    the fixture rather than of paths.py; and in a plain standalone run with no env
    override, ARCHIVE defaults to ROOT / "archive" anyway, so a hardcoded
    RAW_INBOX = ROOT / "archive" / "raw_inbox" would look identical to the correct
    ARCHIVE / "raw_inbox". Only a root that has actually moved can tell the two apart.
    """
    import subprocess
    repo = Path(__file__).parent.parent
    archive_dir = "/tmp/dialexis-probe-archive"
    env = dict(os.environ, DIALEXIS_ARCHIVE_DIR=archive_dir)
    env.pop("DIALEXIS_AGENDA_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import paths; "
         "print(paths.ARCHIVE); print(paths.RAW_INBOX); print(paths.RETIRED)"],
        cwd=repo, env=env, capture_output=True, text=True)
    out_lines = result.stdout.strip().splitlines()
    expected = [archive_dir, f"{archive_dir}/raw_inbox", f"{archive_dir}/retired"]
    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    assert out_lines == expected, (
        f"expected {expected}, got {out_lines} (stderr={result.stderr!r})")


def test_agenda_children_follow_the_configured_agenda():
    """AGENDA_LEDGER/AGENDA_ARCHIVE/AGENDA_WORKLOG/WORKER_PROFILE are derived, so they
    must track a RELOCATED AGENDA. Same subprocess rationale as the archive test
    above and test_env_model_dir_is_honoured_by_the_module_constant: a live env
    override is the only way to distinguish "derives from AGENDA" from "hardcoded to
    ROOT / 'agenda'", and reloading paths in-process would lose conftest's isolation.
    """
    import subprocess
    repo = Path(__file__).parent.parent
    agenda_dir = "/tmp/dialexis-probe-agenda"
    env = dict(os.environ, DIALEXIS_AGENDA_DIR=agenda_dir)
    env.pop("DIALEXIS_ARCHIVE_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import paths; "
         "print(paths.AGENDA); print(paths.AGENDA_LEDGER); "
         "print(paths.AGENDA_ARCHIVE); print(paths.AGENDA_WORKLOG); "
         "print(paths.WORKER_PROFILE)"],
        cwd=repo, env=env, capture_output=True, text=True)
    out_lines = result.stdout.strip().splitlines()
    expected = [
        agenda_dir,
        f"{agenda_dir}/actions.md",
        f"{agenda_dir}/archive",
        f"{agenda_dir}/worklog",
        f"{agenda_dir}/worker_profile.md",
    ]
    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    assert out_lines == expected, (
        f"expected {expected}, got {out_lines} (stderr={result.stderr!r})")


def _conftest_tuple(name: str) -> set[str]:
    """Read a module-level string tuple out of tests/conftest.py without importing it.

    conftest.py imports pytest; this file must also run standalone on the stdlib alone,
    and an ImportError there would escape main()'s AssertionError-only handler and abort
    the whole standalone run.
    """
    import ast
    src = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"tests/conftest.py no longer defines {name}")


def _env_vars_read_by_paths_py() -> set[str]:
    """Every DIALEXIS_* name paths.py looks up, read from its SOURCE TEXT.

    Scanning source is normally a smell, but the invariant here is precisely about that
    file's text: a variable is "read by paths.py" iff an os.environ.get("DIALEXIS_...")
    for it appears there. Reflection cannot see it -- os.environ.get() leaves no trace on
    the module -- and any registry of the names could itself fall out of sync, which is
    the very defect being guarded.
    """
    import re
    src = Path(paths.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'os\.environ\.get\(\s*"(DIALEXIS_[A-Z_]+)"', src))


def test_every_env_var_paths_reads_is_cleared_by_the_isolation_fixture():
    """Three hand-maintained lists (conftest's _REPOINTED, conftest's _CLEARED_ENV, and
    test_isolation's relocation tuple) must each be updated by hand when a variable lands
    in paths.py. Nothing derived them, and they had already drifted:
    DIALEXIS_LODESTAR_DIR was read but never cleared, so paths.LODESTAR_RECEIPTS pointed
    outside the test sandbox during every pytest run --
    the same class of hazard that destroyed this database on 2026-08-24.

    This closes the class: a new resolver in paths.py fails here until the fixture clears
    its variable.
    """
    read = _env_vars_read_by_paths_py()
    assert read, "found no DIALEXIS_* lookups in paths.py; the scan pattern must have rotted"
    cleared = _conftest_tuple("_CLEARED_ENV")
    assert read == cleared, (
        "tests/conftest.py's _CLEARED_ENV is out of sync with paths.py.\n"
        f"  read but not cleared: {sorted(read - cleared)}\n"
        f"  cleared but not read: {sorted(cleared - read)}")


_LODESTAR_CONSTANTS = ("LODESTAR_ROOT", "LODESTAR_QUEUE", "LODESTAR_RECEIPTS")


def test_lodestar_paths_are_listed_for_repointing():
    """kairos/ingest.py is the sole writer of a receipts file in a separate, configured repo,
    so a fixture that re-points only ROOT-derived constants left these three aimed outside
    the test sandbox. Static, so it has teeth in both run modes."""
    repointed = _conftest_tuple("_REPOINTED")
    missing = [n for n in _LODESTAR_CONSTANTS if n not in repointed]
    assert not missing, (
        f"tests/conftest.py's _REPOINTED omits {missing}; a test touching the Kairos "
        "airlock would read/write outside the test sandbox")


def test_lodestar_paths_point_away_from_the_default_under_pytest():
    """The live half of the test above. Only meaningful when the fixture is active: in a
    standalone run there is no isolation to check and the real default is correct, so this
    returns early rather than asserting something false."""
    if paths.WORKING_MODEL == paths._CONFIGURED_MODEL:
        return  # no isolation fixture active (standalone run)
    default = Path(paths.__file__).resolve().parent.parent.parent / "Lodestar"
    for name in _LODESTAR_CONSTANTS:
        p = getattr(paths, name)
        assert not (p == default or default in p.parents), (
            f"paths.{name} is {p}, inside the default checkout, during a test; "
            "tests/conftest.py must re-point it")


def test_graph_root_moves_every_content_path_and_wins_over_per_root_vars():
    """DIALEXIS_GRAPH_ROOT is one switch for a whole content world. It must beat the
    per-root variables (a .env naming a real Drive inbox must not be staged from a demo
    run) and must move batches/, output/, the index cache and the access log too: those
    default to the CHECKOUT, and a demo run that cleaned the checkout's batches or
    overwrote the real graph's cache would break the owner's live graph. Subprocess, for
    the same reason as the tests above."""
    import subprocess
    import tempfile
    repo = Path(__file__).parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        env = dict(os.environ, DIALEXIS_GRAPH_ROOT=str(root),
                   DIALEXIS_MODEL_DIR="/tmp/should-lose-model",
                   DIALEXIS_INBOX_DIR="/tmp/should-lose-inbox",
                   DIALEXIS_INDEX_DIR="/tmp/should-lose-index",
                   DIALEXIS_AGENDA_DIR="/tmp/should-lose-agenda",
                   DIALEXIS_ARCHIVE_DIR="/tmp/should-lose-archive")
        env.pop("DIALEXIS_ACCESS_LOG", None)
        code = ("import sys; sys.path.insert(0, 'scripts'); import paths, access_log; "
                "print(paths.WORKING_MODEL); print(paths.AGENDA); print(paths.ARCHIVE); "
                "print(paths.INBOX); print(paths.BATCHES); print(paths.OUTPUT); "
                "print(paths.index_cache_dir()); print(access_log.LOG_PATH); "
                "print(paths.inbox_is_remote())")
        r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        want = [str(root / x) for x in ("graph", "agenda", "archive", "inbox", "batches",
                                        "output", ".cache", ".access-log.jsonl")] + ["False"]
        assert r.stdout.split() == want, (r.stdout, r.stderr)


def test_run_py_graph_root_flag_sets_the_switch():
    """`run.py --graph-root PATH <cmd>` is the CLI spelling of DIALEXIS_GRAPH_ROOT."""
    import subprocess
    import tempfile
    repo = Path(__file__).parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "graph").mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIALEXIS_")}
        r = subprocess.run([sys.executable, str(repo / "run.py"), "--graph-root", str(root),
                            "system_status"], cwd=tmp, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert str(root / "inbox") in r.stdout, r.stdout
        bad = subprocess.run([sys.executable, str(repo / "run.py"), "--graph-root",
                              str(root / "missing"), "system_status"],
                             cwd=tmp, env=env, capture_output=True, text=True)
        assert bad.returncode == 2 and "not a directory" in bad.stderr, bad


def test_graph_root_unset_changes_nothing():
    os.environ.pop("DIALEXIS_GRAPH_ROOT", None)
    assert paths.graph_root() is None


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
