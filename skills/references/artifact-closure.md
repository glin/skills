# Owned artifact closure

The checker counts ignored files and aggregate small outputs. Per-file size limits miss many abandoned small logs. It reads without deleting, moving, changing permissions, or bypassing locks. Existing cleanup authority governs corrective actions.

Resolve the shared skills directory explicitly through the installed catalog. Set `CLOSURE_SHARED_ROOT` to that directory. Its `references/scripts/closure.py` is the single checker. A missing helper is an unmet prerequisite. Never walk `..` through a skill junction to locate it.

## Adoption and ownership

Legacy Runtime resources tables retain authority until explicit migration. Create one manifest for the run's outputs. Keep its control files and compressed inventories outside inventoried output roots. Register exact transaction scratch directories separately. Exclude Git core metadata, recovery copies, foreign transactions, and link targets. Run-root inventory skips `.git`, links, junctions, and nested Git checkouts. Exact scratch registration permits inventory inside `.git` without scanning siblings.

Each root and resource names its exact owner. A nested child scope names a different owner and excludes its outputs from the parent scope. The deepest scope determines each resource's owner. Pass `--owner` to check a child without inventorying its active parent. A finished review with unresolved findings still has terminal raw logs. Preserve its canonical report and named pending readers.

Missing run roots fail the check. Missing registered scratch requires a Removed resource with verified archive or deletion evidence. Unknown excluded links and nested checkouts block completion without entering them. Register an intentional exclusion in `exclusions` with its absolute `path`, exact `owner`, and specific settlement `evidence`. Git core metadata remains excluded without a resource disposition. Exact scratch registration rejects split-index backing files and surrounding worktree metadata. A linked worktree permits its exact owned `plan-commit-*` transaction directory.

The top-level UTF-8 manifest must remain below 1 MiB. Use version 1 and `adopted: true` only after checking ownership. Each resource requires a unique `id`, absolute `path`, exact `owner`, and `status`. Status uses `Planned`, `Active`, `Removed`, or `Retained`. Planned and Active resources block completion.

```json
{
  "version": 1,
  "adopted": true,
  "view_id": "review-run",
  "roots": [{"path": "/absolute/run/outputs", "owner": "review-run"}],
  "scratch": [],
  "resources": []
}
```

## Identities and dispositions

Run `inventory` to obtain every owned file's absolute path, size, and SHA-256. A Retained file resource copies these identity fields. It also names a specific `consumer`, supporting `evidence`, `next_owner`, and `release_condition`. Generic claims such as "old evidence" fail classification. Disposition totals include declared Removed bytes and represented Retained, Active, and Planned bytes.

Directory resources use complete `members` lists. Each member has a slash-separated relative `path`, `size`, and `sha256`. An empty member path identifies the resource file itself. Large lists use `members: {"inventory": "/absolute/members.json.gz", "sha256": "<compressed file SHA-256>", "count": 10000}`. The gzip file contains the complete JSON member list. Give that file a separate resource when it is an owned output. Never create overlapping phase inventories.

A Removed resource must be absent. Archived sources retain their complete member identities and set `archive: {"resource": "<archive resource id>", "prefix": ""}`. The separate archive resource must be Retained by the same owner with its current size and SHA-256. The checker reads ZIP and tar members without extraction and checks every declared member's bytes. For an archived single file, `prefix` names its exact member. `pending_readers` blocks removal. Disposable deleted outputs use `removal: "deleted"` and specific deletion `evidence`. Archive policy still requires packing unique streams and logs.

## Commands and generated view

Operations write only to stdout. Apply their output through the owning workflow's normal file edit. Never redirect onto a file the command reads.

```bash
python3 "$CLOSURE_SHARED_ROOT/references/scripts/closure.py" inventory --manifest /absolute/dispositions.json
python3 "$CLOSURE_SHARED_ROOT/references/scripts/closure.py" migrate --manifest /absolute/dispositions.json --plan /absolute/plan.md --owned-row /absolute/owned/legacy/path
python3 "$CLOSURE_SHARED_ROOT/references/scripts/closure.py" view --manifest /absolute/dispositions.json
python3 "$CLOSURE_SHARED_ROOT/references/scripts/closure.py" check --manifest /absolute/dispositions.json --plan /absolute/plan.md --owner review-run
```

Migration requires explicit ownership for each replaced legacy row, including represented directory members. A mixed row requires explicit adoption of every represented resource. Omit `--owned-row` only when no legacy row describes an adopted resource. Apply the emitted plan while preserving unrelated manual rows. Later updates replace only the matching generated block with `view` output. Its digest binds exact manifest bytes and generated states. The checker rejects stale views, competing owned manual rows, missing files, changed identities, unverified archives, and unexplained outputs. Exit 0 passes, exit 1 reports unsettled outputs, and exit 2 reports input or prerequisite errors. A failed check grants no cleanup authority.

For a run without a plan, save `view` output in a standalone resource view outside inventoried roots. Pass `--resource-view <absolute-view>` instead of `--plan` to `check`. Refresh that file after manifest changes. The same digest and competing-row checks apply. This route requires no task plan.
