# Repository audit rubric

Use this rubric to direct inspection. Report only findings supported by repository evidence.

## Contents

- Priority
- Audit lanes: verification integrity, development tooling, state, input, and recovery, architecture and ownership, duplication and drift, parallel agent work, test suite, instructions and documentation, team discoverability
- Evidence format
- Planning checks
- Promotion pass

## Priority

| Priority | Meaning |
| --- | --- |
| P0 | Verification is unreliable, data or changes can be lost, or ordinary use can violate a core product requirement. Address before structural work. |
| P1 | A credible correctness, recovery, performance, or drift failure exists under supported use. |
| P2 | Maintainability or parallel-work cost is material, but behavior is currently correct. |
| P3 | Optional cleanup with a named benefit and no urgency. |

Priority follows the failure mode, not file size or implementation effort.

## Audit lanes

### Verification integrity

Check:

- Does the documented root command work on supported platforms?
- Do skipped tests make a green run ambiguous?
- Do tests scan unrelated untracked files, ignored worktrees, caches, or generated output?
- Does the suite run the built artifact where build substitution or host behavior matters?
- Can an architectural guard miss nested files, generated entry points, or an import through a facade?
- Does a guard fail when its forbidden case is introduced?
- Are fast, focused, and full verification lanes explicit about omitted coverage?

Report environment and harness failures separately from product failures. A check that visits no relevant files or silently skips its only meaningful assertion is P0.

### Development tooling

Check:

- Whether one declared formatter owns each source type and produces a stable second pass.
- Whether lint rules cover supported JavaScript and TypeScript environments without hiding errors behind broad exclusions.
- Whether type checking remains explicit when a linter or build tool only transpiles source.
- Whether test discovery, skips, timeouts, and failure propagation match local scripts and CI.
- Whether fast, full, browser, smoke, build, and package lanes name the behavior each one omits.
- Whether tool versions support the repository's Node, TypeScript, module, browser, and shipped-runtime constraints.
- Whether generated output, dependencies, private artifacts, and linked worktrees stay outside formatting, linting, and test discovery.
- Whether custom wrappers enforce a repository-specific contract or merely reproduce a maintained tool's native command.
- Whether local development and CI invoke the same configuration and lockfile-resolved binaries.

Do not recommend a tool because it is newer, faster in another repository, or more popular. Require a measured gap such as missing checks, duplicated custom machinery, inconsistent discovery, or excessive runtime. A migration must preserve assertion execution, type checks, browser coverage, packaged behavior, and supported runtimes unless the owner changes that contract.

### State, input, and recovery

Check:

- Runtime validation at every persisted or external data boundary.
- Atomicity and recovery for related file or database writes.
- Concurrent read-modify-write updates and lost-update risk.
- Stable identity and normalization for paths, IDs, models, and request keys.
- Deletion, truncation, partial-write, interruption, and restart semantics.
- Positive bounds on queues, caches, maps, transcript reads, diff work, and retries.
- Synchronous work on editor, UI, or request-handling threads.
- Reset or cleanup commands against a complete registry of owned state.

Distinguish a product defect from a deliberate contract limit. Surface a contract limit to the owner before recommending behavior beyond it.

### Architecture and ownership

Check:

- Whether conceptual boundaries exist physically in imports and folders.
- Whether dependency direction can be stated and enforced.
- Whether host or framework glue contains decisions that could live in tested modules.
- Whether shared domain types are owned by a vendor-specific, UI-specific, or otherwise misleading module.
- Whether compatibility facades still serve production callers and hide cycles or ownership.
- Whether a central controller combines lifecycle, persistence, parsing, rendering, and navigation.
- Whether adding another source or agent fails exhaustively or silently falls into an existing branch.

Recommend a folder only when it creates ownership, dependency, runtime, or conflict isolation. Prefer staged extraction over a directory-wide move.

### Duplication and drift

Look for independently maintained copies of:

- domain schemas and arithmetic;
- message names and payload shapes;
- command IDs, persisted-state keys, versions, and configuration names;
- pricing, protocol, model, or provider identities;
- browser and host decision logic;
- user-visible wording and accessible names;
- platform path handling and executable discovery;
- request lifecycle, timeout, and cleanup behavior.

Classify each pair:

- Same contract: centralize or generate one side.
- Similar mechanics with different policy: share a narrow primitive.
- Source-specific meaning: retain separate adapters and test a common output contract where appropriate.

Do not centralize incidental literals, CSS classes, or vendor protocol values merely because a search finds several copies.

### Parallel agent work

Check:

- Files that mix unrelated ownership and attract most feature edits.
- Repository-wide tables or documents every branch must edit.
- Nonrecursive guards that make subfolders unsafe.
- Global mutable fixtures, shared build output, test ports, caches, and process resources.
- Worktree setup that shares writable dependencies or follows directory links during removal.
- Tests and fixtures concentrated in files that several independent workstreams must change.
- Whether a plan can assign file ownership without overlapping central registries.

Prefer stable package boundaries, recursive guards, derived registries, and isolated worktrees. Nested instruction files are justified only after a folder has unique operational rules.

### Test suite

Measure wall time by lane before proposing consolidation. Check:

- repeated browser, process, server, database, or compiler startup;
- fresh state versus unnecessarily fresh infrastructure;
- fixed sleeps that could wait on an observable condition;
- source-text extraction and regex assertions that can match dead code;
- fixtures that leak temporary directories, processes, listeners, or environment state;
- module-level counters or mutable globals that make order significant;
- duplicated fixture builders whose cleanup or encoding can drift;
- large files that create merge conflicts even when execution is fast;
- tests that cover the same words but different runtime boundaries.

Safe optimizations often include sharing expensive infrastructure while creating fresh per-test contexts, caching immutable fixtures, removing repeated full-tree scans, and adding a fast lane that excludes only named integration coverage. Keep the default full gate unless the owner chooses another contract.

Do not delete tests to reduce a count. Deletion requires evidence that the surviving test reaches the same public behavior, failure mode, and runtime boundary.

### Instructions and documentation

Check:

- Whether always-loaded instructions contain incident narratives or extensive examples.
- Whether prose repeats an executable guard.
- Whether referenced files, symbols, commands, and paths still exist.
- Whether root and nested instructions duplicate or contradict one another.
- Whether current-state architecture docs describe only current responsibilities and non-obvious constraints.
- Whether public setup and support claims match current code and CI.

Keep root instructions focused on routing, safety, and workflow facts every session needs. Put subsystem behavior in subject docs. Add a nested instruction file only for unique path-specific constraints, and point to canonical docs rather than copying them.

### Team discoverability

This lane tests whether the docs answer real tasks. The Instructions and documentation lane tests whether they are current and free of duplicates. When delegation is authorized, give a fresh reviewer with no history of the work a list of real tasks. Otherwise answer from the repository alone, and mark the answers as informed by the author. The reviewer answers each task from the repository alone: yes, partly, or no. It cites the file and line, and names what is missing. It must not fill a gap from general knowledge.

Build the task list from the repository's purpose. A typical list:

- Say what the repository is for, and who uses it.
- Set it up, or adopt it in a new consumer, end to end.
- Decide whether a new piece belongs here or in another repository.
- Make the most common change, such as a model, dependency, or version bump, and find every place it touches.
- Test a change before it reaches users or consumers.
- Debug the most common failure.
- Find who to ask about the repository.
- Have an AI agent do each task: check for an agent instructions file, and whether an agent in a consuming repository finds its way here.

Then list the facts a teammate needs that live outside the repository. Examples are one person's notes, a chat, a plan, or settings. Give each gap a priority from P0 to P3 by its failure mode. A fix that adds ownership, settings, or process is a proposal for the owner, not a defect to correct.

## Evidence format

Use this shape for every finding:

```text
Priority and title
Evidence: file and symbol or line
Failure: concrete supported scenario
Treatment: smallest durable change
Proof: check that fails before and passes after
```

For broad recommendations, name the design goal or product requirement served. State when a recommendation improves conflict isolation rather than runtime or correctness.

## Planning checks

Before finalizing the plan, confirm:

- Foundational guards land before directory changes.
- Correctness fixes precede behavior-preserving moves of the affected code.
- Shared domain contracts precede splitting their consumers.
- Browser or process protocol typing precedes large handler splits.
- Existing approved plans are incorporated rather than duplicated.
- Each phase has an independently useful outcome and a full verification gate.
- Architecture documentation changes in the same commit only when its defined scope changes.
- Owner decisions are explicit rather than hidden inside implementation assumptions.

## Promotion pass

For each recurring lesson, select one destination:

| Lesson type | Destination |
| --- | --- |
| Value can be derived | One implementation or generated artifact |
| Finite variant set | Type and exhaustive dispatch |
| Forbidden dependency or drift | Test, lint rule, or architecture guard |
| Invalid external state | Runtime validation and local diagnostic |
| Workflow all developers need | Shared documentation |
| Repository-only operational constraint | Root or scoped instruction file |
| Procedure useful across repositories | Skill |
| Temporary sequence or open decision | Plan |

After promotion, remove weaker duplicates. The improvement loop is complete only when the stronger mechanism is verified and the redundant prose is gone.
