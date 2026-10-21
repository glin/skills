---
name: pre-compact
description: "Save conversation-only findings in durable local files before compaction. Use when asked to prepare for compaction, save state before compacting, or compact yourself. Preparation alone does not request compaction."
---

# Pre-Compact

Preserve conversation-only state in durable local files before `/compact`. A summary does not guarantee exact identifiers, results, open decisions, or draft text. Save those details so the next context resumes from files.

This is a checklist, not ceremony. Do the steps that apply, skip the ones that don't, and keep it short.

## Hard rules

- Write owned local state files and commit owned changes under the project's conventions and standing authority. Preserve unrelated working tree and index changes.
- Do not create external posts, send messages, or push without explicit authorization. Compaction prep grants no new external authority.
- Do not write memory or rely on scratchpad contents during compaction prep.
- Do not start new investigation. Record deferred work as an open thread.

## Procedure

### 1. Identify conversation-only state

Scan the session for anything that exists only in the chat, not yet on disk or GitHub:

- Findings and conclusions from investigation (root causes, verified chains, quantifications).
- Decisions made and the reasoning behind them.
- Open threads: unfinished decisions, deferred investigations, things waiting on the author.
- Drafted-but-unposted artifacts (comments, issue bodies, PR text sitting in scratchpad).
- Exact identifiers: PR/issue numbers, workflow run IDs, commit SHAs, file:line references, S3 keys, timestamps, test results.

### 2. Persist it

- Use the existing project document or plan that owns the work. Follow `plan-files` for plan state and its repository conventions. Otherwise, use an existing project document or a durable local file when no convention applies.
- Preserve exact numbers, IDs, paths, commit SHAs, decisions, and verified command output. Keep test results and their limitations together.
- Save each exact draft in an owned durable file before summarizing it. Link that file from the document that tracks the work.

### 3. Write a resume block

At the end of the durable doc, add a short "State for resuming" subsection so the next context can pick up without re-searching:

- Every live artifact with its identifier: PR #N at commit SHA, issue #N and its status, file paths, drafted-comment paths.
- The open decisions and deferred work, each in one line.
- Anything the author explicitly asked to do next or defer.

### 4. Report or request compaction

Report the durable file paths, live artifact state, and open threads in a few lines. Say prep is complete only after saving the state.

- For preparation-only requests, report and stop. Invoking `pre-compact` alone does not authorize compaction.
- For an explicit request to compact this session, save state first. Then request compaction through the runtime's supported mechanism.
- Follow the available compaction instructions, including their admission, retry, and completion rules. Do not invent a command or transport.
- If compaction is unavailable or refused, report that outcome and the saved handoff. Give the author the manual `/compact` command.
- Distinguish requested or queued compaction from completed compaction. Claim completion only when the runtime confirms it.

## Relationship to other skills

The `reflect` skill proposes durable documentation updates from lessons learned. The `pre-compact` skill preserves this session's unfinished work before summarization.
