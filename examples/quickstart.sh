#!/bin/sh
# 60-second quickstart: build the invented demo graph, then consult it.
set -e
cd "$(dirname "$0")/.."
python3 examples/build_demo_graph.py --out "${TMPDIR:-/tmp}/dialexis-demo"
G="${TMPDIR:-/tmp}/dialexis-demo"
python3 run.py --graph-root "$G" resolve "Wren Calloway" | head -3
python3 run.py --graph-root "$G" resolve "Teodor Vashenko" | head -2
python3 run.py --graph-root "$G" resolve Ysolde --org "Coastal Modelling Group" | head -2
