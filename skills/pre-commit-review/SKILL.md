---
name: pre-commit-review
description: "Review code changes before committing in isolated reviewer sessions. Use when: pre-commit review, review my changes, code review before commit, review agent changes, multi-model review. Keeps full review reports out of the implementation context."
---

# Pre-Commit Code Review

Run a code review in isolated reviewer sessions. Supports multiple models in parallel.

## Transport

Use a dispatched read-only review for every immutable candidate commit. A generic uncommitted review can keep the isolated-subagent route below.

Write one self-contained prompt file. Name the plan, repository instructions, base and candidate SHAs, change summary, changed files, known gaps, completed checks, review criteria, and allowed repository roots. Forbid reads outside those roots. Keep the prompt and raw reports outside the candidate worktree.

Reuse the exact prompt bytes for every model in a controlled comparison. Do not put a result path in that prompt. Give each launch a unique result destination through transport metadata. Launch with `python3 dispatch/dispatch.py --read-only --effort high` and record the runner, model and effort. Without `--effort`, each runner uses its own default, and a Claude reviewer then runs at medium. When the active Codex session id is unambiguous, pass it through `--notify-thread` and let the callback trigger result processing. Never poll a reviewer from model turns. Once complete, read each report once. Carry forward only verified findings.

Use one initial dispatched pass per requested model. After batched corrections, start at most one new focused re-review session on the new immutable commit. Do not resume a reviewer against an evolving tree. Ask the user before starting any further premium review round.

Reviewers are read-only reporters. Their prompts forbid edits, fixes, commits, and launching subagents. The implementation session verifies findings and batches accepted corrections. If a dispatched reviewer has no usable result after one hour, report its state and ask whether to continue, stop, or replace it.

## When to Use

- Before committing changes (especially agent-generated changes)
- To get a second opinion on code quality without cluttering the main session
- To compare review perspectives across different models

## Procedure

### 1. Determine What to Review

Determine the list of changed files to review.

- **Uncommitted changes (default)**: Use `git diff` to get changed files.
- **Immutable candidate**: Review the full candidate with `git diff <base> <candidate>`.
- **Single commit without a supplied base**: Review it with `git diff <ref>~1 <ref>`.
- **No changes**: If the working tree is clean and no commit is specified, review the latest commit.

**Exclude generated files.** Before building the file list, check if any changed files are generated from templates or build outputs. Exclude them from the detailed review list. Mention them in passing in the prompt, e.g.: "N generated files were also changed, consistent with the template/source changes."

### 2. Parse Model Arguments

The user may specify models in the argument. Parse the argument to identify requested models:

The shorthands, and how to pass a model to `runSubagent`, are in [../references/models.md](../references/models.md). Read it rather than assuming a version; `consult` shares that file, so neither skill carries its own copy.

If multiple models are specified (e.g., `/pre-commit-review opus sonnet`), run one isolated reviewer per model **in parallel**, using the selected transport.

If no models are specified, use one reviewer on the current model, and omit the `model` parameter. Use multiple premium reviewers only when the user requests them or a calling workflow names them. Every reviewer is isolated from the implementation context.

**OpenAI reviewer without `runSubagent`:** use the dispatched transport above for a fresh Codex review of an immutable commit. Point it at the commit (it reads the repository itself; do not pipe the diff through stdin), state the invariants the change must hold, and demand ranked findings with file:line and a concrete failure scenario each.

### 3. Launch reviewer(s)

For the subagent transport, call `runSubagent` for each requested review with:

- `description`: "Code review (`<model-short-name>`)" - use distinct descriptions per model
- `model`: The model string from the table in Step 2 (omit for current session model)
- `prompt`: Construct the prompt using the template below

#### Building the Prompt

Before constructing the prompt, write a **2-4 sentence change summary** describing what was changed and why, based on the conversation history. This gives the reviewer the same intent context you have. Include: the goal, the approach taken, and any notable design decisions. This replaces the `{CHANGE_SUMMARY}` placeholder below.

**Then list what you already know is missing and chose to leave alone.** This replaces `{KNOWN_GAPS}`. A gap you left on purpose reads to a reviewer exactly like a gap nobody noticed, and leaving it unsaid costs twice. The reviewer spends part of its budget rediscovering it, and when the reviewer does not find it you cannot tell whether it looked. Write each one as the case, then the instruction, then the invitation:

```
Known and deliberately not fixed: a conversation the panel opens fresh has neither `resume`
nor `sessionId`, so the seed does not cover its first press. Do not report that one. Do
report anything else the seed misses.
```

The last sentence is not optional. Naming a gap tells the reviewer where to stop looking, and without the invitation it stops on the whole area rather than on the one case. Write "None." when there are none. An empty section is a claim the reviewer can act on, and a missing one is silence.

#### Subagent Prompt Template

```
You are a code reviewer. Review the uncommitted changes in this workspace for issues, before they are committed.

## Change Summary

{CHANGE_SUMMARY}

## Changed Files

{LIST THE CHANGED FILES HERE}

## Known and out of scope

{KNOWN_GAPS}

## Review Criteria

Evaluate the changes for:
1. **Correctness & Bugs**: Logic errors, off-by-one, nil/null derefs, race conditions, missing error handling
2. **Security**: Injection, auth issues, secrets exposure, unsafe input handling (OWASP Top 10)
3. **Code Style & Consistency**: Naming, structure, idiomatic patterns for the language
4. **Performance**: Unnecessary allocations, N+1 queries, missing indexes, algorithmic complexity
5. **AI Code Smells**: dead code, hallucinated constants, zero-value leaks, fields mapped by name similarity instead of semantics, training-data artifacts (wrong copyright years, URLs from other projects).
6. **Commit Alignment and behavioral subtraction**: do the changes match the stated intent? For every user-visible value, control, or warning removed or made conditional, cite explicit authorization in the current request, current plan, or repository requirements. Silence is not authorization. Flag code that looks leftover from a different approach.
7. **Test Coverage**: behavioral changes should have tests. Flag untested logic paths.
8. **Duplication & divergent sources of truth**: check whether existing code already does this. Cross-component changes should share a single source of truth (constant, type, helper) or at least a code comment linking the call sites. For any field, serialized format, checksum, or constant the diff reads or writes, grep the repo for its OTHER readers and writers; if another site derives the "same" value from a different source, flag the divergence even though that site isn't in the diff.
9. **Cost at scale**: name what the change costs at the largest input the product meets in production, or say why the change cannot reach that size.
10. **Incomplete removal**: criterion 8 asks about symbols the diff reads or writes. This is its mirror, and a diff that deletes things needs it. For every symbol, string, class name, field or config key the diff DELETES, grep the repo for what still produces it and what still consumes it. A feature usually lives in several layers: what computes it, what names it in a strings table, what draws it, what stores it, and what documents it. A diff that deletes one layer and leaves the rest passes every other criterion cleanly, and the feature is still on screen. Check the removal's own guard as well: a guard that reads one file, while the deleted thing lived in two, certifies a removal that did not happen. Say which layers the diff reached and which it did not.

Skip criteria that are irrelevant to the file types being reviewed.

## Instructions

Get the full diff for these files yourself using git. Read the changed files in full to understand surrounding context. Use workspace search tools to check for dead code, duplication, and verify constants. Only read files within the workspace.

**The working tree is not yours.** Another session may be editing this repository while you read it, and its work is uncommitted and unrecoverable. Never run `git checkout -- <path>`, `git stash`, `git reset --hard`, or `git clean`, whatever the reason. Do not mutation-test a guard or write a temporary change into the workspace. If the tree is dirty in files outside your review range, leave them alone and say so in your report.

**Work file-by-file.** Do not review the whole changeset in one pass. Walk the changed files one at a time, apply the full criteria list above to each file's diff, and record that file's findings before moving to the next. On a large or busy diff, a single holistic pass spends its attention on the loud findings (concrete bugs) and silently drops the quiet ones (a missing test, a convention violation); per-file passes keep each file's coverage intact.

**Report only.** Do not edit files, implement fixes, commit, or launch subagents. The calling
implementation session owns corrections.

**Then do a coverage sweep.** After the per-file pass, re-scan the changed files once more, looking only for the findings most easily lost next to concrete bugs: a new handler/endpoint/converter with no test, dialect- or convention-specific issues, and missing changelog/docs. This is a coverage check, not a licence to invent issues. Do not raise test-gaps on trivial helpers.

Also run a bounded beyond-diff sweep: for each shared symbol (field, serialized format, checksum, constant) the diff reads or writes, grep its other writers and readers and confirm they agree on source and shape. This is scoped to the symbols the diff touches, not a whole-repo review, so it stays cheap. It is what catches divergent-source-of-truth drift, where the conflicting code is a file the diff never opened.

Only flag issues introduced by the changes. Before reporting any finding, verify the problematic code appears in the `git diff` output (added or modified lines). If code was not changed in this diff, it is out of scope - even if you discover a real bug while reading surrounding context. Pre-existing issues in unchanged code may be mentioned as INFO at most, clearly labeled "[pre-existing]", but never WARNING or CRITICAL.

Do not flag untracked files unless they appear in the staged changes. Do not assume which files the user intends to commit.

Plan files can lag implementation details. When the review prompt names a current plan, treat its requirements and repository-owned design requirements as constraints. Code outranks stale implementation notes, not current product intent. Flag contradictions.

**When the change under review IS a design document** rather than code with one attached, most criteria above do not apply, and an omissions pass matters more than the verification pass. A prompt that lists claims to verify gets those claims verified and nothing else, so ask separately what the document does not say: a step that cannot be followed as written, an axis never mentioned, a claim that something does not exist, a case the verification section cannot reach. Two reviewers on an identical prompt can return zero overlapping findings, with the one that checked every stated claim approving a plan whose central step was unimplementable.

**Do not report successful validation as findings.** Findings are for problems. If you verified something and it was correct, that is not a finding.

**Validate before reporting.** Once you have drafted your findings, re-read the exact lines each one cites and check reachability (is the code path actually reachable, are there guards that already handle it). Drop any finding you cannot substantiate against the cited lines. Only then write the review.

## Output Format

Return a review with:
- A summary with overall risk level (low/medium/high)
- Findings with file locations, severity (CRITICAL/WARNING/INFO), confidence (high/medium/low), and suggested fixes
- **The exact text your finding rests on, at the location you cite, copied character for character.**
  A finding you cannot quote is not a finding. Whoever called you checks the quote against the file,
  so a paraphrase or a reconstructed line reads as fabricated.
- A verdict: APPROVE, REQUEST_CHANGES, or COMMENT

Severity and confidence are separate axes: severity is the impact if the finding is real; confidence is how sure you are that it is real. A high-impact guess is still a guess. **Do not use hedge words ("possibly", "might", "appears to") on a high-confidence finding** - if you are hedging, it is not high confidence, so label it medium or low.

**Report low-confidence findings too, clearly labeled; do not silently drop them.** This is an interactive review where the user decides what matters, so a labeled "maybe" is more useful than a hidden one. (Filtering low-confidence findings makes sense when posting unattended to a PR, but not here.)

If there are no issues, say so clearly. Do not fabricate issues.
```

### 4. Process Review Results

After all subagent(s) return:

**Context management:** The subagent responses contain the full review output. Present a concise summary to the user (findings table + verdict). Do not quote the full raw response in subsequent turns - summarize it once, then refer back to specific findings by number if the user asks follow-up questions.

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

**Verify each finding against the actual file before applying it.** This splits into two checks: one compares text, and one needs judgement.

*Does the cited text exist where the finding says?* Open the cited file at the cited line and compare the quote with what is there, ignoring differences in whitespace. A quote that was re-indented or that spans a line break is still present. Read the cited lines instead of searching for the quote, because a search misses on whitespace, and the miss then reads as a hallucination when the finding is correct. A quote that appears nowhere in the cited file, or a cited file that does not exist, is a fabricated citation and needs no further judgement.

*Does the cited text mean what the finding says?* This is the half that needs judgement, and it is where the remaining errors live. A finding can quote a real line perfectly and draw a conclusion the line does not support. Read the surrounding code, not just the quoted fragment, before acting on any finding whose quote checked out.

If the user wants to commit after approval, stage only the files related to the reviewed changes. Do not use `git add -A` as that may stage unrelated files.

### 6. Delete the review artifacts

A dispatched review writes its prompt, its log and its raw report outside the repository, so the reviewer never sees or dirties the tree it reviews. Nothing removes them afterwards, and every review leaves a directory behind.

Delete the run directory once the findings are verified and every accepted correction is committed. The commit and the summary already carry what the review found, and the raw report is a copy nobody opens again.

```bash
rm -rf <dispatch parent>/<run name>
```

Keep the directory only while a finding is still open, or when the user asks for the report. Give every run its own directory under one parent, so the removal is one command. Say in the summary that the artifacts are gone, so nobody looks for a path that no longer resolves.
