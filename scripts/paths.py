"""paths.py - Canonical filesystem layout. The one definition of where things live."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()


def _load_env_file() -> None:
    """Populate os.environ from ROOT/.env (stdlib-only; setdefault so real env wins).

    The core scripts are stdlib-only and cannot use python-dotenv (the Slack bot's
    dependency). This reads simple KEY=VALUE lines so per-machine config like
    DIALEXIS_INBOX_DIR / DIALEXIS_PUBLISH_* is available to any script that imports
    paths, without a third-party dependency.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
    except OSError:
        pass


_load_env_file()

AGENTS = ROOT / "agents"


def graph_root() -> Path | None:
    """One switch for a whole, self-contained content world: DIALEXIS_GRAPH_ROOT (or
    `run.py --graph-root PATH`, which sets it). Under it live graph/, agenda/, archive/,
    inbox/, batches/, output/, the derived index cache (.cache/) and the access log.

    When set it WINS over the per-root variables (DIALEXIS_MODEL_DIR, _INBOX_DIR,
    _INDEX_DIR, ...), including ones a .env supplies. That is the point: a demo or
    scratch graph run from a checkout whose .env names a real Drive inbox must never
    stage that inbox, clean that checkout's batches, or overwrite that graph's cache.
    Unset (the default), nothing below changes.
    """
    env = os.environ.get("DIALEXIS_GRAPH_ROOT")
    return Path(env).expanduser().resolve() if env else None


def resolve_archive_dir() -> Path:
    """Immutable source copies + retired entities. Content, so it relocates with the graph."""
    if graph_root():
        return graph_root() / "archive"
    env = os.environ.get("DIALEXIS_ARCHIVE_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT / "archive"


ARCHIVE = resolve_archive_dir()
RAW_INBOX = ARCHIVE / "raw_inbox"
RETIRED = ARCHIVE / "retired"
BATCHES = graph_root() / "batches" if graph_root() else ROOT / "batches"


def resolve_inbox() -> Path | None:
    """Where new source drops are read from. DIALEXIS_INBOX_DIR points this at the
    Drive-synced drop zone (e.g. <shared-inbox>). There is NO repo-local
    fallback: unset, this returns None and stage_inbox refuses to run, so a misconfigured
    machine can never silently accumulate an unsynced local inbox. Under a graph root the
    inbox is <root>/inbox, always."""
    if graph_root():
        return graph_root() / "inbox"
    env = os.environ.get("DIALEXIS_INBOX_DIR")
    if env:
        return Path(env).expanduser()
    return None


INBOX = resolve_inbox()


def inbox_is_remote() -> bool:
    """True when the inbox is an external (Drive) path — the signal to apply the
    stage_inbox quiescence gate. Based on the env var, not the current INBOX value,
    so the pytest harness (which monkeypatches paths.INBOX to a temp dir) is unaffected."""
    return bool(os.environ.get("DIALEXIS_INBOX_DIR")) and not graph_root()


OUTPUT = graph_root() / "output" if graph_root() else ROOT / "output"
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"


def resolve_model_dir() -> Path:
    """Where the personal knowledge graph lives. DIALEXIS_MODEL_DIR points this at a
    private content repo, which is what lets the machinery be published while the graph
    stays private. Unlike resolve_inbox(), this DOES fall back to the in-repo path: a
    checkout with no configuration must still work, because the public repo ships a demo
    corpus there."""
    if graph_root():
        return graph_root() / "graph"
    env = os.environ.get("DIALEXIS_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT / "graph"


WORKING_MODEL = resolve_model_dir()
# The model dir AS CONFIGURED AT IMPORT, captured before any test harness can re-point
# WORKING_MODEL/ROOT. index_cache_dir() and tests/test_isolation.py compare the live
# value against this to tell "the real tree" from "a throwaway temp tree". It is no
# longer necessarily inside the repo, hence the name.
_CONFIGURED_MODEL = WORKING_MODEL
INDEX_DIR = WORKING_MODEL / "_index"


def resolve_agenda_dir() -> Path:
    """Kairos's ledger, worklog and worker profile. Content, so it relocates with the graph."""
    if graph_root():
        return graph_root() / "agenda"
    env = os.environ.get("DIALEXIS_AGENDA_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT / "agenda"


AGENDA = resolve_agenda_dir()
AGENDA_LEDGER = AGENDA / "actions.md"
AGENDA_ARCHIVE = AGENDA / "archive"
AGENDA_WORKLOG = AGENDA / "worklog"
WORKER_PROFILE = AGENDA / "worker_profile.md"

REGISTRY_FILE = AGENTS / "ENTITY_TYPE_REGISTRY.md"


def _resolve_lodestar_root() -> Path:
    """Where the Lodestar repo lives, so kairos/ingest.py can read its board->Kairos airlock
    queue and write back the receipts file. DIALEXIS_LODESTAR_DIR overrides; the default
    assumes a sibling checkout named Lodestar next to this repo.
    Kairos never writes anywhere else in that repo — see kairos/ingest.py's module docstring.
    """
    env = os.environ.get("DIALEXIS_LODESTAR_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT.parent / "Lodestar"


LODESTAR_ROOT = _resolve_lodestar_root()
#: Lodestar's board -> Kairos boundary (bin/lib/intents.py QUEUE_REL/RECEIPTS_REL there).
#: Kairos reads the queue and is the only writer of the receipts file; both live in the
#: OTHER repo, never inside this one.
LODESTAR_QUEUE = LODESTAR_ROOT / "airlock" / "kairos-queue.jsonl"
LODESTAR_RECEIPTS = LODESTAR_ROOT / "airlock" / "kairos-receipts.json"


def index_cache_dir() -> Path:
    """Machine-local directory for the DERIVED SQLite cache (index_store.py).

    index.sqlite is a ~10MB file that write_rows() fully rewrites on every
    commit/reconcile, and it is purely derived — rebuildable from the markdown at
    any time. It is therefore per-machine and never committed or shared: a cache
    that costs more to transfer than to regenerate should be regenerated.

    Read at call time, not import time, because the test harness re-points
    WORKING_MODEL. A re-pointed model keeps its cache INSIDE the throwaway tree, so
    it is torn down with it and no test can litter the real ~/.cache/dialexis/ —
    that holds for every test file automatically, with no per-test wiring to forget.

    The live path is keyed by a hash of the resolved model dir, so two checkouts
    never share a cache. Note this means relocating the repo changes the key and
    the cache rebuilds itself on first use; that is intended.
    Override with DIALEXIS_INDEX_DIR.

    History: this used to live outside ROOT specifically because the repo sat in
    Google Drive, where syncing the DB was actively harmful — Drive restamped mtime
    on download, so the (mtime,size) gate in iter_entities() missed on every file
    while is_stale() compared only path SETS and still reported fresh. The cache
    looked warm and silently re-parsed all 4,500 files. The repo moved to local
    disk on 2026-08-24, so that hazard is gone; the per-machine choice is kept on
    its own merits above.
    """
    if WORKING_MODEL == _CONFIGURED_MODEL and graph_root():
        return graph_root() / ".cache"
    env = os.environ.get("DIALEXIS_INDEX_DIR")
    if env:
        return Path(env).expanduser()
    if WORKING_MODEL != _CONFIGURED_MODEL:
        return WORKING_MODEL.parent / "_cache"
    key = hashlib.sha256(str(WORKING_MODEL.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".cache" / "dialexis" / key


def display_path(p: Path) -> str:
    """Repo-relative when inside ROOT, absolute otherwise.

    Content roots can be relocated outside the repo via DIALEXIS_MODEL_DIR /
    DIALEXIS_AGENDA_DIR / DIALEXIS_ARCHIVE_DIR, and Path.relative_to() RAISES for a
    path outside its argument -- so a bare relative_to(ROOT) turns relocation into a
    crash. Absolute output stays consumable by readers that do `ROOT / recorded`,
    because pathlib absorbs an absolute right operand.

    This is the ONE definition: commit.py's manifest result, stage_inbox.py's archived-
    file record and prune_archive.py's ledger line all route through it, so a path
    written by one and re-resolved by another can never disagree about the format.
    """
    gr = graph_root()
    if gr:
        # Under a graph root, record paths relative to IT, so a content world stays valid
        # when copied or moved (the demo graph, a test's temp copy). resolve_recorded()
        # is the matching reader.
        try:
            return str(Path(p).resolve().relative_to(gr))
        except ValueError:
            pass
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def resolve_recorded(recorded: str) -> Path:
    """The file a display_path() string names. Absolute stays absolute; a relative one is
    the graph root's when a graph root is set, else the checkout's (ROOT / recorded)."""
    gr = graph_root()
    if gr and not Path(recorded).is_absolute():
        return gr / recorded
    return ROOT / recorded


def entity_dir(subdir: str) -> Path:
    """Return the path to an entity type's directory under graph/."""
    return WORKING_MODEL / subdir


def batch_dir(batch_id: str) -> Path:
    """Return the path to a specific batch directory."""
    return BATCHES / batch_id


_DEFAULT_PUBLISH_DIRS = [
    "people", "organizations", "projects", "programs", "events",
    "places", "documents", "dreams", "technology_concepts", "technology_systems",
]


def publish_host() -> str | None:
    return os.environ.get("DIALEXIS_PUBLISH_HOST")


def publish_dir() -> Path | None:
    env = os.environ.get("DIALEXIS_PUBLISH_DIR")
    return Path(env).expanduser() if env else None


def publish_entity_dirs() -> list[str]:
    env = os.environ.get("DIALEXIS_PUBLISH_DIRS")
    if env:
        return [d.strip() for d in env.split(",") if d.strip()]
    return list(_DEFAULT_PUBLISH_DIRS)
