---
name: fablecast
description: Orchestrate a non-trivial task. An expensive orchestrator discovers and specs, executor subagents implement in parallel at a tier matched to each unit, and every result is re-verified before it lands. Portable to any repo. Invoke when a task is big enough to split and worth delegating. For a written plan whose phases span multiple sessions, use phased-plan, which invokes this skill inside its delegating phases.
---

# FableCast

Plan-and-delegate orchestration. The main session (the orchestrator) keeps the
expensive judgment work (discovery, decomposition, verification) and hands
tightly-scoped execution to executor subagents, whose tier scales with how much
judgment the spec leaves them.

Speak in roles, not model names, so the pattern survives model changes: today
Fable orchestrates and Opus, Sonnet, or Haiku execute depending on the unit, but
the roles do not change when the models rotate.

## When to use, when not

Use when the task is big enough to split into independent or ordered units and
the delegation overhead pays off: multi-file changes, a batch of similar edits,
broad research, a migration.

Do not use for trivial work (one file, mechanical, quick). The orchestration
overhead loses. Just do it inline.

## Roles

- **Orchestrator** (the main session): scopes, discovers ground truth,
  decomposes, writes executor prompts, re-verifies, integrates. Never cheapen
  this role.
- **Executor** (subagent, tier chosen per unit): implements one tightly-scoped
  unit against a precise prompt. Drop to a cheaper tier only when the spec makes
  the work mechanical; keep it at the top tier when the unit stays genuinely hard
  even fully specified. If a unit needs top-tier reasoning to make design calls,
  that is judgment leaking downward, pull it back into the orchestrator rather
  than just buying a smarter executor.
- **Verifier** (optional, fresh-context subagent): adversarially checks a
  high-stakes result. Use for risky or hard-to-reverse changes.

## Procedure

### 1. Scope
Is this actually worth delegating? If it is one small mechanical change, stop and
do it yourself. Otherwise continue.

### 2. Discover (orchestrator, before any delegation)
Establish ground truth yourself. In a repo you do not know, this is where
correctness is won:
- The verification command (test, build, lint, run) and how to exercise the change.
- The baseline: what passes now, and the base commit. Record both.
- The files, conventions, and any gotchas an executor could not discover on its own.

Put what you learn in writing. It becomes the executors' context.

### 3. Decompose
Split into executor-sized units. Prefer clean file boundaries so units run in
parallel without conflict; use an ordered pipeline when one unit depends on
another. Name exactly what each unit may touch. For a large fan-out, pass the
written spec through a fresh-context critic before delegating: a conflicting or
wrong instruction here multiplies across every executor, and it is far cheaper to
catch in the spec than in N parallel diffs.

### 4. Delegate
Spawn one executor per unit at a tier matched to the unit (see the Executor role
above). Every executor prompt carries five things:
1. The verified facts you discovered (paths, APIs, the baseline tally) so it does
   not re-derive or guess.
2. Hard constraints: which files it may touch, and what it must not do (no commits
   or pushes, do not kill running processes, stay in scope).
3. Concrete deliverables.
4. The exact verification command and the result required to pass.
5. A request for an honest report, including any deviations.

In Claude Code, spawn executors with the Agent tool and set `model` on EVERY
spawn. A subagent with no `model` inherits the parent's, so a top-tier session
that fans out search or scouting agents without setting it is paying top-tier
rates to grep. Match the tier to the unit:

- Read-only search, file location, convention sweeps: the cheapest tier
  (`model: haiku`). This is the default for exploration, never the parent's tier.
- Implementation the spec fully determines: the middle tier (`model: sonnet`).
- Units that stay hard even when fully specified: the top tier (`model: opus`).

Discovery and verification are the two things never pushed downward; they are why
the orchestrator is expensive. The role split, not the price tag, is what makes
this work. Keep parallel units on non-overlapping files, and forbid any repo-global
command that mutates shared state (`git stash`/`reset`/`checkout`/`clean`, or a
formatter or build that rewrites shared files or caches): non-overlapping edits
still collide through a shared git index or build cache, so a sibling's `stash`
or `reset` can silently wipe another unit's work. A unit that must build or run
repo-wide tooling gets its own worktree. For a scripted, repeatable
fan-out over a known work-list, reach for the Workflow tool instead; use this
skill for model-driven planning.

An executor may commit only when it runs alone: splitting one file's mixed
changes into separate commits needs the index, so it cannot run beside a
sibling. Give it the exact `@@` hunk headers per commit and forbid repair or
fallback commands, on any hunk mismatch it stops and reports.

### 5. Re-verify (orchestrator)
Do not trust an executor's "done." Re-run the verification yourself, compare to
the baseline, and read the actual artifact. Only then integrate. If a unit
regressed, restore the known-good state before re-delegating.

### 6. Report
Summarize what each executor did, any deviations, and the verified delta against
the baseline.

## Why the asymmetry matters

The load-bearing invariant is the role split, not the executor's price.
Separating orchestration from execution buys context isolation, parallelism, and
a fresh context to verify in, at any executor tier. A cheaper executor buys only
cost, so reach for it when the spec makes execution mechanical and let the
re-verify gate catch slips; keep the top tier when the work stays hard. Either
way, never cheapen discovery or verification, that is where a foreign repo bites:
the executor will confidently produce something that compiles and is wrong. Keep
judgment expensive; scale execution to the task.
