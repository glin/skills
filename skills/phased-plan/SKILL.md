---
name: phased-plan
description: "Execute a written multi-phase plan across sessions and model tiers: every phase is tagged with who implements and who verifies, one session runs one phase, and the plan file is the only handoff medium. Use when writing a plan too large for one context, when a fresh session must pick up a phase, or when implementer and verifier must not share a context. For delegating a single task to subagents inside one session, use fablecast."
---

# Phased Plan

fablecast delegates inside one session. This is the outer loop: a plan too large
for one context, executed phase by phase across sessions, with the plan file
carrying every decision from one session to the next.

The lead (the expensive interactive model) keeps the judgment: scouting, writing
the spec, defining the gates, verifying. Delegates (cheaper subagents) execute
mechanical work against that spec. The delegate-prompt contract itself is
fablecast's `### 4. Delegate`; invoke that skill and use it as written rather
than restating it inside the plan.

Invoke this skill at two moments: when WRITING a plan that will outlive one
context, and when a fresh session OPENS one.

## Tag every phase before execution starts

Each phase carries a tag naming its implementer and its verifier: `[LEAD]` for
judgment-heavy work (research, novel code, anything where a fidelity error is
silent), `[DELEGATE implement, LEAD verify]` for mechanical work against a spec
the lead writes.

Implementer and verifier are never the same model or context. A delegate's work
is verified by the lead; the lead's own work is verified by an independent
session or an external review.

The tags go in before execution starts, so a session picking up a phase knows its
role from the plan alone, with no conversational context.

## One session per phase

- A phase runs in a fresh or freshly compacted session. Never compact mid-phase.
  The plan file, the project's conventions doc, and agent memory are the only
  handoff medium.
- A phase ends handoff-complete: checkboxes ticked, results recorded INLINE in
  the plan (findings, verification numbers, surprises the next phase needs), and
  committed. A fresh session must be able to start the next phase from the plan
  alone.
- Only the human can compact or open a session, so the end-of-phase report states
  exactly what to do next ("compact now, then say 'start phase N'").
- Do NOT split the plan into per-phase files. Phases share decisions and
  taxonomies, and a split invites drift the moment one phase revises a shared
  decision. Delegate prompts quote the relevant plan sections verbatim instead.

## Write the phase's gate into the plan, and predict its numbers

A phase is not ready to delegate until the plan states a gate the lead can run
against the output.

- Define the gate as a property of the OUTPUT ("zero writers of class X survive
  on the artifact"), not of the effort ("the strip code was added"). An output
  property catches paths the implementer never considered.
- Record the expected numbers, from the scouted baseline, in the plan BEFORE the
  phase runs. A predicted-then-observed match is much stronger than a number
  rationalized after the fact, and a mismatch is a finding either way.
- Every exception the spec grants (an opt-out, a keep list) is re-checked against
  the invariant it could bypass, never exempted from the gate.

## What stays with the lead

Judgment-dense phases: research probes whose next step depends on the previous
result, novel code where a fidelity error is silent, and anything where writing
the spec costs more than doing the work. If the spec cannot state a verifiable
gate, the phase is not ready to delegate; scout more first.

Shared mutable resources, always: a single editor or IDE session, a running user
process, a bound port, git. The clean division is that the delegate edits files
and the lead owns everything live.
