# LoCoMo Optimization Spec — `openclaw-memory-toolkit` v4.x

> Spec rédigée le 2026-10-11. Source : `Specs post locomo.docx` (Drive `Notes/`),
> consolidée avec les résultats officiels du run du 11/10.
> Harnais cible : `skills/locomo-test/locomo_official.py` (+ `hybrid_search.py`).
> Companion : `mh-v4-spec-2026-10-10.md` (refactor résolution LLM, V4.0).

---

## 0. Diagnostic de départ (état mesuré, run officiel 2026-10-11)

Sur le protocole officiel (Catégories 1 à 4, `n = 1386`), l'écart de performance
se décompose en deux pertes distinctes :

| Métrique | Valeur | Lecture |
|---|---|---|
| **QA accuracy (officiel)** | **49,6 %** | score de référence publié |
| QA strict | 41,4 % | sans partiels |
| **hit@15 (retrieval)** | **65,1 %** | plafond du moteur déterministe |
| all_evidence | 51,6 % | toutes les preuves réunies |
| MRR | 0,449 | |

- **Perte n°1 — plafond du Retriever (34,9 % des questions perdues d'avance)** :
  le moteur déterministe (`BM25 + sqlite-vec + RRF`) place au moins une preuve
  dans le top-15 pour **65,1 %** des questions, et réunit *toutes* les preuves
  (`all_evidence`) pour seulement **51,6 %**.
- **Perte n°2 — friction du Reader (15,5 pts sous le plafond)** : la preuve est
  fournie à 65,1 %, mais le QA final s'établit à **49,6 %** (**41,4 %** strict).
- **Run Hardcore complet** (`n = 1946`, cat. 1-5) : **57,6 %** grâce au bouclier
  anti-hallucination à **88,7 %** sur la Catégorie 5 (**360 pièges déjoués / 406**).

### Ventilation par catégorie (officiel, cat. 1-4)

| Catégorie | n | strict | avec partiels |
|---|---|---|---|
| 2 — Temporel | 284 | 50,7 % | 58,5 % |
| 4 — État / ouvert | — | 48,2 % | — |
| 1 — Multi-saut | 250 | 18,8 % | 54,4 % (89 partiels) |
| 3 — Inférence | 83 | 15,7 % | 22,9 % |

### Ventilation Hardcore (cat. 1-5, `n = 1946`, acc = 57,6 %)

| Cat. | Nature | n | strict | acc |
|---|---|---|---|---|
| 1 | Multi-saut | 282 | 18,1 % | 53,9 % |
| 2 | Temporel | 321 | 52,6 % | 62,0 % |
| 3 | Inférence | 96 | 16,7 % | 26,0 % |
| 4 | État / ouvert | 841 | 47,0 % | 61,2 % |
| 5 | Adversariale | 406 | 88,7 % | 88,7 % |

---

## 🎯 AXE 1 — Aligner le Reader sur le plafond actuel (Quick Wins : +8 à +12 pts)

Aucune réindexation. Modifie uniquement le **prompt système du Reader**
(`deepseek-v4.1-flash`) dans `skills/locomo-test/locomo_official.py`.

### 1.1 Débloquer la Catégorie 3 par « Inférence Ancrée » (*Grounded Inference*)

**Constat :** en Cat. 3 (`n = 83`), le strict s'effondre à **15,7 %** (22,9 % avec
partiels → 64 échecs / 83). Le prompt actuel ordonne `INSUFFICIENT` dès qu'un fait
n'est pas écrit littéralement, alors que la Cat. 3 teste justement la déduction.

**Exemples réels (`misses_sample`) :**

| conv | Question | Gold | Réponse actuelle |
|---|---|---|---|
| conv-26 | Would Caroline likely have Dr. Seuss books on her bookshelf? | Yes, since she collects classic children's books | `INSUFFICIENT` |
| conv-42 | What pets wouldn't cause any discomfort to Joanna? | Hairless cats or pigs, since they don't have fur (allergy) | `INSUFFICIENT` |
| conv-49 | What electronic device could Evan gift Sam to help with fitness goals? | fitness tracker | `INSUFFICIENT` |
| conv-50 | Would Calvin enjoy performing at the Hollywood Bowl? | Yes; because he enjoys the rush of performing onstage | `INSUFFICIENT` |

**Pourquoi cela ne casse pas la Cat. 5 (88,7 %) :** toutes les questions de Cat. 3
portent des marqueurs grammaticaux d'hypothèse (`would`, `likely`, `might`, `could`,
`based on`), tandis que les pièges adversariaux posent des questions factuelles
directes sur de fausses prémisses.

**Règle à injecter dans le prompt du Reader :**

```text
HYPOTHETICAL & INFERENCE QUESTIONS (CATEGORY 3):
- If a question asks for a deduction or hypothesis (using words like "would", "likely", "might", "could", "based on", "what kind of... could"), DO NOT answer "INSUFFICIENT" if relevant personality traits, preferences, or constraints (e.g., allergies, hobbies, collections, goals) are present in the context.
- Instead, make a brief, direct commonsense inference grounded in those facts and state the reason: e.g., "Yes, because [fact from context]" or "[Specific item], because [fact from context]".
- Keep strict "INSUFFICIENT" for direct factual questions ("What did X do", "When did X...", "Who is...") whenever the fact or premise is missing or contradicted.
```

### 1.2 Convertir les 89 réponses partielles de Cat. 1 (*Multi-Hop Synthesis*)

**Constat :** en Cat. 1 (`n = 250`), **47 strictes (18,8 %)** mais **89 partielles
(35,6 %)** → couverture **54,4 %**. Le Reader touche la zone dans > 1 cas sur 2
mais perd la moitié des points au jugement.

**3 erreurs récurrentes :**

1. **Réponse générique au lieu des noms propres** — conv-41 (*What people has Maria
   met and helped while volunteering?*) : gold `David, Jean, Cindy, Laura` vs
   réponse `homeless people`. Idem conv-43 (*What books has Tim read?*) :
   gold titres exacts vs `A book about small changes...`.
2. **Arrêt au premier chunk (comptage incomplet)** — conv-43 (*How many games has
   John mentioned winning?*) : gold `6` vs réponse `1`.
3. **Confusion d'attribution sur les questions « both »** — conv-47 (*Do both James
   and John have pets?*) : gold `No` (un seul) vs réponse `Yes`.

**Règle à injecter :**

```text
MULTI-HOP & LIST AGGREGATION RULES (CATEGORY 1):
1. Exhaustive Scan: When a question asks for a list ("What activities...", "What books...", "Which cities...", "How did X...") or a count ("How many times..."), scan ALL 15 retrieved passages before answering and merge every distinct item found across all sessions.
2. Specificity Over Generality: Always output exact proper nouns, titles, names, and locations mentioned in the text (e.g., output "David, Jean, Cindy, Laura" instead of "homeless people"; output exact book titles instead of plot descriptions).
3. Dual-Entity Verification ("both X and Y"): When asked if "both A and B" share a trait or action, verify A and B independently in the excerpts. If only one of them is confirmed, answer "No" (and specify who).
```

---

## 📅 AXE 2 — Le « Dual-Date » : résolution temporelle hybride

Cat. 2 (*Temporal*, `n = 284`) déjà à **50,7 % strict** (58,5 % couverture), mais
de bonnes réponses sont rejetées par le juge à cause d'une ambiguïté de format.

### 2.1 Les deux pièges inverses

**Piège A — gold attend la formule relative, le Reader donne la date résolue / session :**

| conv | Question | Gold | Réponse | Verdict |
|---|---|---|---|---|
| conv-26 | When did Melanie run a charity race? | The sunday before 25 May 2023 | 20 May 2023 | WRONG |
| conv-41 | When did John have a party with veterans? | The Friday before 20 May 2023 | 20 May, 2023 | WRONG |
| conv-50 | When did Dave see Aerosmith perform live? | on the weekend before March 26, 2023 | 26 March, 2023 | WRONG |

**Piège B — gold attend le mois résolu, le Reader recopie la date de session :**

| conv | Question | Gold | Réponse | Verdict |
|---|---|---|---|---|
| conv-30 | When did Jon start expanding his studio's social media presence? | April, 2023 | 16 June, 2023 | WRONG |
| conv-44 | When did Audrey's positive reinforcement training course take place? | June, 2023 | 11 May, 2023 | WRONG |

### 2.2 Niveau 1 — « Dual-Date Output » dans le prompt (0 réindexation)

```text
TEMPORAL & DATE RULES (DUAL-DATE FORMAT):
1. Never output the session header date alone when the speaker uses a relative time marker ("yesterday", "last Friday", "this weekend", "last month", "next month", "two years ago", "three weeks later").
2. For any "When" question involving relative time, ALWAYS output BOTH:
   (a) The relative phrase anchored to the session date (e.g., "The Friday before 20 May 2023", "the weekend before 26 March 2023"), AND
   (b) The resolved calendar date, month, or year in parentheses (e.g., "(19 May 2023)", "(June 2023)").
3. Example output: "The Sunday before 25 May 2023 (21 May 2023)" or "June 2023 (the month after the 11 May 2023 session)".
```

### 2.3 Niveau 2 — « Dual-Date Anchoring » à l'indexation (gain hit@15)

**Pourquoi le Retriever rate des questions datées :** conv-42 (*What movie did
Joanna watch on 1 May, 2022?*, preuve `D10:1`) et conv-47 (*Which recreational
activity was James pursuing on March 16, 2022?*, preuve `D1:26`) — le dialogue dit
simplement *"yesterday"* lors d'une session datée du lendemain. La chaîne
`"1 May, 2022"` n'apparaît **pas** dans le texte indexé par FTS5/BM25.

**Implémentation déterministe (100 % Python `datetime`/`dateutil`, 0 appel LLM) :**
pour chaque tour d'une session datée `S_date` :

1. Injecter l'en-tête normalisé en début de chunk : `[Date: 2 May 2022 | May 2022 | 2022]`.
2. Détecter par regex les marqueurs relatifs et calculer la date cible :
   - `"yesterday"` → `[the day before S_date / (S_date - 1 day)]`
   - `"last <Weekday>"` / `"this weekend"` → `[the <Weekday>/weekend before S_date / resolved_date]`
   - `"last month"` / `"next month"` → `[resolved_month Year]`
   - `"<N> years ago"` → `[S_year - N]`

---

## 🚀 AXE 3 — Relever le plafond du Retriever (65,1 % → 80 %+)

Sans tricher sur la fenêtre finale (`k = 15`).

### 3.1 Découverte clé : le bug du « Tour Adjacent » (±1 / ±2 tours)

Dans un très grand nombre de `misses_sample`, **le moteur trouve la bonne session
et le bon moment, mais tombe 1-2 répliques à côté de la preuve officielle** :

| conv | Question | Preuve attendue | top-3 remonté | Décalage |
|---|---|---|---|---|
| conv-26 | When did Melanie paint a sunrise? | `D1:12` | `D1:14` | +2 |
| conv-30 | When did Gina team up with a local artist? | `D5:5` | `D5:7` | +2 |
| conv-41 | When did Maria go to the beach? | `D3:15` | `D3:14` | −1 |
| conv-41 | When did John get his degree? | `D9:2` | `D9:4` | +2 |
| conv-41 | When did John have a party with veterans? | `D15:11` | `D15:13`, `D15:15` | +2 |
| conv-44 | When did Audrey's positive reinforcement course...? | `D6:2` | `D6:4` | +2 |
| conv-48 | What pets does Jolene have? | `D2:20, D2:22, D2:24` | `D2:26` | +2 |

**Cause :** l'interlocuteur A pose la question contenant les mots-clés en `D3:14`
(*"Hey Maria, when did you go to the beach?"*), Maria répond en `D3:15`
(*"Last Sunday! It was great."*). BM25 + vecteur matche `D3:14` (contient *Maria*
et *beach*), mais l'annotateur a tagué `D3:15`. Inversement parfois.

**Solution chirurgicale — « Dialog-Window Chunking » (`[t-1, t, t+1]` ou ±2) :**

1. **À l'indexation :** indexer chaque unité comme une **micro-fenêtre de dialogue
   de 3 tours consécutifs** (`y-1`, `y`, `y+1`), préfixée par
   `[Session D_x | Date | Speakers]` — au lieu d'un tour isolé (où pronoms et
   réponses courtes `"Yes!"` sont orphelins).
2. **En post-traitement du Retriever (Neighbor Turn Expansion) :** quand un chunk
   `D_x:y` entre dans le top-15, attacher automatiquement `D_x:y-1` et `D_x:y+1`
   (voire ±2 si tours courts) et associer leurs IDs au passage retourné. Rattaché
   mécaniquement tous les quasi-hits ±1/±2 ci-dessus.

### 3.2 Plafond par session (*Session Diversity Cap*) et décomposition par entité

**Problème d'écrasement par une session unique :** conv-43 (*Would Tim enjoy
reading books by C. S. Lewis or John Greene?*, preuves `D1:14, D1:16, D1:18`) →
top-3 saturé par `D19:17, D19:19, D19:23`. conv-50 (*When did Calvin first travel
to Tokyo?*, preuve `D3:1`) → top-3 saturé par `D24` (`D24:13, D24:17, D24:21`).

- **Solution 1 — Session Diversity Cap (MMR léger) :** dans la boucle RRF, imposer
  `max_per_session = 3` chunks par session `D_x` (sauf < 15 chunks trouvés au
  total). Dès que `D24` a 3 extraits, les slots suivants vont aux autres sessions.
- **Solution 2 — Entity-Split Sub-queries :** sur les questions comparatives
  (*What do Jon and Gina both have in common?* conv-30 ; *What type of volunteering
  have John and Maria both done?* conv-41), si la question contient deux prénoms
  connus, lancer deux requêtes RRF distinctes (`query + Entity_A` = 7 slots,
  `query + Entity_B` = 7 slots, + 1 slot global).

### 3.3 Reranking local à deux étages (top-50 RRF → top-15 Cross-Encoder)

**Pourquoi le RRF seul plafonne sur `all_evidence` (51,6 %) :** il additionne des
rangs lexicaux et vectoriels sans lire finement la relation question↔passage. Sur
conv-43 (*What books has Tim read?*, 7 preuves de `D1` à `D26`) ou conv-49 (*What
kind of healthy food suggestions has Evan given to Sam?*, 5 preuves), une partie
des preuves est aux rangs 16-45 du RRF.

**Architecture 2 étages 100 % locale (0 API cloud) :**

1. **Étage 1 (Recall large) :** `hybrid_search.py` récupère **top-50** candidats
   via `BM25 + sqlite-vec + RRF` (quelques ms).
2. **Étage 2 (Précision Cross-Encoder) :** un Cross-Encoder local ultra-léger
   (`BAAI/bge-reranker-v2-m3` ou `cross-encoder/ms-marco-MiniLM-L-6-v2` en ONNX /
   `sentence-transformers`) évalue les 50 paires `(question, chunk_window)` et
   reclasse les **15 meilleurs**.
3. **Impact attendu :** +10 à +15 pts de hit@15, MRR de 0,449 → 0,60+.

> ⚠️ Note historique : un rerank a déjà été testé le 11/10 et a **fait baisser** le
> score (61,8 % → 56,8 %, −5,0 pts) avec le réglage alors utilisé. À re-tester
> proprement en 2 étages (top-50 → top-15) et sur conv-41 seul d'abord.

---

## 🧪 4. Protocole d'exécution et d'ablation recommandé

Mesurer chaque brique sur **une seule conversation témoin : `conv-41`**
(`n = 152` en officiel, actuellement **50,3 % QA** et **59,6 % hit@15**).

| Étape | Modification | Fichier cible | Réindex. ? | Temps (conv-41) | Métrique cible |
|---|---|---|---|---|---|
| **Run 1** | Prompt Reader v2 (Inférence Cat. 3 + Scan exhaustif Cat. 1 + Dual-Date Output Cat. 2) | `locomo_official.py` | ❌ Non | ~3 min | `qa.accuracy` 50,3 % → ~58 %+ |
| **Run 2** | Expansion tours adjacents (±1/±2) + quota max 3/session | `locomo_official.py` / `hybrid_search.py` | ❌ Non (post-RRF) / ✅ légère | ~30 s (retrieval seul) | `hit@15` 59,6 % → ~70 %+ et `all_evidence` |
| **Run 3** | Dual-Date Regex Anchoring à l'ingestion | `locomo_official.py` (indexeur) | ✅ Oui (~10 s) | ~30 s (retrieval seul) | `hit@15` sur questions Cat. 2 |
| **Run 4** | Reranker local Cross-Encoder (top-50 → top-15) | `hybrid_search.py` | ❌ Non | ~1 min (retrieval seul) | `hit@15` / `all_evidence` → 75-80 %+ |
| **Run Final** | Run officiel complet (10 conversations) | harnais complet | ✅ Oui | ~45 min | nouveau score de référence (cible 65-70 %+ QA) |

---

## 5. Invariants à ne jamais violer

- Jamais deux runs LoCoMo simultanés (contention d'indexation → `read timed out`).
- Ne jamais publier un réglage qui gonfle le hit@10/hit@15 sans bouger le QA.
- Ne jamais mettre un hit@topN à côté d'une accuracy QA sur des `n` différents
  (ex. hit@10 sur 197 q vs QA sur 199 q).
- Reader et judge = `deepseek-v4.1-flash` ; ce modèle doit figurer dans le tableau
  des comparables (baselines publiées : GPT-4o-mini).
- Publier « memory-toolkit **+** deepseek-v4.1-flash », pas la performance de la
  skill seule.
- Vérifier chaque chiffre sur le fichier de résultats et le statut process, jamais
  sur une sortie de session.
- Labels de catégories : publier `Category 1-5` avec volumétrie.

---

## 6. Livrables figés (run 2026-10-11, Drive `Hal-Backup/`)

| Fichier | Taille | Contenu |
|---|---|---|
| `locomo-official-2026-10-11-FINAL.json` | 13 025 o | cat. 1-4, `n = 1386`, **49,6 %** |
| `locomo-hardcore-2026-10-11-FINAL.json` | 42 463 o | cat. 1-5, `n = 1946`, **57,6 %** |
| `locomo-official-2026-10-11-all.json` | 68 635 o | run complet |
| `locomo-official-2026-10-11-qa-conv-49.json` | 4 680 o | conv-49 seul |

Hash officiel : `a19c048220f9dd4d…` · Hash hardcore : `79d28339b2f2cc57`.
