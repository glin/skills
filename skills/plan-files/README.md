# Why plans record what they record

A plan serves six jobs for a later session that never saw the conversation. The jobs are: recall decisions, resume work, learn from mistakes, learn model behavior, check reviews, and clean up. Each record below serves one or more of those jobs. A record that serves none of them does not belong in a plan.

[SKILL.md](SKILL.md) owns the template and the rules. A plans repository can add its own cleanup guide, such as a `CLEANUP.md` at its root. This file explains the reasons and repeats no rule.

## Records in the plan file

| Record | Jobs | Why it is kept |
| --- | --- | --- |
| Owner requirements, quoted | Recall decisions, resume work | Code shows what was built, never what the owner asked for or approved. A quote with its source separates an owner decision from an agent's guess. |
| Decisions, with each rejected approach and its reason | Recall decisions | Without the reason, a later session proposes the rejected approach again, or reads old transcripts to find out why it lost. |
| `Authored:`, `Author session:`, `Baseline:` and commit trailers | Learn model behavior | The session identifier joins the plan to the model, effort, cost and transcript of the work. The baseline names the code the plan was written against. |
| Status and Steps | Resume work | A later session needs to know what landed, with its commits, and what is still open. |
| A trial's starting measurements, audit command and audit date | Learn from mistakes | An audit of a workflow change needs the numbers from before the change and the conditions they came from. Data in a temporary folder does not last. |
| Runtime resources | Clean up, resume work | A worktree, process or file outside the plan cannot be found again from tracked files alone. The row says who owns it and when it can go. |
| Cleanup condition on a `Retained` row | Clean up | A condition that no session can check keeps the item forever. A date or a checkable event lets a cleanup release it. Lasting evidence names its reader instead. |
| Verification | Resume work, check reviews | The commands and checks that proved the work let a later session repeat the proof. |

## Records beside the plan file

- **Run folders** hold a review or ship run: the brief, the prompts, reviewer reports, blind copies, the triage, the cost and the run state. Review triage and the review-record scripts open the reports, keys and triage by path. Those files stay after the run lands, so a later session can check how a review went.
- **Run state stays with its plan.** One move then archives the plan and all of its runs together.
- **Retrospectives** record a failure, why the checks missed it, and the guard that now prevents it. The guard changes how later sessions work. The account explains the guard.
- **Handoffs, mockups and research** stay inside the plan folder that owns them, for the same single move.

## What does not belong

- Dependency trees, build output and copied checkouts. A package install or a branch rebuilds them.
- Raw transcripts and event streams. The session identifier already points at them.
- Bulk that no plan, record or script reads. It costs disk and search time and serves no job.
