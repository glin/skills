---
name: review
description: "Coordinate isolated reviewer sessions on a code change or a written plan, loading the rule set that fits the target and any rules the caller passes. Use when: review my changes, code review, review before commit, review agent changes, multi-model review, review this plan, plan review, check the plan before building it, /review. Saves full reports outside the candidate tree. Reading a saved report adds it to the parent context."
---

# Review

Coordinate isolated reviewers and load the rule sources for the target. Supports multiple models in parallel.

This skill is the runner. It owns model selection, transport, budgets, prompt assembly, report handling, quotation checks, triage, and recording. A rule file defines what a reviewer examines and how it reports. [rules/code.md](rules/code.md) reviews a code change. [rules/plan.md](rules/plan.md) reviews a written plan before implementation. A caller can add its own rules file, as step 1 describes.

Project-specific review rules stay with the caller, which owns their names and locations. Keep this public skill independent of private project instructions.

[README.md](README.md) explains the reasons behind review rules. Before changing a rule, read its relevant entry and add the reason for the change in the same commit.

Read [../references/owner-brief.md](../references/owner-brief.md) before preparing any worker prompt. Apply it to initial briefs, review rounds, refreshed briefs, and native or dispatched consultation and falsification rounds.

## Transport

Read [../references/model-providers.md](../references/model-providers.md) before selecting reviewer models and transport. Follow its provider restrictions while keeping this workflow's read-only roles and review limits.

Prefer isolated native reviewers when the active harness supports the requested model, provider, effort, and report-only role. Apply this choice to immutable candidates and uncommitted reviews. Prompt restrictions establish the reviewer's role, not an enforced filesystem read-only sandbox. When the task requires enforced sandboxing, use a transport that supplies it, such as Codex dispatch with `--read-only`. Use dispatched read-only review when the native combination or isolation is unavailable. Never substitute another requested model, provider, or effort.

Dispatched review requires an immutable target. When uncommitted changes lack a supported native review route, report that limit instead of dispatching against a moving tree.

Write one self-contained prompt file. Name the plan, repository instructions, base and candidate SHAs, change summary, changed files, known gaps, completed checks, rule sources, and allowed repository roots. Leave out each field whose template section the rule file omits. Forbid reads outside those roots. Keep the prompt and raw reports outside the candidate worktree.

Assign each reviewer a unique report destination outside the candidate worktree. Native reviewers write their full report there and return only its path, available session metadata, and incomplete coverage. Permit that report write, never candidate edits. When native transport cannot write the assigned report, use dispatched review for an immutable candidate. Report the unavailable route for uncommitted changes instead of dispatching against a moving tree.

Reuse the exact prompt bytes for every model in a controlled comparison. Give each launch its destination through transport metadata. If native transport lacks a separate destination field, use dispatched review for that comparison. For ordinary native review, the prompt can name the destination. Record the harness or runner, model, provider, effort, and chosen completion route. Preserve requested effort in native parameters or dispatched `--effort`. Use high when no effort is specified.

Select one completion route before each launch. A native reviewer returns through harness completion only. It must not launch a detached worker or independently repeat completion handling. Accept its returned path only when it resolves to the assigned destination and contains a nonempty report. A missing report is incomplete review evidence. If completion unexpectedly includes the full report, save it verbatim and note the return-contract deviation. For dispatched review, follow [../dispatch/SKILL.md](../dispatch/SKILL.md) for its completion routes. Launch in the foreground with `--read-only --arc-dispatch --log PATH` and explicit model and effort. For waited completion, run the printed observation command for your shell separately in the background with `--wait-launch LAUNCH_ID --read-only`. Keep the same launch ID when accepted identity remains pending or an observation deadline expires. Keep each launch ID and result path available for recovery. Never poll a reviewer from model turns. Once complete, process each report through its selected route and carry forward only verified findings.

`$SKILLS_DIR` is the harness-reported skill directory with its last component removed as text, before filesystem traversal. Never obtain it through filesystem `..`. Read `references/session-naming.md` in `$SKILLS_DIR` once per invocation. Choose each reviewer's launch name before launch. Apply the convention to dispatched and native reviews, including plan reviews.

Keep the calling workflow's reviewer count and round limits. Without caller limits, use one initial pass per requested model and at most one focused re-review after batched corrections. Select transport for the new immutable commit under the same rules. Do not resume a reviewer against an evolving tree. Ask the author before any further premium review round that the caller's limits do not authorize.

For that re-review, give the reviewer the accepted findings with their exact quotes and the fix delta. Use `git diff <candidate> <replacement>` when the base did not move. Use `git range-diff <base>..<candidate> <target>..<replacement>` when the target moved. Ask the reviewer to read the delta first, then the code that uses the changed lines. Keep the full change available for context, and give this reviewer half the first round's tool-call budget.

Reviewers report findings and never change candidate files. Their prompts forbid fixes, commits, and launching subagents. Only the assigned native report write is permitted. The implementation session verifies findings and batches accepted corrections. If a dispatched reviewer has no usable result after one hour, report its state and ask whether to continue, stop, or replace it.

Record an immutable-candidate review only when the author asks to save a review record. A request such as "save the review record" or "keep a machine-readable review record" opts in without a path. Save it under the reviewed repository's `.claude/review-records/` when the author gives no path. An explicit file path overrides that directory. Without an opt-in request, follow the existing review and cleanup steps without a record. For uncommitted changes, write no record even when the author asks, and state that in the result. Follow [references/run-record.md](references/run-record.md) when recording.

## When to Use

- Before committing changes (especially agent-generated changes)
- To get a second opinion on code quality without cluttering the main session
- To compare review perspectives across different models
- Before implementing a written plan

## Procedure

### 1. Determine What to Review and Load Its Rules

The rule selector is an ordered list of rule sources. A source is `code`, `plan`, or `rules <source>`, where `<source>` is a Markdown file path or an installed skill name. A skill name resolves to `$SKILLS_DIR/<name>/SKILL.md`. The word `rules` always introduces a rules source, and any other Markdown path is a review target. Without a source, the target selects `code` or `plan`, as the target list after these examples shows.

- `/review` loads the rule set that the target selects.
- `/review plan <plan path>` loads the plan rules for that plan.
- `/review rules <path or skill name>` loads only that source.
- `/review code rules <path>` loads the code rules, then the caller's source, so the caller extends the built-in set.

A calling workflow passes the same selector. For a project's own pull request rules, it passes `code rules <path to the project's rules file>`. The caller names the path, and this skill never stores it.

Load the sources in the order given, and join their rule text in that order into `{RULE_SET}`. Runner-owned rules always apply, and no rule source overrides them: report only, the working-tree warning, the allowed roots, the report destination contract, and the exact-quote evidence in the report format. On any other conflict, the later source wins.

Name at most one built-in source. An explicit `plan` needs a Markdown plan file as the target. An explicit `code` needs a change target: uncommitted changes, a commit, or a candidate. A `rules` source leaves the kind to the target. When an explicit source and the target disagree, such as `/review code plan.md` or `plan` with only commit arguments, stop before launch and name both.

Read every rules source before launch. When a source cannot be resolved or read, stop the review and tell the author which source failed. Never fall back to the built-in rules.

One built-in rule file fills the template slots and adds or replaces procedure steps. It is the built-in source that the selector names, or the target's choice when the selector names none. When the selector names no built-in source, use that file's slots and steps, and leave its rule set block out of `{RULE_SET}`. A caller's source has no required structure. It can be a short checklist or a whole review skill with its own procedure, prompt, reviewer defaults, triage and record steps. Read it and apply what it asks for, with judgement. When it carries its own launch steps, keep this runner's transport, budgets, quote check and report handling. Its models, effort and reviewer count become this run's defaults. Its checks, prompt text, finding classes, triage and record steps apply as written.

- **A Markdown plan file named as the target**: Load [rules/plan.md](rules/plan.md). A plan file that reaches the review only through a diff loads the code rules.
- **Uncommitted changes (default)**: Use `git diff` to get changed files. Load [rules/code.md](rules/code.md).
- **Immutable candidate**: Review the full candidate with `git diff <base> <candidate>`. Load [rules/code.md](rules/code.md).
- **Single commit without a supplied base**: Review it with `git diff <ref>~1 <ref>`. Load [rules/code.md](rules/code.md).
- **No changes**: If the working tree is clean and no commit is specified, review the latest commit. Load [rules/code.md](rules/code.md).

Read exactly one built-in rule file, and never read the other. That file fills the template slots in step 3, names any template section it omits, and states which steps of this procedure it adds or replaces.

### 2. Parse Model Arguments

The author can name models and request a record in ordinary language, with or without a file path. Parse the model choice, record opt-in, and optional path separately. For example, "Review this commit with GPT and save the review record" selects a model and the default record location. Do not treat a model name or a review request alone as record opt-in.

The shorthands, and how to pass a model to `runSubagent`, are in [../references/models.md](../references/models.md). Read it rather than assuming a version. `consult` shares that file, so neither skill carries its own copy.

If multiple models are specified (e.g., `/review opus sonnet`), run one isolated reviewer per model **in parallel**, using the selected transport.

If no models are specified, use the default reviewers that the rule sources name. When they name none, use one reviewer on the current model, and omit the `model` parameter. Beyond those defaults, use multiple premium reviewers only when the author requests them or a calling workflow names them. Every reviewer is isolated from the implementation context.

Set a numeric tool-call budget in each first-round reviewer prompt. A focused re-review gets half that budget.

**OpenAI reviewer without `runSubagent`:** use another supported native reviewer tool when it meets the Transport requirements. Otherwise use dispatched read-only review. Point the reviewer at the immutable commit, state the invariants, and demand ranked findings with file:line and a concrete failure scenario each.

### 3. Launch reviewer(s)

When the caller requires comparison input retention, capture its saved prompt, rules, and assigned destinations before launch.
Use [../orchestrate/references/review-comparisons.md](../orchestrate/references/review-comparisons.md) for the shared input contract.
Save execution metadata and the exact report hash when the reviewer returns.

Fill `{REPORT_OUTPUT_CONTRACT}` with the selected route and its permitted output. An ordinary native prompt includes its assigned report path. A controlled prompt refers to the destination supplied through transport metadata. A dispatched prompt requests the full review as the final answer.

For native transport, use the active harness's reviewer tool, such as `runSubagent` or Codex `spawn_agent`. Choose an isolated context with no implementation history. For Codex, use `fork_turns: "none"`. Preserve requested model and effort under the provider rules. Never attach overrides to a full-history fork. Supply these fields through the tool's supported parameters:

- `description` or `task_name`: The chosen workflow name, such as `code-review-1-catalog-update-sol`. Use the artifact stage and actual review round. Adapt punctuation to the tool's naming rules.
- `model`: The model string from the table in Step 2 (omit for current session model)
- Effort: Set the requested effort through the tool's supported parameter, such as Codex `reasoning_effort`.
- `prompt` or `message`: Construct the prompt using the template below

#### Building the Prompt

Fill `{REVIEW_KIND}` and `{REVIEW_TARGET}` from the built-in rule file, and drop each section it omits. Fill `{RULE_SET}` from the rule sources as step 1 describes. List every loaded source in `{RULE_SOURCES}`, in load order, with the resolved path of each rules source, so the report shows which rules applied.

When the rule file keeps the Change Summary section, write a **2-4 sentence change summary** that states the goal and approach before you construct the prompt. This replaces the `{CHANGE_SUMMARY}` placeholder below. Do not claim that the change works or handles a case. The reviewer checks whether the implementation meets that goal.

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

#### Subagent Prompt Template

```
You are a {REVIEW_KIND} reviewer. Review {REVIEW_TARGET}.

## Change Summary

{CHANGE_SUMMARY}

## Plan and Brief

{PLAN_AND_BRIEF_PATHS_OR_NONE}

## Changed Files

{LIST THE CHANGED FILES HERE}

## Allowed repository roots

{ALLOWED_REPOSITORY_ROOTS}

Stay within the allowed repository roots named in this prompt.

## Known gaps and authorized exclusions

{KNOWN_GAPS}

## Rule sources

{RULE_SOURCES}

Name these rule sources at the top of your report.

Every finding names the location it cites and the exact text it rests on at that location, copied character for character. A finding you cannot quote is not a finding. Whoever called you checks the quote against the file, so a paraphrase or a reconstructed line reads as fabricated.

{RULE_SET}

## Working tree and role

**The working tree is not yours.** Another session can edit this repository while you read it, and its work is uncommitted and unrecoverable. Never run `git checkout -- <path>`, `git stash`, `git reset --hard`, or `git clean`, whatever the reason. Do not mutation-test a guard or write a temporary change into the workspace. If the tree is dirty in files outside your review range, leave them alone and say so in your report.

**Report only.** Do not edit candidate files, implement fixes, commit, or launch subagents. The calling implementation session owns corrections.

## Report destination and completion

{REPORT_OUTPUT_CONTRACT}

For native review, write only the assigned report and return its path, available session metadata, and incomplete coverage. For dispatched review, return the report as your final answer. The runner saves it outside the candidate worktree.
```

### 4. Verify Findings Before Acting

Run this as soon as a report arrives, before any finding is presented to the author. This splits into two checks: one compares text, and one needs judgement.

*Does the cited text exist where the finding says?* Run `quote_check.py` against the report, with `--root` set to a checkout at the reviewed state: the commit itself for an immutable candidate, or the working tree as it stood before any fix began for an uncommitted review. `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it.

A dispatched review writes its report to a file outside the repository. Point `--report` at that file:

```bash
python3 "$SKILL_DIR/scripts/quote_check.py" --root <reviewed tree> --report <report file>
```

Native and dispatched review use the saved report with the same `--report` command. Do not copy report text into a shell command.

Read every finding's verdict from the JSON output, whatever the exit code, except exit 2 or exit 3. Exit code 0 can still include `present-elsewhere` findings, so the exit code alone never substitutes for reading the JSON. Exit code 1 means at least one finding is `absent`, `file-missing`, or `unparseable`. Exit code 2 is a usage error. Fix the command and rerun it. Exit code 3 means the script parsed no finding out of the report. The report can still hold real findings in a format the script does not recognize, so on exit 3, check every finding in the report by hand.

Drop only the findings the script's JSON marks `absent` or `file-missing`. These never reach the author as findings. The summary in step 5 notes their count. For a finding marked `present`, skip re-comparing the quote against the file and move straight to the judgement check below. Give a finding marked `present-elsewhere` or `unparseable` the full check: compare the quote against the file yourself, then judge it. Give the same full check to any finding the JSON does not list at all, for example because the report mixed formats. Never drop an `unparseable` finding on the script's verdict alone, since a quote the script failed to pair or place can still name a real problem.

*Does the cited text mean what the finding says?* This is the half that needs judgement, and it is where the remaining errors live. A finding can quote a real line perfectly and draw a conclusion the line does not support. Read the surrounding code, not just the quoted fragment, before acting on any finding whose quote checked out.

### 5. Process Review Results

After step 4 verifies the findings:

When recording, mark an accepted finding that contradicts a factual brief claim with `disputes_brief: true` in the triage. Keep every reviewer's report and its final call in the run folder until the record is written.

**Context management:** Native completion carries a report path and metadata. Read each saved report once for verification and triage. A full report read still enters the coordinator's context. Present one concise summary of surviving findings and the verdict. Note dropped absent or file-missing findings only by count. Later briefs carry verified findings, not repeated full reports or raw logs. Refer to findings by number in follow-up discussion.

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

If the author wants to commit after approval, stage only the files related to the reviewed changes. Do not use `git add -A` as that can stage unrelated files.

### 6. Record and delete the review artifacts

Native and dispatched reviews keep their prompt and raw report outside the candidate worktree. A dispatched review also keeps its worker log there. The calling workflow can retain these artifacts for recovery or review evidence.

When a caller selects a comparison recorder, honor its supplied run folder and input-retention requirement.
Keep the caller's selected rules and frozen versions. Save each reviewer's exact prompt when it differs from the round prompt.
Keep prompts, rule snapshots, raw reports, quotation results, and every finding disposition until the caller records them.
Return their paths and launch metadata to the caller. Do not launch another review for recording.
This retention request does not opt into this runner's separate machine-readable review record.

When record opt-in applies to an immutable candidate, write the record with `scripts/write_record.py` after triage and corrections. Omit `--out` for the default location, or pass `--out <record file>` for an explicit path. Keep the run directory when recording fails, and report the error and its path so the caller can retry. After successful publication, follow [../references/artifact-closure.md](../references/artifact-closure.md) before retiring temporary inputs. Adopt only this run's owned outputs. Preserve the record and canonical reports. Settle terminal raw logs independently of unresolved findings, while preserving named readers. Apply existing cleanup authority, update the generated resource view, and run the checker. Use the reference's standalone `--resource-view` route when the review has no plan. A failure preserves retry inputs. Ordinary reviews retain their existing record opt-in.

When caller retention also applies, this runner's publication never releases the caller's inputs.
Return cleanup responsibility to the caller until its selected recorder succeeds.

Without record opt-in or caller-required retention, delete the run directory once the findings are verified and every accepted correction is committed.
When the caller owns the directory, return cleanup responsibility to that caller.

```bash
rm -rf <run parent>/<run name>
```

Keep the directory while a finding is open, recording or delivery has failed, or the caller or author requires retention. Give every run its own directory under one parent, so the removal is one command. Say in the summary that the artifacts are gone, so nobody looks for a path that no longer resolves.
