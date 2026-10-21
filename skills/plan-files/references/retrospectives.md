# Retrospectives

A retrospective is a durable record for later process analysis. It is not an active plan, a chronological diary, or a shared ledger containing unrelated incidents.

## Location

Give every retrospective its own dated directory:

```text
.claude/plans/retrospectives/<YYYY>/<MM>/YYYY-MM-DD-<slug>/
|-- retrospective.md
`-- ... optional durable evidence
```

Use the date the retrospective started. Never rename it to the date of a later correction. Keep supporting evidence inside the retrospective directory. Do not create one repository-wide retrospective ledger.

The `retrospectives/` directory is outside the active-plan scan. Do not move completed retrospectives into `archive/`.

## Template

```markdown
# Retrospective: <Title>

**Date:** YYYY-MM-DD
**Status:** Closed
**Authored:** <exact runtime model id> (<exact runtime effort>)
**Author session:** <exact runtime session id>
**Baseline:** <full commit SHA>[ + dirty]
**Source session:** <session id that contains the incident, when known>
**Timeline label:** regression | retrospective | <owner label>
**Tags:** `<searchable-tag>`, `<searchable-tag>`

<One paragraph that identifies the incident and its effect.>

## Expected behavior

<The requirement or owner intent that the incident violated.>

## Sequence

1. <The request, decision, failure, discovery, and recovery in order.>

## Responsibility

<Separate the owner's instructions from agent decisions and external causes.>

## Why guards failed

<Explain why tests, review, documentation, or process did not catch the problem.>

## Corrective guards

- <A shipped guard, or a linked active plan for open corrective work.>

## Questions for later analysis

- <A query that can find similar failures across retrospectives or transcripts.>
```

## Rules

- Keep the account factual and attributable. Quote or link the source session, commits, plans, and review reports when available.
- Separate an owner request from an agent inference. Do not assign fault from the outcome alone.
- Record the first bad commit and corrective commits when known.
- Use searchable tags for the failure mode, affected surface, review state, and guard gap.
- Record misleading completion or review claims separately from the product defect.
- State which tests passed and why they failed to protect the requirement.
- Name shipped guards as completed facts. Link unimplemented work to an active plan with its current status.
- Preserve exact model and session provenance through the same rules as plan files.
- Use the embedded plans repository when one exists. Never force a retrospective into the enclosing project's ignored history.
- Use semantic line breaks and keep each incident in its own dated directory.
