"""commit.py - Apply a transaction manifest atomically. The core reliability fix.

The Database Integration Agent (an LLM) emits a JSON manifest of *intended mutations* and
performs NO file operations itself. This script - deterministic, the agent cannot route
around it - validates the manifest against the registry, applies every mutation in memory,
enforces reciprocal edges, sets mechanical fields, then commits all-or-nothing with rollback.

Guarantees the agent's prose could only ever *probably* deliver:
  * atomic         - a transaction journal restores every touched file on any failure
  * validated      - unknown types/relationships/confidence values are rejected before writing
  * non-clobbering - a fact that contradicts an existing one HALTS unless an explicit
                     add_conflict op for that entity accompanies it (kills silent
                     last-writer-wins, the reason the old system showed "0 conflicts")
  * symmetric      - every relationship writes its inverse on the target automatically

Usage: python commit.py <manifest.json>     # exit 0 on success, nonzero (HALT) on refusal
"""
from __future__ import annotations
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import paths
import entity_io as eio
import temporal
from entity_io import Entity
from naming import slug, NamingError
from registry import load_registry, RegistryError


class CommitError(Exception):
    """A transaction was refused. Nothing was written. The message states why."""


class Transaction:
    """Journals original file bytes so any failure restores prior on-disk state exactly."""
    def __init__(self):
        self._seen: dict[Path, bytes | None] = {}

    def write(self, path: Path, text: str) -> None:
        if path not in self._seen:
            self._seen[path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def rollback(self) -> None:
        for path, original in reversed(list(self._seen.items())):
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)


class Committer:
    def __init__(self, registry, conflict_entities: set[tuple[str, str]]):
        self.reg = registry
        self._conflict_entities = conflict_entities   # (type, name) carrying an add_conflict op
        self._cache: dict[Path, Entity] = {}
        self._dirty: set[Path] = set()
        # Ledger state (lazy-loaded on first action op)
        self._ledger_actions: list | None = None
        self._ledger_dirty: bool = False
        self._archive_pending: list = []
        self._renamed_entities: list[Path] = []  # old entity files to remove after successful commit

    def _path_for(self, entity_type: str, canonical_name: str) -> Path:
        return paths.entity_dir(self.reg.directory_for(entity_type)) / f"{slug(canonical_name)}.md"

    def _find_existing_variant(self, entity_type: str, canonical_name: str) -> Path | None:
        """Search for an existing entity file that matches without a middle name/initial.

        Handles the common case where 'Desmond J Pellew' should resolve to an existing
        'desmond_pellew.md' rather than creating a duplicate 'desmond_j_pellew.md'.
        """
        # Middle-name elision is a person-name phenomenon. Applying it to other types
        # (e.g. orgs) falsely collapses distinct names that merely share a first and
        # last token, like a Group-level unit name onto a Division-level one.
        if entity_type != "person":
            return None
        parts = canonical_name.split()
        if len(parts) < 3:
            return None
        entity_dir = paths.entity_dir(self.reg.directory_for(entity_type))
        if not entity_dir.is_dir():
            return None
        first = parts[0].lower()
        last = parts[-1].lower()
        # Build candidate: first_last.md (dropping all middle parts)
        candidate_slug = slug(f"{parts[0]} {parts[-1]}")
        candidate = entity_dir / f"{candidate_slug}.md"
        if candidate.exists():
            return candidate
        # Also try globbing first*last for hyphenated or multi-word last names
        pattern = f"{first}*{last}.md"
        matches = list(entity_dir.glob(pattern))
        # Filter to avoid false positives: matched file's stem must start with first
        # and end with last, with at most 1 fewer segment than the full name
        full_slug = slug(canonical_name)
        for m in matches:
            m_parts = m.stem.split("_")
            full_parts = full_slug.split("_")
            if (m_parts[0] == full_parts[0] and m_parts[-1] == full_parts[-1]
                    and len(m_parts) < len(full_parts)):
                return m
        return None

    def _maybe_warn_confounder_collapse(self, canonical_name: str, variant: Path) -> None:
        """Non-blocking safeguard: when a middle-initial variant collapses onto an
        existing base person, warn (do NOT halt) if the graph also holds another
        same-(first,last) person at the SAME top-level institution — i.e. a possible
        wrong merge. Best-effort and fully guarded; never affects the commit."""
        try:
            import sys as _sys
            import entity_matching as em
            first, _m, last, _p = em.normalize_name(canonical_name)
            if not first or not last:
                return
            base = self._cache.get(variant) or eio.parse_file(variant)
            base_inst = em.person_institutions(base)
            if not base_inst:
                return
            sibs = []
            for p in variant.parent.glob(f"{first}*{last}.md"):
                if p == variant:
                    continue
                try:
                    other = eio.parse_file(p)
                except Exception:
                    continue
                of, _om, ol, _op = em.normalize_name(other.canonical_name)
                if of == first and ol == last and (em.person_institutions(other) & base_inst):
                    sibs.append(other.canonical_name)
            if sibs:
                YEL, RST = "\033[93m", "\033[0m"
                print(f"{YEL}WARNING: collapsed {canonical_name!r} onto existing "
                      f"{base.canonical_name!r}, but same-institution same-name "
                      f"person(s) also exist: {sibs}. Verify this is not a wrong "
                      f"merge (prune + re-add with a middle initial if it is).{RST}",
                      file=_sys.stderr)
        except Exception:
            pass

    # Site-specific: a bare org-unit code name ("<Site> Section 4120") that should merge onto an
    # existing descriptive variant. Pattern comes from the optional scripts/site_local.py.
    try:
        import site_local as _site_local
        _ORG_UNIT_RE = getattr(_site_local, "ORG_UNIT_RE", None)
    except ImportError:
        _ORG_UNIT_RE = None

    def _find_org_variant(self, canonical_name: str) -> str | None:
        """Resolve a bare org-code name onto an existing entity that describes the same unit
        under a longer name (site-local pattern; no-op in the public engine).

        Matches the exact "<prefix> <level> <code>" head, never a looser token basis, so distinct
        units are never collapsed. Returns the existing canonical name, or None.
        """
        if self._ORG_UNIT_RE is None:
            return None
        m = self._ORG_UNIT_RE.match(canonical_name)
        if not m:
            return None
        bare_slug = slug(m.group(1))
        entity_dir = paths.entity_dir(self.reg.directory_for("organization"))
        if not entity_dir.is_dir():
            return None
        # Exact bare file exists → no remap (handled by the normal path).
        if (entity_dir / f"{bare_slug}.md").exists():
            return None
        for cand in sorted(entity_dir.glob(f"{bare_slug}_*.md")):
            return eio.parse_file(cand).canonical_name
        return None

    def _resolve_name(self, entity_type: str | None, name: str | None) -> str | None:
        """Canonicalize an incoming name onto an existing entity where a bare org-code
        name should merge into an already-stored descriptive one. No-op for non-orgs
        and for names that already resolve to an exact file."""
        if entity_type != "organization" or not name:
            return name
        p = self._path_for(entity_type, name)
        if p.exists() or p in self._cache:
            return name
        return self._find_org_variant(name) or name

    def _load(self, entity_type: str, canonical_name: str) -> Entity:
        p = self._path_for(entity_type, canonical_name)
        if p in self._cache:
            return self._cache[p]
        if p.exists():
            e = eio.parse_file(p)
        else:
            # Before creating a new entity, check if a variant exists (e.g., without middle initial)
            variant = self._find_existing_variant(entity_type, canonical_name)
            if variant:
                self._maybe_warn_confounder_collapse(canonical_name, variant)
                e = self._cache.get(variant) or eio.parse_file(variant)
                old_name = e.canonical_name
                e.canonical_name = canonical_name
                e.path = p
                self._cache[p] = e
                self._cache.pop(variant, None)
                self._dirty.discard(variant)
                self._dirty.add(p)
                self._renamed_entities.append(variant)
                # Repoint inbound references from the old (shorter) stem onto the upgraded
                # name, mirroring rename_entity. Without this, every inbound edge to the old
                # stem is orphaned once the old file is deleted after commit.
                directory = self.reg.directory_for(entity_type)
                self._repoint_refs(directory, variant.stem, directory, p.stem,
                                   canonical_name, old_label=old_name,
                                   exclude={variant, p})
                return e
            e = Entity(canonical_name=canonical_name, entity_type=entity_type, status="Unknown")
        e.path = p
        self._cache[p] = e
        return e

    def _touch(self, e: Entity, when: str) -> None:
        e.last_updated = when
        self._dirty.add(e.path)

    def _check_derived(self, meta: dict, where: str) -> str | None:
        """Validate the optional `derived` field and return it.

        Called on every fact and edge, whether or not the manifest set an explicit confidence —
        otherwise a manifest could smuggle an unregistered derivation past the gate simply by
        naming its own confidence.
        """
        derived = meta.get("derived")
        if derived is not None and not self.reg.is_derivation_type(derived):
            raise CommitError(
                f"{where}: unknown derivation type {derived!r}. Allowed: "
                f"{', '.join(self.reg.derivation_types) or '(none registered)'}.")
        return derived

    def _floor_confidence(self, meta: dict, where: str) -> str:
        """Default confidence for a fact/edge: the source's floor, discounted if derived.

        `source` names the original medium; `derived` names the artifact actually read. A fact
        taken from a summary of a transcript cannot be as trustworthy as one taken from the
        transcript, so the floor drops a step. Done here, in code, because the whole point is
        that an agent does not get to price its own second-hand reading.
        """
        floor = self.reg.default_confidence(meta["source"])
        return self.reg.discount_confidence(floor) if meta.get("derived") else floor

    def _validate_fact(self, f: dict, where: str, when: str) -> dict:
        f = dict(f)
        # last_verified is mechanical: only commit.py may set it (like a reciprocal edge).
        if "last_verified" in f:
            raise CommitError(
                f"{where}: 'last_verified' is a mechanical field; a manifest may not set it.")
        f["source"] = f.get("source") or "manual"
        if not self.reg.is_source_type(f["source"]):
            raise CommitError(f"{where}: unknown source type {f['source']!r}.")
        self._check_derived(f, where)
        f["confidence"] = f.get("confidence") or self._floor_confidence(f, where)
        if not self.reg.is_confidence(f["confidence"]):
            raise CommitError(f"{where}: invalid confidence {f['confidence']!r}.")
        # temporal_class is semantic: validate it when the manifest opines. When silent we
        # leave it absent here so merge_facts can keep any prior curated value; a genuinely
        # new fact is classified by the post-merge fill in upsert_entity.
        if "temporal_class" in f and not self.reg.is_temporal_class(f["temporal_class"]):
            raise CommitError(f"{where}: invalid temporal_class {f['temporal_class']!r}.")
        f["last_verified"] = when      # stamp = commit time (reinforcement refreshes it)
        return f

    def upsert_entity(self, op: dict, when: str) -> None:
        et, name = op.get("entity_type"), op.get("canonical_name")
        if name is None and "entity_name" in op:
            raise CommitError(
                f"upsert_entity: found 'entity_name' but the correct field is 'canonical_name'. "
                f"Rename the field and retry.")
        if not name or not name.strip():
            raise CommitError(
                f"upsert_entity: missing or empty 'canonical_name'. "
                f"Each operation requires 'canonical_name' (not 'entity_name' or 'name').\n"
                f"Full operation: {op}")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"upsert_entity: unknown entity type {et!r} for {name!r}. "
                              f"Add it to the registry first.\n"
                              f"Full operation: {op}")
        name = self._resolve_name(et, name) or name
        e = self._load(et, name)

        if op.get("status"):
            if op["status"] not in self.reg.entity_statuses:
                raise CommitError(f"{name}: invalid status {op['status']!r}.")
            e.status = op["status"]
        if op.get("summary"):
            e.summary = op["summary"]
        for a in op.get("aliases", []):
            if a and a not in e.aliases and a != e.canonical_name:
                e.aliases.append(a)

        for sect in ("identifiers", "attributes"):
            incoming = [self._validate_fact(f, f"{name}/{sect}", when) for f in op.get(sect, [])]
            merged, contradictions = eio.merge_facts(getattr(e, sect), incoming)
            if contradictions and (et, name) not in self._conflict_entities:
                detail = "; ".join(f"{k}: {old} vs {new!r}" for k, old, new in contradictions)
                raise CommitError(
                    f"{name}/{sect}: contradicting values without an add_conflict op "
                    f"[{detail}]. Emit add_conflict or refine the value - refusing to "
                    f"silently overwrite.")
            # Classify any fact that still lacks a temporal_class (a new fact, or a legacy
            # one being touched for the first time since the field existed). Existing
            # curated values are preserved by merge_facts; last_verified is never fabricated.
            for fct in merged:
                if "temporal_class" not in fct:
                    fct["temporal_class"] = temporal.infer_temporal_class(
                        fct.get("key", ""), fct.get("value", ""))
            setattr(e, sect, merged)

        for r in op.get("relationships", []):
            self._add_relationship(e, r, when)
        for p in op.get("perspectives", []):
            self._add_perspective(e, p)
        for ev in op.get("events", []):
            ev = dict(ev)
            # Events go through the same derivation check and discount as facts and edges. They
            # were missed when `derived` was added, so an event read from a summary validated as
            # though it were read from the source and kept the undiscounted floor — the exact
            # overstatement the field exists to prevent, surviving in one code path.
            where = f"{e.canonical_name} event {ev.get('date', '?')}"
            self._check_derived(ev, where)
            if ev.get("source") and not ev.get("confidence"):
                ev["confidence"] = self._floor_confidence(ev, where)
            if not any(x.get("date") == ev.get("date") and x.get("desc") == ev.get("desc")
                       for x in e.events):
                e.events.append(ev)
        for s in op.get("sources", []):
            if not any(x.get("archive") == s.get("archive") and x.get("name") == s.get("name")
                       for x in e.sources):
                e.sources.append(s)
        if op.get("notes"):
            if e.notes and e.notes != "(none)":
                e.notes = e.notes + "\n\n" + op["notes"]
            else:
                e.notes = op["notes"]

        self._touch(e, when)

    def _add_perspective(self, e: Entity, p: dict) -> None:
        if p.get("status") and p["status"] not in self.reg.perspective_statuses:
            raise CommitError(f"{e.canonical_name}: invalid perspective status {p['status']!r}.")
        if p.get("evidence_type") and p["evidence_type"] not in self.reg.evidence_types:
            raise CommitError(f"{e.canonical_name}: invalid evidence type {p['evidence_type']!r}.")
        # A title is load-bearing, not decoration: dedupe below keys on it, so two untitled
        # perspectives collide and the second is silently dropped.
        title = (p.get("title") or "").strip()
        if not title or title.lower() == "untitled":
            raise CommitError(
                f"{e.canonical_name}: a perspective needs a distinct 'title' — it is the dedupe "
                f"key, so 'Untitled' silently swallows the next one.")
        # A stance must say WHOSE it is, or it reads as fact. Either name the holder, or declare
        # it as the system's own reading with evidence_type=inferred. This is the distinction
        # between "X thinks Y is risky" and "Y is risky", and losing it is how a judgment
        # becomes a claim about the world.
        if not (p.get("holder") or "").strip() and p.get("evidence_type") != "inferred":
            raise CommitError(
                f"{e.canonical_name}: perspective {title!r} has no 'holder'. Name whose view it "
                f"is, or set evidence_type='inferred' to mark it as our own reading rather than "
                f"someone's stated position.")
        if not any(x.get("title") == title for x in e.perspectives):
            e.perspectives.append(p)

    def _add_relationship(self, e: Entity, r: dict, when: str) -> None:
        rel = r.get("rel_type")
        if not self.reg.is_relationship(rel):
            raise CommitError(f"{e.canonical_name}: unknown relationship {rel!r}. "
                              f"Add it (with inverse) to the registry.")
        tgt_type, tgt_name = r.get("target_type"), r.get("target_name")
        if not self.reg.is_entity_type(tgt_type):
            raise CommitError(f"{e.canonical_name}: relationship target type {tgt_type!r} unknown.\n"
                              f"Relationship: {r}\n"
                              f"Entity type: {e.entity_type}")
        # Provenance is REQUIRED on an edge, and omission is not the same as unknown. An edge
        # with no source cannot be audited or re-verified, and thousands of them accumulated
        # precisely because this was silently optional. `source=unknown` is available and is a
        # deliberate declaration; leaving it out is an error the manifest must fix.
        src = r.get("source")
        if not src:
            raise CommitError(
                f"{e.canonical_name}: relationship {rel!r} -> {r.get('target_name')!r} has no "
                f"'source'. Every edge must name where it came from. Use the source type of the "
                f"material that asserted it, or 'unknown' if it genuinely cannot be established.")
        if not self.reg.is_source_type(src):
            raise CommitError(f"{e.canonical_name}: unknown source type {src!r} on "
                              f"relationship {rel!r}.")
        derived = self._check_derived(r, f"{e.canonical_name} relationship {rel!r}")
        conf = r.get("confidence") or self._floor_confidence(
            r, f"{e.canonical_name} relationship {rel!r}")
        if not self.reg.is_confidence(conf):
            raise CommitError(f"{e.canonical_name}: invalid relationship confidence {conf!r}.")
        if "last_verified" in r:
            raise CommitError(
                f"{e.canonical_name}: 'last_verified' is a mechanical field; a manifest may "
                f"not set it on a relationship.")
        tclass = r.get("temporal_class") or temporal.infer_rel_temporal_class(rel)
        if not self.reg.is_temporal_class(tclass):
            raise CommitError(f"{e.canonical_name}: invalid temporal_class {tclass!r}.")
        dt = r.get("date") or when
        tgt_name = self._resolve_name(tgt_type, tgt_name) or tgt_name
        tgt_stem = slug(tgt_name)

        # Provenance travels with the reciprocal: the inverse edge is the same claim seen from
        # the other end, so it has the same source. Omitting it here would leave half of every
        # edge pair unauditable.
        prov = {"source": src}
        if derived:
            prov["derived"] = derived

        self._upsert_edge(e.relationships, {
            "rel_type": rel, "target_name": tgt_name,
            "target_dir": self.reg.directory_for(tgt_type), "target_stem": tgt_stem,
            "confidence": conf, "temporal_class": tclass, **prov,
            "date": dt, "last_verified": when})
        self._dirty.add(e.path)

        inverse = self.reg.inverse_of(rel)
        inv_rel = rel if inverse is None else inverse   # None == symmetric
        target = self._load(tgt_type, tgt_name)
        self._upsert_edge(target.relationships, {
            "rel_type": inv_rel, "target_name": e.canonical_name,
            "target_dir": self.reg.directory_for(e.entity_type), "target_stem": e.stem,
            "confidence": conf, "temporal_class": tclass, **prov,
            "date": dt, "last_verified": when})
        self._touch(target, when)

    @staticmethod
    def _upsert_edge(relationships: list[dict], edge: dict) -> None:
        """Append a relationship, or refresh last_verified (and backfill temporal_class and
        provenance) on an existing (rel_type, target_stem) edge. Re-asserting re-verifies.

        Provenance upgrades but never downgrades: an edge carrying no source, or the placeholder
        `unknown`, adopts a real source when one is re-asserted, so the backfilled `unknown`
        population drains as normal ingestion touches those edges again. A real source is never
        overwritten by a different one — that is a conflict for a human, not a silent rewrite.
        """
        for existing in relationships:
            if (existing.get("rel_type") == edge["rel_type"]
                    and existing.get("target_stem") == edge["target_stem"]):
                existing["last_verified"] = edge["last_verified"]
                existing.setdefault("temporal_class", edge["temporal_class"])
                incoming = edge.get("source")
                if incoming and incoming != "unknown" \
                        and existing.get("source", "unknown") == "unknown":
                    existing["source"] = incoming
                    if edge.get("derived"):
                        existing["derived"] = edge["derived"]
                    else:
                        existing.pop("derived", None)
                return
        relationships.append(dict(edge))

    def _load_ledger(self) -> list:
        """Lazy-load the action ledger."""
        if self._ledger_actions is None:
            from kairos.parse import parse_ledger
            self._ledger_actions = parse_ledger(paths.AGENDA_LEDGER)
        return self._ledger_actions

    def upsert_action(self, op: dict, when: str) -> None:
        """Create or update an action in the ledger."""
        from kairos.parse import Action, next_id, validate_action, TODAY_CAP, STATUSES
        actions = self._load_ledger()

        action_id = op.get("id")
        if action_id:
            existing = next((a for a in actions if a.id == action_id), None)
            if existing is None:
                raise CommitError(f"upsert_action: no action with id {action_id!r}")
            # Update provided fields
            for field_name in ("title", "next_action", "project", "for_event", "for_dream",
                               "due", "estimate", "energy", "flavor", "status",
                               "carry_count", "last_review"):
                if field_name in op:
                    setattr(existing, field_name, op[field_name])
            target = existing
        else:
            # New action
            target = Action(
                id=next_id(actions),
                title=op.get("title", ""),
                next_action=op.get("next_action", ""),
                project=op.get("project"),
                for_event=op.get("for_event"),
                for_dream=op.get("for_dream"),
                due=op.get("due"),
                estimate=op.get("estimate", "short"),
                energy=op.get("energy", "shallow"),
                flavor=op.get("flavor", "admin"),
                status=op.get("status", "active"),
                created=when,
                carry_count=0,
                last_review=when,
            )
            actions.append(target)

        # Enforce TODAY_CAP
        if target.status == "today":
            today_count = sum(1 for a in actions if a.status == "today" and a.id != target.id)
            if today_count >= TODAY_CAP:
                raise CommitError(
                    f"upsert_action: TODAY_CAP ({TODAY_CAP}) exceeded. "
                    f"Drop an existing today item before adding another.")

        # Validate
        errors = validate_action(target)
        if errors:
            raise CommitError(f"upsert_action: invalid action {target.id}: {'; '.join(errors)}")

        self._ledger_dirty = True

    def archive_action(self, op: dict, when: str) -> None:
        """Move an action from the active ledger to the quarterly archive."""
        from kairos.parse import Action
        actions = self._load_ledger()
        action_id = op.get("id")
        if not action_id:
            raise CommitError("archive_action: id is required")

        target = next((a for a in actions if a.id == action_id), None)
        if target is None:
            raise CommitError(f"archive_action: no action with id {action_id!r}")

        # Set terminal status
        terminal_status = op.get("status", "done")
        if terminal_status not in ("done", "released", "expired"):
            raise CommitError(f"archive_action: status must be done/released/expired, got {terminal_status!r}")
        target.status = terminal_status

        # Remove from active ledger and queue for archive
        actions.remove(target)
        self._archive_pending.append(target)
        self._ledger_dirty = True

    def add_conflict(self, op: dict, when: str) -> None:
        e = self._load(op["entity_type"], op["canonical_name"])
        title = op["title"]
        if any(c.get("title") == title for c in e.conflicts):
            raise CommitError(f"{e.canonical_name}: duplicate conflict heading {title!r}.")
        e.conflicts.append({
            "title": title, "value_a": op.get("value_a", ""), "value_b": op.get("value_b", ""),
            "status": "UNRESOLVED", "do_not_query": "true" if op.get("do_not_query") else "false",
            "resolution_note": ""})
        self._touch(e, when)

    def resolve_conflict(self, op: dict, when: str) -> None:
        e = self._load(op["entity_type"], op["canonical_name"])
        for c in e.conflicts:
            if c.get("title") == op["title"]:
                c["status"] = "RESOLVED"
                c["resolution_note"] = op.get("resolution_note", "")
                self._touch(e, when)
                return
        raise CommitError(f"{e.canonical_name}: no conflict titled {op['title']!r} to resolve.")

    def _iter_entity_paths(self):
        """Yield every entity file path across all registered entity-type directories."""
        for info in self.reg._entity_types.values():
            d = paths.entity_dir(info["directory"])
            if d.is_dir():
                yield from d.glob("*.md")

    def _load_path(self, path: Path) -> Entity:
        """Load an entity by file path, reusing the in-memory cache if present."""
        if path in self._cache:
            return self._cache[path]
        e = eio.parse_file(path)
        e.path = path
        self._cache[path] = e
        return e

    def rename_entity(self, op: dict, when: str) -> None:
        """Rename an entity's canonical name, moving its file and repointing every
        inbound reference across the knowledge base.

        Postconditions enforced deterministically:
          * old file exists; new file does not (a colliding target is a MERGE, refused here)
          * canonical_name + on-disk slug/heading updated to new_name
          * every relationship on any other entity that targets old_stem is repointed to
            new_stem / new_name (this is what preserves reciprocity after the stem changes)
          * inline [[dir/old_stem|label]] wikilinks in summary/notes prose are repointed too
          * old file removed after the atomic write succeeds
        """
        et = op.get("entity_type")
        old_name = op.get("old_name")
        new_name = op.get("new_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"rename_entity: unknown entity type {et!r}.")
        if not old_name or not old_name.strip() or not new_name or not new_name.strip():
            raise CommitError(
                "rename_entity: both 'old_name' and 'new_name' are required and non-empty.")
        if old_name == new_name:
            raise CommitError(f"rename_entity: old_name and new_name are identical ({old_name!r}).")

        directory = self.reg.directory_for(et)
        old_path = self._path_for(et, old_name)
        new_path = self._path_for(et, new_name)
        old_stem = slug(old_name)
        new_stem = slug(new_name)

        if not old_path.exists() and old_path not in self._cache:
            raise CommitError(
                f"rename_entity: no entity named {old_name!r} in {directory}/ to rename.")
        if new_stem != old_stem and (new_path.exists() or new_path in self._cache):
            raise CommitError(
                f"rename_entity: target {new_name!r} already exists in {directory}/. "
                f"This is a MERGE, not a rename - refusing to clobber. Merge the duplicates first.")

        # 1. Rename the entity itself.
        e = self._load(et, old_name)
        e.canonical_name = new_name
        e.path = new_path
        self._cache[new_path] = e
        if new_path != old_path:
            self._cache.pop(old_path, None)
            # Drop the old path from _dirty too: an earlier op in this manifest may have
            # dirtied it, and a path in _dirty with no _cache entry breaks the write loop
            # and miscounts entities_written.
            self._dirty.discard(old_path)
            self._renamed_entities.append(old_path)
        self._touch(e, when)

        # 2. Repoint every inbound reference across the whole KB.
        self._repoint_refs(directory, old_stem, directory, new_stem, new_name,
                           old_label=old_name, exclude={old_path, new_path})

    def _repoint_refs(self, old_directory: str, old_stem: str,
                      new_directory: str, new_stem: str,
                      new_name: str, old_label: str, exclude: set[Path]) -> None:
        """Repoint every reference to (old_directory, old_stem) onto
        (new_directory, new_stem) across the whole KB - both structured relationships
        and inline prose wikilinks in summary/notes. old_directory and new_directory
        differ only for a cross-type reclassify; same-type rename/merge pass the same
        directory for both. Drops any self-loop it creates (a rel whose new target is the
        entity itself), which is how a merge collapses keep<->merge edges. Entities in
        `exclude` are skipped (the renamed/merged entity's own file)."""
        import re
        prose_pat = re.compile(
            rf"\[\[{re.escape(old_directory)}/{re.escape(old_stem)}\|([^\]]+)\]\]")

        def fix_prose(text: str) -> str:
            def repl(m):
                label = m.group(1)
                if label == old_label:
                    label = new_name
                return f"[[{new_directory}/{new_stem}|{label}]]"
            return prose_pat.sub(repl, text or "")

        all_paths = set(self._iter_entity_paths()) | set(self._cache.keys())
        for path in all_paths:
            if path in exclude:
                continue
            if not path.exists() and path not in self._cache:
                continue
            other = self._load_path(path)
            changed = False
            kept = []
            for r in other.relationships:
                if r.get("target_dir") == old_directory and r.get("target_stem") == old_stem:
                    r["target_dir"] = new_directory
                    r["target_stem"] = new_stem
                    r["target_name"] = new_name
                    changed = True
                # Drop a self-loop (e.g. keep formerly pointed at merge).
                if (r.get("target_dir") == new_directory and r.get("target_stem") == new_stem
                        and other.stem == new_stem
                        and self.reg.directory_for(other.entity_type) == new_directory):
                    changed = True
                    continue
                kept.append(r)
            other.relationships = kept
            new_summary = fix_prose(other.summary)
            if new_summary != other.summary:
                other.summary = new_summary
                changed = True
            new_notes = fix_prose(other.notes)
            if new_notes != other.notes:
                other.notes = new_notes
                changed = True
            if changed:
                self._dirty.add(path)

    def merge_entity(self, op: dict, when: str) -> None:
        """Fold a duplicate entity into a keeper, then delete the duplicate.

        `keep_name` is authoritative: on any attribute/identifier key collision the keeper's
        value wins and the duplicate's is dropped (the caller has already decided these are the
        same entity and which record is canonical). Everything non-colliding is folded in -
        aliases (plus the duplicate's own name), new relationships, events, perspectives,
        conflicts, sources, and notes. Every inbound reference to the duplicate is repointed to
        the keeper (reusing the rename machinery), and the duplicate file is removed.
        Both entities must exist.
        """
        et = op.get("entity_type")
        keep_name = op.get("keep_name")
        merge_name = op.get("merge_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"merge_entity: unknown entity type {et!r}.")
        if not keep_name or not keep_name.strip() or not merge_name or not merge_name.strip():
            raise CommitError(
                "merge_entity: both 'keep_name' and 'merge_name' are required and non-empty.")
        if keep_name == merge_name:
            raise CommitError(f"merge_entity: keep_name and merge_name are identical ({keep_name!r}).")

        directory = self.reg.directory_for(et)
        keep_path = self._path_for(et, keep_name)
        merge_path = self._path_for(et, merge_name)
        keep_stem = slug(keep_name)
        merge_stem = slug(merge_name)
        if not keep_path.exists() and keep_path not in self._cache:
            raise CommitError(f"merge_entity: keeper {keep_name!r} not found in {directory}/.")
        if not merge_path.exists() and merge_path not in self._cache:
            raise CommitError(f"merge_entity: duplicate {merge_name!r} not found in {directory}/.")

        keep = self._load(et, keep_name)
        merge = self._load(et, merge_name)

        # 1. Fold the duplicate's content into the keeper (keeper wins on collisions).
        self._fold_entity(keep, merge)
        self._touch(keep, when)

        # 2. Drop the duplicate from the working set and queue its file for deletion.
        self._cache.pop(merge_path, None)
        self._dirty.discard(merge_path)
        self._renamed_entities.append(merge_path)

        # 3. Repoint every inbound reference (merge_stem -> keep_stem) across the KB,
        #    dropping any keep<->merge self-loop this creates.
        self._repoint_refs(directory, merge_stem, directory, keep_stem, keep_name,
                           old_label=merge_name, exclude={merge_path})

    def _fold_entity(self, keep: Entity, merge: Entity) -> None:
        """Fold merge's content into keep. keep is authoritative: on any identifier/attribute
        key collision keep's value wins. Everything non-colliding is appended - aliases (plus
        merge's own canonical name), new relationships, events, perspectives, conflicts,
        sources, and notes. Does NOT touch the summary (keep's summary is retained) and does
        NOT repoint inbound references - the caller owns those steps."""
        for alias in [merge.canonical_name, *merge.aliases]:
            if alias and alias != keep.canonical_name and alias not in keep.aliases:
                keep.aliases.append(alias)
        for sect in ("identifiers", "attributes"):
            keep_facts = getattr(keep, sect)
            keys = {f["key"] for f in keep_facts}
            for f in getattr(merge, sect):
                if f["key"] not in keys:
                    keep_facts.append(f)
                    keys.add(f["key"])
        for r in merge.relationships:
            if not eio.relationship_present(keep.relationships, r["rel_type"], r["target_stem"]):
                keep.relationships.append(dict(r))
        for ev in merge.events:
            if not any(x.get("date") == ev.get("date") and x.get("desc") == ev.get("desc")
                       for x in keep.events):
                keep.events.append(ev)
        for p in merge.perspectives:
            if not any(x.get("title") == p.get("title") for x in keep.perspectives):
                keep.perspectives.append(p)
        for c in merge.conflicts:
            if not any(x.get("title") == c.get("title") for x in keep.conflicts):
                keep.conflicts.append(c)
        for s in merge.sources:
            if not any(x.get("archive") == s.get("archive") and x.get("name") == s.get("name")
                       for x in keep.sources):
                keep.sources.append(s)
        if merge.notes and merge.notes.strip() and merge.notes.strip() != "(none)":
            if keep.notes and keep.notes.strip() and keep.notes.strip() != "(none)":
                keep.notes = keep.notes + "\n\n" + merge.notes
            else:
                keep.notes = merge.notes

    def reclassify_entity(self, op: dict, when: str) -> None:
        """Change an entity's type, repointing every inbound reference across the KB.

        Fields: from_type, from_name, to_type, to_name (to_name defaults to from_name).
        If the destination (to_type, to_name) already exists, the source is folded into it
        (a cross-type merge; the destination is the keeper on collisions) and the source
        file is removed. If the destination is free, the source is retyped in place (its
        file moves to the destination directory/slug and its Type line is rewritten).
        Reciprocity is healed by the unconditional reconcile pass that follows commit.
        Refuses when from_type == to_type (that is a rename_entity or merge_entity)."""
        from_type = op.get("from_type")
        to_type = op.get("to_type")
        from_name = op.get("from_name")
        to_name = op.get("to_name") or from_name
        for t in (from_type, to_type):
            if not self.reg.is_entity_type(t):
                raise CommitError(f"reclassify_entity: unknown entity type {t!r}.")
        if not from_name or not from_name.strip():
            raise CommitError("reclassify_entity: 'from_name' is required and non-empty.")
        if from_type == to_type:
            raise CommitError(
                f"reclassify_entity: from_type and to_type are identical ({from_type!r}). "
                f"Use rename_entity (name change) or merge_entity (same-type dedup) instead.")

        from_dir = self.reg.directory_for(from_type)
        to_dir = self.reg.directory_for(to_type)
        from_path = self._path_for(from_type, from_name)
        to_path = self._path_for(to_type, to_name)
        from_stem = slug(from_name)
        to_stem = slug(to_name)
        if not from_path.exists() and from_path not in self._cache:
            raise CommitError(f"reclassify_entity: no entity named {from_name!r} in {from_dir}/.")

        src = self._load(from_type, from_name)

        if to_path.exists() or to_path in self._cache:
            # Destination exists -> cross-type merge (destination is the keeper).
            dst = self._load(to_type, to_name)
            self._fold_entity(dst, src)
            self._touch(dst, when)
            self._cache.pop(from_path, None)
            self._dirty.discard(from_path)
            self._renamed_entities.append(from_path)
            exclude = {from_path}
        else:
            # Destination is free -> retype the entity in place.
            src.entity_type = to_type
            src.canonical_name = to_name
            src.path = to_path
            self._cache[to_path] = src
            self._cache.pop(from_path, None)
            # Mirrors the merge branch above: an earlier op in this manifest may have
            # dirtied the source path, and leaving it in _dirty with no _cache entry
            # breaks the write loop and miscounts entities_written.
            self._dirty.discard(from_path)
            self._renamed_entities.append(from_path)
            self._touch(src, when)
            exclude = {from_path, to_path}

        self._repoint_refs(from_dir, from_stem, to_dir, to_stem, to_name,
                           old_label=from_name, exclude=exclude)

    def prune_attribute(self, op: dict, when: str) -> None:
        """Remove one or more facts (by exact key) from an entity's attributes or identifiers.

        Fields: entity_type, canonical_name, section ("attributes" or "identifiers"; default
        "attributes"), keys (non-empty list of exact key strings to remove). Value-agnostic:
        matches on key only. HALTS if the entity does not exist, or if any requested key is
        absent from the section (a typo guard) — deletions must be intentional and precise.
        This is the only sanctioned way to delete a fact; upsert can add or overwrite a value
        but never removes a key."""
        et = op.get("entity_type")
        name = op.get("canonical_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"prune_attribute: unknown entity type {et!r}.")
        if not name or not name.strip():
            raise CommitError("prune_attribute: 'canonical_name' is required and non-empty.")
        section = op.get("section", "attributes")
        if section not in ("attributes", "identifiers"):
            raise CommitError(
                f"prune_attribute: section must be 'attributes' or 'identifiers', got {section!r}.")
        keys = op.get("keys")
        if not keys or not isinstance(keys, list):
            raise CommitError(
                "prune_attribute: 'keys' must be a non-empty list of exact keys to remove.")
        p = self._path_for(et, name)
        if not p.exists() and p not in self._cache:
            raise CommitError(
                f"prune_attribute: no entity named {name!r} in {self.reg.directory_for(et)}/.")
        e = self._load(et, name)
        facts = getattr(e, section)
        present = {f["key"] for f in facts}
        missing = [k for k in keys if k not in present]
        if missing:
            raise CommitError(
                f"prune_attribute: {name}/{section} has no key(s) {missing!r} to remove "
                f"(present keys: {sorted(present)}).")
        drop = set(keys)
        setattr(e, section, [f for f in facts if f["key"] not in drop])
        self._touch(e, when)

    def prune_relationship(self, op: dict, when: str) -> None:
        """Remove a single relationship from an entity AND its reciprocal edge on the target.

        Fields: entity_type, canonical_name, rel_type, target_type, target_name. Matches on
        (rel_type, target_stem) — value/date/confidence agnostic. HALTS if the source entity
        does not exist or does not carry the named edge (a typo guard, mirroring
        prune_attribute): a prune must be intentional and precise. The reciprocal edge on the
        target (the inverse rel_type pointing back at the source, or the same type for a
        symmetric relationship) is removed too, so reciprocity is preserved without leaving a
        one-sided dangling edge for reconcile to resurrect. Reciprocal removal is best-effort:
        if the target file or the reciprocal edge is already absent, that is not an error.
        This is the only sanctioned way to delete a relationship; upsert only ever adds edges."""
        et = op.get("entity_type")
        name = op.get("canonical_name")
        rel = op.get("rel_type")
        tgt_type = op.get("target_type")
        tgt_name = op.get("target_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"prune_relationship: unknown entity type {et!r}.")
        if not name or not name.strip():
            raise CommitError("prune_relationship: 'canonical_name' is required and non-empty.")
        if not self.reg.is_relationship(rel):
            raise CommitError(f"prune_relationship: unknown relationship {rel!r}.")
        if not self.reg.is_entity_type(tgt_type):
            raise CommitError(f"prune_relationship: unknown target type {tgt_type!r}.")
        if not tgt_name or not tgt_name.strip():
            raise CommitError("prune_relationship: 'target_name' is required and non-empty.")

        src_path = self._path_for(et, name)
        if not src_path.exists() and src_path not in self._cache:
            raise CommitError(
                f"prune_relationship: no entity named {name!r} in {self.reg.directory_for(et)}/.")
        e = self._load(et, name)
        tgt_stem = slug(tgt_name)
        if not eio.relationship_present(e.relationships, rel, tgt_stem):
            raise CommitError(
                f"prune_relationship: {name} has no {rel!r} edge to {tgt_name!r} "
                f"(stem {tgt_stem!r}) to remove.")
        e.relationships = [r for r in e.relationships
                           if not (r["rel_type"] == rel and r["target_stem"] == tgt_stem)]
        self._touch(e, when)

        # Remove the reciprocal edge on the target (best-effort; symmetric rels reuse rel).
        inverse = self.reg.inverse_of(rel)
        inv_rel = rel if inverse is None else inverse
        tgt_path = self._path_for(tgt_type, tgt_name)
        if tgt_path.exists() or tgt_path in self._cache:
            target = self._load(tgt_type, tgt_name)
            if eio.relationship_present(target.relationships, inv_rel, e.stem):
                target.relationships = [
                    r for r in target.relationships
                    if not (r["rel_type"] == inv_rel and r["target_stem"] == e.stem)]
                self._touch(target, when)

    def prune_event(self, op: dict, when: str) -> None:
        """Remove one or more events from an entity's Events and History by exact (date, desc).

        Fields: entity_type, canonical_name, events (non-empty list of {date, desc} match specs).
        Matching is exact on both date and desc — the same identity key upsert uses to dedup
        events — so a prune targets exactly the line the caller pre-read. HALTS if the entity
        does not exist or if any spec matches no event (a typo guard): every requested removal
        must land. Events carry no reciprocal, so only the one entity is touched. This is the
        only sanctioned way to delete an event; upsert only ever appends."""
        et = op.get("entity_type")
        name = op.get("canonical_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"prune_event: unknown entity type {et!r}.")
        if not name or not name.strip():
            raise CommitError("prune_event: 'canonical_name' is required and non-empty.")
        specs = op.get("events")
        if not specs or not isinstance(specs, list):
            raise CommitError(
                "prune_event: 'events' must be a non-empty list of {date, desc} specs to remove.")
        for s in specs:
            if not isinstance(s, dict) or not s.get("date") or not s.get("desc"):
                raise CommitError(
                    f"prune_event: each spec needs both 'date' and 'desc'; got {s!r}.")

        p = self._path_for(et, name)
        if not p.exists() and p not in self._cache:
            raise CommitError(
                f"prune_event: no entity named {name!r} in {self.reg.directory_for(et)}/.")
        e = self._load(et, name)
        wanted = {(s["date"], s["desc"]) for s in specs}
        present = {(ev.get("date"), ev.get("desc")) for ev in e.events}
        missing = [s for s in wanted if s not in present]
        if missing:
            raise CommitError(
                f"prune_event: {name} has no event(s) matching {sorted(missing)!r} to remove.")
        e.events = [ev for ev in e.events if (ev.get("date"), ev.get("desc")) not in wanted]
        self._touch(e, when)

    def prune_alias(self, op: dict, when: str) -> None:
        """Remove one or more aliases (by exact string) from an entity's Aliases metadata.

        Fields: entity_type, canonical_name, aliases (non-empty list of exact alias strings to
        remove). HALTS if the entity does not exist, or if any requested alias is absent from
        the entity's alias list (a typo guard, mirroring the other prune ops) — every requested
        removal must land, so a prune can never silently no-op. This is the only sanctioned way
        to delete an alias; upsert only ever appends aliases and never removes one, which is what
        makes corrupted or stale aliases (for example the ones a merge folds in from a duplicate)
        otherwise unfixable through a manifest."""
        et = op.get("entity_type")
        name = op.get("canonical_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"prune_alias: unknown entity type {et!r}.")
        if not name or not name.strip():
            raise CommitError("prune_alias: 'canonical_name' is required and non-empty.")
        aliases = op.get("aliases")
        if not aliases or not isinstance(aliases, list):
            raise CommitError(
                "prune_alias: 'aliases' must be a non-empty list of exact alias strings to remove.")
        p = self._path_for(et, name)
        if not p.exists() and p not in self._cache:
            raise CommitError(
                f"prune_alias: no entity named {name!r} in {self.reg.directory_for(et)}/.")
        e = self._load(et, name)
        present = set(e.aliases)
        missing = [a for a in aliases if a not in present]
        if missing:
            raise CommitError(
                f"prune_alias: {name} has no alias(es) {missing!r} to remove "
                f"(present aliases: {sorted(present)}).")
        drop = set(aliases)
        e.aliases = [a for a in e.aliases if a not in drop]
        self._touch(e, when)

    def _require_entity(self, kind: str, op: dict) -> Entity:
        et = op.get("entity_type")
        name = op.get("canonical_name")
        if not self.reg.is_entity_type(et):
            raise CommitError(f"{kind}: unknown entity type {et!r}.")
        if not name or not name.strip():
            raise CommitError(f"{kind}: 'canonical_name' is required and non-empty.")
        p = self._path_for(et, name)
        if not p.exists() and p not in self._cache:
            raise CommitError(
                f"{kind}: no entity named {name!r} in {self.reg.directory_for(et)}/.")
        return self._load(et, name)

    def prune_perspective(self, op: dict, when: str) -> None:
        """Remove perspectives, each named by exactly one match spec.

        Fields: entity_type, canonical_name, and at least one of
          titles: [exact title, ...]          -- shorthand for match {"title": t}
          match:  [{field: exact value}, ...] -- every given field must equal the stored one
        Field names are normalised the way the parser stores them ("Evidence Type" ->
        evidence_type); values compare whitespace-trimmed. An untitled perspective is stored
        and parsed with title "Untitled", so a lone one can be named by title; where several
        share a title, name it with a match spec such as {"title": "Untitled", "claim": ...}.
        HALTS if any spec matches no perspective (typo guard), more than one (ambiguity guard:
        a prune never removes more than it names), or the same perspective as another spec.
        This is the only sanctioned way to delete a perspective; upsert only ever appends."""
        e = self._require_entity("prune_perspective", op)
        titles = op.get("titles") or []
        match = op.get("match") or []
        if not isinstance(titles, list) or not isinstance(match, list) or not (titles or match):
            raise CommitError(
                "prune_perspective: give 'titles' (exact titles) and/or 'match' "
                "(list of {field: exact value} specs), as non-empty lists.")
        specs = []
        for t in titles:
            if not isinstance(t, str) or not t.strip():
                raise CommitError(f"prune_perspective: bad title {t!r}.")
            specs.append({"title": t})
        for m in match:
            if not isinstance(m, dict) or not m:
                raise CommitError(f"prune_perspective: each match spec must be a non-empty "
                                  f"dict of field -> exact value; got {m!r}.")
            specs.append({k.strip("- ").strip().lower().replace(" ", "_"): v
                          for k, v in m.items()})
        drop: set[int] = set()
        for spec in specs:
            hits = [i for i, p in enumerate(e.perspectives)
                    if all(str(p.get(k, "")).strip() == str(v).strip() for k, v in spec.items())]
            if not hits:
                raise CommitError(
                    f"prune_perspective: {e.canonical_name} has no perspective matching "
                    f"{spec!r} (present titles: {[p.get('title') for p in e.perspectives]}).")
            if len(hits) > 1:
                raise CommitError(
                    f"prune_perspective: {spec!r} matches {len(hits)} perspectives on "
                    f"{e.canonical_name}; add fields (e.g. claim, holder) to a 'match' spec "
                    f"so it names exactly one.")
            if hits[0] in drop:
                raise CommitError(
                    f"prune_perspective: {spec!r} names a perspective another spec already "
                    f"names on {e.canonical_name}.")
            drop.add(hits[0])
        e.perspectives = [p for i, p in enumerate(e.perspectives) if i not in drop]
        self._touch(e, when)

    _NOTE_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")

    def prune_note(self, op: dict, when: str) -> None:
        """Remove Notes paragraphs or bullets, each named by an exact substring.

        Fields: entity_type, canonical_name, contains (non-empty list of exact substrings).
        Notes split into blank-line-separated paragraphs; inside a paragraph that has bullet
        lines, each bullet (with its continuation lines) is its own unit. Each substring must
        occur in exactly ONE unit, which is removed whole; everything else, separators
        included, is kept byte-for-byte. HALTS if a substring matches no unit (typo guard) or
        more than one (ambiguity guard), or two substrings name the same unit. This is the only
        sanctioned way to delete note text; upsert only ever appends to Notes."""
        e = self._require_entity("prune_note", op)
        subs = op.get("contains")
        if not subs or not isinstance(subs, list) or not all(
                isinstance(s, str) and s.strip() for s in subs):
            raise CommitError(
                "prune_note: 'contains' must be a non-empty list of exact non-empty substrings.")
        notes = "" if (e.notes or "").strip() == "(none)" else (e.notes or "")
        # parts alternates block, separator, block, ... ; each block -> list of unit strings.
        parts = re.split(r"(\n[ \t]*\n\s*)", notes)
        blocks: list[list[str]] = []
        for blk in parts[0::2]:
            units: list[str] = []
            for line in blk.split("\n"):
                if not units or self._NOTE_BULLET.match(line):
                    units.append(line)
                else:
                    units[-1] += "\n" + line
            blocks.append(units)
        drop: set[tuple[int, int]] = set()
        for s in subs:
            hits = [(bi, ui) for bi, units in enumerate(blocks)
                    for ui, u in enumerate(units) if s in u]
            if not hits:
                raise CommitError(
                    f"prune_note: no Notes paragraph or bullet on {e.canonical_name} "
                    f"contains {s!r}.")
            if len(hits) > 1:
                raise CommitError(
                    f"prune_note: {s!r} occurs in {len(hits)} Notes paragraphs/bullets on "
                    f"{e.canonical_name}; lengthen the substring so it names exactly one.")
            if hits[0] in drop:
                raise CommitError(
                    f"prune_note: {s!r} names a unit another substring already names.")
            drop.add(hits[0])
        seps = parts[1::2]
        out: list[str] = []
        for bi, units in enumerate(blocks):
            kept = [u for ui, u in enumerate(units) if (bi, ui) not in drop]
            if not kept:
                continue
            if out:
                out.append(seps[bi - 1])
            out.append("\n".join(kept))
        e.notes = "".join(out).strip()
        self._touch(e, when)

    def prune_source(self, op: dict, when: str) -> None:
        """Remove Sources entries, each named by its exact archive path.

        Fields: entity_type, canonical_name, archives (non-empty list of exact archive strings).
        Every entry whose archive equals a listed string is removed, whatever its name. HALTS if
        any listed archive matches no entry (typo guard), so a prune can never silently no-op.
        This is the only sanctioned way to delete a Sources entry; upsert only ever appends."""
        e = self._require_entity("prune_source", op)
        archives = op.get("archives")
        if not archives or not isinstance(archives, list) or not all(
                isinstance(a, str) and a.strip() for a in archives):
            raise CommitError(
                "prune_source: 'archives' must be a non-empty list of exact archive paths.")
        present = {s.get("archive") for s in e.sources}
        missing = [a for a in archives if a not in present]
        if missing:
            raise CommitError(
                f"prune_source: {e.canonical_name} has no Sources entry with archive "
                f"{missing!r} to remove.")
        drop = set(archives)
        e.sources = [s for s in e.sources if s.get("archive") not in drop]
        self._touch(e, when)


_RELATED_TYPES = {"project", "program", "technology_system", "technology_concept", "event"}


# Ops that change which person identities exist or what names they answer to. A person
# reference AFTER one of these must be resolved against the post-op state, not disk.
_IDENTITY_OPS = {"merge_entity", "rename_entity", "reclassify_entity", "prune_alias",
                 "prune_attribute"}


def _people_after(c: "Committer", reg, base: list) -> list:
    """Disk people overlaid with the committer's working set: entities an earlier op loaded or
    changed replace their disk copies, and files queued for deletion (merged, renamed,
    upgraded variants) drop out. This is the identity state the next op will actually see."""
    pdir = paths.entity_dir(reg.directory_for("person"))
    gone = set(c._renamed_entities)
    by = {e.path: e for e in base if e.path not in gone}
    for p, ent in c._cache.items():
        if p.parent == pdir and p not in gone:
            by[p] = ent
    return sorted(by.values(), key=lambda e: e.path.name)


def _resolve_names_in_manifest(manifest: dict, reg, *, only: list | None = None,
                               people: list | None = None) -> list[str]:
    """Deterministic pre-apply pass: resolve bare/ambiguous PERSON names to their
    canonical stored identity using context available in the manifest, BEFORE any
    op is applied.

    Context per person reference = the op's own edges (target orgs -> org context,
    target projects/programs/tech/events -> related-entity context) + every entity
    co-mentioned in the manifest + an optional explicit `context` block on the op
    ({orgs, email, related_entities, co_mentions}) + (inside resolve_person) the
    candidate people's own org/project memberships. A unique confident match
    rewrites the name in place; if the reference cannot be narrowed to exactly one
    identity it HALTs (CommitError) with the candidate list — never silently
    creating a duplicate. Applies to person `upsert_entity` canonical_names and to
    person relationship `target_name`s. Returns human-readable rewrite notes.

    Fail-open on unexpected resolver errors (leave the name as-is = prior behavior);
    a genuine ambiguity still HALTs.

    `only` restricts which ops are resolved (context is still drawn from the whole
    manifest); `people` injects the identity state to resolve against (default: disk).
    commit_manifest uses both to resolve ops after an identity-changing op in order."""
    import entity_matching as em

    ops = manifest.get("operations", [])
    all_names, all_orgs, all_related = set(), set(), []
    for op in ops:
        if op.get("op") != "upsert_entity":
            continue
        et, nm = op.get("entity_type"), op.get("canonical_name")
        if nm:
            all_names.add(nm)
            if et == "organization":
                all_orgs.add(nm)
            elif et in _RELATED_TYPES:
                all_related.append((et, nm))
        for r in op.get("relationships", []):
            tt, tn = r.get("target_type"), r.get("target_name")
            if tn:
                all_names.add(tn)
                if tt == "organization":
                    all_orgs.add(tn)
                elif tt in _RELATED_TYPES:
                    all_related.append((tt, tn))

    state = {"people": people}
    notes: list[str] = []

    def _context_for(op: dict, self_name: str) -> dict:
        orgs = set(all_orgs)
        related = list(all_related)
        co = set(all_names)
        co.discard(self_name)
        for r in op.get("relationships", []):
            tt, tn = r.get("target_type"), r.get("target_name")
            if not tn:
                continue
            if tt == "organization":
                orgs.add(tn)
            elif tt in _RELATED_TYPES:
                related.append((tt, tn))
        pnm = op.get("canonical_name")
        if pnm:
            co.add(pnm)
            if op.get("entity_type") in _RELATED_TYPES:
                related.append((op["entity_type"], pnm))
        cb = op.get("context") or {}
        for o in (cb.get("orgs") or []):
            orgs.add(o)
        for c in (cb.get("co_mentions") or []):
            co.add(c)
        for r in (cb.get("related_entities") or []):
            related.append(tuple(r) if isinstance(r, (list, tuple)) else ("", r))
        ctx = {"orgs": list(orgs), "related_entities": related, "co_mentions": list(co)}
        if cb.get("email"):
            ctx["email"] = cb["email"]
        return ctx

    def _resolve_one(name: str, op: dict, where: str) -> str:
        if not name or not name.strip():
            return name
        if state["people"] is None:
            state["people"] = em._load_people(reg)
        try:
            res = em.resolve_person(name, context=_context_for(op, name),
                                    registry=reg, people=state["people"])
        except Exception:
            return name  # resolver failure -> fall back to current behavior
        if res.requires_disambiguation:
            cands = "; ".join(c.canonical_name for c in res.candidates)
            raise CommitError(
                f"Ambiguous person reference {name!r} ({where}): {res.rationale} "
                f"Candidates: [{cands}]. Add a middle initial, org/project/email "
                f"context, or a distinguishing identifier if this is a genuinely "
                f"new person.")
        if res.resolved and res.resolved != name:
            # Do NOT downgrade a fuller incoming name onto a shorter stored
            # canonical (same first+last, incoming carries more middle tokens):
            # that is the name-UPGRADE case _find_existing_variant already handles,
            # and it keeps the fuller form. Only rewrite when we are filling in an
            # under-specified reference (resolved is equal-or-more-specific).
            nf, nmid, nl, _np = em.normalize_name(name)
            rf, rmid, rl, _rp = em.normalize_name(res.resolved)
            if (nf, nl) == (rf, rl) and len(nmid) > len(rmid):
                return name
            notes.append(f"{name!r} -> {res.resolved!r} ({where})")
            return res.resolved
        return name

    for op in (ops if only is None else only):
        if op.get("op") != "upsert_entity":
            continue
        if op.get("entity_type") == "person" and op.get("canonical_name"):
            op["canonical_name"] = _resolve_one(
                op["canonical_name"], op, f"upsert {op['canonical_name']!r}")
        for r in op.get("relationships", []):
            if r.get("target_type") == "person" and r.get("target_name"):
                r["target_name"] = _resolve_one(
                    r["target_name"], op,
                    f"{op.get('canonical_name', '?')} {r.get('rel_type')} target")
    return notes


def commit_manifest(manifest: dict, registry=None) -> dict:
    reg = registry or load_registry()
    when = date.today().isoformat()      # mechanical: agent never sets ingestion/update dates

    # An `unresolved` list is the agent declaring "I could not place these safely"
    # (CONVENTIONS: halting is success). Committing the rest would silently drop them,
    # so a non-empty list HALTS before anything is written.
    unresolved = manifest.get("unresolved")
    if unresolved:
        items = unresolved if isinstance(unresolved, list) else [unresolved]
        lines = "\n".join(
            f"  - {json.dumps(it, ensure_ascii=False) if not isinstance(it, str) else it}"
            for it in items)
        raise CommitError(
            f"manifest declares {len(items)} unresolved item(s); nothing written. "
            f"Resolve each (fix the manifest or extend the registry) and remove it from "
            f"`unresolved`, then re-run:\n{lines}")

    # CRITICAL CHECK: Empty manifest means extraction completely failed
    operations = manifest.get("operations", [])
    if not operations:
        raise CommitError(
            "\n\n"
            "╔══════════════════════════════════════════════════════════════════════╗\n"
            "║                      🚨 CRITICAL ERROR 🚨                            ║\n"
            "║                                                                      ║\n"
            "║  EMPTY MANIFEST: NO CONTENT EXTRACTED FROM SOURCE                   ║\n"
            "║                                                                      ║\n"
            "║  The ingestion agent produced ZERO operations. This should NEVER    ║\n"
            "║  happen - every document should yield at least one entity or fact.  ║\n"
            "║                                                                      ║\n"
            "║  Possible causes:                                                   ║\n"
            "║    • Agent failed to extract any relevant information               ║\n"
            "║    • Source file was empty or corrupted                             ║\n"
            "║    • Agent misunderstood the extraction task                        ║\n"
            "║                                                                      ║\n"
            "║  ACTION REQUIRED: Review the source file and extraction process.    ║\n"
            "╚══════════════════════════════════════════════════════════════════════╝\n"
        )

    # Deterministic pre-apply resolution: rewrite bare/ambiguous person names to
    # their canonical stored identity (or HALT if it can't be narrowed to one).
    # Ops up to the first identity-changing op resolve here against disk; later ones resolve
    # just before they apply, against the post-op state (so prune/merge-then-upsert works in
    # one manifest instead of halting on an ambiguity the earlier op removes).
    barrier = next((i for i, op in enumerate(operations) if op.get("op") in _IDENTITY_OPS),
                   len(operations))
    resolved_notes = _resolve_names_in_manifest(manifest, reg, only=operations[:barrier])
    base_people: list | None = None

    conflict_entities = {
        (op["entity_type"], op["canonical_name"])
        for op in operations if op.get("op") == "add_conflict"
    }
    c = Committer(reg, conflict_entities)
    dispatch = {"upsert_entity": c.upsert_entity, "add_conflict": c.add_conflict,
                "resolve_conflict": c.resolve_conflict, "rename_entity": c.rename_entity,
                "merge_entity": c.merge_entity, "reclassify_entity": c.reclassify_entity,
                "prune_attribute": c.prune_attribute, "prune_alias": c.prune_alias,
                "prune_relationship": c.prune_relationship, "prune_event": c.prune_event,
                "prune_perspective": c.prune_perspective, "prune_note": c.prune_note,
                "prune_source": c.prune_source, "upsert_action": c.upsert_action, "archive_action": c.archive_action}

    for i, op in enumerate(manifest.get("operations", [])):
        kind = op.get("op")
        if kind not in dispatch:
            raise CommitError(f"operation {i}: unknown op {kind!r}.")
        try:
            if i > barrier and kind == "upsert_entity":
                if base_people is None:
                    import entity_matching as em
                    base_people = em._load_people(reg)
                resolved_notes += _resolve_names_in_manifest(
                    manifest, reg, only=[op], people=_people_after(c, reg, base_people))
            dispatch[kind](op, when)
        except CommitError as e:
            # Add context about which operation failed
            label = (op.get("canonical_name") or op.get("old_name") or op.get("keep_name")
                     or op.get("from_name") or "unknown")
            raise CommitError(f"Operation {i} failed ({kind} for '{label}'): {e}") from e

    txn = Transaction()
    try:
        for p in sorted(c._dirty):
            ent = c._cache.get(p)
            if ent is None:
                # Defensive: a dirtied path whose entity was later renamed/reclassified
                # away should have been discarded from _dirty by that op. If one slips
                # through, say so in words -- a bare KeyError here surfaces to the user as
                # "rolled back cleanly: PosixPath(...)", which diagnoses nothing.
                raise CommitError(
                    f"internal: {paths.display_path(p)} is marked dirty but has no "
                    f"cached entity. An op moved this entity (rename/reclassify/merge) "
                    f"without discarding the old path from _dirty. This is a bug in "
                    f"commit.py, not in your manifest.")
            txn.write(p, eio.serialize(ent))

        # Write ledger and archive if actions were modified
        if c._ledger_dirty:
            from kairos.parse import serialize_ledger
            paths.AGENDA.mkdir(parents=True, exist_ok=True)
            txn.write(paths.AGENDA_LEDGER, serialize_ledger(c._ledger_actions))

            if c._archive_pending:
                from datetime import date as _date
                q = (_date.today().month - 1) // 3 + 1
                archive_file = paths.AGENDA_ARCHIVE / f"actions_{_date.today().year}q{q}.md"
                paths.AGENDA_ARCHIVE.mkdir(parents=True, exist_ok=True)
                existing = archive_file.read_text(encoding="utf-8") if archive_file.exists() else ""
                new_content = existing + serialize_ledger(c._archive_pending)
                txn.write(archive_file, new_content)

    except Exception as exc:
        txn.rollback()
        raise CommitError(f"commit failed, rolled back cleanly: {exc}") from exc

    # Remove old entity files that were renamed (upgraded to fuller canonical name)
    for old_path in c._renamed_entities:
        if old_path.exists():
            old_path.unlink()

    # paths.display_path, never relative_to(paths.ROOT): the content roots can be
    # relocated outside the repo (DIALEXIS_MODEL_DIR / _AGENDA_DIR), and relative_to
    # RAISES there. This is past the rollback window above, so a raise here would
    # leave the store half-written -- entities on disk, no rollback, batch still
    # STAGED. See paths.display_path's docstring.
    ledger_files = []
    if c._ledger_dirty:
        ledger_files.append(paths.display_path(paths.AGENDA_LEDGER))
        if c._archive_pending:
            from datetime import date as _date
            q = (_date.today().month - 1) // 3 + 1
            ledger_files.append(paths.display_path(
                paths.AGENDA_ARCHIVE / f"actions_{_date.today().year}q{q}.md"))

    # Write-time atomicity nudge. Bundling is NOT enforced — splitting needs judgment per line and
    # a halt would block ingestion on a large share of the graph. But when it was merely counted by an
    # audit nobody runs mid-ingestion, the rate promptly rose sharply on fresh
    # extraction. Reporting it at the moment of writing is the missing feedback, at no cost.
    bundled = []
    for op in manifest.get("operations", []):
        if op.get("op") != "upsert_entity":
            continue
        for f in list(op.get("attributes") or []) + list(op.get("identifiers") or []):
            n = eio.assertion_count(f.get("value", ""))
            if n > 1:
                bundled.append((n, op.get("canonical_name", "?"), f.get("key")))
    bundled.sort(reverse=True)

    return {"entities_written": len(c._dirty),
            "actions_written": len(c._ledger_actions or []) if c._ledger_dirty else 0,
            "actions_archived": len(c._archive_pending),
            "resolved_names": resolved_notes,
            "bundled_facts": bundled,
            "files": sorted(paths.display_path(p) for p in c._dirty) + ledger_files}


def _mark_batch_committed(manifest_path: Path) -> None:
    """Mark the batch's _manifest.json as COMMITTED after successful application."""
    batch_meta_path = manifest_path.parent / "_manifest.json"
    if not batch_meta_path.exists():
        return
    try:
        meta = json.loads(batch_meta_path.read_text(encoding="utf-8"))
        if meta.get("status") in ("STAGED", "EXTRACTED"):
            meta["status"] = "COMMITTED"
            meta["committed_at"] = date.today().isoformat()
            batch_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass


def _record_halt(manifest_path: Path, violation: Exception) -> None:
    """Append a durable record of a refused manifest.

    Nothing else in the system records a HALT: this function's caller exits nonzero and
    prints to stderr, and `batches/` is gitignored transient work that reconcile auto-cleans.
    So the one signal that says how often the enforcer actually catches something was being
    discarded on every run, which is why the enforcement layer could not be measured
    retrospectively at all -- see docs/superpowers/specs/2026-09-11-evaluation-protocol.md §2.

    Machine-local and gitignored deliberately. HALTs are frequent during ordinary manifest
    iteration, and a tracked append-only log would collide between writer machines on
    exactly the file nobody wants to hand-merge; a per-machine record is sufficient to
    establish a rate. JSONL rather than markdown because the consumer is a script.

    Best-effort: a logging failure must never alter the exit code of a refusal, or an
    observability feature starts deciding commit outcomes.
    """
    try:
        out = paths.OUTPUT
        out.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "manifest": paths.display_path(manifest_path),
            "violation_class": type(violation).__name__,
            "message": str(violation),
        }
        with (out / "halt_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def resolve_manifest_path(arg: str) -> Path:
    """CWD-relative first; if that does not exist and the path is relative, fall back to
    the repo root, so `run.py commit batches/<id>/manifest.json` works from any CWD."""
    p = Path(arg)
    if p.is_absolute() or p.exists():
        return p
    anchored = paths.resolve_recorded(str(p))
    if not anchored.exists():
        anchored = paths.ROOT / p
    return anchored if anchored.exists() else p


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python commit.py <manifest.json>", file=sys.stderr)
        return 2
    manifest_path = resolve_manifest_path(argv[1])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = commit_manifest(manifest)
        _mark_batch_committed(manifest_path.resolve())
        # Keep the derived cache fresh for the commit -> reconcile window so
        # lookups between the two steps see the just-written entities. Best-effort:
        # the unconditional reconcile that follows fully rebuilds the cache anyway.
        try:
            import index_store
            # Recorded paths are repo-relative for an in-repo graph and absolute for a
            # relocated one (paths.display_path). `ROOT / recorded` normalises BOTH,
            # because pathlib absorbs an absolute right operand -- the same idiom every
            # other reader of these recorded paths already uses. Deriving it this way
            # rather than stripping a literal "graph/" also covers a model relocated to a
            # path INSIDE the repo, where the literal matched nothing and the refresh went
            # silently inert. refresh() then drops whatever falls outside the model dir
            # (the agenda ledger) via its own relative_to guard.
            changed = [str(paths.resolve_recorded(f)) for f in result.get("files", [])
                       if f.endswith(".md")]
            if changed:
                index_store.refresh(changed)
        except Exception:
            pass
    except (CommitError, RegistryError, NamingError) as e:
        _record_halt(manifest_path, e)
        # Display errors in RED for visibility
        RED = "\033[91m"
        RESET = "\033[0m"
        print(f"{RED}HALT: {e}{RESET}", file=sys.stderr)
        return 1

    n = result["entities_written"]
    print(f"committed: {n} entit{'y' if n == 1 else 'ies'} written")
    for f in result["files"]:
        print(f"  {f}")
    for note in result.get("resolved_names", []):
        print(f"  resolved: {note}")

    bundled = result.get("bundled_facts") or []
    if bundled:
        total = sum(n for n, _, _ in bundled)
        print(f"\nnote: {len(bundled)} fact(s) written carry ~{total} assertions behind one "
              f"metadata bracket.")
        print("      One assertion per line keeps confidence and provenance bound to a single "
              "claim.")
        for n, ent, key in bundled[:5]:
            print(f"      ~{n} assertions  {ent}: {key}")
        if len(bundled) > 5:
            print(f"      ... and {len(bundled) - 5} more")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
