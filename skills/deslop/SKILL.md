---
name: deslop
description: Remove and prevent LLM-marker writing (agent jargon, dated provenance comments, review-narration) in code, READMEs, and user-facing docs. Invoke to sweep a repo or file set; the prevention rules apply to all writing, always.
---

# De-slop: no LLM markers in code or user-facing text

LLM-assisted work leaves recognizable markers. They are noise to every human reader and they date the codebase.

## Where the rules apply

- ALWAYS: source code comments, READMEs, user-facing documentation, UI strings, error messages, public API docs.
- EXEMPT: internal dev ledgers and audit-style docs (dependency/license tables, decision logs, roadmaps, plan files, CHANGELOGs) - dates and provenance are the point there. Agent instruction files (CLAUDE.md and similar) are internal.

## What counts as slop

1. **Agent-process jargon in code or docs.** Words that describe the agent's verification process, not the software: "kill test", "oracle", "probe", "load-bearing", "belt and suspenders", "greppable", "scouted", "falsified", "smoke-tested this by...". State what the code does and the constraint it satisfies.
2. **Dated provenance comments.** `// fixed 2026-07-06`, `(review 2026-07-06)`, `(user feedback yesterday)`, "since the X fix landed". Git blame carries provenance; a comment states the current contract, timelessly.
3. **References to superseded states.** Comments that explain the code by contrast with what it used to be or with a decision process: "no longer uses X because...", "we decided against Y", "passed its test so now Z". Describe what IS, and only the parts of why that a maintainer needs.
4. **Reviewer-directed narration.** Comments that talk to the person merging the change ("this is correct because", "note that this now handles"), TODO ceremony that restates the diff, apology or hedging language.
5. **LLM prose tells in user-facing text.** "Let's", "simply", "robust", "seamless(ly)", "comprehensive", "leverage", "delve", "It's important to note", "In summary", bullet-list-with-bold-lead-in walls where prose belongs, emoji headers, "## Overview" boilerplate sections that say nothing.
6. **Developer vocabulary in user-facing messages.** A console warning, error, or dialog that narrates internals (a record, a merge step, a hook or service name) instead of saying what happened and what the user will notice, in the product's vocabulary.

## Sweep procedure (when invoked on a repo or path)

1. Grep for the marker vocabulary above plus date patterns (`20\d\d-\d\d-\d\d`) in code comments and user-facing docs. Build the hit list before editing anything.
2. Classify each hit: exempt location (leave), genuine contract (rewrite timelessly), process narration (delete or relocate).
3. Rewrite, do not just delete: a slop comment often marks a real constraint; keep the constraint, drop the story. If deleting would lose real history, move it to the repo's dev log or decision doc, and leave no link behind in the code.
4. Report the sweep as a table: file, before-gist, action taken.

Related: the `rewrite` skill transforms prose for a human to read or post (re-explaining word soup, rewriting drafts); deslop removes and prevents the markers in the artifacts themselves.

## Prevention (standing behavior)

Before writing any comment or doc line, ask: would this sentence make sense to a maintainer in two years who never saw this session? If it references the session (a review, a date, a fix event, a verification step), rewrite it as the timeless contract or leave it out.
