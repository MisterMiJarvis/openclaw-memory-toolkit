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
"""

import argparse
import json
import os
import re
import sqlite3
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
OLLAMA_GEN_URL = os.environ.get("OLLAMA_GEN_URL", OLLAMA_URL.rstrip("/") + "/api/generate")
LLM_MODEL = os.environ.get("CONFLICT_LLM_MODEL", os.environ.get("TRACE_LLM_MODEL", "glm-5.2"))

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
    return conn


def ensure_lifecycle_columns(conn: sqlite3.Connection) -> list[str]:
    """Idempotent migration: add v2.2.0 lifecycle columns to an older DB.

    SQLite has no "ADD COLUMN IF NOT EXISTS"; we inspect PRAGMA table_info and
    add only the missing ones. Returns the list of columns actually added.
    """
    wanted = {
        "subject": "TEXT DEFAULT NULL",
        "status": "TEXT DEFAULT 'active'",
        "confidence": "REAL DEFAULT 1.0",
        "valid_from": "TEXT DEFAULT NULL",
        "source_context": "TEXT DEFAULT NULL",
        "last_confirmed": "TEXT DEFAULT NULL",
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

_SPLIT_RE = re.compile(r"\s*(?:;|\.|,?\s+(?:et|and|puis|then)\s+)\s*", re.IGNORECASE)


def extract_atomic(text: str) -> list[str]:
    """Split a compound statement into unit facts."""
    parts = [p.strip(" .;") for p in _SPLIT_RE.split(text or "") if p.strip(" .;")]
    return parts or ([text.strip()] if (text or "").strip() else [])


# ─── Step 2: targeted retrieval of concurrent active facts ────────────────────

def fetch_active_facts(conn: sqlite3.Connection, subject: str | None,
                       content: str, limit: int = 5) -> list[dict]:
    """Pull candidate facts, restricted to status='active'.

    Prefers same-subject facts (cheap, precise). Falls back to FTS5 lexical
    overlap on the content when no subject is given or nothing matched, so a
    contradiction phrased with different words still surfaces.
    """
    rows: list[sqlite3.Row] = []
    if subject:
        rows = conn.execute(
            "SELECT * FROM memories WHERE status='active' AND subject=? "
            "ORDER BY updated_at DESC LIMIT ?",
            (subject, limit),
        ).fetchall()

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
    r"^\s*(?:le|la|les|l'|on a migr[ée]?|c'est|il y a|the|the main)?\s*"
    r"(?P<subject>[\w\s\-']{2,40}?)\s+"
    r"(?:est|sont|tourne sur|utilise|is|runs on|uses|devient|becomes)\s+"
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
                     source_context: str | None, source: str = "conflict-resolver") -> dict:
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

    if rel == CONTRADICTION:
        if conf < DISPUTE_CONFIDENCE_FLOOR:
            # Weak signal: never destroy a truth on a guess.
            conn.execute(
                "UPDATE memories SET status='disputed', updated_at=? WHERE id=?",
                (ts, existing_id),
            )
            new_id = _insert_fact(conn, new_fact, subject, "active", conf,
                                  source, source_context, ts)
            action.update(action="disputed", new_id=new_id,
                          note="weak contradiction -> old=disputed, new=active")
        else:
            new_id = _insert_fact(conn, new_fact, subject, "active", conf,
                                  source, source_context, ts)
            conn.execute(
                "UPDATE memories SET status='superseded', superseded_by=?, "
                "updated_at=? WHERE id=?",
                (new_id, ts, existing_id),
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
                              source, source_context, ts)
        action.update(action="added", new_id=new_id, note="compatible addition")

    conn.commit()
    return action


def _insert_fact(conn: sqlite3.Connection, content: str, subject: str | None,
                 status: str, confidence: float, source: str,
                 source_context: str | None, ts: str) -> int:
    """Insert a new fact row (FTS5/vec triggers handle the rest).

    Note: no embedding is written here. Vector indexing of new facts is the
    job of a subsequent `hybrid_search.py add`/`index` pass — the arbitrator
    stays LLM-embedding-free so it can run offline.
    """
    cur = conn.execute(
        "INSERT INTO memories (content, category, layer, source, score, subject, "
        "status, confidence, valid_from, source_context, last_confirmed, updated_at) "
        "VALUES (?, 'fact', 'semantic', ?, 0.5, ?, ?, ?, ?, ?, ?, ?)",
        (content, source, subject, status, confidence, ts, source_context, ts, ts),
    )
    return cur.lastrowid


# ─── High-level flows ─────────────────────────────────────────────────────────

def check_fact(conn: sqlite3.Connection, text: str, subject: str | None,
               use_llm: bool = True, apply: bool = False) -> list[dict]:
    """Run the full pipeline for one (possibly compound) new statement."""
    results = []
    for fact in extract_atomic(text):
        candidates = fetch_active_facts(conn, subject, fact)
        best = None
        for cand in candidates:
            verdict = classify_relation(cand["content"], fact, use_llm=use_llm)
            if best is None or _rel_priority(verdict) > _rel_priority(best[1]):
                best = (cand, verdict)
        if best is None:
            results.append({"fact": fact, "verdict": {
                "relation": COMPATIBLE, "confidence": 0.5,
                "reasoning": "no active candidate", "engine": "n/a"},
                "existing_id": None, "action": None})
            continue
        cand, verdict = best
        action = None
        if apply:
            action = apply_resolution(conn, cand["id"], verdict, fact, subject,
                                      source_context=text)
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
                     use_llm=not args.no_llm, apply=args.apply)
    print(json.dumps(res, indent=2, ensure_ascii=False))
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
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            facts.append(obj if isinstance(obj, dict) else {"content": str(obj)})
        except json.JSONDecodeError:
            facts.append({"content": line})

    if not args.apply and not args.force:
        print(f"⚠️  DRY-RUN: {len(facts)} fact(s) will be analysed, no mutation.")
    elif not args.apply:
        print("⚠️  --force without --apply has no effect (analysis is already safe).")

    summary = {"added": 0, "superseded": 0, "disputed": 0, "confirmed": 0}
    for obj in facts:
        content = obj.get("content") or obj.get("fact") or ""
        subject = obj.get("subject") or args.subject
        results = check_fact(conn, content, subject,
                             use_llm=not args.no_llm, apply=args.apply)
        for r in results:
            act = (r.get("action") or {}).get("action")
            if act in summary:
                summary[act] += 1
            tag = act or r["verdict"]["relation"].lower()
            print(f"  [{tag}] {r['fact'][:70]}  ← {r['verdict']['reasoning']}")
    print(f"\nSummary: {summary}")
    if not args.apply:
        print("(dry-run — re-run with --apply to persist)")
    conn.close()


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
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("arbitrate", help="Arbitrate a JSONL batch of facts")
    a.add_argument("file", help="JSONL file, one {content, subject?} per line")
    a.add_argument("--subject", help="Default subject for facts that omit it")
    a.add_argument("--no-llm", action="store_true", help="Skip LLM, use lexical heuristic")
    a.add_argument("--apply", action="store_true", help="Persist resolutions (mutates DB)")
    a.add_argument("--force", action="store_true", help="Required with --apply in non-interactive mode")
    a.set_defaults(func=cmd_arbitrate)

    l = sub.add_parser("lifecycle", help="List facts by lifecycle status")
    l.add_argument("--status", choices=["active", "superseded", "disputed"], help="Filter by status")
    l.add_argument("--limit", type=int, default=30)
    l.set_defaults(func=cmd_lifecycle)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
