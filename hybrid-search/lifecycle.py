#!/usr/bin/env python3
"""lifecycle.py — v5.4-P1 (P3): intention vs execution, never conflated.

The product question is "qu'avons-nous reellement fait ?" — which is
unanswerable if an intention is stored as an accomplishment. This module owns
the only sanctioned state machine.

States
------
  envisaged  : stated as a plan, no commitment
  decided    : a commitment was taken, nothing started
  started    : work actually began
  blocked    : started, but stopped by an obstacle
  done       : completed
  cancelled  : abandoned

Invariant
---------
  A transition INTO `done` requires an execution marker on the evidence
  (past-tense completion: "j'ai terminé", "c'est fait", "terminé", "déployé",
  "installé", "exécuté", "vérifié", "rendu"). An `envisaged` or `decided`
  memory is NEVER promoted to `done` on the strength of a plan sentence.

  This is the direct analogue of the GraphRAG rule "an inferred edge is
  refused": an inferred execution is refused.
"""
from __future__ import annotations

import re

STATES = ("envisaged", "decided", "started", "blocked", "done", "cancelled")

# Allowed transitions. `done` is absent from several sources on purpose.
TRANSITIONS = {
    "envisaged": {"decided", "cancelled"},
    "decided":   {"started", "cancelled"},
    "started":   {"blocked", "done", "cancelled"},
    "blocked":   {"started", "cancelled", "done"},
    "done":      set(),           # terminal
    "cancelled": set(),           # terminal
}

# A completion must be CLAIMED in the past tense. Bare "prévoir", "compter",
# "je vais", "il faudrait" are not execution evidence.
EXECUTION_MARKERS = re.compile(
    r"\b(j'ai (terminé|terminee?|fini|fini[e]?|déployé|deploye[e]?|installé|"
    r"installe[e]?|exécuté|execute[e]?|lancé|lance[e]?|vérifié|verifie[e]?|"
    r"rendu|publié|publie[e]?)|"
    r"c'est fait|terminé|terminee?|achevé|acheve[e]?|déployé|deploye[e]?|"
    r"livré|livre[e]?|exécuté|execute[e]?|vérifié|verifie[e]?|"
    r"a (été )?(terminé|terminee?|déployé|deploye[e]?|installé|installe[e]?|exécuté|execute[e]?)|"
    r"est (terminé|terminee?|déployé|deploye[e]?|installé|installe[e]?|fait|en production)|"
    r"sont (terminés|terminees?|déployés|deployes?|installés|installes?))\b",
    re.I,
)

# Intentions: future/conditional. These must never be read as execution.
INTENTION_MARKERS = re.compile(
    r"\b(je (vais|prévois|compte|pense|souhaite|veux)|"
    r"il faudrait|on devrait|on pourrait|à terme|un jour|"
    r"la semaine prochaine|demain|bientôt|prochainement|"
    r"peut-être|éventuellement|envisage)\b",
    re.I,
)

# Participle forms: "est terminée/déployée/installée" — the marker above only
# matched the masculine. French agreement was silently defeating completion
# detection, which is exactly the kind of hole that turns a done task into an
# "envisaged" one.
EXECUTION_MARKERS_F = re.compile(
    r"\b(est (terminée|déployée|installée|achevée|livrée|exécutée)|"
    r"sont (terminées|déployées|installées|achevées|livrées|exécutées))\b",
    re.I,
)

BLOCKED_MARKERS = re.compile(
    r"\b(bloqué|bloquée|bloqués|en attente|coincé|coincée|impossible|"
    r"ne peut pas|échoue|échoué|erreur persistante)\b", re.I)


def infer_state(text: str, explicit: str | None = None) -> str:
    """Infer the lifecycle state of a statement, or return the explicit one.

    Order matters: an intention marker WINS over an execution marker, because
    "j'ai décidé que je vais déployer" is a decision, not a deployment.
    """
    if explicit:
        if explicit not in STATES:
            raise ValueError(f"unknown lifecycle state: {explicit}")
        return explicit

    t = text or ""
    if INTENTION_MARKERS.search(t):
        # An intention is at most `decided` when the user committed explicitly.
        if re.search(r"\b(j'ai décidé|nous avons décidé|décision|on garde|"
                     r"je garde|on choisit|je choisis)\b", t, re.I):
            return "decided"
        return "envisaged"
    if BLOCKED_MARKERS.search(t):
        return "blocked"
    if EXECUTION_MARKERS.search(t) or EXECUTION_MARKERS_F.search(t):
        return "done"
    if re.search(r"\b(j'ai commencé|en cours|je travaille sur|en chantier)\b", t, re.I):
        return "started"
    if re.search(r"\b(j'ai décidé|nous avons décidé|décision|"
                 r"je garde|on garde|je choisis|on choisit)\b", t, re.I):
        return "decided"
    return "envisaged"


def can_transition(old: str | None, new: str) -> tuple[bool, str]:
    """Validate a state transition. Returns (allowed, reason)."""
    if new not in STATES:
        return False, f"unknown state {new!r}"
    if old is None:
        return True, "initial state"
    if old == new:
        return True, "unchanged"
    if old in ("done", "cancelled"):
        return False, f"{old} is terminal"
    allowed = TRANSITIONS.get(old, set())
    if new not in allowed:
        return False, f"{old} -> {new} is not allowed (allowed: {sorted(allowed)})"
    return True, "ok"


def assert_not_inferred_execution(text: str, new_state: str) -> None:
    """Refuse a `done` claim that the evidence does not support."""
    if new_state != "done":
        return
    if INTENTION_MARKERS.search(text or ""):
        raise ValueError(
            "refused: the evidence carries an intention marker, so it cannot "
            "justify state='done'. An intention is not an execution."
        )
    if not EXECUTION_MARKERS.search(text or ""):
        raise ValueError(
            "refused: state='done' requires an explicit execution marker in "
            "the evidence (see EXECUTION_MARKERS)."
        )
