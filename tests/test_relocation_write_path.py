"""test_relocation_write_path.py - the FULL WRITE PATH under relocation.

Standalone: `python3 tests/test_relocation_write_path.py`. No pytest required.

Why this file exists
--------------------
`DIALEXIS_MODEL_DIR` / `DIALEXIS_AGENDA_DIR` / `DIALEXIS_ARCHIVE_DIR` move the content
roots out of the code checkout. The relocation gate that shipped with them ran only
`reconcile` and `validate` -- both READ paths -- so it could not see that `commit` and
`stage_inbox` called `Path.relative_to(paths.ROOT)` on a now-outside-ROOT content path.
That RAISES, and in `commit` it raised AFTER the rollback window closed: entity files on
disk, renamed originals unlinked, batch left STAGED, no rollback, bare traceback. Exactly
the half-written store the DECIDE/ENFORCE split exists to make impossible.

So this test drives the real CLI, in the real order, with all three roots relocated:

    stage_inbox -> commit -> reconcile -> validate

Everything runs in a SUBPROCESS against a throwaway copy of the machinery, for three
reasons:
  1. It exercises the actual `main()` entry points, so the test cannot drift from what
     the CLI does (an in-process re-implementation could stay green while the CLI broke).
  2. `paths.ROOT` is fixed at import, so only a separate process can have a different one.
  3. The copy is the only ROOT the subprocess can see, so a stray write into a repo-local
     `graph/` / `agenda/` / `archive/` lands in the copy where this test can assert on it
     -- never in the owner's real repo.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# NOT paths.ROOT: under pytest the isolation fixture re-points it at a tmp tree, and this
# test needs the real checkout to copy the machinery from.
REPO = Path(__file__).resolve().parent.parent

#: Copied into the throwaway checkout. Just the machinery -- no content dirs, which is
#: the point: the subprocess must find its content only through the env vars.
_MACHINERY = ("run.py", "scripts", "agents")


def _build_tree(tmp: Path) -> tuple[Path, dict]:
    """Lay out checkout/ (the fake repo) beside content/ (the relocated roots).

    Returns (checkout, env). The content roots are SIBLINGS of the checkout, not
    children: a child would still satisfy relative_to(ROOT) and prove nothing.
    """
    checkout = tmp / "checkout"
    checkout.mkdir()
    for name in _MACHINERY:
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, checkout / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, checkout / name)

    content = tmp / "content"
    for sub in ("graph", "agenda", "archive"):
        (content / sub).mkdir(parents=True)
    inbox = tmp / "inbox"
    inbox.mkdir()

    # Start from a copy with every DIALEXIS_* var stripped, so an ambient .env or a
    # developer's exported config cannot reach into the subprocess.
    env = {k: v for k, v in os.environ.items() if not k.startswith("DIALEXIS_")}
    env.update({
        "DIALEXIS_MODEL_DIR": str(content / "graph"),
        "DIALEXIS_AGENDA_DIR": str(content / "agenda"),
        "DIALEXIS_ARCHIVE_DIR": str(content / "archive"),
        "DIALEXIS_INBOX_DIR": str(inbox),
        # Keep the derived SQLite cache in the throwaway tree too.
        "DIALEXIS_INDEX_DIR": str(tmp / "cache"),
    })
    return checkout, env


def _run(checkout: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(checkout / "run.py"), *args],
                          cwd=str(checkout), env=env, capture_output=True, text=True)


def _detail(label: str, r: subprocess.CompletedProcess) -> str:
    return (f"--- {label} exited {r.returncode} ---\n"
            f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")


def _inventory(root: Path) -> set[str]:
    """Relative paths of every file under root, ignoring bytecode caches."""
    return {str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def test_full_write_path_survives_relocation():
    """stage_inbox -> commit -> reconcile -> validate, all three roots outside ROOT."""
    with TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        checkout, env = _build_tree(tmp)
        content = tmp / "content"
        before = _inventory(checkout)

        # --- stage_inbox: exercises the archive-path record (stage_inbox.py) ---
        (tmp / "inbox" / "source_note.txt").write_text(
            "Ford Prefect is a field researcher.\n", encoding="utf-8")
        r = _run(checkout, env, "stage_inbox")
        assert r.returncode == 0, _detail("stage_inbox", r)

        batches = sorted(p for p in (checkout / "batches").iterdir() if p.is_dir())
        assert len(batches) == 1, f"expected exactly one staged batch, got {batches}"
        batch = batches[0]

        staged = json.loads((batch / "_manifest.json").read_text(encoding="utf-8"))
        assert staged["status"] == "STAGED", staged
        assert len(staged["files"]) == 1, staged
        # The recorded archive path must resolve for the readers that do ROOT / recorded.
        recorded = staged["files"][0]["archive"]
        resolved = checkout / recorded
        assert resolved.exists(), (
            f"recorded archive path {recorded!r} does not resolve from ROOT; "
            "stage_inbox's record and its readers disagree")
        assert resolved.is_relative_to(content / "archive"), (
            f"archive copy landed at {resolved}, not in the relocated archive root")

        # --- commit: the site that used to raise AFTER the rollback window closed ---
        (batch / "manifest.json").write_text(json.dumps({
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_entity",
                "entity_type": "person",
                "canonical_name": "Ford Prefect",
                "status": "Active",
                "summary": "Relocation write-path probe.",
            }],
        }, indent=2), encoding="utf-8")
        r = _run(checkout, env, "commit", str(batch / "manifest.json"))
        assert r.returncode == 0, _detail("commit", r)

        entity = content / "graph" / "people" / "ford_prefect.md"
        assert entity.exists(), f"entity not written to the relocated graph\n{_detail('commit', r)}"

        # The batch must be marked COMMITTED. It is not merely cosmetic: the pre-fix
        # ValueError escaped main()'s handler, so _mark_batch_committed() never ran and
        # the batch stayed STAGED forever with its operations already applied.
        committed = json.loads((batch / "_manifest.json").read_text(encoding="utf-8"))
        assert committed["status"] == "COMMITTED", committed

        # --- reconcile + validate: the read paths, for completeness ---
        r = _run(checkout, env, "reconcile")
        assert r.returncode == 0, _detail("reconcile", r)
        r = _run(checkout, env, "validate")
        assert r.returncode == 0, _detail("validate", r)

        # --- nothing may be written into the repo-local content roots ---
        for leaked in ("graph", "agenda", "archive"):
            assert not (checkout / leaked).exists(), (
                f"{leaked}/ was created inside ROOT while relocated; a writer ignored "
                f"the env var and wrote to the repo")

        # batches/ is deliberately repo-local (see CLAUDE.md's directory layout), so it
        # is the one permitted new location inside ROOT.
        new = {p for p in _inventory(checkout) - before if not p.startswith("batches/")}
        assert not new, f"unexpected new files inside ROOT while relocated: {sorted(new)}"


def test_commit_records_paths_its_readers_can_resolve():
    """`commit`'s printed file list is a contract: reconcile and stage_inbox re-resolve
    those strings as `paths.ROOT / recorded`. Under relocation they come out absolute,
    which still round-trips because pathlib absorbs an absolute right operand -- assert
    that property here rather than trusting it.
    """
    with TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        checkout, env = _build_tree(tmp)
        content = tmp / "content"

        manifest = tmp / "m.json"
        manifest.write_text(json.dumps({
            "manifest_version": "2.0",
            "operations": [{
                "op": "upsert_entity",
                "entity_type": "organization",
                "canonical_name": "Megadodo Publications",
                "status": "Active",
                "summary": "Relocation write-path probe.",
            }],
        }), encoding="utf-8")

        r = _run(checkout, env, "commit", str(manifest))
        assert r.returncode == 0, _detail("commit", r)

        listed = [ln.strip() for ln in r.stdout.splitlines()
                  if ln.strip().endswith("megadodo_publications.md")]
        assert len(listed) == 1, _detail("commit", r)
        recorded = listed[0]
        assert (checkout / recorded).exists(), (
            f"recorded path {recorded!r} does not resolve as ROOT / recorded")
        assert (checkout / recorded).is_relative_to(content / "graph"), (
            f"{recorded!r} resolved outside the relocated graph")


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
