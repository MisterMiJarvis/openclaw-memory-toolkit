#!/usr/bin/env python3
"""test_p0a_connect_vec.py — regression for "no such module: vec0".

The bug: conflict_resolver.connect() opened a plain sqlite3 connection without
loading sqlite-vec. Reading memories_vec_rowids worked (plain table) but
INSERT INTO memories_vec failed, so the auto_capture subprocess silently wrote
facts with no vector.

This test fails if the vec0 module is not loadable on the resolver's connection.
"""
import os, sys, sqlite3, tempfile
from pathlib import Path

HS = Path(__file__).resolve().parent
sys.path.insert(0, str(HS))
import hybrid_search as hs
from conflict_resolver import connect

PASS = FAIL = 0
def check(name, fn):
    global PASS, FAIL
    try:
        fn(); PASS += 1; print(f"  OK   {name}")
    except AssertionError as e:
        FAIL += 1; print(f"  FAIL {name}: {e}")
    except Exception as e:
        FAIL += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")

def fresh():
    d = tempfile.mkdtemp(); p = os.path.join(d, "t.db")
    s = hs.HybridMemoryStore(p, str(HS / "schema.sql")); s.close()
    return p

print("\n[1] la connexion conflict_resolver accepte un INSERT vec0")
def _insert():
    db = fresh(); c = connect(db)
    try:
        c.execute("INSERT INTO memories (content,category,layer,source,status) "
                  "VALUES ('x','fact','semantic','t','active')")
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?,?)",
                  (mid, b"\x00" * (768 * 4)))
        c.commit()
        assert c.execute("SELECT count(*) FROM memories_vec_rowids").fetchone()[0] == 1
    finally:
        c.close()

print("\n[2] le défaut historique est bien reproduit sans le correctif")
def _repro():
    """A plain sqlite3 connection must still fail — proving the test is real."""
    db = fresh()
    c = sqlite3.connect(db)
    try:
        c.execute("INSERT INTO memories (content,category,layer,source,status) "
                  "VALUES ('y','fact','semantic','t','active')")
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            c.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?,?)",
                      (mid, b"\x00" * (768 * 4)))
            raise AssertionError("une connexion nue ne devrait pas pouvoir écrire vec0")
        except sqlite3.OperationalError as e:
            assert "vec0" in str(e), f"message inattendu: {e}"
    finally:
        c.close()

print("\n[3] _insert_fact écrit le vecteur via la connexion du resolver")
def _end_to_end():
    import conflict_resolver as cr
    db = fresh(); c = connect(db)
    try:
        cr._insert_fact(c, "Le carrelage sera beige.", "carrelage", "active", 0.9,
                        "conflict-resolver", "session=t message=m1", "2026-11-05T01:00:00")
        c.commit()
        n = c.execute("SELECT count(*) FROM memories").fetchone()[0]
        v = c.execute("SELECT count(*) FROM memories_vec_rowids").fetchone()[0]
        j = c.execute("SELECT count(*) FROM memories m JOIN memories_vec_rowids r "
                      "ON r.rowid=m.id").fetchone()[0]
        assert n == 1 and v == 1 and j == 1, f"mem={n} vec={v} join={j}"
    finally:
        c.close()

for name, fn in [("INSERT vec0 sur connexion resolver", _insert),
                 ("défaut reproduit sur connexion nue", _repro),
                 ("bout-en-bout _insert_fact", _end_to_end)]:
    check(name, fn)

print(f"\nRÉSULTAT : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
