#!/usr/bin/env python3
"""model_guard.py — v5.4-P1 (SYN-01 P0): fail-closed local-model enforcement.

Why this module exists
----------------------
An URL of http://127.0.0.1:11434 does NOT prove local inference. The Ollama
daemon serves whatever is registered on it, including proxied ":cloud" models
(deepseek-v4.1-flash:cloud, glm-5.2:cloud, ...). A loopback check therefore
guards the transport, not the computation: the prompt still leaves the machine.

This module refuses, before any HTTP call is made:
  * a model whose resolved name carries a cloud marker
  * a model the local daemon does not actually serve
  * an embedding model of the wrong dimension for the schema
  * an endpoint that is not loopback

Cloud rejection policy
----------------------
A model name is refused when its tag carries the token `cloud` in ANY form.
The rule is intentionally broader than the literal suffix ":cloud", because a
counter-audit proved that the earlier `:cloud\\b` pattern accepted `:cloudy`,
`:clouds` and `:cloud2` (\\b requires a word boundary, which those lack).

Rejected, case-insensitively, after stripping whitespace:
  * any tag beginning with `cloud`            :cloud  :cloudy  :clouds  :cloud2
  * any tag beginning with `clouds`           :clouds  :clouds-2
  * the token `cloud` bounded by separators   -cloud   _cloud   /cloud
A false positive is acceptable; a false negative is not. Local models
authorised by SYN-01 (`qwen2.5:7b`, `nomic-embed-text`) contain no `cloud`
token and are unaffected.

Everything here fails closed: a doubt is a refusal, never a fallback.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

# The only models SYN-01 authorises. Anything else is refused.
SYN01_EXTRACTION_MODEL = "qwen2.5:7b"
SYN01_EMBEDDING_MODEL = "nomic-embed-text"
SYN01_EMBEDDING_DIM = 768

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# GO-C1: the counter-audit showed `:cloud\\b` accepted :cloudy/:clouds/:cloud2.
# Rule: the tag (everything after the last ':' separator that starts a tag) OR
# any separator-delimited token beginning with "cloud" is rejected. Broader on
# purpose — a false positive costs a rename, a false negative ships the prompt.
CLOUD_MARKER = re.compile(
    r"(?:^|[:/_-])cloud",   # tag or separator-delimited token starting with cloud
    re.I,
)


class ModelGuardError(RuntimeError):
    """Raised when a model, endpoint or dimension would escape local inference."""


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def assert_loopback(url: str, name: str) -> None:
    """Refuse any endpoint that is not loopback."""
    host = _host_of(url)
    if host not in LOOPBACK_HOSTS:
        raise ModelGuardError(
            f"{name}: endpoint {url!r} resolves to host {host!r}, which is not "
            f"loopback. Refusing (fail-closed)."
        )


def assert_not_cloud(model: str, role: str = "model") -> None:
    """Refuse any model name carrying the ':cloud' marker."""
    if not model or not str(model).strip():
        raise ModelGuardError(f"{role}: empty model name. Refusing (fail-closed).")
    if CLOUD_MARKER.search(str(model)):
        raise ModelGuardError(
            f"{role}: model {model!r} is a CLOUD model. Local inference cannot be "
            f"guaranteed; the prompt would leave this machine. Refusing (fail-closed)."
        )


def assert_served_locally(model: str, served: frozenset, role: str = "model") -> None:
    """Refuse a model the local daemon does not actually serve."""
    if model not in served:
        raise ModelGuardError(
            f"{role}: model {model!r} is not served by the local daemon "
            f"(served: {sorted(served)[:6]}). Ollama would pull it synchronously "
            f"or proxy it. Refusing (fail-closed)."
        )


def assert_embedding_dim(dim: int | None, schema_dim: int = SYN01_EMBEDDING_DIM) -> None:
    """Refuse an embedding model whose dimension does not match the schema."""
    if dim is None:
        raise ModelGuardError("embedding: dimension unknown. Refusing (fail-closed).")
    if int(dim) != int(schema_dim):
        raise ModelGuardError(
            f"embedding: dimension {dim} does not match schema {schema_dim}. "
            f"Vectors would be rejected or silently mis-matched. Refusing (fail-closed)."
        )


def require_env(var: str) -> str:
    """Read a mandatory override variable, refusing an absent or cloud value."""
    val = (os.environ.get(var) or "").strip()
    if not val:
        raise ModelGuardError(
            f"{var} is not set. SYN-01 requires an explicit local model for every "
            f"inference path; without it llm_resolution falls back to the gateway "
            f"primary, which is a cloud model. Refusing (fail-closed)."
        )
    assert_not_cloud(val, role=var)
    return val
