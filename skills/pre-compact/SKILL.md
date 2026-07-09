---
name: pre-compact
description: "Persist conversation-only findings to durable storage before /compact, so a lossy summary can't lose them. Use when: pre compact, get ready to compact, prep for compaction, about to compact, save state before compacting."
---

# Pre-Compact

Get durable findings out of the conversation and into files or GitHub before `/compact` runs. The auto-summary keeps the narrative and decisions but drops specifics: exact run IDs, verified values, which decisions are still open, drafted-but-unposted text. Anything that lives only in chat is recoverable after compaction only from the raw `.jsonl` transcript, which is painful. This skill prevents that.

This is a checklist, not ceremony. Do the steps that apply, skip the ones that don't, and keep it short.

## Hard rules

- Read-only toward the outside world. Do NOT post to GitHub, send messages, or run any mutating command as part of prep. Persisting is to local files (and reporting what already exists on GitHub), never new posts. This respects the user's standing "never post unless explicitly told" rule.
- Persist to the RIGHT place: a checked-in project doc for findings, NOT ephemeral memory and NOT the scratchpad (scratchpad is wiped; memory is for cross-session user/project facts, not this session's investigation results). If a project doc already tracks this work, append to it rather than starting a new file.
- Do not summarize away specifics. Keep exact numbers, IDs, paths, commit SHAs, and verified command output. The whole point is that the summary will already do the lossy version.
- Do not start new investigation. Prep captures what is known; it does not go find more. If the user deferred something ("investigate further after compaction"), record it as an open thread and stop.

## Procedure

### 1. Identify conversation-only state

Scan the session for anything that exists ONLY in the chat, not yet on disk or GitHub:

- Findings and conclusions from investigation (root causes, verified chains, quantifications).
- Decisions made and the reasoning behind them.
- Open threads: unfinished decisions, deferred investigations, things waiting on the user.
- Drafted-but-unposted artifacts (comments, issue bodies, PR text sitting in scratchpad).
- Exact identifiers: PR/issue numbers, workflow run IDs, commit SHAs, file:line references, S3 keys, timestamps, test results.

### 2. Persist it

- Findings and decisions -> append to the checked-in project doc that tracks this work (e.g. a `docs/PLAN-*.md` or `docs/*.md`). Date the section. Use the plan-doc convention if the repo has one.
- Drafted artifacts -> confirm they are saved as files (scratchpad is fine for drafts, but note the path in the durable doc so it survives the summary, since the summary won't preserve scratchpad contents reliably; if a draft must not be lost, copy it into the durable doc).
- Do NOT create a memory file for session-specific investigation results. Memory is for durable user/project facts. (If a genuinely cross-session fact surfaced, that is a separate `/remember`-style action, not part of compaction prep.)

### 3. Write a resume block

At the end of the durable doc, add a short "State for resuming" subsection so the next context can pick up without re-searching:

- Every live artifact with its identifier: PR #N at commit SHA, issue #N and its status, file paths, drafted-comment paths.
- The open decisions and deferred work, each in one line.
- Anything the user explicitly asked to do next or defer.

### 4. Report and stop

Tell the user, in a few lines: what you persisted and where, the live artifacts and their state, and the open threads. Then say prep is done and they can compact. Do not begin any deferred work.

## Relationship to other skills

`reflect` proposes durable documentation/CLAUDE.md updates from lessons learned; `pre-compact` preserves this session's in-flight state so it survives summarization. Use `reflect` for "what should we write down for next time," `pre-compact` for "don't lose what we found before the window resets."
