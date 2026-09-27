# Security Audit Notes — Scanner Triage

Reference document for external security scanners (SkillSpector / ClawHub) and for
future audits. Records which findings are **closed in code**, which are **accepted
false positives**, and — critically — *why*, so the same alerts do not have to be
re-litigated on every scan.

Last updated: 2026-09-27 (v2.1.3)

---

## 1. Closed — real findings fixed in code

### 1.1 `--workspace` bypassed the confinement guard (Intent/Code Divergence)

**Files:** `auto_archive.py`, `consolidate_advisor.py`

The guard `MEMORY_DIR.is_relative_to(WORKSPACE)` ran only at **import time**. The
CLI later re-bound the module globals from `args.workspace` without re-running it,
so `--workspace /somewhere/else` produced unvalidated paths for the rest of the run.

**Fix:** the check is now a `validate_paths()` function, called at import *and*
immediately after every `--workspace` rebind. `WORKSPACE` is also `.resolve()`d
before use so a relative or symlinked argument cannot defer resolution.

**Verified:** a simulated escape (`MEMORY_DIR=/etc` under workspace
`~/.openclaw/workspace`) raises `RuntimeError: Security: MEMORY_DIR escapes
workspace: /etc`. A legitimate workspace still passes.

> Scope note: this guard enforces *confinement* — derived paths stay inside the
> declared workspace. It is not a whitelist of permitted workspace roots; passing
> a self-consistent alternative root remains allowed by design.

### 1.2 Passive health check mutated memory state (T09, Read-Only Mode)

**File:** `memory-health.py`

`run_trace_extraction()` shells out to `trace-extractor.py`, which **appends to
`memory/ontology/graph.jsonl` and rewrites daily notes**. It was called
unconditionally during a normal (read-only) health check, so `memory-health.py`
modified state while announcing `MODE: READ-ONLY`.

**Fix:** the extractor is invoked with `--dry-run` unless the caller explicitly
opted into mutation. The mutating path is reachable only through the internal
`_run_trace_extraction(..., mutating=True)`, set from the user's `--fix` intent.

**Verified:** the generated command line contains `--dry-run` in every non-`--fix`
path; `trace-extractor.py --help` confirms the flag is supported.

### 1.3 Scan scope did not match what was actually indexed (Intent/Code Divergence)

**File:** `hybrid-search/hybrid_search.py`

`ALLOWED_SCAN_DIRS = {MEMORY_DIR}` declared "memory/ only — no skills/, no parent
traversal", but `collect_all_files()` also indexed three files outside it:
`MEMORY.md`, `TOOLS.md` (workspace root) and `skills/memory-health/SKILL.md`.

**Fix:** added an explicit `ALLOWED_SCAN_FILES` allowlist of those exact paths, and
`safe_resolve()` accepts either a path inside `ALLOWED_SCAN_DIRS` **or** an exact
match in `ALLOWED_SCAN_FILES`. Deliberately a set of *named files*, never a
directory — adding a directory would re-open skill enumeration.

**Verified:** `MEMORY.md`, `TOOLS.md` and the skill's own `SKILL.md` are accepted;
`/etc/passwd`, `skills/<other>/SKILL.md`, `SOUL.md`, `USER.md`, `.secrets/*` and
`memory/../SOUL.md` are all refused.

### 1.4 Subprocess isolation (Excessive Agency)

**File:** `memory-health.py`

Two weaknesses: (a) `WORKSPACE` came straight from the environment and drove every
subprocess path; (b) `["openclaw", ...]` calls trusted the inherited `$PATH`.

**Fix:**
- `OPENCLAW_BIN` is resolved once via `shutil.which("openclaw")`, with an absolute
  fallback; both call sites use it. No bare `"openclaw"` remains.
- `EXPECTED_WORKSPACE` is checked at import. An unexpected `WORKSPACE` raises unless
  `WORKSPACE_ALLOW_CUSTOM=1` is set deliberately. Existing absolute-path
  confinement guards on `TRACE_EXTRACTOR` / `LOCOMO_TEST` are unchanged.

---

## 2. Accepted false positives — do not re-report

### 2.1 "Credential Access" in the secret-path filters

**Files:** `hybrid-search/hybrid_search.py`, `scoring.py`
**Scanner class:** Credential Access (critical)

`SECRET_PATH_PATTERNS` / `SECRET_SKIP_PATTERNS` contain regexes matching `\.ssh`,
`id_rsa`, `token`, `password`, `.env`, `.aws`, `.config/google`. The scanner treats
these as attempts to *reach* credentials.

They are the opposite: a **deny-list that prevents** such files from being read or
indexed. They are matched against the *resolved* path precisely so a benign-named
symlink cannot smuggle a secret through.

**Disposition:** False positive — defensive filter. Recommend the scanner exclude
regex-literal definitions from credential-access heuristics.

### 2.2 "Unsafe Defaults" in `CHANGELOG.md` / `README.md`

**Files:** `CHANGELOG.md`, `README.md`
**Scanner class:** Unsafe Defaults / path traversal

The scanner flags `/tmp/vec-test-venv` and `/etc/passwd` in these files. Both appear
only as **documentation of vulnerabilities that were already removed** — the `/tmp`
`sys.path` entry (fixed in v2.1.2) and a symlink-to-`/etc/passwd` test case used to
prove a guard works.

Prose describing a fixed defect is not a live defect.

**Disposition:** Inapplicable. Recommend excluding `*.md` from behavioural analysis,
or restricting such rules to executable file types.

---

## 3. Standing recommendations for the scanner configuration

1. Exclude markdown (`*.md`) from behavioural/taint analysis — documentation of a
   fix is not the fix's absence.
2. Do not treat regex-literal strings inside deny-lists as credential access.
3. Treat import-time-only validation as a distinct rule from runtime validation;
   several findings in this project were of that exact shape, and are now closed.
