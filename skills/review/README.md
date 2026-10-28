# Review reasons

Successful opted-in record publication precedes input retirement, and failed publication preserves retry inputs. A disposition inventory accounts for small ignored logs and named readers without treating unresolved findings as active terminal logs. Ordinary reviews retain their existing record opt-in.

[SKILL.md](SKILL.md) holds the runner rules. `rules/code.md` and `rules/plan.md` hold the rule sets. This file gives the failure each rule prevents.

Before you change a review rule, read its entry here. Add the new reason in the same commit. Write it as one to three sentences on the failure the rule prevents, with no dates, hashes or history.

## Contents

- [Runner and rule sets](#runner-and-rule-sets)
- [Scope and coverage](#scope-and-coverage)
- [Findings and verification](#findings-and-verification)
- [Reviewers and models](#reviewers-and-models)
- [Transport and completion](#transport-and-completion)
- [Briefs, records and artifacts](#briefs-records-and-artifacts)
- [Author authority for exclusions](#author-authority-for-exclusions)
- [Caller-owned comparison evidence](#caller-owned-comparison-evidence)

## Runner and rule sets

The method of review changes more often than the launch, the quote check and the triage. With the code checklist embedded in the runner, a plan review needed its own copy of the launch and triage, and the two copies drifted.

A rule set is a file inside this skill, not a separate skill. Every installed skill adds its description to every session's prompt, and a rule set cannot run without the runner.

The runner loads one built-in rule file, as the [selection step](SKILL.md#1-determine-what-to-review-and-load-its-rules) describes. A code review never reads the plan rules, so the unused rule set costs no context.

A caller's rules source joins the built-in sources through the same ordered selector. A project with its own review rules otherwise had to copy the runner or edit a public file to reach them. Callers plug in existing review methods, including whole review skills, without rewriting them into a fixed format. An explicit marker removes the ambiguity between a plan under review and a rules file.

Runner-owned rules outrank every rule source. A rules source that turned off report-only or the exact-quote evidence would break the quote check and the candidate tree. A missing rules source stops the review, because a silent fallback runs the built-in rules and reports them as the caller's.

The prompt names every loaded source, so a report shows which rules it applied.

The skill keeps the name `review`, because coordinators invoke it by name. A rename breaks every caller at once.

The plan rule set lives in this public skill because the runner selects it. A public runner that selected a private rule file would fail in every other installation.

Naming private rules in the public runner ties unrelated repositories to unavailable instructions, so callers own project-specific rule names and locations and pass the path at each call.

A security, design or UI rule set arrives only when a real review needs it. A rule set written ahead of its first review has no failure to name, so nothing tests its checks.

## Scope and coverage

Under file output, saved reports keep full reviewer responses out of native completion messages. Triaging a report still adds its text to the coordinator's context, so later briefs carry only verified findings. See [Why reviewer reports go to files](../../docs/orchestration.md#why-reviewer-reports-go-to-files) and [Why triage stays with the coordinator](../../docs/orchestration.md#why-triage-stays-with-the-coordinator).

Generated files repeat a source change, so reviewing them spends budget on copies. A defect in unchanged code is out of scope, because it must not block a change that did not cause it.

A single pass over a busy diff drops quiet findings, such as a missing test, next to concrete bugs. File-by-file review and the coverage sweep keep them.

A conflicting reader or writer of a shared value often lives in a file the diff never opened. The beyond-diff sweep finds it, and its limit to touched symbols stops it from becoming a repository audit.

A diff that deletes one layer of a feature leaves the other layers active, and the feature stays on screen. A removal guard that reads one of two locations certifies a removal that did not happen.

A change can pass small fixtures and fail at production size. A removed or conditional user-visible value looks like a plausible local edit. Without a check for authorization, the product loses behavior nobody approved.

A review that checks only a design document's stated claims misses what the document leaves out, such as a step nobody can follow.

## Findings and verification

A report that merges severity and confidence presents a high-impact guess as an urgent fact. The reachability check drops failure findings on code that cannot run. Code that cannot run is a dead-code finding instead.

Agents add fallbacks for values their producer never emits, and tests that fake those values hide the dead code. Tracing the producer separates such a fallback from a defense at a real input or version boundary. Reviewers otherwise stop at a lenient client parser or a layer behind a filter and call the fallback reachable. Each kept site needs cited producer code or a named boundary input, so keeping it costs the same trace as removing it. Reporting every site stops a fix that removes one copy.

In an interactive review the author decides what matters. A finding the reviewer drops in silence takes that choice away.

Reviewers invent text-level defects, and a correction for an invented defect adds a real one. An exact quote lets the coordinator check the text against the file, which a paraphrase defeats. A correct quote still does not prove the conclusion.

The code rules put the verdict alone on the last line, so the coordinator can tell a finished report from a truncated one on every route. The record writer reads the call there.

The quote check runs before the author sees any finding, so an unsupported citation never reaches the author as work. A parser failure does not prove a finding false, so unparsed findings get a manual check. Reviewers write findings in more layouts than a parser can list, so a report with no known marker still gets each cited quote checked. Those records name no finding, so a failed quote sends its finding to a hand check, never to a drop.

A valid finding is not always worth acting on. A fix menu that offers every verified finding makes harmless findings read as work, and the author then has to ask which ones matter. A general rule to challenge each proposal does not stop this, because the menu shapes the recommendation. The Worth mark keeps every finding visible, so the author still decides.

A runtime finding can be severe and still have nothing real that causes it. Judged on harm and cost alone, it lands in the fix-all option, and later ratings repeat that mark. The trigger test stops it at the first mark, and the note mark keeps it in front of the author.

## Reviewers and models

Each extra premium reviewer costs money, so a code review defaults to one reviewer on the current model. A plan review defaults to two reviewers from different model families, because its rule set names that pair.

Duplicate model tables drifted to conflicting versions inside one skill, so [models.md](../references/models.md) holds the only table. Shared provider rules keep each workflow from drifting to its own policy.

Runners use different default efforts. Identical prompt bytes, separate result destinations and recorded effort keep a model comparison from mixing prompt and launch differences.

## Transport and completion

A native reviewer under file output writes only its assigned report outside the candidate tree. Pointer returns avoid repeated full report payloads without granting candidate edits, and they let the coordinator choose when to read. Separate destination metadata preserves identical comparison prompts. A file-output comparison uses dispatch when native transport cannot separate that destination from its prompt.

Text output keeps native review available when a harness refuses a subagent's report write. See [When a reviewer returns text](../../docs/orchestration.md#when-a-reviewer-returns-text). The coordinator saves one canonical report before the quote check, because triage binds that file's hash. A second copy can differ by one character and break that binding.

Forced dispatch added a second completion route where the native route already worked. A report-only role in a prompt does not enforce read-only file access, so a task that needs a sandbox still dispatches. See [Context and delegation](../../docs/orchestration.md#context-and-delegation).

Each dispatched review selects one completion route. A foreground marked launch with an explicit log path retains its accepted session marker in the parent command output. Separate observation carries `--read-only` so its report stays blinded. Arc delivers no callback. See [Dispatch completion](../../docs/orchestration.md#dispatch-completion) and [Retained action effects](../../docs/orchestration.md#retained-action-effects).

A focused re-review reads the fix delta first, so the smaller budget goes to the corrections. A range diff stops a moved base from looking like part of the fix.

## Briefs, records and artifacts

Without owner messages and corrections in the brief, a fresh worker acts on stale instructions. See the [owner brief contract](../references/owner-brief.md).

After the findings settle, a code review's raw report is a duplicate nobody reads, so cleanup deletes it. A plan review keeps its reports beside the plan, because a later audit reads them with the plan entry. The artifacts stay when recording or delivery fails, so a retry still has its evidence.

A code review request is not a request for a lasting file, so a review record needs explicit opt-in. A plan review always writes its plan entry, because the audit of plan reviews decides from every entry. A record needs a stable target, so only an immutable candidate gets one.

The coordinator captures the skill version, the last commit that touched `SKILL.md` or `rules/`, with the first prompt. A rule-file edit changes what a reviewer reads, so it counts as a new version. Capturing it first means a later skill edit cannot change the recorded version.

Delivery fields are optional in schema 2, so earlier records stay valid without a rewrite, and a missing field reads as unknown. The writer checks that each mode change names its failure, so a recovered report cannot pass for a clean file delivery.

Shell serializers can publish call objects that the final recorder then rejects, so the shared writer validates triage before it replaces the file. The explicit shared root keeps copied installations working without a second writer.

Without identity checks and a matching previous-key snapshot, triage calls attach to a report the coordinator never checked. Compact output keeps bounded legacy input readable.

## Author authority for exclusions

If every known gap counts as an exclusion, an implementer can remove unfinished work from review. The author's quoted instruction and its source let a fresh reviewer check who excluded a case.

A belief in a plan does not authorize an exclusion, and an exclusion does not make a belief true. The template heading "Known gaps and authorized exclusions" keeps the two apart.

A change to the coordinator alone leaves the reviewer template with a conflicting instruction, so it shares the rule with [orchestrate](../orchestrate/SKILL.md). The invitation to report other cases stops one exclusion from closing the whole area.
## Caller-owned comparison evidence

A caller can retain prompts, rules, reports, and triage for its selected comparison recorder.
This request does not enable the runner's separate review record.
The caller publishes its record before releasing the retained evidence.
Recording errors leave the inputs available for retry.
This boundary prevents ordinary cleanup from deleting reports that a comparison still needs.
Successful publication of the runner's separate review record cannot release inputs that the caller's recorder still needs.
Citation failures and refuted claims remain separate because an incorrect location can describe a real defect.
