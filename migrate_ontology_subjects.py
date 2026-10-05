#!/usr/bin/env python3
"""Migration M4-B — ontology nodes: real entity id as subject, ghost nodes archived.

Reality check that triggered this script (dry-run output):
  * 898 indexed rows have source='ontology/graph.jsonl' and were bulk-labelled
    subject='graph' by the filename-derived migration. That collapses 898 distinct
    entities under ONE generic key — the opposite of what arbitration needs.
  * graph.jsonl only holds 210 entities with a usable id. So 209 indexed nodes
    match a live entity; the remaining ~689 are GHOSTS: entities that no longer
    exist in the compacted ontology but are still ACTIVE in the hot DB.

PLM view (Stéphane): graph.jsonl is the reference nomenclature. If a part is gone
from the nomenclature, it has no business on the production line (the hot DB).

Actions (both dry-run by default, --apply to write):
  1. SURVIVORS  -> subject = exact entity id (e.g. 3dx_transfer_eiidpp)
  2. GHOSTS     -> status = 'superseded'  (leaves the active retrieval radius;
                   compact.py later moves them to memories_archive)

Never deletes. Never touches non-ontology rows. --apply backs up first.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ONT_SOURCE = "ontology/graph.jsonl"
NODE_KEY = lambda name, typ: f"{name} ({typ})"  # what index_jsonl_file wrote


def backup(db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = Path(db_path).parent / "backups" / f"{Path(db_path).name}.pre-m4b-ontology-{stamp}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    dest = sqlite3.connect(str(dst))
    with dest:
        src.backup(dest)
    dest.close()
    src.close()
    return str(dst)


def load_graph(graph_path: str) -> dict:
    """Return {display_key: entity_id} for every live entity in the oplog."""
    ent = {}
    p = Path(graph_path)
    if not p.exists():
        raise SystemExit(f"graph not found: {graph_path}")
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        e = r.get("entity") or (r if r.get("id") else None)
        if not e or not e.get("id"):
            continue
        props = e.get("properties") or {}
        name = props.get("name") or e.get("name") or ""
        typ = e.get("type") or ""
        ent[NODE_KEY(name, typ)] = e["id"]
    return ent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).parent / "hybrid-search" / "agent_memory.db"))
    ap.add_argument("--graph", default=str(Path.home() / ".openclaw" / "workspace"
                                        / "memory" / "ontology" / "graph.jsonl"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    graph = load_graph(args.graph)
    print(f"graph.jsonl live entities : {len(graph)}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    rows = conn.execute(
        "SELECT id, content, subject, status FROM memories WHERE source=? AND status='active'",
        (ONT_SOURCE,),
    ).fetchall()
    print(f"active ontology rows      : {len(rows)}")

    survivors, ghosts = [], []
    for r in rows:
        eid = graph.get(r["content"])
        if eid:
            survivors.append((eid, r["id"]))
        else:
            ghosts.append(r["id"])

    print(f"  survivors (real id)     : {len(survivors)}")
    print(f"  ghosts (to supersede)   : {len(ghosts)}")
    for eid, rid in survivors[:5]:
        print(f"    ✔ id={rid:5} -> subject={eid}")
    for rid in ghosts[:5]:
        print(f"    ⤫ id={rid:5} -> superseded (no live entity)")

    if not args.apply:
        print("\n(dry-run — pass --apply to write)")
        conn.close()
        return 0

    bkp = backup(args.db)
    print(f"\nbackup: {bkp}")
    # Keep FKs happy: a superseded row cannot be the target of a live superseded_by.
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for eid, rid in survivors:
            conn.execute("UPDATE memories SET subject=? WHERE id=?", (eid, rid))
        for rid in ghosts:
            conn.execute(
                "UPDATE memories SET status='superseded', updated_at=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), rid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    still_graph = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE subject='graph' AND status='active'"
    ).fetchone()[0]
    print(f"done. active rows still subject='graph': {still_graph}")
    print(f"      superseded ghosts remaining active: "
          f"{conn.execute('SELECT COUNT(*) FROM memories WHERE source=? AND status=\"active\"', (ONT_SOURCE,)).fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
