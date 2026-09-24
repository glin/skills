#!/usr/bin/env bash
# Start a separate agent session from a prompt file.
#
#   dispatch.sh --runner claude|codex --prompt FILE [--model M] [--effort E] [--name N]
#               [--cwd DIR] [--worktree] [--add-dir DIR]... [--permission-mode MODE]
#               [--read-tool MCP_TOOL]...
#               [--read-only] [--full-access] [--attach] [--owned] [--log FILE]
#               [--notify-thread CODEX_SESSION] [--arc-dispatch]
# --arc-dispatch marks the command's own text for Arc's reader. Every Claude launch whose
# session id is known prints an ARC_DISPATCH_SESSION= receipt with or without it.
# --owned marks that receipt as a session the user's own message asked for. --attach
# implies --owned.
# Claude writable sessions default to auto permission mode unless overridden.
#
# Prints one record line per fact: mode, runner, model, pid or id, tree, prompt, log/result,
# and commands that watch or manage the session.
set -euo pipefail

skill_dir="$(cd "$(dirname "$0")" && pwd)"
runner=claude; prompt=""; model=""; effort=""; name=""; cwd="$PWD"; worktree=0; attach=0; owned=0; log=""; read_only=0; full_access=0
notify_thread=""
permmode=""
claude_bin="${CLAUDE_BIN:-claude}"
codex_bin="${CODEX_BIN:-codex}"
adddirs=()
read_tools=()
while [ $# -gt 0 ]; do
  case "$1" in
    --runner) runner="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --effort) effort="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    --cwd) cwd="$2"; shift 2 ;;
    --worktree) worktree=1; shift ;;
    --add-dir) adddirs+=("$2"); shift 2 ;;
    --permission-mode) permmode="$2"; shift 2 ;;
    --read-tool) read_tools+=("$2"); shift 2 ;;
    --read-only) read_only=1; shift ;;
    --full-access) full_access=1; shift ;;
    --attach) attach=1; shift ;;
    --owned) owned=1; shift ;;
    --log) log="$2"; shift 2 ;;
    --notify-thread) notify_thread="$2"; shift 2 ;;
    # Parsed and accepted, never read into a variable: Arc's reader scans the command's
    # own text for this flag, so a stored value would have no effect on any launch.
    --arc-dispatch) shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
# A session opened in a window is one the owner watches, whether or not --owned was
# also given.
if [ "$attach" = 1 ]; then owned=1; fi
if [ -z "$prompt" ] || [ ! -f "$prompt" ]; then echo "--prompt FILE is required and must exist" >&2; exit 2; fi
case "$runner" in claude|codex) ;; *) echo "--runner must be claude or codex" >&2; exit 2 ;; esac
# The Codex CLI takes a full model id. A shorthand from the references skill reaches it as
# a literal and the request fails with a 400. Resolve the OpenAI shorthands here.
if [ "$runner" = codex ]; then
  codex_config="${CODEX_HOME:-$HOME/.codex}/config.toml"
  bedrock=0
  if [ -f "$codex_config" ] && sed -n '/^[[:space:]]*\[/q;p' "$codex_config" \
    | grep -Eq "^[[:space:]]*model_provider[[:space:]]*=[[:space:]]*['\"]amazon-bedrock['\"]"; then
    bedrock=1
  fi
  case "$model" in
    sol) model=gpt-6-sol ;;
    sol6) model=gpt-6-sol ;;
    terra) model=gpt-5.6-terra ;;
    luna) model=gpt-6-luna ;;
    luna6) model=gpt-6-luna ;;
    astra) model=gpt-6-astra ;;
  esac
  # Bedrock names the same models with an "openai." prefix and rejects the bare id. The
  # prefix belongs to the provider, not to the shorthand: the bare id is correct against
  # the OpenAI endpoint. Read model_provider from the top-level table only, because the
  # same key reappears under [model_providers.<name>.*] and names a different thing.
  case "$model" in
    gpt-*)
      if [ "$bedrock" = 1 ]; then
        model="openai.$model"
      fi
      ;;
  esac
fi
if [ "$read_only" = 1 ] && [ "$attach" = 1 ] && [ "$runner" = claude ]; then
  echo "--attach is unsupported with Claude --read-only: print-mode output is buffered in the result log" >&2; exit 2
fi
if [ "$read_only" = 1 ] && [ "$worktree" = 1 ] && [ "$runner" = claude ]; then
  echo "--worktree is unsupported with Claude --read-only: print mode runs in the supplied --cwd" >&2; exit 2
fi
if [ "$full_access" = 1 ] && [ "$read_only" = 1 ]; then echo "--full-access and --read-only exclude each other" >&2; exit 2; fi
if [ "$full_access" = 1 ] && [ "$runner" = claude ]; then echo "--full-access is a Codex flag: use --permission-mode for a Claude background session" >&2; exit 2; fi
if [ "${#read_tools[@]}" -gt 0 ] && { [ "$runner" != claude ] || [ "$read_only" != 1 ]; }; then
  echo "--read-tool requires --runner claude --read-only" >&2
  exit 2
fi
if [ "$runner" = claude ] && [ "$read_only" = 1 ] && [ -n "$permmode" ]; then
  echo "--permission-mode cannot override Claude --read-only; it uses dontAsk with no permission prompts" >&2
  exit 2
fi
# Both launchers print an Arc receipt by default now, with or without --arc-dispatch, and
# building that receipt line takes python3. Without it, the launch still runs, only without
# a receipt: dispatch.sh is the fallback when the Python launcher itself cannot start, so it
# must not refuse a launch that dispatch.py would still be unable to make either.
has_python3=0
if [ "$runner" = claude ] && python3 -c 'import json, uuid' >/dev/null 2>&1; then
  has_python3=1
fi
if [ "$runner" = claude ] && [ "$has_python3" = 0 ]; then
  echo "no python3 on PATH: this launch has no Arc receipt and will not link to Arc" >&2
fi
if [ -n "$notify_thread" ] && [ "$attach" = 1 ]; then echo "--notify-thread requires a detached session" >&2; exit 2; fi
cwd="$(cd "$cwd" && pwd)"
prompt="$(cd "$(dirname "$prompt")" && pwd)/$(basename "$prompt")"
# With --name, the default log is <prompt stem>.<name>.log, so two named launches from one
# prompt file keep separate results. dispatch.py builds the same path.
if [ -z "$log" ]; then
  safe_name="$(printf '%s' "$name" | LC_ALL=C tr -cs 'A-Za-z0-9._' '-')"
  safe_name="${safe_name#-}"; safe_name="${safe_name%-}"
  if [ -n "$safe_name" ]; then log="${prompt%.*}.$safe_name.log"; else log="${prompt%.*}.log"; fi
fi
name="${name:-dispatch-$(date +%Y%m%d-%H%M%S)}"
notify_log="${log%.*}.notify.log"
extra=()
for d in "${adddirs[@]}"; do extra+=(--add-dir "$d"); done

# Claude resolves `-w` against the repository's main checkout, not the launching
# directory. A session started in a linked worktree lands under the main checkout,
# so a record built from $cwd names a path that does not exist.
worktree_home() {
  local common=""
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || true
  if [ -n "$common" ]; then dirname "$common"; else printf '%s\n' "$1"; fi
}

open_window() {
  local run="${prompt%.*}.run.sh"
  printf '#!/usr/bin/env bash\ncd %q\n%s\n' "$cwd" "$1" > "$run"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) wt -d "$(cygpath -w "$cwd")" "$(cygpath -w "$(command -v bash)")" "$(cygpath -w "$run")" ;;
    Darwin) osascript -e "tell application \"Terminal\" to do script \"bash $run\"" >/dev/null ;;
    *) if [ -n "${TMUX:-}" ]; then tmux new-window "bash $run"
       elif command -v x-terminal-emulator >/dev/null 2>&1; then x-terminal-emulator -e bash "$run" &
       else gnome-terminal -- bash "$run" & fi ;;
  esac
  echo "window: bash $run"
}

print_common_record() {
  echo "model: ${model:-default}"
  echo "tree: $cwd"
  echo "prompt: $prompt"
  echo "log: $log"
}

print_arc_receipt() {
  python3 -c 'import json,sys
receipt = {"agent": "claude", "session_id": sys.argv[1], "name": sys.argv[2]}
if sys.argv[3] == "1":
    receipt["owned"] = True
print("ARC_DISPATCH_SESSION=" + json.dumps(receipt, separators=(",", ":")))' "$1" "$name" "$owned"
}

if [ "$runner" = claude ]; then
  # A dispatched session runs without a person's pauses, so a 5-minute prompt cache costs
  # less than the 1-hour default. dispatch.py carries the full reason. A caller's value wins.
  export CLAUDE_CODE_PROMPT_CACHE_TTL="${CLAUDE_CODE_PROMPT_CACHE_TTL:-5m}"
  if [ "$read_only" = 1 ]; then
    readonly_tools=(Read Grep Glob)
    for tool in "${read_tools[@]}"; do
      if [[ ! "$tool" =~ ^mcp__[A-Za-z0-9_.:-]+__[A-Za-z0-9_.:-]+$ ]]; then
        echo "--read-tool must name one exact MCP tool, such as mcp__server__get_item: $tool" >&2
        exit 2
      fi
      readonly_tools+=("$tool")
    done

    # An allow rule in any settings file outranks dontAsk, so a rule such as
    # Bash(python3 -c *) would let a reviewer write. With no settings loaded, dontAsk runs
    # only Bash commands Claude Code classifies as read-only. An MCP read needs the
    # settings that configure its server, so that run keeps them and loses both shells.
    if [ "${#read_tools[@]}" -eq 0 ]; then
      settings_args=(--setting-sources "")
      shell_denials=(PowerShell)
    else
      settings_args=()
      shell_denials=(Bash PowerShell)
    fi

    args=(-p
      --permission-mode dontAsk
      --permission-prompts none
      "${settings_args[@]}"
      --allowedTools "${readonly_tools[@]}"
      --disallowedTools
        "${shell_denials[@]}"
        Edit
        Write
        NotebookEdit
        MultiEdit
        WebFetch
        WebSearch
        Task
        Agent
        CronCreate
        CronDelete
        DesignSync
        EnterWorktree
        ExitWorktree
        PushNotification
        RemoteTrigger
        ScheduleWakeup
        SendMessage
        ShareOnboardingGuide
        TaskStop
      --output-format json)
    [ -n "$model" ] && args+=(--model "$model")
    [ -n "$effort" ] && args+=(--effort "$effort")
    # A detached run always chooses its session id before launch now, with or without
    # --arc-dispatch, so it can print the receipt for it. Only a missing python3 skips this.
    session_id=""
    if [ "$has_python3" = 1 ]; then
      session_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
      args+=(--session-id "$session_id")
    fi
    status_file=""
    started_file=""
    if [ "$has_python3" = 1 ]; then
      status_file="$(mktemp -t dispatch-arc-status.XXXXXX)"
      started_file="$status_file.started"
    fi
    if [ -n "$notify_thread" ]; then
      DISPATCH_PROMPT="$prompt" nohup bash -c '
        cwd="$1"; claude_bin="$2"; codex_bin="$3"; log="$4"; notify_log="$5"; notify_thread="$6"; name="$7"; status_file="$8"; started_file="$9"
        shift 9
        cd "$cwd" || exit 70
        set +e
        if [ -n "$started_file" ]; then
          "$claude_bin" "$@" < "$DISPATCH_PROMPT" > "$log" 2>&1 &
          worker=$!
          printf "%s\n" "$worker" > "$started_file"
          wait "$worker"
          status=$?
        else
          "$claude_bin" "$@" < "$DISPATCH_PROMPT" > "$log" 2>&1
          status=$?
        fi
        if [ -n "$status_file" ]; then
          printf "%s\n" "$status" > "$status_file"
          for ((attempt=0; attempt<100; attempt++)); do
            if [ -e "$status_file.ack" ]; then rm -f -- "$status_file" "$status_file.ack"; break; fi
            sleep 0.01
          done
        fi
        state=completed
        if [ "$status" -ne 0 ]; then state=failed; fi
        message="Fresh-session handoff $name $state (exit $status). Result: $log"
        "$codex_bin" queue --thread "$notify_thread" --message "$message" > "$notify_log" 2>&1
        exit "$status"
      ' _ "$cwd" "$claude_bin" "$codex_bin" "$log" "$notify_log" "$notify_thread" "$name" "$status_file" "$started_file" "${args[@]}" "${extra[@]}" >/dev/null 2>&1 &
    elif [ "$has_python3" = 1 ]; then
      DISPATCH_PROMPT="$prompt" nohup bash -c '
        cd "$1" || exit 70
        bin="$2"; log="$3"; status_file="$4"; started_file="$5"; shift 5
        set +e
        "$bin" "$@" < "$DISPATCH_PROMPT" > "$log" 2>&1 &
        worker=$!
        printf "%s\n" "$worker" > "$started_file"
        wait "$worker"
        status=$?
        printf "%s\n" "$status" > "$status_file"
        for ((attempt=0; attempt<100; attempt++)); do
          if [ -e "$status_file.ack" ]; then rm -f -- "$status_file" "$status_file.ack"; break; fi
          sleep 0.01
        done
        exit "$status"
      ' _ "$cwd" "$claude_bin" "$log" "$status_file" "$started_file" "${args[@]}" "${extra[@]}" >/dev/null 2>&1 &
    else
      DISPATCH_PROMPT="$prompt" nohup bash -c 'cd "$1" && bin="$2" && shift 2 && "$bin" "$@" < "$DISPATCH_PROMPT"' _ "$cwd" "$claude_bin" "${args[@]}" "${extra[@]}" > "$log" 2>&1 &
    fi
    pid=$!
    echo "mode: read-only print"
    echo "runner: claude"
    echo "pid: $pid"
    print_common_record
    echo "result: $log"
    echo "watch: tail -f $(printf %q "$log")"
    if [ -n "$notify_thread" ]; then echo "notify-thread: $notify_thread"; echo "notify-log: $notify_log"; fi
    if [ "$has_python3" = 1 ]; then
      for ((attempt=0; attempt<300; attempt++)); do
        if [ -s "$started_file" ] || [ -s "$status_file" ]; then break; fi
        sleep 0.01
      done
      for ((attempt=0; attempt<10; attempt++)); do
        if [ -s "$status_file" ]; then break; fi
        sleep 0.05
      done
      if [ -s "$status_file" ]; then
        if [ "$(< "$status_file")" = 0 ] && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1],encoding="utf-8")).get("session_id")==sys.argv[2] else 1)' "$log" "$session_id" 2>/dev/null; then
          print_arc_receipt "$session_id"
        fi
      # The wrapper process ($pid) outlives the worker by up to a second while it waits
      # for the status file to be acknowledged below, so checking $pid here would call a
      # worker that has already exited with a failure or a mismatched id still running.
      # The worker's own pid, from started_file, is the one this must check.
      elif [ -s "$started_file" ] && kill -0 "$(< "$started_file")" 2>/dev/null; then
        print_arc_receipt "$session_id"
      fi
      rm -f -- "$started_file"
      touch "$status_file.ack"
      if [ -s "$status_file" ]; then rm -f -- "$status_file" "$status_file.ack"; fi
    fi
  else
    args=(--bg -n "$name" --permission-mode "${permmode:-auto}" --allowedTools "Bash Edit Write Read Grep Glob MultiEdit")
    [ "$worktree" = 1 ] && args+=(-w "$name")
    [ -n "$model" ] && args+=(--model "$model")
    [ -n "$effort" ] && args+=(--effort "$effort")
    out="$(cd "$cwd" && cat "$prompt" | "$claude_bin" "${args[@]}" "${extra[@]}" 2>&1)" || { echo "$out"; exit 1; }
    # Every launch runs this strict match, with or without --arc-dispatch, because the
    # receipt now prints by default. The loose fallback below still runs when the strict
    # match finds no line, such as a name dispatch.sh itself did not choose, or when it
    # finds more than one. Claude already started the session with exit 0 either way, so
    # ambiguous output must not abort a launch already under way: it only costs the
    # receipt, with a warning on stderr.
    id=""
    receipt_id=""
    launch_count=0
    idle_suffix=$' (idle \u2014 send a prompt to start)'
    while IFS= read -r line; do
      if [[ "$line" == *"$idle_suffix" ]]; then line="${line%"$idle_suffix"}"; fi
      prefix='backgrounded · '
      suffix=" · $name"
      if [[ "$line" == "$prefix"* && "$line" == *"$suffix" ]]; then
        candidate="${line#"$prefix"}"
        candidate="${candidate%"$suffix"}"
        if [[ "$candidate" =~ ^[0-9a-fA-F]{8}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
          id="$candidate"
          launch_count=$((launch_count + 1))
        fi
      fi
    done <<< "$out"
    if [ "$launch_count" -eq 1 ]; then receipt_id="$id"; fi
    if [ "$launch_count" -gt 1 ]; then
      echo "ambiguous Claude background launch output: printing no Arc receipt for this launch" >&2
      id=""
    fi
    if [ -z "$id" ]; then
      id="$(printf '%s\n' "$out" | grep -m1 'backgrounded' | awk -F'·' '{gsub(/ /, "", $2); print $2}')"
    fi
    [ -n "$id" ] || { echo "$out"; echo "no session id in the launch output" >&2; exit 1; }
    echo "mode: writable background"
    echo "runner: claude"
    echo "id: $id"
    echo "name: $name"
    if [ "$worktree" = 1 ]; then echo "tree: $(worktree_home "$cwd")/.claude/worktrees/$name"; echo "branch: worktree-$name"; else echo "tree: $cwd"; fi
    echo "model: ${model:-default}"
    echo "prompt: $prompt"
    echo "log: claude logs $id"
    echo "result: claude logs $id"
    echo "watch: claude logs $id"
    echo "attach: claude attach $id"
    echo "stop: claude stop $id"
    echo "remove: claude rm $id"
    if [ -n "$notify_thread" ]; then
      nohup "$skill_dir/watch-claude.sh" "$claude_bin" "$codex_bin" "$id" "$name" "$notify_thread" "claude logs $id" "claude attach $id" > "$notify_log" 2>&1 &
      echo "notify-thread: $notify_thread"
      echo "notify-log: $notify_log"
      echo "monitor-pid: $!"
    fi
    if [ "$attach" = 1 ]; then open_window "claude attach $(printf %q "$id")"; fi
    if [ "$has_python3" = 1 ] && [ -n "$receipt_id" ]; then
      print_arc_receipt "$receipt_id"
    fi
  fi
else
  if [ "$worktree" = 1 ]; then echo "codex makes no worktree: run git worktree add first and pass it with --cwd" >&2; exit 2; fi
  # workspace-write keeps .git read-only and blocks a browser start, and exec can never ask,
  # so a task that must commit, add a worktree or run a browser test runs unsandboxed.
  sandbox=workspace-write; [ "$read_only" = 1 ] && sandbox=read-only; [ "$full_access" = 1 ] && sandbox=danger-full-access
  last="${log%.*}.last.md"
  if [ "$attach" = 1 ]; then
    mode="writable attended"; [ "$read_only" = 1 ] && mode="read-only attended"; [ "$full_access" = 1 ] && mode="full-access attended"
    window_args=(-C "$cwd")
    [ -n "$model" ] && window_args+=(-m "$model")
    [ -n "$effort" ] && window_args+=(-c "model_reasoning_effort=$effort")
    window_args+=(-s "$sandbox" "${extra[@]}")
    printf -v command '%q' "$codex_bin"
    for arg in "${window_args[@]}"; do printf -v quoted ' %q' "$arg"; command+="$quoted"; done
    printf -v quoted '%q' "$prompt"
    open_window "$command \"\$(< $quoted)\""
    echo "mode: $mode"; echo "runner: codex"; print_common_record
    echo "result: attended session"; echo "watch: use the opened window"
  else
    args=(exec -C "$cwd" -s "$sandbox" -o "$last")
    [ -n "$model" ] && args+=(-m "$model")
    [ -n "$effort" ] && args+=(-c "model_reasoning_effort=$effort")
    if [ -n "$notify_thread" ]; then
      nohup bash -c '
        cwd="$1"; codex_bin="$2"; prompt="$3"; log="$4"; result="$5"; notify_log="$6"; notify_thread="$7"; name="$8"
        shift 8
        cd "$cwd" || exit 70
        set +e
        "$codex_bin" "$@" "$(< "$prompt")" > "$log" 2>&1
        status=$?
        state=completed
        if [ "$status" -ne 0 ]; then state=failed; fi
        message="Fresh-session handoff $name $state (exit $status). Result: $result"
        "$codex_bin" queue --thread "$notify_thread" --message "$message" > "$notify_log" 2>&1
        exit "$status"
      ' _ "$cwd" "$codex_bin" "$prompt" "$log" "$last" "$notify_log" "$notify_thread" "$name" "${args[@]}" "${extra[@]}" >/dev/null 2>&1 &
    else
      nohup "$codex_bin" "${args[@]}" "${extra[@]}" "$(< "$prompt")" > "$log" 2>&1 &
    fi
    pid=$!
    mode="writable detached"; [ "$read_only" = 1 ] && mode="read-only detached"; [ "$full_access" = 1 ] && mode="full-access detached"
    echo "mode: $mode"
    echo "runner: codex"; echo "pid: $pid"; print_common_record
    echo "result: $last"; echo "watch: tail -f $(printf %q "$log")"
    if [ -n "$notify_thread" ]; then echo "notify-thread: $notify_thread"; echo "notify-log: $notify_log"; fi
  fi
fi
