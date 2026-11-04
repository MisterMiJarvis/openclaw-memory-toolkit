# SYN-01 — VALIDATION DU PATCH P0

**Date :** 2026-11-04 13:18 CET
**Branche :** `syn01-p0-patch`
**Commits :** `feed3f8` (P0) → `77c8445` (correctif P1)
**Référence intacte :** `cogitator-go-d-fix` @ `70c22f1`
**Statut :** aucun appel modèle, aucune inférence, aucune base créée, aucun corpus ingéré

---

## 1. Intégrité — PASS

| contrôle | valeur | verdict |
|---|---|---|
| SHA local HEAD | `77c8445bc994482aaf544534f1408e4f2117e355` | — |
| SHA origin `syn01-p0-patch` | `77c8445bc994482aaf544534f1408e4f2117e355` | **identique** ✅ |
| `cogitator-go-d-fix` local | `70c22f103819f7416852a760a89512e0cc4e8a2d` | intacte ✅ |
| `cogitator-go-d-fix` origin | `70c22f103819f7416852a760a89512e0cc4e8a2d` | intacte ✅ |
| Working tree | propre | ✅ |

### 1.1 Diff audité `70c22f1..77c8445`

```
hybrid-search/auto_capture.py          |  42 +++++++++++-
hybrid-search/conflict_resolver.py     |  35 +++++++++-
hybrid-search/model_guard.py           | 112 ++++++++++++++++++++++++++++
hybrid-search/preflight_syn01.py       |  43 +++++++++++
hybrid-search/test_syn01_p0_guard.py   | 158 +++++++++++++++++++++++++++++++++++
hybrid-search/test_syn01_p0_payload.py | 102 ++++++++++++++++++++++++++++++
6 files changed, 490 insertions(+), 2 deletions(-)
```

**Périmètre : les 6 fichiers sont tous attendus.** Aucun changement hors périmètre. Aucun
fichier de production, de gateway, de skill, de corpus ou de base.

---

## 2. Matrice de conformité des garde-fous — PASS

| # | refus exigé | test | résultat |
|---|---|---|---|
| G1 | modèle `:cloud` | `deepseek-v4.1-flash:cloud`, `glm-5.2:cloud` | ✅ refusé |
| G2 | `:cloudy` | `x:cloudy`, `MODEL:Cloudy` | ✅ refusé |
| G3 | `:clouds` | `x:clouds`, `x:clouds-2` | ✅ refusé |
| G4 | `:cloud2` | `x:cloud2` | ✅ refusé |
| G5 | casse | `:CLOUD`, `MODEL:Cloudy` | ✅ refusé |
| G6 | séparateurs | `x-cloud`, `x_cloud`, `x/cloud` | ✅ refusé |
| G7 | espaces | `"  x:cloud  "` | ✅ refusé |
| G8 | modèle absent du daemon | `llama-99b` | ✅ refusé |
| G9 | dimension 3584 | `qwen2.5:7b` natif | ✅ refusé |
| G10 | dimension 1024 | `mxbai` | ✅ refusé |
| G11 | dimension négative | `-1` | ✅ refusé |
| G12 | endpoint non-loopback | `10.0.0.5`, `8.8.8.8`, `ollama.local`, `0.0.0.0` | ✅ refusé |
| G13 | variable absente | `SYN01_TEST_VAR` non posée | ✅ refusé |
| G14 | variable cloud | valeur `:cloud` | ✅ refusé |
| G15 | faux positifs locaux | `qwen2.5:7b`, `nomic-embed-text[:latest]`, `mxbai`, `llama3.2:3b`, `mistral:7b-instruct` | ✅ acceptés |

**15 contrôles, 15 PASS.**

### 2.1 Rejet avant tout appel HTTP

Test par capture de `urlopen` :

```
x:cloudy                  -> refusé | appel HTTP: NON
x:clouds                  -> refusé | appel HTTP: NON
x:cloud2                  -> refusé | appel HTTP: NON
deepseek-v4.1-flash:cloud -> refusé | appel HTTP: NON
```

**Le rejet intervient avant que la requête ne soit construite.**

### 2.2 Indépendance des deux verrous

| scénario | verrou de nom | verrou de service |
|---|---|---|
| `x:cloudy`, non servi | refusé | refusé |
| `x:cloudy`, **fictivement servi** | **refusé seul** | passerait |
| `llama-99b`, non servi | accepté | **refusé seul** |

Chaque verrou refuse **seul**, sans dépendre de l'autre. Un modèle cloud réellement
installé serait bloqué par le nom ; un modèle inconnu serait bloqué par le service.

---

## 3. Preuves des payloads — PASS

Capture des payloads réellement construits, sans appel réseau :

### 3.1 Extraction (`auto_capture.extract_candidate_facts`)

```json
{
  "model": "qwen2.5:7b",
  "format": "json",
  "stream": false,
  "options": {"temperature": 0.0, "seed": 1, "num_predict": 512, "num_ctx": 4096}
}
```

### 3.2 Arbitrage (`conflict_resolver.classify_relation`)

```json
{
  "model": "qwen2.5:7b",
  "stream": false,
  "format": "json",
  "options": {"temperature": 0.0, "seed": 1, "num_predict": 512, "num_ctx": 4096}
}
```

### 3.3 `use_llm=False`

Capture vide : **zéro `urlopen`**. L'heuristique lexicale est utilisée.

### 3.4 Embedding

`EMBED_MODEL = "nomic-embed-text"` **codé en dur** — aucune variable ne peut le changer.

### 3.5 `num_ctx = 4096` — vérification statique du budget

| élément | tokens |
|---|---|
| prompt système | ~400 |
| tour max (corpus, 196 car.) | ~65 |
| sortie (`num_predict`) | 512 |
| marge | 1 024 |
| **besoin** | **~2 001** |
| `num_ctx` | **4 096** |
| marge restante | 2 095 (**2,0×**) |
| capacité du modèle | 32 768 |

**Dépassement : NON.** Un tour de plus de **6 480 caractères** franchirait le budget —
aucun tour du corpus n'en approche (max 196). **Limite documentée : cette valeur est
dimensionnée pour SYN-01 uniquement.** Un corpus avec des tours longs devrait être
recontrôlé avant tout run.

---

## 4. Tests et codes retour — PASS

| suite | nombre | résultat | rc |
|---|---|---|---|
| `test_syn01_p0_guard.py` | **32** | 32 pass, 0 fail | 0 |
| `test_syn01_p0_payload.py` | **5** | 5 pass, 0 fail | 0 |
| `test_loopback_guard.py` | — | PASS | 0 |
| `test_model_resolution.py` | — | PASS | 0 |
| `test_extract_atomic.py` | — | PASS | 0 |
| `test_meta_gate.py` | — | PASS | 0 |
| `test_orphan_link.py` | — | PASS | 0 |
| `test_lifecycle.py` | — | PASS | 0 |
| `test_provenance.py` | — | PASS | 0 |
| `test_graph_search.py` | — | **22 pass / 2 fail — PRÉEXISTANTS** | 1 |

**Total patch : 37 PASS · 0 FAIL · 0 SKIP.**

### 4.1 Décompte exact

- **17 tests de garde-fou** (version initiale `feed3f8`) : refus cloud, loopback, modèle
  non servi, dimension, variable
- **+15 tests ajoutés** (`77c8445`) : 9 contournements + 6 faux positifs locaux
- **= 32 tests de garde-fou**
- **5 tests de payload** : extraction, refus avant réseau, `use_llm=False`, arbitrage,
  embedding
- **= 37 au total**

### 4.2 Statut des 7 suites de non-régression

Toutes vertes **avant et après** les deux commits. Aucune régression introduite.

### 4.3 Signification exacte de « 22/2 » sur `test_graph_search`

**22 tests passent, 2 échouent.** Ces 2 échecs existaient **avant** le premier commit du
patch (`70c22f1`), vérifiés par `git stash` lors de GO-B. Ils concernent le GraphRAG,
**hors périmètre** de SYN-01. **Ils ne sont pas présentés comme de nouveaux échecs.**

---

## 5. Le contournement découvert

### 5.1 Ce que le contre-audit a trouvé

Le pattern initial `:cloud\b` refusait `:cloud` mais **acceptait** `:cloudy`, `:clouds`,
`:cloud2`, `:clouds-2` — `\b` exige une frontière de mot que ces noms n'ont pas.

Un modèle enregistré `model:cloudy` aurait passé le verrou de nom tout en restant une
inférence distante proxifiée.

### 5.2 Pourquoi ce n'était pas exploitable en l'état

Le second verrou (`assert_served_locally`) refusait `x:cloudy` car ce modèle n'est pas
servi localement. **Deux verrous indépendants, un trou couvert par l'autre.**

### 5.3 Mais c'était une faute de conception

Compter sur un second verrou pour rattraper un premier trou n'est pas une garantie, c'est
une chance. Corrigé.

### 5.4 La correction

```python
# avant
CLOUD_SUFFIX = re.compile(r":cloud\b", re.I)
# après
CLOUD_MARKER = re.compile(r"(?:^|[:/_-])cloud", re.I)
```

**Politique documentée dans le module :** un nom est refusé quand `cloud` commence un tag
ou un token délimité par `: / _ -`, insensible à la casse. Un faux positif coûte un
renommage ; un faux négatif envoie le prompt hors de la machine.

---

## 6. Risques résiduels

| # | risque | preuve | impact | reproductible | correction | bloquant C1 ? |
|---|---|---|---|---|---|---|
| **P0** | **aucun** | — | — | — | — | — |
| **P1** | **aucun** | contournement fermé et testé | — | — | — | — |
| **P2-1** | `num_ctx=4096` dimensionné pour SYN-01 seul | §3.5 : besoin 2 001, un tour > 6 480 car. dépasserait | troncature d'entrée sur corpus à tours longs | oui (calcul statique) | recontrôler le budget avant tout nouveau corpus | **non** |
| **P2-2** | `test_graph_search` 2 échecs | préexistants depuis GO-B, hors périmètre | aucun sur SYN-01 | oui | chantier GraphRAG séparé | **non** |
| **P2-3** | `model_guard` est un module **nouveau**, pas encore couvert par un test d'intégration bout-en-bout | tests unitaires seulement | un refactor futur pourrait l'oublier | non | test d'intégration à ajouter | **non** |

---

## 7. Conditions préalables à l'autorisation C1

1. **Les trois variables d'environnement doivent être posées** au moment du run :
   `AUTO_CAPTURE_MODEL=qwen2.5:7b`, `CONFLICT_LLM_MODEL=qwen2.5:7b`,
   `OLLAMA_URL=http://127.0.0.1:11434`
2. **`preflight_syn01.py` doit retourner rc=0** avant toute ingestion
3. **`num_ctx=4096` doit être recontrôlé** si le corpus change
4. **Aucune modification de `cogitator-go-d-fix`** — la référence reste `70c22f1`

---

## 8. Ce que ce patch ne prouve pas

- Il ne prouve **pas** que SYN-01 produira de bons résultats — la sécurité et la
  reproductibilité sont validées, pas la qualité d'extraction.
- Il ne prouve **pas** que le garde-fou résistera à un refactor futur — aucun test
  d'intégration bout-en-bout n'existe encore (P2-3).
- Il ne mesure **rien** sur le corpus. Aucune base n'a été créée.

---

## 9. Décision

# ✅ PASS — READY FOR C1 AUTHORIZATION

**Tous les critères bloquants sont satisfaits.**

| critère | verdict |
|---|---|
| Intégrité | **PASS** |
| Garde-fous | **PASS** (15/15 contrôles, 32/32 tests) |
| Payloads | **PASS** (5/5, paramètres capturés) |
| Non-régression | **PASS** (7 suites vertes, 2 échecs préexistants documentés) |
| Risques P0 | **0** |
| Risques P1 | **0** |
| Risques P2 | **3**, tous non bloquants |

**Un PASS n'est jamais une autorisation d'exécuter.** C1 reste `BLOCKED — NOT RUN`
jusqu'à un GO explicite.

---

## 10. Restrictions respectées

- ✅ aucun appel modèle, aucune inférence
- ✅ aucune ingestion de corpus
- ✅ aucune base créée
- ✅ aucune modification de production ou de gateway
- ✅ `cogitator-go-d-fix` intacte (`70c22f1` local et origin)
- ✅ aucune modification de skill par mes soins
- ✅ aucun merge

**`sqlite-data-layer-hardening` n'interfère pas avec SYN-01** : `mtime` du 2026-10-27,
aucune dépendance de `model_guard` vers cette skill, aucun test croisé.

---

*Fin du rapport. C1 : BLOCKED — NOT RUN.*
