---
name: pre-commit-review
description: "Review code changes before committing in isolated reviewer sessions. Use when: pre-commit review, review my changes, code review before commit, review agent changes, multi-model review. Keeps full review reports out of the implementation context."
---

# Pre-Commit Code Review

Run a code review in isolated reviewer sessions. Supports multiple models in parallel.

[README.md](README.md) explains the reasons behind review rules. Before changing a rule, read its relevant entry and add the reason for the change in the same commit.

Read [../references/owner-brief.md](../references/owner-brief.md) before preparing any worker prompt. Apply it to initial briefs, review rounds, refreshed briefs, and native or dispatched consultation and falsification rounds.

## Transport

Read [../references/model-providers.md](../references/model-providers.md) before selecting reviewer models and transport. Follow its provider restrictions while keeping this workflow's read-only roles and review limits.

Prefer isolated native reviewers when the active harness supports the requested model, provider, effort, and report-only role. Apply this choice to immutable candidates and uncommitted reviews. Prompt restrictions establish the reviewer's role, not an enforced filesystem read-only sandbox. When the task requires enforced sandboxing, use a transport that supplies it, such as Codex dispatch with `--read-only`. Use dispatched read-only review when the native combination or isolation is unavailable. Never substitute another requested model, provider, or effort.

Dispatched review requires an immutable target. When uncommitted changes lack a supported native review route, report that limit instead of dispatching against a moving tree.

Write one self-contained prompt file. Name the plan, repository instructions, base and candidate SHAs, change summary, changed files, known gaps, completed checks, review criteria, and allowed repository roots. Forbid reads outside those roots. Keep the prompt and raw reports outside the candidate worktree.

Reuse the exact prompt bytes for every model in a controlled comparison. Do not put a result path in that prompt. Give each launch a unique result destination through transport metadata. When native metadata has no report destination, the coordinator saves the returned report verbatim to that launch's destination outside the candidate worktree. Record the harness or runner, model, provider, effort, and chosen completion route. Preserve requested effort in native parameters or dispatched `--effort`. Use high when no effort is specified.

Select one completion route before each launch. A native reviewer returns through harness completion only. It must not launch a detached worker, request a queue callback, or independently repeat completion handling. Save its raw report when that completion arrives. For dispatched review, follow [../dispatch/SKILL.md](../dispatch/SKILL.md) for a detached callback or a supported waited route. Launch with `--read-only` and explicit `--effort`. Keep results and callback logs available for explicit recovery when delivery fails. Recovery does not cancel an accepted queued callback. Never poll a reviewer from model turns. Once complete, process each report through its selected route and carry forward only verified findings.

A detached callback handler claims its exact dispatch receipt event before processing the frozen report. It acknowledges after saving the report and its disposition durably. A repeated consumed event returns the recorded outcome. Interrupted processing requires checking saved effects before resuming the recorded claim. Changed reports remain separate unread events. Use dispatch's explicit recovery commands for uncertain delivery or stopped monitoring.

`$SKILLS_DIR` is the harness-reported skill directory with its last component removed as text, before filesystem traversal. Never obtain it through filesystem `..`. Read `references/session-naming.md` in `$SKILLS_DIR` once per invocation. Choose each reviewer's launch name before launch. Apply the convention to dispatched and native reviews, including plan reviews.

Keep the calling workflow's reviewer count and round limits. Without caller limits, use one initial pass per requested model and at most one focused re-review after batched corrections. Select transport for the new immutable commit under the same rules. Do not resume a reviewer against an evolving tree. Ask the author before any further premium review round that the caller's limits do not authorize.

For that re-review, give the reviewer the accepted findings with their exact quotes and the fix delta. Use `git diff <candidate> <replacement>` when the base did not move. Use `git range-diff <base>..<candidate> <target>..<replacement>` when the target moved. Ask the reviewer to read the delta first, then the code that uses the changed lines. Keep the full change available for context, and give this reviewer half the first round's tool-call budget.

Reviewers are read-only reporters. Their prompts forbid edits, fixes, commits, and launching subagents. The implementation session verifies findings and batches accepted corrections. If a dispatched reviewer has no usable result after one hour, report its state and ask whether to continue, stop, or replace it.

Record an immutable-candidate review only when the author asks to save a review record. A request such as "save the review record" or "keep a machine-readable review record" opts in without a path. Save it under the reviewed repository's `.claude/review-records/` when the author gives no path. An explicit file path overrides that directory. Without an opt-in request, follow the existing review and cleanup steps without a record. For uncommitted changes, write no record even when the author asks, and state that in the result. Follow [references/run-record.md](references/run-record.md) when recording.

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

The author may name models and request a record in ordinary language, with or without a file path. Parse the model choice, record opt-in, and optional path separately. For example, "Review this commit with GPT and save the review record" selects a model and the default record location. Do not treat a model name or a review request alone as record opt-in.

The shorthands, and how to pass a model to `runSubagent`, are in [../references/models.md](../references/models.md). Read it rather than assuming a version; `consult` shares that file, so neither skill carries its own copy.

If multiple models are specified (e.g., `/pre-commit-review opus sonnet`), run one isolated reviewer per model **in parallel**, using the selected transport.

If no models are specified, use one reviewer on the current model, and omit the `model` parameter. Use multiple premium reviewers only when the author requests them or a calling workflow names them. Every reviewer is isolated from the implementation context.

Set a numeric tool-call budget in each first-round reviewer prompt. A focused re-review gets half that budget.

**OpenAI reviewer without `runSubagent`:** use another supported native reviewer tool when it meets the Transport requirements. Otherwise use dispatched read-only review. Point the reviewer at the immutable commit, state the invariants, and demand ranked findings with file:line and a concrete failure scenario each.

### 3. Launch reviewer(s)

For native transport, use the active harness's reviewer tool, such as `runSubagent` or Codex `spawn_agent`. Choose an isolated context with no implementation history. For Codex, use `fork_turns: "none"`. Preserve requested model and effort under the provider rules. Never attach overrides to a full-history fork. Supply these fields through the tool's supported parameters:

- `description` or `task_name`: The chosen workflow name, such as `code-review-1-catalog-update-sol`. Use the artifact stage and actual review round. Adapt punctuation to the tool's naming rules.
- `model`: The model string from the table in Step 2 (omit for current session model)
- Effort: Set the requested effort through the tool's supported parameter, such as Codex `reasoning_effort`.
- `prompt` or `message`: Construct the prompt using the template below

#### Building the Prompt

Before constructing the prompt, write a **2-4 sentence change summary** stating the goal and approach. This replaces the `{CHANGE_SUMMARY}` placeholder below. Do not claim that the change works or handles a case. The reviewer checks whether the implementation meets that goal.

When recording, capture the source skill's full commit SHA in `run.json` as `skill_version` when saving the first reviewer prompt. Use the source revision supplied with a copied installation when its Git history is unavailable. The record writer reads this saved value after the review, so a later skill edit cannot change the recorded version.

**Then list what you already know is missing and chose to leave alone.** This replaces `{KNOWN_GAPS}`. A gap you left on purpose reads to a reviewer exactly like a gap nobody noticed, and leaving it unsaid costs twice. The reviewer spends part of its budget rediscovering it, and when the reviewer does not find it you cannot tell whether it looked. Use "Do not report" only for author-approved exclusions, and assess implementer disclosures under existing scope and severity rules. For an author-approved exclusion, use this form:

```
Author instruction: "{OWNER_INSTRUCTION_QUOTE}"
Source: {OWNER_INSTRUCTION_SOURCE}
Known and deliberately not fixed: a conversation the panel opens fresh has neither `resume`
nor `sessionId`, so the seed does not cover its first press. Do not report that one. Do
report anything else the seed misses.
```

For an author-approved exclusion, keep the invitation to review other cases. Write "None." when there are none. An empty section is a claim the reviewer can act on, and a missing one is silence.

Set `{REVIEW_TARGET}` to the uncommitted changes or to the immutable candidate SHA against its base SHA.

#### Subagent Prompt Template

```
You are a code reviewer. Review {REVIEW_TARGET} for issues.

## Change Summary

{CHANGE_SUMMARY}

## Plan and Brief

{PLAN_AND_BRIEF_PATHS_OR_NONE}

## Changed Files

{LIST THE CHANGED FILES HERE}

## Allowed repository roots

{ALLOWED_REPOSITORY_ROOTS}

## Known gaps and authorized exclusions

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

Get the full diff for these files yourself using git. Read the changed files in full to understand surrounding context. Use workspace search tools to check for dead code, duplication, and verify constants.

For an immutable candidate, read its content with `git show <candidate>:<path>` or from a checkout fixed at that SHA. Do not substitute moving working-tree content. Stay within the allowed repository roots named in this prompt.

**The working tree is not yours.** Another session may be editing this repository while you read it, and its work is uncommitted and unrecoverable. Never run `git checkout -- <path>`, `git stash`, `git reset --hard`, or `git clean`, whatever the reason. Do not mutation-test a guard or write a temporary change into the workspace. If the tree is dirty in files outside your review range, leave them alone and say so in your report.

**Work file-by-file.** Do not review the whole changeset in one pass. Walk the changed files one at a time, apply the full criteria list above to each file's diff, and record that file's findings before moving to the next. On a large or busy diff, a single holistic pass spends its attention on the loud findings (concrete bugs) and silently drops the quiet ones (a missing test, a convention violation); per-file passes keep each file's coverage intact.

**Report only.** Do not edit files, implement fixes, commit, or launch subagents. The calling
implementation session owns corrections.

**Then do a coverage sweep.** After the per-file pass, re-scan the changed files once more, looking only for the findings most easily lost next to concrete bugs: a new handler/endpoint/converter with no test, dialect- or convention-specific issues, and missing changelog/docs. This is a coverage check, not a licence to invent issues. Do not raise test-gaps on trivial helpers.

Also run a bounded beyond-diff sweep: for each shared symbol (field, serialized format, checksum, constant) the diff reads or writes, grep its other writers and readers and confirm they agree on source and shape. This is scoped to the symbols the diff touches, not a whole-repo review, so it stays cheap. It is what catches divergent-source-of-truth drift, where the conflicting code is a file the diff never opened.

Only flag issues introduced by the changes. Before reporting any finding, verify the problematic code appears in the `git diff` output (added or modified lines). If code was not changed in this diff, it is out of scope - even if you discover a real bug while reading surrounding context. Pre-existing issues in unchanged code may be mentioned as INFO at most, clearly labeled "[pre-existing]", but never WARNING or CRITICAL.

Do not flag untracked files unless they appear in the staged changes. Do not assume which files the author intends to commit.

Plan files can lag implementation details. When the review prompt names a current plan, treat its requirements and repository-owned design requirements as constraints. Code outranks stale implementation notes, not current product intent. Flag contradictions.

Check each factual claim that the review reaches against the code. A contradiction is a finding that quotes both the claim and the code. Exclude a known gap only when the author's recorded instruction excludes it. An implementer's disclosure is not author approval. Assess other gaps under the existing severity and scope rules.

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

**Report low-confidence findings too, clearly labeled; do not silently drop them.** This is an interactive review where the author decides what matters, so a labeled "maybe" is more useful than a hidden one. (Filtering low-confidence findings makes sense when posting unattended to a PR, but not here.)

If there are no issues, say so clearly. Do not fabricate issues.
```

### 4. Verify Findings Before Acting

Run this as soon as a report arrives, before any finding is presented to the author. This splits into two checks: one compares text, and one needs judgement.

*Does the cited text exist where the finding says?* Run `quote_check.py` against the report, with `--root` set to a checkout at the reviewed state: the commit itself for an immutable candidate, or the working tree as it stood before any fix began for an uncommitted review. `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it.

A dispatched review writes its report to a file outside the repository. Point `--report` at that file:

```bash
python3 "$SKILL_DIR/scripts/quote_check.py" --root <reviewed tree> --report <report file>
```

A native review returns its report as text. Save it verbatim at the launch's report destination, then use the same `--report` command. For an uncommitted review without a saved report, pipe the returned text on stdin instead. Use a quoted heredoc, never a double-quoted here-string: a double-quoted here-string runs every backtick span in the report as a command and expands every `$`, which corrupts the report before the script ever sees it.

```bash
python3 "$SKILL_DIR/scripts/quote_check.py" --root <reviewed tree> <<'REPORT'
<report text>
REPORT
```

Read every finding's verdict from the JSON output, whatever the exit code, except exit 2 or exit 3. Exit code 0 can still include `present-elsewhere` findings, so the exit code alone never substitutes for reading the JSON. Exit code 1 means at least one finding is `absent`, `file-missing`, or `unparseable`. Exit code 2 is a usage error. Fix the command and rerun it. Exit code 3 means the script could not parse any finding out of the report. The report can still hold real findings in a format the script does not recognize, so on exit 3, check every finding in the report by hand.

Drop only the findings the script's JSON marks `absent` or `file-missing`. These never reach the author as findings. The summary in step 5 notes their count. For a finding marked `present`, skip re-comparing the quote against the file and move straight to the judgement check below. Give a finding marked `present-elsewhere` or `unparseable` the full check: compare the quote against the file yourself, then judge it. Give the same full check to any finding the JSON does not list at all, for example because the report mixed formats. Never drop an `unparseable` finding on the script's verdict alone, since a quote the script could not pair or place can still name a real problem.

*Does the cited text mean what the finding says?* This is the half that needs judgement, and it is where the remaining errors live. A finding can quote a real line perfectly and draw a conclusion the line does not support. Read the surrounding code, not just the quoted fragment, before acting on any finding whose quote checked out.

### 5. Process Review Results

After step 4 verifies the findings:

When recording, mark an accepted finding that contradicts a factual brief claim with `disputes_brief: true` in the triage. Keep every reviewer's report and its final call in the run folder until the record is written.

**Context management:** The subagent responses contain the full review output. Present a concise summary of the surviving findings to the author (findings table + verdict), noting the count of any findings dropped as absent or file-missing without listing them as findings. Do not quote the full raw response in subsequent turns - summarize it once, then refer back to specific findings by number if the author asks follow-up questions.

**For multi-model reviews**, present a single unified findings table:

| # | Severity | Finding | Opus | Sonnet | GPT | Gemini |
|---|----------|---------|------|--------|-----|--------|
| 1 | WARNING | Description of issue | WARNING | WARNING | - | - |
| 2 | INFO | Another issue | - | INFO | INFO | INFO |

Show which models caught each finding and at what severity. After the table, add a brief note on model differences (1-2 sentences).

**For single-model reviews**, present findings directly without the model columns.

Then **ask the author** what they want to do:
- Fix all issues
- Fix specific issues (by number)
- Ignore and commit as-is
- Get more details on specific findings

If the author chooses to fix issues, proceed with the fixes in the main session using the review findings as guidance.

If the author wants to commit after approval, stage only the files related to the reviewed changes. Do not use `git add -A` as that may stage unrelated files.

### 6. Record and delete the review artifacts

Native and dispatched reviews keep their prompt and raw report outside the candidate worktree. A dispatched review also keeps its worker and callback logs there. The calling workflow can retain these artifacts for recovery or review evidence.

When record opt-in applies to an immutable candidate, write the record with `scripts/write_record.py` after triage and corrections. Omit `--out` for the default location, or pass `--out <record file>` for an explicit path. Keep the run directory when recording fails, and report the error and its path so the caller can retry. Delete the run directory only after the record is written and every accepted correction is committed.

Without record opt-in, delete the run directory once the findings are verified and every accepted correction is committed. The commit and the summary already carry what the review found, and the raw report is a copy nobody opens again.

```bash
rm -rf <run parent>/<run name>
```

Keep the directory while a finding is open, recording or delivery has failed, or the caller or author requires retention. Give every run its own directory under one parent, so the removal is one command. Say in the summary that the artifacts are gone, so nobody looks for a path that no longer resolves.
