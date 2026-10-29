# skills

Personal agent skills for Claude Code and Codex. Each skill is a folder under [skills/](skills/) with a `SKILL.md`. Shared rules and scripts live in [skills/references/](skills/references/). [docs/orchestration.md](docs/orchestration.md) records why the orchestration rules exist.

## Plan and run work

| Skill | What it does |
| --- | --- |
| [preplan](skills/preplan/SKILL.md) | Chooses a low-cost way to run a task before the work starts. |
| [orchestrate](skills/orchestrate/SKILL.md) | Runs a repository task end to end: implement, review, fix, within fixed limits. |
| [delegate](skills/delegate/SKILL.md) | Splits a task into bounded subagent units and keeps bulk reads out of the parent. |
| [dispatch](skills/dispatch/SKILL.md) | Starts a fresh Claude Code or Codex session on a bounded task. |
| [consult](skills/consult/SKILL.md) | Asks several models about a hard problem, then attacks any proposed design. |
| [phased-plan](skills/phased-plan/SKILL.md) | Splits work that spans sessions into phases with durable handoffs. |
| [plan-files](skills/plan-files/SKILL.md) | Sets where plan and retrospective files live, their templates and how to archive them. |
| [keepalive](skills/keepalive/SKILL.md) | Keeps a waiting session's prompt cache warm at a bounded cost. |

## Review and clean up

| Skill | What it does |
| --- | --- |
| [review](skills/review/SKILL.md) | Runs isolated reviewers on a code change or a written plan. |
| [agent-ready-repo-audit](skills/agent-ready-repo-audit/SKILL.md) | Audits a repository for correctness risk, drift and safe parallel agent work. |
| [audit-agent-docs](skills/audit-agent-docs/SKILL.md) | Checks CLAUDE.md, AGENTS.md and SKILL.md files for bloat and stale references. |
| [test-gardening](skills/test-gardening/SKILL.md) | Prunes a test suite with coverage proofs and matched timing. |
| [clean-commits](skills/clean-commits/SKILL.md) | Rewrites a branch into a few logical commits before a pull request. |

## Write and explain

| Skill | What it does |
| --- | --- |
| [deslop](skills/deslop/SKILL.md) | Removes and prevents LLM-marker writing in code and docs. |
| [rewrite](skills/rewrite/SKILL.md) | Turns confusing or verbose text into short, readable text. |
| [teach](skills/teach/SKILL.md) | Explains code or a system so a developer can debug and extend it. |

## GitHub

| Skill | What it does |
| --- | --- |
| [draft-pr-description](skills/draft-pr-description/SKILL.md) | Drafts a pull request title and body without opening the PR. |
| [draft-gh-issue](skills/draft-gh-issue/SKILL.md) | Drafts an issue that matches the target repository's template. |
| [gh-attach](skills/gh-attach/SKILL.md) | Uploads images, videos or files to GitHub and returns markdown to paste. |

## Sessions

| Skill | What it does |
| --- | --- |
| [recap](skills/recap/SKILL.md) | Summarizes the current conversation, or weekly notes for a retro. |
| [reflect](skills/reflect/SKILL.md) | Suggests documentation updates from what a session learned. |
| [pre-compact](skills/pre-compact/SKILL.md) | Saves findings that exist only in chat before compaction. |
| [this-transcript](skills/this-transcript/SKILL.md) | Finds and reads the current session's transcript file. |
| [session-cost-audit](skills/session-cost-audit/SKILL.md) | Explains where a session's tokens and dollars went. |
