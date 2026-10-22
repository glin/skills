# Review comparison inputs

A caller selects the recorder. This contract captures existing reviews without launching more workers.
The recorder owns its output format and location.

## Contents

- [Capture](#capture)
- [Triage](#triage)
- [Eligibility](#eligibility)
- [Costs](#costs)
- [Close and resume](#close-and-resume)

## Capture

At Brief, save the resolved recorder identity and loaded skill provenance in the reserved run folder.
Save the recorder's loaded bytes and SHA-256 hash in `recorder.json`.
Use `identity`, `path`, and `sha256` for the resolved name, saved snapshot path, and byte hash.
Keep that identity through continuation. A missing selected recorder is a recording gap.
Before each review launch, save the prompt, its SHA-256 hash, and snapshots of the selected rules.
Resolve base and candidate commits to full SHAs.
Record the requested configuration and the configuration that the launch metadata establishes.
Set `configuration_verified` to true only when the metadata establishes the declared values.
Use `unknown` for unavailable configuration fields. Never infer effort from a model name.

Save `comparison.json` with this structure. Paths are relative to the reserved run folder.
Every referenced input must remain inside that folder, including resolved symlink targets.
Copy an external report or transcript there before adding its path.
Do not copy a coordinator transcript that includes unrelated tasks.

```json
{
  "schema_version": 1,
  "run_id": "<stable run id>",
  "created": "YYYY-MM-DD",
  "task": "<task description>",
  "project": "<project name>",
  "repository": "<source repository identity>",
  "recorder": "<resolved caller-selected recorder>",
  "recorder_path": "recorder.json",
  "status": "complete",
  "landed_commit": null,
  "provenance_path": "skill-provenance.json",
  "rounds": [
    {
      "id": 1,
      "base_commit": "<full SHA>",
      "candidate_commit": "<full SHA>",
      "prompt_path": "round-1/prompt.md",
      "prompt_sha256": "<hash of exact prompt bytes>",
      "rules": [
        {
          "name": "<selected rule source>",
          "path": "round-1/rules/source.md",
          "sha256": "<hash of saved rule bytes>",
          "commit": "<source commit, or unknown>"
        }
      ],
      "triage_path": "round-1/triage.json",
      "quotation_path": "round-1/quotation-results.json",
      "reviewers": [
        {
          "id": "A",
          "candidate_commit": "<same full SHA>",
          "model": "<exact model id>",
          "effort": "high",
          "harness": "<harness and version>",
          "provider": "<provider, or unknown>",
          "transport": "native",
          "configuration_verified": true,
          "session_id": "<session id, or unknown>",
          "launch_path": "round-1/launch/A.json",
          "outcome": "completed",
          "report_path": "round-1/reports/A.md",
          "report_sha256": "<hash of saved report bytes>",
          "report_delivery": {
            "selected": "file",
            "actual": "file",
            "capture": "reviewer-file",
            "failure": null
          },
          "delivery_evidence": [],
          "transcript_path": null,
          "cost_path": null,
          "review_window": null,
          "runtime_seconds": null
        }
      ]
    }
  ]
}
```

Run status is `complete`, `stopped`, or `failed`.
Round ids start at 1 and increase. Each round keeps its full base and candidate SHAs.
Reviewer outcome is `completed`, `incomplete`, `failed`, or `cancelled`.
A completed or incomplete reviewer needs a saved report.
A failed or cancelled reviewer retains every available report and log.
Record gaps explicitly. A failed reviewer never becomes a clean approval.

Each `launch_path` names saved launch metadata in this form:

```json
{
  "id": "A",
  "candidate_commit": "<round candidate SHA>",
  "model": "<established model id, or unknown>",
  "effort": "high",
  "harness": "<established harness and version, or unknown>",
  "provider": "<established provider, or unknown>",
  "transport": "native",
  "session_id": "<session id, or unknown>",
  "configuration_verified": true,
  "executed": true,
  "prompt_path": "round-1/prompt.md",
  "prompt_sha256": "<hash of exact launched prompt bytes>",
  "report_destination": "round-1/reports/A.md",
  "report_delivery": { "selected": "file" },
  "requested": {
    "model": "<requested model id, or unknown>",
    "effort": "high",
    "harness": "<requested harness, or unknown>",
    "provider": "<requested provider, or unknown>"
  }
}
```

Capture requested values before launch. Capture established values from supported launch parameters and returned execution metadata.
Keep unavailable values `unknown`. A submission without execution evidence keeps `executed: false`.
The reviewer entry matches its saved launch metadata. Retain raw launch receipts in the saved transcript when available.
Completed reviews require executed launch evidence and a nonempty report.
Capture `report_sha256` from the canonical report when it is saved. Keep it null when no report exists. A retry reuses that report and hash.
Capture `report_delivery.selected` in launch metadata before launch.
When the report is saved or found missing, copy `selected` into the reviewer entry and add `actual`, `capture`, and `failure`.
The review skill's [run record](../../review/references/run-record.md#report-delivery) defines these values.
`delivery_evidence` lists saved partial reports and delivery errors. An older entry without delivery fields reads as unknown.
The saved report path matches the destination assigned at launch.
Launch metadata binds the exact shared or reviewer-specific prompt path and hash.
Transport is `native`, `dispatch`, or `unknown`.
Missing transcripts, costs, runtime, and review windows remain null.
Runtime uses elapsed whole seconds when saved timestamps establish it.
A text-output prompt names no destination. The coordinator maps each completion to its assigned destination.
If a native file-output prompt embeds a destination, save that reviewer's exact prompt separately.
Add both `prompt_path` and `prompt_sha256` to its reviewer entry.
Otherwise the reviewer uses the round prompt bytes.
Identical prompt bytes and matching delivery modes remove two differences between reviewers.
Neither turns an ordinary review into a controlled experiment.
Record a text recovery, mixed delivery modes, and transcribed reports as comparison limitations.
A transcribed report's hash and quote check cover the saved copy, not the reviewer's original completion.

`provenance_path` names the frozen skill provenance, not a later live skill lookup.
The provenance contains the fields specified in the orchestrate Brief.
Keep saved snapshot hashes even when its source commit is unknown.
Rules carry their frozen bytes and source commit when known.
The saved prompt names the immutable base and candidate.
Do not change prompts solely to qualify a run.

## Triage

After each round, save `triage.json` in this form:

```json
{
  "candidate_commit": "<round candidate SHA>",
  "coverage_complete": true,
  "judge_blind": false,
  "findings": [
    {
      "id": "A-1",
      "reviewer": "A",
      "disposition": "accepted",
      "reason": "<judgment and supporting evidence>",
      "quote": "<reported quote>",
      "citation_error": false,
      "fix_commit": null
    }
  ]
}
```

Keep every reported finding, including quotation failures.
Dispositions are `accepted`, `invalid`, `citation-error`, `follow-up`, `out-of-scope`, `duplicate`, `declined`, or `unevaluated`.
Only independently refuted claims receive `invalid` and count as false positives.
A citation error does not prove that the reported defect is false.
Use the separate `citation_error` boolean for accepted findings with citation errors.
Use `citation-error` when the failed citation leaves the claim unevaluated.
Keep follow-up, out-of-scope, duplicate, declined, and unevaluated findings separate.
Reasons preserve the coordinator's judgment and supporting evidence.
`quotation_path` names saved quotation results, including parser failures and manual corrections.
Save a JSON object even when the report contains no findings.
If triage is incomplete, set `coverage_complete` to false.
Keep full fix SHAs when accepted corrections land.
Claim blind triage only when reviewer identities stayed hidden until judgment.
The default is `judge_blind: false`.

## Eligibility

At least two verified, executed reviewer configurations must differ within the same round and candidate.
Compare verified model, effort, harness, and provider values.
Unknown values cannot prove a difference.
An implementer on one model and a reviewer on another do not qualify.
Repeated reviewers on the same configuration do not qualify.
Configuration changes between candidates or rounds do not qualify.
A failed reviewer qualifies only when its saved report or transcript establishes an executed attempt.
A cancelled reviewer never qualifies.

## Costs

`cost_path` names a frozen Arc JSON report for that reviewer's session and review window.
The report's `source.sessionId` must match the reviewer.
Arc schema version 2 stores `window` at the root, alongside `source` and `parent`.
Set the reviewer's `review_window` to the same `since` and `until` timestamps.
Bounds need explicit timezones. The start is included and the end is excluded.
Use only the matching reviewer's `parent` summary. Do not add `children` or `combined`.
Unknown or unbounded windows leave cost missing.
Null total cost remains missing. Preserve the priced subtotal and coverage separately.
Preserve `requestCountExact`, `requests`, `pricedRequests`, and `unpricedRequests` when available.
Arc omits `coverage` and `requestCountExact` when coverage is complete and requests are exact.
Incomplete coverage leaves full cost missing even when the report supplies a numeric estimate.
Overlapping windows for the same source cannot supply additive reviewer costs.
Never substitute runner dollars for Arc estimates or describe estimates as invoices.

## Close and resume

Invoke the resolved recorder with the existing run folder. Do not launch a new review.
Call it once per close event, including eligible stopped and failed runs.
Unkeyed observations cannot establish misses, recall rates, or a winner.
Record configuration differences, delivery limitations, and coverage gaps as limitations.
Save the durable output path after publication succeeds.
Keep retry inputs when publication fails.
Cleanup follows artifact-closure only after every required recorder succeeds.
Reuse the run id and initial evidence on continuation.
Each new candidate receives a new round with its own prompt and reports.
Keep earlier rounds, reviewer identities, prompts, rules, and original reports.
Save supplements as separate reviewer entries. A repeated configuration does not increase eligibility.
Update triage and fix dispositions without deleting original finding attribution.
The recorder preserves prior published evidence when a continuation adds new evidence.
It never deletes the source run folder.
