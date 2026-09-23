---
name: clean-commits
description: Rewrite a branch into a few clean, logical commits before a pull request opens, so a reviewer sees the final change and not the path to it. Use when asked to squash, tidy, reorganize or clean up a branch's commits, or before opening a pull request on a branch that carries work-in-progress, fix-up or reverted commits.
---

# Clean commits

Rewrite the branch so each commit is one logical change that a reviewer can read alone. The final tree must not change.

## Plan the commits

- Read the branch's commit list and its full diff against the merge base.
- Group the final diff by purpose, for example the behavior change, a config change, docs and tests.
- Drop work-in-progress, fix-up and reverted commits. A change that was added and later removed leaves no trace, because history records the result and not the back and forth.
- Write each message for the final change: an imperative subject, and a body only for a reason that the diff cannot show.

## Rebuild

1. Make sure that the worktree is clean and belongs to this session. Record the old tip with `OLD=$(git rev-parse HEAD)`, and copy each file that changes in more than one commit to a temporary folder.
2. Run `git reset --soft <merge-base>`, then `git reset`. The working tree keeps the final state, and nothing is staged.
3. For each planned commit, put its files in that commit's state and stage them by path. When one file holds changes for two commits, write the intermediate version from the base file plus the first commit's changes, commit it, then restore the final copy for the next commit.
4. Check that `git diff $OLD HEAD` is empty and `git status` is clean. If the diff is not empty, stop, because the rewrite changed the code.

## Push

Force-push with a lease on the old tip. Never use a bare `--force`.

```bash
git push --force-with-lease=refs/heads/<branch>:$OLD <remote> HEAD:refs/heads/<branch>
```

Rewrite only a branch that nobody else builds on. After a pull request is open and reviewed, add new commits instead, because a force-push hides what changed since the last review.
