"""The invented demo graph, built end to end through the real CLI into a temp dir.

Covers: adjudicated-distinct same-name people, a middle-initial variant resolving to one
person, a first-name query narrowed by --org, a conflict section, perspectives, and a
staged-inbox-to-graph ingestion (the builder drives stage_inbox -> commit -> reconcile ->
validate for every demo-seed step).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "demo-graph"
    r = subprocess.run([sys.executable, str(REPO / "examples" / "build_demo_graph.py"),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def resolve(demo, *args):
    r = subprocess.run([sys.executable, str(REPO / "run.py"), "--graph-root", str(demo),
                        "resolve", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_ingestion_built_entities(demo):
    assert len(list((demo / "graph").glob("*/*.md"))) >= 40
    assert (demo / "graph" / "people" / "wren_a_calloway.md").exists()


def test_same_name_people_declined_not_guessed(demo):
    out = resolve(demo, "Wren Calloway")
    assert "AMBIGUOUS" in out and "ADJUDICATED distinct" in out
    assert "Wren A Calloway" in out and "Wren T Calloway" in out


def test_middle_initial_variant_resolves_to_one(demo):
    out = resolve(demo, "Teodor Vashenko")
    assert out.startswith("# Resolved: Teodor M Vashenko")


def test_first_name_narrowed_by_org(demo):
    assert "AMBIGUOUS" in resolve(demo, "Ysolde")
    out = resolve(demo, "Ysolde", "--org", "Coastal Modelling Group")
    assert out.startswith("# Resolved: Ysolde Penhallow")


def test_conflict_recorded_not_overwritten(demo):
    text = (demo / "graph" / "_index" / "conflicts.md").read_text()
    assert "Unresolved (1)" in text and "Kestrel Array" in text


def test_perspectives_section(demo):
    texts = [p.read_text() for p in (demo / "graph" / "events").glob("*.md")]
    assert any("## Perspectives" in t for t in texts)
