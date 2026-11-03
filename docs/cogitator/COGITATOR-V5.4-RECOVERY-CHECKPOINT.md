# COGITATOR V5.4 — RECOVERY CHECKPOINT

**Créé :** 2026-11-03 20:49 CET
**Motif :** remise à zéro de la session (taille base de données / contexte)
**Statut :** sauvegarde pré-reset — aucun résultat scientifique modifié

---

## 1. ÉTAT B0 — GELÉ

### Références immuables

| élément | valeur |
|---|---|
| Commit B0 du harnais | `41afac419cb0cf486d49fc57e7c8c5120417f1b0` |
| = HEAD local = `origin/main` = `sha_approved` | ✅ identique |
| Dépôt harnais | `https://github.com/MisterMiJarvis/locomo-harness.git` (`main`) |
| Moteur mémoire | `https://github.com/MisterMiJarvis/openclaw-memory-toolkit` @ `b0526bf6671fa47a3b7c4c70d85a8ed0b665804c` |
| Dataset LoCoMo | sha256 `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4` (2 805 274 octets) |

### Sceaux des 8 index B0

| conversation | sceau | tours | QA |
|---|---|---|---|
| conv-26 | `30fa94b374cfbdc9719b0844c0dbcb1a28f5572456bcaabebb0631c33de85ce5` | 419 | 199 |
| conv-30 | `2b2d957d77be…` | 369 | 105 |
| conv-41 | `d55cd7bf3d78…` | 663 | 193 |
| conv-44 | `64f69c46cced…` | 675 | 158 |
| conv-47 | `3054d5c1c3c3…` | 689 | 190 |
| conv-48 | `190c0fc5c68b…` | 681 | 239 |
| conv-49 | `7825aa85bcff…` | 509 | 196 |
| conv-50 | `7082accae375…` | — | — |

### Invariants

- B0 **GELÉ** : aucune modification du harnais, du dataset, ni des index.
- Traces retrieval (`sample-results/*.json`) **immuables**.
- Aucune conclusion tirée d'un run dont les contrôles C1–C7 n'ont pas passé.

### Interdictions permanentes

- ❌ Accès VAL (conv-42, conv-43) sans GO explicite.
- ❌ Modification de B0 ou des traces figées.
- ❌ Recréation d'un cron supprimé.
- ❌ Un `done_reason=length` n'est jamais un verdict WRONG (échec technique).

---

## 2. RÉSULTATS VÉRIFIÉS

### Couverture retrieval (279 QA DEV, 25 blocs/QA, 50 candidats)

| bras | paramètres | global 279 | cat1-4 (214) | cat5 (65) |
|---|---|---|---|---|
| **E3-NR** (référence) | reranker sans ONNX | 194C/41P/44A | 140C/38P/36A | 54C/3P/8A |
| **REF** | α=0,55 β=0,30 γ=0,15 | 196C/41P/42A | 140C/38P/36A | 56C/3P/6A |
| **GAMMA+** | α=0,45 β=0,25 γ=0,30 | 201C/40P/38A | 144C/37P/33A | 57C/3P/5A |
| **PF** | + κ=0,35 p_floor=0,40 | **203C**/41P/**35A** | **145C**/38P/**31A** | 58C/3P/4A |

### Matrices appariées vs E3-NR

| bras | gains | pertes | égalités |
|---|---|---|---|
| REF | 6 | 4 | 269 |
| GAMMA+ | 17 | **7** | 255 |
| PF | 16 | **4** | 259 |

### Multi-hop (88 QA, `len(evidence) >= 2`)

E3-NR **34** · REF **34** · GAMMA+ **38** · PF **37**

### Pilote reader 60 QA (GLM 5.3 Flash, B0 strict)

E3 **58,3 %** · E3-NR **60,0 %** — delta **+1,7 pt**
Matrice : 9 gains / 7 pertes / 44 égalités.
Couverture effective : E3 86,8 % vs E3-NR 86,0 %.
**Conclusion : E3-NR non démontré supérieur. ONNX conservé.**

### Pilote reader 20 QA (rappel — artefact d'échantillonnage)

E3 **50,0 %** · E3-NR **65,0 %** — delta +15,0 pts.
⚠️ Sur-représentait cat1/cat2/cat3, sous-représentait cat4. Le +15,0 n'est pas extrapolable.

### Les 7 pertes GAMMA+ — statut PF

| QA | preuve | rang | PF |
|---|---|---|---|
| conv-26#172 | D9:16 | 11 | ✅ récupérée |
| conv-47#17 | D8:36 | 13 | ✅ récupérée |
| conv-44#47 | D24:2 | 14 | ✅ récupérée |
| **conv-48#43** | D12:1 | 15 | ❌ **exception inexpliquée** (protégée, non récupérée) |
| conv-49#91 | D3:6 | 16 | ✅ récupérée |
| conv-26#88 | D2:14 | 21 | ❌ hors rampe (seuil rang 20) |
| **conv-41#33** | D19:3 | 24 | ❌ **régression multi-hop non résolue** |

Décompte exact : **4 récupérées + 1 protégée non récupérée + 2 hors rampe perdues**.

### Verdict PF

| métrique | GO | STOP | PF |
|---|---|---|---|
| P1 cat1-4 complètes | ≥ 149 | < 140 | **145** |
| P2 pertes vs E3-NR | ≤ 3 | > 5 | **4** |
| P3 multi-hop | ≥ 37 | < 34 | **37** |

**→ RETEST** (aucun STOP franchi, deux GO non atteints). **Retest non lancé.**

---

## 3. PARAMÈTRES, TRACES ET SHA

### Configurations exactes

```
REF    : k=25, alpha=0.55, beta=0.30, gamma=0.15
GAMMA+ : k=25, alpha=0.45, beta=0.25, gamma=0.30
PF     : k=25, alpha=0.45, beta=0.25, gamma=0.30, kappa=0.35, p_floor=0.40
Reader : glm-5.3-flash, temperature=0, seed=1, num_predict ABSENT, think ABSENT
Juge   : gpt-4o-mini, JUDGE_SYS 933c + ADVERSARIAL_SYS 293c (harness 41afac4)
```

### SHA256

```
dataset locomo10.json           79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4
experimental/proto_v2.py        331a6f0a92672507a20f70059707b45ba0273f119ecc6f6cfd7c39f3d120847e
experimental/verify_bench.py    7faf3869e993fe2a6b5205439243b9d71fd6b9d8b97a817dcab5780a2b6d745c
sample-results/E3.json          3af57dceaa75496505bc2eebc4bdcbe595074a1b603eddb898e069da952e0567
sample-results/E3-NR.json       a1ad373f39641aec083cc560a1946035049aaadf0728574cdb672995c79a5094
pilot-reader/pilot60-results.json  18481379880399fb5c27b7fd83cb8dc6561c914a2c64f31cf6b198a04c26f8db
experimental/pf-results.json    3a90ee5ead81c3e5d36f9b70148f0cb8d3793a065745321a357b01c1379bd206
pilot-reader/pilot60-ids.json   650606279892fee9b99d349958626cd4307b0fe8347c7d4d430de51a4433c626
```

### Échantillons figés

- `dev-sample-300.json` — 279 QA DEV, SHA `511ba3d7…`
- `pilot60-ids.json` — 60 QA complémentaires, seed `20261103`, stratification proportionnelle
- `pilot20-ids.json` — 20 QA, seed `20261102`

### Documents locaux

```
/home/ubuntu/cogitator-audit/PILOTE-E3-VS-E3NR-20QA.md
/home/ubuntu/cogitator-audit/PILOTE-60QA-E3-VS-E3NR.md
/home/ubuntu/cogitator-audit/PROTOCOLE-60QA-E3-VS-E3NR.md
/home/ubuntu/cogitator-audit/SPEC-VARIANTE-PROTEGEE-PF.md
/home/ubuntu/cogitator-audit/PF-RESULTS-2026-11-03.md
/home/ubuntu/cogitator-audit/INCIDENT-2026-11-02-BUG-SELECT.md
```

---

## 4. INCIDENTS CONNUS DU BANC

### Incidents documentés

1. **2026-11-02 22:58 — Bug d'appel positionnel**
   `select(q, cands, 0.55, 0.30, 0.15)` → `0.55` lié à `k` → sélections vides.
   Symptôme : REF et GAMMA+ à 0/88 multi-hop alors que 196 et 201 couvertures globales.
   **Résultats invalidés** : seconde exécution du 22:58 uniquement.
   **Correction** : paramètres tous keyword-only + validation stricte.

2. **2026-11-02 (préflight) — Erreur multi-hop**
   Annoncé 17/20, réel **7/20**. Cause : `len()` sur un fallback chaîne.
   Correction : vérification du schéma (`evidence` est une `list` dans 20/20).

3. **2026-11-02 (rapport 20 QA) — Erreur de présentation**
   Cat5 présenté comme surreprésenté (20 % vs 23,3 %) alors que c'est une **sous**-représentation.
   Correction documentée.

4. **2026-11-03 — Mesure de redondance invalide**
   Confondre numéro de dialogue `Dn` avec unité de contenu.
   Vérifié : **0 groupe sur 486** contient un texte identique → complémentarité réelle 100 %.
   Hypothèse « redondance » **retirée**.

5. **2026-11-03 08:32 — Panne gateway**
   `SQLite worker stopped: disk I/O error` → `Agent database execution admission is closed`.
   Causes aggravantes : base agent 718 Mo + 911 Mo de résidus `.bak`, boucle de retry workboard.
   Résolution : résidus déplacés en corbeille (911 Mo), `doctor --fix`, gateway stable.

### Contrôles obligatoires C1–C7

| # | contrôle | statut |
|---|---|---|
| C1 | signature keyword-only + 4 tests négatifs | PASS |
| C2 | taille 25 blocs, 0 doublon | PASS |
| C3 | provenance (blocs ⊆ pool de candidats) | PASS |
| C4 | transitions C/P/A recalculées par algorithme naïf distinct | PASS |
| C5 | cohérence tableaux ↔ agrégats | PASS |
| C6 | absence de fuite gold (AST + test différentiel par injection) | PASS |
| C7 | invariance de la rampe PF (seuil exact au rang 20) | PASS |

**Règle : aucun résultat n'est publié si un contrôle C1–C7 échoue.**

---

## 5. DÉCISIONS ACTUELLES

| décision | statut |
|---|---|
| **RETEST κ** | **SUSPENDU** — aucun ajustement de κ autorisé |
| **Gate reader** | **NON AUTORISÉ** — aucun appel reader/juge |
| **GR0 HippoRAG/GraphRAG** | **AUDIT HORS LIGNE UNIQUEMENT** |
| **H1–H4** | **NON AUTORISÉS** — ni construction ni exécution |
| **PF** | Conservé comme **candidat expérimental**, non retenu comme configuration |
| **E3-NR** | **Référence** conservée |
| **ONNX** | **NON SUPPRIMÉ** — conservé comme référence |
| **REF** | Candidat conservateur |
| **GAMMA+** | Candidat exploratoire prometteur mais **non validé** |

---

## 6. VAL conv-42/43 — SCELLÉ

**conv-42 (260 QA) et conv-43 (242 QA) constituent l'ensemble VAL scellé.**

| interdiction | statut |
|---|---|
| Indexation de conv-42/43 | ❌ **INTERDIT** |
| Génération de traces retrieval sur ces conversations | ❌ **INTERDIT** |
| Examen de leurs questions, preuves ou performances | ❌ **INTERDIT** |
| Utilisation pour choisir des paramètres | ❌ **INTERDIT** |
| Préservation pour le GO VAL final | ✅ **OBLIGATOIRE** |

**Constat vérifié** : conv-42/43 sont disjointes des 279 QA DEV (chevauchement 0), **mais cela ne les rend pas utilisables**. Elles sont réservées à la validation finale, après gel complet de la configuration et GO explicite.

**Aucune trace retrieval n'existe pour conv-42/43** — vérifié : 0 QA dans `sample-results/E3.json` et `E3-NR.json`.

---

## 7. AUDIT GR0 — PARTIEL

### Ce qui est vérifié

**Graphe d'ontologie** : `/home/ubuntu/.openclaw/workspace/memory/ontology/graph.jsonl`

| mesure | valeur |
|---|---|
| Entités | **1 150** |
| Types | **21** |
| Lignes | 1 150 (`op: state` 1 100, `op: upsert` 50) |
| Opérations `relate` | **0** |

### Répartition par type

| type | n | propriétés principales |
|---|---|---|
| TimelineEvent | **679** | category, date, description, **source 649/679**, valid_from/to 178 |
| Episode | 250 | name, created_by, day, role, decision_count |
| Decision | 109 | date, description, **source 107/109**, valid_from/to 46 |
| Project | 24 | name, status, scope, timeline |
| Skill | 15 | name, status, cron, features |
| Concept | 15 | name, auto_detected, detection_date |
| Document | 13 | name, audience, drive_path, purpose |
| Person | 9 | name, role, reports_to |
| autres (Service, Tool, Device, Infrastructure, SecurityEvent, Event, Integration, Pet, BugFix, Configuration, Location, Feature, Software) | 36 | — |

### Constat central

**Ce n'est pas un graphe navigable : c'est un catalogue d'entités isolées.**
Aucune arête. Aucune relation. HippoRAG/GraphRAG suppose un graphe parcourable — il n'y a rien à parcourir ici.

### Provenance

- 649/679 TimelineEvent portent `source` → **30 non sourcés**
- 107/109 Decision portent `source` → **2 non sourcés**
- **Risque identifié** : ces ~32 entités sans provenance sont des candidats à des liens non vérifiables.

### Ce qui reste à vérifier (NON FAIT)

- ⬜ Existence de relations dans **d'autres fichiers** (`memory/ontology/graph-index.json`, `ontology.html`)
- ⬜ Existence de relations dans **d'autres tables** du pipeline
- ⬜ **`hybrid-search/graph_search.py`** dans `.work/mh-v213/` — composant de recherche graphe
- ⬜ `ontology_backfill_llm.py`, `ontology_linker.py` — producteurs de relations potentielles
- ⬜ Capacité actuelle à retrouver une chaîne de preuves inter-tours ou inter-conversations
- ⬜ Compatibilité avec le pipeline retrieval actuel

**Ne pas conclure que tout Cogitator est dépourvu de graphe avant ces vérifications.**

---

## 8. TROIS INSTRUCTIONS NON TRAITÉES

### Instruction A — Finalisation du protocole reader

**À produire** : protocole complet, non exécuté, distinguant trois niveaux.

| niveau | description | disponibilité |
|---|---|---|
| **A** | Confirmation exploratoire sur DEV déjà exploité (279 QA) | ✅ disponible — **non indépendant** |
| **B** | Évaluation sur DEV réellement disjoint et autorisé | ❌ **N'EXISTE PAS** |
| **C** | Validation finale sur VAL scellé (conv-42/43) | ⏸ réservé au GO VAL final |

**Constat vérifié** : le dataset LoCoMo contient 10 conversations. 8 sont dans le DEV (conv-26, 30, 41, 44, 47, 48, 49, 50). Les 2 restantes (conv-42, 43) sont VAL.

**→ Il n'existe aucun échantillon DEV indépendant disponible.**

Le protocole doit le dire explicitement et **ne pas inventer d'indépendance**. Le seul test réellement indépendant passera par VAL, après gel complet.

### Instruction B — Finalisation de GR0

Voir section 7. Les six vérifications restantes doivent être complétées, notamment `graph_search.py` et les scripts `ontology_*`.

### Instruction C — Protection explicite de VAL

Voir section 6. Doit figurer dans tout protocole et toute documentation future.

---

## 9. ARCHITECTURE OBLIGATOIRE

**Cogitator est un compagnon de `memory-core`, jamais un remplaçant.**

- `memory-core` reste le système de mémoire de l'agent.
- Cogitator apporte la couche d'évaluation retrieval + reader sur corpus LoCoMo.
- Aucune migration de la mémoire de l'agent vers Cogitator.
- Aucune dépendance de `memory-core` vers Cogitator.

---

## 10. PROCHAINES ACTIONS

### Autorisées sans nouveau GO

- Lecture et analyse des traces figées existantes (hors VAL)
- Audit statique des composants du dépôt
- Production documentaire (rapports, protocoles, specs)
- Synchronisation Drive (Suivi V5.4, Roadmap V5.4) sans écraser l'historique
- Exécution de `verify_bench.py` sur les artefacts existants

### Nécessitent un GO explicite

- ❌ **Tout appel LLM** (reader, juge)
- ❌ **Toute indexation** de conv-42/43 (VAL)
- ❌ **Toute génération de traces** sur VAL
- ❌ **RETEST κ**
- ❌ **Construction ou exécution H1–H4**
- ❌ **Modification de B0** ou des traces figées
- ❌ **Suppression d'une base, d'un index, d'une trace ou d'une archive scientifique**
- ❌ **Changement de configuration du gateway**

---

## VÉRIFICATION DE CE CHECKPOINT

- [x] Fichier créé dans `/home/ubuntu/cogitator-audit/`
- [x] Contenu issu de fichiers lus, non de mémoire
- [x] SHA recalculés à l'instant (2026-11-03 20:49 CET)
- [x] Aucune base, index, trace ou archive supprimée
- [x] Aucune expérience lancée
- [ ] Versionné (git) — à confirmer
- [ ] Synchronisé sur Drive — à confirmer

---

*Fin du checkpoint.*
