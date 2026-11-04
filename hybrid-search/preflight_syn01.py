#!/usr/bin/env python3
"""preflight_syn01.py — SYN-01 P0. Fail-closed pre-run check. No network.
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_guard as MG

ERRORS = []
WARN = []

def req(cond, msg):
    if not cond: ERRORS.append(msg)

extraction = (os.environ.get("AUTO_CAPTURE_MODEL") or "").strip()
arbitration = (os.environ.get("CONFLICT_LLM_MODEL") or "").strip()

req(extraction, "AUTO_CAPTURE_MODEL absent -> llm_resolution retomberait sur le cloud")
req(arbitration, "CONFLICT_LLM_MODEL absent -> fallback cloud possible")

if extraction:
    try: MG.assert_not_cloud(extraction, "AUTO_CAPTURE_MODEL")
    except MG.ModelGuardError as e: ERRORS.append(str(e))
if arbitration:
    try: MG.assert_not_cloud(arbitration, "CONFLICT_LLM_MODEL")
    except MG.ModelGuardError as e: ERRORS.append(str(e))

for var, dflt in (("OLLAMA_URL","http://127.0.0.1:11434"),
                  ("OLLAMA_EMBED_URL","http://127.0.0.1:11434/api/embeddings"),
                  ("OLLAMA_GEN_URL","http://127.0.0.1:11434/api/generate")):
    try: MG.assert_loopback(os.environ.get(var) or dflt, var)
    except MG.ModelGuardError as e: ERRORS.append(str(e))

try: MG.assert_embedding_dim(768)
except MG.ModelGuardError as e: ERRORS.append(str(e))

if ERRORS:
    print("PRE-FLIGHT: FAIL")
    for e in ERRORS: print("  ❌", e)
    sys.exit(1)
print("PRE-FLIGHT: PASS")
print(f"  extraction={extraction} arbitration={arbitration} embed=nomic-embed-text(768)")
sys.exit(0)
