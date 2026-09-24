#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d -t dispatch-test.XXXXXX)"
cleanup() {
  if [ -n "${tmp:-}" ] && [ -d "$tmp" ]; then rm -rf -- "$tmp"; fi
}
trap cleanup EXIT

mkdir -p "$tmp/bin" "$tmp/work"
printf 'Finish the bounded test task.\n' > "$tmp/prompt.md"

cat > "$tmp/bin/codex" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  exec)
    shift
    result=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -o) result="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf 'worker output\n'
    if [ -n "$result" ]; then printf 'durable result\n' > "$result"; fi
    exit "${DISPATCH_TEST_EXEC_STATUS:-0}"
    ;;
  queue)
    shift
    if [ -n "${DISPATCH_TEST_EXPECT_RESULT:-}" ] && [ ! -f "$DISPATCH_TEST_EXPECT_RESULT" ]; then
      printf 'callback arrived before the result file\n' >&2
      exit 9
    fi
    printf '%s\n' "$*" >> "$DISPATCH_TEST_QUEUE_LOG"
    ;;
  *)
    printf 'unexpected codex command: %s\n' "${1:-}" >&2
    exit 2
    ;;
esac
STUB
chmod +x "$tmp/bin/codex"

cat > "$tmp/bin/claude" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = agents ]; then
  count=0
  if [ -f "$DISPATCH_TEST_CLAUDE_STATE_FILE" ]; then count="$(< "$DISPATCH_TEST_CLAUDE_STATE_FILE")"; fi
  printf '%s\n' "$((count + 1))" > "$DISPATCH_TEST_CLAUDE_STATE_FILE"
  case "${DISPATCH_TEST_CLAUDE_SEQUENCE:-complete}" in
    complete)
      if [ "$count" -eq 0 ]; then
        printf '[{"id":"claude-test","status":"busy","state":"working"}]\n'
      else
        printf '[]\n'
      fi
      ;;
    blocked)
      printf '[{"id":"claude-test","state":"blocked"}]\n'
      ;;
    multi_agent)
      if [ "$count" -eq 0 ]; then
        printf '[{"id":"other","state":"failed"},{"id":"claude-test","state":"working"}]\n'
      else
        printf '[]\n'
      fi
      ;;
    unknown)
      printf '[{"id":"claude-test","state":"surprised"}]\n'
      ;;
    missing)
      printf '[]\n'
      ;;
    working)
      printf '[{"id":"claude-test","state":"working"}]\n'
      ;;
    registry_error)
      exit 4
      ;;
    *) exit 4 ;;
  esac
  exit 0
fi
if [ "${1:-}" = -p ]; then
  printf '%s\n' "$@" > "$DISPATCH_TEST_CLAUDE_ARGS"
  cat >/dev/null
  session_id=""
  args=("$@")
  for ((i=0; i<${#args[@]}; i++)); do
    if [ "${args[i]}" = --session-id ]; then session_id="${args[i+1]}"; fi
  done
  if [ -n "${DISPATCH_TEST_CLAUDE_SESSION_ID:-}" ]; then session_id="$DISPATCH_TEST_CLAUDE_SESSION_ID"; fi
  if [ -n "${DISPATCH_TEST_CLAUDE_SLEEP:-}" ]; then sleep "$DISPATCH_TEST_CLAUDE_SLEEP"; fi
  if [ -n "$session_id" ]; then
    printf '{"result":"read-only result","session_id":"%s"}\n' "$session_id"
  else
    printf '{"result":"read-only result"}\n'
  fi
  exit "${DISPATCH_TEST_CLAUDE_PRINT_STATUS:-0}"
fi
printf '%s\n' "$@" > "$DISPATCH_TEST_CLAUDE_ARGS"
if [ -n "${DISPATCH_TEST_CLAUDE_STDIN:-}" ]; then cat > "$DISPATCH_TEST_CLAUDE_STDIN"; fi
if [ -n "${DISPATCH_TEST_CLAUDE_BG_STATUS:-}" ]; then exit "$DISPATCH_TEST_CLAUDE_BG_STATUS"; fi
args=("$@")
name=""
for ((i=0; i<${#args[@]}; i++)); do
  if [ "${args[i]}" = -n ]; then name="${args[i+1]}"; fi
done
if [ -n "${DISPATCH_TEST_CLAUDE_BG_DIAGNOSTIC:-}" ]; then
  printf 'diagnostic: backgrounded · deadbeef · decoy\n'
fi
suffix=""
if [ -n "${DISPATCH_TEST_CLAUDE_BG_IDLE:-}" ]; then suffix=$' (idle \u2014 send a prompt to start)'; fi
printf 'backgrounded · %s · %s%s\n' "${DISPATCH_TEST_CLAUDE_BG_ID:-claude-test}" "$name" "$suffix"
if [ -n "${DISPATCH_TEST_CLAUDE_BG_DUPLICATE:-}" ]; then
  printf 'backgrounded · feedface · %s\n' "$name"
fi
STUB
chmod +x "$tmp/bin/claude"

wait_for_queue() {
  local queue_log="$1"
  local attempt
  for attempt in $(seq 1 100); do
    if [ -s "$queue_log" ]; then return 0; fi
    sleep 0.05
  done
  printf 'callback was not queued\n' >&2
  return 1
}

assert_single_notice() {
  local queue_log="$1"
  local expected="$2"
  wait_for_queue "$queue_log"
  [ "$(wc -l < "$queue_log")" -eq 1 ]
  grep -F -- "$expected" "$queue_log" >/dev/null
}

# Stand in for open_window's real terminal launcher on the current platform, so an
# --attach test can run without ever opening a real window, on any platform. Leaves the
# stub's name in $STUB_LAUNCHER_NAME, after confirming PATH resolves it to the stub and
# not to a real launcher; the caller runs dispatch.sh with "$tmp/term-bin" prepended to
# PATH. Called plain, not in a command substitution, because a set TMUX (unset here on
# the non-Windows, non-Darwin branch) would make open_window reuse the running session
# instead of this stub, and a subshell call would drop that unset on return.
stub_terminal_launcher() {
  local resolved
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) STUB_LAUNCHER_NAME=wt ;;
    Darwin) STUB_LAUNCHER_NAME=osascript ;;
    *) STUB_LAUNCHER_NAME=x-terminal-emulator; unset TMUX ;;
  esac
  mkdir -p "$tmp/term-bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/term-bin/$STUB_LAUNCHER_NAME"
  chmod +x "$tmp/term-bin/$STUB_LAUNCHER_NAME"
  resolved="$(PATH="$tmp/term-bin:$PATH" command -v "$STUB_LAUNCHER_NAME")"
  if [ "$resolved" != "$tmp/term-bin/$STUB_LAUNCHER_NAME" ]; then
    printf '%s resolves to something other than this test'"'"'s stub; refusing to run dispatch.sh --attach, which would open a real terminal window\n' "$STUB_LAUNCHER_NAME" >&2
    return 1
  fi
}

export CODEX_BIN="$tmp/bin/codex"
export CLAUDE_BIN="$tmp/bin/claude"
export DISPATCH_TEST_CLAUDE_ARGS="$tmp/claude-args.log"

default_record="$(bash "$skill_dir/dispatch.sh" --runner claude --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
default_mode="$(awk '$0 == "--permission-mode" { getline; print; exit }' "$DISPATCH_TEST_CLAUDE_ARGS")"
[ "$default_mode" = auto ]
grep -F -- 'id: claude-test' <<< "$default_record" >/dev/null
# claude-test is not hex-shaped, so it is not a usable receipt id: no receipt prints for
# it, with or without --arc-dispatch. A default launch with a usable id is asserted to
# print one below, once DISPATCH_TEST_CLAUDE_BG_ID is set to a hex id.
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$default_record" >/dev/null; then
  printf 'default Claude launch with an unusable id printed an Arc receipt\n' >&2; exit 1
fi

export DISPATCH_TEST_CLAUDE_BG_ID=a1b2c3d4
export DISPATCH_TEST_CLAUDE_STDIN="$tmp/claude-arc-stdin.txt"
arc_writable="$(bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --name 'Review "branch"' --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
grep -Fx -- 'ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review \"branch\""}' <<< "$arc_writable" >/dev/null
cmp "$tmp/prompt.md" "$DISPATCH_TEST_CLAUDE_STDIN"
unset DISPATCH_TEST_CLAUDE_STDIN
export DISPATCH_TEST_CLAUDE_BG_DIAGNOSTIC=1
arc_decoy="$(bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
grep -F -- '"session_id":"a1b2c3d4"' <<< "$arc_decoy" >/dev/null
if grep -F -- '"session_id":"deadbeef"' <<< "$arc_decoy" >/dev/null; then
  printf 'diagnostic id became an Arc receipt\n' >&2; exit 1
fi
unset DISPATCH_TEST_CLAUDE_BG_DIAGNOSTIC
export DISPATCH_TEST_CLAUDE_BG_IDLE=1
arc_idle="$(bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
grep -F -- '"session_id":"a1b2c3d4"' <<< "$arc_idle" >/dev/null
unset DISPATCH_TEST_CLAUDE_BG_IDLE
export DISPATCH_TEST_CLAUDE_BG_DUPLICATE=1
# Claude already started the session with exit 0, so ambiguous launch-line output must not
# abort the launch: it costs only the receipt, with a warning on stderr, and the record
# (including an id, from the loose fallback) still prints.
ambiguous_out="$(bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work" 2>"$tmp/arc-ambiguous.err")" || {
  printf 'ambiguous Claude launch unexpectedly failed\n' >&2; exit 1
}
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$ambiguous_out" >/dev/null; then
  printf 'ambiguous Claude launch printed an Arc receipt\n' >&2; exit 1
fi
grep -F -- 'ambiguous Claude background launch output' "$tmp/arc-ambiguous.err" >/dev/null
grep -F -- 'mode: writable background' <<< "$ambiguous_out" >/dev/null
unset DISPATCH_TEST_CLAUDE_BG_DUPLICATE
arc_unicode="$(bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --name 'Review 😀' --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
arc_unicode_receipt="$(grep '^ARC_DISPATCH_SESSION=' <<< "$arc_unicode")"
python3 -c 'import json,sys; assert json.loads(sys.argv[1].split("=",1)[1])["name"]=="Review 😀"; assert sys.argv[1].isascii()' "$arc_unicode_receipt"
arc_baseline="$(bash "$skill_dir/dispatch.sh" --runner claude --name 'Review "branch"' --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
[ "$arc_baseline" = "$arc_writable" ]
if ! grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$arc_baseline" >/dev/null; then
  printf 'default Claude launch with a usable id printed no Arc receipt\n' >&2; exit 1
fi
mkdir -p "$tmp/no-python"
printf '#!/usr/bin/env bash\nexit 127\n' > "$tmp/no-python/python3"
chmod +x "$tmp/no-python/python3"
rm -f -- "$DISPATCH_TEST_CLAUDE_ARGS"
no_python_out="$(PATH="$tmp/no-python:$PATH" bash "$skill_dir/dispatch.sh" --runner claude --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work" 2>"$tmp/arc-no-python.err")"
[ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] || { printf 'Claude launch without python3 never started\n' >&2; exit 1; }
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$no_python_out" >/dev/null; then
  printf 'launch without python3 printed an Arc receipt\n' >&2; exit 1
fi
grep -F -- 'no python3 on PATH: this launch has no Arc receipt' "$tmp/arc-no-python.err" >/dev/null

# The detached read-only branch never chooses a session id or a receipt without python3,
# unlike the writable case above, which only loses the receipt.
rm -f -- "$DISPATCH_TEST_CLAUDE_ARGS"
no_python_read_only_out="$(PATH="$tmp/no-python:$PATH" bash "$skill_dir/dispatch.sh" --runner claude --read-only --name Audit --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-no-python-read-only.log" 2>"$tmp/arc-no-python-read-only.err")"
for _ in $(seq 1 100); do [ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] && break; sleep 0.05; done
[ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] || { printf 'read-only Claude launch without python3 never started\n' >&2; exit 1; }
if grep -F -- '--session-id' "$DISPATCH_TEST_CLAUDE_ARGS" >/dev/null; then
  printf 'read-only Claude launch without python3 still chose a session id\n' >&2; exit 1
fi
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$no_python_read_only_out" >/dev/null; then
  printf 'read-only Claude launch without python3 printed an Arc receipt\n' >&2; exit 1
fi
grep -F -- 'no python3 on PATH: this launch has no Arc receipt' "$tmp/arc-no-python-read-only.err" >/dev/null
unset DISPATCH_TEST_CLAUDE_BG_ID
export DISPATCH_TEST_CLAUDE_BG_STATUS=7
if bash "$skill_dir/dispatch.sh" --runner claude --arc-dispatch --prompt "$tmp/prompt.md" --cwd "$tmp/work" > "$tmp/arc-failed.out"; then
  printf 'failed Claude launch succeeded\n' >&2; exit 1
fi
if grep -F -- 'ARC_DISPATCH_SESSION=' "$tmp/arc-failed.out" >/dev/null; then
  printf 'failed Claude launch printed an Arc receipt\n' >&2; exit 1
fi
unset DISPATCH_TEST_CLAUDE_BG_STATUS

rm -f -- "$DISPATCH_TEST_CLAUDE_ARGS"
arc_read_only="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --arc-dispatch --name Audit --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-read-only.log")"
arc_receipt="$(grep '^ARC_DISPATCH_SESSION=' <<< "$arc_read_only")"
[ "$(grep -c '^ARC_DISPATCH_SESSION=' <<< "$arc_read_only")" -eq 1 ]
arc_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].split("=",1)[1])["session_id"])' "$arc_receipt")"
[[ "$arc_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
python3 -c 'import json,sys; assert set(json.loads(sys.argv[1].split("=",1)[1])) == {"agent","session_id","name"}' "$arc_receipt"
for _ in $(seq 1 100); do [ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] && break; sleep 0.05; done
grep -Fx -- "$arc_id" "$DISPATCH_TEST_CLAUDE_ARGS" >/dev/null
for _ in $(seq 1 100); do [ -s "$tmp/arc-read-only.log" ] && break; sleep 0.05; done
python3 -c 'import json,sys; assert json.load(open(sys.argv[1],encoding="utf-8"))["session_id"]==sys.argv[2]' "$tmp/arc-read-only.log" "$arc_id"

# A worker still running when the status-file wait times out takes the kill -0 branch on
# the worker's own pid (from started_file), every real detached read-only launch's normal
# path since a real claude run outlives this script's short poll.
rm -f -- "$DISPATCH_TEST_CLAUDE_ARGS"
export DISPATCH_TEST_CLAUDE_SLEEP=0.7
arc_slow_read_only="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --arc-dispatch --name SlowAudit --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-slow-read-only.log")"
unset DISPATCH_TEST_CLAUDE_SLEEP
arc_slow_receipt="$(grep '^ARC_DISPATCH_SESSION=' <<< "$arc_slow_read_only")"
[ "$(grep -c '^ARC_DISPATCH_SESSION=' <<< "$arc_slow_read_only")" -eq 1 ]
arc_slow_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].split("=",1)[1])["session_id"])' "$arc_slow_receipt")"
[[ "$arc_slow_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
for _ in $(seq 1 100); do [ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] && break; sleep 0.05; done
grep -Fx -- "$arc_slow_id" "$DISPATCH_TEST_CLAUDE_ARGS" >/dev/null

export DISPATCH_TEST_CLAUDE_PRINT_STATUS=7
arc_failed_read_only="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --arc-dispatch --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-failed-read-only.log")"
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$arc_failed_read_only" >/dev/null; then
  printf 'failed read-only Claude launch printed an Arc receipt\n' >&2; exit 1
fi
unset DISPATCH_TEST_CLAUDE_PRINT_STATUS
export DISPATCH_TEST_CLAUDE_SESSION_ID=6d9e9b93-c4ae-4dac-a8c8-c01dc4f3041b
arc_mismatched_read_only="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --arc-dispatch --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-mismatched-read-only.log")"
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$arc_mismatched_read_only" >/dev/null; then
  printf 'mismatched read-only Claude launch printed an Arc receipt\n' >&2; exit 1
fi
unset DISPATCH_TEST_CLAUDE_SESSION_ID
arc_codex="$(bash "$skill_dir/dispatch.sh" --runner codex --arc-dispatch --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-codex.log")"
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$arc_codex" >/dev/null; then
  printf 'Codex launch printed an Arc receipt\n' >&2; exit 1
fi

owned_codex="$(bash "$skill_dir/dispatch.sh" --runner codex --owned --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/owned-codex.log")"
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$owned_codex" >/dev/null; then
  printf 'Codex launch with --owned printed an Arc receipt\n' >&2; exit 1
fi

# --attach opens a real terminal, so this stubs this platform's launcher (the same
# stub_terminal_launcher function the claude --attach test below uses) ahead of the real
# one on PATH, so the run never opens a real window on the machine running it.
stub_terminal_launcher
owned_attach_codex="$(PATH="$tmp/term-bin:$PATH" bash "$skill_dir/dispatch.sh" --runner codex --attach --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
if grep -F -- 'ARC_DISPATCH_SESSION=' <<< "$owned_attach_codex" >/dev/null; then
  printf 'Codex launch with --attach printed an Arc receipt\n' >&2; exit 1
fi

export DISPATCH_TEST_CLAUDE_BG_ID=a1b2c3d4
owned_writable="$(bash "$skill_dir/dispatch.sh" --runner claude --owned --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
grep -Fx -- 'ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review","owned":true}' <<< "$owned_writable" >/dev/null
unset DISPATCH_TEST_CLAUDE_BG_ID

rm -f -- "$DISPATCH_TEST_CLAUDE_ARGS"
owned_read_only="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --owned --name Audit --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/arc-owned-read-only.log")"
# The worker writes DISPATCH_TEST_CLAUDE_ARGS asynchronously: without this bounded wait,
# a late write here could still land after a later launch's own check of the same file.
for _ in $(seq 1 100); do [ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] && break; sleep 0.05; done
owned_receipt="$(grep '^ARC_DISPATCH_SESSION=' <<< "$owned_read_only")"
[ "$(grep -c '^ARC_DISPATCH_SESSION=' <<< "$owned_read_only")" -eq 1 ]
python3 -c 'import json,sys; d=json.loads(sys.argv[1].split("=",1)[1]); assert d["owned"] is True; assert set(d) == {"agent","session_id","name","owned"}' "$owned_receipt"

# --attach opens a real terminal, so this stubs this platform's launcher ahead of the
# real one on PATH, the same way the Claude and Codex binaries are stubbed above, so the
# run never opens a real window on the machine running it.
stub_terminal_launcher
export DISPATCH_TEST_CLAUDE_BG_ID=a1b2c3d4
owned_attach_writable="$(PATH="$tmp/term-bin:$PATH" bash "$skill_dir/dispatch.sh" --runner claude --attach --name Review --prompt "$tmp/prompt.md" --cwd "$tmp/work")"
grep -Fx -- 'ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review","owned":true}' <<< "$owned_attach_writable" >/dev/null
unset DISPATCH_TEST_CLAUDE_BG_ID

override_record="$(bash "$skill_dir/dispatch.sh" --runner claude --prompt "$tmp/prompt.md" --cwd "$tmp/work" --permission-mode acceptEdits)"
override_mode="$(awk '$0 == "--permission-mode" { getline; print; exit }' "$DISPATCH_TEST_CLAUDE_ARGS")"
[ "$override_mode" = acceptEdits ]
grep -F -- 'id: claude-test' <<< "$override_record" >/dev/null

export DISPATCH_TEST_QUEUE_LOG="$tmp/queue.log"
export DISPATCH_TEST_EXPECT_RESULT="$tmp/run.last.md"
record="$(bash "$skill_dir/dispatch.sh" --runner codex --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/run.log" --notify-thread origin-123)"
wait_for_queue "$DISPATCH_TEST_QUEUE_LOG"
[ "$(wc -l < "$DISPATCH_TEST_QUEUE_LOG")" -eq 1 ]
grep -F -- '--thread origin-123 --message Fresh-session handoff dispatch-' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- 'completed (exit 0). Result:' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- "notify-thread: origin-123" <<< "$record" >/dev/null
grep -F -- "notify-log: $tmp/run.notify.log" <<< "$record" >/dev/null

export DISPATCH_TEST_QUEUE_LOG="$tmp/failure-queue.log"
export DISPATCH_TEST_EXPECT_RESULT="$tmp/failure.last.md"
export DISPATCH_TEST_EXEC_STATUS=7
failure_record="$(bash "$skill_dir/dispatch.sh" --runner codex --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/failure.log" --notify-thread origin-456)"
wait_for_queue "$DISPATCH_TEST_QUEUE_LOG"
[ "$(wc -l < "$DISPATCH_TEST_QUEUE_LOG")" -eq 1 ]
grep -F -- 'failed (exit 7). Result:' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- "notify-thread: origin-456" <<< "$failure_record" >/dev/null

unset DISPATCH_TEST_EXEC_STATUS
export DISPATCH_NOTIFY_INITIAL_SECONDS=1
export DISPATCH_NOTIFY_MAX_SECONDS=1
export DISPATCH_NOTIFY_BLOCKED_GRACE_SECONDS=1
export DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS=2
export DISPATCH_NOTIFY_TIMEOUT_SECONDS=5
export DISPATCH_TEST_EXPECT_RESULT=""
export DISPATCH_TEST_CLAUDE_STATE_FILE="$tmp/claude-state"

export DISPATCH_TEST_CLAUDE_SEQUENCE=complete
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-complete-queue.log"
claude_record="$(bash "$skill_dir/dispatch.sh" --runner claude --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/claude-complete.log" --notify-thread origin-789)"
wait_for_queue "$DISPATCH_TEST_QUEUE_LOG"
[ "$(wc -l < "$DISPATCH_TEST_QUEUE_LOG")" -eq 1 ]
grep -F -- '--thread origin-789 --message Fresh-session handoff dispatch-' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- 'completed. Result: claude logs claude-test' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- 'notify-thread: origin-789' <<< "$claude_record" >/dev/null
grep -F -- "$tmp/claude-complete.notify.log" <<< "$claude_record" >/dev/null
grep -F -- 'monitor-pid:' <<< "$claude_record" >/dev/null

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=blocked
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-blocked-queue.log"
blocked_record="$(bash "$skill_dir/dispatch.sh" --runner claude --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/claude-blocked.log" --notify-thread origin-790)"
wait_for_queue "$DISPATCH_TEST_QUEUE_LOG"
[ "$(wc -l < "$DISPATCH_TEST_QUEUE_LOG")" -eq 1 ]
grep -F -- 'requires attention (Claude state blocked)' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- 'notify-thread: origin-790' <<< "$blocked_record" >/dev/null

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=multi_agent
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-multi-agent-queue.log"
bash "$skill_dir/watch-claude.sh" "$CLAUDE_BIN" "$CODEX_BIN" claude-test multi-agent origin-792 'claude logs claude-test' 'claude attach claude-test'
assert_single_notice "$DISPATCH_TEST_QUEUE_LOG" 'completed. Result: claude logs claude-test'

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=registry_error
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-registry-error-queue.log"
if bash "$skill_dir/watch-claude.sh" "$CLAUDE_BIN" "$CODEX_BIN" claude-test registry-error origin-793 'claude logs claude-test' 'claude attach claude-test'; then
  printf 'registry-error watcher unexpectedly succeeded\n' >&2
  exit 1
fi
assert_single_notice "$DISPATCH_TEST_QUEUE_LOG" 'could not be monitored after three Claude registry errors'

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=unknown
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-unknown-queue.log"
if bash "$skill_dir/watch-claude.sh" "$CLAUDE_BIN" "$CODEX_BIN" claude-test unknown origin-794 'claude logs claude-test' 'claude attach claude-test'; then
  printf 'unknown-state watcher unexpectedly succeeded\n' >&2
  exit 1
fi
assert_single_notice "$DISPATCH_TEST_QUEUE_LOG" 'reported unknown Claude state surprised'

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=missing
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-missing-queue.log"
if bash "$skill_dir/watch-claude.sh" "$CLAUDE_BIN" "$CODEX_BIN" claude-test missing origin-795 'claude logs claude-test' 'claude attach claude-test'; then
  printf 'missing-agent watcher unexpectedly succeeded\n' >&2
  exit 1
fi
assert_single_notice "$DISPATCH_TEST_QUEUE_LOG" 'never appeared in the Claude registry'

rm -f -- "$DISPATCH_TEST_CLAUDE_STATE_FILE"
export DISPATCH_TEST_CLAUDE_SEQUENCE=working
export DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS=5
export DISPATCH_NOTIFY_TIMEOUT_SECONDS=2
export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-timeout-queue.log"
if bash "$skill_dir/watch-claude.sh" "$CLAUDE_BIN" "$CODEX_BIN" claude-test timeout origin-796 'claude logs claude-test' 'claude attach claude-test'; then
  printf 'timeout watcher unexpectedly succeeded\n' >&2
  exit 1
fi
assert_single_notice "$DISPATCH_TEST_QUEUE_LOG" 'monitor timed out after 2 seconds'

export DISPATCH_TEST_QUEUE_LOG="$tmp/claude-read-only-queue.log"
export DISPATCH_TEST_EXPECT_RESULT="$tmp/claude-read-only.log"
read_only_record="$(bash "$skill_dir/dispatch.sh" --runner claude --read-only --read-tool mcp__tickets__get_ticket --read-tool mcp__tickets__list_comments --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$DISPATCH_TEST_EXPECT_RESULT" --notify-thread origin-791)"
wait_for_queue "$DISPATCH_TEST_QUEUE_LOG"
[ "$(wc -l < "$DISPATCH_TEST_QUEUE_LOG")" -eq 1 ]
grep -F -- 'completed (exit 0)' "$DISPATCH_TEST_QUEUE_LOG" >/dev/null
grep -F -- 'notify-thread: origin-791' <<< "$read_only_record" >/dev/null
[ "$(grep -c '^ARC_DISPATCH_SESSION=' <<< "$read_only_record")" -eq 1 ] || {
  printf 'default read-only Claude launch printed no single Arc receipt\n' >&2; exit 1
}
default_read_only_session_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].split("=",1)[1])["session_id"])' "$(grep '^ARC_DISPATCH_SESSION=' <<< "$read_only_record")")"
[[ "$default_read_only_session_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
# The tokens that follow one flag, up to the next flag, one per line.
flag_values() {
  awk -v flag="$1" '$0 == flag { on = 1; next } on && /^--/ { exit } on { print }' "$DISPATCH_TEST_CLAUDE_ARGS"
}
expect_value() {
  if ! flag_values "$1" | grep -Fx -- "$2" >/dev/null; then
    printf 'Claude read-only launch is missing %s under %s\n' "$2" "$1" >&2
    exit 1
  fi
}
reject_value() {
  if flag_values "$1" | grep -Fx -- "$2" >/dev/null; then
    printf 'Claude read-only launch carries %s under %s\n' "$2" "$1" >&2
    exit 1
  fi
}
expect_value --permission-mode dontAsk
expect_value --permission-prompts none
for tool in Read Grep Glob mcp__tickets__get_ticket mcp__tickets__list_comments; do expect_value --allowedTools "$tool"; done
for tool in Bash PowerShell Agent Edit Write; do expect_value --disallowedTools "$tool"; reject_value --allowedTools "$tool"; done
if grep -Fx -- '--setting-sources' "$DISPATCH_TEST_CLAUDE_ARGS" >/dev/null; then
  printf 'Claude read-only launch with an MCP read drops the settings that configure its server\n' >&2
  exit 1
fi

# With no MCP read, the launch loads no settings, so no allow rule can approve a write,
# and a reviewer keeps read-only shell commands such as git log.
rm -f "$DISPATCH_TEST_CLAUDE_ARGS"
bash "$skill_dir/dispatch.sh" --runner claude --read-only --prompt "$tmp/prompt.md" --cwd "$tmp/work" --log "$tmp/claude-read-only-plain.log" >/dev/null
for _ in $(seq 1 100); do [ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] && break; sleep 0.1; done
[ -s "$DISPATCH_TEST_CLAUDE_ARGS" ] || { printf 'Claude read-only launch never started\n' >&2; exit 1; }
if [ "$(awk '$0 == "--setting-sources" { getline; print "[" $0 "]"; exit }' "$DISPATCH_TEST_CLAUDE_ARGS")" != "[]" ]; then
  printf 'Claude read-only launch loads settings whose allow rules can approve a write\n' >&2
  exit 1
fi
reject_value --disallowedTools Bash
expect_value --disallowedTools PowerShell
expect_value --disallowedTools Edit

if bash "$skill_dir/dispatch.sh" --runner claude --read-only --permission-mode auto --prompt "$tmp/prompt.md" --cwd "$tmp/work" >"$tmp/read-only-override.out" 2>&1; then
  printf 'Claude read-only permission override unexpectedly succeeded\n' >&2
  exit 1
fi
grep -F -- 'cannot override Claude --read-only' "$tmp/read-only-override.out" >/dev/null

if bash "$skill_dir/dispatch.sh" --runner claude --read-only --read-tool Bash --prompt "$tmp/prompt.md" --cwd "$tmp/work" >"$tmp/read-tool-invalid.out" 2>&1; then
  printf 'non-MCP read tool unexpectedly succeeded\n' >&2
  exit 1
fi
grep -F -- '--read-tool must name one exact MCP tool' "$tmp/read-tool-invalid.out" >/dev/null

for runner_args in "--runner codex --read-only" "--runner claude"; do
  # shellcheck disable=SC2086
  if bash "$skill_dir/dispatch.sh" $runner_args --read-tool mcp__tickets__get_ticket --prompt "$tmp/prompt.md" --cwd "$tmp/work" >"$tmp/read-tool-misplaced.out" 2>&1; then
    printf -- '--read-tool unexpectedly accepted with %s\n' "$runner_args" >&2
    exit 1
  fi
  grep -F -- '--read-tool requires --runner claude --read-only' "$tmp/read-tool-misplaced.out" >/dev/null
done

# Two named launches from one prompt file keep separate results.
for review_name in review-opus5 review-opus55; do
  bash "$skill_dir/dispatch.sh" --runner claude --read-only --name "$review_name" --prompt "$tmp/prompt.md" --cwd "$tmp/work" >"$tmp/$review_name.out"
  grep -Fx -- "result: $(cd "$tmp" && pwd)/prompt.$review_name.log" "$tmp/$review_name.out" >/dev/null || {
    printf 'named launch %s does not keep its own result log\n' "$review_name" >&2
    exit 1
  }
done

printf 'dispatch permission and callback tests passed\n'
