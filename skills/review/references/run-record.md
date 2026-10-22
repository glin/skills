# Review record

New records use `source: review`. Readers also accept the historical `source: pre-commit-review` value. Keep historical records unchanged.

## Contents

- [Record files](#record-files)
- [Report delivery](#report-delivery)
- [Bounded triage updates](#bounded-triage-updates)

## Record files

An author can opt in by saying "save the review record" or "keep a machine-readable review record". Record only an immutable candidate commit. Without an explicit file path, save the schema-2 JSON in the reviewed repository's `.claude/review-records/` directory. An explicit file path overrides that directory. Use `YYYY-MM-DD-<project>-<first eight candidate SHA characters>-<UTC HHMMSS>-<16 random lowercase hex digits>.json` for either location. The record has one round per reviewed commit and one entry per finding. A review of uncommitted changes writes no record and reports that result. Without opt-in, write no record.

Keep the run folder outside the reviewed worktree. Save every reviewer prompt and report there. The record writer checks the report's final call against the triage, so keep the reports until it succeeds. A caller can retain the run folder longer when it needs the raw review evidence.

```text
<run folder>/
  run.json
  round-1/
    prompt.md
    round.json
    key.json
    triage.json
    reports/A.md
  round-2/ ...
```

`run.json` names the first reviewed commit. Use the repository's absolute path. Reserve a UTC run id before launching reviewers, and keep it for every round. Set `landed` to a full commit SHA only after the change lands. Use `null` otherwise. When saving the first reviewer prompt, capture the source skill's full commit SHA as `skill_version`. For a source checkout, use `git -C "$SKILL_DIR" log -1 --format=%H -- SKILL.md rules`. For a copied installation without Git history, supply its source commit SHA explicitly. The writer discovers the version from a source checkout only when `skill_version` is absent. The writer computes the prompt hash from the saved prompt.

The published record's `repository` field depends on the destination. A default record uses a path relative to the record's parent directory, such as `../..`, which resolves to the reviewed repository after the directory moves with it. A record written through `--out` keeps the absolute repository path for an external ledger.

```json
{
  "date": "2026-09-27",
  "run_id": "120000-0123456789abcdef",
  "project": "example-project",
  "repository": "/path/to/repository",
  "skill_version": "<full source skill commit SHA>",
  "plan": null,
  "base": "<full commit SHA>",
  "candidate": "<full commit SHA>",
  "landed": null,
  "judge": { "model": "<coordinator model>", "blind": false }
}
```

Each `round-N/round.json` holds `{"base":"<full commit SHA>","candidate":"<full commit SHA>"}`. The first round matches `run.json`. Each `key.json` maps one capital letter to a reviewer. The `report` path is relative to the round folder. Each report ends with `APPROVE`, `COMMENT` or `REQUEST_CHANGES` on its final line. Record measured counters when available, and use `null` for unavailable counters.

```json
{
  "A": {
    "report": "reports/A.md",
    "model": "<model>",
    "effort": "high",
    "harness": "<runner>",
    "session": "<session id or null>",
    "cost_usd": null,
    "minutes": null,
    "requests": null,
    "output_tokens": null,
    "thinking_tokens": null,
    "tool_calls": null,
    "shell_calls": null,
    "shell_denied": null,
    "report_delivery": {
      "selected": "file",
      "actual": "text",
      "capture": "transcribed-text",
      "failure": "report-write-denied"
    }
  }
}
```

The coordinator publishes `triage.json` through the shared writer after checking the reports. Merge duplicate findings, keep rejected and deferred findings with their reasons, and name every reviewer who reported each one. Use `disputes_brief: true` only for an accepted finding that quotes a false factual claim in the brief. Give each accepted finding a full `fix_commit` when `landed` is set.

```json
{
  "calls": { "A": "REQUEST_CHANGES" },
  "findings": [
    {
      "found_by": ["A"], "file": "src/example.py", "line": 12,
      "quote_ok": true, "reported": { "A": "WARNING" },
      "severity": "medium", "verdict": "accepted", "reason": "",
      "fix_commit": null
    }
  ]
}
```

Write the record after the last review round and before removing the run folder. The writer derives the default directory and canonical filename from `run.json`. The writer refuses to overwrite a record or write one for a noncommit candidate. It validates the complete record before publishing it through a temporary sibling. A failed write leaves the destination absent, so the same command can retry. On failure, keep the folder and report the command error. Git Bash drive paths such as `/c/Users/...` work for all path arguments on Windows.

```bash
python3 "$SKILL_DIR/scripts/write_record.py" <run folder>
python3 "$SKILL_DIR/scripts/write_record.py" <run folder> --out <record file>
```

## Report delivery

`report_delivery` is optional in each key entry. A key entry carries all four fields or none. An entry without it, and every record written before it existed, reads as unknown delivery. Do not rewrite older records. Write it into `key.json` before `triage.py initialize`, because later triage updates check the key file's digest. A later key change follows the snapshot and `publish-key` steps of the triage helper. Delivery fields do not change triage identity.

| Field | Values |
| --- | --- |
| `selected` | `file` or `text`, chosen before launch |
| `actual` | `file`, `text`, or `none`, naming the canonical report's source |
| `capture` | `reviewer-file`, `exported-text`, `transcribed-text`, or `none` |
| `failure` | `null`, `report-write-denied`, `report-write-unavailable`, `unexpected-text`, `output-contract-violation`, `missing`, `empty`, or `truncated` |

`capture` follows `actual`: `reviewer-file` for a file, `exported-text` or `transcribed-text` for text, and `none` for no report. `exported-text` needs a mechanical harness export. A copy the coordinator writes is `transcribed-text`. Each failure belongs to one change of mode, as the table below shows. Matching modes have no failure. The writer checks these rules and copies the four fields into the published reviewer entry. A recorded reviewer has a report, so the writer rejects `actual: none`. Leave a reviewer with no report out of `key.json`. Its delivery stays in the comparison reviewer entry when one exists.

| Failure | Modes | Event |
| --- | --- | --- |
| `report-write-denied` | `file` to `text` | The harness refused the report write |
| `report-write-unavailable` | `file` to `text` | The reviewer had no tool or scope for the write |
| `unexpected-text` | `file` to `text` | File output returned the report as text and named no error |
| `output-contract-violation` | `text` to `file` | Text output wrote a file |
| `missing`, `empty`, `truncated` | either to `none` | No complete report arrived |

`report-delivery.json` in the run folder holds only `report-write-denied` and `report-write-unavailable` failures. The coordinator reads it before each launch, as [Report delivery](../SKILL.md#report-delivery) describes. Paths are relative to the run folder. `destination` is the naming pattern, with `<N>` and `<label>` standing for any round and reviewer.

```json
{
  "failures": [
    {
      "harness": "<harness and version>",
      "route": "native",
      "destination": "round-<N>/reports/<label>.md",
      "failure": "report-write-denied",
      "error": "round-1/reports/A.delivery-error.txt"
    }
  ]
}
```

## Bounded triage updates

After successful record publication, settle owned inputs through [../../references/artifact-closure.md](../../references/artifact-closure.md) before retirement. Preserve all retry inputs on publication failure. Record opt-in remains unchanged.

Use `references/scripts/triage.py` from the explicit shared skills root. Default `TRIAGE_SHARED_ROOT` to the owning skill's text-derived `SKILLS_DIR`. For copied installations, set `TRIAGE_SHARED_ROOT` to the installed shared skills directory before running these commands. Export it so the recorder uses the same root. Initialize with the current key SHA-256 and `--expected-target absent`. Every later mutation requires current `--expected-key` and `--expected-target` SHA-256 digests. `calls` updates a subset of known labels. `findings` accepts the complete finding list on stdin, including later fix commits. Calls must be primitive strings. Final recording requires complete calls. Each control JSON must fit within 1 MiB.

```bash
export TRIAGE_SHARED_ROOT="${TRIAGE_SHARED_ROOT:-$SKILLS_DIR}"
python3 "$TRIAGE_SHARED_ROOT/references/scripts/triage.py" initialize "$ROUND" --expected-key "$KEY_SHA256" --expected-target absent
python3 "$TRIAGE_SHARED_ROOT/references/scripts/triage.py" calls "$ROUND" --expected-key "$KEY_SHA256" --expected-target "$TRIAGE_SHA256" --call A=REQUEST_CHANGES
python3 "$TRIAGE_SHARED_ROOT/references/scripts/triage.py" findings "$ROUND" --expected-key "$KEY_SHA256" --expected-target "$TRIAGE_SHA256" < findings.json
```

Identity binds labels to the reviewed candidate, normalized original report reference, and report SHA-256. Counters and session metadata do not change it. The recorder rejects mismatches before using calls or findings. Bounded legacy triage remains readable. Updates require explicit `adopt --verified-identity <checked semantic digest>` after checking labels against original reports. Run `identity` to obtain the projection and digest. Supply both expected file digests for adoption.

Use one coordinator per round. Participating writers share a lock and reject observed digest changes. External writers that ignore the lock can write after the final check. Copied recorders require the shared skills installation. Set `TRIAGE_SHARED_ROOT` to its skills directory when no references sibling exists. Missing helpers fail explicitly. Recording remains opt-in.
