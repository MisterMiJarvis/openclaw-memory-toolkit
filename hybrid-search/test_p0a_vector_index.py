#!/usr/bin/env python3
"""test_p0a_vector_index.py — P0-A: _insert_fact must write a vector.

No network beyond the local embedder, no production DB. Each test builds its
own throwaway SQLite file from schema.sql.
"""
import os, sys, sqlite3, tempfile
from pathlib import Path

HS = Path(__file__).resolve().parent
sys.path.insert(0, str(HS))
import sqlite_vec
import hybrid_search as hs
import conflict_resolver as cr

PASS = FAIL = 0

def check(name, fn):
    global PASS, FAIL
    try:
        fn(); PASS += 1; print(f"  OK   {name}")
    except AssertionError as e:
        FAIL += 1; print(f"  FAIL {name}: {e}")
    except Exception as e:
        FAIL += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")

def fresh_db():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.db")
    s = hs.HybridMemoryStore(p, str(HS / "schema.sql"))
    s.close()
    c = sqlite3.connect(p)
    c.enable_load_extension(True)
    c.load_extension(sqlite_vec.loadable_path())
    c.enable_load_extension(False)
    return p, c

def count_vec(c):
    return c.execute("select count(*) from memories_vec_rowids").fetchone()[0]

def vec_join_ok(c):
    q = """select count(*) from memories m
           join memories_vec_rowids v on v.rowid = m.id"""
    return c.execute(q).fetchone()[0]

print("\n[1] 1 fait -> 1 vecteur")
def _one():
    p, c = fresh_db()
    try:
        mid = cr._insert_fact(c, "Le chat s'appelle Kiddo et a trois ans.", "kiddo",
                              "active", 0.9, "conflict-resolver",
                              "session=t message=m1", "2026-11-05T01:00:00")
        c.commit()
        assert count_vec(c) == 1, f"vecteurs={count_vec(c)}"
        assert vec_join_ok(c) == 1, "jointure rowid=id échoue"
        assert isinstance(mid, int) and mid > 0
        st = cr.vector_status()
        assert st["ok"] == 1 and st["failed"] == 0, f"status={st}"
    finally:
        c.close()

print("\n[2] 38 faits -> 38 vecteurs")
def _many():
    p, c = fresh_db()
    try:
        for i in range(38):
            cr._insert_fact(c, f"Fait numero {i} sur le chantier de Pradelles.",
                            f"e{i}", "active", 0.9, "conflict-resolver",
                            f"session=t message=m{i}", "2026-11-05T01:00:00")
        c.commit()
        n = c.execute("select count(*) from memories").fetchone()[0]
        assert n == 38, f"faits={n}"
        assert count_vec(c) == 38, f"vecteurs={count_vec(c)}"
        assert vec_join_ok(c) == 38, f"jointure={vec_join_ok(c)}"
    finally:
        c.close()

print("\n[3] jointure rowid = memories.id, pas .id")
def _join():
    p, c = fresh_db()
    try:
        cr._insert_fact(c, "Le carrelage sera beige.", "carrelage", "active", 0.9,
                        "conflict-resolver", "session=t message=m1", "2026-11-05T01:00:00")
        c.commit()
        bad = c.execute("""select count(*) from memories m
                           join memories_vec_rowids v on v.id = m.id""").fetchone()[0]
        good = c.execute("""select count(*) from memories m
                            join memories_vec_rowids v on v.rowid = m.id""").fetchone()[0]
        assert good == 1 and bad == 0, f"good={good} bad={bad}"
    finally:
        c.close()

print("\n[4] dimension 768 et vecteur non nul")
def _dim():
    p, c = fresh_db()
    try:
        cr._insert_fact(c, "Isolation reportee a janvier 2027.", "isolation", "active",
                        0.9, "conflict-resolver", "session=t message=m1", "2026-11-05T01:00:00")
        c.commit()
        blob = c.execute("select embedding from memories_vec where rowid=1").fetchone()[0]
        assert len(blob) == 768 * 4, f"bytes={len(blob)} (attendu {768*4})"
        assert blob != b"\x00" * len(blob), "vecteur nul"
    finally:
        c.close()

print("\n[5] échec d'embedding simulé -> fait inséré, vecteur absent, compteur failed")
def _fail():
    p, c = fresh_db()
    try:
        real = hs.get_embedding
        hs.get_embedding = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("embed down"))
        try:
            mid = cr._insert_fact(c, "Un fait dont l'embedding echoue.", "x", "active",
                                  0.9, "conflict-resolver", "session=t message=m1",
                                  "2026-11-05T01:00:00")
        finally:
            hs.get_embedding = real
        c.commit()
        assert mid > 0, "le fait doit quand même être inséré"
        assert c.execute("select count(*) from memories").fetchone()[0] == 1
        assert count_vec(c) == 0, "aucun vecteur attendu"
        st = cr.vector_status()
        assert st["failed"] == 1, f"status={st}"
        assert st["last_error"] and "embed down" in st["last_error"], st
    finally:
        c.close()

print("\n[6] pas de doublon ni d'orphelin après plusieurs insertions")
def _clean():
    p, c = fresh_db()
    try:
        for i in range(5):
            cr._insert_fact(c, f"Fait {i} distinct sur la cuisine.", f"c{i}", "active",
                            0.9, "conflict-resolver", f"session=t message=m{i}",
                            "2026-11-05T01:00:00")
        c.commit()
        mem = c.execute("select count(*) from memories").fetchone()[0]
        vec = count_vec(c)
        joined = vec_join_ok(c)
        assert mem == vec == joined == 5, f"mem={mem} vec={vec} join={joined}"
        dups = c.execute("""select rowid, count(*) c from memories_vec_rowids
                            group by rowid having c>1""").fetchall()
        assert not dups, f"doublons={dups}"
    finally:
        c.close()

print("\n[7] non-régression : le fait reste visible en FTS")
def _fts():
    p, c = fresh_db()
    try:
        cr._insert_fact(c, "Le devis B de sept mille six cents euros est signe.",
                        "devis", "active", 0.9, "conflict-resolver",
                        "session=t message=m1", "2026-11-05T01:00:00")
        c.commit()
        n = c.execute("select count(*) from memories_fts where memories_fts match 'devis'").fetchone()[0]
        assert n >= 1, f"FTS ne voit pas le fait (n={n})"
    finally:
        c.close()

for name, fn in [("1 fait -> 1 vecteur", _one), ("38 faits -> 38 vecteurs", _many),
                 ("jointure rowid", _join), ("dimension 768", _dim),
                 ("échec embedding", _fail), ("ni doublon ni orphelin", _clean),
                 ("FTS non régressé", _fts)]:
    check(name, fn)

print(f"\nRÉSULTAT P0-A : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
