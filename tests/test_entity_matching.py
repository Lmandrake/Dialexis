"""test_entity_matching.py - Unit tests for the middle-initial + contextual resolver.

Runs entirely against a THROWAWAY temp copy of a tiny purpose-built fixture set
(mirrors smoke_test.py's approach). NEVER touches the live graph/.

Covered:
  - normalize_name: hyphenated first names, lowercase particles, suffixes, comma form.
  - middle_compatible / names_match: compatible-merge, contradicting-ambiguous.
  - emails_match: local-part middle-token equivalence + domain guard.
  - find_name_collisions: dynamic scan — contradiction => collision; redundant
    variants of one person => NOT a collision; single person => clean.
  - resolve_person: unique name resolve; each Capability-B context signal (org
    intersection, email, co-occurrence, related-entity); uniqueness-resolves;
    ambiguity returns ALL survivors flagged.
  - Regression: fuzzy_match_entities / find_entity / list_entities still behave.
"""
import sys
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import paths
import entity_io as eio
from registry import load_registry


def _write_person(name, *, aliases=None, emails=None, memberships=None,
                  leads=None, works_on=None, collaborates=None, full_name=None,
                  status="Active"):
    """Write a minimal but real person entity file into the temp people/ dir."""
    ent = eio.Entity(canonical_name=name, entity_type="person", status=status)
    if aliases:
        ent.aliases = list(aliases)
    ident = []
    for e in (emails or []):
        ident.append({"key": "Email", "value": e.lower(), "confidence": "HIGH"})
    if full_name:
        ident.append({"key": "Full name", "value": full_name, "confidence": "HIGH"})
    ent.identifiers = ident
    rels = []
    for m in (memberships or []):
        rels.append({"rel_type": "Member of", "target_name": m,
                     "target_dir": "organizations", "target_stem": _stem(m),
                     "confidence": "HIGH", "date": "2026"})
    for m in (leads or []):
        rels.append({"rel_type": "Leads", "target_name": m,
                     "target_dir": "organizations", "target_stem": _stem(m),
                     "confidence": "HIGH", "date": "2026"})
    for p in (works_on or []):
        rels.append({"rel_type": "Works on", "target_name": p,
                     "target_dir": "projects", "target_stem": _stem(p),
                     "confidence": "HIGH", "date": "2026"})
    for c in (collaborates or []):
        rels.append({"rel_type": "Collaborates with", "target_name": c,
                     "target_dir": "people", "target_stem": _stem(c),
                     "confidence": "HIGH", "date": "2026"})
    ent.relationships = rels
    path = paths.entity_dir("people") / f"{_stem(name)}.md"
    path.write_text(eio.serialize(ent), encoding="utf-8")


def _write_org(name, *, aliases=None):
    ent = eio.Entity(canonical_name=name, entity_type="organization", status="Active")
    if aliases:
        ent.aliases = list(aliases)
    path = paths.entity_dir("organizations") / f"{_stem(name)}.md"
    path.write_text(eio.serialize(ent), encoding="utf-8")


def _stem(name):
    from naming import slug
    return slug(name)


###############################################################################
# Pure-function tests (no disk)
###############################################################################

def test_normalize_name():
    print("Testing normalize_name...", end=" ")
    import entity_matching as em
    assert em.normalize_name("Odalys D Ferncastle") == ("odalys", ["d"], "ferncastle", [])
    assert em.normalize_name("Odalys Ferncastle") == ("odalys", [], "ferncastle", [])
    assert em.normalize_name("Tobias J. Marlowe") == ("tobias", ["j"], "marlowe", [])
    f, m, l, p = em.normalize_name("Anne-Marie de la Fontaine")
    assert f == "anne-marie" and l == "de la fontaine" and p == ["de", "la"], (f, m, l, p)
    # Suffix stripped, not treated as surname.
    assert em.normalize_name("Casper F Rowntree Jr.") == ("casper", ["f"], "rowntree", [])
    assert em.normalize_name("Alaric D Stanhope III")[2] == "stanhope"
    # Single token.
    assert em.normalize_name("Odalys") == ("odalys", [], "", [])
    print("ok")


def test_middle_compatible():
    print("Testing middle_compatible...", end=" ")
    import entity_matching as em
    assert em.middle_compatible([], ["d"]) is True          # absent is optional
    assert em.middle_compatible(["d"], []) is True
    assert em.middle_compatible(["j"], ["james"]) is True    # initial prefix
    assert em.middle_compatible(["james"], ["j"]) is True
    assert em.middle_compatible(["james"], ["james"]) is True
    assert em.middle_compatible(["j"], ["r"]) is False       # contradiction
    assert em.middle_compatible(["james"], ["robert"]) is False
    assert em.middle_compatible(["q", "a"], ["q"]) is True   # overlap prefix ok
    print("ok")


def test_names_match():
    print("Testing names_match...", end=" ")
    import entity_matching as em
    # by-default merge
    assert em.names_match("Odalys Ferncastle", "Odalys D Ferncastle") == "same"
    assert em.names_match("Tobias Marlowe", "Tobias J Marlowe") == "same"
    assert em.names_match("Damian Culverwell", "Damian H Culverwell") == "same"
    # contradiction -> ambiguous (NOT auto-merged)
    assert em.names_match("Tobias J Marlowe", "Tobias R Marlowe") == "ambiguous"
    assert em.names_match("Rupert L Chalmers", "Rupert H Chalmers") == "ambiguous"
    # different last -> different
    assert em.names_match("Casper F Rowntree Jr.", "Casper F Bridgeman Jr.") == "different"
    assert em.names_match("Odalys Ferncastle", "Bob Ferncastle") == "different"
    # initial-prefix first name still same
    assert em.names_match("O Ferncastle", "Odalys D Ferncastle") == "same"
    print("ok")


def test_emails_match():
    print("Testing emails_match...", end=" ")
    import entity_matching as em
    assert em.emails_match("odalys.f.ferncastle@halloway.example",
                           "odalys.ferncastle@halloway.example") == "same"
    assert em.emails_match("odalys.ferncastle@halloway.example",
                           "odalys.ferncastle@halloway.example") == "same"
    # contradiction in middle token
    assert em.emails_match("tobias.j.marlowe@x.gov", "tobias.r.marlowe@x.gov") == "ambiguous"
    # DOMAIN GUARD: same local-part, different domain -> different
    assert em.emails_match("odalys.ferncastle@halloway.example",
                           "odalys.ferncastle@mail.example") == "different"
    assert em.emails_match("not-an-email", "x@y.example") == "different"
    print("ok")


###############################################################################
# Disk-backed tests (temp graph/)
###############################################################################

def test_find_name_collisions():
    print("Testing find_name_collisions...", end=" ")
    import entity_matching as em
    # Redundant variants of ONE person -> NOT a collision.
    _write_person("Damian Culverwell")
    _write_person("Damian H Culverwell")
    # A single unique person -> not a collision.
    _write_person("Odalys D Ferncastle")
    # TWO genuinely distinct same-name people (contradicting middles) -> collision.
    _write_person("Rupert L Chalmers")
    _write_person("Rupert H Chalmers")

    collisions = em.find_name_collisions()
    assert ("rupert", "chalmers") in collisions, collisions
    assert set(collisions[("rupert", "chalmers")]) == {"Rupert L Chalmers", "Rupert H Chalmers"}
    assert ("daniel", "culverwell") not in collisions, "redundant variant wrongly flagged"
    assert ("odalys", "ferncastle") not in collisions
    print("ok")


def test_resolve_unique_name():
    print("Testing resolve unique name (middle-initial-insensitive)...", end=" ")
    import entity_matching as em
    _write_person("Odalys D Ferncastle", emails=["odalys.ferncastle@halloway.example"])
    r = em.resolve_person("Odalys Ferncastle")
    assert r.resolved == "Odalys D Ferncastle", r.as_dict()
    assert r.confidence == "HIGH"
    assert not r.requires_disambiguation
    # Bare first name, only one Odalys -> uniqueness resolves cleanly.
    r2 = em.resolve_person("Odalys")
    assert r2.resolved == "Odalys D Ferncastle", r2.as_dict()
    print("ok")


def test_resolve_by_org_context():
    print("Testing resolve by org membership context...", end=" ")
    import entity_matching as em
    _write_org("CMG", aliases=["Coastal Modelling Group"])
    _write_person("Odalys D Ferncastle", memberships=["CMG"])
    _write_person("Odalys Q Wallace", memberships=["Some Other Group"])  # decoy same first
    r = em.resolve_person("Odalys", context={"orgs": ["CMG"]})
    assert r.resolved == "Odalys D Ferncastle", r.as_dict()
    assert r.confidence in ("MEDIUM-HIGH", "HIGH"), r.confidence
    assert not r.requires_disambiguation
    print("ok")


def test_resolve_by_email_context():
    print("Testing resolve by email context (3 same-surname)...", end=" ")
    import entity_matching as em
    _write_person("Tobias Marlowe", emails=["tobias.j.marlowe@agency.example"])
    _write_person("Dorian P Marlowe", emails=["dorian.marlowe@agency.example"])
    _write_person("Piers S Marlowe", emails=["piers.marlowe@agency.example"])
    r = em.resolve_person("Marlowe", context={"email": "tobias.j.marlowe@agency.example"})
    assert r.resolved == "Tobias Marlowe", r.as_dict()
    assert r.confidence == "HIGH"
    # Email query alone (no name) resolves too.
    r2 = em.resolve_person("tobias.j.marlowe@agency.example")
    assert r2.resolved == "Tobias Marlowe", r2.as_dict()
    print("ok")


def test_resolve_by_related_and_comention():
    print("Testing resolve by related-entity + co-mention...", end=" ")
    import entity_matching as em
    _write_person("Odalys D Ferncastle", works_on=["GAMMA"])
    _write_person("Odalys Q Wallace", works_on=["Unrelated Project"])
    r = em.resolve_person("Odalys", context={"related_entities": ["project:GAMMA"]})
    assert r.resolved == "Odalys D Ferncastle", r.as_dict()

    # Co-mention: a person collaborating with a co-mentioned colleague.
    _write_person("Sky One", collaborates=["Rowan Marchetti"])
    _write_person("Sky Two", collaborates=["Nobody Relevant"])
    r2 = em.resolve_person("Sky", context={"co_mentions": ["Rowan Marchetti"]})
    assert r2.resolved == "Sky One", r2.as_dict()
    print("ok")


def test_ambiguous_returns_all_candidates():
    print("Testing ambiguous collision returns ALL flagged candidates...", end=" ")
    import entity_matching as em
    _write_person("Rupert L Chalmers", memberships=["Group A"])
    _write_person("Rupert H Chalmers", memberships=["Group B"])
    # No context: known collision -> ambiguous, both listed, disambiguation required.
    r = em.resolve_person("Rupert Chalmers")
    assert r.resolved is None, r.as_dict()
    assert r.requires_disambiguation
    assert r.confidence == "LOW"
    names = {c.canonical_name for c in r.candidates}
    assert names == {"Rupert L Chalmers", "Rupert H Chalmers"}, names
    assert all(c.note == "requires disambiguation" for c in r.candidates)

    # With org context that narrows to one -> resolves.
    _write_org("Group A")
    r2 = em.resolve_person("Rupert Chalmers", context={"orgs": ["Group A"]})
    assert r2.resolved == "Rupert L Chalmers", r2.as_dict()
    print("ok")


def test_contradicting_middle_query_prefers_clean_sibling():
    print("Testing explicit-middle query resolves against its own variant...", end=" ")
    import entity_matching as em
    # DB holds two contradicting people; a query WITH a middle initial that matches
    # only one of them should resolve to that one (its sibling is 'different' on middle).
    _write_person("Tobias J Marlowe")
    _write_person("Tobias R Marlowe")
    r = em.resolve_person("Tobias J Marlowe")
    assert r.resolved == "Tobias J Marlowe", r.as_dict()
    assert not r.requires_disambiguation
    print("ok")


def test_no_match():
    print("Testing no-match returns empty cleanly...", end=" ")
    import entity_matching as em
    _write_person("Odalys D Ferncastle")
    r = em.resolve_person("Zebulon Nonexistent")
    assert r.resolved is None and r.candidates == [], r.as_dict()
    assert not r.requires_disambiguation
    print("ok")


###############################################################################
# Regression: legacy functions still behave
###############################################################################

def test_regression_fuzzy_and_find():
    print("Testing legacy fuzzy_match_entities / find_entity / list_entities...", end=" ")
    import entity_matching as em
    _write_person("Dunstan E Waterhouse", aliases=["Dunstan Waterhouse"])
    _write_org("CMG")
    # fuzzy match: partial name dominates
    matches = em.fuzzy_match_entities("waterhouse", threshold=0.5)
    assert any(m["entity"] == "Dunstan E Waterhouse" for m in matches), matches
    # find_entity by exact canonical + type
    p = em.find_entity("Dunstan E Waterhouse", "person")
    assert p is not None and p.name == "dunstan_e_waterhouse.md"
    assert em.find_entity("No Such Person", "person") is None
    # list_entities
    names = {e["name"] for e in em.list_entities("person")}
    assert "Dunstan E Waterhouse" in names, names
    print("ok")


def test_ics_seed_resolve_only():
    """Regression: the .ics attendee seed path rewrites a parsed attendee name to an
    EXISTING canonical (resolve-only) when it resolves cleanly, leaves genuinely new
    attendees as-is, keeps the event's Attended-by targets consistent, and preserves
    the seed op schema exactly."""
    print("Testing .ics attendee seed resolve-only wiring...", end=" ")
    import stage_inbox as si
    # Existing person stored under the FULLER form with the shorter email.
    _write_person("Odalys D Ferncastle", emails=["odalys.ferncastle@halloway.example"])

    ics = "\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "SUMMARY:Seed Test Sync",
        "DTSTART:20260901T170000Z", "DTEND:20260901T173000Z",
        # Parsed CN is the SHORT "Odalys Ferncastle"; email is the middle-initial
        # variant. Resolver must rewrite to the stored "Odalys D Ferncastle".
        'ATTENDEE;CN="Odalys Ferncastle":mailto:odalys.f.ferncastle@halloway.example',
        'ATTENDEE;CN="Brand New Person":mailto:brand.new@example.com',
        "END:VEVENT", "END:VCALENDAR"])
    ops = si._parse_single_ics(ics, "archive/raw_inbox/x.ics")
    persons = [o for o in ops if o["entity_type"] == "person"]
    events = [o for o in ops if o["entity_type"] == "event"]
    pnames = [p["canonical_name"] for p in persons]
    assert "Odalys D Ferncastle" in pnames, pnames          # resolved to existing
    assert "Brand New Person" in pnames, pnames             # new attendee untouched
    at_by = [r["target_name"] for r in events[0]["relationships"]
             if r["rel_type"] == "Attended by"]
    assert "Odalys D Ferncastle" in at_by, at_by            # event edge consistent
    # Seed op schema preserved exactly (no keys added/removed).
    odalys = next(p for p in persons if p["canonical_name"] == "Odalys D Ferncastle")
    assert set(odalys.keys()) == {"op", "entity_type", "canonical_name", "identifiers",
                                "attributes", "relationships", "events", "sources"}, odalys.keys()
    print("ok")


###############################################################################
# Harness
###############################################################################

def _fresh_model():
    """Wipe & recreate entity dirs so each test starts from a clean fixture set."""
    reg = load_registry()
    for _etype, info in reg._entity_types.items():
        d = paths.entity_dir(info["directory"])
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


def run_tests():
    with TemporaryDirectory() as tmpdir:
        paths.ROOT = Path(tmpdir)
        paths.WORKING_MODEL = paths.ROOT / "graph"
        paths.AGENTS = paths.ROOT / "agents"
        real_agents = Path(__file__).parent.parent / "agents"
        paths.AGENTS.mkdir(parents=True)
        shutil.copy2(real_agents / "ENTITY_TYPE_REGISTRY.md", paths.AGENTS)
        paths.REGISTRY_FILE = paths.AGENTS / "ENTITY_TYPE_REGISTRY.md"

        # Pure-function tests (no fixtures needed).
        test_normalize_name()
        test_middle_compatible()
        test_names_match()
        test_emails_match()

        # Disk-backed tests: each starts from a fresh fixture set.
        for t in (test_find_name_collisions, test_resolve_unique_name,
                  test_resolve_by_org_context, test_resolve_by_email_context,
                  test_resolve_by_related_and_comention,
                  test_ambiguous_returns_all_candidates,
                  test_contradicting_middle_query_prefers_clean_sibling,
                  test_no_match, test_regression_fuzzy_and_find,
                  test_ics_seed_resolve_only):
            _fresh_model()
            t()

        print("\nAll entity_matching tests passed")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
