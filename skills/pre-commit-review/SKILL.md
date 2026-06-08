---
name: pre-commit-review
description: "Review code changes before committing using subagent(s). Use when: pre-commit review, review my changes, code review before commit, review agent changes, multi-model review. Saves context tokens by running reviews in isolated subagents."
argument-hint: "[models...] e.g. 'opus sonnet', or blank for the current model + Sonnet 4.6 in parallel"
---

# Pre-Commit Code Review

Run a code review of current changes in an isolated subagent process, saving context tokens in the main chat session. Supports running multiple reviewers with different models in parallel.

## When to Use

- Before committing changes (especially agent-generated changes)
- To get a second opinion on code quality without cluttering the main session
- To compare review perspectives across different models

## Procedure

### 1. Determine What to Review

Determine the list of changed files to review.

- **Uncommitted changes (default)**: Use `git diff` to get changed files.
- **Specific commit**: If the user provides a commit ref (hash, branch, `HEAD~1`, etc.), review that commit with `git diff <ref>~1 <ref>`.
- **No changes**: If the working tree is clean and no commit is specified, review the latest commit.

**Exclude generated files.** Before building the file list, check if any changed files are generated from templates or build outputs. Exclude them from the detailed review list. Mention them in passing in the prompt, e.g.: "N generated files were also changed, consistent with the template/source changes."

### 2. Parse Model Arguments

The user may specify models in the argument. Parse the argument to identify requested models:

<!-- LAST REVIEWED: 2026-06. Bump model versions quarterly; `consult/SKILL.md` is the source of truth for this table, keep them identical. -->

| Shorthand | Model name |
|-----------|------------|
| (none/blank) | current session model + Sonnet 4.6 in parallel |
| `opus` | Claude Opus 4.8 |
| `sonnet` | Claude Sonnet 4.6 |
| `gpt` | GPT-5.5 |
| `codex` | GPT-5.3 Codex |
| `gemini` | Gemini 3.1 Pro |
| `flash` | Gemini 3.5 Flash |
| `all` | opus + sonnet + gpt + gemini in parallel |

**Model parameter format varies by environment.** Check what `runSubagent` expects:
- If the tool docs specify a format like `"Name (Vendor)"`, append the vendor (e.g., `Claude Opus 4.8 (copilot)`).
- If the tool accepts plain model names or IDs, use the model name directly (e.g., `claude-opus-4-8`).
- When in doubt, check the `runSubagent` tool's `model` parameter description for the expected format.

If multiple models are specified (e.g., `/pre-commit-review opus sonnet`), run one subagent per model **in parallel**.

If no models are specified, default to **the current session model + Sonnet 4.6 in parallel**. On a nontrivial diff the two Claude models reliably catch *different* real issues, so a single pass drops findings; neither model is a superset of the other. Sonnet does over-report, so adjudicate the merged list rather than trusting volume. For a small or trivial change, pass one model (e.g. `/pre-commit-review opus`) to skip the second pass. A subagent is always used to isolate the review from the main session and save context tokens.

### 3. Launch Review Subagent(s)

For each requested review, call `runSubagent` with:

- `description`: "Code review (`<model-short-name>`)" - use distinct descriptions per model
- `model`: The model string from the table in Step 2 (omit for current session model)
- `prompt`: Construct the prompt using the template below

#### Building the Prompt

Before constructing the prompt, write a **2-4 sentence change summary** describing what was changed and why, based on the conversation history. This gives the reviewer the same intent context you have. Include: the goal, the approach taken, and any notable design decisions. This replaces the `{CHANGE_SUMMARY}` placeholder below.

#### Subagent Prompt Template

```
You are a code reviewer. Review the uncommitted changes in this workspace for issues. This is a pre-commit review.

## Change Summary

{CHANGE_SUMMARY}

## Changed Files

{LIST THE CHANGED FILES HERE}

## Review Criteria

Evaluate the changes for:
1. **Correctness & Bugs**: Logic errors, off-by-one, nil/null derefs, race conditions, missing error handling
2. **Security**: Injection, auth issues, secrets exposure, unsafe input handling (OWASP Top 10)
3. **Code Style & Consistency**: Naming, structure, idiomatic patterns for the language
4. **Performance**: Unnecessary allocations, N+1 queries, missing indexes, algorithmic complexity
5. **AI Code Smells**: dead code, hallucinated constants, zero-value leaks, fields mapped by name similarity instead of semantics, training-data artifacts (wrong copyright years, URLs from other projects).
6. **Commit Alignment**: do the changes match the stated intent? Flag code that looks leftover from a different approach.
7. **Test Coverage**: behavioral changes should have tests. Flag untested logic paths.
8. **Duplication**: check whether existing code already does this. Cross-component changes should share a single source of truth (constant, type, helper) or at least a code comment linking the call sites.

Skip criteria that are irrelevant to the file types being reviewed.

## Instructions

Get the full diff for these files yourself using git. Read the changed files in full to understand surrounding context. Use workspace search tools to check for dead code, duplication, and verify constants. Only read files within the workspace.

**Work file-by-file.** Do not review the whole changeset in one pass. Walk the changed files one at a time, apply the full criteria list above to each file's diff, and record that file's findings before moving to the next. On a large or busy diff, a single holistic pass spends its attention on the loud findings (concrete bugs) and silently drops the quiet ones (a missing test, a convention violation); per-file passes keep each file's coverage intact.

**Then do a coverage sweep.** After the per-file pass, re-scan the changed files once more, looking only for the findings most easily lost next to concrete bugs: a new handler/endpoint/converter with no test, dialect- or convention-specific issues, and missing changelog/docs. This is a coverage check, not a licence to invent issues. Do not raise test-gaps on trivial helpers.

Only flag issues introduced by the changes. Before reporting any finding, verify the problematic code appears in the `git diff` output (added or modified lines). If code was not changed in this diff, it is out of scope - even if you discover a real bug while reading surrounding context. Pre-existing issues in unchanged code may be mentioned as INFO at most, clearly labeled "[pre-existing]", but never WARNING or CRITICAL.

Do not flag untracked files unless they appear in the staged changes. Do not assume which files the user intends to commit.

Plan files and design docs (e.g. `.prompt.md`, `.md` files in prompts or docs directories) are working documents that may lag behind the implementation. The CODE is the source of truth. Do not flag code-vs-plan deviations.

**Do not report successful validation as findings.** Findings are for problems. If you verified something and it was correct, that is not a finding.

**Validate before reporting.** Once you have drafted your findings, re-read the exact lines each one cites and check reachability (is the code path actually reachable, are there guards that already handle it). Drop any finding you cannot substantiate against the cited lines. Only then write the review.

## Output Format

Return a review with:
- A summary with overall risk level (low/medium/high)
- Findings with file locations, severity (CRITICAL/WARNING/INFO), confidence (high/medium/low), and suggested fixes
- A verdict: APPROVE, REQUEST_CHANGES, or COMMENT

Severity and confidence are separate axes: severity is the impact if the finding is real; confidence is how sure you are that it is real. A high-impact guess is still a guess. **Do not use hedge words ("possibly", "might", "appears to") on a high-confidence finding** - if you are hedging, it is not high confidence, so label it medium or low.

**Report low-confidence findings too, clearly labeled; do not silently drop them.** This is an interactive review where the user decides what matters, so a labeled "maybe" is more useful than a hidden one. (Filtering low-confidence findings makes sense when posting unattended to a PR, but not here.)

If there are no issues, say so clearly. Do not fabricate issues.
```

### 4. Process Review Results

After all subagent(s) return:

**Context management:** The subagent responses contain the full review output. Present a concise summary to the user (findings table + verdict). Do NOT quote the full raw response in subsequent turns - summarize it once, then refer back to specific findings by number if the user asks follow-up questions.

**For multi-model reviews**, present a single unified findings table:

| # | Severity | Finding | Opus | Sonnet | GPT | Gemini |
|---|----------|---------|------|--------|-----|--------|
| 1 | WARNING | Description of issue | WARNING | WARNING | - | - |
| 2 | INFO | Another issue | - | INFO | INFO | INFO |

Show which models caught each finding and at what severity. After the table, add a brief note on model differences (1-2 sentences).

**For single-model reviews**, present findings directly without the model columns.

Then **ask the user** what they want to do:
- Fix all issues
- Fix specific issues (by number)
- Ignore and commit as-is
- Get more details on specific findings

If the user chooses to fix issues, proceed with the fixes in the main session using the review findings as guidance.

### 5. Verify Findings Before Acting

**Verify each finding against the actual file before applying it**, especially typo, spelling, syntax, or other small text-level claims. LLM reviewers occasionally hallucinate text-level issues (e.g., reporting a typo or extra whitespace that does not exist in the file). A quick grep or read takes seconds and avoids introducing a real bug by "fixing" a phantom one.

If the user wants to commit after approval, stage only the files related to the reviewed changes. Do not use `git add -A` as that may stage unrelated files.
