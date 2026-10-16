---
name: recap
description: "Recap the current conversation or, when asked for a retro, summarize weekly notes since the previous retro. Use when: catch me up, recap this conversation, where does this stand, what have we done here, what would I lose if I closed this session, summarize this session, status of this work, or recap since the last retro. Covers one conversation by default."
---

# Recap

For a conversation recap, cover the current conversation only. For a retro, use the weekly notes and follow Retro mode below.

Read the conversation for the original problem, requests, and changes of direction. Check claimed results against the artifacts the work produced. For repository work, use relevant files, `git log`, and plan files. For other work, use its documents, messages, or other outputs. An intended change is not a completed result.

## Retro mode

When the author asks what happened since the last retro, read the weekly notes after the most recent retro marker before the retro being summarized. If a marker for today already exists, treat it as the end marker and start after the preceding marker. Read dated notes through today. Summarize completed outcomes as highlights and setbacks as lowlights. Keep ongoing work and unchecked todos separate from completed outcomes. Add concise summaries under `## Highlights` and `## Pain Points / Challenges` at the bottom of the current week file. Preserve existing notes and update their summaries instead of adding duplicates. When the author says the retro is today, add `- Latest retro today` under today's heading if it is missing. Start from items already in those sections, because the author and earlier agents add them during the week. A week file with both sections empty is a missed step, so fill it from its dated notes. Report the highlights and lowlights in the recap.

## The report

Open with what changed for the author and which original problem it addresses. Explain the practical result in plain language. If only internal work finished, say so. Then use the sections the answer needs, in this order. Omit empty sections.

- **Done**: completed outcomes and what they let the author do. Describe tests, fixtures, tooling, and refactors as internal work when relevant. Never turn their scenarios into claims about product behavior.
- **Open**: unfinished work, limitations, and uncertainty that affect the result. Distinguish a committed change from a confirmed result when that affects confidence.
- **Needs you**: every decision waiting on the owner, one line each, opening with the verb that answers it.
- **Next**: the useful next action, if one remains.

Include running work and owned uncommitted or unpushed artifacts when they affect the next action or risk losing work. For a closing-session question, also name findings that exist only in the conversation.

Include hashes, branches, paths, commands, and costs when explicitly requested or necessary for a decision or action. A generic status request keeps the same outcome-first default. An explicit technical handoff includes the exact details needed to continue.

## Rules

- Keep the report under one screen unless an explicit technical handoff needs more detail to continue. Preserve the original problem and changes of direction. Cut supporting detail first.
- State only effects the evidence supports. If the practical effect is unclear, say so instead of guessing.
- Include relevant results from other sessions only when they affect this conversation's work, and attribute them. Claim a commit as this session's only when this conversation holds the command that made it.
- Give a number or an identifier only when supported by evidence. Never invent one to fill a line.
- Write plainly. No process narration, and no account of how you checked.

## The same question by hand

Use these prompts for a recap or an explicit technical handoff.

- "In plain terms, what problem did we start with, what changed for me, and what remains unfinished?"
- "What would I lose if I closed this session right now?"
- "Give me a technical handoff with the exact files, commits, checks, and next commands needed to continue."

## A different conversation

Read that conversation's transcript instead of your own context, then write the same report. The `this-transcript` skill locates the file for the conversation you are in. For another one, its id names the file under the Claude Code projects directory or under the Codex sessions directory.
