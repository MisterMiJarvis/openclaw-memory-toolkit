# Contributing — OpenClaw Memory Toolkit

This file is for **maintainers of this repository**. It documents how a change
travels from the repo to the published artifact, and the invariants the release
gate enforces. If you only want to *use* the skill, read the [README](README.md)
instead.

## Release Pipeline (repo → skill → GitHub → ClawHub)

> **MUST — every memory-skill change goes through `scripts/release.sh`.**
> No exception. A change to `skills/memory-health/**` is not "done" until
> `scripts/release.sh check` passes green. Do not tag, do not push a release,
> do not hand anything to ClawHub before that. If the gate fails, fix the drift
> or the invariant — never bypass the gate.

One artifact, one direction, four stages. **Never edit the installed skill directly;
never let the repo and the skill drift.**

```
  local repo          installed skill            GitHub            ClawHub
  .work/mh-v213  ──▶  skills/memory-health/  ──▶  push main + tag ──▶  manual (Stéphane)
```

1. **Edit in the repo** (`.work/mh-v213`). Commit there.
2. **Sync repo → skill.** The installed skill is what the agent and the nightly
   cron actually load, so it must be updated from the repo, never the reverse.
   `SKILL.md` is the one exception: the skill copy carries a YAML frontmatter
   (`name:` / `description:`) that the repo omits, so its **body** is synced while
   the frontmatter is preserved.
3. **Run the gate:** `scripts/release.sh check`. It refuses to pass until
   repo↔skill files are byte-identical (SKILL body), `trace_extractor` is a single
   source, version markers and OLLAMA_* loopback invariants hold, every script
   parses, the loopback test passes and the tree is clean.
4. **Tag + push:** `scripts/release.sh release vX.Y.Z "message"`, then create the
   GitHub release.
5. **ClawHub** is published by the operator, from the repo.

## Known single-source exception

`trace_extractor.py` exists in two places: the repo and
`skills/trace-extractor/trace-extractor.py`. The skill copy is the **live** one
(the cron uses it). The gate compares them and warns on divergence; port the live
copy into the repo before tagging so they converge forward.

## Why the gate is non-negotiable

The repo and the installed skill are two copies of the same artifact. The moment
they drift, the cron runs one version while GitHub shows another, and nothing
tells you which is real. The gate exists to make drift impossible to ship
silently. A skipped gate is a defect waiting for a quiet month to surface.
