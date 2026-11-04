#!/usr/bin/env python3
"""
Conflict Resolution — fact lifecycle & superseding for the memory toolkit.

Implements the four-step consistency pipeline from the v2.2.0 spec:

  1. Atomic extraction   — a new message yields independent unit facts
  2. Targeted retrieval  — for each new fact, pull only *active* facts on the
                           same subject/domain (hybrid: FTS5 lexical + vector)
  3. Relation classification — NLI-style arbitration via a local LLM:
                           CONTRADICTION | REDUNDANT | COMPATIBLE
  4. State update        — preserve traceability, never hard-delete:
                           CONTRADICTION -> old fact becomes `superseded`
                           REDUNDANT     -> refresh `last_confirmed`/confidence
                           COMPATIBLE    -> insert as `active`

Design constraints (inherited from the rest of the toolkit):
  * Local-first: the LLM endpoint is validated as loopback-only, exactly like
    hybrid_search.py, so no memory content ever leaves the machine.
  * Read-only by default: analysis prints what it *would* do; mutations require
    an explicit --apply (and --force in non-interactive mode).
  * Deterministic fallback: with no LLM available, classification degrades to a
    conservative lexical heuristic that NEVER auto-supersedes on weak signal.

Usage:
    # Analyse a single candidate fact against active memory
    python3 conflict_resolver.py check "On a migré la BDD sur MySQL 8" \
        --subject serveur_prod

    # Arbitrate a JSONL batch (one fact per line), dry-run
    python3 conflict_resolver.py arbitrate facts.jsonl

    # Apply resolutions (mutates DB: supersede / confirm)
    python3 conflict_resolver.py arbitrate facts.jsonl --apply

    # Maintenance: list stale/superseded/disputed facts
    python3 conflict_resolver.py lifecycle --status superseded

    # Interactive resolution of blocked facts
    python3 conflict_resolver.py pending
    python3 conflict_resolver.py resolve 42 --confirm
    python3 conflict_resolver.py resolve 42 --reject --replacement "corrected fact"
"""

import argparse
import json
import os
import re
import sqlite3
import time
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ─── Config ───────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
DB_PATH = os.environ.get(
    "MEMORY_DB", str(_HERE / "agent_memory.db")
)
WORKSPACE = Path(
    os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))
).resolve()

ALLOWED_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1"}

# v5.4-P1: bounded wait for the write lock. Capture and the nightly cron can
# legitimately overlap; both must survive a transient lock.
LOCK_WAIT_SECONDS = 10.0
LOCK_BACKOFF_SECONDS = 0.25


def get_safe_ollama_url(env_var: str, default: str) -> str:
    """Validate and return an OLLAMA URL, restricting it to loopback only.

    Same guard as hybrid_search.py: the destination is fixed to localhost at
    import time, so a remote endpoint cannot receive memory content.
    """
    raw_url = os.environ.get(env_var, default)
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid scheme for {env_var}: {parsed.scheme}")
    hostname = parsed.hostname or ""
    if hostname not in ALLOWED_OLLAMA_HOSTS:
        raise ValueError(
            f"Host '{hostname}' not allowed for {env_var}. Only localhost is permitted."
        )
    return raw_url


OLLAMA_URL = get_safe_ollama_url("OLLAMA_URL", "http://localhost:11434")
# SECURITY: OLLAMA_GEN_URL is read through the SAME loopback guard as OLLAMA_URL.
# An earlier version used `os.environ.get("OLLAMA_GEN_URL", ...)` directly, which
# bypassed the check: a crafted environment could point the generation endpoint
# at a remote host while this module's docstring still claimed "fixed to localhost
# at import time", and each classification POSTs two memory facts to it. The
# generated URL is derived from the already-validated OLLAMA_URL, so the default
# remains loopback and any explicit override must pass the allowlist too.
OLLAMA_GEN_URL = get_safe_ollama_url("OLLAMA_GEN_URL", OLLAMA_URL.rstrip("/") + "/api/generate")
# Model resolution is centralised in llm_resolution.py (v4.0). The old local
# PREFERRED_MODELS tuple + resolve_llm_model() were a copy of consolidate_advisor's,
# and that duplication broke arbitration: a hard-coded "glm-5.2" answered 404,
# classify_relation fell through to the conservative heuristic, and every fact
# came back COMPATIBLE — arbitration was dead on arrival. Single source of truth
# is now agents.defaults.model.primary, read from the gateway config.
# Import is tolerant so this script still runs if the module is absent.
try:
    from llm_resolution import resolve_llm_model as _resolve_llm_model

except ImportError:  # pragma: no cover - defensive: keep arbitration runnable
    def _resolve_llm_model(explicit=None, override_env=None,
                           require_local_presence=False):
        return explicit or os.environ.get("CONFLICT_LLM_MODEL") or "qwen2.5:7b"


def resolve_llm_model(explicit: str | None = None) -> str:
    """Pick the model to use, from llm_resolution (single source of truth)."""
    return _resolve_llm_model(explicit=explicit, override_env="CONFLICT_LLM_MODEL")


LLM_MODEL = resolve_llm_model(
    os.environ.get("CONFLICT_LLM_MODEL") or os.environ.get("TRACE_LLM_MODEL")
)

# Confidence below which a weak contradiction is escalated to `disputed`
# instead of being auto-superseded (a wrong supersede destroys a truth).
DISPUTE_CONFIDENCE_FLOOR = 0.6

# Relation labels
CONTRADICTION = "CONTRADICTION"
REDUNDANT = "REDUNDANT"
COMPATIBLE = "COMPATIBLE"


# ─── DB access ────────────────────────────────────────────────────────────────

def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise SystemExit(
            f"❌ DB not found: {db_path}\n"
            "   Run 'hybrid_search.py init' first (or set MEMORY_DB)."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # C1/C3 — same trio as HybridMemoryStore: the resolver writes concurrently
    # with the async capture hook and RRF reads, so it needs the same guard.
    conn.execute("PRAGMA busy_timeout=10000")  # v5.4-P1: capture+arbitration overlap
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_lifecycle_columns(conn: sqlite3.Connection) -> list[str]:
    """Idempotent migration: add lifecycle columns to an older DB.

    SQLite has no "ADD COLUMN IF NOT EXISTS"; we inspect PRAGMA table_info and
    add only the missing ones. Returns the list of columns actually added.
    v3.4 adds `superseded_at` (point-in-time retrieval: when a row STOPPED being
    active — see schema.sql).
    """
    wanted = {
        "subject": "TEXT DEFAULT NULL",
        "status": "TEXT DEFAULT 'active'",
        "confidence": "REAL DEFAULT 1.0",
        "valid_from": "TEXT DEFAULT NULL",
        "source_context": "TEXT DEFAULT NULL",
        "last_confirmed": "TEXT DEFAULT NULL",
        "superseded_at": "TEXT DEFAULT NULL",
        # v5.4-P1 (P3): what kind of memory this is, and where it sits in its
        # lifecycle. An intention must never be mistaken for an execution.
        "kind": "TEXT DEFAULT 'fact'",
        "lifecycle": "TEXT DEFAULT NULL",
    }
    existing = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    added = []
    for col, decl in wanted.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl}")
            added.append(col)
    if added:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject)")
        # v3.4: as-of queries filter on superseded_at alongside status.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_superseded_at "
                     "ON memories(superseded_at)")
        conn.commit()
    return added


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── Step 1: atomic extraction (lightweight, no LLM) ──────────────────────────
#
# Full extraction is the job of trace-extractor.py --llm. This helper exists so
# `check`/`arbitrate` accept a free-form sentence and still behave: it splits a
# compound statement on punctuation and coordinating conjunctions, yielding
# unit claims. Kept deliberately dumb and deterministic.

# ─── Split rules ────────────────────────────────────────────────────────────
# A period is only treated as a sentence boundary, never as a cut inside a
# number: version strings (24.04), decimals (3.14) and percentages (100% /
# 12,5 %) must survive intact. Bug seen 2026-10-05: "Ubuntu 24.04" was split
# into "Ubuntu 24" + "04", and "...à 100% atteint" lost its tail.
#
# A period splits only when NOT sandwiched between digits on both sides.
_SPLIT_RE = re.compile(
    r"\s*(?:;"                       # semicolons always split
    r"|(?<![0-9])\.(?![0-9])"       # a dot, but not 24.04 / 3.14
    r"|,?\s+(?:et|and|puis|then)\s+"  # coordinating conjunctions
    r")\s*",
    re.IGNORECASE,
)


def extract_atomic(text: str) -> list[str]:
    """Split a compound statement into unit facts.

    Numbers are protected: version strings, decimals and percentages are kept
    whole (see _SPLIT_RE). Surrounding whitespace and trailing punctuation are
    trimmed, but a trailing '%' is preserved.
    """
    parts = [
        p.strip(" .;")
        for p in _SPLIT_RE.split(text or "")
        if p.strip(" .;")
    ]
    # A split may have orphaned a leading fragment (e.g. "04" after a bad
    # decimal cut). Re-attach any part that is purely numeric to the previous
    # one, so "Ubuntu 24" + "04" becomes "Ubuntu 24.04" again.
    merged: list[str] = []
    for part in parts:
        if merged and re.fullmatch(r"[0-9][0-9.,]*", part):
            merged[-1] = f"{merged[-1]}.{part}"
        else:
            merged.append(part)
    return merged or ([text.strip()] if (text or "").strip() else [])


# ─── Step 2: targeted retrieval of concurrent active facts ────────────────────

def normalise_subject(text: str) -> str | None:
    """Extract the LOGICAL entity a sentence is about (GO-D P2).

    "L'utilisateur a commence la migration de ses embeddings vers X" is a
    statement about the *embeddings*, not about the user. Comparing grammatical
    subjects therefore misses contradictions between two sentences that talk
    about the same entity. This returns the logical subject so both rows land
    on the same key.

    Order matters:
      1. "ses X" / "son X"  -> X   (possessive refers to the real object)
      2. "les X" / "le X"   -> X
      3. first capitalised token, but only if it is not a sentence opener
    """
    if not text:
        return None
    t = text.strip()

    m = re.search(r"\b(?:de\s+)?(?:ses|son|sa)\s+([a-z\u00e0-\u00ff][\w-]{3,})", t.lower())
    if m:
        return m.group(1)

    m = re.match(r"\s*(?:les?|la|l'|l\u2019)\s+([a-z\u00e0-\u00ff][\w-]{3,})", t.lower())
    if m:
        return m.group(1)

    STOP = {"l'utilisateur", "utilisateur", "le", "la", "les", "je", "il", "on", "c'est"}
    for tok in re.findall(r"\b([A-Z][A-Za-z0-9_-]{2,})\b", t):
        if tok.lower() not in STOP:
            return tok.lower()
    return None


def fetch_active_facts(conn: sqlite3.Connection, subject: str | None,
                       content: str, limit: int = 5) -> list[dict]:
    """Pull candidate facts, restricted to status='active'.

    Prefers same-subject facts (cheap, precise). Falls back to FTS5 lexical
    overlap on the content when no subject is given or nothing matched, so a
    contradiction phrased with different words still surfaces.
    """
    rows: list[sqlite3.Row] = []
    # GO-D P2: fall back to the LOGICAL subject when the grammatical one is a
    # generic role word ("utilisateur"), otherwise a correction phrased with a
    # different grammatical subject is never compared to the original.
    lookup_subject = subject
    if not lookup_subject or str(lookup_subject).lower() in (
        "utilisateur", "user", "l_utilisateur",
    ):
        lookup_subject = normalise_subject(content) or subject

    # GO-D P2: searching with the logical subject is useless if the stored rows
    # still carry the grammatical one. The lookup must therefore also match the
    # raw subject, so an older row stored as 'utilisateur' is still found when
    # the new fact normalises to 'embeddings'.
    candidates_for = [lookup_subject]
    if subject and subject != lookup_subject:
        candidates_for.append(subject)

    if lookup_subject:
        placeholders = ",".join("?" * len(candidates_for))
        rows = conn.execute(
            f"SELECT * FROM memories WHERE status='active' AND subject IN ({placeholders}) "
            "ORDER BY updated_at DESC LIMIT ?",
            (*candidates_for, limit),
        ).fetchall()

    # GO-C P1 idempotence: the same fact is often re-extracted under a different
    # subject ("kiddo" vs "chat"), so a subject-only lookup finds nothing and the
    # fact is inserted twice. Always ALSO run the lexical search and merge, so a
    # re-run of the same transcript cannot create a duplicate.
    if subject:
        terms = [t for t in re.findall(r"\w{4,}", content or "")][:6]
        if terms:
            fts_q = " OR ".join(f'"{t}"' for t in terms)
            try:
                extra = conn.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? AND m.status='active' "
                    "ORDER BY bm25(memories_fts) LIMIT ?",
                    (fts_q, limit),
                ).fetchall()
                seen = {r["id"] for r in rows}
                rows = list(rows) + [r for r in extra if r["id"] not in seen]
            except sqlite3.OperationalError:
                pass

    if not rows:
        # Lexical fallback: OR the significant terms against FTS5.
        terms = [t for t in re.findall(r"\w{4,}", content or "")][:6]
        if terms:
            fts_q = " OR ".join(f'"{t}"' for t in terms)
            try:
                rows = conn.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? AND m.status='active' "
                    "ORDER BY bm25(memories_fts) LIMIT ?",
                    (fts_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status='active' "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    return [dict(r) for r in rows]


# ─── Step 3: relation classification ──────────────────────────────────────────

ARBITER_SYSTEM = (
    "Tu es un arbitre de consistance logique pour une mémoire d'agent. "
    "Compare l'énoncé EXISTANT et l'énoncé NOUVEAU. "
    "Réponds UNIQUEMENT par un objet JSON avec les clés "
    '"relation" (CONTRADICTION | REDUNDANT | COMPATIBLE), '
    '"confidence" (0.0 à 1.0) et "reasoning" (une phrase).'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def classify_relation(existing: str, new: str, use_llm: bool = True) -> dict:
    """Classify the relation between two facts.

    Returns {relation, confidence, reasoning, engine}. With no usable LLM, falls
    back to `heuristic_relation()` which is intentionally conservative.
    """
    if use_llm:
        prompt = (
            f"{ARBITER_SYSTEM}\n\n"
            f"EXISTANT: {existing}\n"
            f"NOUVEAU: {new}\n\n"
            "JSON:"
        )
        payload = json.dumps({
            "model": LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }).encode()
        try:
            req = urllib.request.Request(
                OLLAMA_GEN_URL, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read()).get("response", "")
            m = _JSON_RE.search(body)
            if m:
                parsed = json.loads(m.group(0))
                rel = str(parsed.get("relation", "")).upper().strip()
                if rel in (CONTRADICTION, REDUNDANT, COMPATIBLE):
                    conf = float(parsed.get("confidence", 0.75))
                    return {
                        "relation": rel,
                        "confidence": max(0.0, min(1.0, conf)),
                        "reasoning": str(parsed.get("reasoning", ""))[:300],
                        "engine": f"llm:{LLM_MODEL}",
                    }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
            pass  # fall through to heuristic

    return heuristic_relation(existing, new)


# Conservative deterministic fallback. It only ever *proposes* a contradiction
# when both statements look like assertions about the same predicate with
# different objects — and it never exceeds 0.5 confidence, so callers escalate
# to `disputed` rather than silently destroying a fact.
_ASSERT_RE = re.compile(
    r"^\s*(?:le|la|les|l'|l\u2019|on a migr[ée]?|c'est|il y a|the|the main)?\s*"
    # GO-C P2: the subject must not swallow a trailing "s" from the next word
    # ('Les embeddings' -> subject 's embeddings'). Requiring a word boundary and
    # stopping at the article boundary fixes the false split that made two
    # statements about the same entity look unrelated.
    r"(?<!\w)(?P<subject>[\w\s\-']{2,40}?)(?=\s)\s+"
    # GO-C P2: the copula list was too narrow. "a commence", "est passe a",
    # "migre vers", "va vers" all state a value and must be compared, otherwise
    # a correction is classified COMPATIBLE and both rows stay active forever.
    r"(?:est|sont|a commenc[eé]|ont commenc[eé]|a migr[eé]|ont migr[eé]|"
    r"migre vers|migrent vers|va vers|vont vers|passe [aà]|passent [aà]|"
    r"tourne sur|tournent sur|tourne sous|tournent sous|utilise|utilisent|"
    r"is|are|runs on|uses|"
    r"devient|deviennent|becomes)\s+"
    r"(?P<object>.+?)\s*$",
    re.IGNORECASE,
)


def heuristic_relation(existing: str, new: str) -> dict:
    a, b = existing.strip().lower(), new.strip().lower()
    if a == b:
        return {"relation": REDUNDANT, "confidence": 1.0,
                "reasoning": "identical text", "engine": "heuristic"}

    ma, mb = _ASSERT_RE.match(a), _ASSERT_RE.match(b)
    if ma and mb:
        subj_a = ma.group("subject").strip()
        subj_b = mb.group("subject").strip()
        obj_a = ma.group("object").strip()
        obj_b = mb.group("object").strip()
        # Same-looking subject, different value -> possible contradiction.
        if (subj_a in subj_b or subj_b in subj_a) and obj_a != obj_b:
            return {"relation": CONTRADICTION, "confidence": 0.5,
                    "reasoning": f"same predicate, different value ('{obj_a}' vs '{obj_b}')",
                    "engine": "heuristic"}
        if subj_a in subj_b or subj_b in subj_a:
            return {"relation": REDUNDANT, "confidence": 0.6,
                    "reasoning": "same predicate, same value", "engine": "heuristic"}

    return {"relation": COMPATIBLE, "confidence": 0.5,
            "reasoning": "no confident relation detected", "engine": "heuristic"}


# ─── Step 4: state update (traceable, never destructive) ──────────────────────

def apply_resolution(conn: sqlite3.Connection, existing_id: int, verdict: dict,
                     new_fact: str, subject: str | None,
                     source_context: str | None, source: str = "conflict-resolver",
                     kind: str = "fact", lifecycle: str | None = None) -> dict:
    """Apply one classified resolution. Returns an action summary.

    CONTRADICTION -> mark the old fact superseded (status + superseded_by),
                     then insert the new fact as active.
    REDUNDANT     -> no new row; refresh last_confirmed, bump confidence.
    COMPATIBLE    -> insert the new fact as active.
    `disputed`    -> old fact flagged disputed, new fact inserted active.
    """
    rel = verdict["relation"]
    conf = verdict.get("confidence", 0.5)
    ts = now_iso()
    action = {"relation": rel, "confidence": conf, "existing_id": existing_id}

    # C2 — the whole resolution is ONE atomic unit. Without this, a crash after the
    # UPDATE that sets status='superseded' but before its successor is inserted
    # would leave a fact superseded by nothing: silently lost. BEGIN IMMEDIATE
    # takes the write lock up front so the sequence cannot interleave with the
    # async capture hook.
    # v5.4-P1 resilience: a concurrent writer (live capture + nightly cron) would
    # otherwise crash the whole pipeline on `database is locked`, losing every
    # fact already extracted for the turn. Retry with bounded backoff instead.
    # GO-C P0: an implicit transaction may already be open (the INSERT that
    # created the first fact, or any DML since the last commit). BEGIN IMMEDIATE
    # then raises "cannot start a transaction within a transaction". Commit the
    # pending work first, then take the explicit lock.
    if conn.in_transaction:
        conn.commit()

    lock_deadline = time.time() + LOCK_WAIT_SECONDS
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "within a transaction" in msg:
                conn.commit()
                continue
            if "locked" not in msg or time.time() >= lock_deadline:
                raise
            time.sleep(LOCK_BACKOFF_SECONDS)

    try:

        if rel == CONTRADICTION:
            if conf < DISPUTE_CONFIDENCE_FLOOR:
                # Weak signal: never destroy a truth on a guess.
                conn.execute(
                    "UPDATE memories SET status='disputed', updated_at=? WHERE id=?",
                    (ts, existing_id),
                )
                new_id = _insert_fact(conn, new_fact, subject, "active", conf,
                                      source, source_context, ts, kind=kind, lifecycle=lifecycle)
                action.update(action="disputed", new_id=new_id,
                              note="weak contradiction -> old=disputed, new=active")
            else:
                new_id = _insert_fact(conn, new_fact, subject, "active", conf,
                                      source, source_context, ts, kind=kind, lifecycle=lifecycle)
                conn.execute(
                    "UPDATE memories SET status='superseded', superseded_by=?, "
                    "updated_at=?, superseded_at=? WHERE id=?",
                    (new_id, ts, ts, existing_id),
                )
                action.update(action="superseded", new_id=new_id,
                              note="old marked superseded, new inserted active")

        elif rel == REDUNDANT:
            row = conn.execute(
                "SELECT confidence FROM memories WHERE id=?", (existing_id,)
            ).fetchone()
            old_conf = (row["confidence"] if row and row["confidence"] is not None else 0.5)
            conn.execute(
                "UPDATE memories SET last_confirmed=?, confidence=?, updated_at=? WHERE id=?",
                (ts, min(1.0, old_conf + 0.1), ts, existing_id),
            )
            action.update(action="confirmed", note="no duplicate; confidence bumped")

        else:  # COMPATIBLE / ADDITION
            new_id = _insert_fact(conn, new_fact, subject, "active", conf,
                                  source, source_context, ts, kind=kind, lifecycle=lifecycle)
            action.update(action="added", new_id=new_id, note="compatible addition")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return action


EXIT_PARTIAL_FAILURE = 3  # distinct from 1 (usage error) and 2 (crash)


def _edge_uid(payload: dict) -> str:
    """Stable identifier for one retry entry.

    Derived from the fact's own identity, never from wall-clock time, so the
    same unpersisted fact queued twice collapses to one entry (GO-D P0).
    """
    import hashlib as _h
    key = "\x1f".join([
        str(payload.get("content") or ""),
        str(payload.get("subject") or ""),
        str(payload.get("source_context") or ""),
        str(payload.get("kind") or "fact"),
        str(payload.get("lifecycle") or ""),
    ])
    return _h.sha256(key.encode("utf-8")).hexdigest()[:16]


def _write_retry_queue(failed: list[dict], db_path: str) -> str:
    """Persist the facts that could not be written — COMPLETE, not truncated.

    GO-D P0: the previous version kept content[:80] and dropped subject,
    provenance, kind and lifecycle. A queue like that cannot be replayed: the
    fact comes back mangled and its source is gone. This version keeps the full
    payload plus a stable uid, and de-duplicates by uid so a second lock does
    not append the same entry twice.

    The write is atomic (temp + rename): an interruption during the queue
    write must not leave a half-line that breaks JSONL parsing on replay.
    """
    import datetime as _dt
    qdir = Path(db_path).parent
    queue = qdir / "retry_queue.jsonl"
    ts = _dt.datetime.now().isoformat(timespec="seconds")

    existing = {}
    if queue.exists():
        for line in queue.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue          # tolerate a corrupted line rather than abort
            uid = obj.get("uid") or _edge_uid(obj)
            existing[uid] = obj

    for f in failed:
        payload = {
            "content": f.get("content") or "",
            "subject": f.get("subject"),
            "source_context": f.get("source_context"),
            "kind": f.get("kind") or "fact",
            "lifecycle": f.get("lifecycle"),
            "confidence": f.get("confidence"),
            "queued_at": ts,
            "db": str(db_path),
        }
        uid = _edge_uid(payload)
        if uid in existing:
            existing[uid]["last_seen_at"] = ts
            existing[uid]["attempts"] = int(existing[uid].get("attempts", 1)) + 1
        else:
            payload["uid"] = uid
            payload["attempts"] = 1
            existing[uid] = payload

    tmp = queue.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for obj in existing.values():
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, queue)
    return str(queue)


def _insert_fact(conn: sqlite3.Connection, content: str, subject: str | None,
                 status: str, confidence: float, source: str,
                 source_context: str | None, ts: str,
                 kind: str = "fact", lifecycle: str | None = None) -> int:
    """Insert a new fact row (FTS5/vec triggers handle the rest).

    Note: no embedding is written here. Vector indexing of new facts is the
    job of a subsequent `hybrid_search.py add`/`index` pass — the arbitrator
    stays LLM-embedding-free so it can run offline.
    """
    cur = conn.execute(
        "INSERT INTO memories (content, category, layer, source, score, subject, "
        "status, confidence, valid_from, source_context, last_confirmed, updated_at, "
        "kind, lifecycle) "
        "VALUES (?, 'fact', 'semantic', ?, 0.5, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (content, source, subject, status, confidence, ts, source_context, ts, ts,
         kind, lifecycle),
    )
    return cur.lastrowid


# ─── High-level flows ─────────────────────────────────────────────────────────

def check_fact(conn: sqlite3.Connection, text: str, subject: str | None,
               use_llm: bool = True, apply: bool = False,
               split: bool = True, source_context: str | None = None,
               source: str = "conflict-resolver",
               kind: str = "fact", lifecycle: str | None = None) -> list[dict]:
    """Run the full pipeline for one (possibly compound) new statement.

    split=False trusts the caller to have supplied an already-atomic fact
    (e.g. auto_capture.py's LLM extractor). This avoids re-splitting on
    punctuation that is part of a value, such as 'Ubuntu 24.04'.
    """
    results = []
    for fact in (extract_atomic(text) if split else [text.strip()]):
        candidates = fetch_active_facts(conn, subject, fact)
        best = None
        for cand in candidates:
            verdict = classify_relation(cand["content"], fact, use_llm=use_llm)
            if best is None or _rel_priority(verdict) > _rel_priority(best[1]):
                best = (cand, verdict)
        if best is None:
            action = None
            if apply:
                # v5.4-P1: a fact with no candidate must still be INSERTED.
                # Returning action=None meant "first fact ever" was silently
                # dropped: nothing was ever persisted for a new subject.
                action = {"action": "added", "new_id": _insert_fact(
                    conn, fact, subject, "active", 0.5, source,
                    source_context or fact, now_iso(),
                    kind=kind, lifecycle=lifecycle)}
            results.append({"fact": fact, "verdict": {
                "relation": COMPATIBLE, "confidence": 0.5,
                "reasoning": "no active candidate", "engine": "n/a"},
                "existing_id": None, "action": action})
            continue
        cand, verdict = best
        action = None
        if apply:
            action = apply_resolution(conn, cand["id"], verdict, fact, subject,
                                      source_context=source_context or text,
                                      source=source,
                                      kind=kind, lifecycle=lifecycle)
        results.append({
            "fact": fact, "existing_id": cand["id"],
            "existing_content": cand["content"],
            "verdict": verdict, "action": action,
        })
    return results


def _rel_priority(v: dict) -> float:
    """Prefer contradictions, then redundancies, then compatibility."""
    rank = {CONTRADICTION: 2, REDUNDANT: 1, COMPATIBLE: 0}.get(v["relation"], 0)
    return rank + v.get("confidence", 0.0) / 100.0


# ─── CLI ──────────────────────────────────────────────────────────────────────

def cmd_check(args):
    conn = connect(args.db)
    added = ensure_lifecycle_columns(conn)
    if added:
        print(f"🔧 migrated schema: added {', '.join(added)}")
    res = check_fact(conn, args.text, args.subject,
                     use_llm=not args.no_llm, apply=args.apply,
                     split=not args.no_split)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if args.apply and conn.in_transaction:
        conn.commit()
    if not args.apply:
        print("\n(analyse only — pass --apply to mutate the DB)")
    conn.close()


def cmd_arbitrate(args):
    conn = connect(args.db)
    ensure_lifecycle_columns(conn)
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"❌ facts file not found: {path}")
    facts = []
    malformed = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            facts.append(obj if isinstance(obj, dict) else {"content": str(obj)})
        except json.JSONDecodeError:
            # GO-D P4: a non-JSON line is a CORRUPT buffer, not a fact. The old
            # code promoted the raw text to {"content": line}, so a truncated
            # write or a stray log line became a memory entry — "NOT JSON AT ALL"
            # was inserted verbatim by the tests. Corrupt lines are counted and
            # skipped; they never reach the database.
            malformed += 1
            continue

    if not args.apply and not args.force:
        print(f"⚠️  DRY-RUN: {len(facts)} fact(s) will be analysed, no mutation.")
    elif not args.apply:
        print("⚠️  --force without --apply has no effect (analysis is already safe).")

    if malformed:
        print(f"⚠️  BUFFER: {malformed} malformed line(s) skipped (not facts).")
    summary = {"added": 0, "superseded": 0, "disputed": 0, "confirmed": 0}
    lock_failures = 0
    pending = []
    for obj in facts:
        content = obj.get("content") or obj.get("fact") or ""
        subject = obj.get("subject") or args.subject
        # v5.4-P1 provenance: prefer the message-level context carried by the
        # buffer entry (message_id/session_id) over the raw fact text.
        prov = obj.get("source_context")
        if not prov and obj.get("message_id"):
            prov = ("session=%s message=%s"
                    % (obj.get("session_id") or "-", obj["message_id"]))
        try:
            results = check_fact(conn, content, subject,
                                 use_llm=not args.no_llm, apply=args.apply,
                                 split=not args.no_split,
                                 source_context=prov or content,
                                 kind=obj.get("kind") or "fact",
                                 lifecycle=obj.get("lifecycle"))
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            # v5.4-P1: a locked DB must not destroy the whole run. Keep the fact
            # pending, finish the others, and report it explicitly at the end.
            lock_failures += 1
            pending.append({
                "content": content,
                "subject": subject,
                "source_context": prov or content,
                "kind": obj.get("kind") or "fact",
                "lifecycle": obj.get("lifecycle"),
                "confidence": obj.get("confidence"),
            })
            print(f"  [LOCKED] {content[:70]}  ← retried, still locked")
            continue
        for r in results:
            act = (r.get("action") or {}).get("action")
            if act in summary:
                summary[act] += 1
            tag = act or r["verdict"]["relation"].lower()
            print(f"  [{tag}] {r['fact'][:70]}  ← {r['verdict']['reasoning']}")
    print(f"\nSummary: {summary}")
    if lock_failures:
        # v5.4-P1 (GO-C P0): a non-persisted fact is NEVER a success.
        # 1. write a durable retry queue so nothing is lost
        # 2. return a non-zero exit code so callers cannot mistake this for OK
        queue = _write_retry_queue(pending, args.db)
        print(f"⚠️  FAILED: {lock_failures} fact(s) NOT persisted (database busy).")
        print(f"   retry queue: {queue}")
        print(f"   re-run: conflict_resolver.py --db {args.db} arbitrate {queue} --apply")
        if not args.apply:
            print("(dry-run — re-run with --apply to persist)")
        conn.close()
        return EXIT_PARTIAL_FAILURE
    if args.apply:
        # GO-C P3: apply_resolution commits per fact, but a trailing BEGIN
        # IMMEDIATE from the next iteration can leave one open. Commit the
        # remainder explicitly so nothing is lost at close().
        if conn.in_transaction:
            conn.commit()
    if not args.apply:
        print("(dry-run — re-run with --apply to persist)")
    conn.close()
    return 0


def cmd_lifecycle(args):
    conn = connect(args.db)
    ensure_lifecycle_columns(conn)
    where = "1=1"
    params: list = []
    if args.status:
        where += " AND status=?"
        params.append(args.status)
    rows = conn.execute(
        f"SELECT id, content, subject, status, confidence, superseded_by, "
        f"valid_from, last_confirmed, source FROM memories WHERE {where} "
        f"ORDER BY updated_at DESC LIMIT ?",
        (*params, args.limit),
    ).fetchall()
    if not rows:
        print("(no rows)")
    for r in rows:
        print(f"#{r['id']:<6} [{r['status']:<10}] subj={r['subject'] or '-'!s:<18} "
              f"conf={r['confidence']} sup_by={r['superseded_by'] or '-'}")
        print(f"        {r['content'][:100]}")
    # Counts by status
    counts = conn.execute(
        "SELECT status, COUNT(*) c FROM memories GROUP BY status ORDER BY c DESC"
    ).fetchall()
    print("\nBy status: " + ", ".join(f"{r['status']}={r['c']}" for r in counts))
    conn.close()


def cmd_pending(args):
    """Surface unresolved `disputed` facts for the agent/user to adjudicate.

    This is the *tool* half of "resolve at the next relevant turn": the skill
    cannot decide when a turn is relevant, but it can answer "what is waiting
    for a human?" cheaply and deterministically, so the agent can call it
    contextually. Compact one-line-per-fact output, --json for programmatic use.
    """
    conn = connect(args.db)
    ensure_lifecycle_columns(conn)
    rows = conn.execute(
        "SELECT id, content, subject, confidence, updated_at, source_context "
        "FROM memories WHERE status='disputed' ORDER BY updated_at DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
    elif not rows:
        print("✅ no disputed facts pending")
    else:
        print(f"⚠️  {len(rows)} disputed fact(s) awaiting adjudication:")
        for r in rows:
            print(f"  #{r['id']}  subj={r['subject'] or '-'}  conf={r['confidence']}")
            print(f"      {r['content'][:110]}")
            if r["source_context"]:
                print(f"      ctx: {str(r['source_context'])[:100]}")
        print("\nResolve with:  conflict_resolver.py resolve <id> --confirm|--reject")
    conn.close()


def cmd_resolve(args):
    """Explicitly lift the ambiguity on a `disputed` fact.

    --confirm  : the current wording stands -> status back to `active`, and the
                 contradiction that caused the dispute (if it is still present)
                 is superseded by it, so retrieval stops showing both.
    --reject   : the wording was wrong -> superseded, with an optional
                 --replacement inserted as the new active fact.

    Only rows currently in `disputed` are eligible: resolving an `active` or
    already-`superseded` row is refused, so this command cannot be used to
    rewrite lifecycle state by accident.
    """
    conn = connect(args.db)
    ensure_lifecycle_columns(conn)
    row = conn.execute("SELECT * FROM memories WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(json.dumps({"resolved": False, "reason": f"id {args.id} not found"}))
        conn.close()
        return 1
    if row["status"] != "disputed":
        print(json.dumps({"resolved": False,
                          "reason": f"id {args.id} is '{row['status']}', not 'disputed'"}))
        conn.close()
        return 1

    ts = now_iso()
    if args.confirm:
        conn.execute(
            "UPDATE memories SET status='active', confidence=1.0, last_confirmed=?, "
            "updated_at=? WHERE id=?",
            (ts, ts, args.id),
        )
        # Supersede any *other* active or disputed claim on the same subject that
        # the confirmed fact now beats, so the pair cannot both stay visible.
        # `IS` handles the NULL-subject case; explicit `= ?` for non-NULL.
        if row["subject"] is not None:
            rivals = conn.execute(
                "SELECT id FROM memories WHERE status IN ('active','disputed') "
                "AND id!=? AND subject=?",
                (args.id, row["subject"]),
            ).fetchall()
        else:
            rivals = conn.execute(
                "SELECT id FROM memories WHERE status IN ('active','disputed') "
                "AND id!=? AND subject IS NULL",
                (args.id,),
            ).fetchall()
        superseded = []
        for rival in rivals:
            conn.execute(
                "UPDATE memories SET status='superseded', superseded_by=?, updated_at=?, "
                "superseded_at=? WHERE id=?",
                (args.id, ts, ts, rival["id"]),
            )
            superseded.append(rival["id"])
        action = "confirmed"
        result = {"resolved": True, "id": args.id, "action": action,
                  "superseded_rivals": superseded}
    else:  # --reject
        conn.execute(
            "UPDATE memories SET status='superseded', updated_at=?, superseded_at=? WHERE id=?",
            (ts, ts, args.id),
        )
        new_id = None
        if args.replacement:
            new_id = _insert_fact(conn, args.replacement, row["subject"], "active",
                                  1.0, "user-resolution", args.replacement, ts)
            conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, args.id))
        result = {"resolved": True, "id": args.id, "action": "rejected",
                  "replacement_id": new_id}
    conn.commit()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    conn.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Fact conflict resolution — lifecycle, superseding, NLI arbitration"
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Path to agent_memory.db (default: {DB_PATH})")
    sub = parser.add_subparsers(dest="command")

    c = sub.add_parser("check", help="Analyse one candidate fact")
    c.add_argument("text", help="New fact/statement")
    c.add_argument("--subject", help="Entity the fact is about (e.g. serveur_prod)")
    c.add_argument("--no-llm", action="store_true", help="Skip LLM, use lexical heuristic")
    c.add_argument("--apply", action="store_true", help="Persist the resolution (mutates DB)")
    c.add_argument("--no-split", action="store_true",
                   help="Treat the fact as already atomic (no punctuation split)")
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("arbitrate", help="Arbitrate a JSONL batch of facts")
    a.add_argument("file", help="JSONL file, one {content, subject?} per line")
    a.add_argument("--subject", help="Default subject for facts that omit it")
    a.add_argument("--no-llm", action="store_true", help="Skip LLM, use lexical heuristic")
    a.add_argument("--apply", action="store_true", help="Persist resolutions (mutates DB)")
    a.add_argument("--force", action="store_true", help="Required with --apply in non-interactive mode")
    a.add_argument("--no-split", action="store_true",
                   help="Facts are already atomic (no punctuation split)")
    a.set_defaults(func=cmd_arbitrate)

    l = sub.add_parser("lifecycle", help="List facts by lifecycle status")
    l.add_argument("--status", choices=["active", "superseded", "disputed"], help="Filter by status")
    l.add_argument("--limit", type=int, default=30)
    l.set_defaults(func=cmd_lifecycle)

    p = sub.add_parser("pending", help="Surface unresolved `disputed` facts for adjudication")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_pending)

    r = sub.add_parser("resolve", help="Explicitly lift the ambiguity on a `disputed` fact")
    r.add_argument("id", type=int, help="id of the disputed fact")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--confirm", action="store_true", help="The wording stands -> back to active")
    g.add_argument("--reject", action="store_true", help="The wording was wrong -> superseded")
    r.add_argument("--replacement", help="With --reject: corrected fact to insert as active")
    r.set_defaults(func=cmd_resolve)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    # v5.4-P1 (GO-C P0): propagate the subcommand's return code. Before this,
    # `main()` discarded it, so a partially failed run still exited 0 — the
    # operational false success the audit flagged.
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
