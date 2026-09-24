---
name: preplan
description: "Choose a cost-efficient execution shape before a non-trivial agent task. Produces a bounded brief with model roles, session topology, verification budget, permissions, return path, stop conditions, and a copy-ready execution prompt. Use when the user asks how to run work cheaply, requests model and orchestration advice together, wants an autonomous one-prompt workflow, or wants to compare inline work, subagents, fresh-session handoffs, and phased execution. Do not use for a trivial task whose execution is cheaper than planning it."
---

# Preplan

Choose the least expensive workflow that has enough capability and verification to produce an accepted result. Optimize the whole job, including planning, implementation, retries, verification, review, user attention, and integration.

This skill plans execution. It does not implement, launch agents, change settings, or grant permissions unless the user explicitly asks the same turn to plan and execute.

## Keep the preplan cheaper than the work

Read the user's task, its plan or handoff, repository instructions, current state, and standard verification command. Batch independent inspection into as few reads as practical. Stop discovery when the execution shape and risk are clear.

Do not load personal model notes or historical evals unless the user asks to use them. Verify current model availability, effort levels, and prices from the active runtime and official provider sources when they affect the recommendation. Distinguish API prices, subscription usage, credits, and estimated list-price equivalents.

Skip this skill when one bounded edit and one focused check will cost less than the planning pass.

## Classify the task

Record only facts that change execution:

- Durable result: answer, report, artifact, commit, landed change, or live application state.
- Specification: settled, incomplete, or dependent on owner judgment.
- Scope: local, cross-file, cross-component, or repository-wide.
- Failure visibility: immediate, test-detectable, review-detectable, or silent.
- Verification: none, subjective, focused checks, strong automated checks, or live application proof.
- State: shared checkout, mutable service, browser, IDE, device, account, port, cache, or external approval.
- Expected duration and whether the work fits one coherent context.
- Actions that need advance authority and conditions that still require the user.

## Choose the session topology

Prefer the smallest topology that preserves ownership and evidence.

- Work inline when the task is small, coherent, and already in a useful context.
- Start one fresh root session when one agent can own a self-contained task and the current context is mostly unrelated.
- Use a subagent when its concise result changes the active parent's next decision.
- Use a fresh-session handoff when an independent session can return a commit or report through a durable path.
- Use phased execution when one context cannot hold the work without becoming workflow state.

Do not keep a coordinator alive only to relay a self-contained result. Do not split coherent implementation across agents merely to use cheaper models. Parallelize writers only across disjoint files and mutable resources.

Read [references/handoffs.md](references/handoffs.md) before recommending a subagent, fresh-session handoff, callback, or detached worker.

## Choose models and effort

Select the cheapest available model with enough capability for the judgment that remains after the brief.

- Use the cheapest tier for mechanical search, inventory, extraction, and deterministic transformations.
- Use a balanced tier for bounded implementation or analysis with strong checks and settled decisions.
- Use a top tier for unresolved architecture, subtle state, broad discovery, weak verification, or costly silent failure.
- Use a fresh reviewer only when plausible failures justify its cost.
- Use a stronger reviewer to challenge a well-specified cheaper implementation, not to repair an unresolved specification.

A thin coordinator needs no premium model. Eliminate that role when a fresh root can own the result. Increase effort only when the remaining judgment or relevant evidence justifies it.

State confidence and the condition that triggers escalation to a stronger model or another review.

## Budget the execution

Estimate a range rather than a false exact price. State every assumption that materially changes it.

Include:

- Number of long-lived model contexts.
- Model and effort for each role.
- Expected full-suite, browser, build, package, and live-application runs.
- Review and likely correction rounds.
- Permission-review or automation overhead.
- Known expensive reads, generated artifacts, and shared resources.
- Stop conditions for timeouts, retries, compactions, target movement, and ambiguous state.

Prefer focused checks during implementation and the smallest decisive full gate at the immutable candidate. Every retry must test a new correction or measurement question. Use operating-system waits and callbacks instead of model polling.

## Plan autonomy

List the exact actions an unattended run needs authority to perform. Keep authority scoped to named worktrees, commands, applications, branches, and result paths.

A workflow is fully autonomous only when it has:

- A settled specification.
- A named implementation owner.
- Objective completion checks.
- Required permissions at launch.
- A durable progress and result path.
- A return channel or an explicit statement that the user must resume it.
- Stop conditions for ambiguity and irreversible actions.

Do not call a detached run autonomous when no supported mechanism can notify or resume its origin.

## Produce one execution brief

Lead with the recommended topology, models, effort, and confidence. Then provide:

1. Why this topology wins for the task.
2. Role ownership and session boundaries.
3. Cost range and assumptions.
4. Verification schedule.
5. Permissions and user-intervention conditions.
6. Return path and completion signal.
7. Timeout, retry, compaction, and escalation rules.
8. One copy-ready prompt for the selected execution session.
9. One cheaper alternative when it changes the tradeoff meaningfully.

If the user authorized execution, pass the brief to the applicable execution skill after presenting the estimate. Use `orchestrate` for one end-to-end session, `dispatch` for a fresh-session handoff, `delegate` for bounded child work, and `phased-plan` for multiple sessions.
