#!/usr/bin/env python3
"""
Auto-Capture — post-turn fact extraction for the memory toolkit.

The write-behind half of the autonomous memory loop: after an exchange, this
script decides *whether* there is anything durable to remember, extracts atomic
facts with a local LLM, and hands them to conflict_resolver.py for arbitration.

It is designed to run as a detached cron/background job — never in the request
path — so it does not add perceived latency to a chat turn.

Design constraints (inherited from the rest of the toolkit):
  * Local-first: the LLM endpoint is validated as loopback-only via the same
    get_safe_ollama_url() guard as hybrid_search.py / conflict_resolver.py, so
    no conversation content ever leaves the machine.
  * User-anchored: extraction is told, in the strongest terms, to capture only
    durable facts asserted by the USER or confirmed by a tool result — never the
    assistant's own speculation. This is the echo-loop guard.
  * Strong-signal gating: a cheap regex pre-filter skips trivial exchanges
    before any LLM call, so we do not burn a model run on "ok merci".
  * Non-destructive ingress: facts are written to a JSONL buffer and submitted
    to conflict_resolver.py. Superseding is decided there, with traceability.
  * Explicit opt-in to mutation: --apply is required (and --force in
    non-interactive mode) before the DB is touched.

Usage:
    # Analyse a single exchange (dry-run: prints what would be captured)
    python3 auto_capture.py --user "j'ai migré le serveur sous Ubuntu 24.04" \
        --assistant "noté"

    # Full pipeline: extract then arbitrate+apply into the DB
    python3 auto_capture.py --user "..." --assistant "..." --apply --force

    # Transcript mode: analyse a whole JSONL/plain transcript file
    python3 auto_capture.py --file transcript.jsonl --apply --force

    # Gating self-test (no LLM, no DB)
    python3 auto_capture.py --selftest
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# ─── Config ───────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("MEMORY_DB", str(_HERE / "agent_memory.db"))
WORKSPACE = Path(
    os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))
).resolve()
CONFLICT_RESOLVER = str(_HERE / "conflict_resolver.py")
DEFAULT_BUFFER = "/tmp/auto_captured_facts.jsonl"

ALLOWED_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1"}

DEFAULT_MODEL = os.environ.get("AUTO_CAPTURE_MODEL", "qwen2.5:7b")
GEN_TIMEOUT = int(os.environ.get("AUTO_CAPTURE_TIMEOUT", "60"))

CAPTURE_PROMPT = """Tu es un extracteur de mémoire pour un agent personnel.
Analyse le tour de conversation fourni et extrais UNIQUEMENT les faits durables,
décisions, configurations ou corrections explicites.

RÈGLES STRICTES :
- Capture en priorité ce que l'UTILISATEUR affirme sur lui-même, son matériel,
  ses projets, ses préférences, ses décisions ("j'ai changé", "mon nouveau",
  "note que", "correction :", "désormais", "finalement").
- Capture aussi un fait CONFIRMÉ par une sortie d'outil technique (version,
  chemin, état de service), seulement s'il est explicitement présent.
- N'invente RIEN. N'extrais JAMAIS une supposition, un plan, une hypothèse, un
  exemple, ou une formulation de l'assistant ("je pourrais", "on pourrait",
  "il faudrait peut-être").
- Un fait atomique = une seule affirmation, autoportante, au présent.
- Si aucun fait durable n'est présent, renvoie [].

Réponds UNIQUEMENT par un tableau JSON, sans texte autour :
[{"subject": "entite_courte", "fact": "affirmation atomique", "confidence": 0.9}]

subject = entité concernée en minuscules sans espaces (ex: serveur_prod, kiddo,
stephane_sante, astrocapture, maison_jeanne).
confidence = 0.0 à 1.0, ta certitude que c'est un fait durable et fiable.
"""

# ─── URL guard (identical policy to the rest of the toolkit) ──────────────────


def get_safe_ollama_url(env_var: str, default: str) -> str:
    """Validate and return an OLLAMA URL, restricting it to loopback only."""
    raw_url = os.environ.get(env_var, default)
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid scheme for {env_var}: {parsed.scheme}")
    hostname = parsed.hostname or ""
    if hostname not in ALLOWED_OLLAMA_HOSTS:
        raise ValueError(
            f"Refusing non-loopback {env_var}: {hostname!r}. "
            "Memory content must never leave the machine."
        )
    return raw_url.rstrip("/")


OLLAMA_URL = get_safe_ollama_url("OLLAMA_URL", "http://127.0.0.1:11434")

# ─── Strong-signal gating ─────────────────────────────────────────────────────

TRIVIAL_PATTERN = re.compile(
    r"^\s*(bonjour|salut|hello|hi|coucou|ok|d'accord|merci|thanks|thx|"
    r"oui|non|yes|no|super|parfait|nickel|top|bien reçu|reçu|"
    r"👍|🙏|😂|❤️)[\s.!?,…]*$",
    re.I,
)

# Strong signals: the user asserts a durable change/decision/fact.
SIGNAL_PATTERNS = [
    re.compile(r"\b(j'ai changé|j'ai migré|j'ai installé|j'ai configuré|"
               r"j'ai décidé|j'ai acheté|j'ai supprimé|j'ai renommé|"
               r"j'ai passé|j'ai commencé|j'ai arrêté)\b", re.I),
    re.compile(r"\b(mon nouveau|ma nouvelle|mes nouveaux|mes nouvelles)\b", re.I),
    re.compile(r"\b(note que|note bien|retiens que|souviens-toi|"
               r"rappelle-toi|mémorise)\b", re.I),
    re.compile(r"\b(correction|corrige|en fait|finalement|désormais|"
               r"à partir de maintenant|dorénavant)\b", re.I),
    re.compile(r"\b(c'est\s+\S+\s+maintenant|est devenu|remplace|"
               r"n'est plus|ne sont plus)\b", re.I),
    re.compile(r"\b(rappel\s*:|info\s*:|pour info|à savoir|config\s*:)\b", re.I),
]


def is_trivial(text: str) -> bool:
    """Cheap gate: skip greetings and acknowledgements."""
    cleaned = text.strip()
    if not cleaned:
        return True
    if TRIVIAL_PATTERN.match(cleaned):
        return True
    # Very short messages with no verb/entity signal are not worth an LLM pass.
    if len(cleaned.split()) < 3 and not any(p.search(cleaned) for p in SIGNAL_PATTERNS):
        return True
    return False


def has_signal(text: str) -> bool:
    """True if the text carries a strong capture signal."""
    return any(p.search(text) for p in SIGNAL_PATTERNS)


def should_capture(user_msg: str) -> bool:
    """Decide whether an exchange is worth analysing."""
    if is_trivial(user_msg):
        return False
    # If there is an explicit signal, definitely capture.
    if has_signal(user_msg):
        return True
    # Otherwise require a non-trivial, entity-bearing sentence.
    return len(user_msg.split()) >= 8


# ─── LLM extraction ───────────────────────────────────────────────────────────


def extract_candidate_facts(user_msg: str, assistant_msg: str,
                            model: str = DEFAULT_MODEL) -> list[dict]:
    """Ask a local LLM for atomic durable facts from the exchange.

    Returns [] on any failure — capture must never crash the caller.
    """
    user_msg = (user_msg or "").strip()
    assistant_msg = (assistant_msg or "").strip()
    if not user_msg:
        return []

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CAPTURE_PROMPT},
            {
                "role": "user",
                "content": (
                    "Tour de conversation à analyser.\n"
                    f"USER: {user_msg}\n"
                    f"ASSISTANT: {assistant_msg}\n"
                ),
            },
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0},
    }

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=GEN_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("message", {}).get("content", "")
        facts = json.loads(content)
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError,
            ValueError):
        # A small local model may wrap the JSON in prose or trailing text.
        facts = _salvage_json(content if 'content' in dir() else "")

    return _normalise_facts(facts)


def _salvage_json(text: str):
    """Extract the first JSON array or object from a noisy LLM response."""
    if not text:
        return []
    text = text.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return []


def _normalise_facts(raw) -> list[dict]:
    """Keep only well-formed, plausible, non-assistant-speculation facts."""
    # A single-object response is a valid one-fact answer: wrap it.
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact") or item.get("content") or "").strip()
        if len(fact) < 15 or len(fact) > 400:
            continue
        if len(fact.split()) < 3:
            continue
        subject = str(item.get("subject") or "").strip().lower()
        subject = re.sub(r"\s+", "_", subject) or None
        # Anti-hallucination: a small local model invents subjects. Keep the
        # subject only if one of its tokens actually appears in the fact text.
        if subject:
            tokens = [t for t in re.split(r"[^\w]+", subject) if len(t) >= 3]
            flat = re.sub(r"[^\w]+", "", fact).lower()
            if not tokens or not any(t in flat for t in tokens):
                subject = None
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))
        # Drop hedging/speculative phrasing that leaked through — echo guard.
        if re.search(r"\b(peut-être|pourrait|pourrions|éventuellement|"
                     r"il faudrait|on devrait|hypothèse|supposons)\b", fact, re.I):
            continue
        out.append({"content": fact, "subject": subject,
                    "confidence": confidence})
    return out


# ─── Pipeline ─────────────────────────────────────────────────────────────────


def write_buffer(facts: list[dict], path: str = DEFAULT_BUFFER) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        for item in facts:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def run_arbitration(buffer_path: str, apply: bool, force: bool,
                    no_llm: bool, no_split: bool = False,
                    db: str = DB_PATH) -> int:
    """Hand the buffer to conflict_resolver.py."""
    cmd = [sys.executable, CONFLICT_RESOLVER, "--db", db, "arbitrate", buffer_path]
    if apply:
        cmd.append("--apply")
    if force:
        cmd.append("--force")
    if no_llm:
        cmd.append("--no-llm")
    if no_split:
        cmd.append("--no-split")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0 and proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def process_exchange(user_msg: str, assistant_msg: str, args) -> int:
    if not should_capture(user_msg):
        if args.verbose:
            print("⏭  gated: trivial exchange, no capture.")
        return 0

    facts = extract_candidate_facts(user_msg, assistant_msg, args.model)
    if not facts:
        if args.verbose:
            print("⏭  no durable fact extracted.")
        return 0

    print(f"📥 {len(facts)} fact(s) extracted:")
    for f in facts:
        subj = f["subject"] or "-"
        print(f"   [{subj}] {f['content']}  (conf {f['confidence']:.2f})")

    buf = write_buffer(facts, args.buffer)
    return run_arbitration(buf, apply=args.apply, force=args.force,
                           no_llm=args.no_llm, no_split=args.no_split, db=args.db)


def process_transcript(path: str, args) -> int:
    """Analyse a JSONL transcript of {user, assistant} turns."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"❌ transcript not found: {path}")
    turns = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (obj.get("user") or obj.get("assistant")):
            turns.append((obj.get("user", ""), obj.get("assistant", "")))
    if not turns:
        print("⏭  no turns found in transcript.")
        return 0
    rc = 0
    for user_msg, assistant_msg in turns:
        rc = process_exchange(user_msg, assistant_msg, args) or rc
    return rc


# ─── Self-test (no LLM, no DB) ────────────────────────────────────────────────


def selftest() -> int:
    cases = [
        ("salut", False),
        ("ok merci", False),
        ("👍", False),
        ("bien reçu", False),
        ("j'ai migré le serveur sous Ubuntu 24.04", True),
        ("note que mon nouveau tel est un Pixel 9", True),
        ("correction : c'est 4 pas 6", True),
        ("mon nouveau serveur est à jour", True),
        ("il fait beau aujourd'hui", False),
        ("désormais le backup tourne à 23h", True),
    ]
    failures = 0
    for text, expected in cases:
        got = should_capture(text)
        mark = "OK " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  {mark} capture={got!s:5} expected={expected!s:5}  {text!r}")

    # Normalisation + echo guard
    raw = [
        {"subject": "Serveur Prod", "fact": "Le serveur principal tourne sous Ubuntu 24.04.", "confidence": 0.95},
        {"subject": "x", "fact": "trop court", "confidence": 0.9},
        {"subject": "z", "fact": "court aussi", "confidence": 0.9},
        {"subject": "y", "fact": "Le serveur pourrait peut-être migrer un jour vers Debian.", "confidence": 0.8},
    ]
    norm = _normalise_facts(raw)
    if len(norm) != 1 or norm[0]["subject"] is not None:
        # 'Serveur Prod' -> tokens 'serveur','prod'; 'prod' not in the text
        # ('principal'), so the invented half is dropped -> subject None.
        print(f"  FAIL normalisation: {norm}")
        failures += 1
    else:
        print(f"  OK  normalisation + echo guard ({len(norm)} kept of {len(raw)})")

    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print("\nselftest passed.")
    return 0


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Auto-Capture — post-turn fact extraction (write-behind).")
    parser.add_argument("--user", help="user message of the exchange")
    parser.add_argument("--assistant", default="", help="assistant reply")
    parser.add_argument("--file", help="JSONL transcript of {user, assistant} turns")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--buffer", default=DEFAULT_BUFFER,
                        help=f"JSONL buffer path (default: {DEFAULT_BUFFER})")
    parser.add_argument("--db", default=DB_PATH, help=f"agent_memory.db path")
    parser.add_argument("--apply", action="store_true",
                        help="Persist resolutions (mutates DB)")
    parser.add_argument("--force", action="store_true",
                        help="Required with --apply in non-interactive mode")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM relation classification in the resolver")
    parser.add_argument("--no-split", action="store_true",
                        help="Facts are already atomic (skip resolver punctuation split)")
    parser.add_argument("--verbose", action="store_true", help="Explain gating")
    parser.add_argument("--selftest", action="store_true",
                        help="Run gating/normalisation self-test (no LLM/DB)")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if args.file:
        sys.exit(process_transcript(args.file, args))
    if not args.user:
        parser.error("provide --user <msg> or --file <transcript> (or --selftest)")
    sys.exit(process_exchange(args.user, args.assistant, args))


if __name__ == "__main__":
    main()
