# Proposal: reliable reviewer report delivery

Status: Implemented

## Goal

Keep native report-file pointers where they work. Support complete text returns when the harness cannot save a report. Save each usable report before quotation checks, triage, and recording.

The current contract treats a full-text native return as an unexpected deviation. A harness can reject the report write even when the reviewer completes its analysis.

## Recommendation

- Prefer file output when supported tools, granted write scope, and destination constraints permit it. Retain available successful completion evidence for that harness version and native route.
- Select text output before launch when a known restriction or retained delivery failure prevents file output. The reviewer writes no files and returns the complete report.
- Accept a complete text report as recovery after a rejected report write. Save the delivery failure separately from the findings.
- Keep dispatch for requested models, required isolation, or output capabilities that native review cannot provide.

The strongest objection is another completion branch. The current runner already accepts text recovery, so this change makes that branch explicit and preserves its evidence.

Use stable reviewer labels for new destinations, such as `reports/A.md`. This naming convention does not establish write capability. After a denial, select text for the same harness version, route, and destination constraints until new capability evidence exists. Preserve that decision through continuation. Do not rename the destination, switch write tools, or change permissions to retry the denied operation.

## Caller and reviewer contract

The caller selects one output mode before launch. It assigns a unique destination outside the candidate tree and within the granted write scope.

For file output, retain the assigned destination and path-only completion contract. For text output, use this instruction:

```text
Write no files. Return your complete report as the final answer.
Put coverage limits before the final call.
Keep the selected rules' report format.
End with APPROVE, COMMENT, or REQUEST_CHANGES on its own line.
```

The caller saves the full returned report before this existing check:

```text
quote_check.py --root <reviewed tree> --report <saved report>
```

Use a mechanical export only when the harness exposes one. No mechanical native capture was established in this investigation. Record a model-mediated copy as transcribed. A hash identifies that saved copy. It does not prove that the copy matches the original completion.

Save one canonical report per completion at the destination assigned before launch. Before checks or triage, move any partial file to a sibling evidence path. Save the complete returned text at the assigned destination. After quotation checks or triage starts, reuse that file and its hash on retry. Never transcribe it again or silently replace it.

Retain the source completion reference separately. Conflicting complete outputs remain ambiguous until the caller resolves them.

A rejected report write does not invalidate a complete returned review. A denied source read, missing report, empty completion, or truncated report remains incomplete evidence. Never convert these outcomes into a clean approval.

## Delivery metadata

Record selected and actual output modes, the capture method, and a delivery failure category beside each launch and report. Keep report hashes and session identities. The comparison inputs retain exact per-reviewer prompt identities.

```json
{
  "report_delivery": {
    "selected": "file",
    "actual": "text",
    "capture": "transcribed-text",
    "failure": "report-write-denied"
  }
}
```

| Selected | Actual | Capture | Failure category |
| --- | --- | --- | --- |
| File | File | Reviewer-written file | None |
| Text | Text | Exported or transcribed text | None |
| File | Text | Exported or transcribed text | Report write denied, unavailable, or unexpected text |
| Text | File | Reviewer-written file | Output contract violation |
| Either | None | None | Missing, empty, or truncated report |

Actual mode describes the canonical report's source. A mode change requires a saved failure category. A file write during selected text output violates the reviewer contract. Preserve that evidence and resolve the violation before triage.

Missing fields in older records remain unknown. Keep schema 2 and make new fields optional. Do not rewrite earlier records.

Finalize delivery metadata before triage binds the key file. Later key changes follow the existing snapshot and publication steps.

## Comparisons

- Text reviewers need no output path in their prompt. The parent maps each completion to its assigned destination.
- File reviewers still need a destination supplied separately from a controlled comparison prompt. Keep the dispatch requirement when native file output lacks that capability.
- Identical prompt bytes, consistent selected modes, and matching actual modes prevent extra delivery differences. They do not establish a controlled experiment.
- Preserve recovery and mixed modes as comparison limitations. Transcribed reports do not establish byte-exact source evidence.
- Keep existing model, effort, provider, candidate, round, and scoring requirements. Ordinary multi-reviewer observations remain observations.

A passing quote check establishes the saved copy's citation against the reviewed tree. It does not establish what the reviewer originally quoted when transcription can change the text.

Text output enters the parent's context at completion. Reading a full report file also adds that text. File pointers retain control over when and how much the parent reads.

## Implementation scope

| Source | Proposed change |
| --- | --- |
| `skills/review/SKILL.md` | Route selection, reviewer template, recovery, and context wording |
| `skills/review/README.md` | Reasons for supported output modes and evidence limits |
| `skills/orchestrate/SKILL.md` | Honor the selected route instead of requiring native pointer returns |
| `skills/orchestrate/references/review-comparisons.md` | Capture delivery modes and comparison limitations |
| `skills/review/references/run-record.md` | Describe optional delivery metadata and older-record behavior |
| `skills/review/scripts/write_record.py` | Retain and check optional delivery metadata in recorded reviewer entries |
| `skills/references/scripts/triage.py` | No change. Verify that delivery metadata leaves existing report identity unchanged |
| `docs/orchestration.md` | Explain report persistence and context costs across both routes |

`consult` already requests text output. `delegate` has no assigned report-file requirement. `dispatch` already persists final answers. Their current contracts do not need this change.

## Acceptance

- File output still returns a saved report path. Text output reaches the same quotation and triage steps.
- Complete text recovery retains the original delivery error. Missing or truncated evidence stays incomplete.
- Saving methods retain honest provenance. No model-mediated copy claims mechanical byte identity.
- Retried publication reuses the canonical report and its bound hash. Delivery fields do not change triage identity.
- Controlled prompts contain no reviewer-specific destinations. Recovery and mixed routes remain visible limitations.
- Existing record inputs remain readable. New delivery metadata survives publication and continuation.
- Reviewers remain report-only. Implementers own corrections. Prompt, rule, report, quotation, and triage retention remain intact.

Implementation requires the skill audit script and a fresh full-file agent read before commit. Use one bounded native completion test only if source inspection cannot establish report capture behavior. No repository review is needed to test report delivery.
