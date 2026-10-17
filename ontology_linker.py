#!/usr/bin/env python3
"""
ontology_linker.py — Attach orphan entities to the ontology graph.

Reads memory/ontology/graph.jsonl, finds entities with no relation, proposes
the nearest "root" entity using a lexical+semantic-ish match on the entity's
own text (name/description/properties), and writes back ``relate`` facts —
WITHOUT ever deleting or rewriting anything.

The graph is append-only. This script only APPENDS ``{"op": "relate", ...}``
lines. Nothing is destroyed, so every link is inspectable and reversible.

Usage:
  ontology_linker.py --dry-run                 # show proposals, write nothing
  ontology_linker.py --dry-run --limit 20      # only first 20 orphans
  ontology_linker.py --dry-run --json          # machine-readable proposals
  ontology_linker.py --apply                   # append the relate facts
  ontology_linker.py --apply --yes             # skip the interactive confirm

Design notes:
  * We deliberately do NOT use the vector index here: orphan entities are not
    in agent_memory.db (they live in the ontology log, a different store).
    Matching is lexical over the entity's own text, scored by token overlap —
    fast, deterministic, zero external dependency.
  * Roots are entities that already have >= 1 relation. Orphans attach to the
    best-scoring root. If nothing clears the threshold, the orphan is left
    alone and reported.
  * Every appended relation carries evidence + confidence so a later pass can
    audit or supersede it.
"""

import argparse
import collections
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", Path.home() / ".openclaw/workspace")).resolve()
ONTOLOGY = WORKSPACE / "memory" / "ontology" / "graph.jsonl"

ENTITY_OPS = ("create", "upsert", "state")
DEFAULT_THRESHOLD = 0.34          # token-overlap score below which we stay silent

# Entity types that make poor roots: they are facts, not concepts.
WEAK_ROOT_TYPES = {"TimelineEvent", "Episode", "Decision", "BugFix", "SecurityEvent"}

# Generic tokens that carry no identifying power.
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "its", "has", "have", "been", "not", "but", "all", "any", "can", "will",
    "skill", "skills", "notes", "note", "openclaw", "workspace", "data", "file",
    "files", "memory", "system", "service", "project", "status", "active",
    "true", "false", "none", "name", "type", "date", "id", "new", "old",
    "de", "la", "le", "les", "des", "du", "un", "une", "et", "ou", "pour",
    "sur", "dans", "par", "avec", "sans", "est", "son", "sa", "ses", "au",
    "aux", "ce", "cet", "cette", "qui", "que", "quoi", "plus", "pas",
}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def tokenize(*parts):
    text = " ".join(str(p) for p in parts if p)
    text = strip_accents(text.lower())
    toks = set(re.findall(r"[a-z0-9_]{3,}", text))
    return {t for t in toks if t not in STOPWORDS}


def load_graph(path):
    """Replay the append-only log: latest state wins, relations accumulate."""
    entities, order, relations = {}, [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        op = e.get("op")
        if op in ENTITY_OPS and "entity" in e:
            ent = e["entity"]
            eid = ent.get("id")
            if not eid:
                continue
            if eid not in entities:
                order.append(eid)
            entities[eid] = ent
        elif op in ENTITY_OPS and "relation" in e:
            r = e["relation"]
            relations.append((r.get("from"), r.get("rel", "?"), r.get("to"), ""))
        elif op == "relate":
            relations.append((e.get("from"), e.get("rel", "?"), e.get("to"),
                              (e.get("properties") or {}).get("evidence", "")))
    return entities, order, relations


def entity_text(ent):
    """All identifying text of an entity, for matching."""
    props = ent.get("properties") or {}
    bits = [ent.get("id", ""), ent.get("type", "")]
    for k in ("name", "title", "description", "scope", "notes", "role",
              "category", "owner", "summary"):
        if props.get(k):
            bits.append(str(props[k]))
    for k, v in props.items():
        if isinstance(v, str) and len(v) > 12:
            bits.append(v)
    return " ".join(bits)


def score(orphan_tokens, root_tokens):
    """Jaccard-ish overlap, weighted so a shared rare token beats common ones."""
    if not orphan_tokens or not root_tokens:
        return 0.0
    inter = orphan_tokens & root_tokens
    if not inter:
        return 0.0
    # weight by inverse size of the root vocabulary (rarer roots = sharper hit)
    return len(inter) / (len(orphan_tokens) ** 0.5 * len(root_tokens) ** 0.5)


def build_proposals(entities, order, relations, threshold, limit=None):
    connected = set()
    for src, _rel, dst, _ev in relations:
        if src in entities and dst in entities:
            connected.add(src)
            connected.add(dst)

    # candidate roots: connected, with substance, not a fact-type
    roots = []
    for eid in order:
        if eid not in connected:
            continue
        ent = entities[eid]
        if ent.get("type") in WEAK_ROOT_TYPES:
            continue
        toks = tokenize(entity_text(ent))
        if len(toks) < 2:
            continue
        roots.append((eid, toks, ent))
    root_index = [(eid, toks) for eid, toks, _ in roots]

    orphans = [eid for eid in order if eid not in connected]
    if limit:
        orphans = orphans[:limit]

    proposals, unmatched = [], []
    for eid in orphans:
        ent = entities[eid]
        otoks = tokenize(entity_text(ent))
        best = (0.0, None)
        for rid, rtoks in root_index:
            s = score(otoks, rtoks)
            if s > best[0]:
                best = (s, rid)
        props = ent.get("properties") or {}
        label = props.get("name") or props.get("title") or props.get("description") or eid
        row = {
            "orphan_id": eid,
            "orphan_type": ent.get("type", "unknown"),
            "orphan_label": str(label)[:90],
            "root_id": best[1],
            "root_type": entities[best[1]].get("type") if best[1] else None,
            "score": round(best[0], 3),
            "rel": "relates_to",
        }
        if best[0] >= threshold and best[1]:
            proposals.append(row)
        else:
            unmatched.append(row)

    proposals.sort(key=lambda r: -r["score"])
    return proposals, unmatched, len(connected), orphans


def apply_proposals(proposals, path):
    """Append relate facts. Never touches existing lines."""
    today = date.today().isoformat()
    written = 0
    with path.open("a", encoding="utf-8") as f:
        for p in proposals:
            fact = {
                "op": "relate",
                "from": p["orphan_id"],
                "rel": p["rel"],
                "to": p["root_id"],
                "properties": {
                    "evidence": f"auto-link: lexical overlap {p['score']} "
                                f"({p['orphan_type']} -> {p['root_type']})",
                    "confidence": p["score"],
                    "source": "ontology_linker",
                    "valid_from": today,
                },
            }
            f.write(json.dumps(fact, ensure_ascii=False, sort_keys=True) + "\n")
            written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description="Attach orphan entities to the ontology graph.")
    ap.add_argument("--ontology", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    path = Path(a.ontology).resolve() if a.ontology else ONTOLOGY
    if not path.exists():
        print(f"❌ Ontology not found: {path}")
        return 1
    if not a.dry_run and not a.apply:
        a.dry_run = True   # safe default

    entities, order, relations = load_graph(path)
    proposals, unmatched, n_connected, orphans = build_proposals(
        entities, order, relations, a.threshold, a.limit)

    if a.json:
        print(json.dumps({"proposals": proposals, "unmatched": unmatched,
                          "stats": {"entities": len(order), "connected": n_connected,
                                    "orphans_total": len(orphans),
                                    "examined": len(orphans) if not a.limit else min(len(orphans), a.limit),
                                    "proposed": len(proposals)}},
                         ensure_ascii=False, indent=2))
        if a.dry_run:
            return 0

    else:
        print(f"📊 Graphe : {len(order)} entités · {n_connected} connectées · "
              f"{len(orphans)} orphelines")
        print(f"🔎 Orphelines examinées : {len(orphans) if not a.limit else min(len(orphans), a.limit)}")
        print(f"🎯 Seuil : {a.threshold} → {len(proposals)} lien(s) proposé(s), "
              f"{len(unmatched)} laissée(s) de côté")
        print()
        for p in proposals:
            print(f"  [{p['score']:.2f}] {p['orphan_id']} ({p['orphan_type']})")
            print(f"          └─ relates_to → {p['root_id']} ({p['root_type']})")
            print(f"             « {p['orphan_label']} »")
        if unmatched:
            print()
            print(f"  — non reliées (score < {a.threshold}) :")
            for p in unmatched[:10]:
                print(f"     {p['score']:.2f}  {p['orphan_id']} ({p['orphan_type']})")
            if len(unmatched) > 10:
                print(f"     … +{len(unmatched)-10}")

    if a.apply and proposals:
        print()
        if not a.yes:
            print(f"⚠️  Va APPENDRE {len(proposals)} faits 'relate' dans {path.name}.")
            print("    Aucune ligne existante n'est modifiée ni supprimée.")
            try:
                if input("    Confirmer ? [y/N] ").strip().lower() not in ("y", "yes", "o", "oui"):
                    print("    Annulé.")
                    return 0
            except EOFError:
                print("    (pas de TTY — utilise --yes)")
                return 0
        n = apply_proposals(proposals, path)
        print(f"✅ {n} fait(s) 'relate' ajouté(s). Graphe toujours append-only.")
    elif a.apply:
        print("Rien à appliquer.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
