# Cleaning a plan folder before archive

The plans `.gitignore` hides most transient files, so `git status` does not show them. `git mv` carries them into the archive unseen, and they stay there as bulk that nobody reviews. Clean the folder before the move, and list the hidden files first:

```bash
git -C .claude/plans status --short --ignored -uall -- <slug>
```

Sort each `!!` file into the first class that fits. Check each claim before you delete a file.

1. **Copy of a tracked file.** Delete it after `cmp` shows that it matches the tracked file byte for byte.
2. **Rebuildable from Git.** This class holds a diff or a file list between commits that a branch, a tag or a retained ref keeps. Rebuild it from Git and `cmp` the result against the file. Delete the file only when they match, and record the rebuild command in `transient.md`.
3. **Agent event stream.** This class holds a Codex `--json` stream or a copy of a Claude Code transcript. Command outputs usually fill most of it, and the harness keeps the same outputs in its own rollout or transcript. Check that every long output appears in that file. Then replace each output with `<omitted N bytes, full output in <that file>>` and keep every other field. Name each file's path in `transient.md`. That file lives on one machine, so `transient.md` is the only pointer to the full outputs.
4. **Small leftover.** Logs, markers, error files and the stripped streams go into one archive. Run this from inside the folder that holds them, because GNU tar reads a `C:/` path as a remote host:

   ```bash
   tar -cf - <file>... | xz -9e > transient.tar.xz && xz -t transient.tar.xz && tar -tJf transient.tar.xz
   ```

   Check that the listing names every file, then delete the originals.
5. **Durable record.** Reports, prompts, triage and cost files stay as plain files. Track them with a `.gitignore` in the run folder, such as:

   ```text
   !*.json
   !*.txt
   !transient.tar.xz
   ```

   Then `git add` the files it allows.

Keep a file that fits no class. Track it with a narrow allowlist rule when it is durable, or ask the author.

Write `transient.md` beside the archive. It names what the archive holds, what each stripped value pointed to, and the command that rebuilds each removed file. Then run the status command again. It must list no `!!` file and no `??` file under the plan.

If a permission check refuses a deletion, stop before the move. Ask the author once, with the file list and its total size.

Cleanup can also follow an archive commit. When other sessions committed after that commit, put the cleanup in a new commit, because an amend rewrites their commits.
