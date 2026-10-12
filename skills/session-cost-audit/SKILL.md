---
name: session-cost-audit
description: Analyze the token and dollar efficiency of a current or named Claude Code or Codex session. Use when the author asks why a session cost so much, whether its requests were efficient, where context grew, or how to reduce similar spend.
---

# Session Cost Audit

Diagnose recorded spend. Do not judge work quality from cost alone.

## Scope

- Audit the current session when its exact ID is available.
- Audit another session when the author supplies its ID.
- Ask for the session ID only when neither source identifies one exactly.
- Use `preplan` after this audit when the author asks how to run future work.

If the working repository contains `docs/agent-cost-guide.md`, read it before interpreting usage.

## Collect evidence

Use Arc's bundled adapter first. `$SKILL_DIR` is the harness-reported directory that holds this file. The adapter selects `ARC_STATUS`, `ARC_REPO`, an Arc checkout that `npm link` put anywhere on `PATH`, the sibling Arc checkout, or the bundle of the editor extension's `arc` command. It refuses any other `arc` on `PATH`, because it cannot name the build that command runs. Run `npm link` in an Arc checkout's `cli` folder to make that checkout the default. When a checkout's HEAD moved past its build, the adapter rebuilds it first. It leaves the build alone when `cli` or `extension/src` holds uncommitted changes. If Arc is unavailable, follow its build instructions. AgentsView serves only session discovery.

```bash
python "$SKILL_DIR/scripts/arc_cost.py" 'SESSION_ID_OR_TRANSCRIPT_PATH'
```

`summarize_usage.py` and `transcript_usage.py` are compatibility command names for the same Arc schema 2 JSON. Recognized `claude:UUID`, `claude-code:UUID`, and `codex:UUID` discovery prefixes are accepted. Their legacy report shapes and exported parser and rate helpers are retired. Arc's `parent`, `children`, and `combined` replace subtraction-derived delegated costs. Per-request and bucket prices are retired.

Arc reads Claude Code and Codex transcripts, includes linked subagents, and reports request, token, context, and tool-call usage. `costUsd` is the complete list-price estimate when coverage and prices are known. Otherwise it is null, and `pricedCostUsd` is the known priced subtotal. An empty request window omits both fields when no usage evidence remains. `recorded-reconciled` identifies reconciled recorded usage. `recorded-gaps` identifies missing recorded usage. Other coverage states retain unresolved evidence. Exact recorded request identity never proves the provider logged every request. No provider bill validates Arc's five-minute cache-write price. Name that limit when a session used the tier.

A session often holds more than one task. To price one task alone, pass its start and end times:

```bash
python "$SKILL_DIR/scripts/arc_cost.py" 'SESSION_ID_OR_TRANSCRIPT_PATH' --since 'START_TIME' --until 'END_TIME'
```

Each time is ISO 8601 with a `Z` suffix or an explicit offset, such as `YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS+02:00`. `--since` includes its time and `--until` excludes it. Either option works alone. Request figures count only requests inside the window, in the parent and in every subagent. Codex `failedToolCalls` counts `item_completed` events by their own timestamps in parent and child transcripts. The report retains intersecting coverage defects even without a selected bill or failure, and it echoes the window in UTC. Read the times from the parent transcript entries that open and close the task. The report lists subagents in order of their first request. Separate tasks with a window, never with positions in that list.

## Cost by tool kind

Add `--by-tool-kind` to attribute each priced Claude request's cost to the tool results it carries for the first time: `git`, `file read`, `search`, `test or build`, `subagent launch`, `other`, or `no tool result` for a request that carries none, such as the first request or a user prompt. A request that carries several new results at once splits its cost evenly across their kinds. Read `byToolKind.rows`. The `requests` figure in each row counts a request once for every kind it touches, not a fraction, so the per-kind counts can sum to more than the report's total request count. Point the target directly at one `subagents/agent-*.jsonl` file to price that subagent's own tool-kind costs. A Codex transcript reports every identified billed request as `no tool result`, because its billing records do not identify tool results.

Add `--until-text REGEX` alongside `--by-tool-kind` to also report `untilText.byToolKind` before the first match of the regex (case-sensitive, multiline), whether that match sits in an assistant text block or inside a string field of a tool call's own input, such as a hand-back tool's message field. This measures gathering cost before a reviewer's first finding, including a finding a reviewer states only inside its own hand-back call. When nothing matches, `untilText.matched` is false and the report counts every request.

Add `--window-agent AGENT_ID` to price a parent transcript's own requests, never a subagent's, over one window: it starts at the parent request whose Agent tool call launched the subagent named `agent-AGENT_ID.jsonl`, and it runs through `--until`, read inclusive for this window even though the plain `--until` above is exclusive. Read `parentWindow` for requests, cost, and context at the start and at the peak. Add `--by-tool-kind` for `parentWindow.byToolKind` over the same requests. The command exits non-zero with a clear message when the agent id is not found.

Use `agentsview session list --resume` only when an exact ID is unavailable. Match the working directory and start time. Label the match as inferred.

To find Codex sessions that used a given skill, search tool-call arguments for the names of the skill's scripts. Do not search user messages or the skill's path. A user message often does not name the skill. Every Codex thread lists every available skill in its developer message, so a search for a skill's path matches every thread.

When you inspect a Claude transcript directly, group repeated entries by `message.id`. Use `uuid` when the message id is absent. Otherwise use timestamp plus model. Arc keeps the first repeated parent record and the latest repeated child record. Union tool calls across records of the same request. Use Arc's cost report for prices. Counting every entry as a request misstates tool batching.

Read the source transcript only when aggregate usage cannot explain request timing, context growth, tool amplification, compaction, or message delivery. Prefer one bounded extraction over repeated searches.

## Cache expiry

The `cacheExpiry` object counts a request that paid cold input after an idle gap reached the lifetime of the cache before it. Report `returns` and `inputCostAfterExpiryUsd` together, and take the combined pair from `combined.cacheExpiry`. When `coverage` is `unknown-request-coverage`, the returns are observations and the combined input cost is null.

That dollar figure is the uncached input and the cache writes the return request recorded. It leaves out cache reads and output. It is the cost observed after an expiry, so never write that the gap caused it. Name the gap and the cost as two separate facts.

Each pair takes its lifetime from the earlier request. A single recorded cache-write tier names it, which is how a Claude pair answers. Generic Codex cache-write counters name no lifetime. The model's documented lifetime answers for recorded reads or generic writes where known. GPT-6.1 Sol on Bedrock has no documented fallback lifetime. No Claude model documents one.

`byLifetime` splits the returns by the lifetime each pair resolved to. `combined.cacheExpiry` carries the same split across the parent and every subagent.

`byBoundary` splits the returns three ways. `afterAUserTurn` counts a gap the author opened between two of their own prompts. `insideOneTurn` counts a gap inside one run. `turnUnknown` counts a return whose transcript records no prompt the author saw.

Report `unavailableLifetime` and `excludedAmbiguous` beside the returns, and never fold either into the count. `unavailableLifetime` counts a pair with no lifetime to measure. Its earlier request recorded two tiers, or cached nothing, or ran a model with neither a record nor a documented figure. `excludedAmbiguous` counts a pair that changed model or crossed a compaction.

Read `cacheExpiryNote` before you write a recommendation. It names what the runner failed to record.

## Analyze

Report these measures when the records provide them:

1. Total cost, identified request count, and priced coverage. Report `costUsd` only when it is non-null. If `coverage` is `billed-with-unknown-coverage`, `pricedCostUsd` is a lower bound for identified Codex bills, even when `unpricedRequests` is zero. If `coverage` is `legacy-snapshots`, `estimatedCostUsd` is a snapshot estimate without a known bill. If `requestCountExact` is false, treat `requests`, context averages, and request cost averages as incomplete. Report `unpricedModels` and `fastModeUnpricedRequests` as pricing gaps. Bedrock and Vertex Claude ids lack rates and appear in `unpricedModels`. `inference_geo` values `us`, `us-only`, and `in-region` identify in-region Claude requests at either speed, in any letter case. Arc prices those requests at 1.1 times their base rate. A message id starting with `msg_bdrk_` identifies Bedrock. Arc prices it at 1.1 times the base rate unless a preceding `global.` model attachment replaces that charge. `fastModeUnpricedRequests` includes an unpriced fast request or another nonstandard speed such as `turbo`.
2. Uncached input, cached input reads, cache writes, output, and thinking tokens.
3. Parent usage and linked subagent usage.
4. Average and maximum context per request, and Arc's request cost averages. Context predicts cost. Request count on its own does not: one measured pair of runs had the arm with fewer requests and better batching cost 13 percent more, because it carried more context.
5. Repeated tool output multiplied by later requests that carried it.
6. Cache-expiry returns and the input cost recorded after them.
7. Compaction, handoff, steering, queueing, and interruption boundaries.
8. Model and reasoning effort compared with the work performed.
9. Failed tool calls, from `failedToolCalls` and `failedToolCallCostUsd` in parent, child, and combined summaries. Claude charges a failure to the request that issued the call. That dollar figure prices only that request. Codex marks a failure on the `item_completed` event the call produced. That event names no request. Codex failure cost is zero when no call failed and null when a failed call cannot be charged to a request.

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

Take every dollar figure from Arc. Use token-only counterfactuals when Arc provides no monetary estimate. Keep tokens, dollars, and request counts distinct.
