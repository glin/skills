---
name: plan-files
description: "The convention for written plan and retrospective files that outlive a session: where they live, their templates, runtime-resource records, status values, and archival rules. Use when: write a plan, save this plan, write a retrospective, record a regression, where do plans go, start or update a plan file, archive that plan, which plans are still open. Covers durable planning and retrospective artifacts. For phase design and cross-session handoffs, use phased-plan."
---

# Plan files

A plan written only into a chat is gone at the end of the session. A plan file is the handoff medium: a fresh session with no conversational context should be able to read one and continue the work.

## Creation and commit protocol

A local plan is a versioned artifact when its plan directory has an embedded repository. Its initial plan file is material work and must be committed in the same task that creates it. A later material revision must also be committed before the task ends. Do not leave a new plan or revision in the plan repository for a later session to discover.

Before writing a local plan, identify the repository that owns it. Check for `.claude/plans/.git` first. When it exists, run every plan-status, staging, commit, and verification command with `git -C .claude/plans`. The enclosing repository's `git status` does not report nested-repository changes. Existing dirty entries in the plan repository belong to other work unless this task created them. Stage and commit only the plan directory and durable files that this task owns.

For complete, explicitly owned plan files, use [the private commit helper](references/commit-owned.md). Its recipe covers admission, publication, explicit modes and retained recovery content. Mixed ownership and selected staged differences require the explicit-hunk workflow in [the shared-file commit guide](references/git-parallel-sessions.md).

Read the current runtime's model identifier, effort, session identifier, and harness before writing the header or commit message. For Codex, inspect the current rollout or session metadata when the chat prompt does not repeat those values. The effort is set per session, and both harnesses record it as an `"effort"` field: Claude Code in the session transcript under `~/.claude/projects`, and Codex in the rollout's `turn_context` entries. Do not replace an available exact value with a family name, a provider name, or `unknown`. For a plan written locally, unavailable provenance is a blocker: stop and report it rather than creating a plan with unknown provenance. Use `unknown` only when importing an external plan whose original session record is unavailable.

After creating or materially revising a plan in an embedded repository, write one complete commit message to a file. Use the helper for complete owned files, including new files. Keep mixed files and staged differences in the explicit-hunk workflow. Never split the subject and trailers across separate `-m` arguments. Each argument creates a paragraph, and Git parses only the final trailer paragraph. Use a Conventional Commit subject and one contiguous provenance block with exact values from the current runtime:

```text
docs(plan): <describe the plan change>

Agent: <exact-model-id>[<context-if-reported>] (effort <exact-effort>, <harness>)
Agent-Session: <exact-session-id>
```

Validate the message file before committing, then validate the committed message after committing. `git interpret-trailers --parse` must return exactly one non-unknown `Agent:` trailer and exactly one non-unknown `Agent-Session:` trailer. A message with either trailer missing, unknown local provenance, or a blank line between the two trailers fails validation. The first command below must produce no output. The parsed output from the second command must contain both required trailers with their exact values.

```bash
git -C .claude/plans status --porcelain -- <plan-directory>
git -C .claude/plans log -1 --format=%B | git interpret-trailers --parse
```

## Where they live

```
.claude/plans/
├── <YYYY>/<MM>/YYYY-MM-DD-<slug>/       active work
│   ├── plan.md                          the plan itself
│   └── ...                              research logs, handoffs, plan-specific material
├── reference/
│   └── <topic>.md                       findings that outlive any one plan
├── archive/
│   └── <YYYY>/<MM>/YYYY-MM-DD-<slug>/   settled, kept for reference
└── retrospectives/
    └── <YYYY>/<MM>/YYYY-MM-DD-<slug>/   durable record for process analysis
```

**Old paths.** A plan folder keeps its name when it moves into a month folder or into `archive/`. A path written before the nested layout, `.claude/plans/<YYYY-MM-DD-slug>/...` or `.claude/plans/archive/<YYYY-MM-DD-slug>/...`, names the nested folder with the same name. An archive move can happen while another session still holds the plan path. Before you write to a plan path that you recorded earlier, check that it exists. When it does not, run `python3 <skill>/scripts/plan_paths.py resolve .claude/plans <old path>`, where `<skill>` is the folder that holds this SKILL.md. It prints the current path, active or archived. Write to that path. Never recreate the old one. When `resolve` finds no match, ask the owner.

`YYYY-MM-DD` is the date the plan started, and never changes afterwards. `<slug>` is a short kebab-case topic, for example `cache-invalidation` or `oauth-refresh`. Everything related to one plan shares its folder. The month folders come from the start date in the folder name, so a plan never moves between months.

Retrospectives live under `.claude/plans/retrospectives/<YYYY>/<MM>/YYYY-MM-DD-<slug>/retrospective.md`. The date records when the retrospective started and follows the same immutable date rule.

Every plan uses its own `<YYYY>/<MM>/YYYY-MM-DD-<slug>/` directory, even when it currently has no auxiliary files. This keeps the top level uniform and leaves an unambiguous home for later evidence.

Every plan-specific auxiliary belongs inside the owning plan directory. This includes handoffs, mockups, probes, captures, reviews, reports, research, reference material, and generated evidence. Use a descriptive subdirectory such as `handoffs/`, `mockups/`, or `probes/` when a plan has a group of related files. Never create a global `.claude/HANDOFF-*`, `.claude/mockups/`, `.claude/probes/`, or equivalent loose plan-artifact collection.

A finding that more than one plan relies on, or that must stay current after its plan is archived, goes in `.claude/plans/reference/<topic>.md` instead. Examples are a vendor's undocumented behavior, a measured rate, and a survey of other products. Before you research a topic, check `reference/` for an existing file, and update it rather than start a new one. A reference file opens with a `Last checked:` line and a table that links each plan holding its evidence. Each of those plans links back to the reference file and keeps only its own method, runs and decisions. Reference files hold private working knowledge. Facts for the project's readers belong in its public docs.

When `.claude/plans/.git` exists, the plans directory is an independent embedded repository even if the enclosing project ignores it. Read [references/embedded-repo.md](references/embedded-repo.md) before bootstrapping one or committing in one.

## The file

```markdown
# Plan: <Title>

**Date:** YYYY-MM-DD
**Status:** Not started
**Priority:** P0 | P1 | P2
**Complexity:** Low | Medium | High
**Authored:** <exact runtime model id> (<exact runtime effort>)
**Author session:** <exact runtime session id>
**Baseline:** <full commit SHA>[ + dirty]

<One paragraph: what the change does and why.>

**Target file(s):** <paths>

---

## Background
<Context, motivation, technical details a reader will not have.>

## Decisions
<Design choices and the reason each one won.>

## Steps
1. <Actionable, with file paths and symbol names>

## Runtime resources
None yet.

## Verification
<Commands, tests, and manual checks that prove it worked.>
```

## Owner requirements

Keep the owner's relevant message excerpts, source references, later corrections, and restrictions in the plan. Label summaries and preserve their sources. Separate owner requirements, explicitly approved agent proposals, and unapproved agent inferences. Identify the approval for each approved proposal.

A plan or reviewer suggestion alone grants no authority for a visible control, dialog, panel, or interaction. Reviewers compare every proposed surface with the owner's request. If an inferred requirement needs an unauthorized addition, pause that addition and request approval. Continue independent approved work.

## Rules

- **Write the file.** A plan presented only in chat does not exist. This is the whole point of the convention.
- **Keep it self-contained.** Someone reading only the file should be able to implement it. Name concrete paths, symbols, and line numbers.
- **Check an open question before asking it.** Before listing an open question for the owner, check it against the owner's stated need and against the code. When either already answers it, record that answer as a decision and name its source instead of listing it as open. Ask the owner only what neither answers.
- **The file is the source of truth, not the conversation.** Update it whenever the plan changes, including when a decision is reversed.
- **Use semantic line breaks.** Put one sentence on each source line with no fixed column limit. Do not reflow code blocks or tables. This keeps search matches intact without turning each paragraph into one very long editing unit.
- **Set `Priority:` only when the owner has given one.** Use `P0`, `P1` or `P2`, and omit the line otherwise. Never infer a priority from the plan's own prose. A roadmap that ranks plans reads this field, so the plan owns the label and the roadmap mirrors it.
- **Keep `Status:` current:** `Not started`, `In progress`, `Implemented`, `Superseded`, `Closed`, `Won't fix`, or `Archived`.
- **A heading that carries a status is part of that status.** A section titled `## Open: ...` whose fix landed two hundred lines below still reads as open to anyone who stops at the heading. Update the heading where the state changes, not only the file header.
- **Record exact model provenance.** Copy the runtime-reported model identifier and effort setting into `Authored:`. Use identifiers such as `gpt-5.6-sol`, `gpt-5.6-terra`, `claude-opus-5`, and `claude-sonnet-5`. Do not shorten them to a family or product label such as `GPT-5`, `GPT-5.6`, `Codex`, `Opus 5`, or `Sonnet 5`. Do not put a context mode such as `[1m]` in the model identifier. Record the exact reported effort token, including `auto` when that is all the runtime reports. Never convert `default` or a marketing alias from memory. Recover the values from the current session or rollout metadata before writing a local plan. Use `unknown` only for an imported plan whose original runtime record is unavailable.
- **Record the writing session.** Copy the runtime session identifier into `Author session:`. For Codex, use the rollout/session UUID. For Claude Code, use the session UUID. A plan written locally must have its exact session identifier. Write `unknown` only when the plan arrived from another machine and its transcript is unavailable. The session identifier is the durable join back to model, effort, runtime, provider, token, and cost records, so do not substitute a transcript filename or a conversation title.
- **Record the code baseline.** Put the full Git commit SHA that the plan was written against in `Baseline:`. Append ` + dirty` when tracked or untracked workspace changes affected the plan. Use `unknown` outside a Git worktree. Update the baseline only when the plan is deliberately rebased onto a newer code state.
- **Normalize an existing provenance field instead of adding another one.** Legacy plans may use either `Authored:` or `**Authored:**`. Search for both forms before inserting metadata, then leave exactly one canonical `**Authored:**` field.
- **Preserve the writing model.** `Authored:` and `Author session:` identify the model that created the plan and never change. In an unversioned plan, a later model may add one concise `Revised:` field for a material rewrite and `Reviewed:` for a review without a rewrite. Do not add either field for status, typo, or formatting-only edits.
- **Use Git history for later contributors when plans are versioned.** Do not accumulate `Revised:` or model changelog entries in the plan. Every commit that creates or materially changes a plan explains the contribution in its subject and body, then ends with one contiguous trailer block containing `Agent: <exact-model-id>[<context-if-reported>] (effort <exact-effort>, <harness>)` and `Agent-Session: <session-id>`. Use the private commit helper for complete owned plan files. Keep mixed files and selected staged differences in the explicit-hunk workflow. Do not put blank lines between trailer lines, because Git otherwise parses only the final paragraph as trailers. Parse the message with `git interpret-trailers --parse` before and after the commit. Add `Subagents:` to the same block only when subagents contributed. Omit the bracketed context when the runtime does not report it. A local contributor must recover all values from runtime or rollout metadata. The trailers name the runtime that makes the commit, so they are always known, even when that commit imports another author's plan. Record an unknown original author in the plan's `Authored:` and `Author session:` fields instead.
- **Detect the repository boundary before deciding how to commit.** First check for `.claude/plans/.git`. If it exists, commit from that repository with `git -C .claude/plans` even when the enclosing project ignores the directory. Otherwise run `git check-ignore -v .claude/plans` in the project. If nothing matches, commit each plan alongside the change it describes.
- **Never force a plan past the enclosing project's `.gitignore`.** When no embedded plans repository exists, a project that ignores `.claude/plans/` has decided that plans are working notes. Do not use `git add -f` or edit `.gitignore` to bypass that decision. Ask before changing the policy.
- **Refresh the plan before a review pass.** A stale plan makes a reviewer flag intentional changes as deviations.
- **Co-locate auxiliary files.** Put every plan-specific handoff, mockup, probe, capture, review, report, and research file inside the owning plan directory. A global auxiliary folder under `.claude/` loses ownership and prevents one complete archive move.

## Runtime resources

Every plan must contain a `Runtime resources` section. Write `None yet.` until the plan creates, adopts, or schedules its first plan-scoped resource.

Record every resource that a later session must identify or clean up and cannot discover safely from tracked project files alone. This includes Git worktrees and their branches, scratch and temporary files or directories, external artifacts, dependency links, detached jobs or sessions, and background processes or services started for the plan.

Use this table when the first resource appears:

| Type | Path or identifier | Purpose | Owner | Cleanup condition | Status |
| --- | --- | --- | --- | --- | --- |
| Worktree | `<absolute-path>` | Phase 1 implementation | Phase 1 | After the reviewed commit lands | Active |

Record a planned resource before creating it when its location or identifier is known. Otherwise record it immediately after the creating tool returns. Use absolute paths and exact branch, ref, job, session, process, or service identifiers. Never use an environment variable, glob, or description that a cleanup session must rediscover.

Use only `Planned`, `Active`, `Removed`, or `Retained` in the Status column. Update the row in the same task that creates, changes, or cleans the resource. A `Retained` row must name why it remains, who owns it next, and the condition that permits cleanup. Never retain a branch whose change already landed. A change landed when main contains the branch tip, or when `git cherry main <branch>` marks every commit `-`, or when main holds a commit with the same subject and changes for each remaining one. Delete the branch with `git branch -D` and record the deleted tip hash and the landed hash in the row. A `Retained` row whose next owner is the author is a decision for the author. The task that writes the row asks it in its closing message, as a Waiting on you item that opens with **Approve or reject** deleting it.

Do not set the plan to `Implemented`, `Closed`, or `Archived` while a resource remains `Planned` or `Active`. Resource cleanup is open plan work even when the code already landed.

## Before a task ends

For explicitly adopted runs, follow [references/closure.md](references/closure.md) at resource settlement. Check the authoritative disposition manifest and generated Runtime resources view with `scripts/large-files.py --manifest <manifest> --plan <plan>`. For a run without a plan, replace `--plan <plan>` with `--resource-view <view>` as the reference describes. Add `--owner <child>` for an independently completed child. The shared checker must pass before claiming completion. Keep legacy table authority and the positional scan below until explicit migration.

Before a task that wrote into a plan folder ends, run [scripts/large-files.py](scripts/large-files.py) under `python3` with that folder as its argument. It lists every file over 2 MB and exits 1 when it lists any. It never enters a symbolic link or a junction. It exits 2 when it cannot read a folder, and then the check has not run. Handle each file under step 1 of Archiving a plan: summarize its finding in the plan, then pack or delete it as [references/archive-cleanup.md](references/archive-cleanup.md) describes. A plans repository that tracks only Markdown never shows these files in `git status`, so the folder grows unseen without this check.

## Retrospectives

Use a retrospective when the owner asks to preserve a regression, agent failure, recovery, or process lesson for later analysis. Read [references/retrospectives.md](references/retrospectives.md) before writing or moving one. A retrospective records what happened and what will prevent recurrence. Keep open implementation work in a linked active plan instead of turning the retrospective into a second plan.

## Active versus archived

The month folders under `.claude/plans/` hold only plans with open, actionable work. A fresh session runs the command below to see what is in flight. Anything settled left sitting there costs every future session a read.

```bash
ls -d .claude/plans/[0-9][0-9][0-9][0-9]-*/ .claude/plans/[0-9][0-9][0-9][0-9]/[0-9][0-9]/*/
```

The command lists both the old and the nested layouts until every repository has migrated. Keeping the split current is what makes the scan worth running.

Archive once a plan has no open actionable work, meaning its `Status:` is `Implemented` (confirmed where confirmation was required), `Superseded`, `Closed`, `Won't fix`, or `Archived`. Keep it active while it is `Not started`, or `In progress` with steps remaining.

### Archiving a plan

1. Resolve every `Planned` or `Active` runtime-resource row, then remove transient artifacts such as dependency trees, copied worktrees, raw transcripts, JSONL or NDJSON streams, browser traces, logs, build outputs, and reproducible generated evidence. Follow [references/archive-cleanup.md](references/archive-cleanup.md) for files Git ignores, such as a run folder's streams, logs and diffs. It packs stripped streams and logs instead of deleting them. Mark each removed resource `Removed` in the same edit. Mark an intentional survivor `Retained` with its reason, next owner, and cleanup condition. Summarize durable findings in the plan. Move each finding that outlives the plan into the matching `reference/` file, which keeps its link to the archived plan. Do not truncate evidence because a partial file can be mistaken for complete evidence. A value replaced by a marker that names its size and the full copy's location is not truncation. Keep an archived image only when it carries unique visual evidence that prose cannot preserve. Compress retained images losslessly when practical, and use a lossy format only when text readability, transparency, and pixel-exact comparison do not matter.
2. Confirm `git -C .claude/plans status --short --ignored -uall -- <YYYY>/<MM>/<slug>` shows no `??` or `!!` file inside the folder. Preview the move with `python3 <skill>/scripts/plan_paths.py move .claude/plans <YYYY>/<MM>/<slug> archive/<YYYY>/<MM>/<slug> --dry-run`. `<skill>` is the folder that holds SKILL.md. An explicit archive or cleanup request authorizes the move when ownership is clear. Check the preflight, plan, runtime resources, and relevant session activity for another writer at the affected paths. Check the writing session through available session tools when local evidence leaves its state unclear. An unrelated agent process does not establish a conflict. Ask the owner only about a conflicting active writer or unresolved ownership. After checking that the affected paths are quiet, run the move with `--apply`, the commit identity and `--verified-quiet`:

   ```bash
   python3 <skill>/scripts/plan_paths.py move .claude/plans <YYYY>/<MM>/<slug> archive/<YYYY>/<MM>/<slug> --apply --agent '<model>[<context>] (effort <effort>, <harness>)' --session <session UUID> --verified-quiet
   ```

   `--agent` takes the `Agent` trailer value, and `--session` takes the session UUID. `--verified-quiet` records the executor's check for conflicting writers. `--owner-confirmed-quiet` remains valid when the owner supplied that confirmation. Neither flag bypasses staged paths, recent foreign commits at affected paths, open task runs in the moved plan, or unreadable run metadata. An agent command naming an absolute affected path also blocks the move. Resolve a refusal, then run the dry run again. The same checks and flags apply to `--rollback`. A recently changed file in a run folder never blocks the move. The move makes two commits: the first moves the folder, and the second rewrites every link to it. Where no repository tracks `.claude/plans/`, move the folder with a plain `mv` instead, and fix its links by hand.
3. Read the dry run's list of unresolved references and fix each one by hand.

Never delete plan history. Archiving is a move, and `git mv` reverses it if the plan reopens.

## Legacy plans

A plan written before this file fixed the exact status and complexity tokens keeps its original prose header. Nothing requires a sweep to rewrite it on its own. The trigger for normalizing one is its next edit: touching a plan for any reason is also the moment to bring its `Status:` and `Complexity:` lines to the exact tokens above, moving any explanation into the body instead, and to add its `Runtime resources` section. A scan for open work should treat a header that matches none of the exact tokens as a legacy plan outside the active set, not as an error to fix on sight.
