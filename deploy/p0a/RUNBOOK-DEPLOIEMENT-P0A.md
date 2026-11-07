# RUNBOOK — Déploiement P0-A (conflict_resolver.py + auto_capture.py)

**Version** : 2.0 — 2026-11-07
**Cible** : OpenClaw 2026.10.8 (dc2523e) — skill `memory-health`
**Statut** : PRÊT POUR CONTRE-AUDIT — **NON DÉPLOYÉ**
**Base de référence** : commit `p0a-package-deployment` (figé, voir §0)

Toutes les valeurs sont **inline dans les scripts** : aucun placeholder à substituer
manuellement. Chaque étape critique **sort en erreur non nulle** en cas d'échec.

---

## 0. Ancrage : récupérer les patches depuis le commit figé

Les patches ne doivent **jamais** venir d'un répertoire local non versionné.

```bash
set -euo pipefail
REPO=/home/ubuntu/cogitator-audit/openclaw-memory-toolkit
COMMIT=<SHA_DU_COMMIT_CORRECTIF>      # à remplacer par le SHA livré
PKG=/home/ubuntu/p0a-pkg

rm -rf "$PKG"; mkdir -p "$PKG"
cd "$REPO"
git fetch origin >/dev/null
git cat-file -e "$COMMIT^{commit}" || { echo "NO-GO: commit $COMMIT absent"; exit 1; }
git archive "$COMMIT" deploy/p0a | tar -x -C "$PKG"
test -f "$PKG/deploy/p0a/conflict_resolver.patch" || { echo "NO-GO: patch absent"; exit 1; }
test -f "$PKG/deploy/p0a/auto_capture.patch"      || { echo "NO-GO: patch absent"; exit 1; }

# les patches extraits doivent avoir l'empreinte attendue
cd "$PKG/deploy/p0a"
[ -f PATCH-SHA256.txt ] || { echo "NO-GO: manifeste PATCH-SHA256.txt absent du commit"; exit 1; }
sha256sum -c PATCH-SHA256.txt || { echo "NO-GO: patch modifié"; exit 1; }
```

---

## 1. Préflight — bloquant

```bash
set -euo pipefail
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
EXPECTED_CONFLICT=4bb9d44c22edb4081464a19024b95e071adf1d2b85b1775fc79cdedb36b3a10a
EXPECTED_CAPTURE=087493f3b51b9e6df8d38b2a466f0b3218c33a9e327273a4224a1df33e8603f6

for f in conflict_resolver.py auto_capture.py; do
  [ -f "$D/$f" ] || { echo "NO-GO: $f absent"; exit 1; }
done

ACT_CONFLICT=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
ACT_CAPTURE=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)

[ "$ACT_CONFLICT" = "$EXPECTED_CONFLICT" ] || {
  echo "NO-GO: conflict_resolver.py SHA=$ACT_CONFLICT attendu=$EXPECTED_CONFLICT"; exit 1; }
[ "$ACT_CAPTURE" = "$EXPECTED_CAPTURE" ] || {
  echo "NO-GO: auto_capture.py SHA=$ACT_CAPTURE attendu=$EXPECTED_CAPTURE"; exit 1; }

# aucun process long ne doit tenir les modules en mémoire
if pgrep -af "memory-health|hybrid_search|auto_capture|conflict_resolver" | grep -v grep >/dev/null; then
  echo "NO-GO: process memory-health actif"; exit 1
fi

# espace disque : 5 GiB minimum
AVAIL=$(df -B1 --output=avail / | tail -1 | tr -d ' ')
[ "$AVAIL" -ge 5368709120 ] || { echo "NO-GO: espace libre ${AVAIL}o < 5GiB"; exit 1; }

echo "PASS préflight"
```

---

## 2. Sauvegarde — bloquante

### 2a. Fichiers Python

```bash
set -euo pipefail
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
BK=/home/ubuntu/backups/p0a-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BK"
cp -a "$D/conflict_resolver.py" "$BK/conflict_resolver.py"
cp -a "$D/auto_capture.py"      "$BK/auto_capture.py"

# les copies doivent être bit-à-bit identiques aux originaux
cmp -s "$D/conflict_resolver.py" "$BK/conflict_resolver.py" || { echo "NO-GO: copie altérée"; exit 1; }
cmp -s "$D/auto_capture.py"      "$BK/auto_capture.py"      || { echo "NO-GO: copie altérée"; exit 1; }

sha256sum "$BK"/*.py > "$BK/PY-SHA256.txt"
echo "$BK" > /home/ubuntu/.p0a-backup-path
echo "PASS sauvegarde py -> $BK"
```

### 2b. Base SQLite cohérente (avec WAL)

```bash
set -euo pipefail
BK=$(cat /home/ubuntu/.p0a-backup-path)
SRC=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db
DST="$BK/agent_memory.db"

python3 - "$SRC" "$DST" <<'PY'
import sqlite3, sys, os
src, dst = sys.argv[1], sys.argv[2]
if not os.path.exists(src):
    sys.exit("NO-GO: base source absente")
s = sqlite3.connect(src); d = sqlite3.connect(dst)
s.backup(d); d.close(); s.close()
print("backup écrit:", dst, os.path.getsize(dst), "octets")
PY

# le fichier doit exister et être intègre
[ -s "$DST" ] || { echo "NO-GO: backup vide"; exit 1; }
python3 - "$DST" <<'PY'
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
ic = c.execute("pragma integrity_check").fetchone()[0]
fk = len(c.execute("pragma foreign_key_check").fetchall())
mem = c.execute("select count(*) from memories").fetchone()[0]
vec = c.execute("select count(*) from memories_vec_rowids").fetchone()[0]
c.close()
print("integrity=%s fk=%d memories=%d vec=%d" % (ic, fk, mem, vec))
if ic != "ok":
    sys.exit("NO-GO: backup integrity_check=" + ic)
if fk != 0:
    sys.exit("NO-GO: backup foreign_key_check=%d" % fk)
if mem == 0:
    sys.exit("NO-GO: backup sans données")
PY
echo "PASS sauvegarde SQLite"
```

> **Note** : `sqlite3.backup()` capture un état transactionnellement cohérent.
> Une copie `cp` du seul `.sqlite` **ne l'est pas** en mode WAL.

---

## 3. Installation contrôlée — bloquante

```bash
set -euo pipefail
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
PKG=/home/ubuntu/p0a-pkg/deploy/p0a
EXPECTED_CONFLICT_AFTER=0527f5d490438bde4f7cec83ade8e7a8614de1746850558745d64e9df9df3c21
EXPECTED_CAPTURE_AFTER=5f256b8e30b5c48929852544fb4999b543e37571794508abc5d3920feed91fda

# dry-run : les patches doivent s'appliquer sans conflit
patch -p0 --dry-run "$D/conflict_resolver.py" < "$PKG/conflict_resolver.patch" \
  || { echo "NO-GO: patch conflict_resolver ne s'applique pas"; exit 1; }
patch -p0 --dry-run "$D/auto_capture.py" < "$PKG/auto_capture.patch" \
  || { echo "NO-GO: patch auto_capture ne s'applique pas"; exit 1; }

# application
patch -p0 "$D/conflict_resolver.py" < "$PKG/conflict_resolver.patch" \
  || { echo "ÉCHEC conflict_resolver — rollback"; bash /home/ubuntu/p0a-rollback.sh; exit 1; }
patch -p0 "$D/auto_capture.py" < "$PKG/auto_capture.patch" \
  || { echo "ÉCHEC auto_capture — rollback des DEUX"; bash /home/ubuntu/p0a-rollback.sh; exit 1; }

# vérification SHA post-installation — BLOQUANTE
ACT_C=$(sha256sum "$D/conflict_resolver.py" | cut -d' ' -f1)
ACT_A=$(sha256sum "$D/auto_capture.py"      | cut -d' ' -f1)
if [ "$ACT_C" != "$EXPECTED_CONFLICT_AFTER" ] || [ "$ACT_A" != "$EXPECTED_CAPTURE_AFTER" ]; then
  echo "NO-GO: SHA post-install divergents"
  echo "  conflict attendu=$EXPECTED_CONFLICT_AFTER obtenu=$ACT_C"
  echo "  capture  attendu=$EXPECTED_CAPTURE_AFTER obtenu=$ACT_A"
  bash /home/ubuntu/p0a-rollback.sh
  exit 1
fi
echo "PASS installation (SHA conformes)"
```

> **Règle** : un patch partiel (un fichier sur deux) **n'est jamais acceptable**.
> Tout écart SHA déclenche le rollback des **deux** fichiers.

---

## 4. Smoke non destructif — bloquant

**Aucune écriture dans la base active.** Aucun fait artificiel.

```bash
set -euo pipefail
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
cd "$D"

python3 - <<'PY'
import sys, sqlite3
sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search")

fail = []

# 1. imports des deux modules
try:
    import conflict_resolver as cr
    import auto_capture as ac
except Exception as e:
    sys.exit("NO-GO: import échoué: %s: %s" % (type(e).__name__, e))

# 2. API attendue présente
for mod, attr in ((cr, "vector_status"), (ac, "_report_vector_coverage")):
    if not hasattr(mod, attr):
        fail.append("%s.%s absent" % (mod.__name__, attr))

# 3. vector_status initialisé proprement
st = cr.vector_status()
if set(st) != {"ok", "failed", "last_error"}:
    fail.append("vector_status malformé: %r" % (st,))

# 4. sqlite_vec réellement chargeable
try:
    import sqlite_vec
    p = sqlite_vec.loadable_path()
    if not p:
        fail.append("sqlite_vec.loadable_path() vide")
except Exception as e:
    fail.append("sqlite_vec KO: %s: %s" % (type(e).__name__, e))

# 5. memories_vec REQUÊTABLE sur la base active (lecture seule)
try:
    c = sqlite3.connect("file:/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db?mode=ro", uri=True)
    c.enable_load_extension(True)
    import sqlite_vec as sv
    c.load_extension(sv.loadable_path())
    c.enable_load_extension(False)
    n = c.execute("select count(*) from memories_vec").fetchone()[0]
    r = c.execute("select count(*) from memories_vec_rowids").fetchone()[0]
    c.close()
    if n <= 0:
        fail.append("memories_vec vide (%d)" % n)
    print("  memories_vec=%d rowids=%d" % (n, r))
except Exception as e:
    fail.append("memories_vec non requêtable: %s: %s" % (type(e).__name__, e))

# 6. rapport de couverture exécutable SANS écrire
try:
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ac._report_vector_coverage(
            "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db",
            "1990-01-01 00:00:00", "1990-01-02 00:00:00", 0)
    out = buf.getvalue()
    for need in ("GLOBAL", "WINDOW", "RUN attribution", "UNKNOWN"):
        if need not in out:
            fail.append("rapport: '%s' manquant" % need)
    print("  rapport OK")
except Exception as e:
    fail.append("rapport KO: %s: %s" % (type(e).__name__, e))

if fail:
    sys.exit("NO-GO smoke:\n  - " + "\n  - ".join(fail))
print("PASS smoke non destructif")
PY
```

---

## 5. Baseline de couverture (mesure, pas invariant)

`GLOBAL 0/409` **n'est pas un critère de GO**. On mesure l'état **juste avant** le
déploiement et on vérifie l'absence de **régression** ensuite.

```bash
set -euo pipefail
BK=$(cat /home/ubuntu/.p0a-backup-path)
python3 - "$BK/baseline-coverage.txt" <<'PY'
import sys, sqlite3
dst = sys.argv[1]
c = sqlite3.connect("file:/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db?mode=ro", uri=True)
tot, vec = c.execute(
    "SELECT COUNT(*), COUNT(v.rowid) FROM memories m "
    "LEFT JOIN memories_vec_rowids v ON v.rowid = m.id "
    "WHERE m.source = 'conflict-resolver'").fetchone()
allm = c.execute("select count(*) from memories").fetchone()[0]
allv = c.execute("select count(*) from memories_vec_rowids").fetchone()[0]
c.close()
line = "cr_total=%d cr_vec=%d all_total=%d all_vec=%d\n" % (tot, vec, allm, allv)
open(dst, "w").write(line)
print(line.strip())
PY
echo "Baseline enregistrée — comparer après 48 h, alerter si cr_vec DIMINUE."
```

> P0-B (backfill des 409) reste **séparé**. Ce runbook ne le traite pas.

---

## 6. Rollback

Deux procédures **distinctes**. Ne jamais les confondre.

### 6a. Rollback NORMAL — restauration des deux fichiers Python

```bash
#!/usr/bin/env bash
# /home/ubuntu/p0a-rollback.sh
set -euo pipefail
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
BK=$(cat /home/ubuntu/.p0a-backup-path)
[ -d "$BK" ] || { echo "ROLLBACK IMPOSSIBLE: $BK absent"; exit 1; }

cp -a "$BK/conflict_resolver.py" "$D/conflict_resolver.py"
cp -a "$BK/auto_capture.py"      "$D/auto_capture.py"

# preuve : SHA restaurés == SHA d'origine
( cd "$BK" && sha256sum -c PY-SHA256.txt --status ) \
  || { echo "ROLLBACK: SHA restaurés non conformes"; exit 1; }

echo "ROLLBACK NORMAL OK — deux fichiers restaurés"
```

### 6b. Restauration SQLite — EXCEPTIONNELLE, jamais automatique

**Interdite en rollback de P0-A.** Une restauration de base annulerait toutes les
écritures faites depuis le snapshot (mémoires, faits, sessions).

Exigences avant toute exécution :

1. Décision explicite, écrite, hors runbook
2. Arrêt de **toutes** les écritures (gateway arrêté, `lsof` vide)
3. Analyse et sauvegarde des données créées **depuis** le snapshot
4. Comparaison : ce qui serait perdu vs ce qui serait récupéré
5. Nouvelle sauvegarde de l'état courant avant d'écraser

```bash
# NE PAS EXÉCUTER SANS DÉCISION EXPLICITE
# set -euo pipefail
# BK=$(cat /home/ubuntu/.p0a-backup-path)
# ... procédure volontairement non fournie ici ...
```

---

## 7. Timeouts et retries réellement configurés (documentés, non modifiés)

| paramètre | valeur | source |
|---|---|---|
| Modèle d'embedding | `nomic-embed-text` | `hybrid_search.py:81` |
| `OLLAMA_URL` | `http://localhost:11434` | défaut |
| Hôtes autorisés | `localhost`, `127.0.0.1`, `::1` | `ALLOWED_OLLAMA_HOSTS` |
| Timeouts par tentative | `(30, 60, 120)` s | `EMBED_TIMEOUTS` |
| Retries | `3` | signature `get_embedding` |
| Backoff timeout | `2**attempt` s | `hybrid_search.py` |
| Backoff HTTP 429/5xx | `min(5*(attempt+1), 15)` s | idem |
| Disjoncteur | `3` échecs consécutifs | `EMBED_BREAKER_THRESHOLD` |

---

## 8. Risques résiduels

### R1 — F3b : `memories_vec` absente, extension chargée
L'insertion du vecteur échoue sur `no such table`, l'erreur est **avalée** dans
`except Exception`. Le fait est écrit, `vector_status()` enregistre `failed+1`,
mais rien ne remonte à l'appelant.
**Statut** : non bloquant (cas anormal). Mitigation : lire `vector_status()` en fin
de run, surveiller `WINDOW_PARTIAL`.

### R2 — Latence synchrone
`get_embedding()` appelé avant la transaction, même fil. Mesuré **38 ms p50**
à chaud, 94 % du temps de capture. 100 faits ≈ 4 s.
L'embedding ne tient **jamais** le verrou d'écriture SQLite.

### R3 — Indisponibilité Ollama
Jusqu'à **210 s** de blocage par fait (30+60+120) avant échec définitif.
Disjoncteur à 3 échecs consécutifs → `EmbeddingEndpointDown`.
Le fait est quand même écrit, sans vecteur.

### R4 — Limites du reporting
`WINDOW_COMPLETE` ≠ « run complet » (écritures concurrentes possibles).
`RUN attribution : UNKNOWN` est structurel : aucun marqueur de run en base.

### R5 — Pas de backfill
Les 409 faits historiques restent sans vecteur. **P0-B, séparé.**

---

## 9. Limites réelles des preuves (à ne pas surinterpréter)

| preuve | ce qu'elle ne démontre PAS |
|---|---|
| **F2** (échec transactionnel) | Ne simule pas un échec de **commit** : la connexion fermée produit `ProgrammingError`, pas un `SQLITE_FULL`/`SQLITE_BUSY` en phase de commit. La propagation d'une erreur réelle de commit n'est pas prouvée. |
| **Benchmark** | Mesure les opérations constitutives (embedding, insert, commit) **séparément**. Ce n'est **pas** `_insert_fact()` de bout en bout : les branchements du patch, `vector_status()`, la gestion d'erreur et les imports ne sont pas dans la mesure. |
| **Compteurs `vector_status()`** | Le benchmark insère via **SQL direct**, pas via `_insert_fact()`. Les compteurs `ok`/`failed` restent donc à **0** — ils ne reflètent pas les insertions mesurées. |
| **F3** (sqlite_vec KO) | Simule l'échec de `loadable_path()`. Ne couvre pas un échec `load_extension()` sur une bibliothèque présente mais corrompue. |
| **F3b** (table absente) | L'erreur est avalée — c'est le comportement testé, pas un correctif. |

---

## 10. Ordre d'exécution

```
§0  Récupération patches depuis commit figé   → NO-GO si archive/KO
§1  Préflight SHA + process + disque           → NO-GO bloquant
§2  Sauvegarde py (cmp) + SQLite (backup+check) → NO-GO bloquant
§3  dry-run, application, SHA post-install     → rollback des deux si écart
§4  Smoke non destructif (imports+vec+rapport)  → NO-GO bloquant
§5  Baseline couverture (mesure, pas invariant)
§7  Surveillance 48 h : vector_status + WINDOW_PARTIAL
§6a Rollback normal disponible à tout moment
```
