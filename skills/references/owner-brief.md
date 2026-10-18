# Owner requirements in worker briefs

Every implementer, reviewer, consultant, and falsification worker receives the owner's relevant messages and their source references. Include later corrections and restrictions. Copy exact excerpts when their wording defines scope. Label summaries as summaries and keep the source available.

Separate owner requirements, explicitly approved agent proposals, and unapproved agent inferences. Quote the owner's approval and identify the proposal it approves. A plan or reviewer suggestion alone grants no authority.

Name the authorized outcome, exclusions, repository baseline, owned paths, checks, return path, and limits. Preserve these requirements when refreshing or narrowing a brief.

When a brief tells a worker how to launch a session or run a tool, write the full command line. Use only flags that the installed tool's `--help` lists. A flag copied from a sibling tool, such as `arc start --agent-run` given to `dispatch.py`, stops the worker until the owner answers.

Before proposing or adding a visible control, dialog, panel, or interaction, compare it with the owner's request. Reviewers perform the same comparison for every added surface. If an inferred requirement needs an unauthorized visible addition, pause that addition and request approval. Continue independent approved work.

## Session labels and links

For agent launches and fresh-session handoffs in Arc, inherit the launching session's current labels unless the author specifies another set.
Read the parent labels before launch and confirm the child's saved labels afterward through the scoped Arc CLI.
Use the installed `arc-sessions` skill for window selection, label changes and delivery outcomes.
Add inherited labels without removing unrelated labels unless the author requests an exact replacement.
Keep labels separate from the session name and reader or agent ownership.
Report launched sessions with named Arc session links when their destinations are verified in the invoking window.
Follow the installed skill's link syntax and identity checks.
Otherwise report the name, complete session ID and scope limitation as plain text.
Use the complete raw session ID from accepted launch metadata or scoped listing, without a provider prefix.
If Arc cannot confirm or save labels, keep the existing worker.
Record the pending metadata and report the limitation alongside the verified link or plain-text session identity.

## Waits and the cache lifetime

A Claude subagent's prompt cache lasts 5 minutes by default, and so does a Claude session that `dispatch` starts. A request after a longer gap rebuilds the whole context at the cache write price. That write costs 12.5 to 50 times the read price, depending on the model. Codex caches for a fixed 30 minutes. The full price table and the lifetime controls are in `~/.claude/docs/llm-cost-and-caching.md`.

Tell every worker that waits for a build, a test run or a measurement which of these rules it follows:

- A worker whose result is a file starts the long job in the background and returns. The coordinator receives the exit notice and launches a fresh worker to read the result.
- A worker that must wait in its own context ends each wait in under 270 seconds. Each return reads the cache and keeps it alive, at a small fraction of a rebuild. This is the one exception to the rule that a coordinator never polls, and it binds only a worker that cannot return before its result exists.
- A worker with an unavoidable wait of 5 to 60 minutes launches as `subagent_type: long-wait`, whose definition sets a 1-hour cache. One expected gap pays for the higher write price. A subscription in overage silently drops it to 5 minutes, so it still ends each wait in under 270 seconds when it can.

A coordinator cannot keep a worker's cache alive from outside. A PreToolUse hook refuses a subagent's foreground command with a timeout over 270 seconds, except in `long-wait`. A silent Monitor gets no such check. A worker blocked inside a tool call reads no message until that call returns. Do not set `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` or `subagentPromptCacheTtl` for one worker, because each applies to every subagent of the session. For a dispatched Claude session with long waits, set `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` in the environment of the `dispatch.py` command.

## Measured acceptance

When a brief asks for an acceptance rule that is frozen before candidate results, the worker does these steps before the freeze commit:

- Pilot every gated metric on the baseline. A bound must be wider than that metric's spread between two baseline launches, or the metric is reported and not gated.
- Prove in the pilot that each scenario runs the changed code, with a log line or a counter from the changed path.
- List each required scenario with its trigger, the work it runs and its evidence path. A scenario with no evidence blocks acceptance unless the owner changes the scope.
- Hold every load that moves a gated metric equal in both arms, including test runs from other sessions, and record that load in each launch.
- Retry a launch whose instrument failed, as for a machine failure. It never counts against either arm.

The coordinator then launches a read-only reviewer to check the protocol against this list. The freeze commit waits for that check.
