"""entity_matching.py - Fuzzy + contextual matching to support (not make) identity decisions.

This module lives on the **DECIDE** side of Dialexis. It maps a name (or an
under-specified reference) to entities that ALREADY exist in `graph/` so
callers can avoid minting duplicates. It is strictly **resolve-only**: nothing here
deletes, merges, renames, or rewrites an entity file. Merging two files that both
already exist remains an explicit `merge_entity` op applied by `commit.py` under a
human-reviewed manifest.

It offers three layers, cheapest first:

1. `fuzzy_match_entities` / `find_entity` / `list_entities` — the original
   string-similarity net (unchanged; other modules import them).
2. A **middle-initial-insensitive identity** predicate for PERSON names
   (`normalize_name`, `middle_compatible`, `names_match`, `find_name_collisions`).
   Two people are the same identity by DEFAULT when first + last match and the
   middle tokens are compatible (one absent, or an initial that prefixes the
   other's middle — "J" ⊆ "James"). They are held AMBIGUOUS only when middle
   tokens CONTRADICT (both present and incompatible, e.g. "Hollis J Marlowe" vs
   "Hollis R Marlowe"). The same logic applies to email local-parts
   (`odalys.f.ferncastle@halloway.example` ≡ `odalys.ferncastle@halloway.example`, the
   `d` being an optional middle token) — with a hard guard that the domains match.
3. **Contextual resolution** of incompletely-specified references
   (`resolve_person`, `ResolutionResult`): resolve "Odalys in CMG", a bare
   first name, a last name, or an email to a single known person using org
   membership, email, same-source co-mentions, and relationships to named
   entities. When more than one person survives, ALL survivors are returned, each
   flagged low-confidence with a "requires disambiguation" note — the resolver
   never picks silently.

The guiding rule: a wrong auto-merge is destructive, so the code errs toward
"ambiguous" whenever the identity rules are genuinely unclear.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import paths
import entity_io as eio
from naming import slug
from registry import load_registry


def fuzzy_match_entities(query: str, entity_type: str | None = None, threshold: float = 0.6,
                         registry=None) -> list[dict]:
    """Find entities matching a query string.

    Args:
        query: Name or partial name to search for
        entity_type: If provided, restrict to this entity type
        threshold: Minimum similarity score (0-1)
        registry: Registry instance

    Returns:
        List of matches sorted by score, each with 'entity', 'score', 'path'
    """
    reg = registry or load_registry()
    matches = []

    # Read from the derived index (one ~3ms query) instead of globbing+parsing
    # every file. lookup_index falls back to a live parse if the cache is
    # unavailable, so behaviour is identical, just faster.
    import index_store
    for row in index_store.lookup_index(reg):
        if entity_type and row["type"] != entity_type:
            continue
        # Score against canonical name, then aliases (best wins)
        score = _match_score(query, row["name"])
        for alias in row["aliases"]:
            score = max(score, _match_score(query, alias))

        if score >= threshold:
            matches.append({
                "entity": row["name"],
                "type": row["type"],
                "score": score,
                "path": row["path"],
            })

    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches


def _similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def _tokens(s: str) -> list[str]:
    """Lowercase alphanumeric tokens (drops punctuation, initials-as-tokens preserved)."""
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _match_score(query: str, candidate: str) -> float:
    """Blended name-match score in [0, 1].

    Combines plain fuzzy ratio with two boosts that beat fuzzy noise:
    - whole-string substring (e.g. "waterhouse" in "dunstan e waterhouse"),
    - token-subset (all query tokens present, e.g. "dunstan waterhouse" ⊆ "dunstan e waterhouse"),
    so a real partial-name query dominates unrelated names that only share letters.
    """
    q, c = query.lower().strip(), candidate.lower().strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    ratio = SequenceMatcher(None, q, c).ratio()
    # Both boosts are coverage-weighted: how much of the candidate the query accounts for.
    # A short query buried in a long title (a mere mention) scores well below a near-complete match.
    sub = 0.6 + 0.4 * (len(q) / len(c)) if q in c else 0.0
    qt, ct = _tokens(q), _tokens(c)
    tok = 0.6 + 0.4 * (len(qt) / len(ct)) if qt and ct and set(qt) <= set(ct) else 0.0
    return max(ratio, sub, tok)


def find_entity(name: str, entity_type: str, registry=None) -> Path | None:
    """Find an entity file by canonical name and type.

    Returns:
        Path to entity file if found, None otherwise
    """
    reg = registry or load_registry()
    entity_slug = slug(name)
    entity_dir = paths.entity_dir(reg.directory_for(entity_type))
    path = entity_dir / f"{entity_slug}.md"

    return path if path.exists() else None


def list_entities(entity_type: str | None = None, registry=None) -> list[dict]:
    """List all entities, optionally filtered by type.

    Returns:
        List of dicts with 'name', 'type', 'status', 'path'
    """
    reg = registry or load_registry()
    import index_store
    return [{"name": row["name"], "type": row["type"],
             "status": row["status"], "path": row["path"]}
            for row in index_store.lookup_index(reg)
            if not entity_type or row["type"] == entity_type]


###############################################################################
# Middle-initial-insensitive PERSON identity
#
# Everything below is the resolver layer. It reasons about human names and, when
# asked, about the people already stored in graph/people. It never writes.
###############################################################################

# Lowercase name particles that belong with the surname rather than being a
# standalone middle token (consistent with the org-chart parser, which keeps
# "de la Fontaine" attached to the surname). We treat any run of these as part of
# the last-name unit so "Anne-Marie de la Fontaine" tokenizes to first="anne-marie",
# last="de la fontaine" with no middle tokens.
_NAME_PARTICLES = {"de", "del", "de la", "della", "la", "le", "van", "von",
                   "der", "den", "van der", "van den", "da", "das", "dos",
                   "di", "du", "el", "al", "bin", "ibn", "st", "st.", "san",
                   "mac", "mc", "o"}

# Generational / honorific suffixes that are not identity tokens. Stripped before
# first/middle/last extraction so "Casper F Rowntree Jr." and "Carl F Rowntree" share a key
# (and, separately, "Casper F Rowntree Jr." vs "Casper F Bridgeman Jr." differ on the LAST name,
# not the suffix).
_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v",
                  "phd", "ph.d.", "md", "m.d.", "esq", "esq."}


def _clean_token(tok: str) -> str:
    """Lowercase a name token and drop a single trailing period (initials)."""
    return tok.lower().rstrip(".").strip()


def normalize_name(name: str) -> tuple[str, list[str], str, list[str]]:
    """Tokenize a human name into (first, middle_tokens, last, particles).

    Returns a 4-tuple:
      - first:   the given-name token, lowercased. Hyphenated first names are kept
                 whole ("Anne-Marie" -> "anne-marie").
      - middle_tokens: zero or more lowercased middle tokens, trailing '.' stripped
                 so an initial "J." and "J" and the word "James" are directly
                 comparable by prefix.
      - last:    the surname unit, lowercased. Any leading lowercase particles
                 (de, la, van, ...) are folded into this unit, so the surname of
                 "Anne-Marie de la Fontaine" is "de la fontaine".
      - particles: the list of particle tokens that were folded into `last`
                 (informational; the identity logic keys off first/middle/last).

    Generational suffixes (Jr., III, PhD, ...) are dropped entirely — they are not
    identity tokens.

    Examples:
      normalize_name("Odalys D Ferncastle")   -> ("odalys", ["d"], "ferncastle", [])
      normalize_name("Odalys Ferncastle")     -> ("odalys", [],    "ferncastle", [])
      normalize_name("Hollis J. Marlowe")      -> ("hollis", ["j"], "marlowe", [])
      normalize_name("Anne-Marie de la Fontaine") ->
                                     ("anne-marie", [], "de la fontaine", ["de","la"])
      normalize_name("Casper F Rowntree Jr.")      -> ("carl", ["f"], "rowntree", [])
      normalize_name("Ferncastle, D")       -> ("ferncastle", [], "d", [])
                                     (comma-form is handled by callers via
                                      _normalize_variants; raw here is best-effort)
    """
    # Split on whitespace only (hyphens/apostrophes stay inside a token so
    # "Jean-Pierre" and "O'Brien" survive intact).
    raw = [t for t in re.split(r"\s+", name.strip()) if t]
    # Drop trailing generational/honorific suffixes.
    while raw and _clean_token(raw[-1]).replace(".", "") in {s.replace(".", "") for s in _NAME_SUFFIXES}:
        raw.pop()
    toks = [_clean_token(t) for t in raw if _clean_token(t)]

    if not toks:
        return ("", [], "", [])
    if len(toks) == 1:
        return (toks[0], [], "", [])

    first = toks[0]

    # Walk from the end pulling particle tokens into the surname unit.
    last_parts = [toks[-1]]
    i = len(toks) - 2
    particles: list[str] = []
    while i >= 1 and toks[i] in _NAME_PARTICLES:
        particles.insert(0, toks[i])
        last_parts.insert(0, toks[i])
        i -= 1
    last = " ".join(last_parts)
    middle_tokens = toks[1:i + 1]
    return (first, middle_tokens, last, particles)


def _middle_token_compatible(a: str, b: str) -> bool:
    """Two individual middle tokens are compatible when either is an initial-prefix
    of the other (so 'j' ⊆ 'james', 'j' ⊆ 'j', 'james' ⊆ 'james') — or, more
    strictly, either token is a prefix of the other AND at least one side is a
    single-letter initial, OR they are exactly equal."""
    if a == b:
        return True
    # Initial-prefix: one side is a single letter that begins the other.
    if len(a) == 1 and b.startswith(a):
        return True
    if len(b) == 1 and a.startswith(b):
        return True
    return False


def middle_compatible(a_middles: list[str], b_middles: list[str]) -> bool:
    """Are two middle-token lists compatible for identity?

    Compatible (returns True) when the two sequences can be reconciled position by
    position with no contradiction:
      - one side has NO middle tokens -> compatible (absent is optional)
      - both present -> each aligned pair must be initial-prefix-compatible
        ("J"/"James" ok; "James"/"J" ok; "James"/"James" ok)

    Incompatible (returns False) only when both sides carry middle tokens AND some
    aligned pair CONTRADICTS ("J" vs "R", "James" vs "Robert"). A length mismatch
    among *present* middles (e.g. ["j","q"] vs ["j"]) is treated as compatible on
    the overlapping prefix — the extra token on one side is like an absent token on
    the other. This keeps "John Q Smith" ≡ "John Q A Smith" from being called a
    contradiction while still catching "John Q" vs "John R".
    """
    if not a_middles or not b_middles:
        return True
    for a, b in zip(a_middles, b_middles):
        if not _middle_token_compatible(a, b):
            return False
    return True


def names_match(name_a: str, name_b: str) -> str:
    """Classify two PERSON names as "same" | "ambiguous" | "different".

    Decision (middle-initial-insensitive by default):
      - "different"  — first or last tokens differ. (Not the same identity.)
      - "same"       — first + last match and middle tokens are COMPATIBLE
                       (one absent, or initial-prefix). This is the by-default
                       merge: "Odalys Ferncastle" == "Odalys D Ferncastle".
      - "ambiguous"  — first + last match but middle tokens CONTRADICT (both
                       present and incompatible): "Hollis J Marlowe" vs
                       "Hollis R Marlowe". NOT auto-merged; the caller must
                       disambiguate with context.

    First-name compatibility also allows initial-prefix ("U" vs "Odalys") so a name
    given only by initials still resolves; a genuinely different first name
    ("Odalys" vs "Uma"? no — those differ) yields "different".
    """
    fa, ma, la, _ = normalize_name(name_a)
    fb, mb, lb, _ = normalize_name(name_b)

    if not la or not lb:
        # One side has no surname (single token). Fall back to exact-first match.
        if fa and fb and _middle_token_compatible(fa, fb) and la == lb:
            return "same"
        return "different"

    if la != lb:
        return "different"
    # First names must be compatible (equal or initial-prefix).
    if not _middle_token_compatible(fa, fb):
        return "different"
    if middle_compatible(ma, mb):
        return "same"
    return "ambiguous"


###############################################################################
# Email local-part identity (middle-initial-insensitive, domain-guarded)
###############################################################################

def split_email(email: str) -> tuple[str, str]:
    """Return (local_part, domain) lowercased. ('', '') if not an email."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return ("", "")
    local, _, domain = email.partition("@")
    return (local, domain)


def emails_match(email_a: str, email_b: str) -> str:
    """Classify two emails as "same" | "ambiguous" | "different".

    The domain MUST match for two emails to corroborate one identity (a personal
    gmail and a work address are different keys, never merged on local-part alone).
    Given equal domains, the local-part is tokenized on '.', '_' and '-' and the
    tokens are treated exactly like a human name: first + last must match, middle
    tokens are optional/initial-compatible.

      odalys.f.ferncastle@halloway.example ≡ odalys.ferncastle@halloway.example  -> "same"
      hollis.j.marlowe@x vs hollis.r.marlowe@x                             -> "ambiguous"
      odalys.ferncastle@halloway.example vs odalys.ferncastle@mail.example      -> "different"
    """
    la, da = split_email(email_a)
    lb, db = split_email(email_b)
    if not la or not lb:
        return "different"
    if da != db:
        return "different"
    if la == lb:
        return "same"
    # Reuse the name logic on the tokenized local-parts.
    a_name = " ".join(t for t in re.split(r"[._\-]+", la) if t)
    b_name = " ".join(t for t in re.split(r"[._\-]+", lb) if t)
    return names_match(a_name, b_name)


###############################################################################
# People index + dynamic same-name collision detection
###############################################################################

def _load_people(registry=None) -> list[eio.Entity]:
    """Parse every entity in people/ once. Best-effort: unreadable files skipped."""
    reg = registry or load_registry()
    import index_store
    return sorted(index_store.iter_entities(reg, entity_type="person"),
                  key=lambda e: e.path.name if e.path else e.canonical_name)


def _entity_name_variants(entity: eio.Entity) -> list[str]:
    """All name strings that stand for this person: canonical + aliases + a
    'Full name' identifier/attribute if present. Comma-form aliases ('Ferncastle,
    D') are normalized to 'D Ferncastle' order first."""
    names = [entity.canonical_name, *entity.aliases]
    for bucket in (entity.identifiers, entity.attributes):
        for f in bucket:
            if f.get("key", "").strip().lower() in {"full name", "name"}:
                names.append(f["value"])
    return [_normalize_comma_form(n) for n in names if n and n.strip()]


def _normalize_comma_form(name: str) -> str:
    """'Last, First M' -> 'First M Last'. Leaves already-natural names untouched.
    Mirrors stage_inbox's CN parsing so aliases stored in comma form still match."""
    if "," not in name:
        return name.strip()
    last, _, rest = name.partition(",")
    last = last.strip()
    rest = rest.strip().rstrip(".")
    if not last or not rest:
        return name.strip()
    return f"{rest} {last}"


def find_name_collisions(registry=None, people: list[eio.Entity] | None = None
                         ) -> dict[tuple[str, str], list[str]]:
    """Dynamically detect genuine same-(first,last) collisions among stored people.

    A (first, last) key is a COLLISION only when two or more stored people share it
    yet cannot all be the same identity — i.e. some pair CONTRADICTS on middle
    tokens (`names_match(...) == "ambiguous"`). Mere redundant variants of one
    person ("Damian Culverwell" + "Damian H Culverwell") are NOT a collision: they fold
    together and resolve cleanly.

    Returns {(first, last): [canonical_name, ...]} listing the canonical names of the
    DISTINCT people involved in each true collision. If the DB has no such
    redundancy for a key, that key is absent and callers may resolve it cleanly.

    Example (invented): {('ada','quill'): ['Ada L Quill','Ada H
    Penn']}. NOT flagged: ('sam','hartley') when both records are one person
    under two spellings.
    """
    people = people if people is not None else _load_people(registry)
    from collections import defaultdict
    by_key: dict[tuple[str, str], list[eio.Entity]] = defaultdict(list)
    for ent in people:
        first, _mid, last, _p = normalize_name(ent.canonical_name)
        if first and last:
            by_key[(first, last)].append(ent)

    collisions: dict[tuple[str, str], list[str]] = {}
    for key, ents in by_key.items():
        if len(ents) < 2:
            continue
        # Cluster the entities: any pair that is "same" belongs together. A key is
        # a true collision iff it yields 2+ distinct clusters (i.e. some pair is
        # "ambiguous"/contradicting). "different" can't occur here (same first+last).
        clusters: list[list[eio.Entity]] = []
        for ent in ents:
            placed = False
            for cluster in clusters:
                if names_match(ent.canonical_name, cluster[0].canonical_name) == "same":
                    cluster.append(ent)
                    placed = True
                    break
            if not placed:
                clusters.append([ent])
        if len(clusters) >= 2:
            collisions[key] = [c[0].canonical_name for c in clusters]
    return collisions


DISTINCT_FROM = "Distinct from"


def entity_key(ent: eio.Entity) -> str | None:
    """Stable ``directory/stem`` identity for an entity — the same shape a
    relationship's ``target_dir/target_stem`` uses, so the two are comparable."""
    if ent.path is None:
        return None
    return f"{ent.path.parent.name}/{ent.path.stem}"


def distinct_from_pairs(entities: list[eio.Entity] | None = None,
                        registry=None) -> set[frozenset[str]]:
    """Adjudicated "these are NOT the same thing" edges, as ``{frozenset{key, key}}``.

    A ``Distinct from`` edge records a HUMAN judgment that two similarly-named
    records are two real things (the two Cordelia Wickhams, not one typo). It is a
    registry-symmetric relationship, so ``commit.py`` writes both sides and either
    direction populates the pair here.

    This is the "yes, I already know — handled" signal. Callers use it to stop
    re-litigating a collision that has already been settled:
      * ``analyze_database.find_ambiguities`` drops adjudicated pairs, so the
        deduper is never handed the same pair twice.
      * ``resolve_person`` reports an adjudicated collision as EXPECTED ambiguity
        (disambiguate by context) rather than a suspected duplicate.

    Unlike the old ``_index/known_ambiguities.json`` cache this replaces, the
    judgment lives on the entities themselves: git-tracked, reciprocity-enforced,
    validated, and authored through a manifest like every other fact.
    """
    reg = registry or load_registry()
    if entities is None:
        import index_store
        entities = list(index_store.iter_entities(reg))

    pairs: set[frozenset[str]] = set()
    for ent in entities:
        src = entity_key(ent)
        if src is None:
            continue
        for rel in ent.relationships:
            if rel.get("rel_type") != DISTINCT_FROM:
                continue
            tgt_dir, tgt_stem = rel.get("target_dir"), rel.get("target_stem")
            if tgt_dir and tgt_stem:
                pairs.add(frozenset((src, f"{tgt_dir}/{tgt_stem}")))
    return pairs


###############################################################################
# Contextual person resolution
###############################################################################

# The project's four-level confidence scale (registry: confidence_levels).
_CONF = ("HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW")


@dataclass
class Candidate:
    """One person that survived resolution, with why it matched."""
    canonical_name: str
    path: str                       # relative to graph/
    entity_type: str = "person"
    score: float = 0.0              # name-match score in [0,1] where available
    reasons: list[str] = field(default_factory=list)
    note: str = ""                  # e.g. "requires disambiguation"


@dataclass
class ResolutionResult:
    """Structured outcome of resolve_person.

    Fields:
      query:      the raw query string.
      resolved:   canonical name of the single confident resolution, or None.
      confidence: one of HIGH / MEDIUM-HIGH / MEDIUM / LOW (None if no candidates).
      candidates: all surviving candidates (1 when resolved cleanly; 2+ when
                  ambiguous; possibly several near-misses when nothing dominates).
      rationale:  human-readable explanation of the decision.
      requires_disambiguation: True when the caller must choose / supply more
                  context before this can be treated as a single identity.
    """
    query: str
    resolved: str | None = None
    confidence: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    rationale: str = ""
    requires_disambiguation: bool = False

    @property
    def resolved_path(self) -> str | None:
        if not self.resolved:
            return None
        for c in self.candidates:
            if c.canonical_name == self.resolved:
                return c.path
        return None

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "resolved": self.resolved,
            "confidence": self.confidence,
            "requires_disambiguation": self.requires_disambiguation,
            "rationale": self.rationale,
            "candidates": [
                {"canonical_name": c.canonical_name, "path": c.path,
                 "score": round(c.score, 3), "reasons": c.reasons, "note": c.note}
                for c in self.candidates
            ],
        }


def _rel_path(entity: eio.Entity) -> str:
    try:
        return str(entity.path.relative_to(paths.WORKING_MODEL))
    except Exception:
        return str(entity.path) if entity.path else ""


def _org_name_set(registry=None) -> dict[str, str]:
    """Map every organization name/alias (lowercased) -> canonical org name, so an
    org abbreviation in context ('CMG') resolves to its entity."""
    reg = registry or load_registry()
    import index_store
    out: dict[str, str] = {}
    for ent in index_store.iter_entities(reg, entity_type="organization"):
        out[ent.canonical_name.lower()] = ent.canonical_name
        for alias in ent.aliases:
            if alias.strip():
                out[alias.strip().lower()] = ent.canonical_name
    return out


def _resolve_org_context(orgs: list[str], org_index: dict[str, str]) -> set[str]:
    """Resolve context org strings (names/abbrevs) to canonical org names via the
    org name+alias index. Unmatched strings are kept verbatim (still usable for a
    loose membership-target comparison)."""
    resolved: set[str] = set()
    for o in orgs:
        key = o.strip().lower()
        if key in org_index:
            resolved.add(org_index[key])
        elif o.strip():
            resolved.add(o.strip())
    return resolved


def _person_membership_names(entity: eio.Entity) -> set[str]:
    """Canonical target names of this person's Member of / Leads / Works on edges,
    lowercased — the orgs/groups/projects they belong to."""
    keep = {"Member of", "Leads", "Works on", "Contributes to", "Part of"}
    return {r["target_name"].strip().lower()
            for r in entity.relationships if r.get("rel_type") in keep}


def _person_all_relationship_targets(entity: eio.Entity) -> set[str]:
    """All relationship target names (lowercased) — for related-entity matching."""
    return {r["target_name"].strip().lower()
            for r in entity.relationships if r.get("target_name")}


def _person_emails(entity: eio.Entity) -> list[str]:
    """Every email identifier value on this person (already lowercased on parse)."""
    out = []
    for f in entity.identifiers:
        if "email" in f.get("key", "").lower():
            out.append(f["value"])
    return out


# --- Institution (top-level org) normalization -----------------------------
# A person's org edges may point at a sub-unit while the disambiguating context names only the
# parent institution. For the "same first+last at the same institution" confounder rule we
# compare at the TOP-LEVEL institution. Roll-up patterns are site-specific and come from the
# optional scripts/site_local.py (INSTITUTION_PATTERNS); without it every org is its own institution.
try:
    import site_local as _site_local
    _INSTITUTION_PATTERNS = list(getattr(_site_local, "INSTITUTION_PATTERNS", []))
except ImportError:
    _INSTITUTION_PATTERNS = []


def top_level_institution(org_name: str) -> str:
    """Normalize an org name to its top-level institution (lowercased).

    Names matching a site-local roll-up pattern map to that institution; every other org maps
    to its own lowercased name. Deliberately conservative: unknown orgs stay distinct."""
    s = (org_name or "").strip().lower()
    if not s:
        return ""
    for pat, inst in _INSTITUTION_PATTERNS:
        if pat.search(s):
            return inst
    return s


def person_institutions(entity: eio.Entity) -> set[str]:
    """Top-level institutions this person belongs to, from their organization edges."""
    out = set()
    for r in entity.relationships:
        if r.get("target_dir") == "organizations" and \
                r.get("rel_type") in {"Member of", "Leads", "Works on", "Contributes to"}:
            inst = top_level_institution(r.get("target_name", ""))
            if inst:
                out.add(inst)
    return out


def _query_is_underspecified(query: str) -> bool:
    """A single-token query (bare first OR last name) is under-specified."""
    return len([t for t in re.split(r"\s+", query.strip()) if t]) < 2


def resolve_person(query: str, *, context: dict | None = None,
                   registry=None,
                   people: list[eio.Entity] | None = None) -> ResolutionResult:
    """Resolve a person reference to an EXISTING entity, using optional context.

    This is the main entry point. It is resolve-only: it reads people/ and returns
    a structured decision; it never writes.

    Same-name collisions are detected LIVE on every call (`find_name_collisions`),
    so the answer can never be stale. Backed by the sqlite index that scan costs
    ~60ms across the whole model — cheap enough that the old
    `_index/known_ambiguities.json` fast path was retired rather than maintained.

    What that cache tried to persist and could not, a `Distinct from` edge now does
    properly: when a human has ruled two same-named people genuinely distinct, this
    resolver reports the ambiguity as ADJUDICATED (narrow by context, never merge)
    instead of as a suspected duplicate. See `distinct_from_pairs`.

    Args:
      query:   a person reference — full name, first-name-only, last-name-only, a
               nickname/alias, or an email address.
      context: optional dict of disambiguating signals, any subset of:
                 - "orgs": list[str] of org names/abbreviations the source ties the
                    person to (e.g. ["CMG"]). Resolved against org names+aliases,
                    then intersected with each person's Member of/Leads/Works on.
                 - "email": str — an email for the person. Matched
                    middle-initial-insensitively against stored emails, domain-guarded.
                 - "co_mentions": list[str] of other entity names appearing in the
                    same source. A candidate whose relationships touch a co-mention
                    is favored.
                 - "related_entities": list of (type, name) or "type:name" strings
                    the source ties to the person (projects, tech, places, events).
                    A candidate linked to one is favored.
      registry / people: injectable for testing.

    Returns a ResolutionResult. Semantics:
      - Clean, unique name match (and no active collision) -> HIGH, one candidate.
      - Under-specified query narrowed by context to exactly one -> resolved, with
        confidence reflecting how strong the signal was (email > org/related > co-mention).
      - Multiple survivors -> resolved=None, requires_disambiguation=True, LOW, ALL
        listed each with a "requires disambiguation" note.
      - No match -> resolved=None, empty candidates, rationale explains.

    Examples:
      resolve_person("Odalys Ferncastle")
        -> HIGH, "Odalys D Ferncastle" (middle-initial-insensitive, unique).
      resolve_person("Odalys", context={"orgs": ["CMG"]})
        -> HIGH if exactly one CMG member is named Odalys.
      resolve_person("Marlowe", context={"email": "hollis.j.marlowe@agency.example"})
        -> resolves to Hollis Marlowe via email even though several Marlowes exist.
    """
    context = context or {}
    reg = registry or load_registry()
    people = people if people is not None else _load_people(reg)

    # --- Stage 1: name candidates ---------------------------------------------
    # An email-only query: derive a name from the local part for display, but rely
    # on the email match below.
    email_query = None
    q = query.strip()
    if "@" in q and " " not in q:
        email_query = q

    underspecified = _query_is_underspecified(query) and not email_query
    qtok = _clean_token(query.strip()) if underspecified else ""
    # Does the QUERY itself carry a discriminating middle token? If so, a stored
    # entry whose middle CONTRADICTS the query is a genuinely different person
    # (the query explicitly asked for a different middle) — not "ambiguous". The
    # "ambiguous" state is only meaningful when the query lacks that middle.
    _qf, _query_middles, _ql, _qp = normalize_name(query)

    name_state: dict[str, str] = {}   # canonical_name -> "same"/"ambiguous"
    name_score: dict[str, float] = {}
    for ent in people:
        best_state = "different"
        best_score = 0.0
        for variant in _entity_name_variants(ent):
            sc = _match_score(query, variant)
            if sc > best_score:
                best_score = sc
            if underspecified:
                # First-name-only / last-name-only / nickname: match the single
                # query token against the person's first OR last token (initial-
                # prefix allowed). This is a PARTIAL match — never a clean unique
                # identity on its own unless it turns out to be globally unique,
                # so we mark it "same" (a compatible partial) and rely on the
                # uniqueness / context logic downstream to decide.
                f, _m, l, _p = normalize_name(variant)
                if qtok and (_middle_token_compatible(qtok, f)
                             or (l and _middle_token_compatible(qtok, l))
                             or qtok in {a.lower() for a in ent.aliases}):
                    best_state = "same"
            else:
                st = names_match(query, variant)
                # If the query carries its own middle token, treat an "ambiguous"
                # (contradicting-middle) variant as "different" — the query already
                # disambiguated. Ambiguity is only real when the query is silent on
                # the middle.
                if st == "ambiguous" and _query_middles:
                    st = "different"
                # rank same > ambiguous > different
                if st == "same":
                    best_state = "same"
                elif st == "ambiguous" and best_state != "same":
                    best_state = "ambiguous"
        if best_state in ("same", "ambiguous"):
            name_state[ent.canonical_name] = best_state
            name_score[ent.canonical_name] = best_score

    by_name = {e.canonical_name: e for e in people}

    # If an email was supplied (or is the query), pull in every person whose stored
    # email matches it middle-initial-insensitively and domain-guarded.
    email = (context.get("email") or email_query or "").strip().lower()
    email_hits: set[str] = set()
    if email:
        for ent in people:
            for stored in _person_emails(ent):
                if emails_match(email, stored) == "same":
                    email_hits.add(ent.canonical_name)
                    break

    # Seed the working candidate set.
    if email_query and not name_state:
        # Pure email query with no name signal: candidates are the email hits.
        candidate_names = set(email_hits)
    else:
        candidate_names = set(name_state)
        # An email in context can also *rescue* a name that scored as "different"
        # only because the DB stores a very different canonical — but we only do
        # that when the email uniquely points somewhere, handled in narrowing.

    if not candidate_names and not email_hits:
        return ResolutionResult(
            query=query, resolved=None, confidence=None, candidates=[],
            rationale=f"No stored person matches '{query}'"
                      + (" or the supplied email." if email else "."),
            requires_disambiguation=False)

    # Build Candidate objects.
    def _mk(name: str) -> Candidate:
        ent = by_name[name]
        reasons = []
        st = name_state.get(name)
        if st == "same":
            reasons.append("name matches (middle-initial-insensitive)")
        elif st == "ambiguous":
            reasons.append("same first+last but middle initials contradict")
        if name in email_hits:
            reasons.append(f"email matches ({email})")
        return Candidate(canonical_name=name, path=_rel_path(ent),
                         score=name_score.get(name, 1.0 if name in email_hits else 0.0),
                         reasons=reasons)

    working = {n: _mk(n) for n in (candidate_names | email_hits)}

    # --- Stage 2: hard identity signals (email) -------------------------------
    # Email is the reliable identity key (per CONVENTIONS). If the email matches
    # exactly one person, that resolves regardless of how many name-only survivors
    # there were.
    if email and email_hits:
        if len(email_hits) == 1:
            name = next(iter(email_hits))
            cand = working[name]
            return ResolutionResult(
                query=query, resolved=name, confidence="HIGH",
                candidates=[cand],
                rationale=f"Resolved by email {email} (unique match) -> {name}.",
                requires_disambiguation=False)
        else:
            # Two people share a matching email token pattern — genuinely ambiguous.
            cands = _flag_ambiguous([working[n] for n in email_hits])
            return ResolutionResult(
                query=query, resolved=None, confidence="LOW", candidates=cands,
                rationale=f"Email {email} is compatible with {len(email_hits)} "
                          f"stored people; requires disambiguation.",
                requires_disambiguation=True)

    # --- Stage 3: name-only resolution when no context narrows ----------------
    same_names = [n for n in candidate_names if name_state.get(n) == "same"]
    ambiguous_names = [n for n in candidate_names if name_state.get(n) == "ambiguous"]

    # Detect whether this (first,last) is a same-name collision in the DB. Always a
    # live scan against the model: backed by the sqlite index this costs ~60ms for
    # the whole model, which is why the old _index/known_ambiguities.json fast path
    # was retired — it saved ~60ms while risking a stale answer, and the durable half
    # of what it stored (the human's adjudication) is now a `Distinct from` edge.
    qf, _qm, ql, _qp = normalize_name(query)
    is_collision_key = ((qf, ql) in find_name_collisions(reg, people)
                        if (qf and ql) else False)

    context_orgs = context.get("orgs") or []
    co_mentions = [c.strip().lower() for c in (context.get("co_mentions") or []) if c and c.strip()]
    related = _parse_related(context.get("related_entities") or [])
    has_context = bool(context_orgs or co_mentions or related)

    # Clean unique resolution when exactly one compatible "same" match survives and
    # no contradicting sibling remains. A live collision key normally blocks this
    # (the surname is genuinely shared) — UNLESS the query itself carried a
    # discriminating middle token that already singled this person out.
    if len(same_names) == 1 and not ambiguous_names and (
            not is_collision_key or _query_middles):
        name = same_names[0]
        detail = ("query's middle initial resolves the same-name collision"
                  if is_collision_key and _query_middles
                  else "middle-initial variants treated as one identity")
        return ResolutionResult(
            query=query, resolved=name, confidence="HIGH", candidates=[working[name]],
            rationale=f"Unique name match -> {name} ({detail}).",
            requires_disambiguation=False)

    # Otherwise we have multiple survivors (or a contradiction, or a collision key).
    # Apply context narrowing across ALL surviving candidates.
    survivors = list(candidate_names)
    if has_context:
        narrowed = _narrow_by_context(
            [by_name[n] for n in survivors], context_orgs, co_mentions, related,
            reg, working)
        if len(narrowed) == 1:
            name = narrowed[0].canonical_name
            conf = _confidence_from_reasons(narrowed[0].reasons)
            return ResolutionResult(
                query=query, resolved=name, confidence=conf,
                candidates=[narrowed[0]],
                rationale=f"Narrowed to a single person by context -> {name} "
                          f"({'; '.join(narrowed[0].reasons)}).",
                requires_disambiguation=False)
        elif narrowed:
            survivors = [c.canonical_name for c in narrowed]

    # Still more than one -> ambiguous. If exactly one clean "same" survivor and the
    # rest are only contradicting siblings with no support, prefer the clean one
    # ONLY when there's no live collision AND no other same-match.
    clean_survivors = [n for n in survivors if name_state.get(n) == "same"]
    if len(clean_survivors) == 1 and not is_collision_key and not has_context:
        name = clean_survivors[0]
        return ResolutionResult(
            query=query, resolved=name, confidence="MEDIUM-HIGH",
            candidates=[working[name]],
            rationale=f"Single compatible identity -> {name}; contradicting "
                      f"same-surname entries exist but do not match the query's middle.",
            requires_disambiguation=False)

    cands = _flag_ambiguous([working[n] for n in survivors])
    reason = "multiple people match"
    if is_collision_key:
        reason = (f"'{qf.title()} {ql.title()}' is a known same-name collision in "
                  f"the database")

    # If a human has already ruled these survivors DISTINCT, say so: this is settled
    # ambiguity to disambiguate by context, NOT a suspected duplicate to merge.
    survivor_ents = [by_name[n] for n in survivors]
    adjudicated = distinct_from_pairs(entities=survivor_ents, registry=reg)
    survivor_keys = {entity_key(e) for e in survivor_ents} - {None}
    settled = [p for p in adjudicated if p <= survivor_keys]
    hint_note = (" These are ADJUDICATED distinct people (`Distinct from` on record),"
                 " not suspected duplicates — narrow by context, never merge."
                 if settled else "")
    return ResolutionResult(
        query=query, resolved=None, confidence="LOW", candidates=cands,
        rationale=f"Ambiguous: {reason}"
                  + (" and context did not narrow to one." if has_context
                     else "; supply org/email/related-entity context to disambiguate.")
                  + hint_note,
        requires_disambiguation=True)


def _parse_related(items: list) -> list[tuple[str, str]]:
    """Normalize related_entities into a list of (type, name) with type possibly ''.
    Accepts ("project","GAMMA") tuples or "project:GAMMA" / "GAMMA" strings."""
    out = []
    for it in items:
        if isinstance(it, (tuple, list)) and len(it) == 2:
            out.append((str(it[0]).strip().lower(), str(it[1]).strip()))
        elif isinstance(it, str):
            if ":" in it:
                t, _, n = it.partition(":")
                out.append((t.strip().lower(), n.strip()))
            elif it.strip():
                out.append(("", it.strip()))
    return out


def _narrow_by_context(entities: list[eio.Entity], orgs: list[str],
                       co_mentions: list[str], related: list[tuple[str, str]],
                       registry, working: dict[str, Candidate]) -> list[Candidate]:
    """Return the subset of `entities` supported by at least one context signal,
    annotating each surviving Candidate with the reasons it survived. If NO entity
    is supported by any signal, returns [] (caller keeps the full survivor set)."""
    org_index = _org_name_set(registry)
    wanted_orgs = _resolve_org_context(orgs, org_index) if orgs else set()
    wanted_orgs_lc = {o.lower() for o in wanted_orgs}
    # Also compare at the top-level institution so "Halloway" context matches a
    # candidate whose stored membership is a Halloway sub-unit ("Halloway Group 7 ...").
    wanted_inst = {top_level_institution(o) for o in wanted_orgs_lc} - {""}
    related_names_lc = {n.lower() for _t, n in related}

    supported: list[Candidate] = []
    for ent in entities:
        cand = working[ent.canonical_name]
        reasons_added = []

        if wanted_orgs_lc:
            memberships = _person_membership_names(ent)
            hit = wanted_orgs_lc & memberships
            if not hit and wanted_inst:
                # Institution-level match (sub-unit rolls up to the same institution).
                if wanted_inst & {top_level_institution(m) for m in memberships}:
                    hit = {sorted(wanted_inst)[0]}
            if hit:
                reasons_added.append(f"member of {sorted(hit)[0]}")

        if related_names_lc:
            targets = _person_all_relationship_targets(ent)
            hit = related_names_lc & targets
            if hit:
                reasons_added.append(f"linked to {sorted(hit)[0]}")

        if co_mentions:
            targets = _person_all_relationship_targets(ent)
            hit = set(co_mentions) & targets
            if hit:
                reasons_added.append(f"co-mentioned with {sorted(hit)[0]}")

        if reasons_added:
            cand.reasons = cand.reasons + reasons_added
            supported.append(cand)
    return supported


def _confidence_from_reasons(reasons: list[str]) -> str:
    """Map the strongest supporting signal to a confidence level."""
    joined = " ".join(reasons).lower()
    if "email" in joined:
        return "HIGH"
    if "member of" in joined or "linked to" in joined:
        return "MEDIUM-HIGH"
    if "co-mentioned" in joined:
        return "MEDIUM"
    if "name matches" in joined:
        return "HIGH"
    return "MEDIUM"


def _flag_ambiguous(cands: list[Candidate]) -> list[Candidate]:
    """Attach the standard low-confidence disambiguation note to each candidate."""
    for c in cands:
        c.note = "requires disambiguation"
    # Highest name score first for a stable, useful ordering.
    return sorted(cands, key=lambda c: c.score, reverse=True)
