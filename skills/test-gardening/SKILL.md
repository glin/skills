---
name: test-gardening
description: Audit and prune a test suite by failure boundary, with executable coverage proofs and matched timing. Use for test-suite gardening, duplication audits, or runtime reduction work, not ordinary feature tests.
---

# Test gardening

Treat the failure a test catches as its value. Reject a test-count target. Preserve external-input corruption, partial writes, replay, races, accessibility, and supported input or platform cases by default.

## Inventory and verdicts

Run `scripts/inventory.py` with the repository's test-file globs and a case pattern before editing. Reconcile its output with the test runner's discovered files and cases. Inspect aliases, generated cases, and false positives. Keep the script output as an audit artifact, then assign every case a verdict: keep, merge, move, replace, or remove. Record its failure mode, execution boundary, supported requirement, surviving guard, and the reason for the verdict.

Before assigning a low-value verdict, recover the test's documented failure or commit rationale from comments, issue links, history, or the original change. An unfamiliar edge case is not evidence of redundancy. Search for path-keyed allowances, inventories, and ledgers before moving or deleting a test file. Update every matching entry with the move.

## Proof and timing

For a duplicate, show that the surviving guard exercises the same failure at the same boundary. Reintroduce the former failure and confirm that guard fails. If it stays green, restore the removed test or mark replacement required. A moved test must be discovered and executed by a named destination command before the old command drops it. Deliberately break one protected behavior in that destination and confirm the command fails. Restore the old gate if discovery or the failure proof fails.

Capture a quiet baseline before edits and repeat it after edits. Keep commands, worker counts, browser identity, checkout shape, dependencies, and machine load matched. Report shell wall time, queue delay, aggregate file work, file spans, tests, passes, and skips as separate measurements. Use several samples and medians. Name every confound and make no runtime claim from unmatched samples. Count cases added, moved, merged, and removed for maintenance accounting, never as the objective.

Keep changes that improve maintainability even when matched timing does not improve. Remove the runtime claim in that case. Roll back any deletion that removes the only guard for a supported failure boundary.
