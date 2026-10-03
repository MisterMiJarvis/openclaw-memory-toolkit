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

### 2.3 "Tainted Flow / network sink" at `urlopen` in `consolidate_advisor.py`

**File:** `consolidate_advisor.py` (line ~318), and the same shape in
`hybrid-search/hybrid_search.py` (line ~137)
**Scanner class:** Data Exfiltration / Tainted Flow to network sink

The scanner traces user/memory-derived text into `urllib.request.urlopen` and
flags it as leaving the machine. The endpoint, however, is not attacker-selectable.

`OLLAMA_URL` is produced by `get_safe_ollama_url()` **at import time** (line 94):

```python
ALLOWED_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1"}

def get_safe_ollama_url(env_var, default):
    raw_url = os.environ.get(env_var, default)
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(...)
    hostname = parsed.hostname or ""
    if hostname not in ALLOWED_OLLAMA_HOSTS:
        raise ValueError(f"Host '{hostname}' not allowed ...")
    return raw_url

OLLAMA_URL = get_safe_ollama_url("OLLAMA_URL", "http://localhost:11434")
```

Any `OLLAMA_URL` that is not loopback raises `ValueError` at import — the process
does not start. The destination of every `urlopen` call is therefore
**`localhost` / `127.0.0.1` / `::1`, fixed before any memory content is read**, and
the only variable part of the request is the prompt body, which the local model
needs by definition. `hybrid_search.py` applies the identical guard to *both*
`OLLAMA_URL` and `OLLAMA_EMBED_URL`.

This is a deliberate design property: local inference must not leave the host.
The `urlopen` call is a loopback sink, not an exfiltration sink.

**Disposition:** False positive — **network flow secured by design (loopback-only)**.
Recommend the scanner treat host values validated against a loopback allowlist as
non-exfiltration sinks, or require the sink URL to be attacker-influenced for the
rule to fire.

**Residual hardening (not a defect, tracked):** the guard validates scheme and
hostname but not port, and it is applied at import only. A future revision could
pin the port and re-validate at call time; neither gap is reachable without the
ability to set the process environment in the first place.

---

### 2.4 "Tainted Flow / network sink" at `urlopen` in `trace_extractor.py`

**File:** `trace_extractor.py` (reported lines ~291 and ~310)
**Scanner class:** Data Exfiltration / Tainted Flow to network sink

Same shape as 2.3, but this one is **not** a false positive in the same sense, and
was fixed rather than triaged. The cloud path (`call_ollama_cloud`) posts to
`https://ollama.com/api/chat` with `Authorization: Bearer <key>`; the key comes from
the environment, hence the taint trace. This is a normal API client sending its own
credential to its own endpoint — not exfiltration of another secret. The finding
was nonetheless **acted on** in v2.2.1 (see 1.0): the transport is now disclosed,
`TRACE_LLM_LOCAL_ONLY=1` refuses it, and the destination is printed before each send.

**Disposition:** Behaviour **documented and operator-controlled** as of v2.2.1. The
remaining `urlopen` in `call_ollama` targets the fixed literal
`http://127.0.0.1:11434` — a loopback sink, false positive per 2.3.

### 2.5 "Credential Access" in the secret deny-lists (extended)

**Files:** `hybrid-search/hybrid_search.py` (~439), `scoring.py` (~113, ~128),
`README.md`, `CHANGELOG.md`, `docs/SECURITY-AUDIT-NOTES.md`
**Scanner class:** Privilege Escalation / Credential Access

Superset of 2.1. The scanner matches the *detection patterns* for secrets
(`re.compile(r"credential")`, `r"password"`, `r"token")` inside `SECRET_SKIP_PATTERNS`,
and additionally flags the **prose** in `CHANGELOG.md` / `README.md` /
`SECURITY-AUDIT-NOTES.md` that *documents* the protections. A deny-list that names
what it refuses is evidence of a control, not a credential read.

**Disposition:** Inapplicable. Recommend restricting credential-access rules to
filesystem-read sinks, and never firing on `*.md` prose describing a fix.

### 2.6 Round 7 (scan of published v2.2.1, 2026-10-02) — 54 findings triaged

Report: 54 findings across the published v2.2.1. **One was real** (see §1.5 below);
the remainder are the same four false-positive families, restated at higher volume
because the scan covered the docs as well as the code.

| Scanner claim | Where | Disposition |
|---|---|---|
| Tainted flow `os.environ` → `urlopen` | `consolidate_advisor.py` ~318 | **False positive.** Sink is `OLLAMA_URL`, produced by `get_safe_ollama_url()`; any non-loopback host raises `ValueError` at import. Per §2.3. |
| Tainted flow `os.environ` → `urlopen` | `trace_extractor.py` ~353 | **Real, fixed in v2.2.1.** The cloud call. Documented, operator-controlled, cancellable. Per §2.4. |
| Tainted flow `os.environ` → `urlopen` | `trace_extractor.py` ~359 | **False positive.** `call_ollama()` targets the fixed literal `http://127.0.0.1:11434/api/chat`. |
| Credential Access (×12) | `SECRET_SKIP_PATTERNS`, `README.md`, `CHANGELOG.md`, this file, `scoring.py`, `hybrid_search.py` | **False positive.** Deny-list literals, plus prose *documenting* the controls. Per §2.1 / §2.5. |
| Intent-Code Divergence (×6) | `README.md` 7-9, 194-197; `SKILL.md` 315, 319; `CHANGELOG.md` 9-18, 132-134 | **Valid observation, closed in v2.2.2.** The "local-first / no external API" claim did contradict the cloud transport. Wording corrected. |
| Session Persistence (cron) | `README.md` ~246 | **False positive.** Prose describing the documented nightly cron, not the skill installing one. The skill ships no installer. |
| Autonomous Decision Making (×2) | `auto_archive.py` ~128, `consolidate_advisor.py` ~534 | **False positive.** The quoted `if not sys.stdin.isatty(): return` *is* the human-in-the-loop guard. |

### 1.5 Round 7 — the one real finding: disclosure that contradicted itself

**File:** `trace_extractor.py`
**Scanner class:** none — the scanner reported the *cloud send* (§2.4); this was found
while fixing it.

`llm_destination()`, whose only job is to tell the operator where memory content is
about to go, read the key from `os.environ["OLLAMA_API_KEY"]` alone. The sender,
`get_ollama_api_key()`, also resolves the key from `.secrets/ollama.json` and
`openclaw.json`. With the key in the secrets file — the common deployment — the
banner announced a *local fallback* and the content then went to `ollama.com`.

The warning was therefore wrong in precisely the configuration it was written to
cover, and a reader who checked the banner would draw the wrong conclusion with
confidence. That is worse than no disclosure.

**Fix (v2.2.2):** both paths share `_find_ollama_api_key_sources()`, one precedence
order, returning `(source, key)`. The banner names the source; `TRACE_LLM_LOCAL_ONLY=1`
short-circuits before any lookup.

**Verified:** with a key only in `.secrets/ollama.json`, the function now returns
`('cloud', '… key from secrets-file …')`; with `TRACE_LLM_LOCAL_ONLY=1`,
`('local', '… forced by TRACE_LLM_LOCAL_ONLY')`.

---

## 3. Standing recommendations for the scanner configuration

1. Exclude markdown (`*.md`) from behavioural/taint analysis — documentation of a
   fix is not the fix's absence.
2. Do not treat regex-literal strings inside deny-lists as credential access.
3. Treat import-time-only validation as a distinct rule from runtime validation;
   several findings in this project were of that exact shape, and are now closed.
4. Do not treat a URL validated against a loopback allowlist as a network
   exfiltration sink — local inference endpoints are local by construction.
5. A disclosure that names the wrong destination is itself a defect. If the code
   prints where data goes, trace the printed value to the same source the sender
   uses; a banner is only a control if it cannot disagree with the transport.
6. Session-persistence rules should require an *installer* (cron writer, systemd
   unit, shell-rc append) — prose that documents a cron table is not persistence.
