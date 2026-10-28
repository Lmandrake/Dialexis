"""temporal.py - temporal_class helpers: default-inference + staleness thresholds.

`temporal_class` records how quickly a fact or relationship is expected to go stale.
It sits on the DECIDE side (the LLM may set it in a manifest); this module supplies the
deterministic *default* when the manifest omits it, plus the staleness thresholds the
update-candidacy signal reads. It stores nothing and computes nothing that isn't a pure
function of its inputs.

    permanent - never decays (identity, dated intervals, closed/past events, credentials)
    mutable   - true now, drifts over ~years; the default for present-tense state
    volatile  - churns fast, ~months; explicit temporariness markers

Design note: classification is deliberately coarse. Three buckets is where an LLM's (and a
regex's) judgment stays trustworthy; the signal it feeds is advisory (update candidacy,
never auto-delete), so a borderline fact defaulting to `mutable` is a safe, cheap miss.
"""
from __future__ import annotations

import re
from datetime import date

PERMANENT = "permanent"
MUTABLE = "mutable"
VOLATILE = "volatile"
TEMPORAL_CLASSES = [PERMANENT, MUTABLE, VOLATILE]
DEFAULT_TEMPORAL_CLASS = MUTABLE

# Update-candidacy thresholds in days. `permanent` never surfaces (None). Tunables:
# mutable ~18 months, volatile ~5 months.
STALENESS_DAYS: dict[str, int | None] = {PERMANENT: None, MUTABLE: 548, VOLATILE: 150}

# A closed or open dated span (e.g. "2001-2006", "2018–present") reads as historical/fixed.
_DATE_INTERVAL = re.compile(r"(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:(?:19|20)\d{2}|present|now)",
                            re.IGNORECASE)

# Explicit temporariness in the key or value -> volatile.
_VOLATILE_MARKERS = re.compile(
    r"\b(?:acting|interim|currently|current|temporar\w*|provisional|"
    r"in\s+review|under\s+review|in\s+prep\w*|"
    r"this\s+(?:week|month|quarter|cycle|sprint|semester)|"
    r"until\b|pending|on\s+leave|sabbatical|travel(?:ing|ling)?|visiting)\b",
    re.IGNORECASE)

# Identity / credential attribute keys -> permanent.
_PERMANENT_KEY_MARKERS = (
    "born", "birth", "date of birth", "dob", "died", "death", "deceased",
    "phd", "ph.d", "doctorate", "degree", "alma mater", "graduated", "nationality",
    "citizenship", "gender", "orcid", "doi", "isbn", "founded", "established", "incorporated",
)

# Relationships whose meaning is fixed once true (authorship, citation, adjudication,
# provenance, attendance of a past event). Everything else defaults to mutable.
_PERMANENT_RELS = frozenset({
    "Authored by", "Authors", "Cites", "Cited by", "Distinct from",
    "Sparked", "Sparked by", "Realized as", "Realized from",
    "About", "Subject of", "Attended", "Attended by",
})


def infer_temporal_class(key: str, value) -> str:
    """Default temporal_class for a fact, from its key and value text."""
    k = (key or "").strip().lower()
    v = str(value or "")
    if any(marker in k for marker in _PERMANENT_KEY_MARKERS):
        return PERMANENT
    if _DATE_INTERVAL.search(v):
        return PERMANENT
    if _VOLATILE_MARKERS.search(v) or _VOLATILE_MARKERS.search(k):
        return VOLATILE
    return MUTABLE


def infer_rel_temporal_class(rel_type: str) -> str:
    """Default temporal_class for a relationship, from its type."""
    return PERMANENT if rel_type in _PERMANENT_RELS else MUTABLE


def staleness_threshold_days(temporal_class: str) -> int | None:
    """Days after last_verified before a fact of this class is an update candidate.

    None means never (permanent). Unknown classes are treated as mutable."""
    return STALENESS_DAYS.get(temporal_class, STALENESS_DAYS[MUTABLE])


def parse_stamp(stamp: str) -> date | None:
    """Parse a last_verified / date stamp into a date. Tolerant of partial stamps
    (YYYY, YYYY-MM, YYYY-MM-DD, or a leading ISO datetime); returns None if unusable."""
    if not stamp:
        return None
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(stamp).strip())
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    day = int(m.group(3)) if m.group(3) else 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_well_formed_stamp(stamp: str) -> bool:
    """True if a stamp is a usable date (full or partial ISO). Used by validate.py."""
    return parse_stamp(stamp) is not None


def is_stale(last_verified: str, temporal_class: str, now: date | None = None) -> bool:
    """True if a fact of this class, last verified at `last_verified`, is an update
    candidate as of `now`. permanent is never stale; an unparseable/missing stamp is
    treated as not-a-candidate (we cannot honestly judge its age)."""
    threshold = staleness_threshold_days(temporal_class or DEFAULT_TEMPORAL_CLASS)
    if threshold is None:
        return False
    anchor = parse_stamp(last_verified)
    if anchor is None:
        return False
    now = now or date.today()
    return (now - anchor).days > threshold
