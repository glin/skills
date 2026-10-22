#!/usr/bin/env python
"""Start a separate agent session from a prompt file through Arc.

Standard library only, Python 3.10 or later.

  python dispatch.py --runner claude|codex --prompt FILE --model M --effort E
      [--name N] [--cwd DIR] [--add-dir DIR]... [--permission-mode MODE]
      [--read-tool MCP_TOOL]... [--read-only] [--full-access] [--attach] [--owned]
      [--log FILE] [--wait] --arc-dispatch
  python dispatch.py --wait-launch LAUNCH_ID [--read-only] [--cwd DIR]

A reader-owned writable request starts in a ready Arc window through `arc start`.
Every other request runs through `arc run`, which starts the provider without a
window and owns its permissions, its supervisor and its receipt. This script maps
the flags, checks the prompt file, and prints Arc's launch JSON, a short record,
and Arc's ARC_DISPATCH_SESSION= marker as the last line.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

# arc start takes a full model id, so the wrapper resolves the OpenAI shorthands for
# that route. arc run resolves the same table itself, and a resolved id passes through
# it unchanged. Amazon Bedrock uses an "openai." prefix for these models.
CODEX_MODEL_SHORTHANDS = {
    "sol": "gpt-6.1-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-6-luna",
    "astra": "gpt-6-astra",
}
BEDROCK_PROVIDER = "amazon-bedrock"
BEDROCK_OPENAI_PREFIX = "openai."

# A dispatched Claude session writes its prompt cache at the 5-minute lifetime. On a
# subscription, Claude Code defaults the main conversation to 1 hour, and a 1-hour write
# costs 2 times the input rate against 1.25 times for 5 minutes. The longer lifetime pays
# off only when a person pauses 5 to 60 minutes between requests. A dispatched session
# runs without those pauses, so the shorter lifetime costs less. SKILL.md holds the
# measurement. A value the caller already set in the environment wins, and Arc passes
# the environment to the provider.
CACHE_TTL_ENV = "CLAUDE_CODE_PROMPT_CACHE_TTL"
DISPATCH_CACHE_TTL = "5m"

# arc run blocks for up to 110 seconds while it waits for the session identity or a
# waited observation. The extra margin covers its start-up.
ARC_RUN_TIMEOUT = 180
ARC_WAIT_TIMEOUT = 180
# Preserve the existing startup and reply allowance after Arc's observation deadline.
ARC_REQUEST_OVERHEAD = ARC_WAIT_TIMEOUT - 110
# Arc's supervisor observes a worker for at most 24 hours, then publishes an outcome. A
# waited launch still without one an hour later has a supervisor that stopped without
# publishing, so the wait ends there. DISPATCH_WAIT_LIMIT_SECONDS replaces the bound.
WAIT_LIMIT_SECONDS = 25 * 3600
# A worker's exit code reaches Arc's status a moment after its outcome. Forty reads a
# quarter second apart cover the save, and a worker that still runs ends the reads at once.
EXIT_READS = 40
EXIT_READ_INTERVAL = 0.25

WORKTREE_REFUSAL = (
    "--worktree is not a dispatch flag. The calling workflow creates the worktree with "
    "git worktree add -b <branch> <path> <base>, then launches with --cwd <path>"
)
NOTIFY_REFUSAL = (
    "--notify-thread is not a dispatch flag. Arc delivers no callback. "
    "Launch in the foreground with --arc-dispatch, then run --wait-launch LAUNCH_ID in the background, "
    "or read arc dispatch status and arc dispatch result later. "
    "A Claude parent can also use SendMessage with notify_when_idle for a writable Claude worker"
)

# arc run replies with one of these states when its worker opened. Any other state, such
# as accepted-with-errors after a failed supervisor spawn, means no worker exists.
OPENED_STATES = ("starting", "observed", "deadline")


def use_dispatch_cache_ttl():
    os.environ.setdefault(CACHE_TTL_ENV, DISPATCH_CACHE_TTL)


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def strip_ext(path):
    root, _ext = os.path.splitext(path)
    return root


def default_log(prompt_path, name):
    """The prompt's path with .log in place of its extension, with the launch name inserted
    before .log when --name is given. Two named launches from one prompt file then write
    separate logs and results. dispatch.sh builds the same path."""
    stem = strip_ext(prompt_path)
    safe = re.sub(r"[^A-Za-z0-9._]+", "-", name).strip("-")
    return f"{stem}.{safe}.log" if safe else f"{stem}.log"


def cli_command(args, bash=None):
    """Run a configured shell script through the Bash executable that DISPATCH_CLI_BASH
    names. Native Windows Python cannot run a Bash script itself."""
    bash = os.environ.get("DISPATCH_CLI_BASH") if bash is None else bash
    if bash and os.name == "nt":
        try:
            with open(args[0], "rb") as stream:
                header = stream.readline(256).decode("ascii", errors="replace").strip()
            if header.startswith("#!") and Path(header.split()[-1]).name in ("bash", "sh"):
                return [bash, Path(args[0]).resolve().as_posix(), *args[1:]]
        except OSError:
            pass
    return args


def resolve_bin(env_var, default_name):
    override = os.environ.get(env_var)
    candidate = override if override else default_name
    if override and (len(cli_command([candidate])) > 1 or is_script_entry(candidate)):
        return str(Path(candidate).resolve())
    return shutil.which(candidate)


def is_script_entry(path):
    return Path(path).suffix.lower() in (".cjs", ".js") and os.path.isfile(path)


def codex_provider():
    """Read model_provider from the top-level table of the Codex config.

    The scan stops at the first section header, because model_provider also appears as a
    key under [model_providers.<name>.*] and that copy names a different thing.
    """
    home = os.environ.get("CODEX_HOME")
    config = Path(home) / "config.toml" if home else Path.home() / ".codex" / "config.toml"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = re.match(r"""model_provider\s*=\s*['"]([^'"]+)['"]""", stripped)
        if match:
            return match.group(1)
    return None


def resolve_codex_model(model):
    """Expand a shorthand for the configured provider, then add Bedrock's prefix.

    Bedrock rejects a bare gpt-5.6-sol. It names that model openai.gpt-5.6-sol, and the
    prefix belongs to the provider rather than to the shorthand table, because the same
    shorthand is correct without it against the OpenAI endpoint.
    """
    provider = codex_provider()
    resolved = CODEX_MODEL_SHORTHANDS.get(model, model)
    if resolved.startswith("gpt-") and provider == BEDROCK_PROVIDER:
        return BEDROCK_OPENAI_PREFIX + resolved
    return resolved


def _print_arc_receipt(session_id, name, owned=False, agent="claude"):
    receipt = {"agent": agent, "session_id": session_id, "name": name}
    if owned:
        receipt["owned"] = True
    print("ARC_DISPATCH_SESSION=" + json.dumps(receipt, separators=(",", ":")))


def arc_command(arc_bin):
    """Invoke published Windows Arc entry points without cmd.exe argument parsing. A
    JavaScript entry, such as a built arc.cjs, runs under Node on every platform."""
    if is_script_entry(arc_bin):
        node = shutil.which("node")
        return [node, str(Path(arc_bin).resolve())] if node else None
    command = cli_command([arc_bin])
    if os.name != "nt" or len(command) > 1 or Path(arc_bin).suffix.lower() not in (".cmd", ".bat"):
        return command
    launcher = Path(arc_bin)
    if launcher.stem.lower() != "arc":
        return None
    entries = [launcher.parent.parent / "cli.cjs",
               launcher.parent.parent / "cli/arc.cjs",
               launcher.parent / "node_modules/@glin/arc/dist/arc.cjs",
               launcher.parent.parent / "@glin/arc/dist/arc.cjs"]
    node = shutil.which("node.exe")
    if node:
        for entry in entries:
            if entry.is_file():
                return [node, str(entry.resolve())]
    return None


def try_arc_start(args, cwd, prompt_path, name):
    """Return None only before launch or after an explicit refused acknowledgment."""
    if not args.owned or args.read_only:
        return None
    arc_bin = resolve_bin("ARC_BIN", "arc")
    if not arc_bin:
        return None

    def refuse(reason):
        print(f"Arc route refused before acceptance: {reason}. Starting a standalone launch instead.", file=sys.stderr)
        return None

    incompatible = [flag for flag, value in (
        ("--add-dir", args.add_dir), ("--log", args.log), ("--wait", args.wait),
        ("Claude --permission-mode " + str(args.permission_mode),
         args.runner == "claude" and args.permission_mode in ("dontAsk", "bypassPermissions")),
        ("Codex workspace-write with no approvals", args.runner == "codex" and not args.full_access),
        ("Codex --permission-mode", args.runner == "codex" and args.permission_mode),
    ) if value]
    if incompatible:
        return refuse("Arc cannot preserve " + ", ".join(incompatible))

    launcher = arc_command(arc_bin)
    if launcher is None:
        return refuse("the Arc launcher cannot preserve literal arguments")
    try:
        if not stat.S_ISREG(os.stat(prompt_path).st_mode):
            return refuse("the prompt file must be a regular file")
        with open(prompt_path, "rb") as stream:
            prompt = stream.read(48 * 1024 + 1)
        if len(prompt) > 48 * 1024:
            return refuse("the prompt file exceeds 48 KiB")
        # Match JavaScript trimStart, including its UTF-8 BOM handling.
        whitespace = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
        text = prompt.decode("utf-8-sig", errors="replace").lstrip(whitespace)
        if not text or text.startswith("/"):
            return refuse("the prompt must contain text without a prompt-box command")
    except OSError as error:
        return refuse(f"the prompt file is unavailable: {error}")

    def run_arc(arguments, timeout):
        return subprocess.run([*launcher, *arguments], cwd=cwd,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)

    try:
        discovery = run_arc(["windows", "--json"], 30)
        catalog = json.loads(discovery.stdout)
        if discovery.returncode or not isinstance(catalog, dict) or catalog.get("version") != 1:
            return refuse("window discovery is unavailable")
        windows = catalog.get("windows")
        if not isinstance(windows, list):
            return refuse("window discovery is unavailable")
        selector = os.environ.get("ARC_WINDOW")
        if selector:
            windows = [window for window in windows if isinstance(window, dict) and window.get("hostId") == selector]
        elif catalog.get("complete") is not True:
            return refuse("window discovery is incomplete")
        if not windows:
            return refuse("the selected Arc window is unavailable" if selector else "no Arc window is available")
        if len(windows) != 1 or not isinstance(windows[0], dict):
            return refuse("select one Arc window with ARC_WINDOW")
        window = windows[0]
        host = window.get("hostId")
        if not isinstance(host, str) or not re.fullmatch(r"[a-f0-9]{24}", host):
            return refuse("the window identifier is unavailable")
        folders = window.get("folders")
        if not isinstance(folders, list) or not folders or not isinstance(folders[0], str):
            return refuse("the window has no launch folder")
        if os.path.normcase(os.path.realpath(folders[0])) != os.path.normcase(os.path.realpath(cwd)):
            return refuse("the window's first folder differs from --cwd")
        if window.get("scopeReady") is not True or window.get("panelReady") is not True:
            return refuse("the Arc window is not ready")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return refuse("window discovery is unavailable")

    # An Arc CLI that no longer lists --untagged starts the reader's session by default.
    try:
        usage = run_arc(["--help"], 30)
    except (OSError, subprocess.TimeoutExpired):
        return refuse("the Arc command list is unavailable")
    if usage.returncode:
        return refuse("the Arc command list is unavailable")
    untagged = ["--untagged"] if re.search(r"(?<![\w-])--untagged(?![\w-])", usage.stdout or "") else []

    request_id = f"{host}:{uuid.uuid4().hex}"
    command = ["start", "--window", host, "--request-id", request_id,
               "--provider", args.runner, "--prompt-file", prompt_path,
               *untagged, "--name", name, "--permissions",
               "never" if args.runner == "codex" else args.permission_mode or "auto"]
    for flag, value in (("--model", args.model), ("--effort", args.effort)):
        if value:
            command += [flag, value]
    command.append("--arc-dispatch")
    print(f"window: {host}")
    print(f"request: {request_id}")
    try:
        launched = run_arc(command, 125)
        if launched.stderr:
            print(launched.stderr, end="" if launched.stderr.endswith("\n") else "\n", file=sys.stderr)
        result = json.loads(launched.stdout.splitlines()[0])
        if not isinstance(result, dict) or result.get("requestId") != request_id:
            raise ValueError("missing launch acknowledgment")
        status = result.get("status")
        if status == "refused":
            requester = result.get("requester")
            if (set(result).issubset({"version", "requestId", "status", "reason", "requester"})
                    and type(result.get("version")) is int and result["version"] == 1
                    and "requester" in result
                    and (requester is None or isinstance(requester, str) and 0 < len(requester) <= 256)
                    and ("reason" not in result or isinstance(result["reason"], str))):
                return refuse(result.get("reason", "Arc refused the launch"))
            raise ValueError("malformed launch refusal")
        print(json.dumps(result, ensure_ascii=False))
        if status in ("accepted", "accepted-with-errors"):
            session = result.get("sessionId")
            if not isinstance(session, str) or not session or result.get("provider") != args.runner:
                raise ValueError("missing accepted session identity")
            print("mode: Arc session")
            print(f"runner: {args.runner}")
            print(f"tree: {cwd}")
            print(f"name: {name}")
            print(f"id: {session}")
            _print_arc_receipt(session, name, True, args.runner)
            return 0
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as error:
        print(f"Arc start failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.TimeoutExpired) and error.stderr:
            detail = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
            print(detail, file=sys.stderr)
    print("mode: Arc delivery unknown")
    print(f"Arc delivery is unknown for request {request_id} in window {host}. "
          "Do not start another worker. Inspect this request before any identical-ID retry.", file=sys.stderr)
    return 2


def run_arguments(args, cwd, prompt_path, name, log):
    """Map dispatch flags to one arc run command line, without the launcher."""
    command = ["run", "--provider", args.runner, "--cwd", cwd, "--prompt-file", prompt_path, "--name", name]
    for flag, value in (("--model", args.model), ("--effort", args.effort)):
        if value:
            command += [flag, value]
    if args.read_only:
        command.append("--read-only")
    for tool in args.read_tool:
        command += ["--read-tool", tool]
    if args.full_access:
        command += ["--permissions", "danger-full-access"]
    elif args.permission_mode:
        command += ["--permissions", args.permission_mode]
    for directory in args.add_dir:
        command += ["--add-dir", directory]
    # Explicit classification keeps a workflow worker under Run by agents and a session
    # the reader asked for under reader ownership, whatever Arc's default becomes.
    command.append("--agent-run=false" if args.owned else "--agent-run")
    if args.attach:
        command.append("--attach")
    # Attended Codex refuses log and report paths, so only the generated default is
    # dropped. An explicit --log reaches Arc, which refuses it.
    if not (args.attach and args.runner == "codex") or args.log:
        command += ["--log", log]
    command += ["--completion", "wait" if args.wait else "manual"]
    if args.arc_dispatch:
        command.append("--arc-dispatch")
    command.append("--json")
    return command


def run_arc_json(launcher, arguments, cwd, timeout, required=False):
    """Run one Arc command and return its exit status, stdout lines and stderr. A launch
    raises when Arc gives no answer. A later read reports the failure as exit 2."""
    try:
        completed = subprocess.run([*launcher, *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        if required:
            raise
        return 2, [], f"{error}\n"
    return completed.returncode, (completed.stdout or "").splitlines(), completed.stderr or ""


def first_json(lines):
    for line in lines:
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def print_wait_result(reply):
    """Print the frozen report after the record, so the caller that blocked for the result
    gets its text in the same call. Arc omits the text of a report above its character cap
    or its JSON byte limit. That report prints its path and byte size, never its text, so
    one oversized report cannot flood the caller's context."""
    print("--- result")
    report = reply.get("report")
    text = reply.get("text")
    if not isinstance(report, dict):
        print("no report for this launch")
        return
    if not isinstance(text, str):
        print(f"the result text is unavailable inline. Read {report.get('path')}, {report.get('bytes')} bytes")
        return
    if not text:
        print(f"the result at {report.get('path')} is empty")
        return
    text = re.sub(r"(?m)^ARC_DISPATCH_SESSION=", "worker: ARC_DISPATCH_SESSION=", text)
    print(text, end="" if text.endswith("\n") else "\n")


def wait_limit():
    value = os.environ.get("DISPATCH_WAIT_LIMIT_SECONDS", "")
    return int(value) if re.fullmatch(r"[0-9]+", value) else WAIT_LIMIT_SECONDS


def settled_observation(launcher, cwd, launch_id, settle):
    """Arc's observation from arc dispatch status, or an empty dict when Arc gives none.

    Arc publishes the outcome before it saves the final observation. With settle, the read
    repeats while the supervisor still observes, up to EXIT_READS times."""
    observation = {}
    for attempt in range(EXIT_READS if settle else 1):
        if attempt:
            time.sleep(EXIT_READ_INTERVAL)
        status, lines, _errors = run_arc_json(launcher, ["dispatch", "status", launch_id, "--json"], cwd, ARC_WAIT_TIMEOUT)
        observation = None if status else (first_json(lines) or {}).get("observation")
        if not isinstance(observation, dict):
            return {}
        if type(observation.get("exitCode")) is int or observation.get("monitor") not in ("starting", "observing"):
            return observation
    return observation


def finish_wait(launcher, cwd, launch_id, reply, report_text=True):
    """Wait past arc run's deadline with arc dispatch wait, then print the frozen report,
    or with report_text False only its outcome. Each wait call blocks inside Arc, so this
    loop never sleeps on its own. A worker exit code from 1 to 255 becomes the exit code."""
    started = time.monotonic()
    limit = wait_limit()
    while reply.get("state") == "deadline":
        remaining = limit - (time.monotonic() - started)
        if remaining <= 0:
            print(f"Arc published no outcome for launch {launch_id} in {limit} seconds. "
                  f"Read arc dispatch status {launch_id} before starting another worker.", file=sys.stderr)
            return 2
        deadline_ms = max(1, min(110000, int(remaining * 1000)))
        status, lines, errors = run_arc_json(
            launcher, ["dispatch", "wait", launch_id, "--json", "--deadline-ms", str(deadline_ms)],
            cwd, min(ARC_WAIT_TIMEOUT, remaining + ARC_REQUEST_OVERHEAD))
        reply = first_json(lines) or {}
        if status:
            print(errors, end="", file=sys.stderr)
            print(f"Arc wait failed for launch {launch_id}. Read arc dispatch status {launch_id} before starting another worker.", file=sys.stderr)
            return 2
    print(f"outcome: {reply.get('outcome', reply.get('state', 'unknown'))}")
    event = reply.get("eventId")
    if reply.get("state") != "observed" or not isinstance(event, str):
        print(json.dumps(reply, ensure_ascii=False))
        return 1
    observation = settled_observation(launcher, cwd, launch_id, reply.get("outcome") != "needs-input")
    exit_code = observation.get("exitCode") if type(observation.get("exitCode")) is int else None
    if exit_code is not None:
        print(f"worker exit: {exit_code}")
    reason = observation.get("reason")
    if reply.get("outcome") != "succeeded" and isinstance(reason, str) and reason:
        print(f"Launch {launch_id} ended {reply.get('outcome')}: {reason}", file=sys.stderr)
    status, lines, errors = run_arc_json(launcher, ["dispatch", "result", launch_id, "--event", event, "--json"], cwd, ARC_WAIT_TIMEOUT)
    result = first_json(lines)
    if status or result is None:
        print(errors, end="", file=sys.stderr)
        print(f"Arc could not read the result of launch {launch_id} event {event}.", file=sys.stderr)
        return 2
    if report_text:
        print_wait_result(result)
    elif isinstance(result.get("report"), dict):
        print(f"result: {result['report'].get('path')}")
    if exit_code is not None and 0 < exit_code < 256:
        return exit_code
    return 0 if reply.get("outcome") == "succeeded" and not exit_code else 1


def run_standalone(args, launcher, cwd, prompt_path, name, log):
    if args.runner == "claude":
        use_dispatch_cache_ttl()
    try:
        status, lines, errors = run_arc_json(launcher, run_arguments(args, cwd, prompt_path, name, log), cwd, ARC_RUN_TIMEOUT, required=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        die(f"arc run gave no launch acknowledgment: {error}. Do not start another worker. "
            "Read arc dispatch status for this launch first")
    if errors:
        print(errors, end="" if errors.endswith("\n") else "\n", file=sys.stderr)
    receipt = first_json(lines)
    markers = [line for line in lines if line.startswith("ARC_DISPATCH_SESSION=")]
    if status or receipt is None or not isinstance(receipt.get("launchId"), str):
        for line in lines:
            print(line)
        return status or 2
    launch_id = receipt["launchId"]
    print(next(line for line in lines if line.startswith("{")))
    if receipt.get("state") not in OPENED_STATES:
        print(receipt.get("error") or f"arc run did not open launch {launch_id}: state {receipt.get('state')}", file=sys.stderr)
        print(f"status: arc dispatch status {launch_id}")
        for marker in markers:
            print(marker)
        return 2
    attended_codex = args.attach and args.runner == "codex"
    print("mode: arc run")
    print(f"runner: {args.runner}")
    print(f"name: {name}")
    print(f"tree: {cwd}")
    print(f"prompt: {prompt_path}")
    print(f"launch: {launch_id}")
    if not attended_codex:
        print(f"log: {log}")
    if args.runner == "codex" and not args.attach:
        print(f"result: {strip_ext(log)}.last.md")
    elif args.read_only and not attended_codex:
        print(f"result: {log}")
    print(f"status: arc dispatch status {launch_id}")
    observer = [Path(sys.executable).as_posix(), str(Path(__file__).resolve()), "--wait-launch", launch_id]
    if args.read_only:
        observer.append("--read-only")
    print("await: " + shlex.join(observer))
    if os.name == "nt":
        print("await (PowerShell): & " + " ".join("'" + value.replace("'", "''") + "'" for value in observer))
    if args.runner == "claude" and not args.read_only:
        for verb in ("logs", "attach", "stop"):
            print(f"{verb}: arc dispatch {verb} {launch_id}")
    elif not args.attach:
        print(f"watch: tail -f {shlex.quote(log)}")
    # A read-only launch serves a review, so its report stays out of the caller's output
    # until the caller blinds it. The record's result line names the file.
    code = finish_wait(launcher, cwd, launch_id, receipt, not args.read_only) if args.wait else 0
    for marker in markers:
        print(marker)
    return code


def build_parser():
    p = argparse.ArgumentParser(prog="dispatch.py", add_help=True, allow_abbrev=False)
    p.add_argument(
        "--runner", default="claude", metavar="RUNNER",
        help="claude or codex: which CLI runs the session (default: claude)",
    )
    p.add_argument(
        "--prompt", metavar="FILE",
        help="the path of a file that holds the prompt text",
    )
    p.add_argument(
        "--model", default="", metavar="MODEL",
        help="the model name, exactly as the runner's own --help or model list names it. arc run requires it",
    )
    p.add_argument(
        "--effort", default="", metavar="EFFORT",
        help="the reasoning effort, such as high. arc run requires it",
    )
    p.add_argument(
        "--name", default="", metavar="NAME",
        help="the session name, also used in the default log path",
    )
    p.add_argument(
        "--cwd", default=None, metavar="DIR",
        help="the working directory for the session (default: the current directory)",
    )
    p.add_argument(
        "--worktree", "-w", nargs="?", const=True, default=None, metavar="NAME",
        help="refused: create the worktree first and pass it with --cwd",
    )
    p.add_argument(
        "--add-dir", action="append", default=[], metavar="DIR",
        help="an extra directory the session can read or write, besides --cwd. Repeatable",
    )
    p.add_argument(
        "--permission-mode", default="", metavar="MODE",
        help="the Claude permission mode for a writable session (default: auto). Unusable with --read-only",
    )
    p.add_argument(
        "--read-tool", action="append", default=[], metavar="MCP_TOOL",
        help="an exact MCP tool to allow in Claude --read-only, such as mcp__server__get_item. Repeatable",
    )
    p.add_argument(
        "--read-only", action="store_true",
        help="run an analysis session with no permission prompts: Codex in its read-only sandbox, Claude with the edit tools and git writes denied",
    )
    p.add_argument(
        "--full-access", action="store_true",
        help="disable the Codex sandbox for a task that must commit, add a worktree, or run a browser test",
    )
    p.add_argument(
        "--attach", action="store_true",
        help="open the session in a new terminal window instead of running it detached",
    )
    p.add_argument(
        "--owned", action="store_true",
        help="mark the Arc receipt as a session the user's own message asked for. --attach implies this",
    )
    p.add_argument(
        "--log", default="", metavar="FILE",
        help="the log file path (default: the prompt file's path with .log in place of its extension, or .NAME.log with --name)",
    )
    p.add_argument(
        "--notify-thread", default=None, metavar="SESSION_ID",
        help="refused: Arc delivers no callback. Launch first, then observe with --wait-launch LAUNCH_ID",
    )
    p.add_argument(
        "--wait", action="store_true",
        help="block until Arc publishes the worker's outcome, then print its report text. With --read-only, print only the result path",
    )
    p.add_argument(
        "--wait-launch", default="", metavar="LAUNCH_ID",
        help="observe an existing launch without starting a worker. Carry --read-only to keep a review report blinded",
    )
    p.add_argument(
        "--arc-dispatch", action="store_true",
        help="required for launches: ask Arc for the ARC_DISPATCH_SESSION= marker that links this launch to the calling session",
    )
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Reconfigure stdout and stderr once, before the first record or error line prints.
    # A path, a session name, or a session id can hold a character the console code page
    # cannot encode. On stdout, printing it would raise UnicodeEncodeError and lose the
    # record and the exit code with it. On stderr it would print as a raw escape sequence.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    arc_only = bool(argv and argv[0] == "--internal-arc-only")
    if arc_only:
        argv.pop(0)
    args = build_parser().parse_args(argv)
    if args.worktree is not None:
        die(WORKTREE_REFUSAL)
    if args.notify_thread is not None:
        die(NOTIFY_REFUSAL)
    # A session opened in a window is one the owner watches, whether or not --owned was
    # also given.
    if args.attach:
        args.owned = True

    if args.wait_launch:
        allowed = {"--wait-launch", "--read-only", "--cwd"}
        if any(token.startswith("--") and token.split("=", 1)[0] not in allowed for token in argv):
            die("--wait-launch accepts only --read-only and --cwd. Observe the existing launch without launch flags")
        cwd = os.path.realpath(args.cwd) if args.cwd else os.getcwd()
        if not os.path.isdir(cwd):
            die(f"--cwd must be a directory: {cwd}")
        arc_bin = resolve_bin("ARC_BIN", "arc")
        if not arc_bin:
            die("dispatch needs the Arc CLI: put arc on PATH or set ARC_BIN")
        launcher = arc_command(arc_bin)
        if launcher is None:
            die("the Arc launcher cannot preserve literal arguments: set ARC_BIN to the Arc JavaScript entry")
        return finish_wait(launcher, cwd, args.wait_launch, {"state": "deadline"}, not args.read_only)

    if not args.prompt or not os.path.isfile(args.prompt):
        # A path can hold spaces on its own, such as a real Windows directory name, so only
        # a newline or a long value marks the argument as prompt text rather than a path.
        if args.prompt and ("\n" in args.prompt or len(args.prompt) > 200):
            die("--prompt takes a file path, and this value looks like prompt text")
        die("--prompt FILE is required and must exist")
    if args.runner not in ("claude", "codex"):
        die("--runner must be claude or codex")
    if args.runner == "codex" and args.model:
        args.model = resolve_codex_model(args.model)
    if args.runner == "claude" and args.model == "haiku":
        args.model = "claude-haiku-5-5"
    if args.full_access and args.read_only:
        die("--full-access and --read-only exclude each other")
    if args.full_access and args.runner == "claude":
        die("--full-access is a Codex flag: use --permission-mode for a Claude background session")
    # arc start takes no read tools, so this check runs before the window route.
    if args.read_tool and (args.runner != "claude" or not args.read_only):
        die("--read-tool requires --runner claude --read-only")
    if not args.arc_dispatch:
        die("--arc-dispatch is required for a new session. Add --arc-dispatch to the foreground launch command")

    cwd = os.path.realpath(args.cwd) if args.cwd else os.getcwd()
    if not os.path.isdir(cwd):
        die(f"--cwd must be a directory: {cwd}")
    prompt_path = os.path.realpath(args.prompt)
    name = args.name or f"dispatch-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    log = os.path.realpath(args.log) if args.log else default_log(prompt_path, args.name)

    arc_result = try_arc_start(args, cwd, prompt_path, name)
    if arc_result is not None:
        return arc_result
    if arc_only:
        return 3

    arc_bin = resolve_bin("ARC_BIN", "arc")
    if not arc_bin:
        die("dispatch needs the Arc CLI: put arc on PATH or set ARC_BIN")
    launcher = arc_command(arc_bin)
    if launcher is None:
        die("the Arc launcher cannot preserve literal arguments: set ARC_BIN to the Arc JavaScript entry")
    return run_standalone(args, launcher, cwd, prompt_path, name, log)


if __name__ == "__main__":
    sys.exit(main())
