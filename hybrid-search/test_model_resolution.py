#!/usr/bin/env python3
"""Regression guard: the arbiter/advisor must never target an unserved model.

The v3.6.0 bug: `conflict_resolver` and `consolidate_advisor` sent the bare name
`glm-5.2`, while the daemon serves `glm-5.2:cloud`. Ollama answered 404, the LLM
path fell through to the conservative heuristic, and *every* fact was classified
`COMPATIBLE / no confident relation detected` — arbitration was dead on arrival
and the `superseded`/`disputed` paths never executed once.

What this test pins:
  1. an explicit `*_LLM_MODEL` / `OLLAMA_MODEL` env var always wins;
  2. with no env var and a reachable daemon, the resolver returns a name the
     daemon actually serves (never an invented one);
  3. with the daemon unreachable, it still returns a tagged preference rather
     than crashing or returning a bare/empty name.

Usage:
    python3 hybrid-search/test_model_resolution.py
Exit 0 = all guards hold; exit 1 = at least one regression.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# (module, env var) — each module resolves its model at import time.
MODULES = [
    ("conflict_resolver", "CONFLICT_LLM_MODEL"),
    ("consolidate_advisor", "OLLAMA_MODEL"),
]


def _run(module, env):
    """Import `module` in a clean subprocess and print its resolved model."""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import %s as m;"
        "print(getattr(m, 'LLM_MODEL', None) or getattr(m, 'OLLAMA_MODEL', ''))"
        % (HERE, module)
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, **env},
        capture_output=True, text=True, timeout=60,
    )
    return proc


def test_explicit_env_wins():
    failures = []
    for module, var in MODULES:
        proc = _run(module, {var: "my-explicit-model:tag"})
        got = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if got != "my-explicit-model:tag":
            failures.append(f"{module}: explicit {var} ignored (got {got!r})")
        else:
            print(f"  OK  {module}: explicit {var} honoured")
    return failures


def test_resolves_to_served_model():
    """No env var: the returned name must be one the daemon serves."""
    import json
    import urllib.request
    failures = []
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            served = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
    except Exception:
        print("  !  daemon unreachable — skipping served-model assertion")
        return failures
    for module, var in MODULES:
        env = {k: v for k, v in os.environ.items() if k != var}
        proc = _run(module, env)
        got = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if got not in served:
            failures.append(f"{module}: resolved {got!r} which the daemon does not serve")
        else:
            print(f"  OK  {module}: resolved served model {got!r}")
    return failures


def test_unreachable_daemon_still_tagged():
    """Daemon down: must return a tagged preference, never crash/blank/bare."""
    failures = []
    for module, var in MODULES:
        env = {k: v for k, v in os.environ.items() if k != var}
        env["OLLAMA_URL"] = "http://127.0.0.1:59999"  # closed port
        proc = _run(module, env)
        if proc.returncode != 0:
            failures.append(f"{module}: crashed when daemon unreachable: {proc.stderr.strip()[-160:]}")
            continue
        got = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not got or ":" not in got:
            failures.append(f"{module}: returned unusable model {got!r} when daemon down")
        else:
            print(f"  OK  {module}: offline fallback {got!r}")
    return failures


def main():
    print("== test_model_resolution ==")
    print("[1/3] explicit env var wins")
    f1 = test_explicit_env_wins()
    print("[2/3] no env var -> a model the daemon actually serves")
    f2 = test_resolves_to_served_model()
    print("[3/3] daemon unreachable -> tagged fallback, no crash")
    f3 = test_unreachable_daemon_still_tagged()

    failures = f1 + f2 + f3
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("\nAll model-resolution guards hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
