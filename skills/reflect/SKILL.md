---
name: reflect
description: "Reflect on conversation history and suggest documentation updates. Use when: reflect, update docs, update claude.md, what did we learn, session retrospective."
---

# Reflect

Review the current conversation and suggest updates to project documentation based on what was learned.

## When to Use

- After a long coding session with iterations
- After discovering project conventions or gotchas
- After debugging something that took multiple attempts
- When the user asks "any changes to CLAUDE.md / README?"

## Procedure

### 1. Scan Conversation for Learnings

Review the conversation history for:
- **Mistakes that were corrected** - patterns the agent got wrong that a doc note would prevent next time
- **Conventions discovered** - coding style, file organization, naming patterns, build steps that weren't documented
- **Gotchas and pitfalls** - things that broke unexpectedly or required non-obvious fixes
- **Cross-file coupling discovered** - non-obvious dependencies between files or components (e.g., a config value that must stay in sync with a constant, a function whose callers aren't discoverable by grep)

When citing a learning, name the concrete trigger (`file:line`, command, or error) in one clause, not a retelling. Vague "we learned X" notes age badly; concrete ones don't.

### 2. Identify Target Files

Prefer inline source comments when the learning is specific to a code location (e.g., "this value must match X in file Y"). Prefer doc files (`README.md`, `CLAUDE.md`, `.github/` templates) when the learning is a general convention or workflow. Prefer shared docs (README) over AI-only docs (CLAUDE.md) when the convention applies to all developers.

Before proposing an addition, grep the target file for similar content. Do not suggest adding things that are already documented.

Do not create memory files. All learnings go into version-controlled files.

### 3. Propose Updates

For each learning, suggest a concrete edit to the appropriate file. Present as a table:

| Learning | File | Suggested Addition |
|----------|------|--------------------|
| ... | ... | ... |

Suggestions can target doc files or inline code comments. For inline comments, show the file path + line and the proposed comment text.

**Write each addition at its final length, not a first draft to trim later.** Default to 1-2 sentences matching the file's terseness; state the rule and its fix, cut narrative and multi-example evidence to one clause, and never restate what the file already has (link to the existing section instead).

### 4. Apply on Approval

Wait for the user to approve specific suggestions or all of them. Apply only what they approve.
