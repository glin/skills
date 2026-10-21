# Embedded private plans repository

Use this mode when plan history should be versioned without appearing in the enclosing project's Git history. The enclosing project keeps `.claude/plans/` ignored, while `.claude/plans/.git` owns an independent repository. Do not add a submodule entry or `.gitmodules`, because either would publish the existence and location of the private plans repository in the enclosing project.

## Bootstrap

Follow these steps in order. `<skill>` is the directory that holds this skill's SKILL.md.

1. Inventory `.claude/plans/` by file count, total bytes, extension, and largest files. Locate registered Git worktrees, dependency trees, copied source trees, raw transcripts, traces, logs, executables, and large media. Remove only artifacts proven stale. Keep active evidence on disk but exclude it from Git.
2. Add `.claude/plans/` to the enclosing project's `.gitignore`, so plan files never enter its history.
3. Run `git -C .claude/plans init` before any other `git -C .claude/plans` command. Before `init`, a `config` command there writes into the enclosing repository's config.
4. Create `.claude/plans/CLAUDE.md` and `.claude/plans/AGENTS.md` with the same repository rules, so Claude Code and Codex receive identical instructions. Require Conventional Commits and the exact `Agent:`, `Agent-Session:`, and optional `Subagents:` trailers described by the main skill. Require one contiguous trailer block with no blank lines between trailer lines, so Git recognizes every field.
5. Copy [commit-msg](commit-msg) and [pre-commit](pre-commit) from `<skill>/references/` to `.claude/plans/.githooks/`. Do not arm them until step 10.
6. Name both hooks and the `core.hooksPath` command in both instruction files, with these lines:

   ```text
   A `commit-msg` hook in `.githooks/commit-msg` rejects a commit whose provenance block is missing, split by a blank line, or left as `unknown`.
   A `pre-commit` hook in `.githooks/pre-commit` rejects commits from the shared index, because other sessions stage files there. Use the plan-files private commit helper for complete owned files. Use the explicit-hunk workflow for mixed files or selected staged differences.
   If `git config --get core.hooksPath` reports nothing, run `git config core.hooksPath .githooks`.
   ```

7. Create the nested `.gitignore` from this start, and add exclusions under it:

   ```text
   *
   !*/
   !.gitignore
   !*.md
   !.githooks/commit-msg
   !.githooks/pre-commit
   ```

   Keep `!*/`. Without it, Git never looks inside a directory, so the hook rules match nothing and the hooks stay out of history.
8. Run `chmod +x .claude/plans/.githooks/*` and `git -C .claude/plans add .`. Then run `git -C .claude/plans update-index --chmod=+x .githooks/commit-msg .githooks/pre-commit`. Without the executable mode on disk and in history, Linux and macOS skip both hooks. Inspect every staged path and its size.
9. Write the message with current runtime provenance, and check it with the hook. Then make the first commit:

   ```bash
   sh .claude/plans/.githooks/commit-msg <message-file> && git -C .claude/plans commit -F <message-file>
   ```

   This is the one bare commit the repository allows. A commit by path takes each file mode from the working tree, and Windows reports no executable mode there. No other session uses the repository yet, so nothing else is staged.
10. Arm the hooks with `git -C .claude/plans config core.hooksPath .githooks`. Then run `git -C .claude/plans ls-tree HEAD .githooks/`. It must show mode `100755` for both hooks. Do not configure a remote unless the author asks for one.

The two hooks guard what CLAUDE.md and AGENTS.md cannot. Those files do not reach a session that edits a plan in a subdirectory and never reads the top of the repository.

- [commit-msg](commit-msg) rejects a message without its trailer block. It reads `git interpret-trailers --parse` rather than the raw message. So it rejects a block split by a blank line, and a missing, duplicated, `unknown` or non-UUID value.
- [pre-commit](pre-commit) rejects a commit that does not name its paths. Several sessions share one index, and a bare `git commit` takes every file another session staged. The hook passes `git commit -- <path>...`, a commit from a private index, a message-only amend, and the end of a merge, cherry-pick, revert or rebase.

A commit by path takes each file mode from the working tree. Windows reports no executable mode there, so a hook change by path keeps its existing mode. For complete owned files, use [the private commit helper](commit-owned.md) to set an explicit mode and preserve unrelated staged entries. The helper requires the current full branch ref, baseline commit, baseline entries, working-file SHA-256 values and a provenance message. Mixed ownership and staged differences require the explicit-hunk workflow in [the shared-file commit guide](git-parallel-sessions.md).

Test a hook change in a temporary repository, never in a plans repository. A test commit that a broken hook lets through takes the other sessions' staged files with it. Copy a hook change to `<skill>/references/` in the same sitting, so the next bootstrap installs it. Each hook contains `trap '' PIPE`. A reader such as `| head` can close the pipe early. Without the trap, the hook then dies before its exit, and Git for Windows reads the dead hook as a pass.

The nested `.gitignore` tracks durable Markdown, small source auxiliaries, and repository automation by default. It excludes dependency trees, worktree copies, workspaces, candidates, raw transcript formats, traces, logs, build products, and native executables. Add a narrow allowlist rule when another small supporting file is important enough to preserve. Treat a file larger than 1 MiB as large unless the owner explicitly decides it belongs in history. Do not install a commit hook for the size policy, because whether a large capture belongs in history is the owner's call and a hook would answer it for them. Before every commit, inspect every staged path and its blob size so an accidental raw capture does not enter history.

## Daily use

Run `git -C .claude/plans status --short` before a plan edit. Commit a new plan immediately, and commit each material revision before the task ends. A dirty outer repository does not show this repository's state, and unrelated dirty plan paths do not authorize a batch commit. Write one complete message file and use [the private commit helper](commit-owned.md) for complete owned plan files and durable supporting files. Selected staged differences and mixed ownership require the explicit-hunk workflow. Separate `-m` arguments create separate paragraphs and fail when they separate the trailers. Before the commit, parse the message with `git interpret-trailers --parse`. It must return one exact non-unknown `Agent:` value and one exact non-unknown `Agent-Session:` value. The `commit-msg` hook checks the same thing and refuses the commit, so a rejection there means the message never reached history and needs rewriting rather than amending. After the commit, `git -C .claude/plans status --porcelain -- <plan-directory>` must produce no output.

Keep large or transient evidence outside Git. A plan may link to it while active, but it must also summarize the conclusions needed after that evidence is removed. Before archive, delete those artifacts and repair stale links in the same commit.

Keep every handoff, mockup, probe, capture, review, report, and other plan-specific auxiliary inside the owning plan directory. Named subdirectories are appropriate for related groups. Never create global plan-artifact folders elsewhere under `.claude/`.
