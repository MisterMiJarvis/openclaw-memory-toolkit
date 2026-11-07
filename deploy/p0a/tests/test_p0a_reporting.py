#!/usr/bin/env python3
"""P0-A : tests du reporting _report_vector_coverage de auto_capture.py.

Créé par HAL le 2026-11-07. Copie SQLite isolée uniquement.
"""
import os
import sys
import sqlite3
import shutil
import io
import contextlib

SRC = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db"
PATCHED = "/home/ubuntu/cogitator-p0a-patch/patched"
INSTALLED = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search"
T = "/home/ubuntu/cogitator-p0a-patch/tests"
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def fresh(name):
    dst = os.path.join(T, name)
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(dst + suf):
            os.remove(dst + suf)
    shutil.copy(SRC, dst)
    return dst


def cap(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **k)
    return buf.getvalue()


sys.path.insert(0, INSTALLED)
sys.path.insert(0, PATCHED)
import auto_capture as ac
assert ac.__file__.startswith(PATCHED), ac.__file__

print("=== R1 : GLOBAL / WINDOW / RUN UNKNOWN, base affichée ===")
db = fresh("r1.db")
out = cap(ac._report_vector_coverage, db, "2026-11-07 00:00:00", "2026-11-07 23:59:59", 0)
print(out.rstrip())
record("R1 base affichée", db in out, "chemin présent")
record("R1 GLOBAL présent", "GLOBAL" in out, "ligne GLOBAL")
record("R1 WINDOW présent", "WINDOW" in out, "ligne WINDOW")
record("R1 RUN UNKNOWN explicite", "RUN attribution" in out and "UNKNOWN" in out, "pas d'attribution au run")
record("R1 pas de libellé 'COMPLETE' seul", "status              : COMPLETE" not in out, "évite la confusion run/fenêtre")

print("\n=== R2 : WINDOW vide -> status UNKNOWN ===")
out = cap(ac._report_vector_coverage, db, "1990-01-01 00:00:00", "1990-01-02 00:00:00", 0)
st = [l for l in out.splitlines() if l.strip().startswith("status")]
record("R2 fenêtre vide -> UNKNOWN", any("UNKNOWN" in s for s in st), st[0].strip() if st else "?")

print("\n=== R3 : WINDOW_COMPLETE (faits vectorisés dans la fenêtre) ===")
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys=ON")
# fabrique 2 faits CR vectorisés dans une fenêtre dédiée
import sqlite_vec
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())
conn.enable_load_extension(False)
for i in (1, 2):
    cur = conn.execute(
        "INSERT INTO memories (content, category, layer, source, score, subject, status, "
        "confidence, valid_from, source_context, last_confirmed, updated_at, created_at) "
        "VALUES (?, 'fact', 'semantic', 'conflict-resolver', 0.5, 'r3', 'active', 0.5, ?, ?, ?, ?, ?)",
        (f"P0A R3 fait {i}", "2026-11-07 12:00:00", "r3w", "2026-11-07 12:00:00",
         "2026-11-07 12:00:00", "2026-11-07 12:00:00"))
    mid = cur.lastrowid
    conn.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                 (mid, sqlite_vec.serialize_float32([0.1] * 768)))
conn.commit()
conn.close()
out = cap(ac._report_vector_coverage, db, "2026-11-07 11:59:00", "2026-11-07 12:01:00", 0)
st = [l for l in out.splitlines() if l.strip().startswith("status")]
record("R3 WINDOW_COMPLETE quand tout est vectorisé",
       any("WINDOW_COMPLETE" in s for s in st), st[0].strip() if st else "?")

print("\n=== R4 : WINDOW_PARTIAL (un fait sans vecteur) ===")
conn = sqlite3.connect(db)
conn.execute(
    "INSERT INTO memories (content, category, layer, source, score, subject, status, "
    "confidence, valid_from, source_context, last_confirmed, updated_at, created_at) "
    "VALUES (?, 'fact', 'semantic', 'conflict-resolver', 0.5, 'r4', 'active', 0.5, ?, ?, ?, ?, ?)",
    ("P0A R4 fait sans vecteur", "2026-11-07 13:00:00", "r4w", "2026-11-07 13:00:00",
     "2026-11-07 13:00:00", "2026-11-07 13:00:00"))
conn.commit()
conn.close()
out = cap(ac._report_vector_coverage, db, "2026-11-07 12:59:00", "2026-11-07 13:01:00", 0)
st = [l for l in out.splitlines() if l.strip().startswith("status")]
record("R4 WINDOW_PARTIAL si un fait manque de vecteur",
       any("WINDOW_PARTIAL" in s for s in st), st[0].strip() if st else "?")

print("\n=== R5 : sous-processus en échec -> status FAILED ===")
out = cap(ac._report_vector_coverage, db, "2026-11-07 12:00:00", "2026-11-07 13:00:00", 1)
st = [l for l in out.splitlines() if l.strip().startswith("status")]
record("R5 rc!=0 -> FAILED", any("FAILED" in s for s in st), st[0].strip() if st else "?")
record("R5 returncode affiché", "returncode          : 1" in out, "rc visible")

print("\n=== R6 : base absente -> message explicite ===")
out = cap(ac._report_vector_coverage, "/tmp/inexistant-xyz.db", "2026-11-07 00:00:00", "2026-11-07 01:00:00", 0)
record("R6 base absente -> message clair", "database missing" in out.lower() or "UNKNOWN" in out, out.strip()[:70])

print("\n=== RÉSULTATS ===")
ok = sum(1 for _, o, _ in RESULTS if o)
print(f"  {ok}/{len(RESULTS)} PASS")
for n, o, d in RESULTS:
    print(f"  {'✅' if o else '❌'} {n}" + (f" — {d}" if d else ""))
