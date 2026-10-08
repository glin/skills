---
name: rewrite
description: "Rewrite or re-explain confusing, verbose, jargon-heavy text (usually LLM-written) into concise human-readable form, and cut a reference doc down to what its reader acts on. Use when: make this more concise, rewrite this, reexplain, what does this mean, word soup, de-jargon, too technical, too verbose, make it readable, summarize for slack/github, this README is too long, trim these docs, restructure this doc, what in here is worth keeping. For stripping LLM markers out of shipped text use deslop."
argument-hint: "[text, file, GitHub URL, or 'your last response'; optionally the destination: slack, pr, issue, docs]"
---

# Rewrite

Turn word soup into text a human reads once and understands. Pick the mode first; everything else follows from who the reader is.

## Mode detection

- **Explain mode**: the author is the reader, trying to understand what some text actually says (an RFD, PR description, review comment, agent response). Output an explanation in chat.
- **Rewrite mode**: the result will be posted somewhere (PR body, issue, GitHub or Slack comment, docs, NEWS, commit message). Output raw paste-ready markdown.

Cues: "explain", "what does this mean", "what is this saying" mean explain mode. "rewrite", "make this more concise", "raw markdown", or naming a destination mean rewrite mode. If the destination is unknown and it changes the format, ask one question, then proceed.

## Getting the source

Pasted text, a file path, "your last response" (pull it from the conversation), or a GitHub URL (fetch with `gh`, read-only). Never post, comment, or edit anything anywhere; this skill only outputs text.

## Explain mode

- First sentence: what the text actually says or does, in plain words.
- Then only the details that change what the reader would do next. Drop the rest.
- No loyalty to the original's structure, order, or vocabulary.
- If a passage says nothing, say so ("this paragraph restates the title"). Word soup often hides emptiness; exposing that is part of the job.
- If a claim is ambiguous, flag the ambiguity instead of picking one reading and smoothing it over.

## Rewrite mode

Output the rewritten text in a fenced `markdown` block, then one line on what was cut. Match format to destination: bullets for Slack and GitHub comments, short prose for NEWS and docs, the repo's template scaffolding for PR bodies.

Rules, distilled from the author's own edits of AI-drafted text:

- Lead with the point. For support fixes, name the affected setup and observed failure before the implementation. Preserve accurate customer terminology and exact identifiers. Use first person only when the source or repository convention calls for it.
- Cut what carries no information: rationale essays, empty or N/A template sections, bold feature-breakdown walls, hedging asides ("Happy to link an issue if preferred"), references to untracked planning docs, proposed-fix sections in issues (issues stay problem-focused).
- Ask of every claim: does the reader already have this? A PR reader has the diff, the commit list and the CI run, so a commit count, a file count or a test count is those restated. Accurate is not the same as worth saying.
- Sections are two to four bullets, one fact each. Reach for a paragraph only when a causal chain needs it. A wall of accurate prose fails the same way a bullet wall does.
- Keep load-bearing detail: repro steps, file:line references, real test results, links. The goal is not always shorter: fluff dies, substance stays or grows.
- Concrete over abstract: real URLs, real numbers, real observed values instead of descriptions of them. A runnable command stays in a code block, never paraphrased into prose.
- Prefer the plain everyday word over a coined or evocative one that means the same thing, even where the code uses the coined one.
- Never fabricate. No invented claims, test runs, or confidence the source did not have. Preserve honest uncertainty ("still thinking about what to do").

## Reference docs

A README, module doc, or runbook needs a further pass: most of what bloats one is accurate and well written, so the rules above leave it alone.

Before restructuring a substantial guide, identify each section's job: teach by doing, give steps, support lookup, or explain decisions. Separate those jobs only when mixing them makes the guide harder to use. This check does not change the format of code comments, issues, PR descriptions, or short messages.

Work section by section. The answers need facts about the code, so go find them rather than guessing.

- **Who reads this, and what do they do differently after reading it?** "Nothing" means cut it, however true and well written it is. A fact that only demonstrates completeness is bloat. Ask it of each claim, not just each section.
- **Does this belong in this file?** Keep, cut, and move are three options, not two. An implementation invariant belongs in the docstring of the function that maintains it, where someone about to break it is already reading. Leave a one-line summary and a pointer, not a duplicate.
- **Is the thing being documented durable?** Documenting something scheduled to be removed is waste. If half of it is durable, say which half.

Then, on the whole document:

- **No catch-all buckets.** A "Notes" or "Misc" section is where undecided editorial calls hide. Every item earns a place in the flow, gets a heading of its own, or goes.
- **Order by what the reader needs first**, not by how the work happened, and follow the house pattern for that kind of doc if the project has one.
- **Give real gotchas their own heading.** A limitation buried in a bullet list reads as trivia. Under "Limitations" it reads as the warning it is.

## Banned vocabulary

Tier 1, always replace: leverage, utilize, robust, comprehensive, seamless, delve, pivotal, holistic, streamline, harness, "it's important to note", "in summary", and the "it's not X, it's Y" construction.

Tier 2, rewrite when two or more pile up in one paragraph: ecosystem, surface (as a verb), orchestrate, perturb, asymmetric, blast radius, nontrivial.

A technical term or abbreviation that must stay gets a plain-words definition in place, once. Never leave a coined shorthand or initialism the reader can't look up.

## Formatting

- Proper markdown. Short sentences, one idea each.
- No bold-lead-in bullet walls where prose belongs, no emoji headers, no emdashes.
- No hard-wrapped prose: one physical line per paragraph in anything destined for GitHub or Slack.
- Code comments and docs: lead with the decision, then split distinct ideas into short paragraphs with a blank comment line between them. Concise means fewer wasted words, not fewest lines; don't pack a multi-idea explanation into one dense block.

## Self-audit before returning

Re-scan the output for: any Tier 1 word, any undefined abbreviation or coined shorthand, any sentence that needs a second read, any fact added or lost relative to the source. Fix, then return.
