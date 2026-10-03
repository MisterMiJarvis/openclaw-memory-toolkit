#!/usr/bin/env python3
"""
Trace Extractor v2 — LLM-powered extraction from session traces and daily notes.

Instead of noisy regex patterns, uses the LLM to semantically extract:
  1. DECISIONS — new choices made, things decided or changed
  2. ERRORS — bugs, failures, workarounds discovered
  3. FACTS — new information learned (versions, configs, status changes)
  4. PATTERNS — recurring themes worth tracking

Output:
  - Updates memory/YYYY-MM-DD.md with extracted items
  - Appends new entities to memory/ontology/graph.jsonl
  - Flags items that should be promoted to MEMORY.md
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# PII / secret patterns to scrub before sending text to LLM
PII_PATTERNS = [
    re.compile(r'gh[pousr]_[A-Za-z0-9]{36}'),                      # GitHub PAT
    re.compile(r'sk-[A-Za-z0-9]{20,}'),                            # OpenAI-style keys
    re.compile(r'AIza[A-Za-z0-9_\\-]{35}'),                       # Google API keys
    re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}'), # emails
    re.compile(r'(?:password|passwd|pwd|secret|token|api_key|apikey|access_key)\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'Bearer\s+[A-Za-z0-9._\-]+'),                     # Bearer tokens
    re.compile(r'xox[baprs]-[A-Za-z0-9-]+'),                        # Slack tokens
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),  # PEM keys
    # Long opaque tokens (JWT, base64 secrets, hex keys) — the "uncommon format" gap.
    re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),  # JWT
    re.compile(r'\b[A-Fa-f0-9]{32,}\b'),                          # hex secrets (>=32)
    re.compile(r'\b[A-Za-z0-9+/]{40,}={0,2}\b'),                  # base64 blobs (>=40)
    re.compile(r'(?:\+33|0)[1-9](?:[\s.\-]?\d{2}){4}'),           # FR phone numbers
    re.compile(r'\b(?:\d[ -]?){13,19}\b'),                        # card-like digit runs
]

def sanitize_pii(text: str) -> str:
    """Best-effort removal of API keys, tokens, emails, passwords, phone numbers
    and long opaque blobs from text BEFORE any LLM submission.

    SECURITY: this is a defence-in-depth filter, NOT a guarantee. Regex scrubbing
    cannot catch every secret in an arbitrary format. The transport decision (local
    vs cloud) is the primary control; see llm_destination() below.
    """
    for pattern in PII_PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text


def _find_ollama_api_key_sources() -> tuple[str, str]:
    """Return (source, key) for the first Ollama API key found.

    Mirrors the lookup order of get_ollama_api_key() so the transport disclosure
    cannot disagree with the transport actually used. Source is a short label:
    'env', 'secrets-file', 'config' or '' when no key is available.
    """
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if key:
        return ("env", key)
    for secrets_candidate in (
        Path.home() / ".openclaw" / "workspace" / ".secrets" / "ollama.json",
        Path.home() / ".openclaw" / ".secrets" / "ollama.json",
    ):
        try:
            if secrets_candidate.exists():
                d = json.loads(secrets_candidate.read_text())
                v = d.get("apiKey") or d.get("key") or ""
                if isinstance(v, str) and v.strip():
                    return ("secrets-file", v.strip())
        except Exception:
            continue
    try:
        cfg_path = Path(os.environ.get("OPENCLAW_CONFIG", Path.home() / ".openclaw" / "openclaw.json"))
        cfg = json.loads(cfg_path.read_text())
        ak = (cfg.get("models", {}).get("providers", {}).get("ollama", {}) or {}).get("apiKey", "")
        if isinstance(ak, str) and ak.strip():
            return ("config", ak.strip())
        if isinstance(ak, dict):
            sid = ak.get("id") or ak.get("name") or ""
            ref = os.environ.get(sid, "").strip()
            if ref:
                return ("config-store", ref)
    except Exception:
        pass
    return ("", "")


def llm_destination() -> tuple[str, str]:
    """Return (kind, label) describing where extraction text will be sent.

    kind is 'cloud' or 'local'. Used to disclose the transport to the operator
    before any session/memory content leaves the machine.

    SECURITY: this MUST stay in sync with get_ollama_api_key() and the actual
    call path in extract_with_llm(). An earlier version only checked the
    OLLAMA_API_KEY environment variable, so a key sourced from the secrets file
    or openclaw.json made the banner claim "local fallback" while the content
    was in fact posted to ollama.com. The lookup order is now shared.
    """
    if os.environ.get("TRACE_LLM_LOCAL_ONLY", "").strip() in ("1", "true", "yes"):
        return ("local", "local Ollama (127.0.0.1:11434) — forced by TRACE_LLM_LOCAL_ONLY")
    source, _key = _find_ollama_api_key_sources()
    if source:
        return ("cloud", f"Ollama cloud (ollama.com) — key from {source} — content leaves this machine")
    # No key anywhere: the cloud call is skipped and the local fallback is used.
    return ("local", "local Ollama (127.0.0.1:11434) — no API key configured")


def stable_id(prefix: str, text: str, date_str: str) -> str:
    """Deterministic entity ID. sha256 is stable across processes (unlike hash())."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{date_str.replace('-', '')}_{digest}"


WORKSPACE = Path(os.environ.get("WORKSPACE", Path.home() / ".openclaw/workspace"))
ONTOLOGY_FILE = WORKSPACE / "memory" / "ontology" / "graph.jsonl"
DAILY_NOTES_DIR = WORKSPACE / "memory"
MEMORY_FILE = WORKSPACE / "MEMORY.md"
EXTRACTED_FLAG = WORKSPACE / "memory" / ".trace-extracted"
# SECURITY: No global SESSIONS_DIR access — session transcripts may contain
# secrets, private conversations, and unrelated context. Use --session-file
# for explicit, opt-in extraction of a single file.

EXTRACTION_PROMPT = """Analyze the sanitized daily notes below and extract ONLY genuinely significant items.

SECURITY RULES (MANDATORY):
0. NEVER extract credentials, API keys, tokens, passwords, emails, personal data, or session IDs.
1. DECISIONS: Only NEW choices that were MADE (concrete actions taken, not discussed or mentioned)
2. ERRORS: Only real bugs/failures that required a fix or workaround
3. FACTS: Only NEW information (versions, configs, status changes) not already widely known
4. EXCLUDE: routine status ("summary executed", "flag deleted"), warnings without impact, vague mentions
5. EXCLUDE: any casual conversational context, personal opinions, or subjective commentary
6. Be SPECIFIC: include names, versions, numbers (but never secrets)
7. Keep descriptions SHORT (max 15 words each)

Return ONLY valid JSON. No markdown. No code fences. No extra text. Just the JSON object:
{"decisions":[{"what":"short description","date":"YYYY-MM-DD"}],"errors":[{"what":"short description","date":"YYYY-MM-DD"}],"facts":[{"what":"short description","date":"YYYY-MM-DD"}],"promote_to_memory":["items worth promoting to MEMORY.md"]}

DAILY NOTES:
"""


def extract_session_file(session_path: Path, days_back: int = 3) -> tuple[str, str] | None:
    """Extract conversation text from a single session transcript file.

    SECURITY: Only processes a file explicitly provided via --session-file.
    No global session directory scanning.
    """
    if not session_path.exists():
        print(f"   ⚠️ Session file not found: {session_path}")
        return None

    # Constrain to workspace or explicit absolute path
    resolved = session_path.resolve()
    if not (resolved.is_relative_to(WORKSPACE.resolve()) or session_path.is_absolute()):
        print(f"   ⚠️ Session file outside workspace, skipping: {session_path}")
        return None

    cutoff = datetime.now() - timedelta(days=days_back)
    mtime = datetime.fromtimestamp(session_path.stat().st_mtime)
    if mtime < cutoff:
        print(f"   ⚠️ Session file older than {days_back} days, skipping")
        return None

    texts = []
    try:
        with open(session_path) as fh:
            for line in fh:
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "message":
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", "")

                # Skip tool results (too noisy)
                if role == "toolResult":
                    continue

                # Extract text from content blocks or string
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text", "")
                            # Skip heartbeat/empty messages
                            if t and t.strip() not in ("HEARTBEAT_OK", "NO_REPLY", "[OpenClaw heartbeat poll]") and len(t) > 20:
                                text_parts.append(t)
                    if text_parts:
                        combined = ' '.join(text_parts)
                        if len(combined) > 30:
                            texts.append(f"{role}: {combined[:500]}")
                elif isinstance(content, str) and len(content) > 20:
                    if content.strip() not in ("HEARTBEAT_OK", "NO_REPLY", "[OpenClaw heartbeat poll]"):
                        texts.append(f"{role}: {content[:500]}")
    except Exception as e:
        print(f"   ⚠️ Error reading {session_path.name}: {e}")
        return None

    if not texts:
        return None

    session_text = "\n".join(texts)
    # Truncate to max 4000 chars for LLM
    if len(session_text) > 4000:
        session_text = session_text[-4000:]
        session_text = session_text[session_text.index('\n') + 1:]

    return (mtime.strftime("%Y-%m-%d"), session_text)


def load_ontology_ids():
    """Load existing entity IDs from ontology."""
    ids = set()
    if ONTOLOGY_FILE.exists():
        for line in ONTOLOGY_FILE.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                eid = entry.get("entity", {}).get("id", "")
                if eid:
                    ids.add(eid)
            except json.JSONDecodeError:
                continue
    return ids


def load_memory_content():
    """Load MEMORY.md for dedup check."""
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text().lower()
    return ""


def load_extracted_sessions():
    """Load set of already-extracted dates."""
    if EXTRACTED_FLAG.exists():
        return set(EXTRACTED_FLAG.read_text().strip().split("\n"))
    return set()


def save_extracted_date(day_str):
    """Mark a date as extracted."""
    sessions = load_extracted_sessions()
    sessions.add(day_str)
    EXTRACTED_FLAG.write_text("\n".join(sorted(sessions)) + "\n")


def extract_with_llm(text, dry_run=False):
    """Use LLM (Ollama cloud primary, Ollama local fallback) to extract structured info.

    SECURITY / DISCLOSURE: memory and session content IS transmitted to an LLM.
      - Primary transport is Ollama cloud (https://ollama.com) when an API key is
        configured: content LEAVES this machine.
      - Local fallback is Ollama at 127.0.0.1:11434: content stays on the machine.
      - Set TRACE_LLM_LOCAL_ONLY=1 to force local-only and refuse cloud calls.
    Text is sanitized (sanitize_pii) before submission, but regex scrubbing is
    best-effort, NOT a guarantee. Do not feed raw secret material to this function.
    """
    import urllib.request
    import urllib.error
    import time

    # Sanitize PII/secrets before any LLM submission
    text = sanitize_pii(text)

    # Truncate to avoid timeout/context limits
    MAX_CHARS = 8000
    if len(text) > MAX_CHARS:
        text = text[-MAX_CHARS:]
        text = text[text.index('\n') + 1:]

    prompt = EXTRACTION_PROMPT + text

    kind, label = llm_destination()
    if not dry_run:
        if kind == "cloud":
            print(f"   [Security] ⚠️  CLOUD TRANSMISSION: sending sanitized excerpt to {label}")
        else:
            print(f"   [Security] Sending sanitized excerpt to {label} (stays on this machine)")

    def parse_llm_output(output):
        """Extract JSON from LLM output, fixing common issues."""
        json_match = re.search(r'\{[\s\S]*\}', output)
        if not json_match:
            return None
        json_str = json_match.group()
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        json_str = re.sub(r'^```json\s*', '', json_str)
        json_str = re.sub(r'\s*```$', '', json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            try:
                return json.loads(json_str.replace("'", '"'))
            except json.JSONDecodeError:
                return None

    def get_ollama_api_key():
        """Get Ollama API key, in order of precedence:
        1. env var OLLAMA_API_KEY (injected by OpenClaw automation runtime)
        2. dedicated secrets file ~/.openclaw/workspace/.secrets/ollama.json
        3. openclaw.json provider config (resolving store references if possible)
        If no plaintext key is found, returns empty string (caller will report it).

        SECURITY: keep the lookup order identical to _find_ollama_api_key_sources()
        so the disclosure in llm_destination() matches the transport actually used.
        """
        return _find_ollama_api_key_sources()[1]

    def call_ollama_cloud(attempt):
        """Call Ollama cloud API (ollama.com). Returns raw content or None.

        REFUSES to run when TRACE_LLM_LOCAL_ONLY is set: the local-only mode must
        not silently fall back to a transport that leaves the machine.
        """
        if os.environ.get("TRACE_LLM_LOCAL_ONLY", "").strip() in ("1", "true", "yes"):
            print("   [Security] TRACE_LLM_LOCAL_ONLY=1 — cloud call refused (local-only mode)")
            return None
        api_key = get_ollama_api_key()
        if not api_key:
            print("   ⚠️ No Ollama API key available")
            return None
        payload = json.dumps({
            "model": os.environ.get("TRACE_LLM_MODEL", "deepseek-v4-flash"),
            "messages": [
                {"role": "system", "content": "You extract structured information from daily notes. Respond ONLY with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": int(os.environ.get("TRACE_LLM_MAX_TOKENS", "8000"))},
        }).encode('utf-8')
        req = urllib.request.Request(
            "https://ollama.com/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        timeout = 120 + (attempt * 30)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get("message", {}).get("content", "")

    def call_ollama(attempt):
        """Call local Ollama (qwen2.5:7b fallback). Returns raw content or None."""
        model = os.environ.get("TRACE_LLM_FALLBACK_MODEL", "qwen2.5:7b")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 2048}
        }).encode('utf-8')
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        timeout = 90 + (attempt * 30)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get("message", {}).get("content", "")

    # Primary: Ollama cloud (3 retries) — bascule 11/09/2026 (fin DeepSeek direct)
    for attempt in range(3):
        try:
            output = call_ollama_cloud(attempt)
            if output and output.strip():
                parsed = parse_llm_output(output)
                if parsed is not None:
                    return parsed
                print(f"   ⚠️ JSON parse failed (attempt {attempt+1}/3)")
            else:
                print(f"   ⚠️ Empty Ollama response (attempt {attempt+1}/3)")
        except Exception as e:
            print(f"   ⚠️ Ollama API error: {e} (attempt {attempt+1}/3)")
        time.sleep(2 ** attempt)

    # Fallback: Ollama local
    print("   ⚠️ Ollama cloud failed, falling back to local Ollama (qwen2.5:7b)...")
    for attempt in range(2):
        try:
            output = call_ollama(attempt)
            if output and output.strip():
                parsed = parse_llm_output(output)
                if parsed is not None:
                    return parsed
        except Exception as e:
            print(f"   ⚠️ Ollama error: {e} (attempt {attempt+1}/2)")
        time.sleep(2 ** attempt)

    print("   ⚠️ LLM extraction failed after all attempts")
    return None


def extract_with_patterns(text):
    """Fallback: pattern-based extraction (less accurate)."""
    decisions = []
    errors = []
    facts = []
    
    # Routine markers to EXCLUDE (not real facts)
    routine_patterns = [
        r'morning summary exécuté', r'blogwatcher read-all', r'flag.*supprimé',
        r'telegram envoyé', r'résumé.*envoyé', r'veille-trigger',
        r'leadership.*→ supabase', r'spanish.*envoyé', r'reading.*vérifié',
        r'med.*doses restantes', r'astro weather.*\d+/100',
        r'blogwatcher.*\d+ nouveaux articles$',
        r'^-\s+.*due\s+\d', r'^-\s+.*\(due ',  # overdue task lines
        r'heartbeat poll', r'HEARTBEAT_OK', r'NO_REPLY',
    ]
    
    # Check if a line is routine noise
    def is_routine(line):
        line_lower = line.lower()
        return any(re.search(p, line_lower) for p in routine_patterns)
    
    for line in text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 20:
            continue
        # Skip lines that are conversation role prefixes from session transcripts
        if line_stripped.startswith('user:') or line_stripped.startswith('assistant:'):
            content_after_role = re.sub(r'^(user|assistant):\s*', '', line_stripped)
            if len(content_after_role) < 30 or is_routine(content_after_role):
                continue
        if is_routine(line_stripped):
            continue
        
        # Errors: ⚠️ or ❌ markers with real content
        if "⚠️" in line or "❌" in line:
            clean = re.sub(r'^[- ]*❌\s*', '', line_stripped)
            clean = re.sub(r'^[- ]*⚠️\s*', '', clean)
            # Skip routine warnings and overdue task lines
            if not is_routine(clean) and len(clean) > 25 and 'due' not in clean.lower()[:20]:
                errors.append(clean[:200])
        
        # Explicit decision markers: - ✅ + decisive verbs
        if line_stripped.startswith("- ✅") or line_stripped.startswith("✅"):
            clean = re.sub(r'^[- ]*✅\s*', '', line_stripped)
            # Only if it contains a decision verb
            decision_verbs = ['décidé', 'choisi', 'chose', 'choix', 'configured', 'migrated', 
                            'installé', 'créé', 'ajouté', 'switched', 'supprimé', 'retiré',
                            'remplacé', 'fixed', 'corrigé', 'résolu']
            if any(v in clean.lower() for v in decision_verbs) and not is_routine(clean):
                decisions.append(clean[:200])
        
        # Version/status changes: lines with version numbers or specific configs
        version_match = re.search(r'(?:v\d+\.\d+|version\s*[:\-]?\s*\S+|port\s+\d{4})', line_stripped.lower())
        if version_match and not is_routine(line_stripped) and len(line_stripped) > 30:
            facts.append(line_stripped[:200])
    
    # Deduplicate
    decisions = list(dict.fromkeys(decisions))[:5]
    errors = list(dict.fromkeys(errors))[:5]
    facts = list(dict.fromkeys(facts))[:5]
    
    return {
        "decisions": [{"what": d, "date": date.today().isoformat()} for d in decisions],
        "errors": [{"what": e, "date": date.today().isoformat()} for e in errors],
        "facts": [{"what": f, "date": date.today().isoformat()} for f in facts],
        "patterns": [],
        "promote_to_memory": []
    }


def write_daily_extraction(extractions, source="trace-extractor"):
    """Write extracted items to today's daily notes."""
    today = date.today().isoformat()
    daily_file = DAILY_NOTES_DIR / f"{today}.md"
    
    # Check if already extracted today
    if daily_file.exists():
        content = daily_file.read_text()
        if "[trace-extractor]" in content:
            return False
    
    entry = f"\n## [{source}] {datetime.now().strftime('%H:%M')} — Auto-extracted trace items"
    
    for category, label, emoji in [
        ("decisions", "Decisions", "🟢"),
        ("errors", "Errors", "🔴"),
        ("facts", "New facts", "🔵"),
        ("patterns", "Patterns", "🟡"),
    ]:
        items = extractions.get(category, [])
        if items:
            entry += f"\n**{label}:**"
            for item in items:
                what = item.get("what", item) if isinstance(item, dict) else item
                entry += f"\n- {emoji} {what}"
    
    promotions = extractions.get("promote_to_memory", [])
    if promotions:
        entry += "\n**⬆️ Promote to MEMORY.md:**"
        for p in promotions:
            entry += f"\n- {p}"
    
    if daily_file.exists():
        content = daily_file.read_text()
        daily_file.write_text(content.rstrip() + entry + "\n")
    else:
        daily_file.write_text(entry + "\n")
    
    return True



def upsert_entities(records):
    """Physically upsert records into the ontology file.

    The file is an OP-log (one JSON object per line). A true upsert means:
    read the current state, replace matching entities in place, append the new
    ones. It rewrites the whole file, which is acceptable at this size
    (a few hundred KB) and keeps the file compact between GC runs.

    Uses a fresh read of existing IDs (never a stale in-memory cache).
    """
    if not records:
        return 0

    incoming = {}
    for rec in records:
        eid = rec.get("entity", {}).get("id")
        if eid:
            incoming[eid] = rec

    if not incoming:
        return 0

    lines = []
    written = set()
    if ONTOLOGY_FILE.exists():
        for line in ONTOLOGY_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            eid = entry.get("entity", {}).get("id")
            if eid and eid in incoming:
                lines.append(json.dumps(incoming[eid], ensure_ascii=False))
                written.add(eid)
            else:
                lines.append(line)

    for eid, rec in incoming.items():
        if eid not in written:
            lines.append(json.dumps(rec, ensure_ascii=False))

    tmp = ONTOLOGY_FILE.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(ONTOLOGY_FILE)
    return len(incoming)

def write_ontology_entities(extractions):
    """Add new entities to ontology from extractions, using upsert to avoid duplicates."""
    existing_ids = load_ontology_ids()
    new_entities = []
    
    for dec in extractions.get("decisions", []):
        what = dec.get("what", "") if isinstance(dec, dict) else dec
        # Create a clean ID from the decision
        entity_id = stable_id("dec", what, date.today().isoformat())
        if entity_id not in existing_ids:
            new_entities.append({
                "op": "upsert",
                "entity": {
                    "id": entity_id,
                    "type": "Decision",
                    "properties": {
                        "description": what[:200],
                        "date": dec.get("date", date.today().isoformat()),
                        "source": "trace-extraction"
                    }
                }
            })
            existing_ids.add(entity_id)
    
    if new_entities:
        upsert_entities(new_entities)
    
    return len(new_entities)


def write_timeline_events(extractions):
    """Create TimelineEvent entities from extractions."""
    existing_ids = load_ontology_ids()
    today = date.today().isoformat()
    new_events = []
    
    # Map extraction categories to timeline categories
    category_map = {
        "decisions": "work",
        "errors": "technical",
        "facts": "technical",
        "patterns": "technical",
    }
    
    for ext_cat, items in extractions.items():
        if ext_cat not in category_map:
            continue
        tl_category = category_map[ext_cat]
        
        for item in items:
            what = item.get("what", item) if isinstance(item, dict) else item
            if not what or len(what) < 10:
                continue
            
            # Deterministic ID (sha256, stable across processes)
            eid = stable_id("tl", what, today)
            
            if eid in existing_ids:
                continue
            
            entity = {
                "op": "create",
                "entity": {
                    "id": eid,
                    "type": "TimelineEvent",
                    "properties": {
                        "date": today,
                        "description": what[:200],
                        "category": tl_category,
                        "source": "trace-extraction"
                    }
                }
            }
            new_events.append(entity)
            existing_ids.add(eid)
    
    if new_events:
        for e in new_events:
            e["op"] = "upsert"
        upsert_entities(new_events)
    
    return len(new_events)


def auto_promote_to_memory(promotions):
    """Auto-promote items to MEMORY.md if not already there."""
    if not promotions:
        return 0
    
    memory_content = MEMORY_FILE.read_text() if MEMORY_FILE.exists() else ""
    promoted = []
    
    # Find the Active Decisions section
    lines = memory_content.split('\n')
    decisions_idx = None
    for i, line in enumerate(lines):
        if '## Active Decisions' in line:
            decisions_idx = i
            break
    
    if decisions_idx is None:
        # No Active Decisions section found, skip
        return 0
    
    for item in promotions:
        # Check if already in MEMORY.md
        key_terms = [w for w in item.lower().split() if len(w) > 4]
        if all(term in memory_content.lower() for term in key_terms[:3]):
            continue
        
        # Add as a new line after the decisions header
        # Format: - **Item** (date)
        today = date.today().isoformat()
        new_line = f"- **{item}** ({today})"
        lines.insert(decisions_idx + 1, new_line)
        promoted.append(item)
    
    if promoted:
        MEMORY_FILE.write_text('\n'.join(lines))
    
    return len(promoted)


def check_promotions(extractions):
    """Check what should be promoted to MEMORY.md."""
    memory_content = load_memory_content()
    promotions = []
    
    for item in extractions.get("promote_to_memory", []):
        # Check if already in MEMORY.md
        key_terms = [w for w in item.lower().split() if len(w) > 4]
        if not all(term in memory_content for term in key_terms[:3]):
            promotions.append(item)
    
    return promotions


def main():
    parser = argparse.ArgumentParser(description="Extract traces from daily notes and sessions (v2 - LLM-powered)")
    parser.add_argument("--all", action="store_true", help="Process all unprocessed dates")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--days", type=int, default=3, help="Number of recent days to process (default: 3)")
    parser.add_argument("--llm", action="store_true", help="Use LLM for extraction (default: pattern-based)")
    parser.add_argument("--session-file", type=str, default=None,
                        help="Extract from a single session transcript file (explicit opt-in). "
                             "Path must be absolute or relative to workspace.")
    parser.add_argument("--date", type=str, help="Process specific date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    print("🔍 Trace Extractor v2 — LLM-powered extraction")
    print("=" * 55)
    
    # Collect daily notes
    notes = []
    extracted = load_extracted_sessions()
    
    if args.date:
        f = DAILY_NOTES_DIR / f"{args.date}.md"
        if f.exists():
            notes.append((args.date, f.read_text()))
    else:
        for i in range(args.days):
            d = (date.today() - timedelta(days=i)).isoformat()
            f = DAILY_NOTES_DIR / f"{d}.md"
            if f.exists() and d not in extracted:
                notes.append((d, f.read_text()))
    
    # Also collect session transcript if explicitly provided
    session_texts = []
    if args.session_file:
        print("📋 Extracting from session file...")
        session_path = Path(args.session_file)
        result = extract_session_file(session_path, days_back=args.days)
        if result:
            day, text = result
            print(f"   Session: {session_path.name[:40]}... ({len(text)} chars)")
            session_texts = [(day, text)]
        else:
            print("   ⚠️ No extractable content from session file")
    
    if not notes and not session_texts:
        print("No unprocessed daily notes or sessions found.")
        return
    
    print(f"📄 Processing {len(notes)} daily notes" + (f" + {len(session_texts)} sessions" if session_texts else ""))
    
    # Extract
    all_extractions = {"decisions": [], "errors": [], "facts": [], "patterns": [], "promote_to_memory": []}
    
    def merge_extractions(target, source):
        for key in target:
            if key in source:
                target[key].extend(source[key])
    
    # Process daily notes
    if args.llm and notes:
        print("🤖 Using LLM extraction (per-day)...")
        for day_str, day_text in notes:
            print(f"   Processing notes {day_str}...")
            result = extract_with_llm(day_text, dry_run=args.dry_run)
            if result:
                merge_extractions(all_extractions, result)
            else:
                print(f"   ⚠️ LLM failed for {day_str}, using pattern fallback")
                pattern_result = extract_with_patterns(day_text)
                merge_extractions(all_extractions, pattern_result)
    elif notes:
        print("📐 Using pattern-based extraction for notes...")
        combined_text = "\n".join(text for _, text in notes)
        pattern_result = extract_with_patterns(combined_text)
        merge_extractions(all_extractions, pattern_result)
    
    # Process session transcripts
    if session_texts:
        print("🤖 Extracting from session transcripts...")
        for day_str, text in session_texts:
            # Truncate large sessions
            if len(text) > 6000:
                text = text[-6000:]
                text = text[text.index('\n') + 1:]
            print(f"   Processing session from {day_str} ({len(text)} chars)...")
            if args.llm:
                result = extract_with_llm(text, dry_run=args.dry_run)
                if result:
                    merge_extractions(all_extractions, result)
                else:
                    print(f"   ⚠️ LLM failed for session, using pattern fallback")
                    pattern_result = extract_with_patterns(text)
                    merge_extractions(all_extractions, pattern_result)
            else:
                pattern_result = extract_with_patterns(text)
                merge_extractions(all_extractions, pattern_result)
    
    extractions = all_extractions
    
    # Display results
    for category, label, emoji in [
        ("decisions", "Decisions", "🟢"),
        ("errors", "Errors", "🔴"),
        ("facts", "New facts", "🔵"),
        ("patterns", "Patterns", "🟡"),
    ]:
        items = extractions.get(category, [])
        if items:
            print(f"\n{emoji} {label} ({len(items)}):")
            for item in items:
                what = item.get("what", item) if isinstance(item, dict) else item
                print(f"   • {what[:100]}")
    
    promotions = check_promotions(extractions)
    if promotions:
        print(f"\n⬆️  Promote to MEMORY.md ({len(promotions)}):")
        for p in promotions:
            print(f"   • {p[:100]}")
    
    if args.dry_run:
        print("\n🏁 Dry run — no changes written.")
        return
    
    # Write
    wrote_daily = write_daily_extraction(extractions)
    wrote_ontology = write_ontology_entities(extractions)
    wrote_timeline = write_timeline_events(extractions)
    
    for d, _ in notes:
        save_extracted_date(d)
    
    print(f"\n✅ Written: daily notes {'updated' if wrote_daily else 'skipped'}, {wrote_ontology} ontology entities, {wrote_timeline} timeline events")
    
    if promotions:
        print("\n💡 Review these promotions and add to MEMORY.md if needed.")


if __name__ == "__main__":
    main()