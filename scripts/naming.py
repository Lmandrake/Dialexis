"""naming.py - ENFORCE (slugging) + DETECT (name-upgrade / placement escalations)."""
import re


class NamingError(Exception):
    """Raised when a name cannot be slugged or violates naming policy."""


def slug(canonical_name: str) -> str:
    """Convert canonical name to filesystem-safe slug.

    Rules: lowercase; every run of non-[a-z0-9] becomes a single '_';
    strip leading/trailing '_'.
    """
    if not canonical_name or not canonical_name.strip():
        raise NamingError(f"cannot slug empty or whitespace-only name: {canonical_name!r}")

    s = canonical_name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")

    if not s:
        raise NamingError(f"slug collapsed to empty string: {canonical_name!r}")

    return s


def is_name_upgrade_candidate(current: str, proposed: str) -> bool:
    """Check if proposed name is a strict superset of current (e.g., 'John Q. Smith' vs 'John Smith').

    Returns True if proposed contains all words from current plus additional content.
    Used to flag potential name upgrades for confirmation rather than silent rename.
    """
    if not current or not proposed or current == proposed:
        return False

    current_words = set(current.lower().split())
    proposed_words = set(proposed.lower().split())

    return current_words < proposed_words
