---
name: draft-pr-description
description: "Draft a pull request title and body from current changes WITHOUT opening or editing a PR. Use when: draft PR description, write PR body, prep PR description, draft PR don't open, update PR description (revise existing without applying)."
argument-hint: "[optional: 'update' to revise an existing PR, or a base ref like 'main']"
---

# Draft PR Description

Produce a pull request title and body that matches the repository's conventions. Output only. Do not create, edit, push, or comment on anything.

## Hard Rules (Do NOT)

- Do NOT run `gh pr create`, `gh pr edit`, `gh pr comment`, or any mutating `gh` command.
- Do NOT run `git commit`, `git push`, `git tag`, or modify refs.
- Do NOT write the draft to a file unless the user explicitly asks.
- Output the draft inline in the chat in a fenced block.

Read-only commands are fine: `git diff`, `git log`, `gh pr view`, `gh pr list`, `gh repo view`.

## Voice

Distilled from how the user rewrites AI-drafted PR bodies:

- Open with a short casual first-person lede ("Just putting this up because...", "Follow-ups from #X") and cross-links to related PRs, issues, or incidents ("Supersedes #X because...").
- Drop sections that would be empty or `N/A` on lightweight PRs; keep the testing scaffolding on substantive ones.
- Testing sections state only what was actually run and observed. Never present simulated or hypothetical runs as results.
- Cut rationale essays and bold feature-breakdown walls; keep repro steps, file:line references, and real links. Fluff dies, substance stays.
- Apply the `rewrite` skill's Tier 1 banned vocabulary.

## When to Use

- User asks to "draft a PR description", "write the PR body", "prep a PR description", etc.
- User asks to "update" or "revise" an existing PR description without applying the change.

## Procedure

### 1. Determine Scope

Pick the diff range, in this order:

1. If the user names a base ref, use `git diff <base>...HEAD`.
2. If on a feature branch with an upstream, use `git diff $(git merge-base @{u} HEAD)...HEAD`. If no upstream, try `origin/HEAD` or the repo's default branch from `gh repo view --json defaultBranchRef`.
3. If detached or on the default branch, fall back to `git diff` (uncommitted) or the latest commit.

Also collect:

- `git log --oneline <base>..HEAD` (commit subjects only, for context and style detection, NOT to be committed or amended).
- Changed file list and a summary of additions/deletions.

### 2. Detect Repo Conventions

Check, in parallel where possible:

- PR template: `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, or any file under `.github/PULL_REQUEST_TEMPLATE/`. If multiple templates exist, pick the one whose name best matches the change (e.g., `bug_fix.md` for a fix) or ask.
- Contributing guide: `CONTRIBUTING.md`, `CONTRIBUTING`, or `docs/contributing*`. Skim for PR description requirements.
- Recent merged PRs (only if `gh` is authenticated): `gh pr list --state merged --limit 5 --json number,title,body` then inspect a couple to learn tone, section headings, and length.
- Title style from commit subjects (Conventional Commits, gitmoji, ticket prefix, plain imperative). Mirror whichever dominates; if mixed, prefer the template's example or plain imperative.

### 3. Draft the Title

- Imperative mood, present tense, no trailing period.
- Apply the detected style (Conventional Commits prefix, gitmoji, ticket prefix) only if the repo uses it.
- Derive from the diff's primary intent. Fall back to the latest commit subject if the diff is small and the subject is already good. Branch names are a last resort.

### 4. Draft the Body

- If a PR template exists, fill it out section by section. Keep a heading even if its content is short; per the Voice rules, drop sections that would be empty or `N/A` on lightweight PRs.
- If no template, use a minimal structure:
  - **Summary**: 1 to 3 sentences on what changed and why.
  - **Changes**: bulleted list grouped by area or file when useful.
  - **Testing**: how it was verified, or "Not tested" if unknown. Do not fabricate test runs.
- Match the tone and length of recent merged PRs.
- Reference issues only if found in commit messages or branch name (e.g., `Closes #123`). Do not invent issue numbers.
- Do not include secrets, tokens, file contents from `.env`, or large diffs.
- Do NOT hard-wrap prose. Write each paragraph and bullet as a single line and let it soft-wrap; GitHub renders manual line breaks literally, so hard-wrapping produces ragged text. Only break lines where markdown requires it (list items, table rows, fenced/indented code blocks).

### 5. Update Mode (if requested)

If the user asked to update or revise an existing PR:

1. Identify the PR via `gh pr view --json number,title,body,headRefName` (current branch) or a number the user provided.
2. Read the current body.
3. Produce the revised title and body.
4. Show a brief diff or summary of what changed versus the original.
5. Still do NOT run `gh pr edit`. Tell the user the exact command they can run, e.g.:
   ```
   gh pr edit <N> --title "..." --body-file -
   ```

### 6. Output Format

Reply with:

1. A one-line note on the scope (base ref and number of commits/files).
2. The title in a fenced block labeled `title`.
3. The body in a fenced block labeled `markdown`.
4. A short footer noting any assumptions (e.g., "Used default branch `main` as base; no PR template found.").

If the diff is very large, summarize by directory or subsystem rather than listing every file. If intent is unclear, ask one focused question before drafting.
