# Changelog — OpenClaw Memory Toolkit

All notable changes to the OpenClaw Memory Toolkit skill.

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