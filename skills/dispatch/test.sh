#!/usr/bin/env bash
# Drives dispatch.py and dispatch.sh through a stub arc that records its arguments, and
# checks how each launcher maps its flags to arc run and prints Arc's replies. Each new
# process can take a second on Windows, so the checks use Bash builtins.
set -euo pipefail
shopt -s nullglob

skill_dir="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d -t dispatch-test.XXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
tmp_posix="$tmp"
case "$OSTYPE" in
  msys*|cygwin*)
    # Native Windows Python reads these paths, so they use the drive-letter form.
    tmp="$(cd "$tmp" && pwd -W)"
    DISPATCH_CLI_BASH="$(cygpath -w "$BASH")"
    export DISPATCH_CLI_BASH
    ;;
esac
mkdir -p "$tmp/bin" "$tmp/work" "$tmp/extra" "$tmp/calls"
printf 'Finish the bounded test task.\n' > "$tmp/prompt.md"

cat > "$tmp/bin/arc" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
dir="$DISPATCH_TEST_ARC_DIR"
calls=("$dir"/call-*)
printf '%s\n' "$@" > "$dir/call-${#calls[@]}"
launch=launch-1
case "$1" in
  windows) printf '{"version":1,"windows":[],"complete":true}\n' ;;
  run)
    printf '%s\n' "${CLAUDE_CODE_PROMPT_CACHE_TTL:-unset}" > "$dir/ttl"
    provider=""; name=""; wait=0; marker=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --provider) provider="$2"; shift 2 ;;
        --name) name="$2"; shift 2 ;;
        --completion) if [ "$2" = wait ]; then wait=1; fi; shift 2 ;;
        --arc-dispatch) marker=1; shift ;;
        *) shift ;;
      esac
    done
    if [ -n "${DISPATCH_TEST_ARC_REFUSE:-}" ]; then printf '%s\n' "$DISPATCH_TEST_ARC_REFUSE" >&2; exit 1; fi
    if [ -n "${DISPATCH_TEST_ARC_STATE:-}" ]; then
      printf '{"launchId":"%s","transport":"standalone","state":"%s","completion":"manual","origin":"o","error":"The launch did not open. Read \\"its status\\" in C:\\\\arc before starting another session."}\n' "$launch" "$DISPATCH_TEST_ARC_STATE"
    elif [ "$wait" = 1 ]; then
      printf '{"launchId":"%s","state":"deadline"}\n' "$launch"
    else
      printf '{"launchId":"%s","transport":"standalone","state":"starting","completion":"manual","origin":"o"}\n' "$launch"
    fi
    if [ "$marker" = 1 ] && [ -z "${DISPATCH_TEST_PENDING:-}" ]; then
      printf 'ARC_DISPATCH_SESSION={"agent":"%s","session_id":"12345678-1234-1234-1234-123456789abc","name":"%s"}\n' "$provider" "$name"
    fi
    ;;
  dispatch)
    case "$2" in
      wait|result)
        if [ "${DISPATCH_TEST_ARC_FAIL:-}" = "$2" ]; then printf 'Arc %s is unavailable.\n' "$2" >&2; exit 1; fi
        ;;&
      wait)
        waits=("$dir"/wait-*)
        : > "$dir/wait-${#waits[@]}"
        if [ "${#waits[@]}" -lt "${DISPATCH_TEST_ARC_DEADLINES:-0}" ]; then
          printf '{"launchId":"%s","state":"deadline"}\n' "$launch"
        else
          printf '{"launchId":"%s","state":"observed","eventId":"event-1","outcome":"%s","availability":"available"}\n' "$launch" "${DISPATCH_TEST_ARC_OUTCOME:-succeeded}"
        fi
        ;;
      status)
        # The first DISPATCH_TEST_ARC_UNSAVED reads find the final observation not yet saved.
        statuses=("$dir"/status-*)
        : > "$dir/status-${#statuses[@]}"
        exit_code="${DISPATCH_TEST_ARC_EXIT:-0}"; monitor=finished
        if [ "${#statuses[@]}" -lt "${DISPATCH_TEST_ARC_UNSAVED:-0}" ]; then exit_code=null; monitor=observing; fi
        printf '{"version":1,"launch":{"launchId":"%s"},"events":[],"observation":{"exitCode":%s,"monitor":"%s","reason":"%s"}}\n' \
          "$launch" "$exit_code" "$monitor" "${DISPATCH_TEST_ARC_REASON:-}"
        ;;
      result)
        printf '{"launchId":"%s","eventId":"event-1","outcome":"succeeded","report":{"path":"/r.md","sha256":"x","bytes":40},"text":"worker report\\nARC_DISPATCH_SESSION={\\"agent\\":\\"claude\\"}\\n"}\n' "$launch"
        ;;
    esac
    ;;
  *) printf 'unexpected arc command: %s\n' "$1" >&2; exit 2 ;;
esac
STUB
chmod +x "$tmp/bin/arc"
export ARC_BIN="$tmp/bin/arc"
export DISPATCH_TEST_ARC_DIR="$tmp/calls"
unset CLAUDE_CODE_PROMPT_CACHE_TTL ARC_WINDOW DISPATCH_TEST_ARC_REFUSE DISPATCH_TEST_ARC_DEADLINES DISPATCH_TEST_ARC_OUTCOME \
  DISPATCH_TEST_ARC_STATE DISPATCH_TEST_ARC_FAIL DISPATCH_TEST_ARC_EXIT DISPATCH_TEST_ARC_UNSAVED DISPATCH_TEST_ARC_REASON DISPATCH_WAIT_LIMIT_SECONDS

fail() { printf '%s: %s\n' "$launcher" "$1" >&2; exit 1; }
# Windows Python prints C:\dir paths. Git Bash prints /c/dir paths, or /tmp paths for the
# fixture directory. Each becomes the C:/dir form.
normalize() {
  set -- "${1//$tmp_posix/$tmp}"
  REPLY="${1//\\//}"
  if [[ "$REPLY" =~ ^(.*[\ =])?/([a-z])/(.*)$ ]]; then
    REPLY="${BASH_REMATCH[1]}${BASH_REMATCH[2]^^}:/${BASH_REMATCH[3]}"
  fi
}
read_lines() {
  local -n into="$1"
  into=()
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    normalize "${line%$'\r'}"
    into+=("$REPLY")
  done < "$2"
}
# Run one launcher, then load the arc run arguments, the output, and the error text.
launch() {
  local file
  rm -f -- "$tmp/calls/"*
  code=0
  if [ "$launcher" = py ]; then
    python3 "$skill_dir/dispatch.py" ${launch_marker:+--arc-dispatch} "$@" > "$tmp/out" 2> "$tmp/err" || code=$?
  else
    bash "$skill_dir/dispatch.sh" ${launch_marker:+--arc-dispatch} "$@" > "$tmp/out" 2> "$tmp/err" || code=$?
  fi
  verbs=(); run=()
  for file in "$tmp/calls/"call-*; do
    read_lines call "$file"
    verbs+=("${call[0]} ${call[1]:-}")
    if [ "${call[0]}" = run ]; then run=("${call[@]}"); fi
  done
  read_lines out "$tmp/out"
  read_lines err "$tmp/err"
}
has_pair() {
  local i
  for ((i = 0; i + 1 < ${#run[@]}; i++)); do
    if [ "${run[i]}" = "$1" ] && [ "${run[i + 1]}" = "$2" ]; then return 0; fi
  done
  return 1
}
expect_pair() { has_pair "$1" "$2" || fail "arc run lacks $1 $2"; }
has_flag() { local item; for item in "${run[@]}"; do if [ "$item" = "$1" ]; then return 0; fi; done; return 1; }
expect_flag() { has_flag "$1" || fail "arc run lacks $1"; }
reject_flag() { if has_flag "$1"; then fail "arc run carries $1"; fi; }
has_line() { local -n lines="$1"; local item; for item in "${lines[@]}"; do if [[ "$item" == *"$2"* ]]; then return 0; fi; done; return 1; }
expect_out() { has_line out "$1" || fail "output lacks: $1"; }
reject_out() { if has_line out "$1"; then fail "output carries: $1"; fi; }
expect_no_arc_call() { [ "${#verbs[@]}" = 0 ] || fail "$1 called arc as: ${verbs[*]}"; }
expect_exit() { [ "$code" = "$1" ] || fail "$2 exited $code: ${err[*]}"; }
# Arc's marker is the last line, and no other line starts with it.
marker_count() { local item; markers=0; for item in "${out[@]}"; do if [[ "$item" == ARC_DISPATCH_SESSION=* ]]; then markers=$((markers + 1)); fi; done; }
expect_marker_last() {
  marker_count
  [[ "${out[-1]}" == ARC_DISPATCH_SESSION=* ]] || fail "the marker is not the last line"
  [ "$markers" = 1 ] || fail "$markers lines carry the marker"
}

for launcher in py sh; do
  launch_marker=""
  for runner in claude codex; do
    launch --runner "$runner" --owned --model sonnet --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
    expect_exit 2 "an unmarked $runner launch"
    has_line err "--arc-dispatch is required" || fail "the marker refusal is missing"
    expect_no_arc_call "an unmarked $runner launch"
  done
  launch_marker=1
  # A read-only Claude launch with an explicit log and the marker.
  launch --runner claude --read-only --model sonnet --effort high --name review-1 \
    --read-tool mcp__example__get_item --add-dir "$tmp/extra" --cwd "$tmp/work" \
    --prompt "$tmp/prompt.md" --log "$tmp/review.log" --arc-dispatch
  expect_exit 0 "read-only Claude"
  expect_pair --provider claude; expect_pair --model sonnet; expect_pair --effort high
  expect_pair --name review-1; expect_pair --read-tool mcp__example__get_item
  expect_pair --add-dir "$tmp/extra"; expect_pair --cwd "$tmp/work"
  expect_pair --prompt-file "$tmp/prompt.md"; expect_pair --log "$tmp/review.log"
  expect_flag --read-only; expect_flag --arc-dispatch; expect_flag --agent-run; expect_flag --json
  expect_pair --completion manual; reject_flag --permissions; reject_flag --agent-run=false
  read_lines ttl "$tmp/calls/ttl"
  [ "${ttl[0]}" = 5m ] || fail "Claude runs without the 5-minute cache"
  expect_out "result: $tmp/review.log"; expect_out '{"launchId":"launch-1"'
  expect_out "status: arc dispatch status launch-1"
  expect_marker_last

  # A Codex launch maps --full-access and builds the default log from the name.
  launch --runner codex --full-access --model gpt-6.1-sol --effort xhigh --name fix-1 \
    --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 0 "Codex"
  expect_pair --provider codex; expect_pair --permissions danger-full-access
  expect_pair --log "$tmp/prompt.fix-1.log"
  expect_flag --arc-dispatch; reject_flag --read-only
  expect_out "result: $tmp/prompt.fix-1.last.md"
  expect_marker_last

  # A pending native identity keeps its launch even when no waiter starts.
  export DISPATCH_TEST_PENDING=1
  launch --runner codex --read-only --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 0 "a pending foreground launch"
  [ "${verbs[*]}" = "run --provider" ] || fail "foreground launch started observation"
  expect_out "--wait-launch launch-1 --read-only"
  reject_out "ARC_DISPATCH_SESSION="
  unset DISPATCH_TEST_PENDING

  # Observation uses only the saved launch and keeps review reports blinded.
  launch_marker=""
  export DISPATCH_TEST_ARC_DEADLINES=2
  launch --wait-launch launch-1 --read-only
  expect_exit 0 "observation of a pending launch"
  [ "${verbs[*]}" = "dispatch wait dispatch wait dispatch wait dispatch status dispatch result" ] \
    || fail "observation called arc as: ${verbs[*]}"
  expect_out "result: /r.md"; reject_out "worker report"; reject_out "ARC_DISPATCH_SESSION="
  for outcome in failed unknown needs-input; do
    export DISPATCH_TEST_ARC_OUTCOME="$outcome" DISPATCH_TEST_ARC_DEADLINES=0 DISPATCH_TEST_ARC_EXIT=7
    launch --wait-launch launch-1
    expect_exit 7 "a $outcome worker exiting 7"
    expect_out "outcome: $outcome"; expect_out "worker report"
  done
  unset DISPATCH_TEST_ARC_OUTCOME DISPATCH_TEST_ARC_EXIT
  export DISPATCH_WAIT_LIMIT_SECONDS=0
  launch --wait-launch launch-1
  expect_exit 2 "observation past its bound"
  expect_no_arc_call "observation past its bound"
  unset DISPATCH_WAIT_LIMIT_SECONDS DISPATCH_TEST_ARC_DEADLINES
  export DISPATCH_TEST_ARC_FAIL=wait
  launch --wait-launch launch-1
  expect_exit 2 "an absent waiter"
  [ "${verbs[*]}" = "dispatch wait" ] || fail "an absent waiter launched a worker"
  unset DISPATCH_TEST_ARC_FAIL
  launch_marker=1

  # --wait follows arc run past two deadlines, prints the report, and keeps the marker last.
  export DISPATCH_TEST_ARC_DEADLINES=2
  launch --runner codex --wait --arc-dispatch --model gpt-6.1-sol --effort high \
    --cwd "$tmp/work" --prompt "$tmp/prompt.md" --log "$tmp/wait.log"
  expect_exit 0 "a successful wait"
  expect_pair --completion wait; expect_pair --log "$tmp/wait.log"
  [ "${verbs[*]}" = "run --provider dispatch wait dispatch wait dispatch wait dispatch status dispatch result" ] \
    || fail "wait called arc as: ${verbs[*]}"
  expect_out "--- result"; expect_out "worker report"; expect_out "outcome: succeeded"; expect_out "worker exit: 0"
  expect_marker_last

  # A waited read-only review prints its result path and the marker, never the report text.
  launch --runner codex --read-only --wait --arc-dispatch --model gpt-6.1-sol --effort high \
    --cwd "$tmp/work" --prompt "$tmp/prompt.md" --log "$tmp/review.jsonl"
  expect_exit 0 "a waited review"
  expect_out "result: $tmp/review.last.md"; reject_out "--- result"; reject_out "worker report"
  expect_marker_last

  # A failed outcome sets a nonzero exit and prints Arc's reason.
  export DISPATCH_TEST_ARC_DEADLINES=0 DISPATCH_TEST_ARC_OUTCOME=failed DISPATCH_TEST_ARC_REASON="The provider did not start."
  launch --runner codex --wait --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 1 "a failed wait"
  has_line err "Launch launch-1 ended failed: The provider did not start." || fail "a failed wait names no cause"
  unset DISPATCH_TEST_ARC_REASON

  # The worker's exit code passes through when Arc saves it after publishing the outcome.
  export DISPATCH_TEST_ARC_EXIT=3 DISPATCH_TEST_ARC_UNSAVED=2
  launch --runner codex --wait --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 3 "a worker that exited 3"
  expect_out "worker exit: 3"

  # A recorded exit code outside 1 to 255, such as a Windows crash code, exits 1.
  export DISPATCH_TEST_ARC_OUTCOME=succeeded DISPATCH_TEST_ARC_EXIT=3221225477 DISPATCH_TEST_ARC_UNSAVED=0
  launch --runner codex --wait --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 1 "a worker with a Windows crash code"
  expect_out "worker exit: 3221225477"
  unset DISPATCH_TEST_ARC_DEADLINES DISPATCH_TEST_ARC_OUTCOME DISPATCH_TEST_ARC_EXIT DISPATCH_TEST_ARC_UNSAVED

  # A wait past its bound stops with the marker last and starts no further wait.
  export DISPATCH_TEST_ARC_DEADLINES=5 DISPATCH_WAIT_LIMIT_SECONDS=0
  launch --runner codex --wait --arc-dispatch --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 2 "a wait past its bound"
  [ "${verbs[*]}" = "run --provider" ] || fail "a wait past its bound called arc as: ${verbs[*]}"
  has_line err "Arc published no outcome for launch launch-1" || fail "the bounded wait names no cause"
  expect_marker_last
  unset DISPATCH_TEST_ARC_DEADLINES DISPATCH_WAIT_LIMIT_SECONDS

  # An owned writable Claude request with no Arc window runs standalone under reader ownership.
  launch --runner claude --owned --permission-mode acceptEdits --model sonnet --effort high \
    --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 0 "owned writable Claude"
  has_line err "no Arc window is available" || fail "the Arc route did not report its refusal"
  expect_pair --permissions acceptEdits; expect_flag --agent-run=false; reject_flag --agent-run
  expect_out "attach: arc dispatch attach launch-1"

  # An Arc refusal reaches the caller with Arc's exit status.
  export DISPATCH_TEST_ARC_REFUSE="Standalone run requires --model."
  launch --runner claude --read-only --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 1 "an Arc refusal"
  has_line err "Standalone run requires --model." || fail "the Arc refusal text is missing"
  unset DISPATCH_TEST_ARC_REFUSE

  # A launch Arc accepted but never opened keeps its receipt, prints Arc's error, and fails.
  export DISPATCH_TEST_ARC_STATE=accepted-with-errors
  launch --runner codex --read-only --arc-dispatch --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 2 "an unopened launch"
  expect_out '"state":"accepted-with-errors"'; expect_out "status: arc dispatch status launch-1"
  reject_out "mode: arc run"
  has_line err 'The launch did not open. Read "its status" in C:/arc before starting another session.' \
    || fail "Arc's launch error is missing or cut at an escaped quote"
  unset DISPATCH_TEST_ARC_STATE

  # --read-tool outside Claude --read-only refuses before any Arc call, on the window route too.
  for request in "--runner claude --owned" "--runner codex"; do
    # shellcheck disable=SC2086
    launch $request --read-tool mcp__example__get_item --model sonnet --effort high \
      --cwd "$tmp/work" --prompt "$tmp/prompt.md"
    expect_exit 2 "--read-tool with $request"
    has_line err "--read-tool requires --runner claude --read-only" || fail "the --read-tool refusal is missing"
    expect_no_arc_call "--read-tool with $request"
  done

  # Attended Codex reaches arc run under reader ownership with no log and no result path.
  launch --runner codex --attach --read-only --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 0 "attended Codex"
  expect_flag --attach; expect_flag --agent-run=false; reject_flag --agent-run; reject_flag --log
  reject_out "log:"; reject_out "result:"

  # An explicit --log for attended Codex reaches Arc, which refuses it.
  launch --runner codex --attach --full-access --model gpt-6.1-sol --effort high --cwd "$tmp/work" \
    --prompt "$tmp/prompt.md" --log "$tmp/attended.log"
  expect_pair --log "$tmp/attended.log"; expect_flag --attach

  # Attended Claude keeps its default log.
  launch --runner claude --attach --model sonnet --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
  expect_exit 0 "attended Claude"
  expect_flag --attach; expect_flag --agent-run=false; expect_pair --log "$tmp/prompt.log"

  # A failed wait or result read still prints the marker last, so the worker stays linked.
  export DISPATCH_TEST_ARC_DEADLINES=1
  for step in wait result; do
    export DISPATCH_TEST_ARC_FAIL="$step"
    launch --runner codex --read-only --wait --arc-dispatch --model gpt-6.1-sol --effort high \
      --cwd "$tmp/work" --prompt "$tmp/prompt.md"
    expect_exit 2 "a failed $step"
    expect_marker_last
    has_line err "launch launch-1" || fail "the failed $step names no launch"
  done
  unset DISPATCH_TEST_ARC_DEADLINES DISPATCH_TEST_ARC_FAIL
done

# A bare ARC_BIN=arc.cmd resolves through PATH to the JavaScript entry beside its directory.
launcher=sh
mkdir -p "$tmp/pkg/bin"
printf '@echo off\r\n' > "$tmp/pkg/bin/arc.cmd"
chmod +x "$tmp/pkg/bin/arc.cmd"
cat > "$tmp/pkg/cli.cjs" <<'ENTRY'
const run = require('child_process').spawnSync(process.env.DISPATCH_TEST_BASH, [process.env.DISPATCH_TEST_STUB, ...process.argv.slice(2)], { stdio: 'inherit' });
process.exit(run.status ?? 1);
ENTRY
export DISPATCH_TEST_BASH="${DISPATCH_CLI_BASH:-$BASH}" DISPATCH_TEST_STUB="$ARC_BIN"
saved_path="$PATH"
export ARC_BIN=arc.cmd PATH="$tmp_posix/pkg/bin:$PATH"
launch --runner codex --read-only --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
export ARC_BIN="$DISPATCH_TEST_STUB" PATH="$saved_path"
expect_exit 0 "a PATH-based arc.cmd"
expect_pair --provider codex

# A bare ARC_BIN=arc.cmd that PATH cannot find names the missing CLI.
# PATH holds only system tools, so an installed Arc never runs.
export ARC_BIN=arc.cmd PATH="/usr/bin:/bin"
launch --runner codex --read-only --model gpt-6.1-sol --effort high --cwd "$tmp/work" --prompt "$tmp/prompt.md"
export ARC_BIN="$DISPATCH_TEST_STUB" PATH="$saved_path"
expect_exit 2 "an arc.cmd missing from PATH"
has_line err "dispatch needs the Arc CLI: put arc on PATH or set ARC_BIN" || fail "the missing arc.cmd names no PATH cause"

printf 'dispatch launcher tests passed\n'
