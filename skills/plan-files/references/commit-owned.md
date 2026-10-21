# Complete owned plan commits

Use `scripts/commit_owned.py` for complete files that this task owns, including executable hook changes. It supports existing files and new files. It does not delete files, select hunks or change the plan-path migration journal.

Run from the plans repository root. Capture the full current branch ref and commit before preparing the request:

```bash
git symbolic-ref HEAD
git rev-parse HEAD
git ls-tree HEAD -- <path>
python3 -c 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' <path>
```

Write the complete message with the provenance block required by the main skill. Invoke the helper through its installed skill directory. `<skill>` names the directory containing `SKILL.md`:

```bash
python3 <skill>/scripts/commit_owned.py commit --repo . --ref <full-branch-ref> --baseline <full-commit> --message <message-file> --file <path> <working-sha256> <baseline-blob> <baseline-mode> <output-mode>
```

Repeat `--file` for each exact repository-relative path. Modes must be `100644` or `100755`. For a new file, use `absent absent` for its baseline blob and mode. A selected shared-index entry must equal its baseline entry. Resolve a staged difference or mixed ownership through the explicit-hunk workflow in [the shared-file commit guide](git-parallel-sessions.md).

The helper captures working bytes and hashes normalized Git blobs with each file's attributes. It builds the candidate from the admitted baseline. It runs the pre-commit, prepare-commit-msg and commit-msg hooks with a private index. Hook changes to the admitted message or index refuse publication. Git signs the commit when `commit.gpgSign` is true.

The shared index lock protects a fresh binary index snapshot. The helper preserves unrelated entries and prepares the replacement before publishing the branch with compare-and-swap. It holds that lock until index replacement completes. A normal full-index commit cannot consume stale selected entries in that interval.

Success prints the owned commit SHA and removes its recovery ref and scratch directory. Disposal requires the candidate's exact parent, message, changed paths and blobs. The current branch must still contain the owned commit and its expected owned blobs. Selected staging must also match. A later legitimate owned-file change requires explicit resolution and retains recovery content.

## Recovery

Initial branch, baseline, path, hash and provenance refusals create no scratch or receipt. Correct the request before retrying. Locked staging checks and foreign-lock refusals retain the resources already created. Later refusals report the exact `receipt.json` path under the repository's Git directory. The receipt identifies the branch, baseline, candidate, recovery ref, copied inputs, prepared index and lock ownership when available. Keep the directory and any recovery ref together. Never clear an index lock because a process exited.

After publication, a failed replacement retains the owned lock and prepared bytes. Retry the checked replacement with:

```bash
python3 <skill>/scripts/commit_owned.py recover <receipt-path> --action complete
```

To undo only an unchanged owned published tip, use:

```bash
python3 <skill>/scripts/commit_owned.py recover <receipt-path> --action rollback
```

Rollback requires the unchanged shared index and owned lock. It compare-and-swaps only the exact candidate back to its baseline, then releases the lock. An interrupted rollback resumes at baseline only with its recorded rollback intent and unchanged index and lock. Before publication, rollback releases a checked owned lock, including a partial write matching the recorded prepared bytes. It retains the candidate's recovery ref and scratch because no other ref proves that candidate remains reachable.

A moved tip blocks rollback. Completion permits a descendant tip only when the owned candidate remains reachable and the expected owned blobs survive. Interrupted disposal resumes after checking the candidate's parent, provenance, changed paths, current owned blobs and selected staging again. Missing members are accepted only during recorded disposal. A phase value alone grants no deletion authority. Complete receipt output must match a recognized receipt state. Partial or changed receipt output remains for explicit inspection. Changed selected staging, changed shared index, changed lock, unexpected members, directory links and permission refusals require explicit resolution. Recovery never rolls back a foreign commit or bypasses a refused replacement through another API. Rebuild a refused unpublished candidate from a fresh admitted baseline after resolving the conflict. The helper does not reparent a stale candidate or merge shared files.

## Discard an unpublished, unprepared transaction

Explicitly authorized disposal uses the exact current receipt SHA256:

```bash
python3 <skill>/scripts/commit_owned.py recover <receipt-path> --action discard --receipt-sha256 <sha256> --dry-run
python3 <skill>/scripts/commit_owned.py recover <receipt-path> --action discard --receipt-sha256 <sha256>
```

Discard requires a `building` receipt without a captured shared index, prepared index, or recorded lock ownership. It checks the repository, receipt, complete members, candidate parent, provenance, changed paths, blobs, and scoped recovery ref. Any local branch containing the candidate refuses disposal. It deletes only that recovery ref and the exact admitted directory members. It leaves shared staging, working files, and foreign locks untouched. A refusal or interrupted deletion retains remaining files for explicit inspection. Discard never resumes a partial deletion automatically.

## Why the helper stays private

The caller commits complete files in a shared embedded plans repository. The plan-path migration has its own journal and recovery contract. Sharing their transaction code would couple those contracts without another compatible producer. This helper therefore owns only this caller's temporary indexes, captured inputs and message.

A snapshot taken before lock acquisition can lose unrelated staging. Releasing the lock between branch publication and index replacement lets another commit restore stale owned entries. Binary writes preserve Git index bytes on Windows. Replacement preserves the existing index permissions on POSIX. Reachability alone cannot prove that a later commit retained the owned content. These checks permit disposal only for the current checked transaction. They do not control uncooperative ref writers or future changes.
