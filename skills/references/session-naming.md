# Workflow session names

Before launching a workflow worker, choose a name that identifies its artifact, job, and round:

```text
<stage>-<job>-<round>-<topic>-<seat>
```

## Fields

- Use `plan` for a plan or design document, and `code` for implementation work and its review.
- Use `implement`, `fix`, `review`, or `re-review` for ordinary jobs. Name other jobs explicitly, such as `carry-review` or `diagnose`.
- Start reviews at `1` within each stage. The review after accepted corrections is `re-review-2`. Further authorized reviews use their actual round numbers.
- Keep the topic short and concrete. Use an existing reviewer seat or model shorthand last to distinguish parallel workers. Record exact model and effort in runtime metadata.
- Use lowercase words, hyphens, and numbers. Avoid bare `focused`, `final`, and numeric suffixes that do not identify a job.

A focused first pass is still `review-1`. Restricted coverage alone does not make a review a re-review.

## Existing counters and attempts

Use the workflow's existing round or correction-batch identifier for implementers and fixers. Do not change workflow counters to fit names. When the first correction batch addresses review one, name that worker `fix-1`. A workflow that numbers the replacement as implementation round two uses `fix-2` instead.

A coverage continuation retains its review job and round, with `coverage-2` appended for the second attempt. A launcher retry retains its job and round, with `retry-2` appended. Increment the attempt suffix for later authorized attempts. Neither attempt creates a review round or authorizes more work.

For a writable Claude retry, check whether the original worktree exists. If it exists, pass its path through `--cwd` and omit `--worktree`. Keep its original branch and path even when the retry name changes.

A carry review checks a rebase or interaction with a moved target. Use its recorded round identifier when the workflow assigns one. Otherwise retain the round whose review is being carried. The explicit `carry-review` job distinguishes it from reviewing accepted corrections.

## Examples

These names use a workflow whose first implementation and first correction batch both have identifier `1`:

```text
plan-review-1-catalog-update-opus
plan-review-1-catalog-update-sol
plan-re-review-2-catalog-update-opus
plan-re-review-2-catalog-update-sol
code-implement-1-catalog-update-sol
code-review-1-catalog-update-opus
code-review-1-catalog-update-sol
code-fix-1-catalog-update-sol
code-re-review-2-catalog-update-opus
code-re-review-2-catalog-update-sol
code-review-1-catalog-update-opus-coverage-2
code-review-1-catalog-update-opus-retry-2
code-carry-review-1-catalog-update-opus
```

## Launch fields and identity

Pass the chosen name through dispatch's `--name` or the native tool's supported description or task-name field. Honor that field's restrictions. When a native task-name field requires underscores, replace hyphens with underscores while preserving field order. Vendor nicknames and automatic system reviewers remain outside this convention when their tools expose no naming field.

Use each fresh review's existing unique run directory and an explicit log destination. The launcher does not enforce name uniqueness, and sanitized default log names can collide. Continue sessions by UUID and preserve worktree paths. When a resumed session cannot change its launch name, record its attempt separately in run metadata.

Keep display names separate from artifact paths and controlled-comparison prompt bytes. Do not rename historical sessions or change review prompts solely to set a transport name. Writable Claude dispatch passes the chosen name through `-n`. Read-only Claude and Codex receipt names do not guarantee the child's saved session title.
