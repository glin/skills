---
name: phased-plan
description: "Design phases and durable handoffs for work that spans sessions. Use when splitting a large plan into independently verifiable phases or resuming one from its saved state. For child assignments within one session, use delegate."
---

# Phased plan

Use this skill when writing a plan that spans sessions or resuming one. The plan must carry the decisions and evidence needed to continue without the previous conversation.

## Define the phases

Split work at boundaries with an observable result and a useful stopping point. Record dependencies so a later session can identify the next ready phase.

For each phase, record:

- Scope and dependencies, including prerequisites and exclusions.
- The execution workflow for the phase.
- Implementer and verifier roles, chosen for the work rather than a fixed model hierarchy.
- Acceptance checks that test the result, including exceptions that can bypass the intended property.
- Results and evidence, including checks run, unresolved findings, and relevant commit or artifact paths.
- The next runnable step and any blocker or required authorization.

When numbers clarify acceptance, record the baseline and expected result before the phase runs. Explain observed mismatches. A matching number alone does not prove correctness.

Use the selected execution workflow's review requirements. When independent verification is required, use a separate context. The verifier can use the same model.

## Keep a durable handoff

Use [plan-files](../plan-files/SKILL.md) for locations, common templates, status conventions, commits, and archival.

Maintain one plan entrypoint with phase order and shared decisions. Link supporting specifications and evidence instead of duplicating them across phase documents.

Before compaction or a session change, save enough state to resume the current phase. Record unfinished work and live resources that need continued ownership. A transition does not require completing the phase.

Existing plans retain their structure. Normalize a phase's handoff information when that phase is next edited, without rewriting settled records.

## Resume and execute

Read the plan, its relevant supporting material, and repository instructions. Check recorded results against the current repository state before choosing the next ready step.

Pass authorized work to the selected execution workflow. It owns implementation, resource isolation, review, budgets, continuation, and delivery. Use [orchestrate](../orchestrate/SKILL.md) for end-to-end repository work. Use [delegate](../delegate/SKILL.md) for bounded child assignments.

Continue in the current session while its context remains useful. Use a fresh session when the work needs isolation or a new context. The runtime and existing authorization determine available transitions.

A ready phase supplies no authority to execute it. Preserve phase-only restrictions and recorded approvals when preparing the next handoff.
