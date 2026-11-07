#!/usr/bin/env python3
"""P0-A : tests de non-régression sur copie SQLite isolée.

Créé par HAL le 2026-11-07. Aucune écriture en production.
Usage: python3 test_p0a.py <chemin_db_copie>
"""
import os
import sys
import sqlite3
import time
import shutil

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/cogitator-p0a-patch/tests/test_agent_memory.db"
PATCHED = "/home/ubuntu/cogitator-p0a-patch/patched"
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def counts(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    r = {
        "memories": c.execute("select count(*) from memories").fetchone()[0],
        "vec": c.execute("select count(*) from memories_vec_rowids").fetchone()[0],
        "cr": c.execute("select count(*) from memories where source='conflict-resolver'").fetchone()[0],
        "cr_vec": c.execute(
            "select count(*) from memories m join memories_vec_rowids v on v.rowid=m.id "
            "where m.source='conflict-resolver'").fetchone()[0],
    }
    c.close()
    return r


def main():
    print(f"Base de test : {DB}")
    before = counts(DB)
    print(f"AVANT : {before}")

    # --- T1 : les 35 517 vecteurs existants restent intacts
    record("T1 vecteurs existants intacts", before["vec"] == 35517, f"{before['vec']} vecteurs")

    # --- T2 : les 409 faits CR restent sans vecteur (pas de backfill)
    record("T2 pas de backfill des 409", before["cr"] == 409 and before["cr_vec"] == 0,
           f"CR={before['cr']} vectorisés={before['cr_vec']}")

    # --- T3 : module patché importable (PATCHED doit passer en PREMIER)
    sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search")
    sys.path.insert(0, PATCHED)  # après = priorité absolue
    try:
        import conflict_resolver as cr
        ok = hasattr(cr, "vector_status") and cr.__file__.startswith(PATCHED)
        record("T3 import conflict_resolver patché", ok,
               f"fichier={cr.__file__} vector_status={hasattr(cr, 'vector_status')}")
    except Exception as e:
        record("T3 import conflict_resolver patché", False, f"{type(e).__name__}: {e}")
        return

    # --- T4 : vector_status() existe et se réinitialise
    try:
        s1 = cr.vector_status()
        s2 = cr.vector_status()
        record("T4 vector_status reset", s1.get("ok") == 0 and s2.get("ok") == 0, str(s1))
    except Exception as e:
        record("T4 vector_status reset", False, str(e))

    # --- T5 : insert d'un fait -> vecteur créé
    try:
        conn = cr.connect(str(DB))
        t0 = time.time()
        mid = cr._insert_fact(conn, "P0A TEST — fait de référence isolé, ne pas conserver",
                              "p0a-test", "active", 0.5, "conflict-resolver",
                              "p0a-test-window", "2026-11-07 11:00:00")
        conn.commit()
        dt = time.time() - t0
        after = counts(DB)
        gained_vec = after["vec"] - before["vec"]
        record("T5 fait inséré + vectorisé", after["memories"] == before["memories"] + 1 and gained_vec >= 1,
               f"mem_id={mid} vec+{gained_vec} en {dt*1000:.0f} ms")
        conn.close()
    except Exception as e:
        record("T5 fait inséré + vectorisé", False, f"{type(e).__name__}: {e}")

    # --- T6 : les 409 restent sans vecteur après insertion
    after = counts(DB)
    record("T6 409 toujours sans vecteur", after["cr_vec"] == 1 and after["cr"] == 410,
           f"CR={after['cr']} vectorisés={after['cr_vec']} (le nouveau est vectorisé)")

    # --- T7 : FTS fonctionne
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = c.execute("select count(*) from memories_fts where memories_fts match 'P0A'").fetchone()[0]
        c.close()
        record("T7 FTS opérationnel", n >= 1, f"{n} résultat(s)")
    except Exception as e:
        record("T7 FTS opérationnel", False, str(e))

    # --- T8 : recherche vectorielle opérationnelle
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        import sqlite_vec
        c.enable_load_extension(True)
        c.load_extension(sqlite_vec.loadable_path())
        c.enable_load_extension(False)
        n = c.execute("select count(*) from memories_vec").fetchone()[0]
        c.close()
        record("T8 vec0 requêtable", n > 0, f"{n} vecteurs")
    except Exception as e:
        record("T8 vec0 requêtable", False, f"{type(e).__name__}: {e}")

    print("\n=== RÉSULTATS ===")
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"  {ok}/{len(RESULTS)} PASS")
    for name, o, d in RESULTS:
        print(f"  {'✅' if o else '❌'} {name}" + (f" — {d}" if d else ""))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
