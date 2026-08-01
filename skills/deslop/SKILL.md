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
7. **Academic or mathematical register where a plain word works.** "tautological", "by construction", "orthogonal" (meaning independent), "monotonic", "canonical", "vacuously", "degenerate", "isomorphic", "a priori", "modulo", "semantics" (meaning behavior). This is proof vocabulary applied to code that needs none, and it reads as a model reaching for rigor instead of explaining. Write "a condition that always passes", "independent", "keeps rising". Keep the term only where it is genuinely the domain's own word: "monotonic" inside a real curve derivation, "idempotent" for a re-runnable step. A word that names an API's own concept is never slop, however technical it reads - simplifying it away makes the comment un-searchable against the thing it documents. Repetition is its own tell - a rhetorical phrase like "that is the whole point" passes once and indicts the file at five occurrences, so count before keeping.
8. **ALL-CAPS shouting for emphasis.** `THIS MUST RUN FIRST`, `NEVER do this`, `>> THE ONE LAW <<` in a comment or doc. Write normal sentence case and let the sentence carry the weight. Acronyms (API, HTTP) and code identifiers keep their own case; this is about words capitalised only to shout.
9. **Comments that are wrong about the code.** An invented name for something the code already names, a doc block left on the wrong member after an insertion, a claim the lines below contradict. No vocabulary grep finds these; read each comment against the member it sits on.

## Sweep procedure (when invoked on a repo or path)

0. If the target language has its own doc-comment system, or the repo carries a deslop annex for its toolchain, read that first: the structural defects (invalid tags, misbound doc blocks, generated names a comment gets wrong) are language-specific and invisible to the grep below.
1. Grep for the marker vocabulary above plus date patterns (`20\d\d-\d\d-\d\d`) in code comments and user-facing docs. Build the first hit list.
2. Then read the files, before editing anything. Grep matches vocabulary and nothing else, so rules 4, 7 and 9 never reach the hit list on their own: a comment that contradicts the lines below it, a doc block stranded on the wrong member, an invented name for something the code already names, narration aimed at a reviewer, a phrase worn into a tic. None of those need a marker word, and in a file swept before they are most of what is left. Read every comment against the member it sits on, count any phrase that recurs, and add what you find. A sweep that skips this step has checked the vocabulary and not the writing.
3. Classify each hit: exempt location (leave), genuine contract (rewrite timelessly), process narration (delete or relocate).
4. Rewrite, do not just delete: a slop comment often marks a real constraint; keep the constraint, drop the story. If deleting would lose real history, move it to the repo's dev log or decision doc, and leave no link behind in the code.
5. Report the sweep as a table: file, before-gist, action taken, and which pass found it. A table whose rows all came from grep is a report that step 2 did not run.

Related: the `rewrite` skill transforms prose for a human to read or post (re-explaining word soup, rewriting drafts); deslop removes and prevents the markers in the artifacts themselves.

## Prevention (standing behavior)

Before writing any comment or doc line, ask: would this sentence make sense to a maintainer in two years who never saw this session? If it references the session (a review, a date, a fix event, a verification step), rewrite it as the timeless contract or leave it out.
