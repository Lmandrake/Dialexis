"""stage_inbox.py - Archives sources verbatim, opens a batch, deletes verified inbox originals.

Also auto-parses .ics (iCalendar) files and generates a seed manifest with
upsert_entity operations for all attendees and the event. This ensures calendar
invitees deterministically become entities without relying on agent judgment.
"""
from __future__ import annotations
import json
import re
import shutil
import sys
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import paths

try:   # site-specific parsers (org-chart PDFs, calendar conventions); absent in the public export
    import site_local
except ImportError:
    site_local = None


###############################################################################
# Quiescence helpers — Drive-inbox mid-sync guard
###############################################################################

def _snapshot(path: Path) -> tuple[int, str]:
    """(size, sha256) of a file's current contents — the quiescence fingerprint.
    mtime is deliberately NOT used: Drive restamps it on download."""
    data = path.read_bytes()
    return len(data), sha256(data).hexdigest()


def _quiescent_files(files: list, recheck_s: float = 2.0) -> tuple[list, list]:
    """Split files into (stable, skipped). A file is stable if its (size, sha256)
    is unchanged across a recheck_s pause — i.e. Drive is not still writing it.
    A file that vanishes between reads is treated as not-stable (skipped)."""
    first = {}
    for f in files:
        try:
            first[f] = _snapshot(f)
        except OSError:
            pass  # unreadable now → treat as not-stable below
    if recheck_s:
        time.sleep(recheck_s)
    stable, skipped = [], []
    for f in files:
        try:
            again = _snapshot(f)
        except OSError:
            skipped.append(f); continue
        if f in first and first[f] == again:
            stable.append(f)
        else:
            skipped.append(f)
    return stable, skipped


###############################################################################
# ICS (iCalendar) auto-parsing
###############################################################################

def _unfold_ics(raw: str) -> str:
    """Remove RFC 5545 line folding (continuation lines start with space/tab)."""
    return re.sub(r'\r?\n[ \t]', '', raw)


def _extract_field(line: str, field: str) -> str | None:
    """Extract a named parameter or value from an ICS property line."""
    if field == "CN":
        # Quoted CN: CN="First Last"
        m = re.search(r'CN="([^"]+)"', line)
        if m:
            return m.group(1).strip()
        # Unquoted CN: CN=Last, First M (US 12A):mailto:...
        # Must stop at : or ; that follows the name
        m = re.search(r'CN=([^;:]+)', line)
        if m:
            val = m.group(1).strip()
            # Remove trailing mailto if accidentally captured
            val = re.sub(r'\s*mailto.*$', '', val, flags=re.IGNORECASE)
            return val if val else None
        return None
    if field == "mailto":
        m = re.search(r'mailto:([^\s;>]+)', line, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "ROLE":
        m = re.search(r'ROLE=([^;:\s]+)', line)
        return m.group(1).strip() if m else None
    return None


def _parse_cn_to_name(cn: str) -> tuple[str, str | None]:
    """Parse a CN field into (canonical_name, org_code).

    Handles multiple formats:
      Site:      "Last, First M (US 12A)"
      NASA:      "Last, First M (ACME-407.0)"
      Contractor:"Last, First M (ACME-407.0)[Company, LLC]"
      HQ marker: "Last, First {HQ} (NORTH-E3)"
      External:  "First Last"
      Email-only:"user@example.com"
    """
    # If CN is just an email address, derive name from it
    if re.match(r'^[^@\s]+@[^@\s]+\.\w+$', cn.strip()):
        local = cn.split('@')[0]
        # Try first.last or first_last patterns
        parts = re.split(r'[._]', local)
        if len(parts) >= 2:
            canonical = ' '.join(p.capitalize() for p in parts)
        else:
            canonical = cn.strip()
        return canonical, None

    org_code = None

    # Strip bracketed content: [Company, LLC]
    cn = re.sub(r'\[[^\]]*\]', '', cn).strip()
    # Strip braced content: {HQ}
    cn = re.sub(r'\{[^}]*\}', '', cn).strip()

    # Extract org code from parenthetical: (US 12A) or (ACME-407.0) or (NORTH-E3)
    org_match = re.search(r'\(([^)]+)\)', cn)
    if org_match:
        raw_org = org_match.group(1).strip()
        # Site format: "US 12A" → extract just the code
        if raw_org.startswith('US '):
            org_code = raw_org[3:].strip()
        else:
            org_code = raw_org
        cn = cn[:org_match.start()].strip()

    # Remove trailing punctuation artifacts
    cn = cn.rstrip(' ,;')

    if ',' in cn:
        parts = cn.split(',', 1)
        last = parts[0].strip()
        first = parts[1].strip()
        # Remove middle initial periods: "First M." → "First M"
        first = re.sub(r'\.\s*$', '', first)
        canonical = f"{first} {last}"
    else:
        canonical = cn.strip()

    return canonical, org_code


def _parse_ics_datetime(dtval: str) -> tuple[str, str | None]:
    """Parse ICS DTSTART/DTEND into (date_str, time_str).

    Handles: 20260708T200500Z, 20260708T130000, 20260708
    Returns: ("2026-07-08", "20:05 UTC") or ("2026-07-08", None)
    """
    dtval = dtval.strip()
    # Strip TZID prefix if present (e.g., "TZID=America/Los_Angeles:20260708T130000")
    if ':' in dtval and not dtval.startswith('20'):
        dtval = dtval.split(':', 1)[1]

    date_str = f"{dtval[0:4]}-{dtval[4:6]}-{dtval[6:8]}"
    time_str = None
    if 'T' in dtval:
        t = dtval.split('T')[1].replace('Z', '')
        hh, mm = t[0:2], t[2:4]
        suffix = " UTC" if dtval.endswith('Z') else ""
        time_str = f"{hh}:{mm}{suffix}"
    return date_str, time_str


def _parse_single_ics(content: str, archive_path: str) -> list[dict]:
    """Parse one ICS file and return manifest operations."""
    unfolded = _unfold_ics(content)
    lines = unfolded.split('\n')

    attendees = []  # list of (cn, email, role)
    summary = None
    dtstart = None
    dtend = None
    location = None

    for line in lines:
        if line.startswith('ORGANIZER'):
            # Organizer is typically also in ATTENDEE list; skip here
            pass
        elif line.startswith('ATTENDEE'):
            cn = _extract_field(line, "CN")
            email = _extract_field(line, "mailto")
            role = _extract_field(line, "ROLE") or "REQ-PARTICIPANT"
            if cn and email:
                attendees.append((cn, email, role))
        elif line.startswith('SUMMARY:'):
            summary = line.split(':', 1)[1].strip()
            # Unescape ICS
            summary = summary.replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ')
        elif line.startswith('DTSTART'):
            val = line.split(':', 1)[1] if ':' in line else ''
            dtstart = val.strip()
        elif line.startswith('DTEND'):
            val = line.split(':', 1)[1] if ':' in line else ''
            dtend = val.strip()
        elif line.startswith('LOCATION') and not line.startswith('LOCATION;ALTREP'):
            val = line.split(':', 1)[1] if ':' in line else ''
            location = val.strip().replace('\\,', ',').replace('\\;', ';')
        elif line.startswith('LOCATION;'):
            # LOCATION with parameters (e.g., ALTREP)
            val = line.split(':', 1)[1] if ':' in line else ''
            if not location:
                location = val.strip().replace('\\,', ',').replace('\\;', ';')

    if not summary or not attendees:
        return []

    # Parse event date/time
    event_date, start_time = _parse_ics_datetime(dtstart) if dtstart else ("unknown", None)
    _, end_time = _parse_ics_datetime(dtend) if dtend else (None, None)

    # Build event canonical name (keep it concise for entity filenames)
    event_name = f"{summary} ({event_date})" if event_date != "unknown" else summary
    # Truncate if excessively long (>120 chars) — agent can refine later
    if len(event_name) > 120:
        event_name = event_name[:117] + "..."

    # Time attribute
    time_attr = None
    if start_time and end_time:
        time_attr = f"{start_time} - {end_time}"
    elif start_time:
        time_attr = start_time

    operations = []

    # Resolve-only contract for calendar attendees:
    #   Before writing an attendee's seed upsert, ask the resolver whether this
    #   person ALREADY exists (matched middle-initial-insensitively, with the
    #   attendee's own email as the strongest context signal and the other
    #   attendees as co-mentions). On a clean, unambiguous resolution to an
    #   existing canonical, we REWRITE the seed op's canonical_name (and the
    #   event's matching "Attended by" target) to that existing name — so the
    #   commit merges facts onto the real entity instead of minting a near-duplicate
    #   ("Odalys Ferncastle" vs the stored "Odalys D Ferncastle").
    #   This is strictly resolve-only: we never delete, merge, or rename a file;
    #   an ambiguous result or no match leaves the parsed name untouched (a new
    #   entity, exactly as before). If the resolver is unavailable for any reason,
    #   we silently fall back to the parsed name — staging must never fail on it.
    attendee_names = []
    for cn, _e, _r in attendees:
        nm, _oc = _parse_cn_to_name(cn)
        attendee_names.append(nm)

    def _resolve_existing(parsed_name: str, email: str) -> str:
        try:
            from entity_matching import resolve_person
            co = [n for n in attendee_names if n and n != parsed_name]
            res = resolve_person(parsed_name,
                                 context={"email": email, "co_mentions": co})
            if res.resolved and not res.requires_disambiguation:
                return res.resolved
        except Exception:
            pass
        return parsed_name

    # Generate person entities for all attendees (including organizer if in list)
    seen_emails = set()
    # Map parsed name -> resolved canonical, so the event's Attended-by edges below
    # point at the same (possibly resolved) names as the person upserts.
    resolved_by_parsed: dict[str, str] = {}
    for cn, email, role in attendees:
        if email.lower() in seen_emails:
            continue
        seen_emails.add(email.lower())

        canonical_name, org_code = _parse_cn_to_name(cn)
        resolved = _resolve_existing(canonical_name, email.lower())
        resolved_by_parsed[canonical_name] = resolved
        canonical_name = resolved

        person_op = {
            "op": "upsert_entity",
            "entity_type": "person",
            "canonical_name": canonical_name,
            "identifiers": [
                {"key": "Email", "value": email.lower(), "source": "calendar",
                 "confidence": "HIGH", "date": event_date}
            ],
            "attributes": [],
            "relationships": [
                {"rel_type": "Attends", "target_type": "event",
                 "target_name": event_name, "confidence": "HIGH",
                 "source": "calendar", "date": event_date}
            ],
            "events": [],
            "sources": [
                {"name": "Calendar invite (ATTENDEE field)",
                 "archive": archive_path}
            ]
        }

        if site_local is not None:
            attr, rel = site_local.ics_person_extras(email, org_code, event_date)
            if attr:
                person_op["attributes"].append(attr)
            if rel:
                person_op["relationships"].append(rel)
        elif org_code:
            person_op["attributes"].append({
                "key": "Org Code", "value": org_code,
                "source": "calendar", "confidence": "HIGH", "date": event_date})

        operations.append(person_op)

    # Generate event entity
    event_op = {
        "op": "upsert_entity",
        "entity_type": "event",
        "canonical_name": event_name,
        "attributes": [
            {"key": "Date", "value": event_date,
             "source": "calendar", "confidence": "HIGH", "date": event_date}
        ],
        "relationships": [],
        "events": [],
        "sources": [
            {"name": "ICS calendar event",
             "archive": archive_path}
        ]
    }

    if time_attr:
        event_op["attributes"].append(
            {"key": "Time", "value": time_attr,
             "source": "calendar", "confidence": "HIGH", "date": event_date})
    if location:
        event_op["attributes"].append(
            {"key": "Location", "value": location,
             "source": "calendar", "confidence": "HIGH", "date": event_date})

    # Add Attended by relationships for each person. Use the SAME resolved canonical
    # the person upsert used (resolve-only), so the event edge and the person file
    # agree and no duplicate target is introduced.
    for cn, email, role in attendees:
        if email.lower() not in seen_emails:
            continue
        canonical_name, _ = _parse_cn_to_name(cn)
        canonical_name = resolved_by_parsed.get(canonical_name, canonical_name)
        event_op["relationships"].append({
            "rel_type": "Attended by", "target_type": "person",
            "target_name": canonical_name, "confidence": "HIGH",
            "source": "calendar", "date": event_date
        })

    operations.append(event_op)
    return operations


def _parse_ics_files(archived_files: list[dict]) -> list[dict]:
    """Parse all .ics files in a batch and return manifest operations."""
    all_ops = []
    for entry in archived_files:
        if not entry["original"].lower().endswith(".ics"):
            continue
        archive_path = entry["archive"]
        full_path = paths.resolve_recorded(archive_path)
        try:
            content = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not content.strip():
            continue
        ops = _parse_single_ics(content, archive_path)
        all_ops.extend(ops)
    return all_ops


###############################################################################
# Core staging logic
###############################################################################

def stage_inbox() -> dict | None:
    """Stage all files from inbox to archive and create a new batch.

    Returns:
        dict with batch_id and files staged, or None if inbox is empty
    """
    # A graph root (DIALEXIS_GRAPH_ROOT) carries its own <root>/inbox, which is local by
    # design; every other configuration still requires the Drive-synced inbox.
    if paths.INBOX is None or not (paths.inbox_is_remote() or paths.graph_root()):
        raise RuntimeError(
            "No inbox configured. Dialexis ingests only from the Drive-synced inbox; "
            "there is no local inbox fallback. Set DIALEXIS_INBOX_DIR in .env to the "
            "synced _inbox path (e.g. <shared-inbox>)."
        )
    if not paths.INBOX.exists():
        raise RuntimeError(
            f"Configured inbox does not exist: {paths.INBOX}. "
            "Is Google Drive mounted and fully synced on this machine?"
        )

    all_entries = list(paths.INBOX.iterdir())
    all_files = [f for f in all_entries if f.is_file()]
    skipped = [f for f in all_files if f.name.startswith('.')]
    inbox_files = [f for f in all_files if not f.name.startswith('.')]

    if skipped:
        print(f"  Skipped {len(skipped)} dotfile(s): {', '.join(f.name for f in skipped)}")

    if paths.inbox_is_remote() and inbox_files:
        inbox_files, not_ready = _quiescent_files(inbox_files)
        if not_ready:
            print(f"  Skipped {len(not_ready)} still-syncing file(s): "
                  f"{', '.join(f.name for f in not_ready)}")

    if not inbox_files:
        non_dot = [f for f in all_files if not f.name.startswith('.')]
        if non_dot:
            # Files exist but were all deferred by the quiescence gate
            print(f"  (Nothing left to stage; {len(non_dot)} file(s) deferred by quiescence gate)")
        elif all_files:
            print(f"  (All {len(all_files)} file(s) in inbox are dotfiles — nothing to stage)")
        return None

    # Create batch ID from timestamp
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_path = paths.batch_dir(batch_id)
    batch_path.mkdir(parents=True, exist_ok=True)

    # Archive each file
    paths.RAW_INBOX.mkdir(parents=True, exist_ok=True)
    archived = []

    for inbox_file in inbox_files:
        # Compute hash for verification
        content = inbox_file.read_bytes()
        file_hash = sha256(content).hexdigest()[:16]

        # Archive with timestamp prefix
        archive_name = f"{batch_id}_{inbox_file.name}"
        archive_path = paths.RAW_INBOX / archive_name

        # Copy to archive
        shutil.copy2(inbox_file, archive_path)

        # Verify copy
        if archive_path.read_bytes() != content:
            raise RuntimeError(f"Archive verification failed for {inbox_file.name}")

        archived.append({
            "original": inbox_file.name,
            # display_path, not relative_to(ROOT): DIALEXIS_ARCHIVE_DIR can put the
            # archive outside the repo, where relative_to raises -- and it would raise
            # here AFTER the file was already copied and the original unlinked. The
            # readers below (_parse_ics_files, _parse_org_chart_files) re-resolve this
            # as `paths.ROOT / archive`, which works for both forms.
            "archive": paths.display_path(archive_path),
            "hash": file_hash
        })

        # Delete verified original
        inbox_file.unlink()

    # Create batch manifest
    manifest = {
        "batch_id": batch_id,
        "status": "STAGED",
        "staged_at": datetime.now().isoformat(),
        "files": archived
    }

    manifest_path = batch_path / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Auto-parse ICS files and generate seed manifest with entity operations
    ics_ops = _parse_ics_files(archived)

    # Auto-parse org chart PDFs
    org_chart_ops = site_local._parse_org_chart_files(archived) if site_local else []

    all_auto_ops = ics_ops + org_chart_ops
    if all_auto_ops:
        parts = []
        if ics_ops:
            parts.append("ICS calendar file(s)")
        if org_chart_ops:
            parts.append("org chart PDF(s)")
        seed_manifest = {
            "batch_id": batch_id,
            "source_description": f"Auto-generated from {' and '.join(parts)}",
            "operations": all_auto_ops
        }
        agent_manifest_path = batch_path / "manifest.json"
        agent_manifest_path.write_text(
            json.dumps(seed_manifest, indent=2), encoding="utf-8")

    return {"batch_id": batch_id, "files_staged": len(archived),
            "ics_entities": len(ics_ops),
            "org_chart_entities": len(org_chart_ops)}


def main() -> int:
    try:
        result = stage_inbox()
        if result is None:
            print("Inbox is empty, nothing to stage")
            return 0

        print(f"Staged batch {result['batch_id']}: {result['files_staged']} files")
        if result.get("ics_entities"):
            print(f"  ICS auto-parse: {result['ics_entities']} entity operations generated")
        if result.get("org_chart_entities"):
            print(f"  Org chart auto-parse: {result['org_chart_entities']} entity operations generated")
        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
