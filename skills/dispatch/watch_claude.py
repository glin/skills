"""Poll the local Claude Code registry for one background session and queue a callback.

Reads `claude agents --json` on a backoff schedule. Queue acceptance and parent
consumption are separate receipt states. Makes no model request itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from receipt import Receipt, DEADLINE, publish, cli_command

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
    """Legacy direct queue call. A nonzero exit leaves acceptance unknown."""
    args = cli_command([codex_bin, "queue", "--thread", thread, "--message", message])
    if notify_log:
        with open(notify_log, "w", encoding="utf-8") as fh:
            result = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT, check=False, timeout=DEADLINE)
        if result.returncode != 0:
            with open(notify_log, "a", encoding="utf-8") as fh:
                fh.write(f"\ncodex queue exited {result.returncode}: acceptance unknown\n")
    else:
        result = subprocess.run(args, check=False, timeout=DEADLINE)
        if result.returncode != 0:
            print(f"codex queue exited {result.returncode}: acceptance unknown", file=sys.stderr)
    return result.returncode


def run_watcher(claude_bin, codex_bin, agent_id, name, thread, result, attach, *, notify_log=None, env=None, clock=time.monotonic, sleep=time.sleep, receipt=None, monitor_owned=False):
    """Poll until the agent leaves the registry, blocks, fails, or the monitor times out.

    Registry disappearance does not establish the worker's terminal status.
    """
    if receipt is not None and not monitor_owned:
        with receipt.monitor():
            return run_watcher(claude_bin, codex_bin, agent_id, name, thread, result, attach,
                               notify_log=notify_log, env=env, clock=clock, sleep=sleep,
                               receipt=receipt, monitor_owned=True)
    env = os.environ if env is None else env

    initial_seconds = _env_seconds(env, "DISPATCH_NOTIFY_INITIAL_SECONDS", DEFAULT_INITIAL_SECONDS)
    max_seconds = _env_seconds(env, "DISPATCH_NOTIFY_MAX_SECONDS", DEFAULT_MAX_SECONDS)
    blocked_grace_seconds = _env_seconds(env, "DISPATCH_NOTIFY_BLOCKED_GRACE_SECONDS", DEFAULT_BLOCKED_GRACE_SECONDS)
    startup_grace_seconds = _env_seconds(env, "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS", DEFAULT_STARTUP_GRACE_SECONDS)
    timeout_seconds = _env_seconds(env, "DISPATCH_NOTIFY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    bash = (receipt.read().get('cli_bash') or '') if receipt is not None else None

    def notice(message):
        return send_notice(codex_bin, thread, message, notify_log)

    def notice_and_return(message, expected_status):
        if receipt is not None:
            # Diagnostics never replace the launch-private report or establish success.
            try:
                logs = subprocess.run(cli_command([claude_bin, 'logs', agent_id], bash), capture_output=True,
                                      timeout=DEADLINE, check=False)
                if logs.returncode == 0:
                    publish(receipt.read()['log'], logs.stdout if isinstance(logs.stdout, bytes) else logs.stdout.encode())
            except (OSError, subprocess.TimeoutExpired):
                pass
            kind = 'unverified' if ' completed.' in message else ('attention' if 'requires attention' in message else 'monitoring-error')
            if 'ended with Claude state' in message:
                kind = 'failed'
            event = receipt.observe(kind, None, detail=message)
            return expected_status if receipt.deliver(event['id']) == 0 else 1
        return expected_status if notice(message) == 0 else 1

    started_at = clock()
    delay_seconds = initial_seconds
    seen = receipt.read().get('registry_seen', False) if receipt is not None else False
    blocked_at = None
    consecutive_errors = 0

    while clock() - started_at < timeout_seconds:
        try:
            proc = subprocess.run(cli_command([claude_bin, "agents", "--json"], bash), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=DEADLINE)
            if proc.returncode == 0:
                snapshot = json.loads(proc.stdout)
                if not isinstance(snapshot, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in snapshot):
                    raise ValueError('invalid Claude registry response')
                block = find_agent(proc.stdout, agent_id)
                state = (block.get('state') or block.get('status')) if block is not None else None
                if state is not None and not isinstance(state, str):
                    raise ValueError('invalid Claude registry state')
        except (OSError, subprocess.TimeoutExpired, ValueError):
            proc = subprocess.CompletedProcess([], 1)
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
                if not seen and receipt is not None:
                    receipt.update(registry_seen=True)
                seen = True
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
    if len(argv) not in (7, 8):
        print("usage: watch_claude.py CLAUDE_BIN CODEX_BIN ID NAME THREAD RESULT ATTACH [RECEIPT]", file=sys.stderr)
        return 2
    claude_bin, codex_bin, agent_id, name, thread, result, attach = argv[:7]
    if len(argv) == 8:
        receipt = Receipt(argv[7])
    else:
        # Historical watcher arguments remain usable without attributing old reports.
        receipt = Receipt.create(thread, 'claude-writable', name, str(Path(tempfile.gettempdir()) / 'watcher.log'), result, codex_bin)
        receipt.update(session=agent_id, claude_bin=claude_bin)
        print(f'receipt: {receipt.root}', file=sys.stderr)
    try:
        return run_watcher(claude_bin, codex_bin, agent_id, name, thread, result, attach, receipt=receipt)
    except (OSError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
