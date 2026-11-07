# RUNBOOK — Déploiement P0-A (conflict_resolver.py + auto_capture.py)

**Version** : 1.0 — 2026-11-07
**Cible** : OpenClaw 2026.10.8 (dc2523e) — skill `memory-health`
**Statut** : PRÊT POUR CONTRE-AUDIT — **NON DÉPLOYÉ**

---

## 0. SHA de référence

| fichier | SHA256 installé (AVANT) | SHA256 patché (APRÈS) |
|---|---|---|
| `conflict_resolver.py` | `4bb9d44c22edb4081464a19024b95e071adf1d2b85b1775fc79cdedb36b3a10a` | `0527f5d490438bde4f7cec83ade8e7a8614de1746850558745d64e9df9df3c21` |
| `auto_capture.py` | `087493f3b51b9e6df8d38b2a466f0b3218c33a9e327273a4224a1df33e8603f6` | `5f256b8e30b5c48929852544fb4999b543e37571794508abc5d3920feed91fda` |

Répertoire cible : `/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/`

---

## 1. Préflight — NO-GO si un seul contrôle échoue

```bash
D=/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search
sha256sum $D/conflict_resolver.py $D/auto_capture.py
```

**Attendu** : les deux SHA « AVANT » ci-dessus, à l'octet près.

Si divergence → **STOP**. Le fichier installé a changé depuis la qualification.
Ne pas déployer, re-qualifier.

```bash
# gateway actif ?
systemctl --user is-active openclaw-gateway

# pas de migration/backfill en cours ?
pgrep -af "memory-health|hybrid_search|auto_capture" | grep -v grep

# espace disque (sauvegarde = ~700 Mo)
df -h / | tail -1
```

**GO si** : SHA conformes · gateway `active` · aucun process memory-health · ≥ 5 Go libres.
**NO-GO sinon.**

---

## 2. Sauvegarde

### 2a. Fichiers Python

```bash
BK=/home/ubuntu/backups/p0a-$(date +%Y%m%d-%H%M%S)
mkdir -p $BK
cp -a $D/conflict_resolver.py $D/auto_capture.py $BK/
sha256sum $BK/*.py > $BK/PY-SHA256.txt
```

### 2b. Base SQLite cohérente (avec WAL)

**Ne pas copier le `.sqlite` à chaud.** Utiliser `sqlite3.backup()` :

```bash
python3 - <<'PY'
import sqlite3
src = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db"
dst = "$BK/agent_memory.db"   # remplacer $BK
s = sqlite3.connect(src); d = sqlite3.connect(dst)
s.backup(d); d.close(); s.close()
print("backup OK")
PY

# vérification
python3 -c "
import sqlite3
c=sqlite3.connect('file:$BK/agent_memory.db?mode=ro',uri=True)
print('integrity:', c.execute('pragma integrity_check').fetchone()[0])
print('memories :', c.execute('select count(*) from memories').fetchone()[0])
print('vec      :', c.execute('select count(*) from memories_vec_rowids').fetchone()[0])
c.close()"
```

**Attendu** : `integrity: ok`, `memories: 35926`, `vec: 35517` (± écritures concurrentes depuis la qualification).

**Note** : la base est en WAL. `sqlite3.backup()` capture un état transactionnellement cohérent — c'est ce qui rend le `.backup` fiable alors qu'une copie `cp` du seul `.sqlite` ne l'est pas.

---

## 3. Installation contrôlée

```bash
P=/home/ubuntu/cogitator-p0a-patch

# appliquer les patches (pas de copie directe : le patch échoue si le contexte a bougé)
patch -p0 --dry-run $D/conflict_resolver.py < $P/conflict_resolver.patch
patch -p0 --dry-run $D/auto_capture.py      < $P/auto_capture.patch

# si les deux dry-run disent "checking file ... " sans erreur :
patch -p0 $D/conflict_resolver.py < $P/conflict_resolver.patch
patch -p0 $D/auto_capture.py      < $P/auto_capture.patch

# vérification post-installation
sha256sum $D/conflict_resolver.py $D/auto_capture.py
```

**Attendu** : les deux SHA « APRÈS ». Si différent → **rollback immédiat (§6)**.

---

## 4. Smoke test — sans écrire dans la base active

```bash
cd $D
python3 - <<'PY'
import sys
sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search")
import conflict_resolver as cr
import auto_capture as ac
print("conflict_resolver:", cr.__file__)
print("vector_status présent:", hasattr(cr, "vector_status"))
print("_report_vector_coverage présent:", hasattr(ac, "_report_vector_coverage"))
print("vector_status() =", cr.vector_status())
PY
```

**Attendu** : les trois `True`, `{'ok': 0, 'failed': 0, 'last_error': None}`.

**Ce smoke ne fait AUCUNE insertion.** Il vérifie uniquement que les modules s'importent et exposent les nouvelles fonctions.

### Smoke optionnel en lecture seule (couverture réelle)

```bash
cd $D && python3 -c "
import auto_capture as ac
ac._report_vector_coverage('agent_memory.db', '1990-01-01 00:00:00', '1990-01-02 00:00:00', 0)
"
```

**Attendu** : `GLOBAL 0/409`, `WINDOW 0/0`, `RUN attribution : UNKNOWN`, `status : UNKNOWN`.

---

## 5. Critères GO/NO-GO après installation

| contrôle | GO | NO-GO |
|---|---|---|
| SHA post-installation | = APRÈS | ≠ |
| imports | OK | ImportError |
| `vector_status` | présent | absent |
| `_report_vector_coverage` | présent | absent |
| GLOBAL dans le rapport | `0/409` | autre valeur |
| gateway | inchangé, actif | down |

**Le gateway n'a pas besoin de redémarrer** : les scripts sont importés à chaque invocation par `auto_capture`, pas chargés au démarrage du daemon. Sauf si un process long les tient en mémoire — à vérifier par `pgrep`.

---

## 6. Rollback

```bash
# 1. restaurer les fichiers
cp -a $BK/conflict_resolver.py $D/conflict_resolver.py
cp -a $BK/auto_capture.py      $D/auto_capture.py
sha256sum $D/conflict_resolver.py $D/auto_capture.py   # = SHA AVANT

# 2. (si la base a été altérée) restaurer la copie cohérente
python3 - <<'PY'
import sqlite3
src = "$BK/agent_memory.db"  # remplacer $BK
dst = "/home/ubuntu/.openclaw/workspace/skills/memory-health/hybrid-search/agent_memory.db"
s = sqlite3.connect(src); d = sqlite3.connect(dst)
s.backup(d); d.close(); s.close()
print("rollback DB OK")
PY
```

**Règle** : le rollback restaure les **deux** fichiers Python ensemble. Jamais un seul.

---

## 7. Risques résiduels documentés

### R1 — F3b : `memories_vec` absente, extension chargée
Si la table `memories_vec` disparaît (corruption, migration ratée) alors que `sqlite_vec` est chargé, l'insertion du vecteur échoue sur `no such table` et l'erreur est **avalée** dans `except Exception`. Le fait est écrit, `vector_status()` enregistre `failed+1`, mais **rien ne remonte à l'appelant**.
**Impact** : capture silencieusement non vectorisée.
**Mitigation** : lire `vector_status()` en fin de run, ou surveiller `WINDOW_PARTIAL` dans le rapport.
**Statut** : non bloquant, cas anormal.

### R2 — Latence synchrone
`get_embedding()` est appelé **avant** la transaction, dans le même fil. Mesuré : **38 ms p50** à chaud, 94 % du temps de capture.
Sur 100 faits : ~4 s. Aucune file asynchrone n'a été introduite.
**Avantage** : l'embedding ne tient jamais le verrou d'écriture SQLite — les autres writers ne sont pas bloqués.

### R3 — Indisponibilité Ollama
`get_embedding(retries=3)` avec timeouts croissants **(30 s, 60 s, 120 s)** → jusqu'à **210 s** de blocage par fait avant échec définitif.
**Disjoncteur** : `EMBED_BREAKER_THRESHOLD = 3` (env override possible). Après 3 échecs consécutifs → `EmbeddingEndpointDown`, pipeline interrompu.
**Comportement du patch** : le fait est **quand même écrit**, sans vecteur ; `vector_status()` porte l'erreur.
**Risque** : un Ollama lent peut bloquer la capture plusieurs minutes avant que le disjoncteur ne coupe.

### R4 — Limites du reporting WINDOW / RUN UNKNOWN
- `WINDOW_COMPLETE` **ne signifie pas** que le run est complet — la fenêtre peut contenir des écritures concurrentes.
- `RUN attribution : UNKNOWN` est structurel : la base n'a **aucun marqueur de run**. `source` identifie le producteur, pas l'invocation.
- `WINDOW_PARTIAL` est le seul signal fiable d'une vectorisation incomplète.

### R5 — Pas de backfill
Les **409 faits historiques** `conflict-resolver` restent **sans vecteur**. C'est volontaire. Ils resteront invisibles à `search_vector` jusqu'à un backfill explicitement décidé.

---

## 8. Timeouts et retries réellement configurés (documentés, non modifiés)

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

**Aucune de ces valeurs n'est modifiée par P0-A.**

---

## 9. Ordre d'exécution résumé

```
1. Préflight SHA           → NO-GO si divergence
2. Sauvegarde py + SQLite  → vérifier integrity
3. dry-run patch ×2        → NO-GO si conflit
4. application patch       → vérifier SHA APRÈS
5. smoke import            → NO-GO si ImportError
6. rapport lecture seule   → GLOBAL doit être 0/409
7. surveillance 48 h       → vector_status + WINDOW_PARTIAL
```
