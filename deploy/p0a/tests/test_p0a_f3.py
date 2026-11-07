#!/usr/bin/env python3
"""P0-A : F3 (sqlite_vec indisponible au chargement) et F3b (table absente).

Créé par HAL le 2026-11-07. Copie SQLite isolée uniquement.
"""
import os
import sys
import sqlite3
import shutil
import importlib

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


def counts(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    m = c.execute("select count(*) from memories").fetchone()[0]
    try:
        v = c.execute("select count(*) from memories_vec_rowids").fetchone()[0]
    except sqlite3.OperationalError:
        v = 0   # table absente : 0 vecteur indexable
    c.close()
    return (m, v)


sys.path.insert(0, INSTALLED)
sys.path.insert(0, PATCHED)
import conflict_resolver as cr
assert cr.__file__.startswith(PATCHED), cr.__file__
import hybrid_search

print("=== F3 : sqlite_vec indisponible AU CHARGEMENT ===")
db = fresh("f3_real.db")
m0, v0 = counts(db)

# simule l'échec de load_extension dans connect()
import sqlite_vec
orig_path = sqlite_vec.loadable_path


def broken_path():
    raise OSError("sqlite_vec shared library missing (simulated)")


sqlite_vec.loadable_path = broken_path
try:
    conn = cr.connect(db)
    load_err = cr._VEC_LOAD_ERROR
    print(f"  _VEC_LOAD_ERROR après connect(): {load_err}")
    err = ""
    try:
        cr._insert_fact(conn, "P0A F3 — vec unavailable at load",
                        "p0a-f3", "active", 0.5, "conflict-resolver",
                        "p0a-f3-window", "2026-11-07 11:45:00")
        conn.commit()
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
    m1, v1 = counts(db)
    conn.close()
finally:
    sqlite_vec.loadable_path = orig_path

raised_runtime = err.startswith("RuntimeError")
mentions_vec = "vec0" in err.lower() or "vector indexing unavailable" in err.lower()
record("F3 sqlite_vec KO au chargement -> exception explicite",
       raised_runtime and mentions_vec,
       err or "aucune exception")
record("F3b-bis connect() a bien enregistré l'échec de chargement",
       bool(load_err), str(load_err)[:60])

print("\n=== F3b : table memories_vec ABSENTE (scénario distinct) ===")
db = fresh("f3b.db")
m0, v0 = counts(db)
conn = cr.connect(db)
conn.execute("DROP TABLE memories_vec")
conn.commit()
cr.vector_status()  # reset
err = ""
try:
    mid = cr._insert_fact(conn, "P0A F3b — table absente",
                          "p0a-f3b", "active", 0.5, "conflict-resolver",
                          "p0a-f3b-window", "2026-11-07 11:46:00")
    conn.commit()
    inserted = True
except Exception as e:
    err = f"{type(e).__name__}: {str(e)[:100]}"
    inserted = False
st = cr.vector_status()
m1, v1 = counts(db)
conn.close()

record("F3b erreur enregistrée dans vector_status", st["failed"] >= 1, str(st))
record("F3b aucun succès d'indexation annoncé", st["ok"] == 0, f"ok={st['ok']}")
record("F3b fait conservé (pas de perte de données)", m1 == m0 + 1, f"memories+{m1-m0}")
record("F3b erreur visible dans last_error", bool(st["last_error"]), str(st["last_error"])[:60])
record("F3b aucune exception levée (avalée) = BLOCAGE POTENTIEL",
       err == "", err or "aucune exception")

print("\n=== RÉSULTATS ===")
ok = sum(1 for _, o, _ in RESULTS if o)
print(f"  {ok}/{len(RESULTS)} PASS")
for n, o, d in RESULTS:
    print(f"  {'✅' if o else '❌'} {n}" + (f" — {d}" if d else ""))
