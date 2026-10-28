#!/usr/bin/env python3
"""build_demo_graph.py - Build the invented demo graph through the REAL ingestion pipeline.

    python3 examples/build_demo_graph.py                  # rebuild examples/demo-graph/
    python3 examples/build_demo_graph.py --out /tmp/demo  # build somewhere else

Every person, organisation, place and project in the demo world is invented. Nothing here is
hand-written into a graph: each source under examples/demo-seed/<step>/ is dropped into the
graph root's inbox and taken through the same CLI a real session uses,

    stage_inbox -> commit <the step's manifest.json> -> reconcile -> validate

with `run.py --graph-root <out>`, so the checkout's own graph/, batches/ and inbox are never
touched. The step's manifest.json stands in for the LLM's DECIDE output (the one step a
script cannot do); its `{archive}` placeholder becomes the path stage_inbox actually archived
the source to. Any nonzero exit stops the build: a HALT here means the demo is wrong.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SEED = HERE / "demo-seed"
DEFAULT_OUT = HERE / "demo-graph"
#: Per-build state that is never part of the shipped demo graph.
TRANSIENT = (".cache", ".access-log.jsonl", "batches", "inbox", "output")


def run(out: Path, *args: str) -> str:
    r = subprocess.run([sys.executable, str(REPO / "run.py"), "--graph-root", str(out), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"build_demo_graph: `run.py {' '.join(args)}` exited {r.returncode}\n"
                 f"{r.stdout}\n{r.stderr}")
    return r.stdout


def ingest_step(out: Path, step: Path) -> None:
    sources = sorted(p for p in step.iterdir() if p.name != "manifest.json")
    if len(sources) != 1:
        sys.exit(f"build_demo_graph: {step.name} must hold one source and a manifest.json")
    shutil.copy2(sources[0], out / "inbox" / sources[0].name)
    run(out, "stage_inbox")
    batches = [b for b in (out / "batches").iterdir() if b.is_dir()]
    if len(batches) != 1:
        sys.exit(f"build_demo_graph: expected one staged batch, found {batches}")
    batch = batches[0]
    archive = json.loads((batch / "_manifest.json").read_text())["files"][0]["archive"]
    text = (step / "manifest.json").read_text().replace("{archive}", archive)
    (batch / "manifest.json").write_text(text)
    run(out, "commit", str(batch / "manifest.json"))
    run(out, "reconcile")
    run(out, "validate")
    print(f"  {step.name}: ingested {sources[0].name}")


def build(out: Path) -> None:
    out = out.resolve()
    if out in (REPO, REPO / "graph") or (REPO / "graph") in out.parents:
        sys.exit(f"build_demo_graph: refusing to build into {out}")
    if out.exists():
        if not (out / "graph").is_dir() and any(out.iterdir()):
            sys.exit(f"build_demo_graph: {out} exists and is not a demo graph; refusing")
        shutil.rmtree(out)
    for sub in ("graph", "inbox"):
        (out / sub).mkdir(parents=True)
    for step in sorted(p for p in SEED.iterdir() if p.is_dir()):
        ingest_step(out, step)
    for name in TRANSIENT:
        p = out / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    n = sum(1 for _ in (out / "graph").glob("*/*.md"))
    print(f"demo graph: {n} entities in {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    build(ap.parse_args().out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
