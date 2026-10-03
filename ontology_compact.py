#!/usr/bin/env python3
"""Ontology graph compactor (GC for the OP-log).

Reads memory/ontology/graph.jsonl (an append-only operation log), replays it
into a consolidated state, and writes one line per active entity.

Safety: backs up first, writes to a temp file, validates entity-by-entity,
and only swaps in place if the state is provably identical.

Usage:
    python3 scripts/ontology-compact.py --dry-run   # report only
    python3 scripts/ontology-compact.py             # compact if safe
    python3 scripts/ontology-compact.py --min-gain 10  # skip if <10% smaller
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", Path.home() / ".openclaw/workspace"))
GRAPH = WORKSPACE / "memory" / "ontology" / "graph.jsonl"
BACKUP_DIR = WORKSPACE / "memory" / "ontology" / "backups"


def replay(path):
    """Replay the op-log into final entity state + superseded ids."""
    entities = {}
    superseded = set()
    order = []
    bad = 0
    total = 0

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if r.get("op") == "supersede":
            eid = r.get("entity_id")
            if eid:
                superseded.add(eid)
            continue
        e = r.get("entity")
        if not e or not e.get("id"):
            continue
        if e["id"] not in entities:
            order.append(e["id"])
        entities[e["id"]] = e

    return entities, superseded, order, total, bad


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-gain", type=float, default=5.0,
                    help="skip compaction if gain is below this percentage")
    args = ap.parse_args()

    if not GRAPH.exists():
        print(f"no graph at {GRAPH}")
        return 1

    entities, superseded, order, total, bad = replay(GRAPH)
    active = [e for e in order if e not in superseded]

    lines_out = []
    for eid in active:
        lines_out.append(json.dumps({"op": "state", "entity": entities[eid]},
                                    ensure_ascii=False, sort_keys=True))
    payload = ("\n".join(lines_out) + "\n") if lines_out else ""

    size_before = GRAPH.stat().st_size
    size_after = len(payload.encode("utf-8"))
    gain = (size_before - size_after) / size_before * 100 if size_before else 0

    print(f"  lines     : {total} -> {len(active)}  ({total - len(active)} dropped)")
    print(f"  size      : {size_before:,} -> {size_after:,} o  (-{gain:.0f}%)")
    print(f"  entities  : {len(active)} active, {len(superseded)} superseded")
    if bad:
        print(f"  WARNING   : {bad} unparsable line(s)")

    if args.dry_run:
        print("  (dry-run: nothing written)")
        return 0

    if gain < args.min_gain:
        print(f"  skip: gain {gain:.1f}% below threshold {args.min_gain}%")
        return 0

    # --- backup ---
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"graph.jsonl.pre-compact-{stamp}"
    shutil.copy2(GRAPH, backup)
    assert md5(backup) == md5(GRAPH), "backup checksum mismatch"

    # --- write temp + validate ---
    tmp = GRAPH.with_suffix(".jsonl.tmp")
    tmp.write_text(payload)
    new_entities, _, new_order, _, _ = replay(tmp)
    if sorted(new_order) != sorted(active):
        tmp.unlink()
        print("  ABORT: entity set mismatch after compaction")
        return 2
    if any(entities[k] != new_entities.get(k) for k in active):
        tmp.unlink()
        print("  ABORT: entity content mismatch after compaction")
        return 2

    # --- swap ---
    os.replace(tmp, GRAPH)
    print(f"  compacted. backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
