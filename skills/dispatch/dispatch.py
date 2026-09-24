#!/usr/bin/env python
"""Start a separate agent session from a prompt file.

Python port of dispatch.sh, at parity with its flags and its one-line launch
record, plus --wait. Standard library only, Python 3.10 or later.

  python dispatch.py --runner claude|codex --prompt FILE [--model M] [--effort E]
      [--name N] [--cwd DIR] [--worktree] [--add-dir DIR]... [--permission-mode MODE]
      [--read-tool MCP_TOOL]... [--read-only] [--full-access] [--attach] [--owned]
      [--log FILE] [--notify-thread CODEX_SESSION] [--wait]

Claude writable sessions default to auto permission mode unless overridden.

Prints one record line per fact: mode, runner, model, pid or id, tree, prompt,
log/result, and commands that watch or manage the session.

Two design choices carry no bash equivalent:

- The prompt travels on stdin for every run except the attended codex window.
  On Windows, codex and claude resolve to npm .cmd shims, and a .cmd file can
  only run through cmd.exe, which re-parses argv, so keeping the prompt out
  of argv keeps its text out of that re-parse and off the 32,767-character
  Windows command-line cap. The attended window is the one exception: it
  runs interactive codex, not `codex exec`, and only `codex exec` reads a
  prompt from stdin, so the attended window passes the prompt the way
  dispatch.sh does, substituted from the prompt file into the launched
  command line.
- A detached run (no --wait) still has to report the codex thread id before
  it returns, but the worker must outlive this process. dispatch.py launches
  itself again with a hidden --internal-supervise mode: that second process
  is the real parent of the worker, tees its stdout to the log file, and
  drops the worker's pid, and (for codex) its thread id, in small handoff
  files as soon as each is known. The short-lived launcher polls those
  files for a few seconds, prints the record, and exits, leaving the
  supervisor to finish the worker and, if asked, send the notify-thread
  callback.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import watch_claude

SKILL_DIR = Path(__file__).resolve().parent

# The Codex CLI takes a full model id. A shorthand from the references skill reaches it
# as a literal and the request fails with a 400. Resolve the OpenAI shorthands here.
# Amazon Bedrock uses an "openai." prefix for these models.
CODEX_MODEL_SHORTHANDS = {
    "sol": "gpt-6-sol",
    "sol6": "gpt-6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-6-luna",
    "luna6": "gpt-6-luna",
    "astra": "gpt-6-astra",
}
BEDROCK_PROVIDER = "amazon-bedrock"
BEDROCK_OPENAI_PREFIX = "openai."

READONLY_CLAUDE_TOOLS = ["Read", "Grep", "Glob"]
WRITABLE_CLAUDE_TOOLS = ["Bash", "Edit", "Write", "Read", "Grep", "Glob", "MultiEdit"]
READONLY_CLAUDE_DENY = [
    "Edit", "Write", "NotebookEdit", "MultiEdit", "WebFetch", "WebSearch", "Task", "Agent",
    "CronCreate", "CronDelete", "DesignSync", "EnterWorktree", "ExitWorktree",
    "PushNotification", "RemoteTrigger", "ScheduleWakeup", "SendMessage",
    "ShareOnboardingGuide", "TaskStop",
]

# A dispatched Claude session writes its prompt cache at the 5-minute lifetime. On a
# subscription, Claude Code defaults the main conversation to 1 hour, and a 1-hour write
# costs 2 times the input rate against 1.25 times for 5 minutes. The longer lifetime pays
# off only when a person pauses 5 to 60 minutes between requests. A dispatched session
# runs without those pauses, so the shorter lifetime costs less. SKILL.md holds the
# measurement. A value the caller already set in the environment wins.
CACHE_TTL_ENV = "CLAUDE_CODE_PROMPT_CACHE_TTL"
DISPATCH_CACHE_TTL = "5m"

# How long a detached read-only Claude pump waits, in short ticks, for a worker to exit
# before trusting it as still running and printing its receipt. Matches dispatch.sh's
# status-file poll window, so a worker that dies shortly after start reads the same way
# on both launchers instead of only on the shell one.
STILL_RUNNING_POLL_SECONDS = 0.5
STILL_RUNNING_POLL_INTERVAL = 0.05


def use_dispatch_cache_ttl():
    os.environ.setdefault(CACHE_TTL_ENV, DISPATCH_CACHE_TTL)

import re

# \Z anchors on the literal end of the string. $ would also accept one trailing newline,
# which cmd.exe can leave on an argument such as mcp__a__b\n after re-parsing a claude.cmd
# command line, and that stray tool would then silently escape --disallowedTools.
READ_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_.:-]+__[A-Za-z0-9_.:-]+\Z")


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


def add_dir_flags(dirs):
    flags = []
    for d in dirs:
        flags += ["--add-dir", d]
    return flags


def resolve_bin(env_var, default_name):
    override = os.environ.get(env_var)
    candidate = override if override else default_name
    return shutil.which(candidate)


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


def resolve_git_bash():
    """Derive Git Bash from git's own install location rather than searching PATH for a
    bare "bash", which can resolve to WSL's bash.exe in System32 and cannot run a C:\\
    run file. git.exe itself can be one of several depths under the Git install root
    (cmd\\git.exe, mingw64\\bin\\git.exe), so this checks each ancestor directory rather
    than assuming a fixed number of parents."""
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    for ancestor in list(Path(git_bin).resolve().parents)[:5]:
        for candidate in (ancestor / "bin" / "bash.exe", ancestor / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def worktree_home(path):
    """Claude resolves -w against the repository's main checkout, not the launching
    directory. A session started in a linked worktree lands under the main checkout,
    so a record built from a linked-worktree path names a location that does not exist."""
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return path
    common = out.stdout.strip()
    return os.path.dirname(common) if common else path


def git_commit_of(path, ref):
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return out.stdout.strip() or None


def refuse_worktree_behind_head(cwd):
    """Claude creates a -w worktree from origin/HEAD, not from the local HEAD. A checkout
    ahead of its remote would start the worker without the local commits."""
    origin = git_commit_of(cwd, "refs/remotes/origin/HEAD")
    head = git_commit_of(cwd, "HEAD")
    if origin and head and origin != head:
        die(
            f"--worktree would start the worker at origin/HEAD {origin[:8]}, not at HEAD {head[:8]}, "
            "because Claude creates its worktree from origin/HEAD. "
            "Run git worktree add -b <branch> <path> HEAD, then launch with --cwd <path> and without --worktree"
        )


def print_common_record(model, cwd, prompt_path, log):
    return [
        f"model: {model or 'default'}",
        f"tree: {cwd}",
        f"prompt: {prompt_path}",
        f"log: {log}",
    ]


def open_window(command, cwd, prompt_path):
    run_file = strip_ext(prompt_path) + ".run.sh"
    with open(run_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n")
        fh.write(f"cd {shlex.quote(cwd)}\n")
        fh.write(command + "\n")
    system = platform.system()
    if system == "Windows":
        bash_path = resolve_git_bash()
        if not bash_path:
            die("no Git Bash found next to git on PATH to open a window with")
        # A bare "wt" only auto-resolves to wt.exe: CreateProcess appends .exe by itself
        # when given no extension, but never tries .cmd or .bat the way cmd.exe's own
        # PATHEXT search does, so it can never find a non-.exe stand-in placed on PATH.
        # shutil.which resolves the full path with its real extension first, the same way
        # every other executable in this file (claude, codex, git) is resolved, and Popen
        # then launches exactly that resolved path.
        wt_path = shutil.which("wt")
        if not wt_path:
            die("no Windows Terminal (wt) found on PATH to open a window with")
        subprocess.Popen([wt_path, "-d", cwd, bash_path, run_file])
    elif system == "Darwin":
        script = f'tell application "Terminal" to do script "bash {run_file}"'
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL)
    else:
        if os.environ.get("TMUX"):
            subprocess.Popen(["tmux", "new-window", f"bash {run_file}"])
        elif shutil.which("x-terminal-emulator"):
            subprocess.Popen(["x-terminal-emulator", "-e", "bash", run_file])
        elif shutil.which("gnome-terminal"):
            subprocess.Popen(["gnome-terminal", "--", "bash", run_file])
        else:
            die("no terminal launcher found (tmux, x-terminal-emulator, or gnome-terminal)")
    print(f"window: bash {run_file}")


def _detach_kwargs():
    if os.name == "nt":
        # DETACHED_PROCESS lets a console-app grandchild, such as node under a detached
        # claude.cmd, allocate its own visible console window. Closing that window sends
        # a close signal that kills whatever is attached to it. CREATE_NO_WINDOW never
        # allocates a console for the tree, so there is no window left for anyone to close.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _child_pids(parent_pid):
    """The direct children of parent_pid as (pid, exe_name) pairs, read from a Toolhelp32
    snapshot (ctypes and the Win32 API only, no external dependency). Windows-only; call
    behind an os.name guard."""
    import ctypes

    class _ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32First.restype = ctypes.c_int
    kernel32.Process32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32)]
    kernel32.Process32Next.restype = ctypes.c_int
    kernel32.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []
    children = []
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        found = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ParentProcessID == parent_pid:
                children.append((entry.th32ProcessID, entry.szExeFile.decode(errors="replace")))
            found = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return children


def _resolve_worker_pid(wrapper_pid, target=None, timeout=3.0):
    """On Windows, spawning a .cmd or .bat target such as codex.cmd or claude.cmd returns
    the pid of an intermediate cmd.exe that CreateProcess inserts to run it, not the real
    worker underneath. Killing only that wrapper leaves the worker running as an orphan, so
    find its one child and report that instead. A native target, such as an .exe resolved
    directly, spawns with no such wrapper: wrapper_pid already names the worker, and
    walking its children would instead find whatever short-lived helper process, such as
    git or an MCP server, that worker happens to start on its own. target names the
    resolved executable so this can tell the two cases apart; pass it whenever it is known.
    A non-.cmd/.bat target, and every POSIX target, returns wrapper_pid immediately.

    Under CREATE_NO_WINDOW, Windows still attaches a hidden conhost.exe to the wrapper's
    console even though no window ever appears for it, so it shows up as a same-generation
    sibling of the real worker in the wrapper's child list. It is never the worker, so it is
    always skipped rather than ever being the pid this returns."""
    if os.name != "nt":
        return wrapper_pid
    if target is not None and Path(target).suffix.lower() not in (".cmd", ".bat"):
        return wrapper_pid
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        children = [pid for pid, name in _child_pids(wrapper_pid) if name.lower() != "conhost.exe"]
        if children:
            return children[0]
        time.sleep(0.05)
    return wrapper_pid


def _spawn_watch_claude(claude_bin, codex_bin, agent_id, name, thread, result, attach, notify_log_path):
    watch_script = str(SKILL_DIR / "watch_claude.py")
    cmd = [sys.executable, watch_script, claude_bin, codex_bin, agent_id, name, thread, result, attach]
    log_fh = open(notify_log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT, **_detach_kwargs())
    log_fh.close()
    return proc.pid


def _codex_pump(cmd, cwd, prompt_path, log_path, id_file_path, result_path, notify_thread, notify_log_path, name, codex_bin, pid_file_path=None):
    """Run codex to completion, teeing its --json stdout to the log file and capturing
    the thread.started id as soon as it appears. Blocks until the worker exits."""
    thread_id = None
    with open(prompt_path, "rb") as prompt_fh, open(log_path, "w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=prompt_fh, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        if pid_file_path:
            with open(pid_file_path, "w", encoding="utf-8") as pf:
                pf.write(str(_resolve_worker_pid(proc.pid, cmd[0])))
        for line in proc.stdout:
            log_fh.write(line)
            log_fh.flush()
            if thread_id is None:
                stripped = line.strip()
                if stripped:
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        event = None
                    if isinstance(event, dict) and event.get("type") == "thread.started":
                        thread_id = event.get("thread_id")
                        if id_file_path:
                            with open(id_file_path, "w", encoding="utf-8") as idf:
                                idf.write(thread_id or "")
        proc.stdout.close()
        status = proc.wait()
    if notify_thread:
        state = "completed" if status == 0 else "failed"
        message = f"Fresh-session handoff {name} {state} (exit {status}). Result: {result_path}"
        watch_claude.send_notice(codex_bin, notify_thread, message, notify_log_path)
    return thread_id, status


def _claude_readonly_pump(cmd, cwd, prompt_path, log_path, result_path, notify_thread, notify_log_path, name, codex_bin, pid_file_path=None, id_file_path=None, expected_session_id=None):
    with open(prompt_path, "rb") as prompt_fh, open(log_path, "wb") as log_fh:
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=prompt_fh, stdout=log_fh, stderr=subprocess.STDOUT)
        if pid_file_path:
            with open(pid_file_path, "w", encoding="utf-8") as pf:
                pf.write(str(_resolve_worker_pid(proc.pid, cmd[0])))
        if id_file_path:
            # The caller knows this ID before launch. A prompt, failed CLI start, or
            # immediate rejected option must not produce a receipt. The poll runs for
            # STILL_RUNNING_POLL_SECONDS, the same bound dispatch.sh's status-file wait
            # uses, so both launchers agree on a worker that dies shortly after start
            # rather than only within the first tick.
            status = None
            deadline = time.monotonic() + STILL_RUNNING_POLL_SECONDS
            while True:
                try:
                    status = proc.wait(timeout=STILL_RUNNING_POLL_INTERVAL)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        break
            if status is None or (status == 0 and _extract_claude_session_id(log_path) == expected_session_id):
                with open(id_file_path, "w", encoding="utf-8") as idf:
                    idf.write(expected_session_id)
        status = proc.wait()
    if notify_thread:
        state = "completed" if status == 0 else "failed"
        message = f"Fresh-session handoff {name} {state} (exit {status}). Result: {result_path}"
        watch_claude.send_notice(codex_bin, notify_thread, message, notify_log_path)
    return status


def _new_params_path(log_path):
    """A parameters file name unique to this one call, from tempfile.mkstemp, so two
    launches never share one, whether or not they share a log path. A name derived only
    from log_path would let a second launch at the same path overwrite the first launch's
    parameters, and a read-only launch could then run a writable one's command, or the
    reverse."""
    params_dir = os.path.dirname(log_path) or "."
    fd, path = tempfile.mkstemp(prefix="dispatch-", suffix=".pump.json", dir=params_dir)
    os.close(fd)
    return path


def _spawn_supervisor(params, id_file_path=None, pid_file_path=None, wait_seconds=20):
    """Launch --internal-supervise as its own detached process, which becomes the real
    parent of the worker so it can outlive this launch. A leftover id or pid file from an
    earlier run at the same log path would otherwise read back as this run's session id or
    pid, so clear both before the new supervisor starts. Poll briefly for the worker's real
    pid, and, when id_file_path is set, the provider session id, so the launcher reports the
    process that killing actually stops. Stop polling once both are known, once the
    supervisor itself has exited (nothing more will ever be written), or once wait_seconds
    runs out. The parameters file that hands the launch to --internal-supervise gets a name
    unique to this call, from tempfile.mkstemp, so two launches never share one, whether or
    not they share a log path: sharing one would let a read-only launch overwrite the params
    a concurrent writable launch is about to read, or the reverse."""
    if id_file_path:
        try:
            os.remove(id_file_path)
        except OSError:
            pass
    if pid_file_path:
        try:
            os.remove(pid_file_path)
        except OSError:
            pass
    call_params = dict(params)
    call_params["pid_file_path"] = pid_file_path
    params_path = _new_params_path(params["log_path"])
    with open(params_path, "w", encoding="utf-8") as fh:
        json.dump(call_params, fh)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--internal-supervise", params_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detach_kwargs())

    def _read_stripped(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    result_id = None
    worker_pid = None
    supervisor_exit = None
    deadline = time.monotonic() + wait_seconds
    while True:
        if id_file_path and result_id is None and os.path.exists(id_file_path):
            content = _read_stripped(id_file_path)
            if content:
                result_id = content
        if pid_file_path and worker_pid is None and os.path.exists(pid_file_path):
            content = _read_stripped(pid_file_path)
            if content.isdigit():
                worker_pid = int(content)
        id_done = (not id_file_path) or (result_id is not None)
        pid_done = (not pid_file_path) or (worker_pid is not None)
        if id_done and pid_done:
            break
        supervisor_exit = proc.poll()
        if supervisor_exit is not None or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    # The pid file is the first thing the supervisor writes once its worker exists. A
    # supervisor that exited without ever writing one crashed before starting the worker,
    # so the pid this would otherwise report (its own, already-dead pid) names nothing a
    # caller can watch or kill: treat that as a failed launch instead of a quiet success.
    if pid_file_path and worker_pid is None and supervisor_exit is not None:
        die(f"the detached supervisor exited before starting the worker (exit {supervisor_exit})")
    return (worker_pid if worker_pid is not None else proc.pid), result_id


def _run_internal_supervise(params_path):
    with open(params_path, "r", encoding="utf-8") as fh:
        params = json.load(fh)
    try:
        if params["kind"] == "codex":
            _thread_id, status = _codex_pump(
                params["cmd"], params["cwd"], params["prompt_path"], params["log_path"],
                params.get("id_file_path"), params["result_path"], params.get("notify_thread"),
                params.get("notify_log_path"), params["name"], params["codex_bin"],
                pid_file_path=params.get("pid_file_path"),
            )
        else:
            status = _claude_readonly_pump(
                params["cmd"], params["cwd"], params["prompt_path"], params["log_path"],
                params["result_path"], params.get("notify_thread"), params.get("notify_log_path"),
                params["name"], params["codex_bin"],
                pid_file_path=params.get("pid_file_path"),
                id_file_path=params.get("id_file_path"), expected_session_id=params.get("expected_session_id"),
            )
    finally:
        try:
            os.remove(params_path)
        except OSError:
            pass
    return status


def _extract_claude_session_id(log_path):
    # The log file holds Claude's raw combined stdout and stderr. errors="replace" keeps
    # one bad byte in that stream from raising UnicodeDecodeError and losing the --wait
    # record, and the worker's already-final exit code, after Claude has already run.
    try:
        session_id = None
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get("session_id"):
                    session_id = data["session_id"]
        return session_id
    except OSError:
        pass
    return None


def _print_arc_receipt(session_id, name, owned=False):
    receipt = {"agent": "claude", "session_id": session_id, "name": name}
    if owned:
        receipt["owned"] = True
    print("ARC_DISPATCH_SESSION=" + json.dumps(receipt, separators=(",", ":")))


def uuid_is_full(value):
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _format_session_line(thread_id, log_path):
    """The record line printed for a detached codex launch. Without --wait, codex may not
    have emitted thread.started yet by the time the launcher gives up waiting, so 'unknown'
    would read as a permanent fact when the id is really still on its way into log_path."""
    if thread_id:
        return f"session: {thread_id}"
    return f"session: pending, appears in {log_path} once codex reports thread.started"


WAIT_RESULT_CHAR_CAP = 16000


def _print_wait_result(result_path):
    """Print the text of a --wait run's result file after its record lines, so the caller
    that already blocked for the exit code gets the text in the same call instead of
    spending a second one to read the file. A result above the character cap prints only
    its path and its byte size, never its text, so one oversized result cannot flood the
    caller's context."""
    print("--- result")
    try:
        size = os.path.getsize(result_path)
    except OSError:
        print(f"no result file at {result_path}")
        return
    if size == 0:
        print(f"the result file at {result_path} is empty")
        return
    with open(result_path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if len(text) > WAIT_RESULT_CHAR_CAP:
        print(f"the result at {result_path} is {size} bytes, above the {WAIT_RESULT_CHAR_CAP:,} character cap")
        return
    print(text, end="" if text.endswith("\n") else "\n")


@dataclass
class Context:
    cwd: str
    prompt_path: str
    name: str
    log: str
    notify_log: str
    claude_bin: str | None
    codex_bin: str | None


def do_claude_read_only(args, ctx):
    use_dispatch_cache_ttl()
    readonly_tools = list(READONLY_CLAUDE_TOOLS)
    for tool in args.read_tool:
        if not READ_TOOL_RE.match(tool):
            die(f"--read-tool must name one exact MCP tool, such as mcp__server__get_item: {tool}")
        readonly_tools.append(tool)

    cli_args = ["-p", "--permission-mode", "dontAsk", "--permission-prompts", "none"]
    # An allow rule in any settings file outranks dontAsk, so a rule such as
    # Bash(python -c *) would let a reviewer write. With no settings loaded, dontAsk runs
    # only Bash commands Claude Code classifies as read-only. An MCP read needs the
    # settings that configure its server, so that run keeps them and loses both shells.
    if args.read_tool:
        shell_denials = ["Bash", "PowerShell"]
    else:
        cli_args += ["--setting-sources", ""]
        shell_denials = ["PowerShell"]
    cli_args += ["--allowedTools", *readonly_tools]
    cli_args += ["--disallowedTools", *shell_denials, *READONLY_CLAUDE_DENY]
    cli_args += ["--output-format", "json"]
    # A waited run takes its id from the JSON result instead, so it never needs one chosen
    # up front. Every other read-only run does, because it must print a receipt for a
    # session id known only before launch, with or without --arc-dispatch.
    chosen_session_id = str(uuid.uuid4()) if not args.wait else None
    if chosen_session_id:
        cli_args += ["--session-id", chosen_session_id]
    if args.model:
        cli_args += ["--model", args.model]
    if args.effort:
        cli_args += ["--effort", args.effort]
    cmd = [ctx.claude_bin, *cli_args, *add_dir_flags(args.add_dir)]

    if args.wait:
        status = _claude_readonly_pump(cmd, ctx.cwd, ctx.prompt_path, ctx.log, ctx.log, None, None, ctx.name, ctx.codex_bin)
        session_id = _extract_claude_session_id(ctx.log)
        print("mode: read-only waited")
        print("runner: claude")
        for line in print_common_record(args.model, ctx.cwd, ctx.prompt_path, ctx.log):
            print(line)
        print(f"result: {ctx.log}")
        print(f"session: {session_id or 'unknown'}")
        _print_wait_result(ctx.log)
        if status == 0 and session_id and uuid_is_full(session_id):
            _print_arc_receipt(session_id, ctx.name, args.owned)
        raise SystemExit(status)

    # A detached run always chooses its session id before launch, so it always runs under
    # the supervisor to learn that id back, with or without --arc-dispatch.
    params = {
        "kind": "claude_readonly", "cmd": cmd, "cwd": ctx.cwd, "prompt_path": ctx.prompt_path,
        "log_path": ctx.log, "result_path": ctx.log, "notify_thread": args.notify_thread,
        "notify_log_path": ctx.notify_log, "name": ctx.name, "codex_bin": ctx.codex_bin,
        "expected_session_id": chosen_session_id,
    }
    pid_file_path = ctx.log + ".pid"
    fd, id_file_path = tempfile.mkstemp(prefix="dispatch-", suffix=".id", dir=os.path.dirname(ctx.log) or ".")
    os.close(fd)
    params["id_file_path"] = id_file_path
    pid, launched_id = _spawn_supervisor(params, id_file_path=id_file_path, pid_file_path=pid_file_path)
    try:
        os.remove(pid_file_path)
    except OSError:
        pass
    try:
        os.remove(id_file_path)
    except OSError:
        pass

    print("mode: read-only print")
    print("runner: claude")
    print(f"pid: {pid}")
    for line in print_common_record(args.model, ctx.cwd, ctx.prompt_path, ctx.log):
        print(line)
    print(f"result: {ctx.log}")
    print(f"watch: tail -f {shlex.quote(ctx.log)}")
    if args.notify_thread:
        print(f"notify-thread: {args.notify_thread}")
        print(f"notify-log: {ctx.notify_log}")
    if launched_id == chosen_session_id:
        _print_arc_receipt(launched_id, ctx.name, args.owned)


def do_claude_writable(args, ctx):
    if args.worktree:
        refuse_worktree_behind_head(ctx.cwd)
    use_dispatch_cache_ttl()
    cli_args = [
        "--bg", "-n", ctx.name, "--permission-mode", args.permission_mode or "auto",
        "--allowedTools", " ".join(WRITABLE_CLAUDE_TOOLS),
    ]
    if args.worktree:
        cli_args += ["-w", ctx.name]
    if args.model:
        cli_args += ["--model", args.model]
    if args.effort:
        cli_args += ["--effort", args.effort]
    cmd = [ctx.claude_bin, *cli_args, *add_dir_flags(args.add_dir)]

    with open(ctx.prompt_path, "rb") as prompt_fh:
        proc = subprocess.run(cmd, cwd=ctx.cwd, stdin=prompt_fh, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        print(out, end="")
        raise SystemExit(1)

    agent_id = None
    receipt_id = None
    # Every launch runs this strict match, with or without --arc-dispatch, because the
    # receipt now prints by default. The loose fallback below still runs when the
    # strict regex finds no line, such as a name dispatch.py itself did not choose, or
    # when it finds more than one. Claude already started the session with exit 0 either
    # way, so ambiguous output must not abort a launch already under way: it only costs
    # the receipt, with a warning on stderr.
    launch_line = re.compile(r"^backgrounded · ([0-9a-fA-F]{8}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}) · " + re.escape(ctx.name) + r"$")
    matches = []
    for line in out.splitlines():
        line = line.removesuffix(" (idle \u2014 send a prompt to start)")
        match = launch_line.fullmatch(line)
        if match:
            matches.append(match.group(1))
    if len(matches) == 1:
        agent_id = matches[0]
        receipt_id = agent_id
    elif len(matches) > 1:
        print("ambiguous Claude background launch output: printing no Arc receipt for this launch", file=sys.stderr)
    if agent_id is None:
        for line in out.splitlines():
            if "backgrounded" in line:
                parts = [p.strip() for p in line.split("·")]
                if len(parts) >= 2:
                    agent_id = parts[1]
                break
    if not agent_id:
        print(out, end="")
        print("no session id in the launch output", file=sys.stderr)
        raise SystemExit(1)

    print("mode: writable background")
    print("runner: claude")
    print(f"id: {agent_id}")
    print(f"name: {ctx.name}")
    if args.worktree:
        print(f"tree: {worktree_home(ctx.cwd)}/.claude/worktrees/{ctx.name}")
        print(f"branch: worktree-{ctx.name}")
    else:
        print(f"tree: {ctx.cwd}")
    print(f"model: {args.model or 'default'}")
    print(f"prompt: {ctx.prompt_path}")
    print(f"log: claude logs {agent_id}")
    print(f"result: claude logs {agent_id}")
    print(f"watch: claude logs {agent_id}")
    print(f"attach: claude attach {agent_id}")
    print(f"stop: claude stop {agent_id}")
    print(f"remove: claude rm {agent_id}")
    if args.notify_thread:
        pid = _spawn_watch_claude(
            ctx.claude_bin, ctx.codex_bin, agent_id, ctx.name, args.notify_thread,
            f"claude logs {agent_id}", f"claude attach {agent_id}", ctx.notify_log,
        )
        print(f"notify-thread: {args.notify_thread}")
        print(f"notify-log: {ctx.notify_log}")
        print(f"monitor-pid: {pid}")
    if args.attach:
        open_window(f"claude attach {shlex.quote(agent_id)}", ctx.cwd, ctx.prompt_path)
    if receipt_id and (re.fullmatch(r"[0-9a-fA-F]{8}", receipt_id) or uuid_is_full(receipt_id)):
        _print_arc_receipt(agent_id, ctx.name, args.owned)


def do_codex(args, ctx):
    if args.worktree:
        die("codex makes no worktree: run git worktree add first and pass it with --cwd")

    # workspace-write keeps .git read-only and blocks a browser start, and exec can never
    # ask, so a task that must commit, add a worktree or run a browser test runs unsandboxed.
    sandbox = "workspace-write"
    if args.read_only:
        sandbox = "read-only"
    if args.full_access:
        sandbox = "danger-full-access"
    last = strip_ext(ctx.log) + ".last.md"

    if args.attach:
        window_args = ["-C", ctx.cwd]
        if args.model:
            window_args += ["-m", args.model]
        if args.effort:
            window_args += ["-c", f"model_reasoning_effort={args.effort}"]
        window_args += ["-s", sandbox]
        window_args += add_dir_flags(args.add_dir)
        command = " ".join(shlex.quote(part) for part in [ctx.codex_bin, *window_args])
        # Interactive codex (no `exec`) takes its prompt as a positional argument, not on
        # stdin, so this substitutes the prompt file the way dispatch.sh does.
        command += f' "$(< {shlex.quote(ctx.prompt_path)})"'
        open_window(command, ctx.cwd, ctx.prompt_path)
        mode = "writable attended"
        if args.read_only:
            mode = "read-only attended"
        if args.full_access:
            mode = "full-access attended"
        print(f"mode: {mode}")
        print("runner: codex")
        for line in print_common_record(args.model, ctx.cwd, ctx.prompt_path, ctx.log):
            print(line)
        print("result: attended session")
        print("watch: use the opened window")
        return

    cli_args = ["exec", "-C", ctx.cwd, "-s", sandbox, "-o", last, "--json"]
    if args.model:
        cli_args += ["-m", args.model]
    if args.effort:
        cli_args += ["-c", f"model_reasoning_effort={args.effort}"]
    cli_args += add_dir_flags(args.add_dir)
    cmd = [ctx.codex_bin, *cli_args]

    mode_word = "writable"
    if args.read_only:
        mode_word = "read-only"
    if args.full_access:
        mode_word = "full-access"

    if args.wait:
        thread_id, status = _codex_pump(cmd, ctx.cwd, ctx.prompt_path, ctx.log, None, last, args.notify_thread, ctx.notify_log, ctx.name, ctx.codex_bin)
        print(f"mode: {mode_word} waited")
        print("runner: codex")
        for line in print_common_record(args.model, ctx.cwd, ctx.prompt_path, ctx.log):
            print(line)
        print(f"result: {last}")
        print(f"session: {thread_id or 'unknown'}")
        _print_wait_result(last)
        raise SystemExit(status)

    id_file_path = ctx.log + ".id"
    pid_file_path = ctx.log + ".pid"
    params = {
        "kind": "codex", "cmd": cmd, "cwd": ctx.cwd, "prompt_path": ctx.prompt_path, "log_path": ctx.log,
        "id_file_path": id_file_path, "result_path": last, "notify_thread": args.notify_thread,
        "notify_log_path": ctx.notify_log, "name": ctx.name, "codex_bin": ctx.codex_bin,
    }
    pid, thread_id = _spawn_supervisor(params, id_file_path=id_file_path, pid_file_path=pid_file_path)
    try:
        os.remove(id_file_path)
    except OSError:
        pass
    try:
        os.remove(pid_file_path)
    except OSError:
        pass

    print(f"mode: {mode_word} detached")
    print("runner: codex")
    print(f"pid: {pid}")
    for line in print_common_record(args.model, ctx.cwd, ctx.prompt_path, ctx.log):
        print(line)
    print(f"result: {last}")
    print(_format_session_line(thread_id, ctx.log))
    print(f"watch: tail -f {shlex.quote(ctx.log)}")
    if args.notify_thread:
        print(f"notify-thread: {args.notify_thread}")
        print(f"notify-log: {ctx.notify_log}")


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
        help="the model name, exactly as the runner's own --help or model list names it",
    )
    p.add_argument(
        "--effort", default="", metavar="EFFORT",
        help="the reasoning effort, such as high",
    )
    p.add_argument(
        "--name", default="", metavar="NAME",
        help="the session name, also used for the worktree directory and branch when --worktree is set",
    )
    p.add_argument(
        "--cwd", default=None, metavar="DIR",
        help="the working directory for the session (default: the current directory)",
    )
    p.add_argument(
        "--worktree", action="store_true",
        help="create a Claude worktree named after --name. Codex makes no worktree of its own",
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
        help="run a read-only analysis session with no permission prompts and no file changes",
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
        "--notify-thread", default="", metavar="SESSION_ID",
        help="the origin Codex session id to notify with a queued message when a detached session finishes",
    )
    p.add_argument(
        "--wait", action="store_true",
        help="block for the worker's exit code and print its result text (Claude --read-only or Codex only)",
    )
    # Parsed and accepted, never read: Arc's reader scans the command's own text for this
    # flag, so args.arc_dispatch itself has no effect on any launch.
    p.add_argument(
        "--arc-dispatch", action="store_true",
        help="the marker Arc reads from a command's own text. Every Claude launch prints its receipt whether or not this flag is set",
    )
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Reconfigure stdout and stderr once, before the first record or error line prints,
    # not only right before the result text. A path, a session name, or a session id
    # can hold a character the console code page cannot encode. On stdout, printing it
    # ahead of a later reconfigure raises UnicodeEncodeError and loses the record and
    # the exit code with it. stderr never raises on that character, but without this
    # it renders it as a raw backslash-escape sequence instead of the text itself.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    if argv and argv[0] == "--internal-supervise":
        return _run_internal_supervise(argv[1])

    args = build_parser().parse_args(argv)
    # A session opened in a window is one the owner watches, whether or not --owned was
    # also given.
    if args.attach:
        args.owned = True

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
    if args.read_only and args.attach and args.runner == "claude":
        die("--attach is unsupported with Claude --read-only: print-mode output is buffered in the result log")
    if args.read_only and args.worktree and args.runner == "claude":
        die("--worktree is unsupported with Claude --read-only: print mode runs in the supplied --cwd")
    if args.full_access and args.read_only:
        die("--full-access and --read-only exclude each other")
    if args.full_access and args.runner == "claude":
        die("--full-access is a Codex flag: use --permission-mode for a Claude background session")
    if args.read_tool and (args.runner != "claude" or not args.read_only):
        die("--read-tool requires --runner claude --read-only")
    if args.runner == "claude" and args.read_only and args.permission_mode:
        die("--permission-mode cannot override Claude --read-only; it uses dontAsk with no permission prompts")
    if args.notify_thread and args.attach:
        die("--notify-thread requires a detached session")
    if args.wait and args.notify_thread:
        die("--notify-thread requires a detached session")
    if args.wait and args.attach:
        die("--wait is unsupported with --attach: attach opens a window for a person to watch, and --wait already blocks for an exit code")
    if args.wait and args.runner == "claude" and not args.read_only:
        die("--wait requires Claude --read-only: a background Claude session returns before it finishes and cannot be waited on")

    cwd = os.path.realpath(args.cwd) if args.cwd else os.getcwd()
    if not os.path.isdir(cwd):
        die(f"--cwd must be a directory: {cwd}")
    prompt_path = os.path.realpath(args.prompt)
    name = args.name or f"dispatch-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    log = os.path.realpath(args.log) if args.log else default_log(prompt_path, args.name)
    notify_log = strip_ext(log) + ".notify.log"

    claude_bin = resolve_bin("CLAUDE_BIN", "claude")
    codex_bin = resolve_bin("CODEX_BIN", "codex")
    if args.runner == "claude" and not claude_bin:
        die("claude executable not found on PATH (set CLAUDE_BIN)")
    if args.runner == "codex" and not codex_bin:
        die("codex executable not found on PATH (set CODEX_BIN)")
    if args.notify_thread and not codex_bin:
        die("--notify-thread requires the codex executable to queue the callback")

    ctx = Context(cwd=cwd, prompt_path=prompt_path, name=name, log=log, notify_log=notify_log, claude_bin=claude_bin, codex_bin=codex_bin)

    if args.runner == "claude":
        if args.read_only:
            return do_claude_read_only(args, ctx)
        return do_claude_writable(args, ctx)
    return do_codex(args, ctx)


if __name__ == "__main__":
    sys.exit(main())
