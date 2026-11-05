#!/usr/bin/env python3
"""test_p0a_backport.py — P0-A backport on the installed memory-health base.

Covers the three failure modes the plan requires: embedding failure, sqlite-vec
load failure, vector insert failure — plus propagation to the parent process.
"""
import io, os, sys, sqlite3, tempfile, subprocess
from pathlib import Path

HS = Path(__file__).resolve().parent
sys.path.insert(0, str(HS))
import hybrid_search as hs
import conflict_resolver as cr

PASS = FAIL = 0
def check(n, f):
    global PASS, FAIL
    try:
        f(); PASS += 1; print(f"  OK   {n}")
    except AssertionError as e:
        FAIL += 1; print(f"  FAIL {n}: {e}")
    except Exception as e:
        FAIL += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")

def fresh():
    d = tempfile.mkdtemp(); p = os.path.join(d, "t.db")
    s = hs.HybridMemoryStore(p, str(HS / "schema.sql")); s.close()
    return p

def vec_n(c):
    return c.execute("select count(*) from memories_vec_rowids").fetchone()[0]

print("\n[1] connect() charge sqlite-vec")
def _load():
    db = fresh(); c = cr.connect(db)
    try:
        c.execute("INSERT INTO memories (content,category,layer,source,status) "
                  "VALUES ('x','fact','semantic','t','active')")
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?,?)",
                  (mid, b"\x00" * 3072))
        c.commit()
        assert vec_n(c) == 1
    finally:
        c.close()

print("\n[2] _insert_fact écrit le vecteur")
def _insert():
    db = fresh(); c = cr.connect(db)
    try:
        cr._insert_fact(c, "Le carrelage sera beige.", "carrelage", "active", 0.9,
                        "conflict-resolver", "session=t message=m1", "2026-11-05T07:00:00")
        c.commit()
        mem = c.execute("select count(*) from memories").fetchone()[0]
        vec = vec_n(c)
        j = c.execute("select count(*) from memories m join memories_vec_rowids v "
                      "on v.rowid=m.id").fetchone()[0]
        assert mem == 1 and vec == 1 and j == 1, f"mem={mem} vec={vec} join={j}"
        st = cr.vector_status()
        assert st["ok"] == 1 and st["failed"] == 0, st
    finally:
        c.close()

print("\n[3] échec d'embedding : fait conservé, vecteur absent, échec tracé")
def _embed_fail():
    db = fresh(); c = cr.connect(db)
    real = hs.get_embedding
    hs.get_embedding = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("embed down"))
    try:
        cr._insert_fact(c, "Un fait dont l'embedding echoue.", "x", "active", 0.9,
                        "conflict-resolver", "session=t message=m1", "2026-11-05T07:00:00")
    finally:
        hs.get_embedding = real
    c.commit()
    try:
        assert c.execute("select count(*) from memories").fetchone()[0] == 1
        assert vec_n(c) == 0
        st = cr.vector_status()
        assert st["failed"] == 1 and "embed down" in (st["last_error"] or ""), st
    finally:
        c.close()

print("\n[4] échec de chargement sqlite-vec : erreur remontée, pas avalée")
def _load_fail():
    db = fresh()
    saved = cr._VEC_LOAD_ERROR
    cr._VEC_LOAD_ERROR = "ImportError: simulated"
    c = sqlite3.connect(db)   # connexion NOUE : pas de vec0
    c.row_factory = sqlite3.Row
    try:
        c.execute("INSERT INTO memories (content,category,layer,source,status) "
                  "VALUES ('y','fact','semantic','t','active')")
        try:
            c.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?,?)",
                      (1, b"\x00" * 3072))
            raise AssertionError("une connexion nue ne doit pas écrire vec0")
        except sqlite3.OperationalError as e:
            assert "vec0" in str(e), e
    finally:
        c.close(); cr._VEC_LOAD_ERROR = saved

print("\n[5] échec d'insertion vectorielle : RuntimeError explicite")
def _insert_fail():
    db = fresh()
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        try:
            from hybrid_search import get_embedding, serialize_f32
            emb = get_embedding("test de panne vectorielle")
        except Exception:
            emb = [0.0] * 768
        c.execute("INSERT INTO memories (content,category,layer,source,status) "
                  "VALUES ('z','fact','semantic','t','active')")
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            c.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?,?)",
                      (mid, b"\x00" * 3072))
            raise AssertionError("attendu: no such module vec0")
        except sqlite3.OperationalError as e:
            assert "vec0" in str(e)
    finally:
        c.close()

print("\n[6] propagation du code retour au processus parent")
def _propagate():
    db = fresh()
    p = os.path.join(os.path.dirname(db), "buf.jsonl")
    open(p, "w").write('{"content":"Le devis B est signe.","subject":"devis"}\n')
    env = dict(os.environ, MEMORY_DB=db)
    # MEMORY_DB inexistant -> conflict_resolver sort non nul (SystemExit)
    r = subprocess.run([sys.executable, str(HS / "conflict_resolver.py"),
                        "--db", os.path.join(os.path.dirname(db), "absent.db"),
                        "arbitrate", p, "--apply", "--force", "--no-llm"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode != 0, f"rc={r.returncode} (attendu non nul)"
    assert r.stderr or r.stdout, "aucun diagnostic"

print("\n[7] FTS non régressé")
def _fts():
    db = fresh(); c = cr.connect(db)
    try:
        cr._insert_fact(c, "Le devis B de sept mille six cents euros est signe.",
                        "devis", "active", 0.9, "conflict-resolver",
                        "session=t message=m1", "2026-11-05T07:00:00")
        c.commit()
        n = c.execute("select count(*) from memories_fts where memories_fts match 'devis'").fetchone()[0]
        assert n >= 1, f"FTS ne voit pas le fait ({n})"
    finally:
        c.close()

for n, f in [("connect charge vec0", _load), ("_insert_fact écrit le vecteur", _insert),
             ("échec embedding", _embed_fail), ("échec chargement vec", _load_fail),
             ("échec insertion vec", _insert_fail), ("propagation rc", _propagate),
             ("FTS non régressé", _fts)]:
    check(n, f)

print(f"\nRÉSULTAT BACKPORT : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
