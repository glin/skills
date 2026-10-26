---
name: keepalive
description: "Keep a top-level session's prompt cache warm while it waits hours for workers, an owner, or an outside job, at a bounded cost per tick. Use when: keepalive, keep the cache alive, wait for the owner, idle for N hours, wake me when a report arrives, /keepalive 3h. Covers a Claude Code session that wakes itself, and a Codex session that a Claude session ticks through Arc."
---

# Keepalive

A session that waits for hours pays one of two prices. A tick inside the cache lifetime reads the cache. A silent wait past the lifetime rewrites the whole context at the next request. This skill decides which is cheaper and runs the loop.

## Find the cache lifetime first

A Claude session's own loop only pays on a one-hour cache. Claude Code gives the one-hour cache to the main conversation on a subscription within plan limits. It gives the five-minute cache to a subagent, to an API key, to Bedrock, Vertex and Foundry, to a subscription in usage credits, and to a session that `dispatch` launched with neither `--cache-ttl 1h` nor a caller's `CLAUDE_CODE_PROMPT_CACHE_TTL=1h`. `~/.claude/docs/llm-cost-and-caching.md` owns that list.

Read the lifetime from this session's own transcript before the first tick. The `this-transcript` skill finds the file. The latest assistant message with a nonzero `usage.cache_creation` names the tier it wrote: `ephemeral_1h_input_tokens` above zero means the one-hour cache, and `ephemeral_5m_input_tokens` alone means the five-minute cache. On the five-minute cache, do not loop. A four-minute tick needs 15 requests an hour, so it passes the cost of one rewrite within one to three hours, depending on the model. A 45 minute tick misses the cache every time.

## When a loop pays

Count a tick in model requests. A tick whose only tool call is the wakeup is one request. A tick with one batched check and the wakeup call is two requests. One request reads the whole context at the cache read price, and a rewrite writes it at the one-hour write price. The ratio of the two sets the budget:

| Model | Read price over one-hour write price | Ticks of one request under one rewrite | Ticks of two requests |
| --- | ---: | ---: | ---: |
| Sonnet 5, Opus 5 | 0.05 | 19 | 9 |
| Opus 5.5 | 0.025 | 39 | 19 |
| Fable 5.1 | 0.0125 | 79 | 39 |

Use the 0.05 row when the model is not in the table. At one tick per 45 minutes, nine ticks cover 6.75 hours. Run the loop only while the ticks the whole wait needs stay under the row's count for this tick's request count. Past it, let the cache expire. End the session only when no worker reports to it.

Do not loop when the next task needs none of the context. End the session instead. The rewrite then never happens, because the next task starts fresh.

## Before the first tick

1. Commit or hand off every dirty tracked file.
2. Write a continuation file with the open work, each worker's id and expected report time, the rules in force, and the recap format for the owner's return. When the owner names a continuation file after the duration, update that file instead. Run `pre-compact` when findings live only in chat.
3. When the last request's input was above about 150,000 tokens, request one compaction. The keepalive request authorizes that one compaction. Inside an Arc window, use `arc compact request --self --after-turn` as the `arc-sessions` skill's compaction guide describes. Outside Arc, ask the owner to run `/compact` before they leave. Each tick re-reads the whole context, so a small context makes every tick cheap.
4. Tell each Claude worker to report by `SendMessage` to this session's name from `ListAgents`, in its brief. The report then arrives as a message, and the tick never polls a transcript. A Codex worker cannot send a message. Record its expected finish time, and read its result on the tick with the completion route its launch gave it.

## The loop

Use the `ScheduleWakeup` tool. Set `delaySeconds` to 2700 for a one-hour cache, with room for the model's own time. Pass the tick text below as the prompt. Set `noop` to true when nothing changed, so idle ticks collapse in the owner's view.

The argument gives the end time. `/keepalive 3h` ends three hours after the first tick. With no argument, end after three hours. The end time never exceeds the tick budget above.

Tick text, with the file path and the end time filled in:

```text
Keepalive tick. Follow <continuation file>. Read new messages. Stop the loop with stop true when the work it waits for is complete, when the owner wrote, or when <end time> passed. Otherwise make at most one batched tool call, which confirms the branch tip, reads each Codex worker's completion route once its expected finish time passed, and reads a Claude worker's transcript only when its report is 50 minutes past its expected time. Answer in one line. Schedule the next tick with noop true when nothing changed.
```

Check every stop condition before you schedule again. A tick that finds a report or a landing does that work, records it in the plan, checks the stop conditions, and only then schedules the next tick with `noop` false.

## Codex sessions

A Codex session cannot wake itself. GPT-6.1 Sol, GPT-6 Astra and GPT-5.6 cache for 30 minutes, GPT-5.5 typically for 30 minutes, GPT-5.4 for 5 minutes, and no flag changes that. `~/.claude/docs/llm-cost-and-caching.md` owns that list. Do not tick a Codex session on a five-minute cache.

A Claude session that runs this loop can tick an idle Codex session that the owner asked to keep warm. It sets its own `delaySeconds` to 1500 and counts its own ticks, now about every 25 minutes, against its row in the table above. Each Codex tick weighs against the Codex session's own rewrite, at that model's read and write prices. On each tick it sends one follow-up with a fresh request id:

```text
arc send CODEX_SESSION_ID --prompt-file tick.md --window WINDOW_ID --request-id WINDOW_ID:UUID --json
```

The tick file holds one line: `Keepalive tick. Reply with the single word OK.` Each tick is one Codex request at the read price. An unknown delivery outcome blocks every later follow-up to that session until the owner directs it in the panel, so on a refusal report it to the owner and do not retry.

## Related

- `coordinate` owns the worker assignments that this loop waits for.
- The "Waits and the cache lifetime" section of `references/owner-brief.md` owns the worker-side rules.
- `~/.claude/docs/llm-cost-and-caching.md` owns the prices and lifetimes this skill uses.
