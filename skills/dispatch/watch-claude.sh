#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 7 ]; then
  echo "usage: watch-claude.sh CLAUDE_BIN CODEX_BIN ID NAME THREAD RESULT ATTACH" >&2
  exit 2
fi

claude_bin="$1"
codex_bin="$2"
id="$3"
name="$4"
thread="$5"
result="$6"
attach="$7"

initial_seconds="${DISPATCH_NOTIFY_INITIAL_SECONDS:-2}"
max_seconds="${DISPATCH_NOTIFY_MAX_SECONDS:-30}"
blocked_grace_seconds="${DISPATCH_NOTIFY_BLOCKED_GRACE_SECONDS:-45}"
startup_grace_seconds="${DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS:-60}"
timeout_seconds="${DISPATCH_NOTIFY_TIMEOUT_SECONDS:-86400}"

for value in "$initial_seconds" "$max_seconds" "$blocked_grace_seconds" "$startup_grace_seconds" "$timeout_seconds"; do
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "dispatch notification intervals must be whole seconds" >&2
    exit 2
  fi
done

send_notice() {
  "$codex_bin" queue --thread "$thread" --message "$1"
}

find_agent() {
  awk -v wanted="$id" '
    function emit_if_target(value, compact) {
      compact = value
      gsub(/[[:space:]]/, "", compact)
      if (index(compact, "\"id\":\"" wanted "\"") > 0) {
        print value
        found = 1
      }
    }
    {
      input = input $0 "\n"
    }
    END {
      depth = 0
      quoted = 0
      escaped = 0
      object = ""
      for (i = 1; i <= length(input) && !found; i++) {
        character = substr(input, i, 1)
        if (depth > 0) object = object character
        if (quoted) {
          if (escaped) escaped = 0
          else if (character == "\\") escaped = 1
          else if (character == "\"") quoted = 0
          continue
        }
        if (character == "\"") {
          quoted = 1
          continue
        }
        if (character == "{") {
          if (depth == 0) object = character
          depth++
        } else if (character == "}") {
          depth--
          if (depth == 0) emit_if_target(object)
        }
      }
    }
  '
}

started_at=$SECONDS
delay_seconds="$initial_seconds"
seen=0
blocked_at=-1
consecutive_errors=0

while [ $((SECONDS - started_at)) -lt "$timeout_seconds" ]; do
  set +e
  snapshot="$("$claude_bin" agents --json)"
  snapshot_status=$?
  set -e

  if [ "$snapshot_status" -ne 0 ]; then
    consecutive_errors=$((consecutive_errors + 1))
    if [ "$consecutive_errors" -ge 3 ]; then
      send_notice "Fresh-session handoff $name could not be monitored after three Claude registry errors. Attach: $attach. Result: $result"
      exit 1
    fi
  else
    consecutive_errors=0
    block="$(printf '%s\n' "$snapshot" | find_agent)"

    if [ -z "$block" ]; then
      if [ "$seen" -eq 1 ]; then
        send_notice "Fresh-session handoff $name completed. Result: $result"
        exit 0
      fi
      if [ $((SECONDS - started_at)) -ge "$startup_grace_seconds" ]; then
        send_notice "Fresh-session handoff $name never appeared in the Claude registry. Result: $result"
        exit 1
      fi
    else
      seen=1
      state="$(printf '%s\n' "$block" | sed -n 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
      if [ -z "$state" ]; then
        state="$(printf '%s\n' "$block" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
      fi
      case "$state" in
        working|busy|starting)
          blocked_at=-1
          ;;
        blocked|idle)
          if [ "$blocked_at" -lt 0 ]; then blocked_at=$SECONDS; fi
          if [ $((SECONDS - blocked_at)) -ge "$blocked_grace_seconds" ]; then
            send_notice "Fresh-session handoff $name requires attention (Claude state $state). Attach: $attach. Result: $result"
            exit 0
          fi
          ;;
        failed|stopped|killed|error)
          send_notice "Fresh-session handoff $name ended with Claude state $state. Attach: $attach. Result: $result"
          exit 1
          ;;
        *)
          send_notice "Fresh-session handoff $name reported unknown Claude state ${state:-missing}. Attach: $attach. Result: $result"
          exit 1
          ;;
      esac
    fi
  fi

  sleep "$delay_seconds"
  if [ "$delay_seconds" -lt "$max_seconds" ]; then
    delay_seconds=$((delay_seconds * 2))
    if [ "$delay_seconds" -gt "$max_seconds" ]; then delay_seconds="$max_seconds"; fi
  fi
done

send_notice "Fresh-session handoff $name monitor timed out after $timeout_seconds seconds. Attach: $attach. Result: $result"
exit 1
