---
name: dispatch
description: "Start a fresh agent session on a bounded task and leave its result in a commit or report. This local convention is a fresh-session handoff, not a child subagent or a formal vendor handoff. Supports Claude Code and Codex. Use when: dispatch this, hand this off to a fresh agent, start a fresh session on this, run this in the background, open this in a window, detach this. For child work whose result must return to the active parent, use delegate."
---

# Dispatch: a fresh-session handoff

A subagent reports back into the session that spawned it, and that report is re-read on every later call. A fresh-session handoff reports into files and commits. The parent keeps none of its context. The local command for this convention is `dispatch`. Use it when the parent would only relay the result.

OpenAI's Agents SDK also uses the word handoff, but for a different mechanism. Its specialist takes over the active conversation inside one run. Here the worker is an independent session.

The skills repository's `docs/orchestration.md` records why each rule here exists.

## When to dispatch and when to fan out

Dispatch when the result is a commit and a note: a phase of a written plan, a verifier pass, a migration with a spec. Fan out with a subagent when the result feeds the parent's next step: research, a measurement, a batch of small edits the parent reviews. A large parent context makes dispatch cheaper still, because nothing comes back to be re-read.

A fresh-session handoff is autonomous only when it has a durable result and a supported completion notification. Record the originating session id before launch. Current Codex can receive a queued message through `codex queue --thread <session-id> --message <text>`. Pass that id through `--notify-thread` for any detached worker whose result must return to a Codex session. A Codex worker queues after its process exits. A writable Claude worker uses one local registry watcher with exponential backoff, then queues one completion, attention, failure, or timeout notice. The parent never polls from model turns.

When Claude launches Claude, find the worker by its `--name` in `ListAgents`, then subscribe with `SendMessage` and `notify_when_idle: true`. One notice arrives when that worker next goes idle or exits. Subscribe from the launching session because the notice follows the spawning relationship.

## The words

- "dispatch X to opus" starts an unattended background session on that model. The build each shorthand names is in [../references/models.md](../references/models.md).
- "dispatch X in a window" starts the same session, then opens it in a new terminal window, so the user watches and answers prompts without pasting anything.
- "with codex" or "with claude" picks the runner. Without a word, use the runner this session runs on.
- Pass `--owned` when the user's own message asked for this dispatch, such as "dispatch that for me" or "dispatch X to opus". Leave it off when an agent dispatches as a step of its own workflow, such as a reviewer or a verifier.

## Steps

1. **Write the prompt to a file.** Never inline it: a shell command near ten kilobytes fails before the shell reads it. The prompt carries, in this order:
   - The task and its limits. Name what not to start.
   - The decisions already made, and an instruction not to reopen them. The session sees the code but not the reasoning that shaped it.
   - Where the instructions live, as absolute paths: a handoff document if there is one, and the repository's conventions file. Absolute, because a worktree holds no ignored file and the agent starts with no context.
   - The working tree and its rules: the branch, commit but never push, no destructive git commands, no dependency install unless told.
   - What to verify and how, with the exact commands.
   - For ordinary dispatch, where to write results: the handoff document if there is one, else a results file beside the log. Then a closing summary shape.
   - For a controlled comparison, omit the result destination. Transport metadata assigns each launch a unique path so every reviewer receives identical prompt bytes.
2. **Choose the tree.** Edits go in a worktree whenever another session shares the checkout. Claude makes one with `-w <name>`, under `<main-checkout>/.claude/worktrees/<name>` on branch `worktree-<name>`, locked while the session lives. That is the main checkout and not the launching directory, so a session started in a linked worktree puts its tree beside the main tree. The branch starts at `origin/HEAD`, not at the local `HEAD`. Codex has no such flag: run `git worktree add` first and pass the path with `-C`. A worktree holds no ignored file, so a dependency directory must be installed or linked there before the run. A linked directory must be unlinked before the worktree is removed, or the removal walks through the link into the main tree.
3. **Launch `dispatch.py` from this skill's directory, under `python3`.** It builds the command, starts the session, and prints one record. A detached launch prints mode, runner, model, id or pid, tree, branch, log, result, prompt, a watch command, and, for a Claude launch whose session id is known, an `ARC_DISPATCH_SESSION=` receipt line. A waited launch with `--wait` prints mode, runner, model, tree, prompt, log, result, and the session id. It then prints the result text in place of a watch command, and the receipt line last, after the result text, when the run succeeded with a full UUID: every launch mode keeps the receipt as the final line of stdout. If `dispatch.py` fails before its worker starts, launch the same flags once with `dispatch.sh`. Drop `--wait` first, since `dispatch.sh` carries no `--wait` and exits 2 on an unknown option. Pass `--log` to name a result file, then poll that file instead of waiting.

   `$SKILL_DIR` is the directory that holds this SKILL.md. The harness gives that location when it loads the skill. Never search the filesystem for it.

   ```
   python3 "$SKILL_DIR/dispatch.py" --runner claude --arc-dispatch --model claude-opus-5-5 --worktree \
     --name send-item4 --prompt /abs/path/prompt.md --add-dir /abs/path/of/handoff/dir
   ```

   Every Claude launch command carries `--arc-dispatch`. Both launchers print one `ARC_DISPATCH_SESSION=` line for each Claude launch whose session id is known, with or without the flag. Arc reads that line only from the stdout of a command whose own text carries the flag, and only one launch per command. Codex and failed launches print no receipt. Do not add `--arc-dispatch` to a Codex launch: a waited Codex launch's stdout is the worker's own free text, which could by chance hold a receipt-shaped line, and the flag on that command's text would let Arc link it. Building the Claude receipt line takes `python3`. Without it, `dispatch.sh` still launches Claude and writes one stderr line saying that the launch has no Arc receipt.

   Add `--attach` for a window. Add `--owned` when the user's own message asked for this dispatch, per "The words" above. `--attach` implies `--owned`, because a session opened in a window is one the author watches. Add `--effort high` for a reasoning effort. Writable Claude background sessions default to `auto`. Its classifier handles routine shell work without waiting for a person and still refuses unsafe actions. Pass `--permission-mode MODE` only when the task needs another mode. `acceptEdits` still prompts for many shell commands, so it is not an unattended default. Add `--read-only` for a fresh analysis session that runs without permission prompts and cannot change files. Add one `--read-tool mcp__server__operation` for each exact connector read it needs. Verify that every named operation is non-mutating before launch. Add `--full-access` only for a Codex task that must commit, add a worktree, or run a browser test. It disables the Codex sandbox. The launching account and prompt rules still limit the session. Add `--notify-thread <origin-session-id>` to a detached Claude or Codex launch when its result must return to an existing Codex session. The callback records its own log beside the worker log. Add `--wait` to run the worker in the foreground instead of detaching it. It excludes `--notify-thread` and `--attach`. The launch dies before its worker starts when either flag is also present. It runs a Codex worker of any kind, and a Claude worker only with `--read-only`. It prints the result path, then the session id, then the result text, and exits with the worker's own exit code. A result above the script's character cap prints its path and its size in place of its text.
4. **Report the record** to the user with the watch, attach, stop and remove commands the script printed.
5. **Record the dispatch** in the handoff document if there is one: the same record, on one line, where the task is described. If there is none, the log and the prompt file are the record.

## A background session cannot move its own permission root

Auto mode handles routine shell work, but it will not approve a permission-root relocation, because that changes what the session may touch. The tool that switches a session into a worktree asks for it, the prompt waits, and a detached session has nobody to answer. The run stops with everything done and nothing delivered.

Give a writable session every directory it will need at launch, through `--add-dir`. Have it reach each directory with `git -C <path>` rather than by relocating. A worker that must land on a branch in the main checkout needs `--add-dir` on that checkout.

## Read-only dispatch

Use `--read-only` for reviews, audits, and other analysis whose full report should stay in a file instead of returning to the parent conversation. The prompt must name the immutable commit to inspect. The launch metadata names where the report will be saved. Keep that unique path out of a shared controlled-review prompt. Do not use read-only dispatch for a moving working tree. Codex read-only sandboxing prevents writes. Claude uses permission rules instead: it preapproves `Read`, `Grep`, `Glob`, and the MCP operations named with `--read-tool`, denies built-in mutation tools, and denies every remaining permission request without prompting. An allow rule in a settings file outranks `dontAsk`, so a rule such as `Bash(python3 -c *)` would let the session write. Without `--read-tool`, the launch therefore loads no settings files. That also drops their `env`, hooks, model and login helpers, so pass `--model` explicitly. Bash then runs only commands Claude Code classifies as read-only. A plain git read runs, such as `git log`, `git show`, `git diff`, `git rev-parse`, `git cat-file`, `git grep` and `git ls-files`. The same read is denied once it names another directory, sets configuration, redirects, or chains. `git -C DIR show`, `git --git-dir=DIR log`, `git -c core.pager=cat log`, `git show --output=FILE` and `git log > FILE` all fail that way. `cd DIR && git log` fails too, and `git ls-tree` is denied outright. So the session reads git only in the directory it starts in. Give `--cwd` the repository that holds the commit, and tell the reviewer to run git without `-C`. A second repository needs its own launch, or its diff in the prompt. Never widen this by adding Bash to the launch's allowed-tools list, or by editing `dispatch.py`'s `shell_denials`. The default launch above loads no settings files, because `dispatch.py` passes `--setting-sources ""`. The `--read-tool` launch does keep settings, but it denies Bash outright on the command line, so a settings-file allow rule reaches nothing either way. The real risk sits in `dispatch.py` itself. A pattern such as `Bash(git -C DIR show:*)` added to `readonly_tools` would also admit `git -C DIR show --output=FILE`, a write. Claude Code owns this classification and changes it between releases. `DISPATCH_CANARY=1 python3 test_dispatch.py ReadOnlyGitCanaryTests` re-measures the plain-read, cross-directory-denial and write-flag-denial checks above against the installed CLI. It does not drive `git -c`, a shell redirect, or the outright `git ls-tree` denial. PowerShell is denied. With `--read-tool`, the launch keeps the settings that configure the MCP server and denies Bash and PowerShell outright, so that session cannot run git. Put the diff in the prompt, or split the work. A read-only run writes no plan file, unlike plan mode. Name the allowed repository roots in the prompt. Those roots reach `Read`, `Grep` and `Glob`, and never git.

Without `--read-tool`, the empty setting sources also stop every `CLAUDE.md` from loading, the project file and the user file alike. Auto-memory still loads. On Claude Code 2.1.280, a `claude -p` launched with `--setting-sources ""` could not name a fact that only `CLAUDE.md` holds, and the same call with `project` could. A read-only review therefore runs without the repository's conventions unless the prompt carries them. When the result will be compared with a CI run, paste the root `CLAUDE.md` into the prompt, or the two runs differ in more than the thing under test. Keep the empty setting sources all the same: they are what stops a settings-file allow rule from making a read-only session writable.

```
python3 "$SKILL_DIR/dispatch.py" --runner claude --read-only --arc-dispatch --model <model> \
  --read-tool mcp__example__get_item --read-tool mcp__example__list_items \
  --cwd /abs/path/to/repo --prompt /abs/path/review-prompt.md --log /abs/path/review.log
```

Codex uses the `read-only` sandbox and writes its final message beside the log. Claude uses fresh `-p` print mode, `dontAsk`, no permission-prompt target, exact read preapprovals, explicit built-in denials, the settings rule above, and JSON output. `auto` is not used because its classifier can approve a write. The detached log is also the result file. Claude print output is buffered until completion, so `--attach` is rejected for that combination. Claude read-only print mode also does not create a worktree. The record gives the PID and `tail -f` command for either runner.

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
- `claude stop <id>` stops it and keeps the conversation. `claude rm <id>` deletes the session and its worktree when that is safe.

`--add-dir` is needed for every file outside the tree the agent must edit, such as a handoff document in the main checkout. Writable dispatch uses `--bg` and defaults to `auto`. Read-only dispatch uses detached `claude -p --permission-mode dontAsk --permission-prompts none`, as the read-only section describes. It cannot be attached because print output is buffered.

With `--notify-thread`, read-only Claude queues after its process exits. Writable Claude starts `watch_claude.py`, which reads the local `claude agents --json` registry with a 2, 4, 8, 16, then 30 second backoff. A worker that leaves the registry after appearing is complete. A blocked or idle worker gets 45 seconds to leave before the callback asks the parent to attach. Three registry errors, an unknown terminal state, or the 24-hour monitor limit each queue one diagnostic notice. The watcher makes no model request.

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

- A launch can die at once, for example on a rejected model name, and nothing reports it. Read the log once before you wait for the result.
- Take model names and flags from the tool's own `--help` or model list, never from memory. Both change between releases, and a wrong one kills the launch.
- Codex `workspace-write` keeps the repository's `.git` read-only and refuses to start a browser. `exec` runs with approval `never`, so a detached task that must add a worktree, commit, or run a browser test stops at the first of them with `Permission denied` on a ref lock or `spawn EPERM`. Pass `--full-access` for such a task, or run it attended and approve each escalation in the window.
- Auto mode is the writable Claude default because `acceptEdits` still prompts for many shell commands and silently blocks an unattended session. Auto can still stop when its classifier requires a person. Tell the user to attach when that happens.
- Auto mode is not read-only. Its classifier can approve writes, so never use it when the dispatch promises that no file or external-system mutation can occur.
- Plan mode admits an MCP read only when its server marks the operation read-only. Servers without that annotation turn ordinary reads into permission failures, so Claude `--read-only` uses `dontAsk` with exact preapprovals and explicit mutation denials instead.
- `--read-tool` is an authority grant, not a tool-name heuristic. Inspect the operation before naming it; a mutating MCP operation remains mutating even when the prompt calls it a read.
- Claude.ai connectors depend on the normal settings sources, so a read-only dispatch with `--read-tool` keeps them. Their allow rules remain authority, for connectors and for every other tool. Review them before you claim that no unlisted mutation can occur.
- Do not use `bypassPermissions` merely to avoid prompts. It disables approval checks and requires explicit user authorization plus Claude's one-time command-line disclaimer.
- The prompt names absolute paths for everything outside the tree. A relative path resolves inside the worktree, where the file is not.
- The dispatched agent commits on its branch and never pushes. The parent or a verifier lands the branch. A dispatched agent opens a pull request only when told to.
- A Claude `-w` worktree starts at `origin/HEAD`, so a checkout ahead of its remote gives the worker an old base. `dispatch.py --worktree` refuses to launch when `HEAD` and `origin/HEAD` differ. Run `git worktree add -b <branch> <path> HEAD`, then launch with `--cwd <path>` and without `--worktree`. `dispatch.sh` has no such check, so never retry the refusal with it.
- Two sessions on one checkout overwrite each other. The worktree is not optional when another session may be editing.
- `claude agents` without `--json` fails from a script with "requires an interactive terminal". The script uses the id from the launch line instead.
- A writable Claude worker briefly reports `blocked` after a normal final turn. The watcher waits 45 seconds for it to leave the registry before it reports that human attention is required. Never treat the first blocked observation as completion.
- A session dispatched from a linked worktree does not show up in the launching project's session list. Claude files its transcript under the new tree's own path, which is a different project. Find it with `claude agents --json`.
- `claude rm` refuses a session whose worktree holds an uncommitted change, and `git worktree remove` refuses a worktree the live session locks. Stop the session, then commit or discard inside the worktree, then `claude rm`. The prompt tells the agent to commit everything it makes, so the tree is clean when it ends.
- A session on a subscription plan draws on the plan's session limit. A dispatched run that starts near the limit stops when it is reached and waits for the reset.

## Handoff documents

A handoff document is whatever the project uses: a plan file, an issue, a spec, a section of a design doc. This skill assumes no folder and no format. The prompt points at it by absolute path and says where in it the results go. With no document, the prompt names a results file beside the log, and the summary at the end of the run is the record. For a controlled comparison, the transport record carries each unique result path instead.
