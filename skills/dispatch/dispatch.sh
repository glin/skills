#!/usr/bin/env bash
# Start a separate agent session from a prompt file through Arc.
#
#   dispatch.sh --runner claude|codex --prompt FILE --model M --effort E [--name N]
#               [--cwd DIR] [--add-dir DIR]... [--permission-mode MODE]
#               [--read-tool MCP_TOOL]... [--read-only] [--full-access] [--attach]
#               [--owned] [--log FILE] [--cache-ttl 5m|1h] [--wait] --arc-dispatch
#               [--service-tier default|fast|priority|ultrafast]
#   dispatch.sh --wait-launch LAUNCH_ID [--read-only] [--cwd DIR]
# Codex --service-tier selects speed once. fast aliases priority. Omission inherits
# effective defaults. Arc checks runtime support before launch.
# A reader-owned writable request starts in a ready Arc window through arc start when
# Python 3.10 can run dispatch.py's Arc route. A Claude request refuses an option arc start
# cannot keep unless --attach asks for a terminal. Every other request runs through arc run.
# --attach implies --owned. --arc-dispatch asks Arc for its ARC_DISPATCH_SESSION= marker.
#
# Prints Arc's launch JSON, a short record, and Arc's marker as the last line. With
# --wait, it prints the result JSON from arc dispatch result before the marker, except
# for --read-only, whose record names the result file.
set -euo pipefail
shopt -s extglob

# Each new process can take a second on Windows, so path and text handling stays in Bash
# builtins. abs_path sets REPLY to the absolute form of an existing directory, or of a
# file in one, without a subshell.
abs_path() {
  local path="${1//\\//}" here="$PWD" base=""
  if [ "${2:-}" = file ]; then
    case "$path" in */*) base="${path##*/}"; path="${path%/*}/" ;; *) base="$path"; path=. ;; esac
  fi
  cd -- "$path" || exit 2
  REPLY="$PWD"
  if [ -n "$base" ]; then REPLY="${PWD%/}/$base"; fi
  cd -- "$here"
}
case "$0" in */*|*\\*) abs_path "$0" file ;; *) abs_path "./$0" file ;; esac
skill_dir="${REPLY%/*}"
original_args=("$@")
runner=claude; prompt=""; model=""; effort=""; name=""; cwd="$PWD"; attach=0; owned=0; log=""
read_only=0; full_access=0; wait=0; arc_dispatch=0; permmode=""; wait_launch=""; cache_ttl=""
service_tier=""; service_tier_given=0
adddirs=()
read_tools=()
worktree_refusal="--worktree is not a dispatch flag. The calling workflow creates the worktree with git worktree add -b <branch> <path> <base>, then launches with --cwd <path>"
missing_arc="dispatch needs the Arc CLI: put arc on PATH or set ARC_BIN"
notify_refusal="--notify-thread is not a dispatch flag. Arc delivers no callback. Launch in the foreground with --arc-dispatch, then run --wait-launch LAUNCH_ID in the background, or read arc dispatch status and arc dispatch result later. A Claude parent can also use SendMessage with notify_when_idle for a writable Claude worker"
while [ $# -gt 0 ]; do
  case "$1" in
    --runner) runner="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --effort) effort="$2"; shift 2 ;;
    --service-tier|--service-tier=*)
      if [ "$service_tier_given" = 1 ]; then echo "--service-tier must be passed once" >&2; exit 2; fi
      service_tier_given=1
      if [ "$1" = --service-tier ]; then
        service_tier="${2:-}"; shift
        if [ $# -gt 0 ]; then shift; fi
      else
        service_tier="${1#*=}"; shift
      fi
      case "$service_tier" in default|fast|priority|ultrafast) ;; *) echo "--service-tier must be default, fast, priority, or ultrafast" >&2; exit 2 ;; esac
      ;;
    --name) name="$2"; shift 2 ;;
    --cwd) cwd="$2"; shift 2 ;;
    --worktree|--worktree=*|-w) echo "$worktree_refusal" >&2; exit 2 ;;
    --add-dir) adddirs+=("$2"); shift 2 ;;
    --permission-mode) permmode="$2"; shift 2 ;;
    --read-tool) read_tools+=("$2"); shift 2 ;;
    --read-only) read_only=1; shift ;;
    --full-access) full_access=1; shift ;;
    --attach) attach=1; shift ;;
    --owned) owned=1; shift ;;
    --log) log="$2"; shift 2 ;;
    --cache-ttl) cache_ttl="$2"; shift 2 ;;
    --notify-thread|--notify-thread=*) echo "$notify_refusal" >&2; exit 2 ;;
    --wait) wait=1; shift ;;
    --wait-launch) wait_launch="$2"; shift 2 ;;
    --arc-dispatch) arc_dispatch=1; shift ;;
    -h|--help)
      while IFS= read -r line; do
        case "$line" in "#!"*) ;; "#"*) printf '%s\n' "${line#"#"}" ;; *) break ;; esac
      done < "$0"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
# A session opened in a window is one the owner watches, whether or not --owned was
# also given.
if [ "$attach" = 1 ]; then owned=1; fi
if [ -z "$wait_launch" ]; then
if [ "$service_tier_given" = 1 ] && [ "$runner" != codex ]; then echo "--service-tier requires --runner codex" >&2; exit 2; fi
if [ -z "$prompt" ] || [ ! -f "$prompt" ]; then echo "--prompt FILE is required and must exist" >&2; exit 2; fi
case "$runner" in claude|codex) ;; *) echo "--runner must be claude or codex" >&2; exit 2 ;; esac
if [ "$full_access" = 1 ] && [ "$read_only" = 1 ]; then echo "--full-access and --read-only exclude each other" >&2; exit 2; fi
if [ "$full_access" = 1 ] && [ "$runner" = claude ]; then echo "--full-access is a Codex flag: use --permission-mode for a Claude background session" >&2; exit 2; fi
case "$cache_ttl" in ''|5m|1h) ;; *) echo "--cache-ttl must be 5m or 1h" >&2; exit 2 ;; esac
if [ -n "$cache_ttl" ] && [ "$runner" != claude ]; then echo "--cache-ttl is a Claude flag: Codex sets its own cache lifetime" >&2; exit 2; fi
# arc start takes no read tools, so this check runs before the window route.
if [ "${#read_tools[@]}" -gt 0 ] && { [ "$runner" != claude ] || [ "$read_only" != 1 ]; }; then
  echo "--read-tool requires --runner claude --read-only" >&2; exit 2
fi
# arc run refuses attached waited completion, so the Arc route refuses it too.
if [ "$attach" = 1 ] && [ "$wait" = 1 ]; then echo "--attach and --wait exclude each other" >&2; exit 2; fi
if [ "$arc_dispatch" != 1 ]; then
  echo "--arc-dispatch is required for a new session. Add --arc-dispatch to the foreground launch command" >&2; exit 2
fi
else
  observer_args=("${original_args[@]}")
  while [ "${#observer_args[@]}" -gt 0 ]; do
    case "${observer_args[0]}" in
      --wait-launch|--cwd) observer_args=("${observer_args[@]:2}") ;;
      --read-only) observer_args=("${observer_args[@]:1}") ;;
      *) echo "--wait-launch accepts only --read-only and --cwd. Observe the existing launch without launch flags" >&2; exit 2 ;;
    esac
  done
fi
arc_bin="${ARC_BIN:-arc}"
# Share Arc delivery classification with the Python launcher. Exit 3 means no accepted launch.
if [ -z "$wait_launch" ] && [ "$owned" = 1 ] && [ "$read_only" = 0 ]; then
  # dispatch.py prints the same refusal. This copy also holds when Python 3.10 is unavailable.
  if [ "$runner" = claude ] && [ "$attach" = 0 ]; then
    kept=""; changes=""
    if [ "${#adddirs[@]}" -gt 0 ]; then kept="--add-dir"; changes="remove --add-dir"; fi
    if [ -n "$log" ]; then kept="${kept:+$kept, }--log"; changes="${changes:+$changes and }remove --log"; fi
    if [ -n "$cache_ttl" ]; then kept="${kept:+$kept, }--cache-ttl"; changes="${changes:+$changes and }remove --cache-ttl"; fi
    case "$permmode" in
      dontAsk|bypassPermissions)
        kept="${kept:+$kept, }Claude --permission-mode $permmode"
        changes="${changes:+$changes and }choose a permission mode Arc offers, such as auto" ;;
    esac
    if [ -n "$kept" ]; then
      keep_change="add --attach"
      if [ "$wait" = 1 ]; then keep_change="add --attach and remove --wait"; fi
      echo "Reader-owned launch refused: Arc cannot keep $kept. No worker was started. Choose one of two relaunches. To start an Arc session the owner continues in Arc's prompt box, $changes. To keep the options in a terminal window the owner types in, $keep_change" >&2
      exit 2
    fi
  fi
  if python3 -c 'import sys; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
    dispatch_cli_bash="$BASH"
    case "$OSTYPE" in
      msys*|cygwin*) dispatch_cli_bash="$(cygpath -w "$dispatch_cli_bash")" ;;
    esac
    export DISPATCH_CLI_BASH="$dispatch_cli_bash"
    arc_status=0
    python3 "$skill_dir/dispatch.py" --internal-arc-only "${original_args[@]}" || arc_status=$?
    if [ "$arc_status" != 3 ]; then exit "$arc_status"; fi
  elif command -v "$arc_bin" >/dev/null 2>&1; then
    reach=""
    if [ "$runner" = claude ]; then reach=" The owner cannot send to that session from Arc."; fi
    echo "Arc route refused before acceptance: Python 3.10 is unavailable. Starting a standalone launch instead.$reach" >&2
  fi
fi

abs_path "$cwd"; cwd="$REPLY"
if [ -z "$wait_launch" ]; then
abs_path "$prompt" file; prompt="$REPLY"
# With --name, the default log is <prompt stem>.<name>.log, so two named launches from one
# prompt file keep separate results. dispatch.py builds the same path.
safe_log_name() {
  local LC_ALL=C
  REPLY="${1//+([^A-Za-z0-9._])/-}"
  REPLY="${REPLY#-}"; REPLY="${REPLY%-}"
}
log_given=0
if [ -z "$log" ]; then
  safe_log_name "$name"
  if [ -n "$REPLY" ]; then log="${prompt%.*}.$REPLY.log"; else log="${prompt%.*}.log"; fi
else
  log_given=1
  abs_path "$log" file; log="$REPLY"
fi
# printf %(...)T needs Bash 4.2, and macOS ships Bash 3.2.
if [ -z "$name" ]; then
  if [ "${BASH_VERSINFO[0]}" -gt 4 ] || { [ "${BASH_VERSINFO[0]}" = 4 ] && [ "${BASH_VERSINFO[1]}" -ge 2 ]; }; then
    printf -v name 'dispatch-%(%Y%m%d-%H%M%S)T' -1
  else
    name="dispatch-$(date +%Y%m%d-%H%M%S)"
  fi
fi

fi

# A Windows batch wrapper re-parses its arguments through cmd.exe, so dispatch runs the
# published JavaScript entry beside it through Node, as dispatch.py does.
arc_bin="${arc_bin//\\//}"
# A bare wrapper name such as arc.cmd resolves through PATH first, so its directory is known.
case "$arc_bin" in
  */*) ;;
  *.cmd|*.CMD|*.bat|*.BAT)
    if resolved="$(command -v "$arc_bin")"; then arc_bin="$resolved"; else echo "$missing_arc" >&2; exit 2; fi ;;
esac
case "$arc_bin" in
  *.cjs|*.js) arc=(node "$arc_bin") ;;
  *.cmd|*.CMD|*.bat|*.BAT)
    arc=()
    launcher_dir="${arc_bin%/*}"
    launcher_name="${arc_bin##*/}"
    case "$launcher_name" in [Aa][Rr][Cc].[Cc][Mm][Dd]|[Aa][Rr][Cc].[Bb][Aa][Tt]) ;; *) launcher_dir=/nonexistent ;; esac
    for entry in "$launcher_dir/run.cjs" "${launcher_dir%/*}/cli.cjs" "${launcher_dir%/*}/cli/arc.cjs" \
      "$launcher_dir/node_modules/@glin/arc/dist/arc.cjs" "${launcher_dir%/*}/@glin/arc/dist/arc.cjs"; do
      if [ -f "$entry" ]; then arc=(node "$entry"); break; fi
    done
    if [ "${#arc[@]}" = 0 ]; then
      echo "the Arc launcher cannot preserve literal arguments: set ARC_BIN to the Arc JavaScript entry" >&2; exit 2
    fi
    ;;
  *) arc=("$arc_bin") ;;
esac
if ! command -v "${arc[0]}" >/dev/null 2>&1; then
  echo "$missing_arc" >&2; exit 2
fi

# Sets REPLY to the first string value of one key in Arc's one-line JSON reply, with each
# escaped quote and backslash in the value unescaped.
json_field() {
  local pattern='"'"$1"'"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)"'
  REPLY=""
  if [[ "$2" =~ $pattern ]]; then
    REPLY="${BASH_REMATCH[1]//\\\"/\"}"
    REPLY="${REPLY//\\\\/\\}"
  fi
}


if [ -z "$wait_launch" ]; then
run_args=(run --provider "$runner" --cwd "$cwd" --prompt-file "$prompt" --name "$name")
if [ -n "$model" ]; then run_args+=(--model "$model"); fi
if [ -n "$effort" ]; then run_args+=(--effort "$effort"); fi
if [ "$service_tier_given" = 1 ]; then run_args+=(--service-tier "$service_tier"); fi
if [ "$read_only" = 1 ]; then run_args+=(--read-only); fi
# Bash 3.2 treats an empty array as unset under set -u, so each loop guards its expansion.
for tool in ${read_tools[@]+"${read_tools[@]}"}; do run_args+=(--read-tool "$tool"); done
if [ "$full_access" = 1 ]; then run_args+=(--permissions danger-full-access)
elif [ -n "$permmode" ]; then run_args+=(--permissions "$permmode"); fi
for d in ${adddirs[@]+"${adddirs[@]}"}; do run_args+=(--add-dir "$d"); done
# Explicit classification keeps a workflow worker under Run by agents and a session the
# reader asked for under reader ownership, whatever Arc's default becomes.
if [ "$owned" = 1 ]; then run_args+=(--agent-run=false); else run_args+=(--agent-run); fi
if [ "$attach" = 1 ]; then run_args+=(--attach); fi
# Attended Codex refuses log and report paths, so only the generated default is dropped.
# An explicit --log reaches Arc, which refuses it.
attended_codex=0
if [ "$attach" = 1 ] && [ "$runner" = codex ]; then attended_codex=1; fi
if [ "$attended_codex" = 0 ] || [ "$log_given" = 1 ]; then run_args+=(--log "$log"); fi
if [ "$wait" = 1 ]; then run_args+=(--completion wait); else run_args+=(--completion manual); fi
if [ "$arc_dispatch" = 1 ]; then run_args+=(--arc-dispatch); fi
run_args+=(--json)

if [ "$runner" = claude ] && [ -n "$cache_ttl" ]; then
  export CLAUDE_CODE_PROMPT_CACHE_TTL="$cache_ttl"
elif [ "$runner" = claude ] && [ "$owned" = 0 ]; then
  # A reader-owned launch keeps the provider default, as in dispatch.py.
  export CLAUDE_CODE_PROMPT_CACHE_TTL="${CLAUDE_CODE_PROMPT_CACHE_TTL:-5m}"
fi


status=0
out="$("${arc[@]}" "${run_args[@]}" < /dev/null)" || status=$?
receipt=""; marker=""
while IFS= read -r line; do
  case "$line" in
    "{"*) if [ -z "$receipt" ]; then receipt="$line"; fi ;;
    ARC_DISPATCH_SESSION=*) marker="${marker:+$marker$'\n'}$line" ;;
  esac
done <<< "$out"
json_field launchId "$receipt"; launch_id="$REPLY"
if [ "$status" != 0 ] || [ -z "$launch_id" ]; then
  if [ -n "$out" ]; then printf '%s\n' "$out"; fi
  if [ "$status" = 0 ]; then status=2; fi
  exit "$status"
fi
printf '%s\n' "$receipt"
# arc run replies with one of these states when its worker opened. Any other state, such
# as accepted-with-errors after a failed supervisor spawn, means no worker exists.
json_field state "$receipt"; launch_state="$REPLY"
case "$launch_state" in
  starting|observed|deadline) ;;
  *)
    json_field error "$receipt"
    echo "${REPLY:-arc run did not open launch $launch_id: state $launch_state}" >&2
    echo "status: arc dispatch status $launch_id"
    if [ -n "$marker" ]; then printf '%s\n' "$marker"; fi
    exit 2 ;;
esac
echo "mode: arc run"
echo "runner: $runner"
echo "name: $name"
echo "tree: $cwd"
echo "prompt: $prompt"
echo "launch: $launch_id"
if [ "$attended_codex" = 0 ]; then echo "log: $log"; fi
if [ "$runner" = codex ] && [ "$attach" = 0 ]; then echo "result: ${log%.*}.last.md"
elif [ "$read_only" = 1 ] && [ "$attended_codex" = 0 ]; then echo "result: $log"; fi
echo "status: arc dispatch status $launch_id"
printf -v observer_command '%q --wait-launch %q' "$skill_dir/dispatch.sh" "$launch_id"
if [ "$read_only" = 1 ]; then observer_command="$observer_command --read-only"; fi
echo "await: bash $observer_command"
if [ "$runner" = claude ] && [ "$read_only" = 0 ]; then
  for verb in logs attach stop; do echo "$verb: arc dispatch $verb $launch_id"; done
elif [ "$attach" = 0 ]; then
  printf -v quoted_log %q "$log"
  echo "watch: tail -f $quoted_log"
fi

else
  launch_id="$wait_launch"
  receipt='{"state":"deadline"}'
  marker=""
  wait=1
fi

code=0
if [ "$wait" = 1 ]; then
  # arc run returns at its own deadline. Each arc dispatch wait call blocks inside Arc,
  # so this loop never sleeps on its own. A failed read still reaches the marker below.
  # Arc's supervisor observes a worker for at most 24 hours, then publishes an outcome. A
  # wait still without one an hour later has a supervisor that stopped without publishing.
  wait_limit="${DISPATCH_WAIT_LIMIT_SECONDS:-}"
  case "$wait_limit" in ''|*[!0-9]*) wait_limit=90000 ;; esac
  wait_started=$SECONDS
  reply="$receipt"
  json_field state "$reply"
  while [ "$REPLY" = deadline ]; do
    if [ $((SECONDS - wait_started)) -ge "$wait_limit" ]; then
      echo "Arc published no outcome for launch $launch_id in $wait_limit seconds. Read arc dispatch status $launch_id before starting another worker." >&2
      code=2; break
    fi
    remaining_ms=$(((wait_limit - (SECONDS - wait_started)) * 1000))
    if [ "$remaining_ms" -gt 110000 ]; then remaining_ms=110000; fi
    if ! reply="$("${arc[@]}" dispatch wait "$launch_id" --json --deadline-ms "$remaining_ms" < /dev/null)"; then
      echo "Arc wait failed for launch $launch_id. Read arc dispatch status $launch_id before starting another worker." >&2
      code=2; break
    fi
    json_field state "$reply"
  done
  if [ "$code" = 0 ]; then
    state="$REPLY"
    json_field outcome "$reply"; outcome="$REPLY"
    echo "outcome: ${outcome:-${state:-unknown}}"
    json_field eventId "$reply"; event="$REPLY"
    if [ "$state" != observed ] || [ -z "$event" ]; then
      printf '%s\n' "$reply"
      code=1
    else
      # Arc publishes the outcome before it saves the final observation, so the read repeats
      # up to 40 times, a quarter second apart, while the supervisor still observes a worker that ended.
      worker_exit=""; exit_reads=0
      while status_reply="$("${arc[@]}" dispatch status "$launch_id" --json < /dev/null)"; do
        if [[ "$status_reply" =~ \"exitCode\"[[:space:]]*:[[:space:]]*(-?[0-9]+) ]]; then
          worker_exit="${BASH_REMATCH[1]}"
          echo "worker exit: $worker_exit"
          break
        fi
        exit_reads=$((exit_reads + 1))
        json_field monitor "$status_reply"
        if [ "$outcome" = needs-input ] || [ "$exit_reads" -ge 40 ]; then break; fi
        case "$REPLY" in starting|observing) sleep 0.25 ;; *) break ;; esac
      done
      # The observation comes last in Arc's status reply, so its reason is the first after it.
      if [ "$outcome" != succeeded ] && [[ "$status_reply" == *'"observation":'* ]]; then
        json_field reason "${status_reply#*\"observation\":}"
        if [ -n "$REPLY" ]; then echo "Launch $launch_id ended $outcome: $REPLY" >&2; fi
      fi
      if result="$("${arc[@]}" dispatch result "$launch_id" --event "$event" --json < /dev/null)"; then
        # A read-only launch serves a review, so its report stays out of the caller's output
        # until the caller blinds it. The record's result line names the file.
        if [ "$read_only" = 0 ]; then
          echo "--- result"
          printf '%s\n' "$result"
        else
          json_field path "$result"
          if [ -n "$REPLY" ]; then echo "result: $REPLY"; fi
        fi
        if [ -n "$worker_exit" ] && [ "$worker_exit" -gt 0 ] && [ "$worker_exit" -lt 256 ]; then code="$worker_exit"
        elif [ "$outcome" != succeeded ] || [ "${worker_exit:-0}" != 0 ]; then code=1; fi
      else
        echo "Arc could not read the result of launch $launch_id event $event." >&2
        code=2
      fi
    fi
  fi
fi
if [ -n "$marker" ]; then printf '%s\n' "$marker"; fi
exit "$code"
