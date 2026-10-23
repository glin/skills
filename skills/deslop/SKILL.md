---
name: deslop
description: Remove and prevent LLM-marker writing (agent jargon, dated provenance comments, review-narration) in code, READMEs, and user-facing docs. Invoke on a repo or a file set to sweep it. The prevention rules apply to all writing, always. Use when: deslop, de-slop, sweep for AI writing, this reads like an LLM wrote it, make it sound human, strip the AI tells, clean the comments before committing, remove agent jargon. For transforming prose a human will read or post, use rewrite instead.
---

# De-slop: no LLM markers in code or user-facing text

LLM-assisted work leaves recognizable markers. They are noise to every human reader and they date the codebase.

## Where the rules apply

- Always: source code comments, READMEs, user-facing documentation, UI strings, error messages, public API docs, and names written into generated artifacts.
- Exempt: internal dev ledgers and audit-style docs (dependency/license tables, decision logs, roadmaps, plan files, CHANGELOGs) - dates and provenance are the point there. Agent instruction files (CLAUDE.md and similar) are internal.

## What counts as slop

1. **Agent-process jargon in code or docs.** Words that describe the agent's verification process, not the software: "kill test", "oracle", "probe", "load-bearing", "belt and suspenders", "greppable", "scouted", "falsified", "smoke-tested this by...". State what the code does and the constraint it satisfies.
2. **Dated provenance comments.** `// fixed 2026-07-06`, `(review 2026-07-06)`, `(user feedback yesterday)`, "since the X fix landed". Git blame carries provenance; a comment states the current contract, timelessly.
3. **References to superseded states.** Comments that explain the code by contrast with an earlier version or with a decision process: "no longer uses X because...", "we decided against Y", "passed its test so now Z". Describe the present behaviour, and only the parts of why a maintainer needs.
4. **Reviewer-directed narration.** Comments that talk to the person merging the change ("this is correct because", "note that this now handles"), TODO ceremony that restates the diff, apology or hedging language.
5. **LLM prose tells in user-facing text.** "Let's", "simply", "robust", "seamless(ly)", "comprehensive", "leverage", "delve", "It's important to note", "In summary", bullet-list-with-bold-lead-in walls where prose belongs, emoji headers, "## Overview" boilerplate sections that say nothing.
6. **Developer vocabulary in user-facing messages.** A console warning, error, or dialog that narrates internals (a record, a merge step, a hook or service name) instead of saying what happened and what the user will notice, in the product's vocabulary.
7. **Academic or mathematical register where a plain word works.** "tautological", "by construction", "orthogonal" (meaning independent), "monotonic", "canonical", "vacuously", "degenerate", "isomorphic", "a priori", "modulo", "semantics" (meaning behavior). This is proof vocabulary applied to code that needs none, and it reads as a model reaching for rigor instead of explaining. Write "a condition that always passes", "independent", "keeps rising". Keep the term only where it is genuinely the domain's own word: "monotonic" inside a real curve derivation, "idempotent" for a re-runnable step. A word that names an API's own concept is never slop, however technical it reads - simplifying it away makes the comment un-searchable against the thing it documents. Repetition is its own tell - a rhetorical phrase like "that is the whole point" passes once and indicts the file at five occurrences, so count before keeping.
8. **Moral or anthropomorphic register applied to code.** Praising or accusing the software instead of describing it: an "honest" control, a design that "lies about" the mechanism, a check that "keeps X honest", code that "wants to" or "deserves" or "happily" does something, an optimizer "entitled to" strip a clip, a fallback that "politely" or "gracefully" gives up. Software has no motives and owes nothing, so the word is always standing in for a concrete claim the sentence has not made, and the reader cannot recover the claim from the praise. Say the claim instead: not "one radio group is the honest control" but "no two of the shapes combine, so they are one radio group"; not "two controls keep the comparison honest" but "two controls show the comparison can still fail"; not "the honest readout is nothing" but "the readout is zero". **"Honest" is the most common of these**, reads as insight, and survives every other pass because no rule above names it - grep it by itself. Keep such a word only where it describes a person: a comment about what an author or a user intended may say they were honest about a limitation.
9. **All-caps shouting for emphasis.** `THIS MUST RUN FIRST`, `>> THE ONE LAW <<`, and the commoner single word: `the SAME source`, `the CURRENT value`, `A AND B`. Write normal sentence case and let the sentence carry the weight. A pointing word or a connective never earns a capital. A negation takes a word that carries it, such as `never` or `no`. Four words are exempt, because a capital earns its place on a warning whose cost is silent: `MUST`, `NEVER`, `ALWAYS`, `REQUIRED`. State the consequence beside them and the capital works harder still. Acronyms (API, HTTP), code identifiers, and anything quoted verbatim from a parameter value, a menu label or a log token keep their own case; a project adds its own to `allowCaps`.
10. **Comments that are wrong about the code.** An invented name for something the code already names (a default value described as "the production shape", a flag's false branch called "the normal posture"), a doc block left on the wrong member after an insertion, a claim the lines below contradict. No vocabulary grep finds these; read each comment against the member it sits on. The invented-name form is the one the author's own read pass misses most, because the coinage still means something to the person who coined it.
11. **Generated names that only make sense from the generator's side.** A name written into a generated artifact never appears in the generator's own interface, so it is read by whoever opens that artifact and has to stand on its own. Two ways it fails: naming the side effect that produced it, which only a reader who already knows that mechanism can decode, and reusing a word the artifact's domain already means something else by, which sends the reader looking for a feature that does not exist.

## The machine-checkable half

The rules above that are made of fixed vocabulary, as regular expressions. `scan.mjs` reads this block, so editing a rule here is editing what the scan looks for and the two cannot drift apart. Nothing else carries this vocabulary; do not copy it into a repo's own script.

`unmechanizable` is the important field. A scan that stays quiet about what it could not check gets read as "this file is swept" when it only means "the vocabulary is clean", and the rules listed there are where the real defects keep turning up.

```json deslop-patterns
{
  "rules": {
    "agent-jargon":   "\\b(kill test|oracle|load-bearing|belt and suspenders|greppable|scouted|smoke[- ]test\\w*|falsified)\\b",
    "dated":          "(fixed 20\\d\\d|as of 20\\d\\d|since the [^.]{0,30}fix landed)",
    "dated-stamp":    "20\\d\\d-\\d\\d-\\d\\d",
    "superseded":     "\\b(previously|used to (be|do|have|write|read)|formerly|we decided against|no longer uses|has been (renamed|replaced|moved))\\b",
    "reviewer-narr":  "\\b(note that|this is correct because|as mentioned|as noted above|worth noting|to be clear)\\b",
    "prose-tell":     "\\b(let's|simply|robust|seamless\\w*|comprehensive|leverage[ds]?|delve|it's important to note|in summary)\\b",
    "academic":       "\\b(tautolog\\w*|by construction|orthogonal|vacuous\\w*|isomorphic|a priori|modulo)\\b",
    "academic-domain":"\\b(monotonic\\w*|canonical\\w*|degenerate|semantics)\\b",
    "agent-jargon-domain": "\\b(probe[ds]?|probing)\\b",
    "moral-anthro":   "\\b(honest\\w*|lies about|deserves?|wants to|happily|entitled to|gracefully|politely|naive\\w*|knows better)\\b"
  },
  "partial": ["reviewer-narr", "academic-domain", "agent-jargon-domain"],
  "codeOnly": ["dated-stamp"],
  "unmechanizable": [
    "rule 10: a comment that contradicts the code below it",
    "rule 10: a doc block left on the wrong member after an insertion",
    "rule 10: an invented name for something the code already names",
    "rule 5: a \"## Overview\" section that says nothing",
    "rule 3: a comment explaining the code by what it used to pass",
    "rule 7: a rhetorical phrase worn into a tic, which needs a count and not a match",
    "rule 6: developer vocabulary in a message the product's user reads",
    "rule 11: a generated name that only makes sense from the generator's side"
  ]
}
```

`academic-domain` is separate because those four are the domain's own word about as often as they are padding: a curve that really is monotonic, a canonical form that really is one. Treat its hits as questions. A repo answers them once in its config's `knownFalsePositives` rather than re-deciding every sweep.

`agent-jargon-domain` holds `probe` alone, and is separate for the same reason. In a comment about how a change was checked it is agent jargon. In networking, hardware and diagnostic code it is the domain's own noun, and a repo full of `shape-probe.py` and `probe-payload-sizes.py` would drown in hits. Treat its hits as questions, and settle them once in `knownFalsePositives`.

`dated-stamp` is separate, and code-only, because a document has genres a comment does not. In a comment a bare date is provenance, which git blame already carries. In a changelog, a release note or a version history the date is the content, and a rule that flags those is wrong rather than strict. The phrase forms stay in `dated` and still fire in a document, because "fixed 2026-07-02" attaches a date to a claim wherever it is written.

## Sweep procedure (when invoked on a repo or path)

0. If the target language has its own doc-comment system, or the repo carries a deslop annex for its toolchain, read that first: the structural defects (invalid tags, misbound doc blocks, generated names a comment gets wrong) are language-specific and invisible to the grep below.
1. Run `node <this skill>/scan.mjs` from the repo root for the first hit list. It needs no setup: the file list comes from `git ls-files`, so anything the repo already gitignores is already out. A repo that keeps answering the same questions every sweep can settle them once, in a private `<git common dir>/info/deslop-scope.json`, in a `.claude/deslop.config.mjs`, or in a fenced ```json deslop-scope block in the CLAUDE.md it already keeps. `scan.mjs` finds the git common dir with `git rev-parse --git-common-dir`, so a linked worktree still finds the main clone's file. Git never tracks that path, so it holds the settings without writing them into the repository, and it is the recommended place for a repository that does not vendor this skill. It takes the same keys as the CLAUDE.md block and is the first source checked, ahead of both committed sources. The CLAUDE.md block is for a repository that does vendor this skill and already keeps a CLAUDE.md other readers will see. It takes the same keys minus `rules` and `annex`, which are code. A config file wins when both it and the CLAUDE.md block exist. Any of the three settles -- which trees ship, which capitalised words are the domain's own, which patterns have been ruled false positives, and any extra rules or toolchain annex it wants loaded. Either way this pass ends at the vocabulary.
2. Then read the files, before editing anything. Grep matches vocabulary and nothing else, so rules 4, 7 and 10 never reach the hit list on their own: a comment that contradicts the lines below it, a doc block stranded on the wrong member, an invented name for something the code already names, narration aimed at a reviewer, a phrase worn into a tic. None of those need a marker word, and in a file swept before they are most of what is left. Read every comment against the member it sits on, count any phrase that recurs, and add what you find. A sweep that skips this step has checked the vocabulary and not the writing.
3. Run `node <this skill>/prose-metrics.mjs` for the half of the writing no pattern reaches. It counts sentences past 40 words, semicolons standing in for a full stop, and sentences split in the wrong place, then lists the long doc summaries. It builds its own corpus by calling `scan.mjs --list`, so it sweeps the files step 1 swept and nothing else. When that call fails it exits with `prose-metrics needs scan.mjs to produce the corpus`, and the corpus is what to fix. Every line it prints is a place to read, not a defect to fix. A long sentence is sometimes the clearest way to say the thing, and length alone never makes a summary wrong. `--words N` moves the threshold and `--all` prints every hit instead of the worst twenty. `--since REF` narrows the corpus to prose written since REF, by the same definition step 1 uses.
4. Classify each hit: exempt location (leave), genuine contract (rewrite timelessly), process narration (delete or relocate).
5. Rewrite, do not just delete: a slop comment often marks a real constraint; keep the constraint, drop the story. If deleting would lose real history, move it to the repo's dev log or decision doc, and leave no link behind in the code.
6. Report the sweep as a table: file, before-gist, action taken, and which pass found it. A table whose rows all came from grep is a report that step 2 did not run.
7. Do not let the scanner say the sweep is finished. It reports what its patterns match, so a clean run is evidence about the patterns. Ask what each rule cannot see, and answer with something else. A rule wanting two adjacent words never sees one word. A rule holding a word list is a vocabulary, not a class, so grep the forms the list omits. A pattern tested per line never sees a phrase wrapped across two lines. A comment extractor can skip a whole shape of comment, such as one that does not start its line. Four such gaps turned up in one sweep, each invisible to a green run, and none would have been found by running it again.

Before widening a rule because another slop filter carries it, read `references/adjacent-tools.md`. It holds what the neighbouring tools cover, where they overlap, and which of their rules were rejected and why.

Related: the `rewrite` skill transforms prose for a human to read or post (re-explaining word soup, rewriting drafts); deslop removes and prevents the markers in the artifacts themselves.

## Prevention (standing behavior)

Before writing any comment or doc line, ask: would this sentence make sense to a maintainer in two years who never saw this session? If it references the session (a review, a date, a fix event, a verification step), rewrite it as the timeless contract or leave it out.
