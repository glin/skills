# Plan review rules

[../SKILL.md](../SKILL.md) loads this file for a Markdown plan file. A plan review finds a plan's defects before an implementer pays for them. Record every review in the plan it reviewed, because a later audit decides from those records whether the review pays for itself.

This rule set adds a start step before launch, replaces step 5's findings table and question with its own triage, adds a pricing step, and replaces step 6 with a plan record and a commit.

## Contents

[Target](#target), [Start](#start), [Reviewers](#reviewers), [Template slots](#template-slots), [Quote check](#quote-check), [Triage](#triage), [Price](#price), [Record](#record), [Commit](#commit).

## Target

`/review [plan] <plan path> [phase]` names the target. A phase is a heading in the plan. A shorthand from the model table is a model argument, not a phase. Without a phase, review the first phase the plan does not mark landed. Review a plan with no phases whole.

A plan review runs no re-review. The triage corrects the plan instead.

Plans and reviewer suggestions grant no authority for visible additions.

## Start

Before launch, record the UTC time as the start, in the form `YYYY-MM-DDTHH:MM:SSZ`. Record this session's id from `$CLAUDE_CODE_SESSION_ID` or `$CODEX_SESSION_ID`. Name the target, the repository whose code the plan changes, and record its full HEAD SHA.

The reviewers read the code at that SHA. Leave the plan unedited until triage ends. With both fixed, the Transport rules treat the plan review as an immutable target.

## Reviewers

Without model arguments, launch two read-only reviewers in parallel with the same prompt and high effort: `opus` and `astra`. The current model names live in [../../references/models.md](../../references/models.md). Model arguments override this pair, as they do for a code review.

A plan review has no candidate worktree, so the plan's folder holds its prompt and reports. Write the filled prompt to `plan-review/prompt.md` in the plan's folder. Assign each reviewer the report destination `plan-review/<model>.md` in that folder. Each launch supplies its own destination, so both reviewers receive the same saved prompt. A dispatched reviewer runs with `--cwd <target> --add-dir <plan folder>` and logs to `<plan folder>/plan-review/<model>.log`.

## Template slots

- `{REVIEW_KIND}`: `plan`.
- `{REVIEW_TARGET}`: `the plan at <plan path>, <phase>, before anyone implements it`. For a plan with no phases, write `the whole plan` for `<phase>`.
- Omit the Change Summary and Changed Files sections.
- `{PLAN_AND_BRIEF_PATHS_OR_NONE}`: the plan path.
- `{ALLOWED_REPOSITORY_ROOTS}`: the target repository and the plan's folder.
- `{RULE_SET}`: the block below, with its placeholders filled.

```text
Do not edit files. Load no skill file.

<Relevant owner message excerpts, source references, later corrections, and restrictions. Separate approved agent proposals from unapproved inferences.>

Check every proposed visible control, dialog, panel, or interaction against that authority. A plan alone grants none. Pause an unauthorized inferred addition while independent approved work continues.

The code that the plan changes is the repository at <target path>, commit <full SHA>. Read that code. Treat every claim in the plan as a claim to check. A wrong premise is the most valuable thing you can report.

Check these nine things:
1. Every file, function, command and behavior that the plan names exists at that commit as the plan describes it.
2. One implementer can build the phase and pass its tests in one context. The phase does not bundle independent changes.
3. Every requirement has a check that a test or a reviewer can run.
4. The plan leaves no product decision open.
5. The plan needs nothing an agent cannot reach, such as a live screen capture, a device or a credential.
6. The plan names every risk to money, security, data, or a contract between components.
7. The plan asks for nothing that the repository's CLAUDE.md or AGENTS.md files forbid.
8. No two requirements conflict at the sizes, fonts and platforms that the plan names.
9. When the plan changes something that the code draws or computes in more than one place, the plan names every place.

Use at most 30 tool calls. Return one line per finding, most severe first:
<check number> | <plan line> "<exact plan quote>" | <file:line> "<exact code or instruction quote>" | <one sentence: what goes wrong>
Write none for the code quote only on checks 3, 4 and 8. After the last finding, name every check you did not finish as: coverage incomplete: <check numbers>
When you finish every check and find nothing, write the rule sources and then only: No findings.
```

## Quote check

Step 4 applies. `quote_check.py` reads no finding from this line format and exits 3, so step 4's manual check covers every finding. Check every quote in one shell call. Find the plan quote at its plan line, and the code quote at its line in `git show <target HEAD>:<path>`. Drop a finding whose quote is absent, and count the drops.

## Triage

This triage replaces step 5's findings table and its question to the author. Merge a finding that more than one reviewer made, and keep every name. Give every finding a verdict, accepted or declined, with a one-line reason. Give every accepted finding one class:

- `stop`: without the fix, the run stops or splits. A phase too large for one implementer, an open product decision, a need the harness cannot reach, and a premise that makes a step unbuildable all count.
- `round`: without the fix, a code reviewer flags the result, and a fix round follows.
- `note`: the fix helps, but it prevents neither of the above.

The class is the triager's prediction. The audit judges it against the run that follows.

Correct the plan for every accepted finding that needs no decision. Ask the author one question that carries every accepted product decision and every proposed phase split. Record the answer in the plan.

## Price

On Windows, run `python "$SKILLS_DIR/session-cost-audit/scripts/arc_cost.py" <session> --since <start>`. On macOS/Linux, run `python3 "$SKILLS_DIR/session-cost-audit/scripts/arc_cost.py" <session> --since <start>`. Choose one pricing end and pass it as `--until`. Save the Arc report. Take `combined.pricedCostUsd` and start an owned source set with `source.sessionId` and every `children[].sessionId`. Price each absent dispatched session through Arc with the same exact bounds. Add only an unowned parent or child `pricedCostUsd`, then assign that source to its report. Never add overlapping combined totals or runner-reported dollars. Save source ownership and bounds for continuation. Replace entries when the window changes. Disclose null full cost and incomplete coverage. Run the command once more if it fails. Write `unpriced` only when the retry also fails.

## Record

This record replaces step 6, and every plan review writes it. The runner's review record, with its `run.json`, `skill_version` and `disputes_brief` fields, does not apply to a plan review. Add the entry below to the plan, under one `## Plan review` heading. Keep each reviewer's report as `plan-review/<model>.md`, and delete the `.log` files.

```markdown
## Plan review

### <phase>, <YYYY-MM-DD>

- Target `<repository>` at `<full HEAD SHA>`. Cost <usd> USD: <model> <usd>, <model> <usd>, triage <usd>.
- Found <n> after the merge. Dropped <n> on a failed quote. Accepted <n>: `stop` <n>, `round` <n>, `note` <n>. Declined <n>.
- Hindsight: open.

| Check | Class | Found by | Finding | Verdict and action |
| --- | --- | --- | --- | --- |
| 2 | `stop` | both | The phase changes the store and the panel at once. | Accepted. Split into 3a and 3b. |
```

The audit replaces `open` on the Hindsight line with what the run that followed shows.

## Commit

Commit the plan and its `plan-review/` folder by path, in the repository that owns the plan, with the trailers that repository requires.

Report the counts, the cost, the plan corrections, and the question if there is one.
