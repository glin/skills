---
name: draft-pr-description
description: "Draft a pull request title and body from current changes WITHOUT opening or editing a PR. Use when: draft PR description, write PR body, prep PR description, draft PR don't open, update PR description (revise existing without applying)."
argument-hint: "[optional: 'update' to revise an existing PR, or a base ref like 'main']"
---

# Draft PR Description

Produce a pull request title and body that matches the repository's conventions. Output only. Do not create, edit, push, or comment on anything.

## Hard rules

- Do not run `gh pr create`, `gh pr edit`, `gh pr comment`, or any mutating `gh` command.
- Do not run `git commit`, `git push`, `git tag`, or modify refs.
- Do not write the draft to a file unless the author explicitly asks.
- Output the draft inline in the chat in a fenced block.

Read-only commands are fine: `git diff`, `git log`, `gh pr view`, `gh pr list`, `gh repo view`.

## Voice

Distilled from how the author rewrites AI-drafted PR bodies:

- For a fix, start with two plain bullets that a collaborator who missed the investigation can follow. **The bug:** what a user sees, in which component, and why it happened. **The fix:** what the change does about it. Put them at the top of the template's first section, such as Description. With no template, use them as the Summary. Never put them above the template. The report, evidence and details follow them.
- Leave out behavior the change does not touch. A line such as "X is unchanged" reads as a new problem. Name deferred work only as a "Connected to" issue link.
- After the two bullets, give the report behind the PR. For support fixes, name the affected setup. Preserve accurate customer terminology and exact configuration identifiers. Do not replace a specific report with abstract subsystem prose. Name the kind of report as the source, such as a support ticket, without customer names or private links. Then state every client or input type that the fix's code paths cover.
- Drop sections that would be empty or `N/A` on lightweight PRs; keep the testing scaffolding on substantive ones.
- Testing sections state only what was actually run and observed. Never present simulated or hypothetical runs as results. Leave a template checklist box unchecked rather than claiming an unrun step, annotating why (deferred to CI, not applicable).
- Describe the final logical change, never the iteration journey. When branch history is iteration noise (approaches added then reverted), suggest squashing before opening so description and history match.
- Cut rationale essays and bold feature-breakdown walls; keep repro steps, file:line references, and real links. Fluff dies, substance stays.
- Apply the `rewrite` skill's Rewrite mode rules and its Tier 1 banned vocabulary.

## When to Use

- The author asks to "draft a PR description", "write the PR body", "prep a PR description", etc.
- The author asks to "update" or "revise" an existing PR description without applying the change.

## Procedure

### 1. Determine Scope

Pick the diff range, in this order:

1. If the author names a base ref, use `git diff <base>...HEAD`.
2. If on a feature branch with an upstream, use `git diff $(git merge-base @{u} HEAD)...HEAD`. If no upstream, try `origin/HEAD` or the repo's default branch from `gh repo view --json defaultBranchRef`.
3. If detached or on the default branch, fall back to `git diff` (uncommitted) or the latest commit.

Also collect:

- `git log --oneline <base>..HEAD` (commit subjects only, for context and style detection, not to be committed or amended).
- Changed file list and a summary of additions/deletions.

### 2. Detect Repo Conventions

Check, in parallel where possible:

- PR template: `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, or any file under `.github/PULL_REQUEST_TEMPLATE/`. If multiple templates exist, pick the one whose name best matches the change (e.g., `bug_fix.md` for a fix) or ask.
- Contributing guide: `CONTRIBUTING.md`, `CONTRIBUTING`, or `docs/contributing*`. Skim for PR description requirements.
- Recent merged PRs (only if `gh` is authenticated): `gh pr list --state merged --limit 5 --json number,title,body` then inspect a couple to learn tone, section headings, and length.
- Title style from commit subjects (Conventional Commits, gitmoji, ticket prefix, plain imperative). Mirror whichever dominates; if mixed, prefer the template's example or plain imperative.

### 3. Draft the Title

- Imperative mood, present tense, no trailing period.
- Wrap code identifiers, paths, and endpoints in backticks (e.g. `--source`, `/config`); GitHub renders them as inline code.
- Apply the detected style (Conventional Commits prefix, gitmoji, ticket prefix) only if the repo uses it.
- Write the title as the change a user or collaborator will notice, in the product's own words. When the repo keeps a changelog, describe the change its entry describes, in imperative mood. Start a fix with "Fix" and the symptom. Start a feature with "Add" and the capability. A detected prefix style replaces these verbs. Make the component at fault the subject, not the client that reported the bug. A client name reads as that client's bug. When the product docs name the component two ways, use the name they use most. Keep internal field and function names out of the title. The body carries them.
- Do not reuse a commit subject that describes the mechanism. Use a branch name only when nothing else states the intent.

### 4. Draft the Body

- If a PR template exists, fill it out section by section. Keep a heading even if its content is short; per the Voice rules, drop sections that would be empty or `N/A` on lightweight PRs.
- If no template, use a minimal structure:
  - **Summary**: for a fix, the two Voice bullets. Otherwise 1 to 3 sentences on what changed and why.
  - **Changes**: bulleted list grouped by area or file when useful.
  - **Testing**: how it was verified, or "Not tested" if unknown. Do not fabricate test runs.
- Match the tone and length of recent merged PRs.
- Reference issues only if found in commit messages or branch name (e.g., `Closes #123`). Do not invent issue numbers.
- Do not include secrets, tokens, file contents from `.env`, or large diffs.
- Treat user-provided issue, incident, or support wording as the source for the problem statement. Use the diff to describe the correction and tests. Widen the problem statement only to the scope that the fix's code paths cover.
- Do not hard-wrap prose. Write each paragraph and bullet as a single line and let it soft-wrap; GitHub renders manual line breaks literally, so hard-wrapping produces ragged text. Only break lines where markdown requires it (list items, table rows, fenced/indented code blocks).

### 5. Update Mode (if requested)

If the author asked to update or revise an existing PR:

1. Identify the PR via `gh pr view --json number,title,body,headRefName` (current branch) or a number the author provided.
2. Read the current body.
3. Produce the revised title and body.
4. Show a brief diff or summary of what changed versus the original.
5. Still do not run `gh pr edit`. Tell the author the exact command they can run, e.g.:
   ```
   gh pr edit <N> --title "..." --body-file -
   ```
   If the title contains backticks, single-quote the `--title` value (there is no `--title-file`), else the shell strips or executes them.

### 6. Output Format

Reply with:

1. A one-line note on the scope (base ref and number of commits/files).
2. The title in a fenced block labeled `title`.
3. The body in a fenced block labeled `markdown`.
4. A short footer noting any assumptions (e.g., "Used default branch `main` as base; no PR template found.").

If the diff is very large, summarize by directory or subsystem rather than listing every file. If intent is unclear, ask one focused question before drafting.
