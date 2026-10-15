# Pre-commit review reasons

[SKILL.md](SKILL.md) holds the active review instructions. This file records decision reasons from the skill's Git history. Historical model choices and replaced transport rules are not current instructions.

**Evidence labels.** A recorded reason comes from a commit message or explanatory text introduced with the rule. An interpretation explains a diff without claiming a recorded motive or incident. "No reason recorded" identifies an evidence gap. Commit references identify the source of each entry.

## Contents

- [Isolated review and reliable coverage](#isolated-review-and-reliable-coverage)
- [Confidence and finding evidence](#confidence-and-finding-evidence)
- [Historical reviewer defaults and shared model rules](#historical-reviewer-defaults-and-shared-model-rules)
- [Review transport and completion](#review-transport-and-completion)
- [Saved evidence and brief ownership](#saved-evidence-and-brief-ownership)
- [Author authority for review exclusions](#author-authority-for-review-exclusions)
- [Consistent reviewer prompts](#consistent-reviewer-prompts)
- [Evidence and limits](#evidence-and-limits)
- [Maintaining this log](#maintaining-this-log)

## Isolated review and reliable coverage

**Recorded reason, original skill.** Isolated reviewers keep full review reports out of the implementation conversation. The original instructions also warn that reviewers invent text-level defects. Checking a finding against the file prevents a correction from introducing a real defect. These explanations appear in the imported skill, not a detailed initial commit message (`d962d85`).

**No reason recorded.** The initial import includes generated-file filtering and limits findings to changed code. Its commit message supplies no separate reason for those boundaries (`d962d85`).

**Recorded reason, coverage.** A busy review loses quieter findings beside concrete bugs. File-by-file review and a final coverage sweep preserve attention to missing tests, conventions, and documentation. The commit body states that purpose. The added instructions exclude test complaints about trivial helpers, which limits noise (`c721e12`).

**Recorded reason, shared values.** A conflicting reader or writer often lives outside the diff. The added explanation ties the beyond-diff sweep to divergent sources of truth. Restricting searches to touched fields, formats, checksums, and constants bounds the work. That boundary prevents a focused review from expanding into a repository audit (`48b009e`).

**Recorded reason, removals and plans.** Deleting one feature layer leaves other layers active. The removal criterion therefore examines remaining producers, consumers, and the removal's own guard. A design-document review also needs an omissions pass. Checking only stated claims leaves missing steps and unreachable checks unexamined. Both explanations appear in the added prompt (`e99c66e`).

**Interpretation.** The same change adds production-scale cost and authorization checks for removed or conditional user-visible behavior. These criteria extend correctness beyond small fixtures and locally plausible edits. The diff records the checks, without identifying a particular incident as their cause (`e99c66e`).

## Confidence and finding evidence

**Recorded reason, confidence.** Severity describes impact, while confidence describes certainty. A high-impact guess remains a guess. The confidence axis and pre-report reachability check prevent the report from treating those judgments as interchangeable (`c721e12`).

**Recorded reason, uncertain findings.** Interactive review leaves the decision with the author. Labeling a low-confidence finding preserves that choice, while silently filtering it removes the choice. This reason appears in both the commit body and the added instruction (`55c6d95`).

**Recorded reason, quotations.** A reconstructed or paraphrased line defeats a literal citation check. Exact quotations make the finding's textual premise inspectable. The accompanying explanation separates two questions: whether the text exists and whether it supports the conclusion. A correct quotation alone does not establish a correct finding (`e99c66e`).

**Recorded reason, automated checking.** A parser failure does not establish that a finding is false. The added quote-check procedure retains manual review for unparseable findings and findings outside its JSON output. It also treats matching text at another location differently from absent text. These distinctions preserve substantive findings without accepting fabricated citations (`9890035`).

**Interpretation.** Moving verification before presentation prevents an unsupported citation from reaching the author as an actionable finding. The diff establishes this order. It does not measure false-finding reduction (`9890035`).

## Historical reviewer defaults and shared model rules

The reviewer defaults before September are superseded. September's single current-model reviewer default remains active. Use [SKILL.md](SKILL.md#2-parse-model-arguments) for current selection and [models.md](../references/models.md) for available shorthands.

**Recorded historical reasons.** The June single-reviewer change asserted that file-by-file review removed the benefit of a second model. Its next revision narrowed that assertion to a second Claude model (`c721e12`, `55c6d95`). A later commit reported real-diff testing with complementary findings and restored two reviewers. It also warned that the merged findings needed adjudication (`8816f01`).

These are recorded historical claims, not a controlled comparison reproduced here. The messages do not supply matched prompts, fixtures, or complete experimental conditions. They do not establish present model performance.

**No reason recorded, model versions.** The July update selected Opus 4.8 and Sonnet 5 and added Fable 5. Its message records those choices without a selection rationale (`662c685`).

**Interpretation, later default.** The September revision returned to one current-model reviewer unless the author or calling workflow requested more. This makes additional premium reviewers an explicit choice. The commit does not record a new comparative result supporting the reversal (`e99c66e`).

**Recorded reason, one model table.** Duplicate tables had drifted from active model versions despite instructions to keep them synchronized. The commit body names conflicting versions within the same skill. One shared table removes that duplication, while the harness catalog takes precedence over an unacceptable model spelling (`1ed3cd0`).

**Interpretation, provider and naming references.** Shared provider rules avoid separate execution policies in each workflow. Job and round names distinguish reviewer assignments. These changes link common guidance rather than expanding the review criteria. Their messages record centralization and naming requirements, without a review-specific failure account (`0b9daa9`, `ff85f6c`).

**Recorded reason, sentence case.** The wording change rejects capitalized imperatives that give a command without its reason. The commit body explains that surrounding reasoning lets the model extend a rule to an unlisted case (`aa720b6`).

## Review transport and completion

**Recorded historical reason, CLI fallback.** The added fallback reaches an OpenAI reviewer when the harness cannot target it. Its inline guidance reserves expensive deep reviews for risky changes. The direct CLI recipe is superseded. Its historical timing and token assertions are not current cost estimates (`35a7ab3`).

**Recorded reason, comparable prompts.** Without explicit effort, runners use different defaults. The transport guidance records that source of variation. Identical comparison prompts, separate result destinations, and recorded effort distinguish prompt content from launch metadata (`e99c66e`). These controls alone do not establish a valid performance comparison.

**Recorded correction, native completion.** Mandatory dispatch for immutable candidates added a separate completion route even when the harness supported native review. The correction prefers supported native reviewers and preserves dispatch for unavailable native combinations or required sandbox enforcement. A report-only role does not enforce filesystem read-only access. Shared reasoning lives in [Context and delegation](../../docs/orchestration.md#context-and-delegation) (`9cd3f15`).

**Recorded reason, callback recovery.** Worker observation, queue acceptance, and parent consumption are distinct states. Receipt claims and durable acknowledgments preserve what the parent consumed. Interrupted processing retains uncertainty, and changed reports remain separate events. This is not a universal guarantee against duplicate external effects. Shared details live in [Detached dispatch receipts](../../docs/orchestration.md#detached-dispatch-receipts) (`4b494d4`).

**Interpretation, focused re-review.** Accepted findings and the fix delta concentrate the next pass on corrections and nearby interactions. A range diff separates a moved target from the replacement change. The added instructions keep full context available while reducing the second pass's tool budget (`9890035`).

## Saved evidence and brief ownership

**Recorded reason, artifact lifetime.** Detached review leaves prompt, log, and report files behind. The cleanup explanation avoids retaining duplicate reports after findings and corrections are settled. Later changes retain artifacts when recording or delivery fails, so recovery still has evidence (`e99c66e`, `9890035`, `9cd3f15`).

**Interpretation, optional records.** Explicit record opt-in separates a review request from a request for a lasting machine-readable artifact. Restricting records to immutable candidates gives the record a stable reviewed target (`9890035`).

**Recorded reason, source version.** Capturing the skill revision with the first prompt prevents a later skill edit from changing the recorded review version. The added explanation states that boundary. [SKILL.md](SKILL.md#transport) links the record mechanics (`9890035`).

**Recorded requirement, owner messages.** Worker briefs carry owner messages, corrections, and approval boundaries. The commit body states that requirement. The shared [owner brief contract](../references/owner-brief.md) owns its details (`7340fb4`).

## Author authority for review exclusions

Known gaps come from both author scope decisions and implementer disclosures. Treating every gap as an exclusion lets an implementer remove unfinished work from review.

A plan or brief also contains beliefs that require checking. A factual belief does not authorize an exclusion, and an exclusion does not make a factual claim true.

**Recorded reason.** Only the author's recorded instruction supplies exclusion authority. The prompt includes its exact quote and source so a fresh reviewer can check who excluded the case. The correction and its companion explanation establish this distinction (`134ce74`, `9a43616`).

The strongest objection is reopening intentional exclusions or unrelated defects. Explicit author exclusions remain valid, and ordinary scope and severity rules still apply. A disclosure alone supplies neither an exemption nor a reason to block unrelated work.

## Consistent reviewer prompts

Changing a coordinator's rule alone leaves conflicting instructions in the downstream reviewer template. The template therefore uses the same authority boundary as [orchestrate](../orchestrate/SKILL.md).

The example requires the author's instruction quote and source before saying "Do not report". Without that qualification, copying the example would turn an implementer's disclosure into an exclusion. The invitation to review other cases prevents one exclusion from closing the whole area.

The heading "Known gaps and authorized exclusions" keeps those categories distinct. The old blanket attribution of the plan, brief, and known gaps to the author obscured their different sources. Removing it keeps factual checks separate from authority checks. **Recorded reason, companion explanation** (`9a43616`).

## Evidence and limits

Local commit `134ce7493783a20bbc1c4f522e57137e36eb1df3` records the correction in orchestrate and the downstream review instructions. The conflicting wording establishes an instruction inconsistency. It does not establish that the wording caused a historical product regression.

A read-only dry run checked decisions under the corrected instructions. It did not compare old and new instructions or prove prevention of product regressions. **Recorded limit, companion explanation** (`9a43616`).

Shared implementation reasoning for [retained action effects](../../docs/orchestration.md#retained-action-effects) belongs in the orchestration guide.

## Maintaining this log

Before changing a review rule, read the relevant entry and add the new reason in the same commit. Record the concrete failure or authority boundary, the strongest objection, and the evidence limits. Link shared reasoning instead of duplicating it. Distinguish author instructions from agent beliefs and disclosures.
