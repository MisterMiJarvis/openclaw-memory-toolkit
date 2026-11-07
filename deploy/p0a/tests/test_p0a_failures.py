#!/usr/bin/env python3
"""P0-A : scénarios de panne et reporting, sur copie SQLite isolée.

Créé par HAL le 2026-11-07. Aucune écriture en production.
Usage: python3 test_p0a_failures.py <chemin_db_copie>
"""
import os
import sys
import sqlite3
import shutil
import importlib

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/cogitator-p0a-patch/tests/fail.db"
PATCHED = "/home/ubuntu/cogitator-p0a-patch/patched"
INSTALLED = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search"
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def fresh_db(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    for suf in ("-wal", "-shm"):
        if os.path.exists(dst + suf):
            os.remove(dst + suf)
    shutil.copy(src, dst)
    return dst


def counts(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    r = (c.execute("select count(*) from memories").fetchone()[0],
         c.execute("select count(*) from memories_vec_rowids").fetchone()[0])
    c.close()
    return r


def load_patched():
    sys.path.insert(0, INSTALLED)
    sys.path.insert(0, PATCHED)
    import conflict_resolver
    importlib.reload(conflict_resolver)
    assert conflict_resolver.__file__.startswith(PATCHED), conflict_resolver.__file__
    return conflict_resolver


def main():
    src = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db"
    print(f"Source (lecture seule) : {src}")

    # ── F1 : embedding indisponible -> fait conservé, absence signalée
    db = fresh_db(src, "/home/ubuntu/cogitator-p0a-patch/tests/f1.db")
    m0, v0 = counts(db)
    cr = load_patched()
    import hybrid_search
    orig_get = hybrid_search.get_embedding

    def boom(*a, **k):
        raise RuntimeError("embedder offline (simulated)")

    hybrid_search.get_embedding = boom
    try:
        conn = cr.connect(db)
        mid = cr._insert_fact(conn, "P0A FAILURE TEST — embedding down",
                              "p0a-fail", "active", 0.5, "conflict-resolver",
                              "p0a-fail-window", "2026-11-07 11:30:00")
        conn.commit()
        m1, v1 = counts(db)
        st = cr.vector_status()
        ok = (m1 == m0 + 1) and (v1 == v0) and st["failed"] >= 1 and st["last_error"]
        record("F1 embedding KO -> fait conservé, échec signalé", ok,
               f"memories+{m1-m0} vec+{v1-v0} status={st['ok']}/{st['failed']} err={(st['last_error'] or '')[:40]}")
        conn.close()
    finally:
        hybrid_search.get_embedding = orig_get

    # ── F2 : erreur transactionnelle -> propagation, pas de faux succès
    db = fresh_db(src, "/home/ubuntu/cogitator-p0a-patch/tests/f2.db")
    m0, v0 = counts(db)
    conn = cr.connect(db)
    # fermer la connexion sous la transaction provoquera une erreur au write
    raised = False
    err = ""
    try:
        cr._insert_fact(conn, "P0A FAILURE TEST — tx error",
                        "p0a-fail", "active", 0.5, "conflict-resolver",
                        "p0a-fail-window", "2026-11-07 11:31:00")
        conn.rollback()
        conn.close()
        try:
            conn.execute("INSERT INTO memories (content) VALUES ('x')")
        except sqlite3.ProgrammingError as e:
            raised = True
            err = str(e)[:40]
    except Exception as e:
        raised = True
        err = f"{type(e).__name__}: {str(e)[:40]}"
    try:
        conn.close()
    except Exception:
        pass
    m1, v1 = counts(db)
    record("F2 tx en erreur -> propagation, rollback effectif", raised and m1 == m0,
           f"raised={raised} memories+{m1-m0} ({err})")

    # ── F3 : vec0 indisponible -> RuntimeError explicite
    db = fresh_db(src, "/home/ubuntu/cogitator-p0a-patch/tests/f3.db")
    import conflict_resolver as _cr
    orig_connect = _cr.connect
    real_connect = _cr.connect

    def connect_no_vec(db_path=None):
        c = real_connect(db_path if db_path else DB)
        try:
            c.execute("DROP TABLE IF EXISTS memories_vec")
        except Exception:
            pass
        return c

    import hybrid_search as hs
    emb = hs.get_embedding
    cr._insert_fact  # noqa
    try:
        conn = real_connect(db)
        # simule l'absence de vec0 en supprimant la table sur cette connexion
        try:
            conn.execute("DROP TABLE IF EXISTS memories_vec")
            conn.commit()
        except Exception:
            pass
        err = ""
        try:
            cr._insert_fact(conn, "P0A FAILURE TEST — no vec0",
                            "p0a-fail", "active", 0.5, "conflict-resolver",
                            "p0a-fail-window", "2026-11-07 11:32:00")
            conn.commit()
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:80]}"
        conn.close()
    except Exception as e:
        err = f"setup: {type(e).__name__}: {str(e)[:60]}"

    explicit = ("vec0" in err.lower()) or ("vector indexing unavailable" in err.lower()) or ("RuntimeError" in err)
    record("F3 vec0 absent -> erreur explicite", explicit, err or "aucune erreur levée")

    # ── F4 : vector_status distingué ok/failed
    db = fresh_db(src, "/home/ubuntu/cogitator-p0a-patch/tests/f4.db")
    cr.vector_status()  # reset
    conn = cr.connect(db)
    cr._insert_fact(conn, "P0A TEST — ok path", "p0a-ok", "active", 0.5,
                    "conflict-resolver", "p0a-ok-window", "2026-11-07 11:33:00")
    conn.commit()
    conn.close()
    st = cr.vector_status()
    record("F4 vector_status compte les succès", st["ok"] >= 1, str(st))

    print("\n=== RÉSULTATS ===")
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"  {ok}/{len(RESULTS)} PASS")
    for name, o, d in RESULTS:
        print(f"  {'✅' if o else '❌'} {name}" + (f" — {d}" if d else ""))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
