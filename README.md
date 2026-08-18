# 🧠 OpenClaw Memory Pipeline

**Complete memory management pipeline for OpenClaw agents: extraction, archiving,
scoring, consolidation, health monitoring, and ontology — all local-first.**

Five standalone Python scripts that form a complete memory lifecycle pipeline for
[OpenClaw](https://github.com/openclaw/openclaw) agents. No external API dependencies
(Ollama runs locally via HTTP, no cloud APIs)
— works with any local LLM (Ollama, LM Studio, etc.) or fully without LLM in fallback mode.

Built for local-first OpenClaw setups (Ollama/GLM, nomic-embed-text).

## Pipeline

```
Nightly Cron (23h)
  │
  ├─ 1. trace_extractor.py     # Extract decisions/errors/facts from sessions
  ├─ 2. auto_archive.py        # Archive daily notes >21 days
  ├─ 3. scoring.py             # Score all memories with temporal decay
  ├─ 4. consolidate_advisor.py # Suggest consolidations (agent reviews)
  └─ 5. memory_health.py       # Periodic health check (weekly)
```

All scripts are standalone and composable. Run individually or as a pipeline.

## Scripts

### 1. `trace_extractor.py` — Session extraction

Extracts decisions, errors, facts, and patterns from OpenClaw session transcripts
and daily notes. Updates daily notes, appends entities to the ontology graph.

```bash
# Nightly (pattern-based, fast ~5s)
python3 scripts/trace_extractor.py --days 1

# Deep extraction (LLM-powered, ~60-180s)
python3 scripts/trace_extractor.py --days 3 --llm

# With a specific session transcript file (opt-in, explicit)
python3 scripts/trace_extractor.py --days 1 --llm --session-file /path/to/session.jsonl

# Preview only
python3 scripts/trace_extractor.py --days 1 --llm --dry-run
```

**Categories:** 🟢 DECISIONS, 🔴 ERRORS, 🔵 FACTS, ⬆️ PROMOTIONS

### 2. `auto_archive.py` — Daily note archiving

Moves daily notes older than N days to `memory/archive/YYYY-MM/`.

```bash
python3 scripts/auto_archive.py                 # Archive notes > 21 days
python3 scripts/auto_archive.py --days 30       # Custom threshold
python3 scripts/auto_archive.py --dry-run       # Preview only
python3 scripts/auto_archive.py --verbose       # Show each file
```

Idempotent. Only moves `YYYY-MM-DD*.md` files. Zero dependencies.

### 3. `scoring.py` — Temporal decay scoring

Scores all memory items using exponential recency decay, category weights,
frequency boost, entity boost, and completion penalty.

```bash
python3 scripts/scoring.py                      # Score all memories
python3 scripts/scoring.py --verbose            # Show top 20
python3 scripts/scoring.py --threshold 0.3      # Filter by min score
python3 scripts/scoring.py --dry-run            # Don't write output
```

**Formula:** `score = weight_category × recency_decay × frequency_boost × entity_boost × completion_penalty`

**Category weights:** DECISIONS ×3, ERRORS ×2, FACTS ×1.5, PATTERNS ×1.2, TRANSIENT ×1

**Output:** `memory/scores.json` — full ranking with stats and promotion candidates.

### 4. `consolidate_advisor.py` — Consolidation suggestions

Analyzes recent daily notes + scores.json to identify clusters, promotions,
stale items, and duplicates. Writes consolidation_report.json by default.
Modifies MEMORY.md only with --apply-promotions flag (requires confirmation).

```bash
python3 scripts/consolidate_advisor.py                     # Last 7 days
python3 scripts/consolidate_advisor.py --days 14           # Custom window
python3 scripts/consolidate_advisor.py --verbose           # All suggestions
python3 scripts/consolidate_advisor.py --no-llm            # Skip LLM (fallback)
python3 scripts/consolidate_advisor.py --apply-promotions  # Write to MEMORY.md
```

**Output:** `memory/consolidation_report.json` — clusters, promotions, stale items, duplicates.

LLM optional (Ollama) for cluster summaries. Falls back to text-based with `--no-llm`.

### 5. `memory_health.py` — System health check

Comprehensive diagnostics: trace extraction, benchmark, MEMORY.md size, ontology
health, daily notes hygiene, index status, drift detection.

```bash
python3 scripts/memory_health.py              # Full health check
python3 scripts/memory_health.py --quick      # Skip benchmark & LLM
python3 scripts/memory_health.py --benchmark  # Benchmark only
python3 scripts/memory_health.py --deep       # LLM + sessions + benchmark
python3 scripts/memory_health.py --fix        # Fix mode (archive, clean)
```

**Output:** `results/YYYY-MM-DD.json` — full diagnostic report.

## Ontology

JSONL-based entity and relation graph with YAML schema.

**Entity types:** Person, Organization, Project, Task, Document, Event, Skill,
Device, Service, Tool, Infrastructure, Concept, Location, Pet, BugFix,
SecurityEvent, Integration, Feature, Software, Configuration

**Relation types:** reports_to, has_owner, includes, depends_on, manages, uses,
integrated_with, located_at, fixes, monitors

**Files:**
- `ontology/schema.yaml` — type and relation definitions
- `memory/ontology/graph.jsonl` — entity and relation records (generated)
- `memory/ontology/graph-index.json` — search index (generated)

## Configuration

Environment variables with defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `~/.openclaw/workspace` | OpenClaw workspace path |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `glm-5.2` | Model for LLM extraction/summaries |
| `TRACE_LLM_MODEL` | `glm-5.2` | Model for trace-extractor LLM calls |

## Requirements

- Python 3.10+
- Ollama (optional — LLM extraction and cluster summaries)
- No pip packages required for core pipeline. Hybrid search requires sqlite-vec (optional).

## Nightly Cron

```bash
# Nightly (23h):
python3 scripts/trace_extractor.py --days 1
python3 scripts/auto_archive.py
python3 scripts/scoring.py
python3 scripts/consolidate_advisor.py --no-llm

# Weekly health check (Monday):
python3 scripts/memory_health.py --quick

# Monthly deep check (manual):
python3 scripts/memory_health.py --deep
```

## Design Principles

1. **Local-first** — no external API, no paid dependencies
2. **Composable** — each script is standalone, can run independently
3. **Safe by default** — dry-run available for all analysis scripts; some nightly cron commands modify files by default (archive, scores, consolidation report). Review cron commands before deploying.
4. **Human-in-the-loop** — consolidation suggestions, not auto-merge
5. **Pipeline-friendly** — scripts chain naturally, outputs feed inputs

## License

MIT — free to use, modify, and share.

## Acknowledgments

- [OpenClaw](https://github.com/openclaw/openclaw) — the agent framework this was built for

## Security Notes

- ⚠️ **Nightly cron modifies files by default**: `auto_archive.py` moves files, `scoring.py` writes scores.json, `consolidate_advisor.py` writes consolidation_report.json. Review cron commands before deploying.
- ⚠️ **`--fix` mode is destructive**: `memory-health.py --fix` moves daily notes to archive/ and rewrites ontology. Requires interactive confirmation or `--force` flag. Creates timestamped backups in `memory/backup/` before modifying.
- ⚠️ **`--force` flag**: The `--force` flag exists on `consolidate_advisor.py` and `memory-health.py` for non-interactive/cron use. It skips confirmation prompts. Requires existing verified backup. Only use in trusted automation with backups in place.
- ⚠️ **`--apply-promotions` modifies MEMORY.md**: `consolidate_advisor.py --apply-promotions` appends entries to MEMORY.md. Requires interactive confirmation or `--force` flag. Dry-run is the default when `--apply-promotions` is used without `--force`.
- ⚠️ **OLLAMA_URL restricted to localhost**: LLM calls (trace extraction, cluster summaries, embeddings) send memory text to Ollama. URL is validated to be `localhost`, `127.0.0.1`, or `::1` only — no remote hosts allowed.
- ⚠️ **PII sanitization before LLM calls**: Both `trace-extractor.py` and `consolidate_advisor.py` sanitize text with `sanitize_pii()` (regex-based removal of API keys, tokens, emails, passwords, PEM keys) before sending to any LLM. A console warning is printed before each LLM submission.
- ⚠️ **Session transcripts are opt-in only**: `trace-extractor.py` no longer scans `~/.openclaw/agents/` globally. Use `--session-file <path>` to explicitly provide a single transcript file. Paths are confined to the workspace.
- ⚠️ **`scores.json` stores hashes, not raw text**: `scoring.py` replaces note text with SHA256 hashes (first 16 chars) in all JSON output. File permissions set to `0o600` (owner-only read/write).
- ⚠️ **`EXTRACTION_PROMPT` excludes secrets**: The LLM extraction prompt explicitly instructs the model to never extract credentials, API keys, tokens, passwords, personal data, or session IDs.
- ⚠️ **Subprocess paths confined to workspace**: `subprocess.run` targets are validated with `Path.resolve()` + `is_relative_to(WORKSPACE)` before execution. No `os.environ` path injection possible.
- ⚠️ **Memory files may contain sensitive data**: Review all files before indexing with hybrid search. The `scoring.py` script skips files matching secret patterns (`.secrets/`, `*.env`, `credentials*`, `*token*`, `*password*`, `.git/`).
- ⚠️ **Hybrid search consent warnings**: `hybrid_search.py` `index` command displays a consent warning before batch embedding. Use `--yes` to skip in automation. `add` command prints a one-line embedding notice (use `--quiet` to suppress).
- ⚠️ **`--benchmark` is benchmark-only**: `memory-health.py --benchmark` runs only the LoCoMo benchmark, skipping all other health checks. Combine with `--deep` or `--quick` to run both.
- ⚠️ **Hybrid search `init` requires `--force`** to overwrite an existing database. Running `init` without `--force` on an existing DB will prompt for confirmation.