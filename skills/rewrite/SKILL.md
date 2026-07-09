---
name: rewrite
description: "Rewrite or re-explain confusing, verbose, jargon-heavy text (usually LLM-written) into concise human-readable form. Use when: make this more concise, rewrite this, reexplain, what does this mean, word soup, de-jargon, too technical, too verbose, summarize for slack/github, make it readable."
argument-hint: "[text, file, GitHub URL, or 'your last response'; optionally the destination: slack, pr, issue, docs]"
---

# Rewrite

Turn word soup into text a human reads once and understands. Pick the mode first; everything else follows from who the reader is.

## Mode detection

- **Explain mode**: the user is the reader, trying to understand what some text actually says (an RFD, PR description, review comment, agent response). Output an explanation in chat.
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

Rules, distilled from the user's own edits of AI-drafted text:

- Lead with the point. PR and issue bodies open with a casual first-person lede ("Just putting this up because...", "Found while working on...") plus cross-links to related PRs, issues, or incidents ("Supersedes #X because...").
- Cut what carries no information: rationale essays, empty or N/A template sections, bold feature-breakdown walls, hedging asides ("Happy to link an issue if preferred"), references to untracked planning docs, proposed-fix sections in issues (issues stay problem-focused).
- Keep load-bearing detail: repro steps, file:line references, real test results, links. The goal is not always shorter: fluff dies, substance stays or grows.
- Concrete over abstract: real URLs, real numbers, real observed values instead of descriptions of them.
- Never fabricate. No invented claims, test runs, or confidence the source did not have. Preserve honest uncertainty ("still thinking about what to do").

## Banned vocabulary

Tier 1, always replace: leverage, utilize, robust, comprehensive, seamless, delve, pivotal, holistic, streamline, harness, "it's important to note", "in summary", and the "it's not X, it's Y" construction.

Tier 2, rewrite when two or more pile up in one paragraph: ecosystem, surface (as a verb), orchestrate, perturb, asymmetric, blast radius, nontrivial.

A technical term that must stay gets a plain-words definition in place, once.

## Formatting

- Proper markdown. Short sentences, one idea each.
- No bold-lead-in bullet walls where prose belongs, no emoji headers, no emdashes.
- No hard-wrapped prose: one physical line per paragraph in anything destined for GitHub or Slack.
- Code comments and docs: lead with the decision, then split distinct ideas into short paragraphs with a blank comment line between them. Concise means fewer wasted words, not fewest lines; don't pack a multi-idea explanation into one dense block.

## Self-audit before returning

Re-scan the output for: any Tier 1 word, any sentence that needs a second read, any fact added or lost relative to the source. Fix, then return.
