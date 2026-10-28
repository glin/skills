---
name: dispatch
description: "Start a fresh agent session on a bounded task and leave its result in a commit or report. This local convention is a fresh-session handoff, not a child subagent or a formal vendor handoff. Supports Claude Code and Codex. Use when: dispatch this, hand this off to a fresh agent, start a fresh session on this, run this in the background, open this in a window, detach this. For child work whose result must return to the active parent, use delegate."
---

# Dispatch: a fresh-session handoff

A subagent reports back into the session that spawned it, and that report is re-read on every later call. A fresh-session handoff reports into files and commits. The parent keeps none of its context. The local command for this convention is `dispatch`. Use it when the parent would only relay the result.

OpenAI's Agents SDK also uses the word handoff, but for a different mechanism. Its specialist takes over the active conversation inside one run. Here the worker is an independent session.

The skills repository's `docs/orchestration.md` records why each rule here exists.

Both launchers forward every launch to the Arc CLI. A reader-owned writable request starts in a ready Arc window through `arc start`. A reader-owned Claude request that `arc start` cannot keep starts nothing, as Reader-owned Arc sessions explains. Every other request starts through `arc run`, which launches the provider without a window and owns its permissions, supervision and receipt. Run `arc run --help` for its flags and refusals. The launchers map their own flags, check the prompt file, and print Arc's reply.

Read [../references/owner-brief.md](../references/owner-brief.md) before preparing any worker prompt. Apply it to initial briefs, review rounds, refreshed briefs, and native or dispatched consultation and falsification rounds.

## Completion routes

A fresh-session handoff needs a durable result and one selected completion route. The proven routes are waited and manual, plus the Claude idle notice below for a writable Claude worker under a Claude parent. Arc delivers no callback into the parent session. Both launchers refuse `--notify-thread` before launch.

- **Waited.** Launch in the foreground with `--arc-dispatch`, without `--wait`. The standalone route prints a launch ID and an observation command. Reader-owned Arc sessions states when the Arc route prints them. Save the launch ID and any printed log or result paths before starting observation. Run the printed observation command for your shell as a separate background command. `await:` is the Bash form. On Windows, Python also prints `await (PowerShell):` with its call operator and literal arguments. Both forms use `--wait-launch LAUNCH_ID`. Carry `--read-only` from a review launch to its observation command. Observation requires no runner, model, effort or prompt flags and starts no worker. It blocks until Arc publishes the worker's outcome, then prints the outcome and the worker's exit code when Arc records one. A writable observation also prints the report. A read-only observation serves a review, so it prints only the `result:` path and keeps the report text out of the caller's output until the caller blinds it. The launcher exits with a worker exit code from 1 to 255. Its own failures also exit 2, so read the `worker exit:` line before you act on exit 2. Otherwise it exits 0 when the outcome is `succeeded` and the worker exited 0 or Arc recorded no exit code, and 1 in every other case. Exit 0 therefore does not prove that the worker exited 0. An observation can also end at its deadline, as Observation deadlines below explains. The host must notify the parent when the observation command completes. A pending native identity or another Arc observation deadline keeps the same launch ID. Resume observation by that ID after an interrupted waiter. Never relaunch it. The worker keeps running when no waiter starts. This route serves any worker under any parent. Arc can publish an outcome while the worker still runs. A writable Claude worker that stays blocked or idle for 45 seconds publishes `needs-input`. Arc publishes `unknown` for causes such as an unreadable Claude registry, a worker that leaves it or never appears in it, an ambiguous Claude session identity, or a Claude worker that reports done without final response evidence. On any outcome other than `succeeded`, the launcher prints Arc's reason on stderr. If the worker still runs, run `arc dispatch attach LAUNCH_ID` to answer it. If it reported done, read its report only.
- **Manual.** Save the printed launch ID. Read `arc dispatch status LAUNCH_ID` for its events, then `arc dispatch result LAUNCH_ID --event EVENT_ID` for the frozen report. Use this route only when the parent turn ends and a later turn collects the result.

Direct launch with `--wait --arc-dispatch` remains available for compatibility. It waits before returning the marker. Do not background that whole launch when the parent needs the marker in its launch output.

### Observation deadlines

`dispatch.py` ends each observation command before the waiting parent's prompt cache expires, when the parent's cache lifetime is known. A parent whose environment sets `CLAUDE_CODE_PROMPT_CACHE_TTL` to `5m` or `1h` gets that lifetime minus 60 seconds, counted from the command's start. A Codex parent gets no deadline, because its code cell in Codex parents bounds each wake. A Claude parent without that variable gets no deadline either, because settings and billing mode also select its lifetime. The deadline bounds every Arc request of the observation, including the status and result reads after Arc publishes the outcome. `DISPATCH_WAIT_LIMIT_SECONDS` sets the deadline in seconds for any parent. A Claude Code session on a subscription can set it to 3300 for its 1 hour cache. In Bash, prefix the observation command with `DISPATCH_WAIT_LIMIT_SECONDS=3300`. In PowerShell, start it with `$env:DISPATCH_WAIT_LIMIT_SECONDS = '3300';`. Claude Code stops a background command at its own timeout, 30 minutes by default and 2 hours at most. A Claude parent sets that timeout above the deadline, such as 3600000 ms for 3300 seconds, and at its 7200000 ms maximum for an observation without a deadline. At the deadline the command prints `outcome: deadline` and the observation commands again, and exits 75. The worker keeps running. Run the printed command for your shell as your next background command. It carries the same launch ID and the `DISPATCH_WAIT_LIMIT_SECONDS` value, and a review observation keeps `--read-only`. A worker can also exit 75, so branch on the `outcome: deadline` line, never on exit 75. Under a 5 minute lifetime the parent wakes every 4 minutes, and after about 50 minutes those wakes cost more than one cache rebuild. A 5 minute parent that expects a longer wait sets `DISPATCH_WAIT_LIMIT_SECONDS` above the wait, or runs with a 1 hour lifetime. An observation without a deadline exits 2 after 25 hours with no outcome, because Arc's supervisor publishes an outcome within 24 hours of its start. `dispatch.sh` observes without a deadline, and `DISPATCH_WAIT_LIMIT_SECONDS` replaces its 25 hour bound.

### Codex parents

A Codex parent waits on the observation command inside one code cell. The cell returns to the model when the command exits or at its 1500000 ms yield, and each return costs one model request. A 3 hour wait therefore costs about 8 requests. When `CODEX_THREAD_ID` is set and `CLAUDE_CODE_SESSION_ID` is not, `dispatch.py` prints that cell after `await (Codex cell):`, up to the next empty line. Run the cell unchanged as one `exec` call. It starts the command with `exec_command` and reads it with `write_stdin` at a 300000 ms yield until the command exits. Codex ends each read after 300 seconds, and the loop starts the next read without a model request. The first line sets a 1500000 ms cell yield, under the 30 minute cache of current Codex models. The cell prints the command's output and its exit code. If the cell returns while the command still runs, call `wait` on that cell with `yield_time_ms` 1500000. Never read the command at a 60000 ms yield, because each read that returns costs one model request.

A Claude parent that launched a writable Claude worker can complete through `SendMessage` with `notify_when_idle: true`. `ListAgents` in the launching session lists that worker as a `bg` peer, and the parent receives a `[Cross-session idle notice]` when the worker's turn ends. Every other worker and parent uses the waited route. If the host has no automatic completion path, report that limit. Do not create an agent solely to wait, poll or relay. Native `wait_agent` cannot observe a detached CLI job. Never process one result through two routes.

## When to dispatch and when to fan out

Dispatch when the result is a commit and a note: a phase of a written plan, a verifier pass, a migration with a spec. Fan out with a subagent when the result feeds the parent's next step: research, a measurement, a batch of small edits the parent reviews. A large parent context makes dispatch cheaper still, because nothing comes back to be re-read.

Prefer native children when the harness supports the required model, provider, effort, speed, isolation, and role. Apply [model and provider rules](../references/model-providers.md) before selecting transport. Use dispatch for unsupported native combinations or required sandbox enforcement. A native child returns through harness completion only. It must not start a detached worker or independently repeat completion handling. A report-only prompt does not enforce filesystem read-only access.

## Codex service tier

Both launchers accept one optional Codex launch flag:

```text
--service-tier default|fast|priority|ultrafast
```

`default` explicitly selects Standard. `fast` aliases canonical `priority`. `ultrafast` requests Ultrafast when Arc confirms runtime support. Omission adds no override and inherits effective defaults. Empty, unknown, repeated, and non-Codex values refuse before launch. Observation through `--wait-launch` refuses tier selection.

Dispatch forwards the selection through hosted, standalone, read-only, and attended routes. An acknowledged hosted refusal permits the existing standalone route with the same tier. An older Arc that rejects the flag never authorizes removing it. Unknown hosted delivery still forbids another launch. Arc owns capability and accepted-tier validation. [Service-tier transport rules](../references/model-providers.md#service-tier) govern native inheritance and requested-speed preservation.

## The words

- "dispatch X to opus" starts a separate session on that model. The CLI route runs it in the background. The build each shorthand names is in [../references/models.md](../references/models.md).
- "dispatch X in a window" opens a reader-owned session in a compatible ready Arc window. The standalone route opens a terminal window.
- "with codex" or "with claude" picks the runner. Without a word, use the runner this session runs on.
- Pass `--owned` for a session the author directly requests, such as "dispatch X to opus". A coordinator that the author asks to launch is such a session. [Coordinator placement](../references/owner-brief.md#coordinator-placement) names its two launches. Reader-owned Arc sessions below states what it starts and what it refuses. Omit it for consultation members, reviewers, verifiers, and other workers launched within a workflow. A request to consult or review remains workflow work even when the author names the model. The flag marks reader ownership, not authorization to launch a worker. `--attach` still implies `--owned`.

## Steps

### Reader-owned Arc sessions

Both launchers prefer `arc start` for reader-owned writable requests when Arc is available, the selected window is ready, and the request fits its capabilities. `--attach` implies reader ownership and selects this Arc route when compatible. The Arc route starts a reader-owned session, which the owner continues in Arc's prompt box. A dispatch the owner requested never passes `--agent-run` to `arc start`. Dispatch reads `arc --help` once per launch and passes `--untagged` only when the installed Arc lists it. A successful Arc route prints `mode: Arc session`, window, request, name, and session ID. It creates no terminal window or log. Dispatch also reads `arc start --help` and selects waited completion only when it lists `--completion`. The launcher prints the launch ID and the observation command only when it selected waited completion and Arc returned a launch ID. `--wait` then blocks until Arc publishes the outcome. An accepted reply with no launch ID ends a `--wait` launch with exit 2, and the launcher starts no other worker. When Arc's accepted start reply supplies a string `link`, the launcher prints that value unchanged after `link:`. Copy it into your reply. When no `link:` line prints, report the name and ID as plain text. Read the `arc-sessions` skill before you act on an Arc outcome. It owns the start prompt rules, delivery outcomes, request ID retries, and start tags. `dispatch.sh` runs this route through `dispatch.py`, so it requires Python 3.10. Without it, `dispatch.sh` still refuses the Claude options below. It prints a local refusal for any other request and starts a standalone launch.

Window selection uses `ARC_WINDOW`, or exactly one live window with complete discovery. The window's first folder must match the resolved launch folder, including when `--cwd` is omitted. A folder mismatch, absent window, ambiguous selection, incomplete discovery, unready window, failed `arc --help` call, or `--wait` request to an `arc start` that lists no `--completion` produces an explicit local refusal before launch. Dispatch then starts a standalone launch through `arc run`. For a Claude launch, the refusal line says that the owner cannot send to that session from Arc. A writable Claude launch prints its attach command. `ARC_BIN` selects an explicit Arc executable or its JavaScript entry. Windows dispatch invokes the published Arc JavaScript entry through Node. An unsupported batch wrapper refuses locally because it cannot preserve literal arguments.

`arc start` cannot keep extra directories, an explicit log or a cache lifetime. A reader-owned Claude launch with `--add-dir`, `--log` or `--cache-ttl` refuses before dispatch contacts Arc. No worker starts, and the launcher exits 2. The refusal names the options and two relaunches. Remove the options to start an Arc session that the owner continues in Arc's prompt box. Or add `--attach` to keep the options in a terminal window that the owner types in. Take the relaunch that the owner's request decides, or ask the owner. Never remove a requested option without that decision. Never drop `--owned` to keep the options, because a launch without it files the session under Run by agents. With `--attach`, the same options start through `arc run` in a terminal window. A Codex launch with `--add-dir` or `--log` prints a local refusal and starts through `arc run`. Read-only requests always start through `arc run`. Reviewer and verifier launches remain nonowned.

Supported Claude permissions retain their requested value, with `auto` as the default. `dontAsk` and `bypassPermissions` have no Arc preset and refuse the same way. Codex `--full-access` maps to Arc's `never` preset. Codex's default workspace-write sandbox with no approvals and a Codex `--permission-mode` have no Arc preset. Each prints a local refusal and starts through `arc run`. Model and effort remain explicit requested values.

A prompt file that is not a regular file, exceeds 48 KiB, is empty, or begins with a slash command refuses the window route locally before dispatch contacts Arc. `arc run` applies the same prompt rules and refuses before it starts a worker.

An Arc `refused` acknowledgment ends a reader-owned Claude launch without `--attach` with exit 2, regardless of the process exit code. The launcher prints Arc's reason and two relaunches: change what the reason names, or add `--attach`. For a Codex or `--attach` launch, the acknowledgment permits a standalone launch before acceptance. Both accepted outcomes, including `accepted-with-errors`, end the dispatch in the Arc session. Unknown delivery, malformed output, missing acknowledgments, and timeouts after the start attempt never permit a standalone launch. A process exit code alone establishes no delivery outcome. A selected window that disappears after discovery leaves delivery unknown without a matching refusal acknowledgment. An unknown outcome prints `mode: Arc delivery unknown` and exits 2. Report the printed window, request ID, and diagnostics.

### Standalone handoff

1. **Write the prompt to a file.** Never inline it: a shell command near ten kilobytes fails before the shell reads it. The prompt carries, in this order:
   - The task and its limits. Name what not to start.
   - The decisions already made, and an instruction not to reopen them. The session sees the code but not the reasoning that shaped it.
   - Where the instructions live, as absolute paths: a handoff document if there is one, and the repository's conventions file. Absolute, because a worktree holds no ignored file and the agent starts with no context.
   - The working tree and its rules: the branch, commit but never push, no destructive git commands, no dependency install unless told.
   - What to verify and how, with the exact commands.
   - For ordinary dispatch, where to write results: the handoff document if there is one, else a results file beside the log. Then a closing summary shape.
   - For a controlled comparison, omit the result destination. Transport metadata assigns each launch a unique path so every reviewer receives identical prompt bytes.
2. **Choose the tree.** Edits go in a worktree whenever another session shares the checkout. The calling workflow creates it before launch with `git worktree add -b <branch> <main-checkout>/.claude/worktrees/<name> <base>` and passes it with `--cwd`. Both launchers refuse `--worktree` and `-w`, and Arc creates no worktree. A worktree holds no ignored file, so a dependency directory must be installed or linked there before the run. A writable Claude launch needs a folder that Claude trusts, and trust does not pass from a parent folder. A new worktree therefore fails until the owner runs `claude` there once and accepts the trust prompt. Arc then publishes `failed` with Claude's refusal as the reason. Codex refuses an untrusted folder outside a Git repository the same way. Never accept trust for the owner. One run owns one tree. A retry reuses it, or removes the old tree first.
3. **Launch `dispatch.py` from this skill's directory, under `python3`.** Read `references/session-naming.md` in `$SKILLS_DIR` once per invocation. Choose each launch's `--name` before launch. `--arc-dispatch` is required before any Arc launch. `--model` and `--effort` are required, because `arc run` refuses a launch without them. The launcher prints Arc's launch JSON line, then mode, runner, name, tree, prompt, launch ID, log, result, the status and observation commands, and either a watch command or, for writable Claude, the logs, attach and stop commands. With `--arc-dispatch`, Arc's `ARC_DISPATCH_SESSION=` marker is the last line of stdout, also after a failed wait or result read. When Arc accepts a launch that never opens, the launcher prints the launch JSON line and the status command, prints Arc's error on stderr, and exits 2. A waited launch whose provider fails to start publishes a `failed` outcome instead and exits 1. If `dispatch.py` fails before it reaches Arc, retry once with `dispatch.sh` and the same flags. `dispatch.sh` prints a writable launch's waited result as Arc's result JSON line instead of its text.

   `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it. `$SKILLS_DIR` removes that path's last component as text, before filesystem traversal. Never derive it through `..`.

   ```
   python3 "$SKILL_DIR/dispatch.py" --runner claude --arc-dispatch --model claude-opus-5-5 --effort high \
     --name code-implement-1-catalog-update-opus --cwd /abs/path/of/worktree \
     --prompt /abs/path/prompt.md --log /abs/path/worker.log --add-dir /abs/path/of/handoff/dir
   ```

   Run observation as a separate background host command after the foreground launch returns:

   ```
   python3 "$SKILL_DIR/dispatch.py" --wait-launch LAUNCH_ID
   ```

   Keep the marked launch command in the foreground. Every supported Claude or Codex launch command carries `--arc-dispatch`. Arc reads the marker only from the stdout of a command whose own text carries the flag. Arc waits up to 110 seconds for the accepted session identity and prints no marker when none arrives. Attended Codex launches identify no session and print no marker. A pending identity keeps the printed launch ID. Never start a replacement worker because its marker is absent. Waited runs frame receipt-shaped worker lines in the report, so only the launcher's last line carries the marker.

   Add `--attach` for a terminal window. For attended Codex, omit the explicit `--log` and use the opened window instead of this waited-observation example. Attended Codex saves no report and prints no session marker. For a compatible Arc window, follow Reader-owned Arc sessions without standalone-only flags. Add `--owned` when the author's own message asked for this dispatch, per "The words" above. `--attach` implies `--owned`, because a session opened in a window is one the author watches. Writable Claude background sessions default to `auto`. Its classifier handles routine shell work without waiting for a person and still refuses unsafe actions. Pass `--permission-mode MODE` only when the task needs another mode. `acceptEdits` still prompts for many shell commands, so it is not an unattended default. Add `--read-only` for a fresh analysis session that runs without permission prompts. Only a Codex session is guaranteed to change no file, as Read-only dispatch explains. Add one `--read-tool mcp__server__operation` for each exact connector read it needs. Verify that every named operation is non-mutating before launch. Add `--full-access` only for a Codex task that must commit, add a worktree, or run a browser test. It disables the Codex sandbox. The launching account and prompt rules still limit the session. For waited completion, run the printed observation command for your shell separately in the background. The launch and observation commands use the same `ARC_BIN` and `ARC_HOME` when set. Both launchers refuse `--wait` with `--attach` before any worker starts. A writable launch's report above 16,000 characters prints its path and its size in place of its text.
4. **Report the record** to the author with the status, watch, logs, attach and stop commands the script printed.
5. **Record the dispatch** in the handoff document if there is one: the same record, on one line, where the task is described. If there is none, the log and the prompt file are the record.
6. **Close** when the session ends, stops or fails. Remove its tree with [../references/scripts/remove-worktree.py](../references/scripts/remove-worktree.py) under `python3`: `remove-worktree.py --repository <main-checkout> <tree>`. Never use `git worktree remove` or a recursive delete, because either can follow a dependency link and delete the files behind it. The script removes a clean tree whose HEAD a branch carries, and it keeps a tree with uncommitted or untracked files. When the task wrote into a plan folder, list its large files with `find <plan-folder> -type f -size +2M -not -path '*/.git/*'`. Extract, gzip or delete each one under the plan convention's archive rule. Report each tree removed or kept, with the reason.

## A background session cannot move its own permission root

Auto mode handles routine shell work, but it will not approve a permission-root relocation, because that changes what the session may touch. The tool that switches a session into a worktree asks for it, the prompt waits, and a detached session has nobody to answer. The run stops with everything done and nothing delivered.

Give a writable session every directory it will need at launch, through `--add-dir`. A reader-owned Claude launch refuses `--add-dir` without `--attach`, as Reader-owned Arc sessions explains. Have it reach each directory with `git -C <path>` rather than by relocating. A worker that must land on a branch in the main checkout needs `--add-dir` on that checkout.

## Read-only dispatch

Use `--read-only` for reviews, audits, and other analysis whose full report stays in a file instead of returning to the parent conversation. The prompt must name the immutable commit to inspect. The launch metadata names where the report will be saved. Keep that unique path out of a shared controlled-review prompt. Do not use read-only dispatch for a moving working tree.

Arc applies the read-only restrictions. Codex runs in its `read-only` sandbox, which prevents writes. Claude runs in print mode with permission rules and auto mode instead. It preapproves `Read`, `Grep` and `Glob`, denies the edit tools and the other built-in mutation tools, and denies PowerShell. Bash runs under auto mode, whose classifier judges each command. A reviewer can run pipes, compound commands and git reads in any directory, such as `git -C DIR log`, `git show --stat SHA | cat` and `wc -l FILE`. Deny rules stop the git commands that write refs, configuration, the index, the working tree or the object store, such as `git commit`, `git fetch`, a bare `git stash`, `git add` and `git config`. Arc's launch policy holds the full list. A verb that also reads, such as `git stash list` or `git config --get`, is denied whole. `git branch`, `git tag`, `git remote` and `git notes` are denied with any argument. Read refs with `git for-each-ref` or `git log --decorate` instead. `git diff`, `git log` and `git show` are denied with `--output`, which writes a file. Each rule matches the plain form and the `git -C DIR` form. Another spelling, such as `git -c KEY=VALUE commit`, reaches the classifier instead. The classifier can approve any other Bash command that writes a file, such as a shell redirect, so Claude `--read-only` does not guarantee an unchanged tree. Use Codex `--read-only` when the task needs that guarantee. Tell a Claude reviewer in the prompt to change no file, because the classifier reads the conversation for boundaries.

Auto mode supports Haiku 5.5 and later Opus, Sonnet, and Fable models. Read [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes#eliminate-permission-prompts-with-auto-mode) for provider and CLI requirements. Arc runs an older model, such as Haiku 4.5, in `dontAsk` instead, with the same denials. Bash then runs one plain read at a time, such as `git log`, `git show --stat HEAD` or `git rev-parse HEAD`. A pipe, a chain and `git -C DIR` are denied. Choose a model with auto mode for a review that needs them.

The launch loads no settings files and disables hooks, through `--setting-sources ""`, `--strict-mcp-config` and `--settings`. An allow rule in a settings file would skip the classifier, so a rule such as `Bash(npm run build *)` would let the session write. The empty setting sources also drop the settings' `env`, model and login helpers, so pass `--model` explicitly. With `--read-tool`, the launch stays in `dontAsk` and denies Bash and PowerShell outright, so that session cannot run git. Arc passes only the MCP servers that the named tools need, from the user and project MCP configuration. A server outside that configuration, such as a claude.ai connector, refuses the launch. `dontAsk` denies every MCP operation not named with `--read-tool`. Put the diff in the prompt, or split the work. A read-only run writes no plan file, unlike plan mode. Name the allowed repository roots in the prompt, and pass each root outside `--cwd` with `--add-dir`.

The empty setting sources also stop every `CLAUDE.md` from loading, the project file and the user file alike. Auto-memory still loads. On Claude Code 2.1.280, a `claude -p` launched with `--setting-sources ""` could not name a fact that only `CLAUDE.md` holds, and the same call with `project` could. A read-only review therefore runs without the repository's conventions unless the prompt carries them. When the result will be compared with a CI run, paste the root `CLAUDE.md` into the prompt, or the two runs differ in more than the thing under test.

```
python3 "$SKILL_DIR/dispatch.py" --runner claude --read-only --arc-dispatch --model <model> --effort high \
  --name code-review-1-catalog-update-opus --read-tool mcp__example__get_item --read-tool mcp__example__list_items \
  --cwd /abs/path/to/repo --prompt /abs/path/review-prompt.md --log /abs/path/review.log
```

Codex writes its final message beside the log, at the log's stem followed by `.last.md`. Claude print output, a JSON result, goes to the log, so the log is also the result file. Claude print output is buffered until completion, so Arc refuses `--attach` for that combination. Arc chooses the Claude print session ID before launch and records it in the receipt.

## Claude runner

Proven on Claude Code 2.1.257, launched from inside another Claude Code session.

Both launchers set `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` for a workflow worker's Claude launch through `arc run` unless the caller already set it, and Arc passes it to the provider. A reader-owned launch through `arc run`, such as an `--attach` launch or the launch after the window route refuses, keeps the caller's value or the provider default. `--cache-ttl 5m` or `--cache-ttl 1h` replaces either value, and a Codex launch refuses the flag. A launch through `arc start` keeps the provider default, which is 1 hour on a subscription. On a subscription, Claude Code writes the main conversation's cache at 1 hour, which costs 2 times the input rate. A 5-minute write costs 1.25 times. The 1-hour lifetime pays off only when a person pauses 5 to 60 minutes between requests, because each such return rebuilds the whole cache at 5 minutes. A dispatched session runs without those pauses. Repriced at list price over 177 dispatched sessions, 5 minutes cost 7.9% less, and 94% of the sessions were cheaper. Read-only runs saved about 14%, and writable runs saved 4.8%. A writable run loses money when it waits on a permission prompt or on a follow-up message for 5 to 60 minutes. The same repricing made interactive sessions cost 21% more, so the setting stays in the dispatch launches and out of the Claude settings file. A workflow worker that waits on people or on long jobs between requests takes `--cache-ttl 1h`.

A writable launch runs Claude in background mode:

```
claude --bg -n <name> --permission-mode auto \
  --allowedTools "Bash Edit Write Read Grep Glob MultiEdit" --model <model> --effort <effort> [--add-dir <dir>]
```

Arc follows the background job until native terminal evidence and drains its log. Manage it through the launch ID, never the native background ID:

- `arc dispatch logs LAUNCH_ID` prints recent output.
- `arc dispatch attach LAUNCH_ID` opens the session in the current terminal. A permission prompt the flags do not cover waits there until someone answers it.
- `arc dispatch stop LAUNCH_ID` stops it and keeps the conversation. Stop does not establish worker exit.
- `arc dispatch rm LAUNCH_ID` refuses removal. Arc removes no directory, branch, transcript or receipt.

`--add-dir` is needed for every file outside the tree the agent must edit, such as a handoff document in the main checkout.

## Codex runner

```
codex exec -C <dir> -s workspace-write --json -c approval_policy="never" -c approvals_reviewer="user" \
  -m <model> -c model_reasoning_effort=<effort> [--add-dir <dir>]
```

Proven on codex-cli 0.151.0. `exec` is non-interactive and runs with approval `never`. Arc writes the final message to the log's `.last.md` file. The attended form runs interactive `codex -C <dir> -m <model> -s workspace-write` in a new terminal, with the prompt as its positional argument. It saves no log or report. Codex makes no worktree of its own: make one first and pass it with `--cwd`. With `--read-only`, Arc replaces `workspace-write` with `read-only`. With `--full-access`, it replaces it with `danger-full-access`.

## Terminal windows

Arc opens `--attach` sessions in a terminal: Windows Terminal with Git Bash on Windows, Terminal on macOS, and tmux, `x-terminal-emulator` or `gnome-terminal` elsewhere. Git Bash comes from `DISPATCH_CLI_BASH` or from Git's install location, because a bare `bash` on the Windows path can be WSL.

## Traps

- A launch can die at once, for example on a rejected model name. Read its log once after a detached launch, and keep the log and result paths to diagnose a missing report.
- Take model names and flags from the tool's own `--help` or model list, never from memory. Both change between releases, and a wrong one kills the launch.
- Codex `workspace-write` keeps the repository's `.git` read-only and refuses to start a browser. `exec` runs with approval `never`, so a detached task that must add a worktree, commit, or run a browser test stops at the first of them with `Permission denied` on a ref lock or `spawn EPERM`. Pass `--full-access` for such a task, or run it attended and approve each escalation in the window. A Claude Code auto-mode coordinator must not assume chat approval will admit a Codex `--full-access` launch. After a classifier denial, it must ask the author to admit the launch, for example with a Bash allow rule, and must retry with `--full-access`, never without it.
- Auto mode is the writable Claude default because `acceptEdits` still prompts for many shell commands and silently blocks an unattended session. Auto can still stop when its classifier requires a person. Tell the author to attach when that happens.
- Auto mode is not read-only. Its classifier can approve writes. The read-only section names what Claude `--read-only` still denies, and when to use Codex instead.
- `--read-tool` is an authority grant, not a tool-name heuristic. Inspect the operation before naming it. A mutating MCP operation remains mutating even when the prompt calls it a read.
- Do not use `bypassPermissions` merely to avoid prompts. It disables approval checks and requires explicit authorization from the author plus Claude's one-time command-line disclaimer.
- The prompt names absolute paths for everything outside the tree. A relative path resolves inside the worktree, where the file is not.
- The dispatched agent commits on its branch and never pushes. The parent or a verifier lands the branch. A dispatched agent opens a pull request only when told to.
- Two sessions on one checkout overwrite each other. The worktree is not optional when another session may be editing.
- A session dispatched from a linked worktree does not show up in the launching project's session list. Claude files its transcript under the new tree's own path, which is a different project. Find it with `claude agents --json`.
- Start one launch per Bash call. A loop that starts several launches in one call shows Arc one launch command and several receipts, so Arc links none of those sessions to the parent's turn in its panel. A wrapper script that runs the launcher without `--arc-dispatch` on the wrapper's own command line has the same result.
- A session on a subscription plan draws on the plan's session limit. A dispatched run that starts near the limit stops when it is reached and waits for the reset.

## Handoff documents

A handoff document is whatever the project uses: a plan file, an issue, a spec, a section of a design doc. This skill assumes no folder and no format. The prompt points at it by absolute path and says where in it the results go. With no document, the prompt names a results file beside the log, and the summary at the end of the run is the record. For a controlled comparison, the transport record carries each unique result path instead.
