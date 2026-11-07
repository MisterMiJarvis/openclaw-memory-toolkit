#!/usr/bin/env python3
"""P0-A : benchmark de latence sur copie SQLite isolée.

Créé par HAL le 2026-11-07. Aucune écriture en production.
Mesure : 20 faits (modèle chaud) puis 40 faits.
Sépare temps modèle (embedding) / temps SQLite / reste.
"""
import os
import sys
import time
import shutil
import sqlite3
import statistics

SRC = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db"
PATCHED = "/home/ubuntu/cogitator-p0a-patch/patched"
INSTALLED = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search"
T = "/home/ubuntu/cogitator-p0a-patch/tests"


def fresh(name):
    dst = os.path.join(T, name)
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(dst + suf):
            os.remove(dst + suf)
    shutil.copy(SRC, dst)
    return dst


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run_batch(db, n, label, warm=False):
    """Insère n faits et mesure séparément embedding / sqlite / total."""
    sys.path.insert(0, INSTALLED)
    sys.path.insert(0, PATCHED)
    import conflict_resolver as cr
    import hybrid_search as hs

    conn = cr.connect(db)
    cr.vector_status()
    totals, models, sqls = [], [], []

    # warm-up : 1 embedding hors mesure
    if warm:
        t = time.perf_counter()
        hs.get_embedding("warmup P0A benchmark")
        print(f"  warm-up embedding : {(time.perf_counter()-t)*1000:.0f} ms")

    for i in range(n):
        content = f"P0A BENCH {label} fait {i} — mesure de latence isolée"
        t0 = time.perf_counter()
        # temps modèle seul
        tm0 = time.perf_counter()
        emb = hs.get_embedding(content[:2000])
        model_ms = (time.perf_counter() - tm0) * 1000
        # temps insertion (fait + vecteur), sans embedding
        ts0 = time.perf_counter()
        cur = conn.execute(
            "INSERT INTO memories (content, category, layer, source, score, subject, status, "
            "confidence, valid_from, source_context, last_confirmed, updated_at) "
            "VALUES (?, 'fact', 'semantic', 'conflict-resolver', 0.5, ?, 'active', 0.5, ?, ?, ?, ?)",
            (content, f"bench-{label}", "2026-11-07 12:00:00", f"bench-{label}",
             "2026-11-07 12:00:00", "2026-11-07 12:00:00"))
        mid = cur.lastrowid
        conn.execute("INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                     (mid, hs.serialize_f32(emb)))
        sqlite_ms = (time.perf_counter() - ts0) * 1000
        conn.commit()
        total_ms = (time.perf_counter() - t0) * 1000

        totals.append(total_ms)
        models.append(model_ms)
        sqls.append(sqlite_ms)

    st = cr.vector_status()
    conn.close()

    resid = [t - m - s for t, m, s in zip(totals, models, sqls)]
    print(f"\n=== {label} : {n} faits ===")
    print(f"  durée totale      : {sum(totals)/1000:.1f} s")
    print(f"  embedding appels  : {st['ok']} ok / {st['failed']} échec")
    print(f"  TOTAL   p50 {pct(totals,50):7.1f} ms | p95 {pct(totals,95):7.1f} ms | max {max(totals):7.1f} ms")
    print(f"  MODELE  p50 {pct(models,50):7.1f} ms | p95 {pct(models,95):7.1f} ms | max {max(models):7.1f} ms")
    print(f"  SQLITE  p50 {pct(sqls,50):7.1f} ms | p95 {pct(sqls,95):7.1f} ms | max {max(sqls):7.1f} ms")
    print(f"  RESTE   p50 {pct(resid,50):7.1f} ms | p95 {pct(resid,95):7.1f} ms | max {max(resid):7.1f} ms")
    part_model = 100 * sum(models) / sum(totals)
    print(f"  part modèle       : {part_model:.1f} % du temps total")
    return dict(n=n, total=sum(totals)/1000, p50=pct(totals, 50), p95=pct(totals, 95),
                mx=max(totals), model_p50=pct(models, 50), sqlite_p50=pct(sqls, 50),
                part=part_model)


def main():
    print("Source :", SRC)
    db = fresh("bench.db")

    print("\n" + "=" * 60)
    print("LOT 1 : 20 faits, modèle préalablement échauffé")
    print("=" * 60)
    r20 = run_batch(db, 20, "lot20", warm=True)

    print("\n" + "=" * 60)
    print("LOT 2 : 40 faits, modèle déjà chaud")
    print("=" * 60)
    r40 = run_batch(db, 40, "lot40", warm=False)

    print("\n" + "=" * 60)
    print("SYNTHÈSE")
    print("=" * 60)
    print(f"  20 faits : {r20['total']:.1f} s | p50 {r20['p50']:.0f} ms | p95 {r20['p95']:.0f} ms | max {r20['mx']:.0f} ms")
    print(f"  40 faits : {r40['total']:.1f} s | p50 {r40['p50']:.0f} ms | p95 {r40['p95']:.0f} ms | max {r40['mx']:.0f} ms")
    print(f"  part modèle 20 : {r20['part']:.1f} %  | 40 : {r40['part']:.1f} %")
    print(f"  coût par fait ≈ {r40['p50']:.0f} ms (p50)")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    print(f"  faits insérés : {c.execute(chr(115)+'elect count(*) from memories where subject like ' + chr(39) + 'bench-%' + chr(39)).fetchone()[0]}")
    c.close()


if __name__ == "__main__":
    main()
