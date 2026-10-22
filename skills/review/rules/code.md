# Code review rules

[../SKILL.md](../SKILL.md) loads this file for uncommitted changes, an immutable candidate, or a single commit. This rule set adds or replaces no procedure step.

## Changed files

Determine the list of changed files to review from the diff that step 1 selects. This list fills `{LIST THE CHANGED FILES HERE}`.

**Exclude generated files.** Before building the file list, check if any changed files are generated from templates or build outputs. Exclude them from the detailed review list. Mention them in passing in the prompt, e.g.: "N generated files were also changed, consistent with the template/source changes."

## Template slots

- `{REVIEW_KIND}`: `code`.
- `{REVIEW_TARGET}`: the uncommitted changes, or the immutable candidate SHA against its base SHA, followed by `for issues`.
- Keep every template section.
- `{PLAN_AND_BRIEF_PATHS_OR_NONE}`: the plan and brief paths, or `None.`
- `{ALLOWED_REPOSITORY_ROOTS}`: the roots that the prompt file names under Transport.
- `{RULE_SET}`: the block below, unchanged.

## Rule set

```text
## Review Criteria

Evaluate the changes for:
1. **Correctness & Bugs**: Logic errors, off-by-one, nil/null derefs, race conditions, missing error handling
2. **Security**: Injection, auth issues, secrets exposure, unsafe input handling (OWASP Top 10)
3. **Code Style & Consistency**: Naming, structure, idiomatic patterns for the language
4. **Performance**: Unnecessary allocations, N+1 queries, missing indexes, algorithmic complexity
5. **AI Code Smells**: dead code, hallucinated constants, zero-value leaks, fields mapped by name similarity instead of semantics, training-data artifacts (wrong copyright years, URLs from other projects).
6. **Commit Alignment and behavioral subtraction**: do the changes match the stated intent? For every user-visible value, control, or warning removed or made conditional, cite explicit authorization in the current request, current plan, or repository requirements. Silence is not authorization. Flag code that looks leftover from a different approach.
7. **Test Coverage**: a behavioral change needs tests. Flag untested logic paths.
8. **Duplication & divergent sources of truth**: check whether existing code already does this. A cross-component change needs a single source of truth (constant, type, helper) or at least a code comment linking the call sites. For any field, serialized format, checksum, or constant the diff reads or writes, grep the repo for its OTHER readers and writers. If another site derives the "same" value from a different source, flag the divergence even though that site isn't in the diff.
9. **Cost at scale**: name what the change costs at the largest input the product meets in production, or say why the change cannot reach that size.
10. **Incomplete removal**: criterion 8 asks about symbols the diff reads or writes. This is its mirror, and a diff that deletes things needs it. For every symbol, string, class name, field or config key the diff DELETES, grep the repo for what still produces it and what still consumes it. A feature usually lives in several layers: what computes it, what names it in a strings table, what draws it, what stores it, and what documents it. A diff that deletes one layer and leaves the rest passes every other criterion cleanly, and the feature is still on screen. Check the removal's own guard as well: a guard that reads one file, while the deleted thing lived in two, certifies a removal that did not happen. Say which layers the diff reached and which it did not.

Skip criteria that are irrelevant to the file types being reviewed.

## Instructions

Get the full diff for these files yourself using git. Read the changed files in full to understand surrounding context. Use workspace search tools to check for dead code, duplication, and verify constants.

For an immutable candidate, read its content with `git show <candidate>:<path>` or from a checkout fixed at that SHA. Do not substitute moving working-tree content.

**Work file-by-file.** Do not review the whole changeset in one pass. Walk the changed files one at a time, apply the full criteria list above to each file's diff, and record that file's findings before moving to the next. On a large or busy diff, a single holistic pass spends its attention on the loud findings (concrete bugs) and silently drops the quiet ones (a missing test, a convention violation). Per-file passes keep each file's coverage intact.

**Then do a coverage sweep.** After the per-file pass, re-scan the changed files once more, looking only for the findings most easily lost next to concrete bugs: a new handler/endpoint/converter with no test, dialect- or convention-specific issues, and missing changelog/docs. This is a coverage check, not a licence to invent issues. Do not raise test-gaps on trivial helpers.

Also run a bounded beyond-diff sweep: for each shared symbol (field, serialized format, checksum, constant) the diff reads or writes, grep its other writers and readers and confirm they agree on source and shape. This is scoped to the symbols the diff touches, not a whole-repo review, so it stays cheap. It is what catches divergent-source-of-truth drift, where the conflicting code is a file the diff never opened.

Only flag issues introduced by the changes. Before reporting any finding, verify the problematic code appears in the `git diff` output (added or modified lines). If code was not changed in this diff, it is out of scope - even if you discover a real bug while reading surrounding context. You can mention pre-existing issues in unchanged code as INFO at most, clearly labeled "[pre-existing]", but never WARNING or CRITICAL.

Do not flag untracked files unless they appear in the staged changes. Do not assume which files the author intends to commit.

Plan files can lag implementation details. When the review prompt names a current plan, treat its requirements and repository-owned design requirements as constraints. Code outranks stale implementation notes, not current product intent. Flag contradictions.

Check each factual claim that the review reaches against the code. A contradiction is a finding that quotes both the claim and the code. Exclude a known gap only when the author's recorded instruction excludes it. An implementer's disclosure is not author approval. Assess other gaps under the existing severity and scope rules.

**When the change under review IS a design document** rather than code with one attached, most criteria above do not apply, and an omissions pass matters more than the verification pass. A prompt that lists claims to verify gets those claims verified and nothing else, so ask separately what the document does not say: a step that cannot be followed as written, an axis never mentioned, a claim that something does not exist, a case the verification section cannot reach. Two reviewers on an identical prompt can return zero overlapping findings, with the one that checked every stated claim approving a plan whose central step was unimplementable.

**Do not report successful validation as findings.** Findings are for problems. If you verified something and it was correct, that is not a finding.

**Validate before reporting.** Once you have drafted your findings, re-read the exact lines each one cites and check reachability (is the code path actually reachable, are there guards that already handle it). Drop any finding you cannot substantiate against the cited lines. Only then write the review.

## Report Format

Write a review with:
- A summary with overall risk level (low/medium/high)
- Findings with file locations, severity (CRITICAL/WARNING/INFO), confidence (high/medium/low), and suggested fixes
- **The exact text your finding rests on**, as the evidence rule above requires.
- A verdict: APPROVE, REQUEST_CHANGES, or COMMENT

Severity and confidence are separate axes. Severity is the impact if the finding is real. Confidence is how sure you are that it is real. A high-impact guess is still a guess. **Do not use hedge words ("possibly", "might", "appears to") on a high-confidence finding** - if you are hedging, it is not high confidence, so label it medium or low.

**Report low-confidence findings too, clearly labeled. Do not silently drop them.** This is an interactive review where the author decides what matters, so a labeled "maybe" is more useful than a hidden one. (Filtering low-confidence findings makes sense when posting unattended to a PR, but not here.)

If there are no issues, say so clearly. Do not fabricate issues.
```
