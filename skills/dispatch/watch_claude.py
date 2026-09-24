"""Poll the local Claude Code registry for one background session and queue a callback.

Ported from watch-claude.sh. Reads `claude agents --json` on a backoff schedule and
sends exactly one completion, attention, failure, or timeout notice through
`codex queue --thread <thread> --message <text>`. Makes no model request itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

DEFAULT_INITIAL_SECONDS = 2
DEFAULT_MAX_SECONDS = 30
DEFAULT_BLOCKED_GRACE_SECONDS = 45
DEFAULT_STARTUP_GRACE_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 86400

WORKING_STATES = {"working", "busy", "starting"}
BLOCKED_STATES = {"blocked", "idle"}
TERMINAL_FAILURE_STATES = {"failed", "stopped", "killed", "error"}


def _env_seconds(env, key, default):
    value = env.get(key)
    # Bash's ${VAR:-default} takes the default for an unset or an empty value alike.
    if not value:
        return default
    if not value.isdigit():
        print("dispatch notification intervals must be whole seconds", file=sys.stderr)
        raise SystemExit(2)
    return int(value)


def find_agent(snapshot_text, wanted_id):
    """Return the registry entry whose id matches wanted_id, or None."""
    try:
        data = json.loads(snapshot_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get("id") == wanted_id:
            return item
    return None


def send_notice(codex_bin, thread, message, notify_log=None):
    """Queue exactly one callback through `codex queue`. Returns its exit code. A rejected
    or failed queue request delivers no notice, so its failure is written to the notify log
    (or, with no log path, to stderr) and returned, letting the caller's own exit code show
    that nothing went out rather than reporting the quiet success of an ignored return code."""
    args = [codex_bin, "queue", "--thread", thread, "--message", message]
    if notify_log:
        # dispatch.sh truncates the notify log with `>`. Appending here would mix an
        # earlier run's callback output into a log path a caller reuses across dispatches.
        with open(notify_log, "w", encoding="utf-8") as fh:
            result = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT, check=False)
        if result.returncode != 0:
            with open(notify_log, "a", encoding="utf-8") as fh:
                fh.write(f"\ncodex queue exited {result.returncode}: no notice was delivered\n")
    else:
        result = subprocess.run(args, check=False)
        if result.returncode != 0:
            print(f"codex queue exited {result.returncode}: no notice was delivered", file=sys.stderr)
    return result.returncode


def run_watcher(claude_bin, codex_bin, agent_id, name, thread, result, attach, *, notify_log=None, env=None, clock=time.monotonic, sleep=time.sleep):
    """Poll until the agent leaves the registry, blocks, fails, or the monitor times out.

    Returns 0 for an expected exit path (completed, or blocked past the grace
    period), 1 for an error path (registry errors, unknown state, missing
    agent, or timeout). Sends exactly one notice through send_notice.
    """
    env = os.environ if env is None else env

    initial_seconds = _env_seconds(env, "DISPATCH_NOTIFY_INITIAL_SECONDS", DEFAULT_INITIAL_SECONDS)
    max_seconds = _env_seconds(env, "DISPATCH_NOTIFY_MAX_SECONDS", DEFAULT_MAX_SECONDS)
    blocked_grace_seconds = _env_seconds(env, "DISPATCH_NOTIFY_BLOCKED_GRACE_SECONDS", DEFAULT_BLOCKED_GRACE_SECONDS)
    startup_grace_seconds = _env_seconds(env, "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS", DEFAULT_STARTUP_GRACE_SECONDS)
    timeout_seconds = _env_seconds(env, "DISPATCH_NOTIFY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)

    def notice(message):
        return send_notice(codex_bin, thread, message, notify_log)

    def notice_and_return(message, expected_status):
        # A rejected or failed codex queue request delivers no notice, so the watcher's own
        # exit code has to say so too: expected_status describes what happened to the agent,
        # but that outcome was never reported anywhere if the callback itself failed.
        return expected_status if notice(message) == 0 else 1

    started_at = clock()
    delay_seconds = initial_seconds
    seen = False
    blocked_at = None
    consecutive_errors = 0

    while clock() - started_at < timeout_seconds:
        proc = subprocess.run([claude_bin, "agents", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            consecutive_errors += 1
            if consecutive_errors >= 3:
                return notice_and_return(f"Fresh-session handoff {name} could not be monitored after three Claude registry errors. Attach: {attach}. Result: {result}", 1)
        else:
            consecutive_errors = 0
            block = find_agent(proc.stdout, agent_id)

            if block is None:
                if seen:
                    return notice_and_return(f"Fresh-session handoff {name} completed. Result: {result}", 0)
                if clock() - started_at >= startup_grace_seconds:
                    return notice_and_return(f"Fresh-session handoff {name} never appeared in the Claude registry. Result: {result}", 1)
            else:
                seen = True
                state = block.get("state") or block.get("status")
                if state in WORKING_STATES:
                    blocked_at = None
                elif state in BLOCKED_STATES:
                    if blocked_at is None:
                        blocked_at = clock()
                    if clock() - blocked_at >= blocked_grace_seconds:
                        return notice_and_return(f"Fresh-session handoff {name} requires attention (Claude state {state}). Attach: {attach}. Result: {result}", 0)
                elif state in TERMINAL_FAILURE_STATES:
                    return notice_and_return(f"Fresh-session handoff {name} ended with Claude state {state}. Attach: {attach}. Result: {result}", 1)
                else:
                    return notice_and_return(f"Fresh-session handoff {name} reported unknown Claude state {state or 'missing'}. Attach: {attach}. Result: {result}", 1)

        sleep(delay_seconds)
        if delay_seconds < max_seconds:
            delay_seconds = min(delay_seconds * 2, max_seconds)

    return notice_and_return(f"Fresh-session handoff {name} monitor timed out after {timeout_seconds} seconds. Attach: {attach}. Result: {result}", 1)


def main(argv):
    if len(argv) != 7:
        print("usage: watch_claude.py CLAUDE_BIN CODEX_BIN ID NAME THREAD RESULT ATTACH", file=sys.stderr)
        return 2
    claude_bin, codex_bin, agent_id, name, thread, result, attach = argv
    return run_watcher(claude_bin, codex_bin, agent_id, name, thread, result, attach)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
