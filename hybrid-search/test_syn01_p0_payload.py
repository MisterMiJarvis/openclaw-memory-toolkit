#!/usr/bin/env python3
"""test_syn01_p0_payload.py — SYN-01 P0.

Captures the payload each path WOULD send, by stubbing urlopen. No network.
"""
import io, json, os, sys, unittest.mock as mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS = FAIL = 0
def check(name, fn):
    global PASS, FAIL
    try:
        fn(); PASS += 1; print(f"  OK   {name}")
    except AssertionError as e:
        FAIL += 1; print(f"  FAIL {name}: {e}")
    except Exception as e:
        FAIL += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")

class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

captured = {}

def fake_urlopen(req, timeout=None):
    body = req.data.decode() if hasattr(req, "data") and req.data else ""
    captured["url"] = req.full_url if hasattr(req, "full_url") else str(req)
    captured["payload"] = json.loads(body) if body else {}
    return FakeResp(json.dumps({"message": {"content": "[]"}}).encode())

print("\n[1] payload d'extraction — paramètres de génération")
def _extraction():
    import importlib, auto_capture as AC
    importlib.reload(AC)
    captured.clear()
    with mock.patch("urllib.request.urlopen", fake_urlopen):
        AC.extract_candidate_facts("Mon serveur tourne sous Ubuntu 24.04 LTS depuis hier.",
                                   "", model="qwen2.5:7b")
    p = captured.get("payload", {})
    o = p.get("options", {})
    assert p.get("model") == "qwen2.5:7b", f"model={p.get('model')}"
    assert o.get("temperature") == 0.0, f"temperature={o.get('temperature')}"
    assert o.get("seed") == 1, f"seed={o.get('seed')}"
    assert o.get("num_predict") == 512, f"num_predict={o.get('num_predict')}"
    assert o.get("num_ctx") == 4096, f"num_ctx={o.get('num_ctx')}"
    assert p.get("format") == "json", f"format={p.get('format')}"
    assert p.get("stream") is False, f"stream={p.get('stream')}"
check("extraction: 4 paramètres + format + stream", _extraction)

print("\n[2] refus avant tout appel réseau — modèle cloud")
def _cloud_refused():
    import importlib, auto_capture as AC, model_guard as MG
    importlib.reload(AC)
    captured.clear()
    with mock.patch("urllib.request.urlopen", fake_urlopen):
        try:
            AC.extract_candidate_facts("Mon serveur tourne sous Ubuntu 24.04 LTS.",
                                       "", model="deepseek-v4.1-flash:cloud")
        except MG.ModelGuardError:
            pass
        else:
            raise AssertionError("cloud model n'a pas été refusé")
    assert captured == {}, f"un appel réseau a eu lieu: {captured}"
check("modèle cloud: refus AVANT appel réseau", _cloud_refused)

print("\n[3] use_llm=False — aucun appel")
def _use_llm_false():
    import importlib, conflict_resolver as CR
    importlib.reload(CR)
    captured.clear()
    with mock.patch("urllib.request.urlopen", fake_urlopen):
        CR.classify_relation("Le serveur tourne sous Ubuntu.", "Le serveur tourne sous Debian.",
                             use_llm=False)
    assert captured == {}, f"appel réseau avec use_llm=False: {captured}"
check("use_llm=False: zéro appel", _use_llm_false)

print("\n[4] payload d'arbitrage — paramètres de génération")
def _arbitration():
    import importlib, conflict_resolver as CR
    importlib.reload(CR)
    CR.LLM_MODEL = "qwen2.5:7b"
    captured.clear()
    with mock.patch("urllib.request.urlopen", fake_urlopen):
        CR.classify_relation("Le serveur tourne sous Ubuntu.", "Le serveur tourne sous Debian.",
                             use_llm=True)
    p = captured.get("payload", {}); o = p.get("options", {})
    assert p.get("model") == "qwen2.5:7b", f"model={p.get('model')}"
    assert o.get("temperature") == 0.0 and o.get("seed") == 1, f"options={o}"
    assert o.get("num_predict") == 512 and o.get("num_ctx") == 4096, f"options={o}"
    assert p.get("format") == "json" and p.get("stream") is False
check("arbitrage: 4 paramètres + format + stream", _arbitration)

print("\n[5] embedding — modèle et dimension")
def _embed():
    import importlib, hybrid_search as HS
    importlib.reload(HS)
    assert HS.EMBED_MODEL == "nomic-embed-text", f"EMBED_MODEL={HS.EMBED_MODEL}"
check("embedding: nomic-embed-text codé en dur", _embed)

print(f"\nRÉSULTAT payload : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
