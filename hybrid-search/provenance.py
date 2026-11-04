#!/usr/bin/env python3
"""provenance.py — v5.4-P1 (GO-C P4): make `session=<id> message=<id>` resolvable.

A provenance string that cannot be resolved is decoration. This module turns it
into verifiable evidence: the message must exist, belong to the stated session,
carry a role and a timestamp, and yield a faithful excerpt.

Anything else is reported, never invented.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import shutil
from pathlib import Path

AGENT_DB = Path(os.environ.get(
    "OPENCLAW_AGENT_DB",
    str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "openclaw-agent.sqlite"),
))

_PAT = re.compile(r"session=([^\s]+)\s+message=([^\s]+)")

STATUS = ("OK", "NOT_FOUND", "WRONG_SESSION", "MALFORMED", "SOURCE_MISSING")


def parse(source_context: str | None) -> tuple[str | None, str | None, str]:
    """Return (session_id, message_id, status). Never guesses."""
    if not source_context:
        return None, None, "MALFORMED"
    m = _PAT.search(source_context)
    if not m:
        return None, None, "MALFORMED"
    return m.group(1), m.group(2), "OK"


def _snapshot(db: Path) -> Path | None:
    if not db.exists():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="oc-prov-")) / "s.sqlite"
    try:
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(str(tmp)); src.backup(dst); dst.close(); src.close()
        return tmp
    except sqlite3.Error:
        return None


def resolve(source_context: str | None, excerpt_chars: int = 120,
            agent_db: Path | None = None) -> dict:
    """Resolve a provenance string against the OpenClaw session store.

    Returns {status, session_id, message_id, role, timestamp, excerpt, detail}.
    Never returns invented data: on any doubt the status is explicit.
    """
    sid, mid, st = parse(source_context)
    out = {"status": st, "session_id": sid, "message_id": mid,
           "role": None, "timestamp": None, "excerpt": None, "detail": ""}
    if st != "OK":
        out["detail"] = "provenance string does not carry session= and message="
        return out

    db = agent_db or AGENT_DB
    snap = _snapshot(db)
    if snap is None:
        out["status"] = "SOURCE_MISSING"
        out["detail"] = f"agent store not readable: {db}"
        return out

    try:
        conn = sqlite3.connect(str(snap))
        row = conn.execute(
            "SELECT session_id, role, timestamp, text FROM session_transcript_fts "
            "WHERE message_id = ? LIMIT 1", (mid,)
        ).fetchone()
        if not row:
            out["status"] = "NOT_FOUND"
            out["detail"] = "no message with this id in the transcript store"
            return out
        real_sid, role, ts, text = row
        if str(real_sid) != str(sid):
            out["status"] = "WRONG_SESSION"
            out["detail"] = f"message belongs to another session ({str(real_sid)[:8]}…)"
            out["role"], out["timestamp"] = role, ts
            return out
        out.update(status="OK", role=role, timestamp=ts,
                   excerpt=(text or "")[:excerpt_chars].replace("\n", " "))
        return out
    finally:
        shutil.rmtree(snap.parent, ignore_errors=True)
