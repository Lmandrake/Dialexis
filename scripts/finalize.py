"""finalize.py - Mark a batch as INTEGRATED and remove its directory.

Step 7 of the ingestion harness. Called after commit + reconcile + validate succeed.

Safe to skip: reconcile.py's self-healing verifies entity existence and cleans up
batches on its next run regardless of status. This script provides immediate cleanup.

Usage: python3 run.py finalize <batch_id>
"""
from __future__ import annotations
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import paths


def finalize_batch(batch_id: str) -> dict:
    """Mark batch INTEGRATED and remove its directory."""
    batch_dir = paths.batch_dir(batch_id)
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch directory not found: {batch_dir}")

    meta_path = batch_dir / "_manifest.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "INTEGRATED"
        meta["integrated_at"] = datetime.now().isoformat()
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    shutil.rmtree(batch_dir)
    return {"batch_id": batch_id, "action": "removed"}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 run.py finalize <batch_id>", file=sys.stderr)
        return 2

    batch_id = argv[1]
    try:
        result = finalize_batch(batch_id)
        print(f"Finalized batch {result['batch_id']}: directory removed")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
