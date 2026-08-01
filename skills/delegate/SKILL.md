---
name: delegate
description: "Orchestrate a non-trivial task, and keep bulk file reading out of the main context. An expensive orchestrator discovers and specs, executor subagents implement in parallel at a tier matched to each unit, and every result is re-verified before it lands. Saves context tokens by isolating exploration and execution in subagents. Portable to any repo. Use when: delegate this, split this up, fan out, orchestrate, batch of similar edits, multi-file change, migration, broad research, or when answering a question would mean reading many files. For a written plan whose phases span multiple sessions, use phased-plan, which invokes this skill inside its delegating phases."
---

# Delegate

Plan-and-delegate orchestration. The main session (the orchestrator) keeps the
expensive judgment work (discovery, decomposition, verification) and hands
tightly-scoped execution to executor subagents, whose tier scales with how much
judgment the spec leaves them.

Speak in roles, not model names, so the pattern survives model changes. The
orchestrator is whatever model the interactive session already runs; it is not
selectable from here. Executors are spawned per unit at the top, middle, or
cheapest tier. A top-tier orchestrator with middle-tier executors is the common
shape, not a compromise.

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
- **Verifier** (fresh-context subagent): adversarially checks a result. Use for
  risky or hard-to-reverse changes, and whenever several executors contributed:
  re-verification checks each unit against its own spec, so a hole in the spec
  passes every unit and still ships.

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
- Any resource only one unit can hold at a time: a long-running process, a device,
  a licence seat, a shared service instance.

Put what you learn in writing. It becomes the executors' context.

### 3. Decompose
Split into executor-sized units. Prefer clean file boundaries so units run in
parallel without conflict; use an ordered pipeline when one unit depends on
another. Name exactly what each unit may touch. Size waves to how many executors
the runtime runs at once, not to how many could run without conflict: the
surplus queues silently, so a written plan naming more stops describing the run.
For a large fan-out, pass the
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

Model selection depends on the agent client:

- **Codex with Amazon Bedrock:** omit `model` and `reasoning_effort` from
  Agent spawns so the child inherits the configured provider-qualified model.
  Before a large fan-out, run one minimal canary spawn. If it fails, stop and
  diagnose the first error instead of trying alternate model names.
- **Codex with providers that accept explicit Agent model overrides:** set
  `model` on every spawn and match the tier to the unit:
  - Read-only search, file location, and convention sweeps use the cheapest tier.
  - Fully specified implementation uses the middle tier.
  - Work that stays hard after specification uses the top tier.
- **Claude Code:** set `model` on every Agent spawn. Use `haiku` for
  read-only search, `sonnet` for fully specified implementation, and `opus`
  only when the unit stays hard after specification.

Discovery and verification are the two things never pushed downward; they are why
the orchestrator is expensive. The role split, not the price tag, is what makes
this work. Keep parallel units on non-overlapping files, and forbid any repo-global
command that mutates shared state (`git stash`/`reset`/`checkout`/`clean`, or a
formatter or build that rewrites shared files or caches): non-overlapping edits
still collide through a shared git index or build cache, so a sibling's `stash`
or `reset` can silently wipe another unit's work. A unit that must build or run
repo-wide tooling gets its own worktree. A resource no worktree can duplicate
(step 2) is yours alone: forbid executors from touching it and batch its use
between waves. That restraint binds you too while executors are live, since an
executor cannot tell your temporary breakage from a real failure: commit before
deliberately breaking anything, restore with git rather than by hand, and never
carry a break across a wave boundary. For a scripted, repeatable
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
the baseline. Do not re-quote an executor's full report in later turns; isolating
it only pays off if its raw output never enters this context. Refer back by unit
and finding instead.

## Why the asymmetry matters

The load-bearing invariant is the role split, not the executor's price.
Separating orchestration from execution buys context isolation, parallelism, and
a fresh context to verify in, at any executor tier. A cheaper executor buys only
cost, so reach for it when the spec makes execution mechanical and let the
re-verify gate catch slips; keep the top tier when the work stays hard. Either
way, never cheapen discovery or verification, that is where a foreign repo bites:
the executor will confidently produce something that compiles and is wrong. Keep
judgment expensive; scale execution to the task.
