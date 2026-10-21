# Pre-commit review reasons

Successful opted-in record publication precedes input retirement, and failed publication preserves retry inputs. A disposition inventory accounts for small ignored logs and named readers without treating unresolved findings as active terminal logs. Ordinary reviews retain their existing record opt-in.

[SKILL.md](SKILL.md) holds the review rules. This file gives the failure each rule prevents.

Before you change a review rule, read its entry here. Add the new reason in the same commit. Write it as one to three sentences on the failure the rule prevents, with no dates, hashes or history.

## Scope and coverage

Isolated reviewers keep full reports out of the implementation context, which still needs that space.

Generated files repeat a source change, so reviewing them spends budget on copies. A defect in unchanged code is out of scope, because it must not block a change that did not cause it.

A single pass over a busy diff drops quiet findings, such as a missing test, next to concrete bugs. File-by-file review and the coverage sweep keep them.

A conflicting reader or writer of a shared value often lives in a file the diff never opened. The beyond-diff sweep finds it, and its limit to touched symbols stops it from becoming a repository audit.

A diff that deletes one layer of a feature leaves the other layers active, and the feature stays on screen. A removal guard that reads one of two locations certifies a removal that did not happen.

A change can pass small fixtures and fail at production size. A removed or conditional user-visible value looks like a plausible local edit. Without a check for authorization, the product loses behavior nobody approved.

A review that checks only a design document's stated claims misses what the document leaves out, such as a step nobody can follow.

## Findings and verification

A report that merges severity and confidence presents a high-impact guess as an urgent fact. The reachability check drops findings on code that cannot run.

In an interactive review the author decides what matters. A finding the reviewer drops in silence takes that choice away.

Reviewers invent text-level defects, and a correction for an invented defect adds a real one. An exact quote lets the coordinator check the text against the file, which a paraphrase defeats. A correct quote still does not prove the conclusion.

The quote check runs before the author sees any finding, so an unsupported citation never reaches the author as work. A parser failure does not prove a finding false, so unparsed findings get a manual check.

## Reviewers and models

Each extra premium reviewer costs money, so the default is one reviewer on the current model.

Duplicate model tables drifted to conflicting versions inside one skill, so [models.md](../references/models.md) holds the only table. Shared provider rules keep each workflow from drifting to its own policy.

Runners use different default efforts. Identical prompt bytes, separate result destinations and recorded effort keep a model comparison from mixing prompt and launch differences.

## Transport and completion

Forced dispatch added a second completion route where the native route already worked. A report-only role in a prompt does not enforce read-only file access, so a task that needs a sandbox still dispatches. See [Context and delegation](../../docs/orchestration.md#context-and-delegation).

Each dispatched review selects one completion route. A foreground marked launch with an explicit log path retains its accepted session marker in the parent command output. Separate observation carries `--read-only` so its report stays blinded. Arc delivers no callback. See [Dispatch completion](../../docs/orchestration.md#dispatch-completion) and [Retained action effects](../../docs/orchestration.md#retained-action-effects).

A focused re-review reads the fix delta first, so the smaller budget goes to the corrections. A range diff stops a moved base from looking like part of the fix.

## Briefs, records and artifacts

Without owner messages and corrections in the brief, a fresh worker acts on stale instructions. See the [owner brief contract](../references/owner-brief.md).

After the findings settle, the raw report is a duplicate nobody reads, so cleanup deletes it. The artifacts stay when recording or delivery fails, so a retry still has its evidence.

A review request is not a request for a lasting file, so a record needs explicit opt-in. A record needs a stable target, so only an immutable candidate gets one.

The coordinator captures the skill version with the first prompt, so a later skill edit cannot change the recorded version.

Shell serializers can publish call objects that the final recorder then rejects, so the shared writer validates triage before it replaces the file. The explicit shared root keeps copied installations working without a second writer.

Without identity checks and a matching previous-key snapshot, triage calls attach to a report the coordinator never checked. Compact output keeps bounded legacy input readable.

## Author authority for exclusions

If every known gap counts as an exclusion, an implementer can remove unfinished work from review. The author's quoted instruction and its source let a fresh reviewer check who excluded a case.

A belief in a plan does not authorize an exclusion, and an exclusion does not make a belief true. The template heading "Known gaps and authorized exclusions" keeps the two apart.

A change to the coordinator alone leaves the reviewer template with a conflicting instruction, so it shares the rule with [orchestrate](../orchestrate/SKILL.md). The invitation to report other cases stops one exclusion from closing the whole area.
