---
name: dispatch
description: "Start a fresh agent session on a bounded task and leave its result in a commit or report. This local convention is a fresh-session handoff, not a child subagent or a formal vendor handoff. Supports Claude Code and Codex. Use when: dispatch this, hand this off to a fresh agent, start a fresh session on this, run this in the background, open this in a window, detach this. For child work whose result must return to the active parent, use delegate."
---

# Dispatch: a fresh-session handoff

A subagent reports back into the session that spawned it, and that report is re-read on every later call. A fresh-session handoff reports into files and commits. The parent keeps none of its context. The local command for this convention is `dispatch`. Use it when the parent would only relay the result.

OpenAI's Agents SDK also uses the word handoff, but for a different mechanism. Its specialist takes over the active conversation inside one run. Here the worker is an independent session.

The skills repository's `docs/orchestration.md` records why each rule here exists.

## Detached result delivery

A callback launch creates a unique receipt directory before starting its worker. The launch record names that directory and its recovery command. Callback logs and reports use launch-private paths. Requested paths remain references in the receipt. Writable Claude workers receive their private report path in the prompt. Standalone, attended, native and waited routes retain their existing result handling.

The receipt separates worker observations, queue attempts and parent consumption. Frozen events include the origin, launch identity, observation kind, known exit status, report availability and report digest. Registry disappearance leaves exit status unknown. Missing, empty, malformed and failed reports remain inspectable. Status and refresh detect later report changes without automatically sending a callback.

Use the receipt helper with the exact origin, launch and event from the callback. Claim before processing the frozen result. Acknowledge only after durable incorporation or explicit failure resolution. Choose a stable processing owner for the handler. Keep its returned token. A repeated consumed event returns its recorded outcome. An interrupted claim leaves processing outcome unknown. Check durable effects before resuming with that owner's token. An acknowledgment for an older event does not consume a changed report.

```
python3 "$SKILL_DIR/receipt.py" RECEIPT status
python3 "$SKILL_DIR/receipt.py" RECEIPT read --event EVENT
python3 "$SKILL_DIR/receipt.py" RECEIPT claim --origin ORIGIN --launch LAUNCH --event EVENT --owner OWNER
python3 "$SKILL_DIR/receipt.py" RECEIPT ack --origin ORIGIN --launch LAUNCH --event EVENT --owner OWNER --token TOKEN --outcome "durable outcome reference"
python3 "$SKILL_DIR/receipt.py" RECEIPT refresh
python3 "$SKILL_DIR/receipt.py" RECEIPT recover --event EVENT
python3 "$SKILL_DIR/receipt.py" RECEIPT monitor
```

Queue exit zero records reported acceptance. It does not establish parent consumption. A nonzero exit, timeout or interrupted submission leaves acceptance unknown. Automatic retry applies only to failure before the queue process starts, with two retries at most. Each queue, registry and diagnostic subprocess has a 30-second deadline. Receipt locks release when their process exits. Recovery acquires an exclusive monitor lock and never restarts the worker.

Recovery preserves event identities and all attempts. It suppresses an exact consumed event before submission. Accepted delivery is not automatically repeated. To resend unknown acceptance, explicitly add `--resend-unknown` to the recovery command. That action risks a duplicate callback. Consumption during submission does not cancel an accepted notice. External effects are not atomic with acknowledgment, so this protocol does not promise exactly-once delivery or processing.

Use one selected completion route. Do not independently process a callback result through polling or a second worker. Explicit recovery remains necessary when a detached queue cannot resume an idle parent. Use native completion or a supported waited route when automatic continuation is required. Shell callback mode requires Python 3.10 and the receipt helper. It refuses callback mode before launching a worker when either is unavailable. Standalone shell delivery remains available without Python.

Read [../references/owner-brief.md](../references/owner-brief.md) before preparing any worker prompt. Apply it to initial briefs, review rounds, refreshed briefs, and native or dispatched consultation and falsification rounds.

## When to dispatch and when to fan out

Dispatch when the result is a commit and a note: a phase of a written plan, a verifier pass, a migration with a spec. Fan out with a subagent when the result feeds the parent's next step: research, a measurement, a batch of small edits the parent reviews. A large parent context makes dispatch cheaper still, because nothing comes back to be re-read.

Prefer native children when the harness supports the required model, provider, effort, isolation, and role. Use dispatch for unsupported native combinations or required sandbox enforcement. A native child returns through harness completion only. It must not request a detached queue callback or independently repeat completion handling. A report-only prompt does not enforce filesystem read-only access.

A fresh-session handoff needs a durable result and one selected completion route. For a detached worker returning to a Codex origin, record the origin session id and pass it through `--notify-thread`. Save the launch and result paths, then end the origin turn so the queued completion can resume it. Process its result when that callback arrives. For a Codex worker under a Claude coordinator, run `dispatch.py --wait` as one background shell command. `--wait` blocks that command until the worker exits, and the host must notify the coordinator when the background command completes. A Codex worker with `--notify-thread` queues a callback after exit. A writable Claude worker uses one local registry watcher to queue completion, attention, failure or timeout. If the host has no automatic completion path, report that limit. Do not create an agent solely to wait, poll or relay. Native `wait_agent` cannot observe a detached CLI job. Preserve result and callback logs for explicit recovery when delivery fails. Recovery does not cancel an accepted queued callback.

When Claude launches Claude, find the worker by its `--name` in `ListAgents`, then subscribe with `SendMessage` and `notify_when_idle: true`. One notice arrives when that worker next goes idle or exits. Subscribe from the launching session because the notice follows the spawning relationship.

## The words

- "dispatch X to opus" starts a separate session on that model. The CLI route runs it in the background. The build each shorthand names is in [../references/models.md](../references/models.md).
- "dispatch X in a window" opens a reader-owned session in a compatible ready Arc window. The CLI route opens a terminal window.
- "with codex" or "with claude" picks the runner. Without a word, use the runner this session runs on.
- Pass `--owned` for a standalone session the author directly requests, such as "dispatch X to opus". Omit it for consultation members, reviewers, verifiers, and other workers launched within a workflow. A request to consult or review remains workflow work even when the author names the model. The flag marks reader ownership, not authorization to launch a worker. `--attach` still implies `--owned`.

## Steps

### Reader-owned Arc sessions

Both launchers prefer `arc start` for reader-owned writable requests when Arc is available, the selected window is ready, and the request fits its capabilities. `--attach` implies reader ownership and selects this Arc route when compatible. The Arc route starts a reader-owned session, which the reader continues in Arc's prompt box. A reader-requested dispatch never passes `--agent-run`. Dispatch reads `arc --help` once per launch and passes `--untagged` only when the installed Arc lists it. A successful Arc route prints `mode: Arc session`, window, request, name, and session ID. It creates no terminal window or detached result log. Read the `arc-sessions` skill before you act on an Arc outcome. It owns the start prompt rules, delivery outcomes, request ID retries, and start tags.

Window selection uses `ARC_WINDOW`, or exactly one live window with complete discovery. The window's first folder must match the resolved launch folder, including when `--cwd` is omitted. A folder mismatch, absent window, ambiguous selection, incomplete discovery, unready window, or failed `arc --help` call produces an explicit local refusal before launch. Dispatch then runs the requested CLI transport. Missing Arc uses the established CLI route. `ARC_BIN` selects an explicit Arc executable. Windows dispatch invokes the published Arc JavaScript entry through Node. An unsupported batch wrapper refuses locally because it cannot preserve literal arguments.

Arc cannot preserve requested worktree creation, extra directories, explicit logs, callbacks, or waited completion. These requests print a local refusal and retain their requested CLI transport. Read-only requests always retain that transport. Reviewer and verifier launches remain nonowned. Never remove requested flags to obtain an Arc launch.

Supported Claude permissions retain their requested value, with `auto` as the dispatch default. `dontAsk` and `bypassPermissions` retain CLI transport after local refusal. Codex `--full-access` maps to Arc's `never` preset. Codex's default workspace-write sandbox with no approvals has no Arc preset. It retains CLI transport after local refusal. Model and effort remain explicit requested values.

A prompt file that is not a regular file, exceeds 48 KiB, is empty, or begins with a slash command refuses locally before dispatch contacts Arc. Existing input validation rejects invalid prompt paths. CLI fallback preserves requests that its prompt handling accepts.

An Arc `refused` acknowledgment permits CLI fallback before acceptance, regardless of the process exit code. Both accepted outcomes, including `accepted-with-errors`, end the dispatch in the Arc session. Unknown delivery, malformed output, missing acknowledgments, and timeouts after the start attempt never permit fallback. A process exit code alone establishes no delivery outcome. A selected window that disappears after discovery leaves delivery unknown without a matching refusal acknowledgment. An unknown outcome prints `mode: Arc delivery unknown` and exits 2. Report the printed window, request ID, and diagnostics.

### CLI handoff

1. **Write the prompt to a file.** Never inline it: a shell command near ten kilobytes fails before the shell reads it. The prompt carries, in this order:
   - The task and its limits. Name what not to start.
   - The decisions already made, and an instruction not to reopen them. The session sees the code but not the reasoning that shaped it.
   - Where the instructions live, as absolute paths: a handoff document if there is one, and the repository's conventions file. Absolute, because a worktree holds no ignored file and the agent starts with no context.
   - The working tree and its rules: the branch, commit but never push, no destructive git commands, no dependency install unless told.
   - What to verify and how, with the exact commands.
   - For ordinary dispatch, where to write results: the handoff document if there is one, else a results file beside the log. Then a closing summary shape.
   - For a controlled comparison, omit the result destination. Transport metadata assigns each launch a unique path so every reviewer receives identical prompt bytes.
2. **Choose the tree.** Edits go in a worktree whenever another session shares the checkout. Claude makes one with `-w <name>`, under `<main-checkout>/.claude/worktrees/<name>` on branch `worktree-<name>`, locked while the session lives. That is the main checkout and not the launching directory, so a session started in a linked worktree puts its tree beside the main tree. The branch starts at `origin/HEAD`, not at the local `HEAD`. Codex has no such flag: run `git worktree add` first and pass the path with `-C`. A worktree holds no ignored file, so a dependency directory must be installed or linked there before the run. One run owns one tree. A retry reuses it, or removes the old tree first. A Codex tree goes at `<main-checkout>/.claude/worktrees/<name>` too.
3. **Launch `dispatch.py` from this skill's directory, under `python3`.** Read `references/session-naming.md` in `$SKILLS_DIR` once per invocation. Choose each launch's `--name` before launch. It builds the command, starts the session, and prints one record. A detached launch prints mode, runner, model, id or pid, tree, branch, log, result, prompt, a watch command, and, for a supported Claude or Codex launch whose session id is known, an `ARC_DISPATCH_SESSION=` receipt line. A waited launch with `--wait` prints mode, runner, model, tree, prompt, log, result, and the session id. It then prints the result text in place of a watch command, and the receipt line last, after the result text, when the run succeeded with a full UUID: every launch mode keeps the receipt as the final line of stdout. If `dispatch.py` fails before its worker starts, retry once with `dispatch.sh`. Keep `--notify-thread` for a Codex origin and drop only unsupported `--wait`. For a Claude origin, use one background shell command to wait for the `dispatch.sh` result file and print it once. That route requires the host to notify the coordinator when the command completes. The command must also detect an immediate launch failure from the log or worker status. Keep the log and result paths for recovery. If neither automatic return path exists, report the limit.

   `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it. `$SKILLS_DIR` removes that path's last component as text, before filesystem traversal. Never derive it through `..`.

   ```
   python3 "$SKILL_DIR/dispatch.py" --runner claude --arc-dispatch --model claude-opus-5-5 --worktree \
     --name code-implement-1-catalog-update-opus --prompt /abs/path/prompt.md --add-dir /abs/path/of/handoff/dir
   ```

   Every supported Claude or Codex launch command carries `--arc-dispatch`. The Python launcher prints one `ARC_DISPATCH_SESSION=` line for each supported launch whose session id is known, with or without the flag. Arc reads that line only from the stdout of a command whose own text carries the flag. Waited runs frame receipt-shaped worker lines before printing the launcher receipt last. The result file keeps the exact worker text. Failed and unidentified launches print no receipt. Attended Codex launches and standalone shell-fallback Codex launches identify no session and print no receipt. Shell callback launches use the Python launcher and its receipt behavior. A detached Codex launch can return with a pending ID after its bounded wait. Building a Claude receipt takes `python3`. Without it, `dispatch.sh` still launches Claude and reports the missing receipt on stderr.

   Add `--attach` for a window. Add `--owned` when the author's own message asked for this dispatch, per "The words" above. `--attach` implies `--owned`, because a session opened in a window is one the author watches. Add `--effort high` for a reasoning effort. Writable Claude background sessions default to `auto`. Its classifier handles routine shell work without waiting for a person and still refuses unsafe actions. Pass `--permission-mode MODE` only when the task needs another mode. `acceptEdits` still prompts for many shell commands, so it is not an unattended default. Add `--read-only` for a fresh analysis session that runs without permission prompts. Only a Codex session is guaranteed to change no file, as Read-only dispatch explains. Add one `--read-tool mcp__server__operation` for each exact connector read it needs. Verify that every named operation is non-mutating before launch. Add `--full-access` only for a Codex task that must commit, add a worktree, or run a browser test. It disables the Codex sandbox. The launching account and prompt rules still limit the session. Add `--notify-thread <origin-session-id>` to a detached Claude or Codex launch when its result must return to an existing Codex session. The callback records its own log beside the worker log. Add `--wait` to keep the worker in the launcher's foreground. Under a Claude coordinator, run that launcher as one background shell command with host completion notification. It excludes `--notify-thread` and `--attach`. The launch dies before its worker starts when either flag is also present. It runs a Codex worker of any kind, and a Claude worker only with `--read-only`. It prints the result path, then the session id, then the result text, and exits with the worker's own exit code. A result above the script's character cap prints its path and its size in place of its text.
4. **Report the record** to the author with the watch, attach, stop and remove commands the script printed.
5. **Record the dispatch** in the handoff document if there is one: the same record, on one line, where the task is described. If there is none, the log and the prompt file are the record.
6. **Close** when the session ends, stops or fails. Remove its tree with [../references/scripts/remove-worktree.py](../references/scripts/remove-worktree.py) under `python3`: `remove-worktree.py --repository <main-checkout> <tree>`. Never use `git worktree remove` or a recursive delete, because either can follow a dependency link and delete the files behind it. The script removes a clean tree whose HEAD a branch carries, and it keeps a tree with uncommitted or untracked files. When the task wrote into a plan folder, list its large files with `find <plan-folder> -type f -size +2M -not -path '*/.git/*'`. Extract, gzip or delete each one under the plan convention's archive rule. Report each tree removed or kept, with the reason.

## A background session cannot move its own permission root

Auto mode handles routine shell work, but it will not approve a permission-root relocation, because that changes what the session may touch. The tool that switches a session into a worktree asks for it, the prompt waits, and a detached session has nobody to answer. The run stops with everything done and nothing delivered.

Give a writable session every directory it will need at launch, through `--add-dir`. Have it reach each directory with `git -C <path>` rather than by relocating. A worker that must land on a branch in the main checkout needs `--add-dir` on that checkout.

## Read-only dispatch

Use `--read-only` for reviews, audits, and other analysis whose full report should stay in a file instead of returning to the parent conversation. The prompt must name the immutable commit to inspect. The launch metadata names where the report will be saved. Keep that unique path out of a shared controlled-review prompt. Do not use read-only dispatch for a moving working tree. Codex read-only sandboxing prevents writes. Claude uses permission rules and auto mode instead. It preapproves `Read`, `Grep` and `Glob`, denies the edit tools and the other built-in mutation tools, and denies PowerShell. Bash runs under auto mode, whose classifier judges each command. A reviewer can run pipes, compound commands and git reads in any directory, such as `git -C DIR log`, `git show --stat SHA | cat` and `wc -l FILE`. Deny rules stop every git command that writes refs, configuration, the index, the working tree or the object store, such as `git commit`, `git fetch`, a bare `git stash`, `git add` and `git config`. `READONLY_GIT_DENY_VERBS` in `dispatch.py` holds the full list. A verb that also reads, such as `git stash list` or `git config --get`, is denied whole, because no rule tells its read form from its write form. `git branch`, `git tag`, `git remote` and `git notes` are denied with any argument, because a branch or tag name can be any word. Read refs with `git for-each-ref` or `git log --decorate` instead. `git diff`, `git log` and `git show` are denied with `--output`, which writes a file. Each rule matches the plain form and the `git -C DIR` form. Another spelling, such as `git -c KEY=VALUE commit`, reaches the classifier instead. The classifier can approve any other Bash command that writes a file, such as a shell redirect, so Claude `--read-only` does not guarantee an unchanged tree. Use Codex `--read-only` when the task needs that guarantee. Tell a Claude reviewer in the prompt to change no file, because the classifier reads the conversation for boundaries. Auto mode supports Haiku 5.5 and later Opus, Sonnet, and Fable models. Read [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes#eliminate-permission-prompts-with-auto-mode) for provider and CLI requirements. Claude Code starts an unsupported model, such as Haiku 4.5, in manual mode, where a session without permission prompts loses its chained reads. Both launchers run such a model in `dontAsk` instead, with the same denials, and print a line that says so. Bash then runs one plain read at a time, such as `git log`, `git show --stat HEAD` or `git rev-parse HEAD`. A pipe, a chain and `git -C DIR` are denied. Choose a model with auto mode for a review that needs them. An allow rule in a settings file skips the classifier, so a rule such as `Bash(npm run build *)` would let the session write. Without `--read-tool`, the launch therefore loads no settings files and no MCP servers, through `--setting-sources ""` and `--strict-mcp-config`. That also drops their `env`, hooks, model and login helpers, so pass `--model` explicitly. Never add Bash to the launch's allowed-tools list: an allow rule skips the classifier the same way. `DISPATCH_CANARY=1 python3 test_dispatch.py ReadOnlyGitCanaryTests` re-measures the piped read, the cross-directory read, the `git commit`, bare `git stash`, `git fetch` and `git show --output` denials, and the `Write` denial against the installed CLI. It runs Sonnet by default, so it does not measure the `dontAsk` launch of an older model. With `--read-tool`, the launch keeps the settings that configure the MCP server, stays in `dontAsk` mode, and denies Bash and PowerShell outright, so that session cannot run git. `dontAsk` denies every MCP operation not named with `--read-tool`, where auto mode would send it to the classifier. Put the diff in the prompt, or split the work. A read-only run writes no plan file, unlike plan mode. Name the allowed repository roots in the prompt, and pass each root outside `--cwd` with `--add-dir`.

Without `--read-tool`, the empty setting sources also stop every `CLAUDE.md` from loading, the project file and the user file alike. Auto-memory still loads. On Claude Code 2.1.280, a `claude -p` launched with `--setting-sources ""` could not name a fact that only `CLAUDE.md` holds, and the same call with `project` could. A read-only review therefore runs without the repository's conventions unless the prompt carries them. When the result will be compared with a CI run, paste the root `CLAUDE.md` into the prompt, or the two runs differ in more than the thing under test. Keep the empty setting sources all the same: they are what stops a settings-file allow rule from making a read-only session writable.

```
python3 "$SKILL_DIR/dispatch.py" --runner claude --read-only --arc-dispatch --model <model> \
  --name code-review-1-catalog-update-opus --read-tool mcp__example__get_item --read-tool mcp__example__list_items \
  --cwd /abs/path/to/repo --prompt /abs/path/review-prompt.md --log /abs/path/review.log
```

Codex uses the `read-only` sandbox and writes its final message beside the log. Claude uses fresh `-p` print mode, auto mode, or `dontAsk` with `--read-tool` or a model without auto mode, no permission-prompt target, exact read preapprovals, explicit built-in denials, the settings rule above, and JSON output. The detached log is also the result file. Claude print output is buffered until completion, so `--attach` is rejected for that combination. Claude read-only print mode also does not create a worktree. The record gives the PID and `tail -f` command for either runner.

For a detached Claude read-only launch, `dispatch.py` and, when `python3` is on `PATH`, `dispatch.sh` choose a UUID before starting `claude -p` and pass it with `--session-id`. The receipt uses that UUID after the worker starts. Without `python3`, `dispatch.sh` skips both and the launch carries no session id and no receipt. A waited read-only launch takes its ID from the successful JSON result. A writable background launch takes the prefix from Claude's launch output. The detached worker's final JSON result must report the chosen UUID.

## Claude runner

Proven on Claude Code 2.1.257, launched from inside another Claude Code session.

Both Claude launches set `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` unless the caller already set it. On a subscription, Claude Code writes the main conversation's cache at 1 hour, which costs 2 times the input rate. A 5-minute write costs 1.25 times. The 1-hour lifetime pays off only when a person pauses 5 to 60 minutes between requests, because each such return rebuilds the whole cache at 5 minutes. A dispatched session runs without those pauses. Repriced at list price over 177 dispatched sessions, 5 minutes cost 7.9% less, and 94% of the sessions were cheaper. Read-only runs saved about 14%, and writable runs saved 4.8%. A writable run loses money when it waits on a permission prompt or on a follow-up message for 5 to 60 minutes. The same repricing made interactive sessions cost 21% more, so the setting stays in the dispatch launches and out of the Claude settings file.

```
claude --bg -w <name> -n <name> --model <model> --permission-mode auto \
  --allowedTools "Bash Edit Write Read Grep Glob MultiEdit" [--add-dir <dir>] "$(cat prompt.md)"
```

It prints `backgrounded · <id> · <name>` and returns. An idle launch adds a parenthetical status after the name. The id is the second field of that line. Then:

- `claude agents --json` lists sessions. The bare form needs a terminal.
- `claude logs <id>` prints recent output.
- `claude attach <id>` opens the session in the current terminal. A permission prompt the flags do not cover waits there until someone answers it.
- `claude stop <id>` stops it and keeps the conversation. `claude rm <id>` deletes the session record. For a session with a worktree, the remove command the script prints runs the removal script in Close first, and it runs `claude rm` only once the tree is gone.

`--add-dir` is needed for every file outside the tree the agent must edit, such as a handoff document in the main checkout. Writable dispatch uses `--bg` and defaults to `auto`. Read-only dispatch uses detached `claude -p --permission-mode auto --permission-prompts none`, or `dontAsk` with `--read-tool` or a model without auto mode, as the read-only section describes. It cannot be attached because print output is buffered.

With `--notify-thread`, read-only Claude queues after its process exits. Writable Claude starts `watch_claude.py`, which reads the local `claude agents --json` registry with a 2, 4, 8, 16, then 30 second backoff. Registry disappearance produces an unverified observation with unknown exit status. A blocked or idle worker gets 45 seconds to leave before the callback asks the parent to attach. Three registry errors, an unknown terminal state, or the 24-hour monitor limit each queue one diagnostic notice. The watcher makes no model request.

## Codex runner

```
codex exec -C <dir> -m <model> [-c model_reasoning_effort=<effort>] -s workspace-write \
  [--add-dir <dir>] -o <last-message-file> "$(cat prompt.md)"
```

Proven on codex-cli 0.151.0. `exec` is non-interactive and does not take `-a`. The script detaches it into a log and writes the final message to the `-o` file. The attended form runs `codex -C <dir> -m <model> -s workspace-write "$(cat prompt.md)"` in a new window. Codex makes no worktree of its own: make one first and pass it with `-C`. With `--read-only`, the script replaces `workspace-write` with `read-only`. With `--full-access`, it replaces it with `danger-full-access`. With `--notify-thread`, the detached launcher reports the worker's exit state and result path through `codex queue`. The option requires an existing session on the same app-server daemon.

## Terminal windows

The script writes a run file beside the prompt: a shebang line, a `cd` command, and the launch command. It opens that file with:

- Windows: `wt -d <dir> <git bash> <run file>`. Git Bash is resolved from the git installation, not the shell that runs the script, because a bare `bash` on the Windows path can be WSL.
- macOS: `osascript -e 'tell application "Terminal" to do script "bash <run file>"'`.
- Linux: `tmux new-window` inside tmux, else `x-terminal-emulator -e`, else `gnome-terminal --`.

## Traps

- A launch can die at once, for example on a rejected model name. Read its log once after a detached launch, and retain the log, result and callback log paths to diagnose a missing or failed notice.
- Take model names and flags from the tool's own `--help` or model list, never from memory. Both change between releases, and a wrong one kills the launch.
- Codex `workspace-write` keeps the repository's `.git` read-only and refuses to start a browser. `exec` runs with approval `never`, so a detached task that must add a worktree, commit, or run a browser test stops at the first of them with `Permission denied` on a ref lock or `spawn EPERM`. Pass `--full-access` for such a task, or run it attended and approve each escalation in the window.
- Auto mode is the writable Claude default because `acceptEdits` still prompts for many shell commands and silently blocks an unattended session. Auto can still stop when its classifier requires a person. Tell the author to attach when that happens.
- Auto mode is not read-only. Its classifier can approve writes. The read-only section names what Claude `--read-only` still denies, and when to use Codex instead.
- Plan mode admits an MCP read only when its server marks the operation read-only. Servers without that annotation turn ordinary reads into permission failures, so Claude `--read-only --read-tool` uses `dontAsk` with exact preapprovals and explicit mutation denials instead.
- `--read-tool` is an authority grant, not a tool-name heuristic. Inspect the operation before naming it; a mutating MCP operation remains mutating even when the prompt calls it a read.
- Claude.ai connectors depend on the normal settings sources, so a read-only dispatch with `--read-tool` keeps them. Their allow rules remain authority, for connectors and for every other tool. Review them before you claim that no unlisted mutation can occur.
- Do not use `bypassPermissions` merely to avoid prompts. It disables approval checks and requires explicit authorization from the author plus Claude's one-time command-line disclaimer.
- The prompt names absolute paths for everything outside the tree. A relative path resolves inside the worktree, where the file is not.
- The dispatched agent commits on its branch and never pushes. The parent or a verifier lands the branch. A dispatched agent opens a pull request only when told to.
- A Claude `-w` worktree starts at `origin/HEAD`, so a checkout ahead of its remote gives the worker an old base. `dispatch.py --worktree` refuses to launch when `HEAD` and `origin/HEAD` differ. Run `git worktree add -b <branch> <path> HEAD`, then launch with `--cwd <path>` and without `--worktree`. Standalone `dispatch.sh` has no such check, so never retry the refusal with it.
- Two sessions on one checkout overwrite each other. The worktree is not optional when another session may be editing.
- `claude agents` without `--json` fails from a script with "requires an interactive terminal". The script uses the id from the launch line instead.
- A writable Claude worker briefly reports `blocked` after a normal final turn. The watcher waits 45 seconds for it to leave the registry before it reports that the author's attention is required. Never treat the first blocked observation as completion.
- A session dispatched from a linked worktree does not show up in the launching project's session list. Claude files its transcript under the new tree's own path, which is a different project. Find it with `claude agents --json`.
- `claude rm` refuses a session whose worktree holds an uncommitted change, and a live session locks its worktree. Stop the session, then commit or discard inside the worktree. Remove the tree with the script in Close, then `claude rm`. The prompt tells the agent to commit everything it makes, so the tree is clean when it ends.
- A session on a subscription plan draws on the plan's session limit. A dispatched run that starts near the limit stops when it is reached and waits for the reset.

## Handoff documents

A handoff document is whatever the project uses: a plan file, an issue, a spec, a section of a design doc. This skill assumes no folder and no format. The prompt points at it by absolute path and says where in it the results go. With no document, the prompt names a results file beside the log, and the summary at the end of the run is the record. For a controlled comparison, the transport record carries each unique result path instead.
