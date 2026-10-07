---
name: agent-ready-repo-audit
description: "Audit a software repository for correctness risks, maintainability, test-suite health, drift, and safe parallel agent work, then produce evidence-backed priorities and a phased plan. Use when: full codebase audit, agent-readiness audit, maintainability audit, duplication or drift review, test-suite bloat, modularity planning, or a team discoverability or onboarding review (can a new teammate or agent find and maintain this repository). Do not use for reviewing only the current diff or implementing a known change."
---

# Agent-ready repository audit

Produce a measured audit and an implementation order. The goal is safer change, not more folders, fewer tests, or a cleaner-looking diagram.

## Boundaries

- Treat the audit as read-only unless the author also asks for implementation.
- Read the repository's instructions, design goals, and architecture before judging structure. Evaluate proposals against the product's requirements.
- Preserve unrelated working-tree changes. Record the starting commit and status.
- Do not use file length, test count, directory depth, or abstraction count as quality targets. Use them to locate ownership and conflict hotspots.
- Keep source-specific behavior visible when differences carry product value. Share contracts and primitives, not a lowest-common-denominator abstraction.
- Treat delegated findings as hypotheses until the main agent opens the cited code and confirms the failure mode.
- Do not add instructions where a type, test, lint rule, generated registry, or runtime validation can enforce the rule.

## Workflow

### 1. Establish ground truth

Identify:

- the current commit, branch, working-tree state, and concurrent worktrees;
- the standard verification commands and platform-specific caveats;
- the product requirements that decide whether a proposed boundary belongs;
- generated, bundled, persisted, or externally supplied data that ordinary source inspection can miss.

If the repository has a reading guide, follow it. If documentation contradicts reachable code, report the contradiction and confirm which behavior is live before recommending an edit.

### 2. Measure the baseline

Run the normal verification gate when it is safe. Record exact pass, fail, and skip results plus wall time. Distinguish product failures from harness, permissions, environment, and checkout-contamination failures.

Measure the distribution before optimizing tests. A large test count is not evidence of a slow suite. Identify which files, processes, browsers, waits, fixtures, or repeated setup own the elapsed time.

An audit that sorts items into kinds, such as duplicate tests or tests that need no browser, starts from a script-built list of every item. Every item on the list gets a verdict. A subagent report without a verdict for each listed item counts as incomplete coverage.

Inventory formatters, linters, type checkers, test runners, build tools, package checks, and their CI owners. Distinguish a missing capability from an unfamiliar tool choice. Identify custom scripts that duplicate maintained tooling, then compare their exact contracts before recommending replacement.

Inventory source and test files by size and responsibility. Inspect dependency direction, central registries, persistence boundaries, browser or process boundaries, and files frequently edited together. Counts locate places to read; they do not create findings by themselves.

### 3. Audit by independent lanes

Read [references/rubric.md](references/rubric.md). For a focused request, read only the relevant lanes. For a full audit, cover every lane.

When delegation is authorized and lanes have separate ownership, assign read-only lanes independently. Give each worker the baseline, permitted files, evidence format, and explicit instruction not to edit. Keep discovery, synthesis, and final verification in the main session.

### 4. Prove each finding

Every finding must include:

1. Priority.
2. File and symbol evidence.
3. A concrete failure or maintenance cost.
4. The smallest durable treatment.
5. A check that would prove the treatment.

Separate confirmed defects, drift risks, and optional organization. Similar code is not automatically duplication: show that both copies represent the same contract and can change independently. A test is not redundant until another test exercises the same behavior at the same boundary.

### 5. Order the plan by dependency

Prefer this sequence unless repository evidence requires another:

1. Make verification and architecture guards trustworthy.
2. Repair correctness, persistence, concurrency, validation, and bounded-work risks.
3. Establish neutral domain contracts and single sources of truth.
4. Split browser, process, reader, host, or view boundaries along ownership seams.
5. Reduce controller and test hotspots incrementally.
6. Align documentation and scoped instructions after the physical boundaries exist.

Avoid a broad move-and-rewrite. File movement, behavioral change, test cleanup, and comment cleanup should usually be separate commits.

Write the plan where the repository expects plans. Include exit criteria, verification commands, owner decisions, and workstreams that can proceed without editing the same files. Do not edit current-state architecture documentation until implementation changes a component job, data source, or non-obvious constraint.

### 6. Prototype only definite improvements

If the author asks for implementation, choose a low-risk change whose effect can be measured and whose coverage can remain intact. Use an isolated worktree when requested or when the main checkout contains concurrent work. Record before and after results. Do not merge or publish unless the author explicitly asks.

Optimization must preserve the boundary being tested. Reuse expensive infrastructure when fresh per-test state still provides isolation. Add fast and full lanes rather than weakening the default gate. Remove tests only with a coverage map that proves equivalent behavior remains.

### 7. Promote each learning

Before closing, route every durable learning to the strongest appropriate mechanism:

1. Single source of truth by construction.
2. Compile-time type or exhaustive dispatch.
3. Test, lint rule, architecture guard, or runtime validator.
4. Diagnostic at the point of failure.
5. Shared developer documentation.
6. Repository instruction only when the stronger mechanisms cannot carry it.
7. Reusable skill when the procedure applies across repositories.

Do not preserve incident history in source comments or always-loaded instructions. Once an executable guard carries a rule, remove prose that only repeats it.

## Deliverable

Lead with the overall condition of the repository and the few changes with the highest leverage. Include:

- baseline and verification confidence;
- prioritized findings with evidence and failure modes;
- strengths worth preserving;
- a target dependency shape, if structural change is justified;
- a phased plan with exit criteria;
- safe parallel work ownership;
- decisions that require the repository owner;
- measured prototype results, if implementation was requested.

Be direct about what should not change. A repository with strong behavioral tests and weak physical boundaries usually needs consolidation, not a rewrite.
