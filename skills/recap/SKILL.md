---
name: recap
description: "Recap the conversation you are in: what it asked for, what landed, what is open, and what needs a decision. Use when: catch me up, recap this conversation, where does this stand, what have we done here, what would I lose if I closed this session, summarize this session, status of this work. Covers one conversation, and never reports across sessions."
---

# Recap

Answer "catch me up on this conversation" in one short report. Cover this conversation only.

Read your own context for what was asked. Read `git log` and the repository's plan files for what landed. Never take the conversation's last message as the answer, because it says what was intended rather than what is on disk.

## The report

Open with one line saying where the work stands now. Then write these sections in this order, and drop any section with nothing in it.

- **Asked**: every request in the order it was made, one condensed line each. Leave out text the harness injected, such as a command tag or a hook note.
- **Done**: what landed, newest first, with the commit hash in backticks at the end of the line. Name the repository when more than one is involved.
- **Open**: work started and not finished, one line each, saying what remains.
- **Needs you**: every decision waiting on the owner, one line each, opening with the verb that answers it.
- **Running**: anything still in flight, such as a background command, a dispatched session, or a subagent.
- **Spend**: the dollars so far, and the largest single item.

## Rules

- Keep the whole report under one screen. Cut the oldest detail first, and say how many items you dropped.
- Claim a commit only when this conversation holds the command that made it. A commit another session made is not this session's work.
- Check what landed against `git log` before you write it. A commit you meant to make and a commit that exists are two different facts.
- Give a number or a hash only where you have one. Never estimate either to fill a line.
- Write plainly. No process narration, and no account of how you checked.

## The same question by hand

These three reach the same answer without the skill.

- "Status check: what landed, what is unlanded, what needs a decision from me, in that order, from git and the plan files rather than this conversation."
- "What would I lose if I closed this session right now?"
- "One line on where this stands, then only what is blocked on me."

## A different conversation

Read that conversation's transcript instead of your own context, then write the same report. The `this-transcript` skill locates the file for the conversation you are in. For another one, its id names the file under the Claude Code projects directory or under the Codex sessions directory.
