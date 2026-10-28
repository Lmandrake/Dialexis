# Batch Lifecycle and Automatic Cleanup

## Overview

Batches are **temporary work directories** used during ingestion. They are automatically cleaned
up by `reconcile.py` after the entities have been committed to `graph/`.

## Batch Statuses

```
STAGED     → Files archived to archive/raw_inbox/, batch directory created
COMMITTED  → Manifest applied to graph/ by commit.py
(removed)  → Batch directory deleted by reconcile.py auto-cleanup
```

There are only two statuses written to `_manifest.json`: **STAGED** (by `stage_inbox.py`) and
**COMMITTED** (by `commit.py`). Cleanup is handled automatically — there is no separate
"INTEGRATED" step the agent must perform.

## Automatic Cleanup (reconcile.py)

Every time `reconcile.py` runs, it checks all batch directories and removes only those with
**positive evidence of integration**. Two criteria are checked:

### Path 1: INTEGRATED status (explicit / legacy)
A batch whose `_manifest.json` has `status: "INTEGRATED"` (set by the optional `finalize.py`
script). Retained for backward compatibility.

### Path 2: Committed batch verified against the database
A batch directory containing `manifest.json` (the commit manifest) **and** every entity that
manifest upserts is confirmed present in `graph/`. This self-heals even when the status
field was never advanced.

> **No staged-batch cleanup.** A still-`STAGED` batch with no commit manifest is **never**
> removed, even if all its source files are already in `archive/raw_inbox/`. Staging archives
> sources *immediately*, so "sources archived" is true the moment a batch is created — long
> before it is committed. An earlier version deleted staged batches on that condition and
> silently destroyed uncommitted, in-flight work. With no commit manifest there is nothing to
> verify against the database, so the batch is left in place for the user to resolve.

## What Gets Preserved

After cleanup, these remain permanently:
- Source files in `archive/raw_inbox/` (immutable record)
- Entity files in `graph/` (the database)
- Generated indices in `graph/_index/`

## Standard Ingestion Flow

```bash
python3 run.py stage_inbox                           # Creates batch, archives sources
# [agent produces manifest.json]
python3 run.py commit batches/<id>/manifest.json     # Applies manifest, marks COMMITTED
python3 run.py reconcile                             # Normalizes DB, CLEANS BATCH
python3 run.py validate                              # Confirms integrity
```

After reconcile, the batch directory is gone. Zero active batches = system is clean.

## Manual Batch Management

To keep a batch for debugging, ensure it has no `manifest.json` commit file and its
`_manifest.json` status is neither `INTEGRATED` nor verifiable against the database. A `STAGED`
(or otherwise non-integrated) batch with no commit manifest is retained automatically:

```json
{
  "batch_id": "20260615_145122",
  "status": "DEBUG",
  "files": [...]
}
```

## Checking Batch Status

```bash
python3 run.py system_status
```

Shows active batches with their status. Zero batches after ingestion = working correctly.
