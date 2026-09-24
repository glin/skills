# Session topology and return paths

Read this reference only when the execution brief considers more than one agent session.

## Terms

Use **subagent** for a child inside the active agent run. The parent stays responsible for the result and receives the child's report in its own context. This matches the manager pattern, where one agent retains control and calls another for bounded work.

Use **fresh-session handoff** for an independent session that receives a bounded prompt and writes a durable commit or report. The local `/dispatch` skill launches this shape. Do not call it a dispatched subagent because the worker is not a child of the originating model run.

Use **handoff** without qualification only when the runtime transfers the active conversation to another agent. The OpenAI Agents SDK uses the term this way. Its handoff stays inside one run and makes the specialist active.

Sources:

- OpenAI Agents SDK orchestration: `https://openai.github.io/openai-agents-python/multi_agent/`
- OpenAI Agents SDK handoffs: `https://openai.github.io/openai-agents-python/handoffs/`

## Choose by where the result must go

Use a subagent when the active parent needs the result to decide what to do next. Examples include one bounded search, one invariant check, or one measurement whose answer changes the specification.

Use a fresh-session handoff when the worker can finish a coherent phase and leave a commit or report. The originating session needs only the final status and durable path. This keeps the worker's discovery, commands, failures, and raw report out of the parent's later context.

Use one fresh root session instead of either shape when the current conversation has little useful context and no parent decision remains.

## Require a return contract

Before launch, record:

- Originating session id and runner.
- Run id and task name.
- Worker session id when known.
- Prompt path.
- Result path.
- Worktree, branch, and expected commit state.
- Completion states: success, blocked, failed, or cancelled.
- The one notification mechanism that returns the result.

The worker writes status, commit, checks, review result, blockers, and next safe action atomically to the result path. The result file is authoritative. The notification carries only status and the result path.

## Avoid model polling

The launcher process waits for the worker outside the model loop. It sends one notification after the worker exits or reaches a terminal state. The originating model does not poll logs, agents, files, or process state.

Current Codex CLI exposes:

```text
codex queue --thread <session-id> --message <text>
```

Verify the command with `codex queue --help` before relying on it. A launcher can queue one completion message to an existing session on the shared app-server daemon. The message names the run status and result path. The local `/dispatch` launcher implements this for detached Codex workers through `--notify-thread <session-id>`.

Current Claude Code CLI help exposes background sessions, attach, logs, stop, remove, and resume. It does not expose a general cross-session queue command. Treat a result file as the return path unless the active runtime provides a verified callback or team-message channel.

## Autonomy limits

A fresh-session handoff still needs advance permission for its actual side effects. The launch prompt must name allowed repositories, worktrees, applications, commands, commits, branches, and landing targets.

Stop and notify the origin when:

- Existing changes overlap the intended edit and ownership is unclear.
- Required credentials or application state are unavailable.
- A product decision changes the result materially.
- Verification contradicts the specification.
- The next action is irreversible and was not authorized.

Do not broaden permissions merely to prevent a callback from asking for help.
