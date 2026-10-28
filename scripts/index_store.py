"""index_store.py - Derived, rebuildable SQLite cache over graph/.

The markdown files ARE the database. This module maintains a DERIVED cache at
``~/.cache/dialexis/<repo-id>/index.sqlite`` — machine-local and never committed,
because a ~10MB fully-rewritten derived file costs more to share than to
regenerate (see ``paths.index_cache_dir``, which also records the Google Drive
history that originally forced it out of the tree). It accelerates the whole-DB
read path that ``entity``, ``resolve``, ``reconcile``, and ``validate`` all share.

It plays two roles from one table:

  * ``lookup_index()``  - cheap ``(name, aliases, type, status, path)`` rows for
    fuzzy matching, without deserializing anything heavy. Turns the ~650ms
    "glob + parse 4,500 files" scan into a single ~3ms query.
  * ``iter_entities()`` - full ``Entity`` objects. For every on-disk ``.md``
    whose ``(mtime, size)`` matches its cached row, the ``Entity`` is
    reconstructed from ``blob_json`` (a fast JSON load) instead of re-parsed
    from markdown (slow regex/section work). Any mismatch falls back to a live
    ``eio.parse_file`` of that file, so the cache can only ever make a read
    FASTER, never WRONG.

Cardinal rule: this is never a source of truth. Deleting the DB changes only
speed, never a single semantic outcome. Every public function degrades to the
original ``glob + parse_file`` behaviour if the DB is missing, stale, or sqlite
raises. Only an authorized writer refreshes/rebuilds it (via ``commit`` /
``reconcile`` / ``sync_index``) — a user-driven session or a configured relay
process; readers never leave it stale for long. The cache being per-machine means
a write on one machine leaves the other's cache cold until its next read, which
is correct-but-slower by design, never wrong.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import paths
import entity_io as eio
from entity_io import Entity
from registry import load_registry

SCHEMA_VERSION = "1"
_MTIME_EPS = 1e-6  # float mtime round-trips through sqlite REAL; compare with slack

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    size       INTEGER NOT NULL,
    name       TEXT NOT NULL,
    aliases    TEXT,
    type       TEXT NOT NULL,
    status     TEXT,
    summary    TEXT,
    blob_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_type ON entities(type);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _db_dir() -> Path:
    """The machine-local cache dir, resolved at call time so it follows
    paths.WORKING_MODEL (which the test harness re-points to a throwaway model).

    NOT under graph/: this file is derived and must never ride a Drive
    sync — see paths.index_cache_dir() for the full rationale."""
    return paths.index_cache_dir()


def _db_path() -> Path:
    return _db_dir() / "index.sqlite"


# --------------------------------------------------------------------------- #
# Entity <-> blob serialization (path is derived, never stored in the blob)
# --------------------------------------------------------------------------- #

_BLOB_FIELDS = ("canonical_name", "entity_type", "status", "summary", "aliases",
                "identifiers", "attributes", "relationships", "perspectives",
                "events", "conflicts", "notes", "sources", "last_updated")


def _entity_to_blob(e: Entity) -> str:
    return json.dumps({f: getattr(e, f) for f in _BLOB_FIELDS}, ensure_ascii=False)


def _blob_to_entity(blob: str, path: Path) -> Entity:
    d = json.loads(blob)
    e = Entity(**{f: d.get(f) for f in _BLOB_FIELDS})
    e.path = path
    return e


# --------------------------------------------------------------------------- #
# Filesystem enumeration
# --------------------------------------------------------------------------- #

def _all_md_paths(reg, entity_type: str | None = None) -> list[Path]:
    out: list[Path] = []
    for _etype, info in reg._entity_types.items():
        if entity_type is not None and _etype != entity_type:
            continue
        d = paths.entity_dir(info["directory"])
        if d.exists():
            out.extend(d.glob("*.md"))
    return out


def _rel(path: Path) -> str:
    return str(path.relative_to(paths.WORKING_MODEL))


def _abs(rel_path: str) -> Path:
    return paths.WORKING_MODEL / rel_path


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection | None:
    """Open the DB, creating the schema if needed. None on any sqlite/OS error."""
    try:
        _db_dir().mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_db_path()))
        con.executescript(_SCHEMA)
        return con
    except (sqlite3.Error, OSError):
        return None


def _row_for(e: Entity, path: Path) -> tuple:
    st = path.stat()
    return (_rel(path), st.st_mtime, st.st_size, e.canonical_name,
            "|".join(e.aliases), e.entity_type, e.status,
            (e.summary or "")[:300], _entity_to_blob(e))


# --------------------------------------------------------------------------- #
# Build / refresh
# --------------------------------------------------------------------------- #

def _parse_all(reg, entity_type: str | None = None) -> list[Entity]:
    """Live parse of every entity file (the slow path / fallback)."""
    out: list[Entity] = []
    for path in _all_md_paths(reg, entity_type):
        try:
            e = eio.parse_file(path)
            e.path = path
            out.append(e)
        except Exception:
            continue
    return out


def write_rows(entities: list[Entity]) -> int:
    """Rebuild the DB from an already-parsed set of entities (no re-parse).

    Used by ``sync_index`` which parses every file once to emit the markdown
    indices; it hands that same set here so the SQLite cache is populated in the
    same pass. Fully replaces the table. Returns rows written (0 on failure)."""
    con = _connect()
    if con is None:
        return 0
    try:
        rows = []
        for e in entities:
            if e.path is None:
                continue
            try:
                rows.append(_row_for(e, e.path))
            except OSError:
                continue
        with con:
            con.execute("DELETE FROM entities")
            con.executemany(
                "INSERT OR REPLACE INTO entities "
                "(path,mtime,size,name,aliases,type,status,summary,blob_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version',?)",
                        (SCHEMA_VERSION,))
        return len(rows)
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def rebuild(registry=None) -> dict:
    """Drop and fully repopulate the cache from disk (parses every file)."""
    reg = registry or load_registry()
    entities = _parse_all(reg)
    n = write_rows(entities)
    return {"rebuilt": n}


def refresh(paths_changed: list[str] | None = None, registry=None) -> dict:
    """Incrementally upsert the given changed paths (relative to graph/)
    and prune rows whose file no longer exists. If the DB is absent/empty or no
    paths are supplied, falls back to a full rebuild."""
    reg = registry or load_registry()
    con = _connect()
    if con is None:
        return {"refreshed": 0}
    try:
        have_rows = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    except sqlite3.Error:
        con.close()
        return {"refreshed": 0}
    if not have_rows or paths_changed is None:
        con.close()
        return {"refreshed": rebuild(reg)["rebuilt"], "mode": "rebuild"}

    upserts, deletes = [], []
    for rp in paths_changed:
        p = _abs(rp) if not os.path.isabs(rp) else Path(rp)
        try:
            rp_norm = _rel(p)
        except ValueError:
            continue
        if p.exists():
            try:
                e = eio.parse_file(p)
                e.path = p
                upserts.append(_row_for(e, p))
            except Exception:
                continue
        else:
            deletes.append((rp_norm,))
    try:
        with con:
            if upserts:
                con.executemany(
                    "INSERT OR REPLACE INTO entities "
                    "(path,mtime,size,name,aliases,type,status,summary,blob_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", upserts)
            if deletes:
                con.executemany("DELETE FROM entities WHERE path=?", deletes)
        return {"refreshed": len(upserts), "pruned": len(deletes)}
    except sqlite3.Error:
        return {"refreshed": 0}
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #

def is_stale(registry=None) -> bool:
    """Cheap drift guard for the LOOKUP path (no parsing): the DB is stale if it
    is missing, the schema version differs, or the SET of on-disk entity paths
    differs from the SET of cached paths (catches add / delete / rename). Content
    edits to an existing path are covered by the writer refreshing on commit and
    by the per-file (mtime,size) check in iter_entities()."""
    if not _db_path().exists():
        return True
    reg = registry or load_registry()
    con = _connect()
    if con is None:
        return True
    try:
        ver = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not ver or ver[0] != SCHEMA_VERSION:
            return True
        db_paths = {r[0] for r in con.execute("SELECT path FROM entities")}
    except sqlite3.Error:
        return True
    finally:
        con.close()
    disk_paths = {_rel(p) for p in _all_md_paths(reg)}
    return db_paths != disk_paths


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #

def lookup_index(registry=None) -> list[dict]:
    """Return ``[{name, aliases, type, status, path}, ...]`` for every entity.

    Fast path: one SELECT over the cache (rebuilds first if the path-set drifted).
    Fallback: if sqlite is unavailable or errors, parse the tree in-memory and
    return the same shape (correct, just slow)."""
    reg = registry or load_registry()
    try:
        if is_stale(reg):
            rebuild(reg)
        con = _connect()
        if con is not None:
            try:
                cur = con.execute(
                    "SELECT name,aliases,type,status,path FROM entities")
                out = [{"name": n, "aliases": a.split("|") if a else [],
                        "type": t, "status": s, "path": p}
                       for (n, a, t, s, p) in cur.fetchall()]
                if out:
                    return out
            finally:
                con.close()
    except sqlite3.Error:
        pass
    # Fallback: live parse.
    return [{"name": e.canonical_name,
             "aliases": e.aliases,
             "type": e.entity_type,
             "status": e.status,
             "path": _rel(e.path)}
            for e in _parse_all(reg) if e.path is not None]


def iter_entities(registry=None, entity_type: str | None = None
                  ) -> Iterator[Entity]:
    """Yield a full ``Entity`` for every on-disk entity file (optionally one type).

    Parse cache with a conservative per-file gate: a cached ``blob_json`` is
    trusted ONLY when the file's current ``(mtime, size)`` matches the row.
    Any mismatch (or missing row, or missing DB) re-parses that file from disk —
    so this is safe for ``validate`` to depend on: a changed file is always read
    fresh, never served from a stale blob."""
    reg = registry or load_registry()
    md_paths = _all_md_paths(reg, entity_type)

    cache: dict[str, tuple[float, int, str]] = {}
    con = _connect()
    if con is not None:
        try:
            q = "SELECT path,mtime,size,blob_json FROM entities"
            params: tuple = ()
            if entity_type is not None:
                q += " WHERE type=?"
                params = (entity_type,)
            for rp, mtime, size, blob in con.execute(q, params):
                cache[rp] = (mtime, size, blob)
        except sqlite3.Error:
            cache = {}
        finally:
            con.close()

    for path in md_paths:
        rp = _rel(path)
        row = cache.get(rp)
        if row is not None:
            try:
                st = path.stat()
                if abs(st.st_mtime - row[0]) < _MTIME_EPS and st.st_size == row[1]:
                    yield _blob_to_entity(row[2], path)
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass  # fall through to a live parse
        try:
            e = eio.parse_file(path)
            e.path = path
            yield e
        except Exception:
            continue


def main(argv: list[str]) -> int:
    """CLI: rebuild the cache, or report its status."""
    args = argv[1:] if argv and not argv[0].endswith(".py") else argv[1:]
    if "--status" in args:
        exists = _db_path().exists()
        size = _db_path().stat().st_size if exists else 0
        stale = is_stale()
        print(f"index.sqlite: {'present' if exists else 'absent'} "
              f"({size:,} bytes), stale={stale}")
        return 0
    stats = rebuild()
    print(f"Rebuilt index.sqlite: {stats['rebuilt']} entities")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
