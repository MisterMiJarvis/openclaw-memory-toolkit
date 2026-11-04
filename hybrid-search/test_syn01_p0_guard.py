#!/usr/bin/env python3
"""test_syn01_p0_guard.py — SYN-01 P0.

No network. Every test either exercises model_guard directly or captures the
payload that auto_capture / conflict_resolver WOULD send, by stubbing urlopen.
A failing negative test must raise before any HTTP call is attempted.
"""
import importlib
import io
import json
import os
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_guard as MG

PASS = FAIL = 0
FAILURES = []


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  OK   {name}")
    except AssertionError as e:
        FAIL += 1
        FAILURES.append((name, str(e)))
        print(f"  FAIL {name}: {e}")
    except Exception as e:
        FAIL += 1
        FAILURES.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERR  {name}: {type(e).__name__}: {e}")


def expect_guard(fn, label):
    try:
        fn()
    except MG.ModelGuardError:
        return
    raise AssertionError(f"{label} did not raise ModelGuardError")


# ─── garde-fou : modèle cloud ────────────────────────────────────────────────
print("\n[1] refus des modèles cloud")
check(":cloud rejeté", lambda: expect_guard(
    lambda: MG.assert_not_cloud("deepseek-v4.1-flash:cloud"), "cloud name"))
check("cloud avec provider prefix rejeté", lambda: expect_guard(
    lambda: MG.assert_not_cloud("ollama/deepseek-v4-pro:cloud"), "prefixed cloud"))
check("nom vide rejeté", lambda: expect_guard(
    lambda: MG.assert_not_cloud(""), "empty"))
check("modèle local accepté", lambda: MG.assert_not_cloud("qwen2.5:7b"))
check("embedding local accepté", lambda: MG.assert_not_cloud("nomic-embed-text"))

# ─── GO-C1: contournements du pattern précédent `:cloud\\b` ─────────────────────
print("\n[1bis] contournements ':cloud\b' fermés")
for bad in ("x:cloudy", "x:clouds", "x:cloud2", "x:clouds-2", "MODEL:Cloudy",
            "x-cloud", "x_cloud", "x/cloud", "  x:cloud  "):
    check(f"{bad!r} refusé", (lambda b=bad: expect_guard(
        lambda: MG.assert_not_cloud(b), b)))

# ─── faux positifs : les modèles locaux autorisés passent ────────────────────
print("\n[1ter] faux positifs sur modèles locaux")
for good in ("qwen2.5:7b", "nomic-embed-text", "nomic-embed-text:latest",
             "mxbai-embed-large:latest", "llama3.2:3b", "mistral:7b-instruct"):
    check(f"{good!r} accepté", (lambda g=good: MG.assert_not_cloud(g)))

# ─── garde-fou : endpoint ────────────────────────────────────────────────────
print("\n[2] refus des endpoints non loopback")
check("http://10.0.0.5 rejeté", lambda: expect_guard(
    lambda: MG.assert_loopback("http://10.0.0.5:11434", "URL"), "remote"))
check("https://ollama.com rejeté", lambda: expect_guard(
    lambda: MG.assert_loopback("https://ollama.com/api", "URL"), "public"))
check("127.0.0.1 accepté", lambda: MG.assert_loopback("http://127.0.0.1:11434", "URL"))
check("localhost accepté", lambda: MG.assert_loopback("http://localhost:11434", "URL"))

# ─── garde-fou : modèle non servi ────────────────────────────────────────────
print("\n[3] refus d'un modèle non servi localement")
served = frozenset({"qwen2.5:7b", "nomic-embed-text:latest"})
check("modèle absent rejeté", lambda: expect_guard(
    lambda: MG.assert_served_locally("llama-99b", served), "not served"))
check("modèle servi accepté",
      lambda: MG.assert_served_locally("qwen2.5:7b", served))

# ─── garde-fou : dimension embedding ─────────────────────────────────────────
print("\n[4] refus d'une dimension d'embedding incompatible")
check("dim 3584 rejetée", lambda: expect_guard(
    lambda: MG.assert_embedding_dim(3584), "qwen native dim"))
check("dim inconnue rejetée", lambda: expect_guard(
    lambda: MG.assert_embedding_dim(None), "None dim"))
check("dim 768 acceptée", lambda: MG.assert_embedding_dim(768))

# ─── garde-fou : variable obligatoire ────────────────────────────────────────
print("\n[5] variable d'environnement obligatoire")
def _env_absent():
    os.environ.pop("SYN01_TEST_VAR", None)
    expect_guard(lambda: MG.require_env("SYN01_TEST_VAR"), "absent env")
def _env_cloud():
    os.environ["SYN01_TEST_VAR"] = "deepseek-v4.1-flash:cloud"
    try:
        expect_guard(lambda: MG.require_env("SYN01_TEST_VAR"), "cloud env")
    finally:
        os.environ.pop("SYN01_TEST_VAR", None)
def _env_local():
    os.environ["SYN01_TEST_VAR"] = "qwen2.5:7b"
    try:
        assert MG.require_env("SYN01_TEST_VAR") == "qwen2.5:7b"
    finally:
        os.environ.pop("SYN01_TEST_VAR", None)
check("variable absente -> refus", _env_absent)
check("variable pointant un cloud -> refus", _env_cloud)
check("variable locale -> acceptée", _env_local)

print(f"\nRÉSULTAT garde-fou : {PASS} pass, {FAIL} fail")
sys.exit(1 if FAIL else 0)
