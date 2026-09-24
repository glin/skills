---
name: session-cost-audit
description: Analyze the token and dollar efficiency of a current or named Claude Code or Codex session. Use when the user asks why a session cost so much, whether its requests were efficient, where context grew, or how to reduce similar spend.
---

# Session Cost Audit

Diagnose recorded spend. Do not judge work quality from cost alone.

## Scope

- Audit the current session when its exact ID is available.
- Audit another session when the user supplies its ID.
- Ask for the session ID only when neither source identifies one exactly.
- Use `preplan` after this audit when the user asks how to run future work.

If the working repository contains `docs/agent-cost-guide.md`, read it before interpreting usage.

## Collect evidence

Use the bundled summary script first. It collects full and parent-only usage without placing thousands of request rows in the conversation. `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it.

```bash
python "$SKILL_DIR/scripts/summarize_usage.py" 'AGENT:SESSION_ID'
```

The script includes linked subagents, isolates the parent, and reports the difference as delegated usage.

Run the transcript script in every audit. It carries the only cache-expiry figure, and it reads a session AgentsView never indexed. Sessions started in a worktree, and sessions a launcher detached, are often missing from its index, and `agentsview session sync` cannot add them. These are exactly the sessions an orchestration run creates.

```bash
python "$SKILL_DIR/scripts/transcript_usage.py" 'SESSION_ID_OR_TRANSCRIPT_PATH'
```

The script reads Claude Code and Codex transcripts, includes linked subagents, and reports request, token, context, and tool-call usage. It reports cost only when all recorded models have a supported list rate. Its Opus 5 rates reproduced two recorded `total_cost_usd` figures to the cent. Its five-minute cache-write rate is validated by nothing, so say so when a session used that tier.

A session often holds more than one task. To price one task alone, pass its start and end times:

```bash
python "$SKILL_DIR/scripts/transcript_usage.py" 'SESSION_ID_OR_TRANSCRIPT_PATH' --since 'START_TIME' --until 'END_TIME'
```

Each time is ISO 8601 with a `Z` suffix or an explicit offset, such as `YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS+02:00`. `--since` includes its time and `--until` excludes it. Either option works alone. Every figure then counts only the requests inside the window, in the parent and in every subagent, except a Codex child's `failed_tool_calls`, which counts each `item_completed` event by that event's own timestamp rather than by request rows. The report omits a subagent with no request and no failure in the window, and it echoes the window in UTC. Read the times from the parent transcript entries that open and close the task. The report lists subagents in order of their first request. Separate tasks with a window, never with positions in that list.

## Cost by tool kind

Add `--by-tool-kind` to attribute each priced Claude request's cost to the tool results it carries for the first time: `git`, `file read`, `search`, `test or build`, `subagent launch`, `other`, or `no tool result` for a request that carries none, such as the first request or a user prompt. A request that carries several new results at once splits its cost evenly across their kinds. The `requests` figure in each row counts a request once for every kind it touches, not a fraction, so the per-kind counts can sum to more than the report's total request count. Point the target directly at one `subagents/agent-*.jsonl` file to price that subagent's own tool-kind costs. A Codex transcript reports every request as `no tool result`, because it does not record tool results the same way.

Add `--until-text REGEX` alongside `--by-tool-kind` to also report the by-kind cost of only the requests before the first match of the regex (case-sensitive, multiline), whether that match sits in an assistant text block or inside a string field of a tool call's own input, such as a hand-back tool's message field. This measures gathering cost before a reviewer's first finding, including a finding a reviewer states only inside its own hand-back call. When nothing matches, the report says so and counts every request.

Add `--window-agent AGENT_ID` to price a parent transcript's own requests, never a subagent's, over one window: it starts at the parent request whose Agent tool call launched the subagent named `agent-AGENT_ID.jsonl`, and it runs through `--until`, read inclusive for this window even though the plain `--until` above is exclusive. The report gives requests, cost, and context at the start and at the peak of that window, and combines with `--by-tool-kind` for a by-kind breakdown of the same requests. The command exits non-zero with a clear message when the agent id is not found.

Use `agentsview session list --resume` only when an exact ID is unavailable. Match the working directory and start time. Label the match as inferred.

To find Codex sessions that used a given skill, search tool-call arguments for the names of the skill's scripts. Do not search user messages or the skill's path. A user message often does not name the skill. Every Codex thread lists every available skill in its developer message, so a search for a skill's path matches every thread.

When you parse a transcript directly, price one `requestId` once and union its tool calls across every entry that shares it. A per-entry count reports a batching rate of zero.

Read the source transcript only when aggregate usage cannot explain request timing, context growth, tool amplification, compaction, or message delivery. Prefer one bounded extraction over repeated searches.

## Cache expiry

The `cache_expiry` object counts a request that paid cold input after an idle gap reached the lifetime of the cache before it. Report `returns` and `input_cost_after_expiry_usd` together, and take the combined pair from `combined.cache_expiry`.

That dollar figure is the uncached input and the cache writes the return request recorded. It leaves out cache reads and output. It is the cost observed after an expiry, so never write that the gap caused it. Name the gap and the cost as two separate facts.

Each pair takes its lifetime from the earlier request. A single recorded cache-write tier names it, which is how a Claude pair answers. Where that request recorded no tier and read a cache, the model's documented lifetime answers, which is how a Codex pair answers. Every OpenAI model documents a lifetime, and no Claude model documents one.

`by_lifetime` splits the returns by the lifetime each pair resolved to. `combined.cache_expiry` carries the same split across the parent and every subagent.

`by_boundary` splits the returns three ways. `after_a_user_turn` counts a gap the user opened between two of their own prompts. `inside_one_turn` counts a gap inside one run. `turn_unknown` counts a return whose transcript records no prompt the user saw.

Report `unavailable_lifetime` and `excluded_ambiguous` beside the returns, and never fold either into the count. `unavailable_lifetime` counts a pair with no lifetime to measure. Its earlier request recorded two tiers, or cached nothing, or ran a model with neither a record nor a documented figure. `excluded_ambiguous` counts a pair that changed model or crossed a compaction.

Read `cache_expiry_note` before you write a recommendation. It names what the runner failed to record.

## Analyze

Report these measures when the records provide them:

1. Total cost, request count, and priced coverage. When `unpriced_models` is not empty, report those models as a gap, because the priced total leaves out their spend. A Claude id in the Bedrock or Vertex spelling, such as `us.anthropic.claude-sonnet-4-5-20250929-v1:0` or `claude-sonnet-4-5@20250929`, has no rate and appears there. A Claude request whose `inference_geo` is `us`, `us-only` or `in-region`, in any letter case, ran in-region, and the script prices it at 1.1 times its rate at either speed. A Claude request whose message id starts with `msg_bdrk_` ran on Amazon Bedrock. The script prices it at 1.1 times its rate unless the latest `model` attachment before it names a `global.` profile, and that charge replaces the in-region one. When `fast_mode_unpriced_requests` is above zero, report that count as requests left unpriced at a speed other than standard. A fast request is priced at its model's fast-mode rate. This count holds every unpriced request whose recorded speed is not standard: a fast request on a model with no published fast rate, and a request at any other speed, such as `turbo`. The priced total leaves out their spend.
2. Uncached input, cached input reads, cache writes, output, and thinking tokens.
3. Parent usage and linked subagent usage.
4. Average and maximum context per request, and how cost per request moved across the session. Context predicts cost. Request count on its own does not: one measured pair of runs had the arm with fewer requests and better batching cost 13 percent more, because it carried more context.
5. Repeated tool output multiplied by later requests that carried it.
6. Cache-expiry returns and the input cost recorded after them.
7. Compaction, handoff, steering, queueing, and interruption boundaries.
8. Model and reasoning effort compared with the work performed.
9. Failed tool calls, from `failed_tool_calls` and `failed_tool_call_cost_usd`. Claude charges a failure to the request that issued the call. That dollar figure prices only that request, never a request before or after it. Codex marks a failure on the item_completed event the call produced, never on the call's own output record. That event names no request. A Codex transcript reports `failed_tool_calls` as a count. It reports `failed_tool_call_cost_usd` as null, because no request can be charged for it. Each figure covers one transcript, the parent or one child, never a combined total across a run.

Do not assume one visible turn equals one provider request. Do not assume a cache hit preserved every earlier token.

Before using a comparison as evidence, name every factor that differed. Call a confounded result suggestive, not causal.

## Find the main waste

Rank no more than three findings. Prefer findings that changed cost materially:

- Too many request boundaries against a large retained context.
- Context retained after its task stopped being useful.
- Large tool results carried through many later requests.
- Rapid messages delivered as separate turns or responses.
- Repeated retries without a new observation.
- Top-tier models used for mechanical work that a smaller model can complete.
- Delegation whose coordination cost exceeded its isolated work.
- Missing compaction or handoff at a real work boundary.

A high cached-input share can represent efficient reuse. It is not waste by itself.

## Estimate savings

Use recorded provider, model, route, and token categories. State when any rate or usage row is missing.

For each recommendation, give a bounded counterfactual:

```text
Observed: 12 requests reading about 100,000 cached tokens each.
Alternative: 4 requests with the same retained prefix.
Avoided work: about 800,000 cached-input tokens, before output and tool effects.
```

Never present API-equivalent estimates as subscription charges or invoices.

## Report

Lead with the largest verified cost driver. Then provide:

- The measured evidence.
- The smallest behavior change that addresses it.
- The estimated savings range.
- Important uncertainty or missing data.
- A compact-or-fresh-session recommendation only at a real work boundary.

Use exact dollar figures only when the records and current rate table support them. Keep tokens, dollars, and request counts distinct.
