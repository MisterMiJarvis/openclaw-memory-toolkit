#!/usr/bin/env python3
"""test_p0a_report.py — P0-A post-execution coverage report (three levels).

GLOBAL is exact. WINDOW is exact over its interval but may include concurrent
writes (declared). RUN attribution is UNKNOWN by design: no run marker exists.
"""
import os, sys, sqlite3, tempfile, subprocess, io, contextlib
from pathlib import Path

HS = Path(__file__).resolve().parent
sys.path.insert(0, str(HS))
import hybrid_search as hs
import auto_capture as ac

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

def add_fact(db, text, with_vec=True):
    import conflict_resolver as cr
    c = cr.connect(db)
    try:
        cr._insert_fact(c, text, "s", "active", 0.9, "conflict-resolver",
                        "session=t message=m", "2026-11-05T09:00:00")
        c.commit()
        if not with_vec:
            c.execute("DELETE FROM memories_vec WHERE rowid=(SELECT MAX(id) FROM memories)")
            c.commit()
    finally:
        c.close()

def capture(db, rc=0):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ac._report_vector_coverage(db, "2000-01-01 00:00:00", "2099-01-01 00:00:00", rc)
    return buf.getvalue()

print("\n[1] succès complet : GLOBAL et WINDOW à 100%")
def _complete():
    db = fresh(); add_fact(db, "Le carrelage sera beige.")
    out = capture(db, 0)
    assert "1/1 = 100.0%" in out, out
    assert "COMPLETE" in out, out
    assert "UNKNOWN" in out, "RUN doit rester UNKNOWN"

print("\n[2] fait sans vecteur : PARTIAL")
def _partial():
    db = fresh(); add_fact(db, "Le devis B est signe.", with_vec=True)
    add_fact(db, "Fait sans vecteur.", with_vec=False)
    out = capture(db, 0)
    assert "1/2 = 50.0%" in out, out
    assert "PARTIAL" in out, out

print("\n[3] run sans nouveau fait : UNKNOWN, pas COMPLETE")
def _empty():
    db = fresh()
    out = capture(db, 0)
    assert "UNKNOWN" in out, out
    assert "COMPLETE" not in out, out

print("\n[4] échec transactionnel : FAILED avec returncode affiché")
def _failed():
    db = fresh(); add_fact(db, "Un fait.")
    out = capture(db, 3)
    assert "returncode          : 3" in out, out
    assert "FAILED" in out, out

print("\n[5] écriture concurrente : la fenêtre inclut, le global reste exact")
def _concurrent():
    db = fresh()
    add_fact(db, "Fait du run.")
    # une écriture « concurrente » simulée, hors fenêtre demandée
    add_fact(db, "Fait concurrent.")
    out = capture(db, 0)
    assert "2/2 = 100.0%" in out, out   # GLOBAL voit les deux

print("\n[6] erreur de lecture : le rapport ne casse pas le run")
def _read_error():
    out = capture("/nonexistent/path.db", 0)
    assert "UNKNOWN" in out, out

print("\n[7] le rapport ne modifie pas la base")
def _readonly():
    db = fresh(); add_fact(db, "Un fait.")
    before = os.path.getsize(db)
    capture(db, 0)
    assert os.path.getsize(db) == before, "la base a grossi"

for n, f in [("succès complet", _complete), ("fait sans vecteur", _partial),
             ("run vide", _empty), ("échec transactionnel", _failed),
             ("écriture concurrente", _concurrent), ("erreur de lecture", _read_error),
             ("lecture seule", _readonly)]:
    check(n, f)

print(f"\nRÉSULTAT BILAN : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
