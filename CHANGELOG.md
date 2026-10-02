# Changelog — OpenClaw Memory Toolkit

All notable changes to the OpenClaw Memory Toolkit skill.

## v2.2.1 — Disclose the LLM Transport, Harden PII Scrubbing (2026-10-02)

Closes the T09 finding raised by the ClawHub / SkillSpector scan on 2026-10-02:
**"Undisclosed Cloud Transmission of Memory and Session Content"** in
`trace_extractor.py`. The finding was valid. The extractor's primary transport is
Ollama **cloud**, so memory and session text leaves the machine — and neither the
code nor the docs said so, in a repository that advertises itself as local-first.

### Fixed
- **Undisclosed cloud transmission** (`trace_extractor.py`): the extraction path
  posts to `https://ollama.com/api/chat` with a bearer key. Every run now prints its
  destination before sending — `[Security] ⚠️ CLOUD TRANSMISSION: …` when the target
  is cloud, `[Security] … (stays on this machine)` when it is local — via the new
  `llm_destination()` helper.
- **No local-only escape hatch**: added **`TRACE_LLM_LOCAL_ONLY=1`**, which makes
  `call_ollama_cloud()` return early and refuses every cloud call. Local-only mode
  cannot silently fail over to a transport that leaves the machine.
- **`sanitize_pii()` gaps**: the filter was regex-only and missed secrets in uncommon
  formats. Extended with JWTs (`eyJ…`), hex blobs ≥32 chars, base64 blobs ≥40 chars,
  French phone numbers, card-like digit runs, `access_key`/`apikey` assignments and
  OpenSSH private keys. The docstring now states plainly that scrubbing is
  **best-effort, not a guarantee**, and that the transport decision is the primary
  control. A "local-first, no cloud" claim is only as good as the transport behind it.

### Docs
- `README.md`, `SKILL.md`: the transmission, the destination, and the local-only
  switch are now documented; the blanket "zero external cloud API" phrasing is
  corrected where it did not hold for the extractor. `TRACE_LLM_LOCAL_ONLY` added to
  the configuration table.
- `docs/SECURITY-AUDIT-NOTES.md`: T09 recorded as a **closed real finding** (§1.0);
  the two false-positive families it generated — tainted-flow at `urlopen` in
  `trace_extractor.py` (§2.4) and credential-access hits on the secret deny-lists and
  on the audit prose itself (§2.5) — are triaged with dispositions.

### Verified (executed, not read)
- `llm_destination()` returns `('local', …)` under `TRACE_LLM_LOCAL_ONLY=1`, and
  `('cloud', "… content leaves this machine")` when `OLLAMA_API_KEY` is set.
- `sanitize_pii()` redacts all six test classes: JWT, hex-32, base64-40, French phone
  number, email, bearer token — 6/6.
- Non-regression: ordinary note text survives (`version 2.1.4`, `exit code 1`
  intact); only the embedded email is redacted.
- `ast.parse()` clean on the modified module.

### Acknowledgements
T09 reported by the ClawHub security scan (SkillSpector). Fixed rather than argued.

## v2.2.0 — Trace Extractor Ships, Ontology GC, Deterministic IDs (2026-10-02)

First release that includes `trace_extractor.py` as a **shipped artifact** rather
than a referenced-but-absent step, plus a long-overdue garbage collector for the
ontology op-log. Two latent bugs in the extractor are fixed: IDs that were
never stable across processes, and an "upsert" that physically appended.

### Added
- **`trace_extractor.py`** — the session/notes extractor is now published. It was
  cited as step 1 of the documented pipeline since v2.0 but the file itself was
  never in the repository, so cloning the toolkit gave you a README referencing a
  script no one could run. Categories: decisions, errors, facts, patterns.
- **`ontology_compact.py`** — garbage collector for `memory/ontology/graph.jsonl`.
  The ontology file is an append-only operation log; nothing ever replayed it, so
  the same entity was rewritten on every run and the file grew without bound.
  The compactor replays the log into a consolidated state (one line per active
  entity, superseded records dropped), backs up first, validates that the entity
  set and contents are identical, and only then swaps in place. Idempotent: it
  skips when the gain is below `--min-gain` (default 5%).

### Fixed
- **Non-deterministic entity IDs** (`trace_extractor.py`): decisions were keyed
  with `hash(what) % 10000`. Python randomises `hash()` per process
  (`PYTHONHASHSEED`), so the *same* decision produced a *different* ID on every
  run. The `if entity_id not in existing_ids` guard could never fire — the ID was
  always new — and the guard's own premise (ID identifies content) was false.
  Observed impact on a production workspace: one episode rewritten **38 times**
  under 38 different IDs, 63 IDs duplicated 2–38×, 52% of all log lines redundant.
  IDs now come from `stable_id()`, a SHA-256 prefix, stable across processes and
  machines.
- **`upsert` that appended** (`trace_extractor.py`): records were labelled
  `"op": "upsert"` but written with `open(path, "a")`. The operation name
  described an intent the code did not implement — an update was impossible, only
  appends happened. Both writers now go through `upsert_entities()`, which reads
  the current file, replaces matching entities in place, appends the rest, and
  writes atomically via a temp file.

### Verified (executed, not read)
- **ID stability**: `stable_id()` called from three separate interpreter processes
  returns the identical digest (`dec_20260629_4f0644cd`, `tl_20260613_873a194b`),
  where the previous `hash()`-based scheme returned a different value each run.
- **Real upsert**: on a two-entity file, updating an existing ID leaves the line
  count unchanged and replaces the record; adding a new ID grows it by exactly one.
- **Compactor on a production graph**: 742,364 → 272,518 bytes (−63%),
  2,018 → 912 lines, 1,106 redundant lines dropped, and the reloaded entity set
  compared equal to the pre-compaction state (912 active entities, 0 lost).
- **Compactor idempotence**: re-run on the already-compacted file reports 0 lines
  dropped and writes nothing (gain below threshold).
- **Syntax**: `ast.parse()` clean on both new scripts.

### Notes
- The ontology compactor pairs with the parser fix in the sibling release line:
  `memory_health.py` and the index builder accept any record carrying an entity,
  so a consolidated `"op": "state"` file and a raw operation log both index
  correctly. Previously the indexer matched `op == "create"` only, which silently
  indexed **zero** entities once a log had been compacted.

## v2.1.4 — Allowlist/Index Agreement, Both Directions (2026-09-27)

Follow-up found by the v2.1.3 control pass. v2.1.3 fixed an allowlist that was
**narrower** than what was indexed; the fix then made it **wider** than disk
reality. Declared scope must equal indexable scope in both directions.

### Fixed
- **Dead entry in `ALLOWED_SCAN_FILES`** (`hybrid-search/hybrid_search.py`): v2.1.3 declared `TOOLS.md` unconditionally, but `collect_all_files()` guards each root file with `os.path.exists()` — and `TOOLS.md` does not exist on a standard workspace (its content was merged into `AGENTS.md`). The allowlist therefore advertised a file that was never indexed: the same intent/code divergence v2.1.3 set out to remove, re-created in the opposite direction. Optional root files are now declared **only when present on disk** (`{f for f in ROOT_CONFIG_FILES if os.path.exists(f)}`), so `MEMORY.md` is always declared and `TOOLS.md` is declared exactly when it is actually indexable. `OWN_SKILL_FILE` stays unconditional (it is a shipped artifact).

### Verified (executed, not read)
- **`TOOLS.md` absent** (real workspace): dropped from the allowlist; `safe_resolve()` refuses the path with `UnsafeFileError: outside allowed scan dirs`.
- **`TOOLS.md` present** (temp workspace, `WORKSPACE_ALLOW_CUSTOM=1`): automatically declared and resolved successfully.
- **`MEMORY.md`** (present): still declared and resolved.
- **Non-regression**: `/etc/passwd` still refused.

### Files Modified (1)
`hybrid-search/hybrid_search.py`

## v2.1.3 — Security Round 6: Runtime Confinement & Read-Only Correctness (2026-09-27)

Second pass over the public ClawHub audit, after the v2.1.2 import-path work.
Four real findings closed; the rest of the report was confirmed as scanner false
positives and is now documented so it does not have to be re-triaged.

### Security
- **`--workspace` no longer bypasses confinement** (`auto_archive.py`, `consolidate_advisor.py`): the `is_relative_to(WORKSPACE)` guard ran only at import time, while the CLI re-bound the module globals from `args.workspace` further down the flow — so `--workspace <other>` produced unvalidated `MEMORY_DIR` / `ARCHIVE_DIR` / `SCORES_FILE` for the whole run. The check is now a `validate_paths()` function invoked at import **and** immediately after every `--workspace` rebind. `WORKSPACE` is also `.resolve()`d before use. This was an intent/code divergence: the code claimed confinement, the flag removed it.
- **Passive health check no longer mutates memory** (`memory-health.py`, T09): `run_trace_extraction()` ran `trace-extractor.py` unconditionally, and that script **appends to `memory/ontology/graph.jsonl` and rewrites daily notes** — so `memory-health.py` reported `MODE: READ-ONLY` while silently changing state. The extractor is now invoked with `--dry-run` unless the caller explicitly opted into mutation; the mutating path is reachable only through `_run_trace_extraction(..., mutating=True)`, set from the user's `--fix` intent.
- **Scan scope now matches what is actually indexed** (`hybrid-search/hybrid_search.py`): `ALLOWED_SCAN_DIRS` declared "memory/ only", but `collect_all_files()` also indexed `MEMORY.md`, `TOOLS.md` and `skills/memory-health/SKILL.md`. Added `ALLOWED_SCAN_FILES`, an explicit allowlist of those exact paths, which `safe_resolve()` now accepts in addition to the scoped directories. Deliberately a set of named files, never a directory — a directory entry would re-open skill enumeration.
- **Subprocess isolation hardened** (`memory-health.py`): `WORKSPACE` came straight from the environment and drove every subprocess path, and `["openclaw", ...]` calls trusted the inherited `$PATH`. `OPENCLAW_BIN` is now resolved once via `shutil.which()` with an absolute fallback (no bare `"openclaw"` remains), and `WORKSPACE` is validated against the expected root unless `WORKSPACE_ALLOW_CUSTOM=1` is set deliberately.

### Verified
- Syntax validated on all four modified modules.
- `validate_paths()` exercised for real: an import-time load succeeds, then a simulated escape (`MEMORY_DIR=/etc` under the real workspace) raises `RuntimeError`; a legitimate workspace still archives normally in `--dry-run`.
- Read-only path confirmed to build `trace-extractor.py --days N --dry-run`; `--help` on the extractor confirms the flag is supported.
- Allowlist exercised: `MEMORY.md` and the skill's own `SKILL.md` accepted; `/etc/passwd`, `skills/<other>/SKILL.md`, `SOUL.md`, `USER.md`, `.secrets/*` and `memory/../SOUL.md` all refused.

### Documented (scanner false positives — not defects)
- **"Credential Access" in the secret-path filters**: `SECRET_PATH_PATTERNS` / `SECRET_SKIP_PATTERNS` match `\.ssh`, `id_rsa`, `token`, `password`, `.env`, `.aws`, `.config/google`. These are a deny-list that *prevents* such files from being read or indexed, matched against the resolved path so a benign-named symlink cannot smuggle a secret through. Defensive filter, not an access attempt.
- **"Unsafe Defaults" in `CHANGELOG.md` / `README.md`**: `/tmp/vec-test-venv` and `/etc/passwd` appear only as documentation of already-removed vulnerabilities and of a guard's test case. Prose describing a fixed defect is not a live defect.
- **"Tainted Flow / network sink" at `urlopen`**: `consolidate_advisor.py` and `hybrid-search/hybrid_search.py` never POST memory content anywhere attacker-chosen. `OLLAMA_URL` (and `OLLAMA_EMBED_URL`) pass through `get_safe_ollama_url()`, which raises `ValueError` at import for any host outside `{localhost, 127.0.0.1, ::1}` — the process will not start against a remote endpoint. The destination is fixed to loopback before any memory is read, so this is a **local flow secured by design**, not exfiltration.
- Full triage written up in `docs/SECURITY-AUDIT-NOTES.md`, including scanner-configuration recommendations.

### Files Modified (5)
`auto_archive.py`, `CHANGELOG.md`, `consolidate_advisor.py`, `hybrid-search/hybrid_search.py`, `memory-health.py`, added `docs/SECURITY-AUDIT-NOTES.md`

## v2.1.2 — Security Round 5: Import Path & Filesystem Confinement (2026-09-27)

Follow-up to the public ClawHub security audit. Three real findings closed; the
remaining secondary-block items were confirmed as scanner false positives.

### Security
- **Attacker-controllable import path removed** (`hybrid-search/hybrid_search.py`): `VEC_VENV_PATH = "/tmp/vec-test-venv/lib/python3.14/site-packages"` + `sys.path.insert(0, ...)` deleted. A world-writable directory at the head of `sys.path` gives import resolution absolute priority over site-packages, so any local process could drop a `sqlite_vec.py` and have it executed on the next `import`. The path also did not exist on the production host — dead *and* dangerous. `sqlite_vec` now imports from the active environment, with an actionable `ImportError` (`pip install sqlite-vec`) and a comment recording why a `/tmp` entry must never return.
- **`safe_resolve()` added to `hybrid_search.py`**: applied to `index_file()`, `index_jsonl_file()` and the `--dir` walk in `cmd_index()`. Guard order: refuse symlinks outright; `os.path.realpath()` to normalise `..`; require the result to sit inside `ALLOWED_SCAN_DIRS` (`memory/` only); match `SECRET_PATH_PATTERNS` against the **resolved** path (never the literal one, so a symlink cannot smuggle a secret through an innocuous name); require a regular file. `--dir` pointing outside scope is rejected before the glob runs. New `UnsafeFileError` exception.
- **Symlinks refused in the memory scanners** (`scoring.py`, `auto_archive.py`, `consolidate_advisor.py`): `is_symlink()` checked before `is_file()` at each walk site. `scoring.py` gained `is_safe_memory_file()` — a symlink named like a daily note is the classic route for pulling a private key into an index that then ships it to the embedding endpoint.
- **Secret patterns extended**: `.ssh`, `.aws`, `.config/google`, `id_rsa`, `id_ed25519`, `.pem`, `.key`.

### Verified
- Syntax validated on all five modules.
- Guards exercised for real: a legitimate note is accepted; a symlink named `2026-01-01-innocent.md` pointing at `/etc/passwd` is refused; `/etc/passwd` passed directly is refused as out of scope; `api-token-notes.md` inside `memory/` is refused; `--dir /etc` is refused before listing.

### Not Changed (by design)
- `auto_archive.py` and `consolidate_advisor.py` still require `--force` in non-interactive mode. This is intended behaviour, not a defect — now documented rather than implicit.

### Files Modified (6)
`auto_archive.py`, `CHANGELOG.md`, `README.md`, `consolidate_advisor.py`, `hybrid-search/hybrid_search.py`, `scoring.py`

## v2.1.1 — Security Round 4: PII Purge & Scope Confinement (2026-08-22)

### Security
- **PII purge**: Deleted all results/*.json (11 files), results/*.svg (9 files), hybrid-search/FULL_INDEX_REPORT.md, hybrid-search/test_results.json, hybrid-search/agent_memory.db, hybrid-search-proto/ (entire prototype folder), __pycache__/*.pyc — all contained real personal data (Stéphane, Airbus, .secrets/, stephanemee.com, AstroCapture)
- **`.gitignore` hardened**: Added `*.svg`, `eval_output/`, `*.log`, `*.pyc`, `hybrid-search-proto/`, `.secrets/`
- **Subprocess query sanitized**: `memory-health.py` hardcoded query `"Stéphane's role at Airbus"` replaced with anonymized `"project alpha configuration"`
- **Script path validation**: All `subprocess.run` script paths validated with `Path.resolve().is_relative_to(WORKSPACE)` — prevents path traversal
- **Skill enumeration blocked**: `hybrid_search.py` no longer globs `skills/*/SKILL.md` — only indexes its own SKILL.md
- **Personal files excluded from index**: `USER.md`, `IDENTITY.md`, `AGENTS.md`, `SOUL.md`, `HEARTBEAT.md` no longer indexed by hybrid_search.py
- **Scope confinement**: `scoring.py`, `auto_archive.py`, `consolidate_advisor.py` validate `MEMORY_DIR.is_relative_to(WORKSPACE)` with explicit `ALLOWED_SCAN_DIR`

### Changed
- **`memory-health.py` is READ-ONLY by default**: No SVG charts, JSON reports, or benchmark reports written to disk without `--output-dir <path>` flag
- **`check_drift()` no longer auto-creates `RESULTS_DIR`**: Only reads existing results if present
- **Docstring updated**: Clearly documents read-only default, `--output-dir` for output, `--fix` as destructive mode
- **`run_tests.py` TEST_QUERIES anonymized**: `AstroCapture` → `project_alpha`, `leadership coaching Airbus` → `team leadership coaching session`, `2026-08-17` → `sample_note_01`, etc.
- **SKILL.md & README.md**: Updated with read-only documentation, security notes section, `--fix` destructive mode warning
- **`consolidate_advisor.py` docstring**: Removed personal name reference

### Files Modified (10)
`.gitignore`, `README.md`, `SKILL.md`, `auto_archive.py`, `consolidate_advisor.py`, `hybrid-search/hybrid_search.py`, `hybrid-search/run_tests.py`, `memory-health.py`, `scoring.py`, deleted `hybrid-search/FULL_INDEX_REPORT.md`

## v1.3.0 — Security Round 3 (2026-08-18)

### Fixed
- **OLLAMA_EMBED_URL no longer hardcoded**: Embedding endpoint now derived from `OLLAMA_URL` (`{OLLAMA_URL}/api/embeddings`) — consent warnings show the actual destination, not a misleading one
- **`check_ollama_url()` validates both endpoints**: Now checks `OLLAMA_URL` and `OLLAMA_EMBED_URL` for localhost
- **Batch/add warnings show real endpoint**: Consent text displays `OLLAMA_EMBED_URL` (where data actually goes) instead of `OLLAMA_URL`
- **`run_tests.py` no longer exposes content**: Removed all content snippets from stdout, `test_results.json`, and `FULL_INDEX_REPORT.md` — only metadata (source, scores, category) is stored
- **"Safe by default" claim corrected**: README and SKILL.md now accurately state that dry-run is *available* but not the default for all scripts (archive, scores, consolidation report write by default)

## v1.2.0 — Security Round 2 (2026-08-18)

### Added
- **Consent warnings on `hybrid_search.py index`**: Batch indexing now displays a warning before sending file contents to Ollama for embedding. Use `--yes` to skip in automation
- **Embedding notice on `hybrid_search.py add`**: Single-file add prints a one-line notice. Use `--quiet` to suppress
- **`OLLAMA_URL` localhost validation**: Warning printed if endpoint is not localhost
- **`--benchmark` is now benchmark-only**: No longer runs the full health check suite — benchmark only (unless combined with `--deep` or `--quick`)
- **READ-ONLY / FIX MODE banner**: `memory-health.py` prints clear mode indicator at startup
- **Inline SECURITY comments**: All `subprocess.run` and `urlopen` calls annotated with security context
- **4 new Security Notes** in README and SKILL.md: `--force` flag, subprocess/urlopen intent, hybrid_search consent, `--benchmark` behavior

### Fixed
- `memory-health.py --fix` now requires interactive confirmation or `--force` flag
- `--force` help text more explicit about backups

## v1.1.0 — Security Round 1 (2026-08-18)

### Fixed
- **`consolidate_advisor.py` docstring corrected**: No longer claims "Does NOT modify any files" — accurately reports that it writes `consolidation_report.json`
- **`--apply-promotions` confirmation added**: Now requires interactive `y/n` prompt or `--force` flag before writing to MEMORY.md
- **`--dry-run` no longer writes report**: `consolidate_advisor.py --dry-run` is truly read-only
- **`memory-health.py --fix` safety**: Creates timestamped backup in `memory/backup/` before modifying; requires confirmation or `--force`
- **`hybrid_search.py init` safety**: Requires `--force` to overwrite existing database
- **Secret file filtering in `scoring.py`**: Skips `.secrets/`, `*.env`, `credentials*`, `*token*`, `*password*`, `.git/`
- **`auto_archive.py` bulk safety**: Requires `--force` or confirmation if moving >10 files
- **`run_tests.py` content exposure**: Removed content snippets from `test_results.json` (metadata only)

### Changed
- README.md: Removed "no external API dependencies" and "pure stdlib" claims — added accurate Security Notes section
- SKILL.md: Same corrections + fixed duplicated Design Principles section
- README.md: Removed Second Brain comparison table and references

## v1.0.0 — Initial Release (2026-08-17/18)

### Added
- **`trace_extractor.py`** — Session extraction: decisions, errors, facts, patterns from transcripts and daily notes
- **`auto_archive.py`** — Daily note archiving: moves notes >N days to `memory/archive/YYYY-MM/`
- **`scoring.py`** — Temporal decay scoring: recency × category weight × frequency boost × entity boost
- **`consolidate_advisor.py`** — Consolidation suggestions: clusters, promotions, stale items, duplicates (LLM optional via Ollama)
- **`memory_health.py`** — System health check: diagnostics, benchmark, ontology health, drift detection
- **`hybrid_search.py`** — Hybrid search: FTS5 (BM25) + sqlite-vec (cosine) + Reciprocal Rank Fusion (k=60)
- **`run_tests.py`** — Validation queries for hybrid search index
- **`ontology/schema.yaml`** — Entity and relation type definitions (20 entity types, 10 relation types)
- **README.md** — Full documentation with pipeline diagram, CLI examples, configuration, design principles
- **SKILL.md** — OpenClaw skill manifest with script descriptions and cron setup
- **LICENSE** — MIT