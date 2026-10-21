# Commit owned changes around shared files

The complete-file helper refuses mixed ownership and selected staging differences. Keep those refusals intact. A shared working tree contains other writers' files and staging.

## Establish ownership

Read the current branch, commit, working changes, and staged changes before editing. Identify the exact files and hunks this task owns. Recheck them immediately before committing. A matching path list does not establish hunk ownership.

Use an isolated worktree or checkout when another writer edits the same file. Coordinate access when project state prevents isolation. Stop on overlapping edits, unclear ownership, unexpected staging, or a changed baseline. Resolve the conflict with the affected writer before proceeding.

Never discard shared working files with checkout, stash, hard reset, or clean. Never clear another process's lock. Never restore an entire shared file to undo a temporary edit. Keep temporary checks in an isolated copy.

## Select owned hunks in isolation

1. Capture the source commit and read its committed file alongside the current working file. Inspect staged and unstaged differences separately.
2. Save a byte-preserving patch containing only owned changes. Preserve Git headers and complete hunks. Split mixed hunks only after checking every changed line and its surrounding context.
3. Create an isolated worktree on a separate branch at the captured commit. Leave the shared working files and index untouched.
4. Run `git apply --check <owned-patch>` in the isolated worktree. Apply the patch only after that check passes. Stop if context conflicts or ownership remains unclear.
5. Inspect the complete candidate diff. Compare it with the source working file. Every remaining difference must belong to another writer or an explicitly excluded change.
6. Check the candidate using the relevant tests and document checks. Checks against the shared working tree do not check the selected candidate.

Keep the patch and isolated checkout until the candidate is committed and its integration is settled. Record their exact locations in the plan's Runtime resources section.

## Commit and integrate

The isolated checkout must contain only the selected owned changes. For an embedded plans repository, use [the complete-file helper](commit-owned.md) with that checkout's baseline and complete candidate files. Preserve its explicit mode, provenance, locking, publication, and recovery requirements. The helper's candidate files are wholly owned in this isolated checkout.

Inspect the resulting commit's changed paths, content, modes, and provenance trailers. Keep the source shared index and working files unchanged. Do not publish a temporary-index commit directly onto the shared branch. That leaves its index inconsistent with the branch.

Coordinate integration after the affected writers settle their changes. Recheck the target baseline and ownership before integrating through the repository's normal workflow. Stop on conflicts or new foreign staging. Preserve the candidate branch until integration is confirmed. Do not claim the target contains the change while the candidate remains separate.

For a repository that cannot use isolation, arrange exclusive access before selecting or committing shared hunks. If exclusive access remains unavailable, preserve the owned patch and report the blocker. Do not bypass the complete-file helper's admission checks or invent a replacement transaction.
