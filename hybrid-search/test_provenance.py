#!/usr/bin/env python3
"""test_provenance.py — GO-C P4.

Resolves a provenance string against the real OpenClaw session store. The test
picks a real message_id from the store so it does not depend on fixtures, and
skips cleanly when the store is absent (CI without OpenClaw).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provenance as P


def main() -> int:
    if not P.AGENT_DB.exists():
        print(f"SKIP: no agent store at {P.AGENT_DB}")
        return 0

    # A real message, read from the store itself.
    import sqlite3, shutil, tempfile
    tmp = Path(tempfile.mkdtemp()) / "s.sqlite"
    src = sqlite3.connect(f"file:{P.AGENT_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp)); src.backup(dst); dst.close(); src.close()
    conn = sqlite3.connect(str(tmp))
    row = conn.execute(
        "SELECT message_id, session_id FROM session_transcript_fts "
        "WHERE role='user' LIMIT 1").fetchone()
    conn.close()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    if not row:
        print("SKIP: store has no user message")
        return 0
    mid, sid = row

    cases = [
        ("OK",            f"session={sid} message={mid}"),
        ("WRONG_SESSION", f"session=definitely-not-this-one message={mid}"),
        ("NOT_FOUND",     f"session={sid} message=no-such-message-xyz"),
        ("MALFORMED",     "le serveur tourne sous Ubuntu"),
        ("MALFORMED",     None),
    ]
    ok = fail = 0
    for expected, sc in cases:
        r = P.resolve(sc)
        good = r["status"] == expected
        ok += good; fail += (not good)
        print(f"{'PASS' if good else 'FAIL'} attendu={expected:13} obtenu={r['status']:13} | {str(sc)[:50]}")
        if r["status"] == "OK":
            assert r["role"] and r["timestamp"] and r["excerpt"], "OK must carry role/ts/excerpt"
            print(f"      role={r['role']} excerpt={r['excerpt'][:50]!r}")

    print(f"\n{ok} pass, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
