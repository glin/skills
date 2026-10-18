"""Unittest port of test.sh, plus coverage for the new --wait flag, the codex
--json session id capture, stdin-only prompt delivery, and decode safety on
Windows for non-ASCII child output.

Every scenario drives the real dispatch.py (and, for the direct watcher
scenarios, watch_claude.py) as a subprocess, exactly as test.sh drives
dispatch.sh, against stub claude and codex executables that shutil.which
finds on PATH: claude.cmd/codex.cmd on Windows, claude/codex on POSIX, each a
thin launcher around a Python stub so the stub logic itself stays one
cross-platform file. The stubs write their own stdout as UTF-8, matching the
real CLIs, so a decode bug in dispatch.py cannot hide behind a stub that
happens to write the locale code page instead.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DISPATCH_PY = SKILL_DIR / "dispatch.py"
WATCH_CLAUDE_PY = SKILL_DIR / "watch_claude.py"

sys.path.insert(0, str(SKILL_DIR))
import dispatch  # noqa: E402
import watch_claude  # noqa: E402
from receipt import Receipt
import threading


CODEX_STUB_SOURCE = '''
import json
import os
import sys
import time


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if not args:
        print("unexpected codex command:", file=sys.stderr)
        return 2
    if args[0] == "exec":
        rest = args[1:]
        capture_args = os.environ.get("DISPATCH_TEST_CODEX_ARGS")
        if capture_args:
            with open(capture_args, "w", encoding="utf-8") as fh:
                fh.write("\\n".join(rest) + "\\n")
        pid_file = os.environ.get("DISPATCH_TEST_CODEX_PID_FILE")
        if pid_file:
            with open(pid_file, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        result_path = None
        json_mode = False
        value_flags = {"-C", "-s", "-o", "-m", "-c", "--add-dir"}
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("-o", "--output-last-message"):
                result_path = rest[i + 1]
                i += 2
                continue
            if tok == "--json":
                json_mode = True
                i += 1
                continue
            if tok in value_flags:
                i += 2
                continue
            i += 1
        stdin_data = sys.stdin.read()
        capture = os.environ.get("DISPATCH_TEST_CODEX_STDIN")
        if capture:
            with open(capture, "w", encoding="utf-8") as fh:
                fh.write(stdin_data)
        thread_id = os.environ.get("DISPATCH_TEST_CODEX_THREAD_ID", "codex-test-thread")
        if os.environ.get("DISPATCH_TEST_CODEX_BAD_BYTE"):
            sys.stdout.buffer.write(b"\\x9d not valid utf-8 or cp1252\\n")
            sys.stdout.buffer.flush()
        if json_mode:
            print(json.dumps({"type": "thread.started", "thread_id": thread_id}))
            sys.stdout.flush()
            sleep_seconds = os.environ.get("DISPATCH_TEST_CODEX_SLEEP_SECONDS")
            if sleep_seconds:
                time.sleep(float(sleep_seconds))
            print(json.dumps({"type": "turn.started"}))
            print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "worker output"}}))
            print(json.dumps({"type": "turn.completed", "usage": {}}))
        else:
            print("worker output")
        if result_path and not os.environ.get("DISPATCH_TEST_CODEX_SKIP_RESULT"):
            if os.environ.get("DISPATCH_TEST_CODEX_RESULT_BAD_BYTE"):
                with open(result_path, "wb") as fh:
                    fh.write(b"not valid utf-8: \\x9d end\\n")
            else:
                result_text = os.environ.get("DISPATCH_TEST_CODEX_RESULT_TEXT", "durable result\\n")
                with open(result_path, "w", encoding="utf-8") as fh:
                    fh.write(result_text)
        return int(os.environ.get("DISPATCH_TEST_EXEC_STATUS", "0"))
    if args[0] == "queue":
        queue_status = int(os.environ.get("DISPATCH_TEST_QUEUE_STATUS", "0"))
        if queue_status != 0:
            print("codex queue rejected", file=sys.stderr)
            return queue_status
        rest = args[1:]
        expect = os.environ.get("DISPATCH_TEST_EXPECT_RESULT")
        if expect and not os.path.isfile(expect):
            print("callback arrived before the result file", file=sys.stderr)
            return 9
        queue_log = os.environ["DISPATCH_TEST_QUEUE_LOG"]
        with open(queue_log, "a", encoding="utf-8") as fh:
            fh.write(" ".join(rest) + "\\n")
        return 0
    print(f"unexpected codex command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''

CLAUDE_STUB_SOURCE = '''
import json
import os
import sys
import time


def _write_args(args):
    args_file = os.environ.get("DISPATCH_TEST_CLAUDE_ARGS")
    if not args_file:
        return
    with open(args_file, "w", encoding="utf-8") as fh:
        fh.write("\\n".join(args) + "\\n")
    ttl_file = os.environ.get("DISPATCH_TEST_CLAUDE_TTL")
    if ttl_file:
        with open(ttl_file, "w", encoding="utf-8") as fh:
            fh.write(os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL", "unset"))


def _capture_stdin():
    stdin_data = sys.stdin.read()
    capture = os.environ.get("DISPATCH_TEST_CLAUDE_STDIN")
    if capture:
        with open(capture, "w", encoding="utf-8") as fh:
            fh.write(stdin_data)
    return stdin_data


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args and args[0] == "agents":
        state_file = os.environ["DISPATCH_TEST_CLAUDE_STATE_FILE"]
        count = 0
        if os.path.isfile(state_file):
            content = open(state_file, encoding="utf-8").read().strip()
            count = int(content) if content else 0
        with open(state_file, "w", encoding="utf-8") as fh:
            fh.write(str(count + 1))
        sequence = os.environ.get("DISPATCH_TEST_CLAUDE_SEQUENCE", "complete")
        if sequence == "complete":
            print(json.dumps([{"id": "claude-test", "status": "busy", "state": "working"}] if count == 0 else []))
        elif sequence == "blocked":
            print(json.dumps([{"id": "claude-test", "state": "blocked"}]))
        elif sequence == "multi_agent":
            print(json.dumps([{"id": "other", "state": "failed"}, {"id": "claude-test", "state": "working"}] if count == 0 else []))
        elif sequence == "unknown":
            print(json.dumps([{"id": "claude-test", "state": "surprised"}]))
        elif sequence == "missing":
            print(json.dumps([]))
        elif sequence == "working":
            print(json.dumps([{"id": "claude-test", "state": "working"}]))
        elif sequence == "bad_byte":
            sys.stdout.buffer.write(b'[{"id":"claude-test","state":"working\\x9d"}]\\n')
            sys.stdout.buffer.flush()
        elif sequence == "registry_error":
            return 4
        else:
            return 4
        return 0
    if args and args[0] == "-p":
        _write_args(args)
        _capture_stdin()
        pid_file = os.environ.get("DISPATCH_TEST_CLAUDE_PID_FILE")
        if pid_file:
            with open(pid_file, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        sleep_seconds = os.environ.get("DISPATCH_TEST_CLAUDE_SLEEP_SECONDS")
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
        chosen_id = args[args.index("--session-id") + 1] if "--session-id" in args else "claude-test-session"
        session_id = os.environ.get("DISPATCH_TEST_CLAUDE_SESSION_ID", chosen_id)
        if os.environ.get("DISPATCH_TEST_CLAUDE_DIAGNOSTIC"):
            print("Claude diagnostic before result")
        print(json.dumps({"result": "read-only result", "session_id": session_id}))
        return int(os.environ.get("DISPATCH_TEST_CLAUDE_PRINT_STATUS", "0"))
    _write_args(args)
    _capture_stdin()
    if os.environ.get("DISPATCH_TEST_CLAUDE_BG_STATUS"):
        return int(os.environ["DISPATCH_TEST_CLAUDE_BG_STATUS"])
    background_id = os.environ.get("DISPATCH_TEST_CLAUDE_BG_ID", "claude-test")
    name = args[args.index("-n") + 1]
    if os.environ.get("DISPATCH_TEST_CLAUDE_BG_DIAGNOSTIC"):
        print("diagnostic: backgrounded \\u00b7 deadbeef \\u00b7 decoy")
    suffix = " (idle \\u2014 send a prompt to start)" if os.environ.get("DISPATCH_TEST_CLAUDE_BG_IDLE") else ""
    print(f"backgrounded \\u00b7 {background_id} \\u00b7 {name}{suffix}")
    if os.environ.get("DISPATCH_TEST_CLAUDE_BG_DUPLICATE"):
        print(f"backgrounded \\u00b7 feedface \\u00b7 {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _launcher_name(name):
    return f"{name}.cmd" if os.name == "nt" else name


def _write_launcher(bin_dir, name, stub_path):
    launcher = bin_dir / _launcher_name(name)
    if os.name == "nt":
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{stub_path}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{stub_path}" "$@"\n', encoding="utf-8")
        os.chmod(launcher, 0o755)
    return launcher


def _write_noop_launcher(bin_dir, name):
    """A stand-in for a terminal launcher such as Windows Terminal: it does nothing and
    exits 0, so a test can let open_window's process-spawn succeed without opening a
    real window."""
    launcher = bin_dir / (f"{name}.cmd" if os.name == "nt" else name)
    if os.name == "nt":
        launcher.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(launcher, 0o755)
    return launcher


def flag_values(lines, flag):
    """The tokens that follow one flag, up to the next flag, one per line."""
    values = []
    in_flag = False
    for line in lines:
        if line == flag:
            in_flag = True
            continue
        if in_flag:
            if line.startswith("--"):
                break
            values.append(line)
    return values


def wait_for_file(path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return
        time.sleep(0.05)
    raise AssertionError(f"file was not written in time: {path}")


class DispatchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dispatch-pytest-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.bin_dir = Path(self.tmpdir) / "bin"
        self.bin_dir.mkdir()
        self.work_dir = Path(self.tmpdir) / "work"
        self.work_dir.mkdir()

        codex_stub = self.bin_dir / "codex_stub.py"
        codex_stub.write_text(CODEX_STUB_SOURCE, encoding="utf-8")
        claude_stub = self.bin_dir / "claude_stub.py"
        claude_stub.write_text(CLAUDE_STUB_SOURCE, encoding="utf-8")
        self.claude_bin_path = str(_write_launcher(self.bin_dir, "claude", claude_stub))
        self.codex_bin_path = str(_write_launcher(self.bin_dir, "codex", codex_stub))

        self.prompt_path = Path(self.tmpdir) / "prompt.md"
        self.prompt_path.write_text("Finish the bounded test task.\n", encoding="utf-8")

        self.env = dict(os.environ)
        self.env["PATH"] = str(self.bin_dir) + os.pathsep + self.env.get("PATH", "")
        self.env.pop("CLAUDE_BIN", None)
        self.env.pop("CODEX_BIN", None)
        self.env["ARC_BIN"] = str(self.bin_dir / "missing-arc")
        self.env.pop("ARC_WINDOW", None)

    def _assert_terminal_launcher_resolves_to_stub(self, name, stub_path):
        """Guard: open_window resolves the platform's terminal launcher by searching
        PATH (Windows additionally forces the match through shutil.which). If that name
        ever resolved to a real launcher instead of this test's own stub, this must fail
        right here, before run_dispatch spawns dispatch.py as a subprocess, rather than
        letting a real window open on the machine running it."""
        resolved = shutil.which(name, path=self.env["PATH"])
        # os.path.normcase: Windows paths are case-insensitive, and shutil.which returns
        # the extension in PATHEXT's own case (".CMD"), which need not match the case the
        # stub file was written with.
        self.assertEqual(
            os.path.normcase(resolved or ""), os.path.normcase(str(stub_path)),
            f"{name} resolves to something other than this test's stub; refusing to run "
            "dispatch.py --attach, which would open a real terminal window",
        )

    def _stub_attach_launcher(self):
        """Stand in for open_window's real terminal launcher on the current platform, so
        an --attach test can run without ever opening a real window, on any platform.
        Windows additionally needs Git Bash to build the run file's shebang target, so
        this skips there when none is on PATH. Returns the launcher's stub path."""
        system = platform.system()
        if system == "Windows":
            if dispatch.resolve_git_bash() is None:
                self.skipTest("no Git Bash found to open a window with")
            name = "wt"
        elif system == "Darwin":
            name = "osascript"
        else:
            # A set TMUX would make open_window reuse the running session instead of
            # this stub, so this test only runs where that branch is reachable.
            name = "x-terminal-emulator"
            self.env.pop("TMUX", None)
        stub = _write_noop_launcher(self.bin_dir, name)
        self._assert_terminal_launcher_resolves_to_stub(name, stub)
        return stub

    def run_dispatch(self, args, extra_env=None, timeout=30):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, str(DISPATCH_PY), *args]
        return subprocess.run(cmd, env=env, cwd=str(self.work_dir), capture_output=True, text=True, timeout=timeout)

    def run_watch_claude(self, args, extra_env=None, timeout=30):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, str(WATCH_CLAUDE_PY), *args]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)

    def expected_notify_log(self, log_path):
        return dispatch.strip_ext(os.path.realpath(str(log_path))) + ".notify.log"


# --- ported from test.sh -----------------------------------------------------


class ArcStartTests(DispatchTestCase):
    def setUp(self):
        super().setUp()
        self.arc_calls = Path(self.tmpdir) / "arc-calls.jsonl"
        self.worker_calls = Path(self.tmpdir) / "worker-args"
        stub = self.bin_dir / "arc_stub.py"
        stub.write_text('''import json, os, sys
args = sys.argv[1:]
with open(os.environ["DISPATCH_TEST_ARC_CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
host = "a" * 24
window = {"hostId": host, "folders": [os.getcwd()], "scopeReady": True, "panelReady": True}
if os.environ.get("DISPATCH_TEST_ARC_FOLDER"): window["folders"] = [os.environ["DISPATCH_TEST_ARC_FOLDER"]]
case = os.environ.get("DISPATCH_TEST_ARC_CASE", "accepted")
if args[0] == "--help":
    legacy = os.environ.get("DISPATCH_TEST_ARC_HELP", "legacy") == "legacy"
    print("Usage: arc windows [--json]\\n       arc start --prompt-file PATH "
          + ("(--agent-run | --untagged)" if legacy else "[--agent-run]") + " [--name NAME]")
elif args[0] == "windows":
    if case == "unready": window["panelReady"] = False
    if case == "wrong-folder": window["folders"] = [os.path.dirname(os.getcwd())]
    windows = [window, {**window, "hostId": "b" * 24}] if case == "ambiguous" else [window]
    if case == "no-windows": windows = []
    print(json.dumps({"version": 1, "windows": windows, "complete": case != "incomplete"}))
else:
    if case == "stderr-only":
        print("The selected Arc window is unavailable.", file=sys.stderr)
        sys.exit(1)
    request = args[args.index("--request-id") + 1]
    if case in ("ambiguous", "incomplete", "unready", "wrong-folder"): case = "accepted"
    if case == "malformed":
        print("not JSON")
        sys.exit(1)
    result = {"version": 1, "requestId": request, "status": case, "requester": None}
    if case == "refused": result["reason"] = "The requested setup is unavailable."
    if case in ("accepted", "accepted-with-errors"):
        result.update(sessionId="12345678-1234-1234-1234-123456789abc", provider=args[args.index("--provider") + 1],
                      errors=["Name save failed"] if case == "accepted-with-errors" else [])
    if case == "missing-ack": result.pop("requestId")
    if case == "conflicting-refusal": result.update(status="refused", sessionId="already-started")
    override = json.loads(os.environ.get("DISPATCH_TEST_ARC_REPLY_OVERRIDE", "{}"))
    result.update(override.get("set", {}))
    for key in override.get("remove", []): result.pop(key, None)
    print(json.dumps(result))
    if case == "nonzero" or os.environ.get("DISPATCH_TEST_ARC_NONZERO"): sys.exit(1)
''', encoding="utf-8")
        self.env["ARC_BIN"] = str(_write_launcher(self.bin_dir, "arc", stub))
        if os.name == "nt":
            entry = self.bin_dir.parent / "cli.cjs"
            entry.write_text("const {spawnSync} = require('node:child_process');\n"
                             f"const child = spawnSync({json.dumps(sys.executable)}, "
                             f"[{json.dumps(str(stub))}, ...process.argv.slice(2)], {{stdio: 'inherit'}});\n"
                             "process.exit(child.status ?? 1);\n", encoding="utf-8")
            self.arc_entry = entry
        self.env["DISPATCH_TEST_ARC_CALLS"] = str(self.arc_calls)
        self.env["DISPATCH_TEST_CLAUDE_ARGS"] = str(self.worker_calls)
        self.env["DISPATCH_TEST_CODEX_ARGS"] = str(self.worker_calls)

    def launch(self, extra=(), case="accepted", shell=False, default_cwd=False):
        arguments = ["--runner", "claude", "--owned", "--arc-dispatch", "--name", "Inspect",
                     "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), *extra]
        if default_cwd:
            del arguments[arguments.index("--cwd"):arguments.index("--cwd") + 2]
        if not shell:
            return self.run_dispatch(arguments, {"DISPATCH_TEST_ARC_CASE": case})
        bash = dispatch.resolve_git_bash() if os.name == "nt" else shutil.which("bash")
        if not bash:
            self.skipTest("Bash unavailable for shell boundary")
        env = {**self.env, "DISPATCH_TEST_ARC_CASE": case,
               "CLAUDE_BIN": self.claude_bin_path.replace("\\", "/"),
               "CODEX_BIN": self.codex_bin_path.replace("\\", "/")}
        driver = Path(self.tmpdir) / "arc-route.sh"
        driver.write_text("exec " + " ".join(shlex.quote(value) for value in
                          [bash.replace("\\", "/"), str(SKILL_DIR / "dispatch.sh").replace("\\", "/"), *arguments]) + "\n",
                          encoding="utf-8")
        return subprocess.run([bash, str(driver).replace("\\", "/")],
                              env=env, cwd=self.work_dir, capture_output=True, text=True, timeout=30)

    def calls(self):
        return [json.loads(line) for line in self.arc_calls.read_text().splitlines()] if self.arc_calls.exists() else []

    def test_both_launchers_accept_without_starting_a_cli_worker(self):
        # A legacy Arc lists --untagged in its help. A current Arc starts the reader's session without it.
        cases = [(shell, case, help_text) for shell in (False, True)
                 for case in ("accepted", "accepted-with-errors") for help_text in ("legacy", "current")]
        for shell, case, help_text in cases:
            with self.subTest(shell=shell, case=case, help=help_text):
                self.env["DISPATCH_TEST_ARC_HELP"] = help_text
                result = self.launch(["--model", "claude-haiku-5-5", "--effort", "low"], case, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.worker_calls.exists())
                start = self.calls()[-1]
                self.assertEqual(start[0], "start")
                for flag, expected in (("--permissions", "auto"), ("--model", "claude-haiku-5-5"),
                                       ("--effort", "low"), ("--window", "a" * 24)):
                    self.assertEqual(start[start.index(flag) + 1], expected)
                if help_text == "legacy":
                    self.assertIn("--untagged", start)
                else:
                    self.assertNotIn("--untagged", start)
                self.assertNotIn("--agent-run", start)
                self.assertIn('"owned":true', result.stdout.splitlines()[-1])
                self.assertIn("mode: Arc session", result.stdout)
                if case == "accepted-with-errors": self.assertIn("Name save failed", result.stdout)

    def test_unknown_delivery_never_falls_back_on_either_launcher(self):
        for shell in (False, True):
            for case in ("unknown", "malformed", "missing-ack", "nonzero", "conflicting-refusal", "stderr-only"):
                with self.subTest(shell=shell, case=case):
                    result = self.launch(case=case, shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertFalse(self.worker_calls.exists(), "unknown delivery started a second worker")
                    self.assertIn("Do not start another worker", result.stderr)
                    self.assertIn("request:", result.stdout)
                    if case == "stderr-only":
                        self.assertIn("The selected Arc window is unavailable.", result.stderr)

    def test_explicit_refusal_falls_back_with_requested_flags(self):
        self.env["DISPATCH_TEST_ARC_NONZERO"] = "1"
        for shell in (False, True):
            for override in ({}, {"remove": ["reason"], "set": {"requester": "parent-session"}}):
                with self.subTest(shell=shell, override=override):
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(["--permission-mode", "acceptEdits", "--model", "sonnet"], "refused", shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    arguments = self.worker_calls.read_text().splitlines()
                    self.assertEqual(flag_values(arguments, "--permission-mode"), ["acceptEdits"])
                    self.assertEqual(flag_values(arguments, "--model"), ["sonnet"])

    def test_malformed_refusals_never_start_a_cli_worker_on_either_launcher(self):
        overrides = [{"set": {"sessionId": value}} for value in ([], False, 0, "", None, {}, "already-started")]
        overrides += [{"remove": [field]} for field in ("version", "requester", "requestId", "status")]
        overrides += [{"set": {field: value}} for field, values in (
            ("version", (True, 0, "1", None)),
            ("requester", (False, [], "", "x" * 257)),
            ("reason", (None, False, [], {})),
            ("status", (False, [], {})),
        ) for value in values]
        overrides.append({"set": {"provider": "claude"}})
        for shell in (False, True):
            for override in overrides:
                with self.subTest(shell=shell, override=override):
                    self.arc_calls.unlink(missing_ok=True)
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(case="refused", shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertFalse(self.worker_calls.exists(), "malformed refusal started a second worker")
                    self.assertEqual([call[0] for call in self.calls()], ["windows", "--help", "start"])
                    self.assertIn("mode: Arc delivery unknown", result.stdout)
                    self.assertIn("request:", result.stdout)
                    self.assertIn("Do not start another worker", result.stderr)

    def test_unready_ambiguous_incomplete_or_wrong_folder_refuses_before_start(self):
        for case in ("unready", "ambiguous", "incomplete", "wrong-folder"):
            with self.subTest(case=case):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(case=case)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("refused before acceptance", result.stderr)
                self.assertEqual([call[0] for call in self.calls()], ["windows"])

    def test_explicit_window_selects_one_from_ambiguous_discovery(self):
        self.env["ARC_WINDOW"] = "a" * 24
        result = self.launch(case="ambiguous")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1][0], "start")

    def test_attach_selects_reader_owned_arc_without_terminal(self):
        self._stub_attach_launcher()
        if os.name == "nt":
            (self.bin_dir / "wt").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for shell in (False, True):
            result = self.launch(["--attach"], shell=shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.worker_calls.exists())
            self.assertFalse(Path(dispatch.strip_ext(str(self.prompt_path)) + ".run.sh").exists())

    def test_read_only_owned_requests_keep_enforced_transport(self):
        result = self.launch(["--read-only", "--wait"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn("--disallowedTools", self.worker_calls.read_text())

    def test_codex_default_refuses_arc_and_full_access_maps_exactly(self):
        result = self.launch(["--runner", "codex"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex workspace-write with no approvals", result.stderr)
        self.assertEqual(self.calls(), [])
        wait_for_file(self.worker_calls)
        arguments = self.worker_calls.read_text().splitlines()
        self.assertEqual(arguments[arguments.index("-s") + 1], "workspace-write")
        self.worker_calls.unlink()
        result = self.launch(["--runner", "codex", "--full-access", "--model", "sol"])
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self.calls()[-1]
        self.assertEqual(start[start.index("--permissions") + 1], "never")
        self.assertEqual(start[start.index("--model") + 1], "gpt-6.1-sol")
        self.assertFalse(self.worker_calls.exists())

    @unittest.skipUnless(os.name == "nt", "native Windows cmd boundary")
    def test_windows_arc_arguments_remain_literal_on_both_launchers(self):
        self.env["DISPATCH_TEST_EXPANSION"] = "expanded"
        for package in (False, True):
            if package:
                entry = self.bin_dir / "node_modules/@glin/arc/dist/arc.cjs"
                entry.parent.mkdir(parents=True)
                entry.write_bytes(self.arc_entry.read_bytes())
                self.arc_entry.unlink()
            for shell in (False, True):
                for name in ("A&ver", "%DISPATCH_TEST_EXPANSION%", 'A|ver<>^!()"&ver'):
                    with self.subTest(package=package, shell=shell, name=name):
                        result = self.launch(["--name", name, "--model", "%DISPATCH_TEST_EXPANSION%"], shell=shell)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        start = self.calls()[-1]
                        self.assertEqual(start[start.index("--name") + 1], name)
                        self.assertEqual(start[start.index("--model") + 1], "%DISPATCH_TEST_EXPANSION%")
                        self.assertNotIn("Microsoft Windows", result.stdout)
                        self.assertFalse(self.worker_calls.exists())

    @unittest.skipUnless(os.name == "nt", "native Windows cmd boundary")
    def test_packaged_windows_arc_starts_owned_codex_with_literal_arguments(self):
        release = Path(self.tmpdir) / "releases" / "release with spaces"
        launcher = release / "launchers" / "arc.cmd"
        entry = release / "cli" / "arc.cjs"
        launcher.parent.mkdir(parents=True)
        entry.parent.mkdir(parents=True)
        launcher.write_text("@echo off\nexit /b 99\n", encoding="utf-8")
        entry.write_bytes(self.arc_entry.read_bytes())
        self.arc_entry.unlink()
        self.env["ARC_BIN"] = str(launcher)
        self.env["DISPATCH_TEST_EXPANSION"] = "expanded"
        name = 'Docs & %DISPATCH_TEST_EXPANSION% |<>^!()"'
        for shell in (False, True):
            with self.subTest(shell=shell):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(["--runner", "codex", "--full-access", "--model", "sol",
                                      "--effort", "high", "--name", name], shell=shell)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("mode: Arc session", result.stdout)
                self.assertFalse(self.worker_calls.exists())
                self.assertEqual([call[0] for call in self.calls()], ["windows", "--help", "start"])
                start = self.calls()[-1]
                for flag, expected in (("--name", name), ("--model", "gpt-6.1-sol"),
                                       ("--effort", "high"), ("--provider", "codex"),
                                       ("--permissions", "never"), ("--prompt-file", str(self.prompt_path))):
                    self.assertEqual(start[start.index(flag) + 1], expected)
                self.assertNotIn("--agent-run", start)
                self.assertIn('"owned":true', result.stdout.splitlines()[-1])

    @unittest.skipUnless(os.name == "nt", "native Windows cmd boundary")
    def test_unknown_windows_arc_wrapper_refuses_without_running_it(self):
        self.arc_entry.unlink()
        for shell in (False, True):
            result = self.launch(shell=shell)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cannot preserve literal arguments", result.stderr)
            self.assertEqual(self.calls(), [])
            self.assertTrue(self.worker_calls.exists())

    def test_unsupported_claude_permissions_preserve_cli_on_both_launchers(self):
        for shell in (False, True):
            for mode in ("dontAsk", "bypassPermissions"):
                with self.subTest(shell=shell, mode=mode):
                    result = self.launch(["--permission-mode", mode], shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    self.assertEqual(self.calls(), [])
                    self.assertEqual(flag_values(self.worker_calls.read_text().splitlines(), "--permission-mode"), [mode])

    def test_local_prompt_incompatibilities_refuse_before_contact_on_both_launchers(self):
        for shell in (False, True):
            for content in (b"X" * (60 * 1024), b"", b" \n /command", b"\xef\xbb\xbf /command"):
                with self.subTest(shell=shell, size=len(content)):
                    self.prompt_path.write_bytes(content)
                    result = self.launch(shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    self.assertEqual(self.calls(), [])
                    self.assertTrue(self.worker_calls.exists())
            result = self.launch(["--prompt", str(self.work_dir)], shell=shell)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("must exist", result.stderr)
            self.assertEqual(self.calls(), [])

    def test_no_windows_reports_no_available_window_on_both_launchers(self):
        for shell in (False, True):
            result = self.launch(case="no-windows", shell=shell)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("no Arc window is available", result.stderr)
            self.assertNotIn("select one Arc window", result.stderr)

    def test_default_cwd_junction_or_symlink_matches_registered_folder(self):
        folder = self.work_dir
        alias = Path(self.tmpdir) / "workspace-alias"
        if os.name == "nt":
            script = "New-Item -ItemType Junction -Path $env:DISPATCH_TEST_ALIAS -Target $env:DISPATCH_TEST_FOLDER -ErrorAction Stop | Out-Null"
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                           env={**os.environ, "DISPATCH_TEST_ALIAS": str(alias), "DISPATCH_TEST_FOLDER": str(folder)})
        else:
            alias.symlink_to(folder, target_is_directory=True)
        self.work_dir = alias
        self.env["DISPATCH_TEST_ARC_FOLDER"] = str(folder)
        try:
            for shell in (False, True):
                result = self.launch(shell=shell, default_cwd=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("mode: Arc session", result.stdout)
                self.assertFalse(self.worker_calls.exists())
        finally:
            self.work_dir = folder
            os.rmdir(alias) if os.name == "nt" else alias.unlink()


class ClaudeWritableTests(DispatchTestCase):
    def test_haiku_shorthand_and_explicit_ids_reach_claude(self):
        cases = {
            "haiku": "claude-haiku-5-5",
            "claude-haiku-5-5": "claude-haiku-5-5",
            "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
            "sonnet": "sonnet",
        }
        for read_only in (False, True):
            for model, expected in cases.items():
                with self.subTest(read_only=read_only, model=model):
                    args_file = Path(self.tmpdir) / f"args-{read_only}-{model}.log"
                    result = self.run_dispatch(
                        ["--runner", "claude", "--model", model,
                         "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
                         *(["--read-only", "--wait"] if read_only else [])],
                        {"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    wait_for_file(args_file)
                    lines = args_file.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(flag_values(lines, "--model"), [expected])
                    self.assertIn(f"model: {expected}", result.stdout)
                    mode = "dontAsk" if read_only and "4-5" in model else "auto"
                    self.assertEqual(flag_values(lines, "--permission-mode"), [mode])

    def test_default_permission_mode_is_auto(self):
        args_file = Path(self.tmpdir) / "claude-args.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(flag_values(lines, "--permission-mode"), ["auto"])
        self.assertIn("id: claude-test", result.stdout)

    def test_permission_mode_override(self):
        args_file = Path(self.tmpdir) / "claude-args.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--permission-mode", "acceptEdits"],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(flag_values(lines, "--permission-mode"), ["acceptEdits"])
        self.assertIn("id: claude-test", result.stdout)

    def test_notify_thread_reports_completion(self):
        queue_log = Path(self.tmpdir) / "claude-complete-queue.log"
        args_file = Path(self.tmpdir) / "claude-args.log"
        log_path = Path(self.tmpdir) / "claude-complete.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path), "--notify-thread", "origin-789"],
            extra_env={
                "DISPATCH_TEST_CLAUDE_ARGS": str(args_file),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "claude-state"),
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "complete",
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1",
                "DISPATCH_NOTIFY_MAX_SECONDS": "1",
                "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS": "2",
                "DISPATCH_NOTIFY_TIMEOUT_SECONDS": "5",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(queue_log)
        content = queue_log.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn("--thread origin-789 --message Fresh-session handoff dispatch-", content)
        self.assertIn("unverified (exit unknown). Result:", content)
        self.assertIn("notify-thread: origin-789", result.stdout)
        self.assertIn("monitor.log", result.stdout)
        self.assertIn("monitor-pid:", result.stdout)

    def test_notify_thread_reports_blocked_state(self):
        queue_log = Path(self.tmpdir) / "claude-blocked-queue.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(Path(self.tmpdir) / "claude-blocked.log"), "--notify-thread", "origin-790"],
            extra_env={
                "DISPATCH_TEST_CLAUDE_ARGS": str(Path(self.tmpdir) / "claude-args.log"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "claude-state"),
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "blocked",
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1",
                "DISPATCH_NOTIFY_MAX_SECONDS": "1",
                "DISPATCH_NOTIFY_BLOCKED_GRACE_SECONDS": "1",
                "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS": "2",
                "DISPATCH_NOTIFY_TIMEOUT_SECONDS": "5",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(queue_log)
        content = queue_log.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn("requires attention (Claude state blocked)", content)
        self.assertIn("notify-thread: origin-790", result.stdout)

    def test_prompt_travels_on_stdin_not_argv(self):
        """Send the prompt on stdin to both runners, never as an argument (decision:
        Windows .cmd shims route through cmd.exe, which re-parses argv)."""
        args_file = Path(self.tmpdir) / "claude-args.log"
        stdin_file = Path(self.tmpdir) / "claude-stdin.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file), "DISPATCH_TEST_CLAUDE_STDIN": str(stdin_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv_text = args_file.read_text(encoding="utf-8")
        self.assertNotIn("Finish the bounded test task", argv_text)
        self.assertEqual(stdin_file.read_text(encoding="utf-8"), self.prompt_path.read_text(encoding="utf-8"))


class WorktreeBaseTests(DispatchTestCase):
    """Claude creates a -w worktree from origin/HEAD, so a launch whose local HEAD is
    ahead of origin/HEAD must stop before any claude process starts."""

    def _git(self, *args):
        env = dict(self.env, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        out = subprocess.run(["git", "-C", str(self.work_dir), *args], env=env,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def _repo_with_origin_head(self, local_commits_ahead):
        self._git("init", "-q", "-b", "main")
        self._git("commit", "-q", "--allow-empty", "-m", "base")
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")
        self._git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        for i in range(local_commits_ahead):
            self._git("commit", "-q", "--allow-empty", "-m", f"local {i}")

    def _launch(self, args_file):
        return self.run_dispatch(
            ["--runner", "claude", "--worktree", "--name", "base-check",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )

    def test_head_ahead_of_origin_head_refuses_before_launch(self):
        self._repo_with_origin_head(local_commits_ahead=2)
        args_file = Path(self.tmpdir) / "claude-args.log"
        result = self._launch(args_file)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(args_file.exists(), "claude started despite a stale worktree base")
        self.assertIn(self._git("rev-parse", "HEAD")[:8], result.stderr)
        self.assertIn(self._git("rev-parse", "refs/remotes/origin/HEAD")[:8], result.stderr)
        self.assertIn("git worktree add -b <branch> <path> HEAD", result.stderr)

    def test_head_at_origin_head_launches_with_worktree(self):
        self._repo_with_origin_head(local_commits_ahead=0)
        args_file = Path(self.tmpdir) / "claude-args.log"
        result = self._launch(args_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(flag_values(args_file.read_text(encoding="utf-8").splitlines(), "-w"), ["base-check"])
        self.assertIn("branch: worktree-base-check", result.stdout)


class CodexNotifyTests(DispatchTestCase):
    def test_shell_callback_runs_bash_scripts_with_shared_receipts(self):
        bash = dispatch.resolve_git_bash() if os.name == 'nt' else shutil.which('bash')
        if not bash:
            self.skipTest('Bash is unavailable')
        env = dict(self.env)
        env.pop('DISPATCH_CLI_BASH', None)
        for name in ('codex', 'claude'):
            script = self.bin_dir / (name + '-script')
            script.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(Path(sys.executable).as_posix()) +
                              ' ' + shlex.quote((self.bin_dir / (name + '_stub.py')).as_posix()) + ' "$@"\n', encoding='utf-8')
            os.chmod(script, 0o755)
            env[name.upper() + '_BIN'] = str(script)
            if os.name == 'nt':
                with self.assertRaises(OSError):
                    subprocess.Popen([str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for runner, flags in [('codex', []), ('claude', ['--read-only']), ('claude', [])]:
            with self.subTest(runner=runner, flags=flags):
                queue = Path(self.tmpdir) / ('queue-' + runner + str(len(flags)))
                env.update(DISPATCH_TEST_QUEUE_LOG=str(queue), DISPATCH_NOTIFY_INITIAL_SECONDS='0',
                           DISPATCH_NOTIFY_MAX_SECONDS='0', DISPATCH_NOTIFY_TIMEOUT_SECONDS='10',
                           DISPATCH_TEST_CLAUDE_STATE_FILE=str(queue) + '.registry')
                result = subprocess.run([bash, str(SKILL_DIR / 'dispatch.sh'), '--runner', runner,
                                         *flags, '--prompt', str(self.prompt_path), '--cwd', str(self.work_dir),
                                         '--notify-thread', 'parent'], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                receipt = Receipt(next(line[9:] for line in result.stdout.splitlines() if line.startswith('receipt: ')))
                wait_for_file(queue)
                deadline = time.monotonic() + 10
                while receipt.read()['events'][-1]['delivery'] == 'submitting' and time.monotonic() < deadline:
                    time.sleep(0.02)
                data = receipt.read()
                self.assertEqual(len(queue.read_text().splitlines()), 1)
                self.assertEqual(len(data['events']), 1)
                self.assertEqual(data['events'][0]['delivery'], 'accepted')
                self.assertEqual(data['events'][0]['consumption'], 'unread')
                self.assertEqual(len(data['events'][0]['attempts']), 1)
                self.assertTrue(data['cli_bash'])
                self.assertEqual(Path(data['report']).parent, receipt.root)
                self.assertEqual(data['events'][0]['kind'], 'unverified' if not flags and runner == 'claude' else 'completed')
                revision = receipt.observe('failed', 7)
                recovery_env = dict(env)
                recovery_env.pop('DISPATCH_CLI_BASH', None)
                recovered = subprocess.run([sys.executable, str(SKILL_DIR / 'receipt.py'), str(receipt.root),
                                            'recover', '--event', revision['id']], env=recovery_env,
                                           capture_output=True, text=True, timeout=30)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(len(queue.read_text().splitlines()), 2)
                self.assertEqual(receipt.read()['events'][-1]['delivery'], 'accepted')
                self.assertEqual(len(receipt.read()['events'][-1]['attempts']), 1)

    def test_success_queues_one_completion_notice(self):
        queue_log = Path(self.tmpdir) / "queue.log"
        result_marker = Path(self.tmpdir) / "run.last.md"
        log_path = Path(self.tmpdir) / "run.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path), "--notify-thread", "origin-123"],
            extra_env={"DISPATCH_TEST_QUEUE_LOG": str(queue_log), "DISPATCH_TEST_EXPECT_RESULT": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(queue_log)
        content = queue_log.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn("--thread origin-123 --message Fresh-session handoff dispatch-", content)
        self.assertIn("completed (exit 0). Result:", content)
        self.assertIn("notify-thread: origin-123", result.stdout)
        self.assertIn("monitor.log", result.stdout)
        self.assertIn("session: codex-test-thread", result.stdout)

    def test_failure_queues_one_failure_notice(self):
        queue_log = Path(self.tmpdir) / "failure-queue.log"
        result_marker = Path(self.tmpdir) / "failure.last.md"
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(Path(self.tmpdir) / "failure.log"), "--notify-thread", "origin-456"],
            extra_env={
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_TEST_EXPECT_RESULT": "",
                "DISPATCH_TEST_EXEC_STATUS": "7",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(queue_log)
        content = queue_log.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn("failed (exit 7). Result:", content)
        self.assertIn("notify-thread: origin-456", result.stdout)


class WatchClaudeDirectTests(DispatchTestCase):
    def _assert_single_notice(self, queue_log, expected, extra_env, timeout=5):
        result = self.run_watch_claude(
            [self.claude_bin_path, self.codex_bin_path, "claude-test", "watch-test", "origin-x",
             "claude logs claude-test", "claude attach claude-test"],
            extra_env=extra_env, timeout=timeout,
        )
        wait_for_file(queue_log)
        content = Path(queue_log).read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn(expected, content)
        return result

    def test_multi_agent_registry_reports_completion(self):
        queue_log = Path(self.tmpdir) / "multi-agent-queue.log"
        result = self._assert_single_notice(
            queue_log, "unverified (exit unknown). Result:",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "multi_agent",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 0)

    def test_registry_error_after_three_failures(self):
        queue_log = Path(self.tmpdir) / "registry-error-queue.log"
        result = self._assert_single_notice(
            queue_log, "could not be monitored after three Claude registry errors",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "registry_error",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 1)

    def test_unknown_state_is_reported(self):
        queue_log = Path(self.tmpdir) / "unknown-queue.log"
        result = self._assert_single_notice(
            queue_log, "reported unknown Claude state surprised",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "unknown",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 1)

    def test_missing_agent_never_appears(self):
        queue_log = Path(self.tmpdir) / "missing-queue.log"
        result = self._assert_single_notice(
            queue_log, "never appeared in the Claude registry",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "missing",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
                "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 1)

    def test_monitor_times_out(self):
        queue_log = Path(self.tmpdir) / "timeout-queue.log"
        result = self._assert_single_notice(
            queue_log, "monitor timed out after 2 seconds",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "working",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
                "DISPATCH_NOTIFY_STARTUP_GRACE_SECONDS": "5", "DISPATCH_NOTIFY_TIMEOUT_SECONDS": "2",
            },
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)

    def test_survives_a_non_utf8_byte_in_the_registry_snapshot(self):
        """A byte such as 0x9d is undefined in the cp1252 code page Windows decodes
        subprocess text output with by default, and used to raise UnicodeDecodeError
        before the state check ever ran, losing the notice entirely."""
        queue_log = Path(self.tmpdir) / "bad-byte-queue.log"
        result = self._assert_single_notice(
            queue_log, "reported unknown Claude state",
            {
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "bad_byte",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 1)


class ArcReceiptTests(DispatchTestCase):
    def test_writable_receipt_uses_reported_prefix_and_chosen_name(self):
        name = 'Review "branch"'
        args = ["--runner", "claude", "--arc-dispatch", "--name", name,
                "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        result = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4"})
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(receipt, ['ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review \\"branch\\""}'])
        default = self.run_dispatch(args[0:2] + args[3:], {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4"})
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(default.stdout, result.stdout)
        default_receipt = [line for line in default.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(default_receipt, receipt)

    def test_owned_flag_adds_owned_key_to_writable_receipt(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--owned", "--name", "Review",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(
            receipt,
            ['ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review","owned":true}'],
        )

    def test_attach_implies_owned_for_writable_receipt(self):
        """--attach opens a window through a real terminal launcher, so this stubs
        whichever launcher this platform's open_window resolves and confirms the
        resolution before launching, so the run never opens a real window on the
        machine running it."""
        self._stub_attach_launcher()
        result = self.run_dispatch(
            ["--runner", "claude", "--attach", "--name", "Review",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(
            receipt,
            ['ARC_DISPATCH_SESSION={"agent":"claude","session_id":"a1b2c3d4","name":"Review","owned":true}'],
        )

    def test_identified_codex_receipts_and_worker_framing(self):
        session_id = "de15cafe-2ddf-4054-a993-6436c4942996"
        worker_text = 'ARC_DISPATCH_SESSION={"agent":"claude","session_id":"aaaaaaaa"}\nARC_DISPATCH_SESSION={bad\n'
        for waited in (False, True):
            for owned in (False, True):
                with self.subTest(waited=waited, owned=owned):
                    log = Path(self.tmpdir) / f"codex-{waited}-{owned}.log"
                    result = self.run_dispatch(
                        ["--runner", "codex", "--arc-dispatch", "--name", "Review", "--prompt", str(self.prompt_path),
                         "--cwd", str(self.work_dir), "--log", str(log), *( ["--wait"] if waited else []),
                         *( ["--owned"] if owned else [])],
                        {"DISPATCH_TEST_CODEX_THREAD_ID": session_id, "DISPATCH_TEST_CODEX_RESULT_TEXT": worker_text},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    lines = result.stdout.splitlines()
                    receipt = [line for line in lines if line.startswith("ARC_DISPATCH_SESSION=")]
                    self.assertEqual(len(receipt), 1, result.stdout)
                    self.assertEqual(lines[-1], receipt[0])
                    payload = json.loads(receipt[0].split("=", 1)[1])
                    expected = {"agent": "codex", "session_id": session_id, "name": "Review"}
                    if owned:
                        expected["owned"] = True
                    self.assertEqual(payload, expected)
                    self.assertIn(f"session: {session_id}", result.stdout)
                    if waited:
                        self.assertIn("worker: ARC_DISPATCH_SESSION={bad", result.stdout)
                        self.assertEqual(Path(next(line.removeprefix("result: ") for line in lines if line.startswith("result: "))).read_text(encoding="utf-8"), worker_text)

    def test_codex_failed_and_unknown_runs_frame_worker_receipts(self):
        for waited, status, session_id in [(waited, status, session_id) for waited in (False, True) for status, session_id in [(7, "de15cafe-2ddf-4054-a993-6436c4942996"), (0, ""), (0, "invalid")]]:
            result = self.run_dispatch(
                ["--runner", "codex", *(["--wait"] if waited else []), "--arc-dispatch", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
                {"DISPATCH_TEST_EXEC_STATUS": str(status), "DISPATCH_TEST_CODEX_THREAD_ID": session_id,
                 "DISPATCH_TEST_CODEX_RESULT_TEXT": "ARC_DISPATCH_SESSION={bad\n"},
            )
            self.assertEqual(result.returncode, status if waited else 0, result.stderr)
            self.assertFalse(any(line.startswith("ARC_DISPATCH_SESSION=") for line in result.stdout.splitlines()))
            if status == 7:
                self.assertIn(f"session: {session_id}", result.stdout)
            if waited:
                self.assertIn("worker: ARC_DISPATCH_SESSION={bad", result.stdout)

    def test_codex_attach_prints_no_receipt(self):
        """The attended path captures no thread ID."""
        self._stub_attach_launcher()
        result = self.run_dispatch(
            ["--runner", "codex", "--attach", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("ARC_DISPATCH_SESSION=", result.stdout)

    def test_waited_read_only_receipt_requires_successful_json_uuid(self):
        session_id = "de15cafe-2ddf-4054-a993-6436c4942996"
        args = ["--runner", "claude", "--read-only", "--wait", "--arc-dispatch", "--name", "Review",
                "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        result = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_SESSION_ID": session_id})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'ARC_DISPATCH_SESSION={{"agent":"claude","session_id":"{session_id}","name":"Review"}}', result.stdout)
        failed = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_SESSION_ID": session_id, "DISPATCH_TEST_CLAUDE_PRINT_STATUS": "7"})
        self.assertEqual(failed.returncode, 7)
        self.assertNotIn("ARC_DISPATCH_SESSION=", failed.stdout)
        unknown = self.run_dispatch(args)
        self.assertEqual(unknown.returncode, 0)
        self.assertNotIn("ARC_DISPATCH_SESSION=", unknown.stdout)

    def test_owned_flag_adds_owned_key_to_waited_read_only_receipt(self):
        session_id = "de15cafe-2ddf-4054-a993-6436c4942996"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--wait", "--owned", "--name", "Review",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            {"DISPATCH_TEST_CLAUDE_SESSION_ID": session_id},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f'ARC_DISPATCH_SESSION={{"agent":"claude","session_id":"{session_id}","name":"Review","owned":true}}',
            result.stdout,
        )

    def test_waited_receipt_follows_diagnostic_before_json(self):
        session_id = "de15cafe-2ddf-4054-a993-6436c4942996"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--wait", "--arc-dispatch", "--name", "Review",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            {"DISPATCH_TEST_CLAUDE_SESSION_ID": session_id, "DISPATCH_TEST_CLAUDE_DIAGNOSTIC": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'ARC_DISPATCH_SESSION={{"agent":"claude","session_id":"{session_id}","name":"Review"}}', result.stdout)

    def test_receipt_keeps_unicode_name_with_limited_stdout_encoding(self):
        name = "Review 😀"
        result = self.run_dispatch(
            ["--runner", "claude", "--arc-dispatch", "--name", name,
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4", "PYTHONIOENCODING": "cp1252:replace"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = next(line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION="))
        self.assertTrue(receipt.isascii())
        self.assertEqual(json.loads(receipt.split("=", 1)[1])["name"], name)

    def test_writable_receipt_ignores_decoy_and_keeps_ambiguous_launches_alive(self):
        args = ["--runner", "claude", "--arc-dispatch", "--name", "Review",
                "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        decoy = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4", "DISPATCH_TEST_CLAUDE_BG_DIAGNOSTIC": "1"})
        self.assertEqual(decoy.returncode, 0, decoy.stderr)
        self.assertIn('"session_id":"a1b2c3d4"', decoy.stdout)
        self.assertNotIn('"session_id":"deadbeef"', decoy.stdout)
        idle = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4", "DISPATCH_TEST_CLAUDE_BG_IDLE": "1"})
        self.assertEqual(idle.returncode, 0, idle.stderr)
        self.assertIn('"session_id":"a1b2c3d4"', idle.stdout)
        # Claude already started the session with exit 0, so ambiguous launch-line output
        # must not abort the launch: it costs only the receipt, with a warning on stderr,
        # and the record (including an id, from the loose fallback) still prints.
        ambiguous = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_BG_ID": "a1b2c3d4", "DISPATCH_TEST_CLAUDE_BG_DUPLICATE": "1"})
        self.assertEqual(ambiguous.returncode, 0, ambiguous.stderr)
        self.assertNotIn("ARC_DISPATCH_SESSION=", ambiguous.stdout)
        self.assertIn("ambiguous Claude background launch output", ambiguous.stderr)
        self.assertIn("mode: writable background", ambiguous.stdout)

    def test_detached_read_only_receipt_uses_preselected_uuid(self):
        args_file = Path(self.tmpdir) / "claude-arc-args.log"
        log_path = Path(self.tmpdir) / "claude-arc.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--arc-dispatch", "--name", "Audit",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), "--log", str(log_path)],
            {"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(len(receipt), 1, result.stdout)
        payload = json.loads(receipt[0].split("=", 1)[1])
        self.assertEqual(payload["agent"], "claude")
        self.assertEqual(payload["name"], "Audit")
        self.assertRegex(payload["session_id"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertEqual(set(payload.keys()), {"agent", "session_id", "name"})
        wait_for_file(log_path)
        self.assertEqual(json.loads(log_path.read_text(encoding="utf-8"))["session_id"], payload["session_id"])
        self.assertEqual(flag_values(args_file.read_text(encoding="utf-8").splitlines(), "--session-id"), [payload["session_id"]])

    def test_owned_flag_adds_owned_key_to_detached_read_only_receipt(self):
        args_file = Path(self.tmpdir) / "claude-arc-owned-args.log"
        log_path = Path(self.tmpdir) / "claude-arc-owned.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--owned", "--name", "Audit",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), "--log", str(log_path)],
            {"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(len(receipt), 1, result.stdout)
        payload = json.loads(receipt[0].split("=", 1)[1])
        self.assertIs(payload["owned"], True)
        self.assertEqual(set(payload.keys()), {"agent", "session_id", "name", "owned"})

    def test_detached_read_only_default_receipt_matches_session_id_and_log(self):
        args_file = Path(self.tmpdir) / "claude-default-args.log"
        log_path = Path(self.tmpdir) / "claude-default.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--name", "Audit",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), "--log", str(log_path)],
            {"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(len(receipt), 1, result.stdout)
        payload = json.loads(receipt[0].split("=", 1)[1])
        self.assertEqual(payload["agent"], "claude")
        self.assertEqual(payload["name"], "Audit")
        wait_for_file(log_path)
        self.assertEqual(json.loads(log_path.read_text(encoding="utf-8"))["session_id"], payload["session_id"])
        self.assertEqual(flag_values(args_file.read_text(encoding="utf-8").splitlines(), "--session-id"), [payload["session_id"]])

    def test_waited_read_only_default_receipt_matches_json_session_id(self):
        session_id = "de15cafe-2ddf-4054-a993-6436c4942996"
        args = ["--runner", "claude", "--read-only", "--wait", "--name", "Review",
                "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        result = self.run_dispatch(args, {"DISPATCH_TEST_CLAUDE_SESSION_ID": session_id})
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(receipt, [f'ARC_DISPATCH_SESSION={{"agent":"claude","session_id":"{session_id}","name":"Review"}}'])

    def test_detached_read_only_prints_receipt_while_worker_still_running(self):
        """A worker still running when the bounded poll times out is trusted to finish and
        link successfully, the path every real detached read-only launch takes, since a real
        claude run outlives dispatch.py's short poll."""
        args_file = Path(self.tmpdir) / "claude-slow-args.log"
        log_path = Path(self.tmpdir) / "claude-slow.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--arc-dispatch", "--name", "SlowAudit",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), "--log", str(log_path)],
            {"DISPATCH_TEST_CLAUDE_ARGS": str(args_file), "DISPATCH_TEST_CLAUDE_SLEEP_SECONDS": "1.5"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = [line for line in result.stdout.splitlines() if line.startswith("ARC_DISPATCH_SESSION=")]
        self.assertEqual(len(receipt), 1, result.stdout)
        payload = json.loads(receipt[0].split("=", 1)[1])
        self.assertEqual(payload["agent"], "claude")
        self.assertRegex(payload["session_id"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        wait_for_file(args_file)
        self.assertEqual(flag_values(args_file.read_text(encoding="utf-8").splitlines(), "--session-id"), [payload["session_id"]])

    def test_detached_read_only_worker_that_dies_shortly_after_start_prints_no_receipt(self):
        """The bounded poll must catch a worker that dies after the first tick but before
        the poll gives up, not only an immediate failure (decision: dispatch.py's poll
        window matches dispatch.sh's status-file wait so both launchers agree)."""
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--arc-dispatch", "--name", "FlakyAudit",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(Path(self.tmpdir) / "claude-flaky.log")],
            {"DISPATCH_TEST_CLAUDE_SLEEP_SECONDS": "0.2", "DISPATCH_TEST_CLAUDE_PRINT_STATUS": "7"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ARC_DISPATCH_SESSION=", result.stdout)

    def test_failed_or_unidentified_launch_emits_no_receipt(self):
        # Every case here now holds with or without --arc-dispatch, since the receipt
        # prints by default. The Codex stub supplies an invalid thread identifier.
        common = ["--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        writable = self.run_dispatch(["--runner", "claude", *common],
                                     {"DISPATCH_TEST_CLAUDE_BG_STATUS": "7"})
        self.assertNotEqual(writable.returncode, 0)
        self.assertNotIn("ARC_DISPATCH_SESSION=", writable.stdout)
        unidentified = self.run_dispatch(["--runner", "claude", *common])
        self.assertEqual(unidentified.returncode, 0)
        self.assertNotIn("ARC_DISPATCH_SESSION=", unidentified.stdout)
        codex = self.run_dispatch(["--runner", "codex", "--wait", "--arc-dispatch", *common])
        self.assertEqual(codex.returncode, 0, codex.stderr)
        self.assertNotIn("ARC_DISPATCH_SESSION=", codex.stdout)
        detached = self.run_dispatch(
            ["--runner", "claude", "--read-only", *common,
             "--log", str(Path(self.tmpdir) / "failed-detached.log")],
            {"DISPATCH_TEST_CLAUDE_PRINT_STATUS": "7"},
        )
        self.assertNotIn("ARC_DISPATCH_SESSION=", detached.stdout)
        mismatched = self.run_dispatch(
            ["--runner", "claude", "--read-only", *common,
             "--log", str(Path(self.tmpdir) / "mismatched-detached.log")],
            {"DISPATCH_TEST_CLAUDE_SESSION_ID": "6d9e9b93-c4ae-4dac-a8c8-c01dc4f3041b"},
        )
        self.assertNotIn("ARC_DISPATCH_SESSION=", mismatched.stdout)


class ClaudeReadOnlyTests(DispatchTestCase):
    def test_read_tools_are_allowed_and_settings_sources_is_dropped(self):
        args_file = Path(self.tmpdir) / "claude-args.log"
        queue_log = Path(self.tmpdir) / "read-only-queue.log"
        result_path = Path(self.tmpdir) / "claude-read-only.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--read-tool", "mcp__tickets__get_ticket",
             "--read-tool", "mcp__tickets__list_comments", "--prompt", str(self.prompt_path),
             "--cwd", str(self.work_dir), "--log", str(result_path), "--notify-thread", "origin-791"],
            extra_env={
                "DISPATCH_TEST_CLAUDE_ARGS": str(args_file),
                "DISPATCH_TEST_QUEUE_LOG": str(queue_log),
                "DISPATCH_TEST_EXPECT_RESULT": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(queue_log)
        content = queue_log.read_text(encoding="utf-8")
        self.assertIn("completed (exit 0)", content)
        self.assertIn("notify-thread: origin-791", result.stdout)

        lines = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(flag_values(lines, "--permission-mode"), ["dontAsk"])
        self.assertEqual(flag_values(lines, "--permission-prompts"), ["none"])
        allowed = flag_values(lines, "--allowedTools")
        for tool in ("Read", "Grep", "Glob", "mcp__tickets__get_ticket", "mcp__tickets__list_comments"):
            self.assertIn(tool, allowed)
        denied = flag_values(lines, "--disallowedTools")
        for tool in ("Bash", "PowerShell", "Agent", "Edit", "Write"):
            self.assertIn(tool, denied)
            self.assertNotIn(tool, allowed)
        self.assertNotIn("--setting-sources", lines)

    def test_plain_read_only_keeps_bash_and_loads_no_settings(self):
        args_file = Path(self.tmpdir) / "claude-args-plain.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(Path(self.tmpdir) / "claude-read-only-plain.log")],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(args_file)
        lines = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(flag_values(lines, "--setting-sources"), [""])
        self.assertIn("--strict-mcp-config", lines)
        self.assertEqual(flag_values(lines, "--permission-mode"), ["auto"])
        self.assertEqual(flag_values(lines, "--permission-prompts"), ["none"])
        denied = flag_values(lines, "--disallowedTools")
        self.assertNotIn("Bash", denied)
        self.assertNotIn("Bash", flag_values(lines, "--allowedTools"))
        for tool in ("PowerShell", "Edit", "Write", "NotebookEdit", "MultiEdit"):
            self.assertIn(tool, denied)
        # A bare git stash stashes a dirty tree, and git fetch, git branch NAME and
        # git show --output write refs or files that auto mode could approve.
        for rule in ("Bash(git commit *)", "Bash(git -C * push *)", "Bash(git -C * stash)",
                     "Bash(git stash)", "Bash(git fetch)", "Bash(git fetch *)",
                     "Bash(git -C * fetch *)", "Bash(git branch *)", "Bash(git tag *)",
                     "Bash(git config *)", "Bash(git add *)", "Bash(git update-ref *)",
                     "Bash(git worktree remove *)", "Bash(git * --output*)"):
            self.assertIn(rule, denied)
        # Bare, these list branches and worktrees, so no rule names them.
        for rule in ("Bash(git branch)", "Bash(git worktree list *)"):
            self.assertNotIn(rule, denied)

    def test_a_model_without_auto_mode_keeps_its_read_only_bash(self):
        """Claude Code starts Haiku 4.5 in manual mode whatever --permission-mode says. The
        launch then runs it in dontAsk, with the same settings and denials, and says why its
        chained reads will be denied, so a reviewer's Bash never narrows without a word."""
        args_file = Path(self.tmpdir) / "claude-args-haiku.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--model", "claude-haiku-4-5-20251001",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(Path(self.tmpdir) / "claude-read-only-haiku.log")],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("claude-haiku-4-5-20251001 has no auto mode", result.stderr)
        wait_for_file(args_file)
        lines = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(flag_values(lines, "--permission-mode"), ["dontAsk"])
        self.assertEqual(flag_values(lines, "--setting-sources"), [""])
        denied = flag_values(lines, "--disallowedTools")
        self.assertNotIn("Bash", denied)
        for rule in ("Edit", "Write", "PowerShell", "Bash(git stash)", "Bash(git fetch *)"):
            self.assertIn(rule, denied)

    def test_only_models_older_than_opus_4_6_lack_auto_mode(self):
        cases = {
            None: True, "sonnet": True, "opus": True, "fable": True, "claude-opus-5-5": True,
            "claude-sonnet-5-5": True, "haiku": True, "claude-haiku-5-5": True,
            "claude-haiku-5-5[1m]": True,
            "us.anthropic.claude-haiku-5-5-v1:0": True,
            "claude-opus-4-6": True, "claude-sonnet-4-6[1m]": True, "claude-opus-4-8": True,
            "claude-haiku-4-5-20251001": False, "claude-sonnet-4-5": False,
            "claude-sonnet-4-20250514": False, "claude-opus-4-1-20250805": False,
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0": False,
            "claude-3-7-sonnet-latest": False,
        }
        for model, expected in cases.items():
            self.assertEqual(dispatch.claude_model_has_auto_mode(model), expected, model)

    def test_both_claude_launches_use_a_five_minute_cache(self):
        launches = {
            "read-only": ["--read-only", "--log", str(Path(self.tmpdir) / "claude-ttl.log")],
            "writable": [],
        }
        for label, extra_args in launches.items():
            for caller_value, expected in ((None, "5m"), ("1h", "1h")):
                with self.subTest(launch=label, caller=caller_value):
                    ttl_file = Path(self.tmpdir) / f"ttl-{label}-{caller_value}.txt"
                    extra_env = {
                        "DISPATCH_TEST_CLAUDE_ARGS": str(Path(self.tmpdir) / "claude-args-ttl.log"),
                        "DISPATCH_TEST_CLAUDE_TTL": str(ttl_file),
                    }
                    self.env.pop("CLAUDE_CODE_PROMPT_CACHE_TTL", None)
                    if caller_value:
                        extra_env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = caller_value
                    result = self.run_dispatch(
                        ["--runner", "claude", *extra_args, "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
                        extra_env=extra_env,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    wait_for_file(ttl_file)
                    self.assertEqual(ttl_file.read_text(encoding="utf-8"), expected)

    def test_permission_mode_cannot_override_read_only(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--permission-mode", "auto",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot override Claude --read-only", result.stderr)

    def test_read_tool_rejects_non_mcp_name(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--read-tool", "Bash",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--read-tool must name one exact MCP tool", result.stderr)

    def test_read_tool_misplaced_is_rejected(self):
        for runner_args in (["--runner", "codex", "--read-only"], ["--runner", "claude"]):
            with self.subTest(runner_args=runner_args):
                result = self.run_dispatch(
                    [*runner_args, "--read-tool", "mcp__tickets__get_ticket",
                     "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--read-tool requires --runner claude --read-only", result.stderr)


# --- new in the Python port ---------------------------------------------------


class WaitFlagTests(DispatchTestCase):
    def test_codex_wait_blocks_and_returns_worker_exit_code(self):
        log_path = Path(self.tmpdir) / "wait.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--read-only", "--wait", "--prompt", str(self.prompt_path),
             "--cwd", str(self.work_dir), "--log", str(log_path)],
            extra_env={"DISPATCH_TEST_EXEC_STATUS": "5", "DISPATCH_TEST_CODEX_THREAD_ID": "wait-thread-42"},
        )
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn("mode: read-only waited", result.stdout)
        self.assertIn("session: wait-thread-42", result.stdout)
        self.assertIn(f"result: {dispatch.strip_ext(str(log_path))}.last.md", result.stdout)

    def test_codex_wait_zero_exit(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_THREAD_ID": "wait-thread-ok"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("session: wait-thread-ok", result.stdout)

    def test_named_launches_sharing_one_prompt_keep_separate_results(self):
        def result_path(runner, name):
            args = ["--runner", runner, "--read-only", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
            if name:
                args += ["--name", name]
            result = self.run_dispatch(args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return next(line for line in result.stdout.splitlines() if line.startswith("result: "))[len("result: "):]

        stem = dispatch.strip_ext(os.path.realpath(str(self.prompt_path)))
        for runner, suffix in (("claude", ".log"), ("codex", ".last.md")):
            with self.subTest(runner=runner):
                first = result_path(runner, "review-opus5")
                second = result_path(runner, "review-opus55")
                self.assertNotEqual(first, second)
                self.assertEqual(first, stem + ".review-opus5" + suffix)
                self.assertEqual(result_path(runner, 'Review "branch" 😀'), stem + ".Review-branch" + suffix)
                self.assertEqual(result_path(runner, ""), stem + suffix)

    def test_wait_rejected_with_writable_claude(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--wait requires Claude --read-only", result.stderr)

    def test_wait_rejected_with_notify_thread(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--notify-thread", "origin-1",
             "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--notify-thread requires a detached session", result.stderr)

    def test_wait_rejected_with_attach(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--attach", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--wait is unsupported with --attach", result.stderr)


class CodexSessionIdTests(DispatchTestCase):
    def test_detached_codex_record_carries_session_id(self):
        """codex exec --json's thread.started id is captured even without --wait, so
        the run can be priced (decision, not scoped to --wait). The stub worker sleeps
        well past this assertion's deadline after emitting thread.started, so a regression
        that makes the detached path block for the worker's exit, rather than only for its
        thread id, fails here on wall-clock time instead of passing unnoticed."""
        start = time.monotonic()
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_THREAD_ID": "detached-thread-99", "DISPATCH_TEST_CODEX_SLEEP_SECONDS": "6"},
            timeout=30,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session: detached-thread-99", result.stdout)
        self.assertLess(elapsed, 4.0, "dispatch.py returned only after the worker's sleep, so it waited synchronously")

    def codex_home(self, provider):
        """Write a Codex config that selects one provider at the top level.

        The provider section repeats the key, so a reader that scans the whole file
        resolves amazon-bedrock whatever the top-level value says.
        """
        home = Path(self.tmpdir) / f"codex-home-{provider}"
        home.mkdir(exist_ok=True)
        (home / "config.toml").write_text(
            f'model_provider = "{provider}"\n'
            'model = "gpt-5.6-terra"\n'
            "\n"
            "[model_providers.amazon-bedrock]\n"
            'model_provider = "amazon-bedrock"\n',
            encoding="utf-8")
        return str(home)

    def dash_m_for(self, provider, model):
        args_file = Path(self.tmpdir) / f"codex-model-{provider}.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--model", model, "--prompt", str(self.prompt_path),
             "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_ARGS": str(args_file),
                       "CODEX_HOME": self.codex_home(provider)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return flag_values(args_file.read_text(encoding="utf-8").splitlines(), "-m")

    def test_codex_model_shorthand_reaches_dash_m(self):
        """A regression here is a shorthand such as sol reaching codex as a literal model
        name instead of the resolved id, which codex would reject with a 400."""
        self.assertEqual(self.dash_m_for("openai", "sol"), ["gpt-6.1-sol"])
        self.assertEqual(self.dash_m_for("openai", "luna"), ["gpt-6-luna"])

    def test_codex_gpt6_shorthands_reach_dash_m(self):
        self.assertEqual(self.dash_m_for("openai", "sol6"), ["gpt-6-sol"])
        self.assertEqual(self.dash_m_for("openai", "luna6"), ["gpt-6-luna"])

    def test_codex_model_takes_the_openai_prefix_on_bedrock(self):
        """Bedrock names the model openai.gpt-5.6-sol and rejects the bare id with a 400.
        The bare id stays correct against the OpenAI endpoint, which the test above holds,
        so the prefix has to come from the configured provider rather than the table."""
        self.assertEqual(self.dash_m_for("amazon-bedrock", "sol"), ["openai.gpt-6.1-sol"])
        self.assertEqual(self.dash_m_for("amazon-bedrock", "sol6"), ["openai.gpt-6-sol"])
        self.assertEqual(self.dash_m_for("amazon-bedrock", "luna"), ["openai.gpt-6-luna"])

    def test_codex_gpt6_ids_take_the_openai_prefix_on_bedrock(self):
        self.assertEqual(self.dash_m_for("amazon-bedrock", "gpt-6-sol"), ["openai.gpt-6-sol"])
        self.assertEqual(self.dash_m_for("amazon-bedrock", "gpt-6-luna"), ["openai.gpt-6-luna"])

    def test_codex_full_model_id_takes_the_prefix_too(self):
        """A caller who writes the resolved id instead of the shorthand hits the same 400,
        so the prefix cannot be applied inside the shorthand lookup alone."""
        self.assertEqual(self.dash_m_for("amazon-bedrock", "gpt-5.6-sol"), ["openai.gpt-5.6-sol"])

    def test_codex_explicit_sol_ids_keep_their_version(self):
        for provider in ("openai", "amazon-bedrock"):
            for model in ("gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "openai.gpt-6-sol"):
                with self.subTest(provider=provider, model=model):
                    expected = "openai." + model if provider == "amazon-bedrock" and model.startswith("gpt-") else model
                    self.assertEqual(self.dash_m_for(provider, model), [expected])


class PromptDeliveryTests(DispatchTestCase):
    def test_codex_prompt_travels_on_stdin_not_argv(self):
        """The codex stub silently skips a stray positional argument, so a regression
        that put the prompt back in argv would otherwise pass unnoticed."""
        args_file = Path(self.tmpdir) / "codex-args.log"
        stdin_file = Path(self.tmpdir) / "codex-stdin.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_ARGS": str(args_file), "DISPATCH_TEST_CODEX_STDIN": str(stdin_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv_text = args_file.read_text(encoding="utf-8")
        self.assertNotIn("Finish the bounded test task", argv_text)
        self.assertEqual(stdin_file.read_text(encoding="utf-8"), self.prompt_path.read_text(encoding="utf-8"))

    def test_read_only_claude_prompt_travels_on_stdin_not_argv(self):
        args_file = Path(self.tmpdir) / "claude-ro-args.log"
        stdin_file = Path(self.tmpdir) / "claude-ro-stdin.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--wait", "--prompt", str(self.prompt_path),
             "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file), "DISPATCH_TEST_CLAUDE_STDIN": str(stdin_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv_text = args_file.read_text(encoding="utf-8")
        self.assertNotIn("Finish the bounded test task", argv_text)
        self.assertEqual(stdin_file.read_text(encoding="utf-8"), self.prompt_path.read_text(encoding="utf-8"))


class DecodeSafetyTests(DispatchTestCase):
    def test_codex_wait_survives_a_non_utf8_byte_in_output(self):
        """0x9d is undefined in cp1252, the code page Windows uses by default to decode
        subprocess text output, and used to raise UnicodeDecodeError partway through the
        --json stream, losing the log, the notify callback, and the --wait exit code."""
        log_path = Path(self.tmpdir) / "bad-byte.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path)],
            extra_env={"DISPATCH_TEST_CODEX_BAD_BYTE": "1", "DISPATCH_TEST_CODEX_THREAD_ID": "bad-byte-thread"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("session: bad-byte-thread", result.stdout)

    def test_codex_wait_result_survives_a_non_utf8_byte(self):
        """The result file a coordinator reads back with --wait is not guaranteed to be
        valid UTF-8. _print_wait_result must still print it, and the process must still
        exit with the worker's own exit code, rather than raising UnicodeDecodeError."""
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_RESULT_BAD_BYTE": "1", "DISPATCH_TEST_EXEC_STATUS": "7"},
        )
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("--- result", result.stdout)

    def test_die_reports_a_console_incompatible_cwd_without_raw_escape_codes(self):
        """CPython always defaults sys.stderr's error handler to backslashreplace, so
        die() never actually raises UnicodeEncodeError the way an unreconfigured
        stdout does. Left unreconfigured, though, a character cp1252 cannot encode
        shows up in the message as a raw backslash-u escape instead of the path
        itself. Matching stdout's own reconfigure gives stderr the same
        errors="replace" substitution, so the --cwd path reads with plain
        replacement characters instead of escape noise, and the exit code stays 2
        either way."""
        bad_cwd = str(Path(self.tmpdir) / "missing-日本")
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", str(self.prompt_path), "--cwd", bad_cwd],
            extra_env={"PYTHONIOENCODING": "cp1252"},
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--cwd must be a directory", result.stderr)
        self.assertNotIn("\\u65e5", result.stderr)


class WorkerPidTests(DispatchTestCase):
    def test_detached_codex_pid_is_the_worker_not_the_supervisor(self):
        """--internal-supervise's own process used to be the pid reported, so killing it
        left the actual codex worker running."""
        pid_file = Path(self.tmpdir) / "codex-worker.pid"
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_PID_FILE": str(pid_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(pid_file)
        worker_pid = int(pid_file.read_text(encoding="utf-8").strip())
        printed_pid = None
        for line in result.stdout.splitlines():
            if line.startswith("pid:"):
                printed_pid = int(line.split(":", 1)[1].strip())
        self.assertIsNotNone(printed_pid, result.stdout)
        self.assertEqual(printed_pid, worker_pid)


class DetachedPrintModePidTests(DispatchTestCase):
    def test_detached_claude_print_mode_pid_is_the_worker_not_the_wrapper(self):
        """A detached read-only Claude launch with no --notify-thread and no --wait used to
        print the wrapper's own pid unconditionally: the pid of the intermediate cmd.exe
        wrapper claude.cmd runs under on Windows, not the worker underneath it."""
        pid_file = Path(self.tmpdir) / "claude-print-worker.pid"
        args_file = Path(self.tmpdir) / "claude-print-args.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={
                "DISPATCH_TEST_CLAUDE_PID_FILE": str(pid_file), "DISPATCH_TEST_CLAUDE_ARGS": str(args_file),
                # A real claude invocation runs for seconds to minutes; the stub otherwise
                # exits within milliseconds, before dispatch.py's own poll loop ever takes
                # a first look, so this keeps the worker alive long enough to be observed.
                "DISPATCH_TEST_CLAUDE_SLEEP_SECONDS": "1.5",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait_for_file(pid_file)
        worker_pid = int(pid_file.read_text(encoding="utf-8").strip())
        printed_pid = None
        for line in result.stdout.splitlines():
            if line.startswith("pid:"):
                printed_pid = int(line.split(":", 1)[1].strip())
        self.assertIsNotNone(printed_pid, result.stdout)
        self.assertEqual(printed_pid, worker_pid)


class WatchCommandQuotingTests(DispatchTestCase):
    def test_watch_command_quotes_a_log_path_with_a_space(self):
        log_dir = Path(self.tmpdir) / "log dir"
        log_dir.mkdir()
        log_path = log_dir / "run.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path)],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved_log = os.path.realpath(str(log_path))
        self.assertNotIn(f"watch: tail -f {resolved_log}\n", result.stdout)
        self.assertIn(f"watch: tail -f {shlex.quote(resolved_log)}", result.stdout)


class GitBashResolutionTests(unittest.TestCase):
    def setUp(self):
        real = dispatch.resolve_git_bash()
        if real is None:
            self.skipTest("no git on PATH to resolve Git Bash from")
        self.real_bash = real

    def test_ignores_a_decoy_bash_earlier_on_path(self):
        """A bare `bash` lookup would find this decoy first, the way it would find WSL's
        bash.exe in System32 on a machine where WSL is installed."""
        with tempfile.TemporaryDirectory(prefix="decoy-bash-") as decoy_dir:
            decoy_bash = Path(decoy_dir) / ("bash.exe" if os.name == "nt" else "bash")
            decoy_bash.write_text("", encoding="utf-8")
            if os.name != "nt":
                os.chmod(decoy_bash, 0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = decoy_dir + os.pathsep + old_path
            try:
                result = dispatch.resolve_git_bash()
            finally:
                os.environ["PATH"] = old_path
        self.assertEqual(result, self.real_bash)
        self.assertNotEqual(result, str(decoy_bash))


class UnitTests(unittest.TestCase):
    def test_find_agent_matches_by_id(self):
        snapshot = json.dumps([{"id": "a", "state": "working"}, {"id": "b", "state": "idle"}])
        self.assertEqual(watch_claude.find_agent(snapshot, "b"), {"id": "b", "state": "idle"})
        self.assertIsNone(watch_claude.find_agent(snapshot, "missing"))
        self.assertIsNone(watch_claude.find_agent("not json", "a"))

    def test_strip_ext_removes_one_extension(self):
        self.assertEqual(dispatch.strip_ext("/tmp/prompt.md"), "/tmp/prompt")
        self.assertEqual(dispatch.strip_ext("/tmp/prompt.run.sh"), "/tmp/prompt.run")

    def test_read_tool_regex(self):
        self.assertTrue(dispatch.READ_TOOL_RE.match("mcp__tickets__get_ticket"))
        self.assertFalse(dispatch.READ_TOOL_RE.match("Bash"))

    def test_read_tool_regex_rejects_a_trailing_newline(self):
        """cmd.exe can leave a trailing newline on an argument after re-parsing a
        claude.cmd command line, and $ (unlike \\Z) accepts one before end of string."""
        self.assertFalse(dispatch.READ_TOOL_RE.match("mcp__tickets__get_ticket\n"))

    def test_env_seconds_empty_value_uses_default(self):
        """Bash's ${VAR:-default} takes the default for an empty value, not only an
        unset one."""
        self.assertEqual(watch_claude._env_seconds({"X": ""}, "X", 7), 7)
        self.assertEqual(watch_claude._env_seconds({}, "X", 7), 7)

    def test_env_seconds_invalid_value_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            watch_claude._env_seconds({"X": "not-a-number"}, "X", 7)
        self.assertEqual(ctx.exception.code, 2)

    def test_claude_bin_and_codex_bin_env_overrides_win_over_path(self):
        """CLAUDE_BIN and CODEX_BIN, when set, name the exact executable resolve_bin must
        return, ahead of whatever bare "claude" or "codex" PATH search would otherwise find."""
        with tempfile.TemporaryDirectory(prefix="bin-override-") as override_dir:
            ext = ".exe" if os.name == "nt" else ""
            claude_override = Path(override_dir) / f"claude-override{ext}"
            codex_override = Path(override_dir) / f"codex-override{ext}"
            claude_override.write_text("", encoding="utf-8")
            codex_override.write_text("", encoding="utf-8")
            if os.name != "nt":
                os.chmod(claude_override, 0o755)
                os.chmod(codex_override, 0o755)
            old_path = os.environ.get("PATH", "")
            old_env = {name: os.environ.get(name) for name in ("CLAUDE_BIN", "CODEX_BIN")}
            # PATH holds only the override files themselves, under different names, so a
            # regression that ignored the env var and fell back to a bare "claude" or
            # "codex" PATH search would resolve to nothing instead of by accident matching.
            os.environ["PATH"] = override_dir
            os.environ["CLAUDE_BIN"] = str(claude_override)
            os.environ["CODEX_BIN"] = str(codex_override)
            try:
                resolved_claude = dispatch.resolve_bin("CLAUDE_BIN", "claude")
                resolved_codex = dispatch.resolve_bin("CODEX_BIN", "codex")
            finally:
                os.environ["PATH"] = old_path
                for name, value in old_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        self.assertEqual(os.path.normcase(resolved_claude or ""), os.path.normcase(str(claude_override)))
        self.assertEqual(os.path.normcase(resolved_codex or ""), os.path.normcase(str(codex_override)))


class RemoveCommandTests(unittest.TestCase):
    def test_a_worktree_session_runs_the_removal_script_before_claude_rm(self):
        home = os.path.join("repo root", "main")
        tree = os.path.join(home, ".claude", "worktrees", "task")
        command = dispatch.remove_command("abc123", home, "task")
        script, _, rest = command.partition(" && ")
        self.assertEqual(shlex.split(script), [
            "python3", dispatch.REMOVE_WORKTREE, "--repository", home, tree,
        ])
        self.assertTrue(os.path.isfile(dispatch.REMOVE_WORKTREE))
        self.assertEqual(rest, f"[ ! -e {shlex.quote(tree)} ] && claude rm abc123")

    def test_a_session_without_a_worktree_only_deletes_its_record(self):
        self.assertEqual(dispatch.remove_command("abc123", None, "task"), "claude rm abc123")


class ExtractSessionIdDecodeTests(unittest.TestCase):
    def test_survives_invalid_utf8_in_the_log_file(self):
        """The log file holds Claude's raw combined stdout and stderr. A byte such as
        0x9d, undefined in cp1252, used to raise UnicodeDecodeError uncaught, so --wait
        printed no record and lost the worker's already-final exit code."""
        with tempfile.TemporaryDirectory(prefix="claude-log-") as d:
            log_path = Path(d) / "result.log"
            log_path.write_bytes(b'{"result": "ok", "session_id": "claude-\x9d-session"}\n')
            session_id = dispatch._extract_claude_session_id(str(log_path))
        self.assertIsNotNone(session_id)
        self.assertTrue(session_id.startswith("claude-"))


class PendingSessionIdTests(unittest.TestCase):
    def test_pending_session_message_names_where_the_id_will_appear(self):
        """When codex has not yet emitted thread.started by the time a detached launch
        gives up waiting, 'unknown' used to read as a permanent fact instead of naming
        where the id will show up once codex reports it."""
        self.assertEqual(
            dispatch._format_session_line(None, "/tmp/run.log"),
            "session: pending, appears in /tmp/run.log once codex reports thread.started",
        )
        self.assertEqual(dispatch._format_session_line("abc", "/tmp/run.log"), "session: abc")


class WorktreeHomeDecodeTests(unittest.TestCase):
    def test_survives_a_non_utf8_byte_in_git_output(self):
        """0x9d is undefined in cp1252, the code page Windows decodes subprocess text
        output with by default, and used to raise UnicodeDecodeError or crash dispatch
        after Claude had already launched, or on POSIX raise on an invalid UTF-8 byte.
        git itself is not stubbed here: a bare "git" lookup through subprocess.run only
        auto-resolves .exe on Windows, never a .cmd stub, so this instead replaces
        subprocess.run and decodes the same raw bytes with the kwargs dispatch.py actually
        passes, which fails outright if errors="replace" is ever dropped."""
        raw = b"not-a-real-common-dir-" + bytes([0x9d]) + b"-marker"

        def fake_run(cmd, **kwargs):
            self.assertEqual(kwargs.get("errors"), "replace")
            decoded = raw.decode(kwargs.get("encoding", "utf-8"), errors=kwargs["errors"])
            return subprocess.CompletedProcess(cmd, 0, stdout=decoded, stderr="")

        with unittest.mock.patch.object(dispatch.subprocess, "run", side_effect=fake_run):
            result = dispatch.worktree_home("/some/path")
        self.assertEqual(result, "")


class SupervisorParamsPathTests(unittest.TestCase):
    def test_two_launches_for_the_same_log_path_get_separate_params_files(self):
        """The supervisor parameters file used to be named only from the log path, so two
        launches sharing one (concurrent dispatches to the same --log) would overwrite each
        other's parameters, and a read-only launch could end up running a writable one's
        command."""
        with tempfile.TemporaryDirectory(prefix="params-path-") as d:
            log_path = str(Path(d) / "shared.log")
            path_a = dispatch._new_params_path(log_path)
            path_b = dispatch._new_params_path(log_path)
            try:
                self.assertNotEqual(path_a, path_b)
                self.assertTrue(os.path.isfile(path_a))
                self.assertTrue(os.path.isfile(path_b))
            finally:
                os.remove(path_a)
                os.remove(path_b)

    def test_detached_claude_same_log_uses_separate_id_handoffs(self):
        with tempfile.TemporaryDirectory(prefix="claude-id-path-") as d:
            prompt = Path(d) / "prompt.md"
            prompt.write_text("Review.\n", encoding="utf-8")
            ctx = dispatch.Context(cwd=d, prompt_path=str(prompt), name="Review", log=str(Path(d) / "shared.log"),
                                   notify_log=str(Path(d) / "notify.log"), claude_bin="claude", codex_bin=None)
            args = dispatch.build_parser().parse_args(["--runner", "claude", "--read-only", "--arc-dispatch",
                                                       "--prompt", str(prompt), "--cwd", d])
            handoffs = []

            def capture(params, id_file_path=None, pid_file_path=None):
                handoffs.append(id_file_path)
                return 123, params["expected_session_id"]

            with unittest.mock.patch.object(dispatch, "_spawn_supervisor", side_effect=capture), \
                 unittest.mock.patch("builtins.print"):
                dispatch.do_claude_read_only(args, ctx)
                dispatch.do_claude_read_only(args, ctx)
            self.assertEqual(len(handoffs), 2)
            self.assertNotEqual(handoffs[0], handoffs[1])


class WorkerPidResolutionTests(unittest.TestCase):
    def test_native_target_reports_its_own_pid_not_a_short_lived_childs(self):
        """A native .exe target, unlike a .cmd/.bat one, spawns with no cmd.exe wrapper in
        between, so wrapper_pid already names the worker. Walking its children regardless
        of target used to find whatever short-lived helper, such as git or an MCP server,
        the worker starts on its own, and report that pid instead."""
        if os.name != "nt":
            self.skipTest("_resolve_worker_pid only walks children on Windows")
        parent = subprocess.Popen([
            sys.executable, "-c",
            # The helper sleeps briefly rather than exiting immediately, so it is still
            # alive for _resolve_worker_pid's poll to observe: a helper that exits before
            # the first poll would make even the reverted, unguarded code fall through to
            # the same wrapper_pid this test expects, by accident rather than by the fix.
            "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1)']); time.sleep(2)",
        ])
        try:
            resolved = dispatch._resolve_worker_pid(parent.pid, target="C:\\fake\\claude.EXE", timeout=1.0)
            self.assertEqual(resolved, parent.pid)
        finally:
            parent.terminate()
            try:
                parent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                parent.kill()


class SupervisorStartupFailureTests(DispatchTestCase):
    def _broken_params(self, log_path):
        return {
            "kind": "codex",
            "cmd": [str(Path(self.tmpdir) / "does-not-exist.exe"), "exec", "--json"],
            "cwd": str(self.work_dir),
            "prompt_path": str(self.prompt_path),
            "log_path": str(log_path),
            "result_path": str(log_path) + ".last.md",
            "notify_thread": None,
            "notify_log_path": None,
            "name": "startup-fail-test",
            "codex_bin": self.codex_bin_path,
        }

    def test_reports_the_failure_and_raises_instead_of_a_quiet_success(self):
        """A supervisor that crashes before writing the worker's pid file used to fall back
        to proc.pid, its own already-dead pid, and print a normal-looking launch record
        with no failure and no callback."""
        log_path = Path(self.tmpdir) / "startup-fail.log"
        with self.assertRaises(SystemExit) as ctx:
            dispatch._spawn_supervisor(
                self._broken_params(log_path),
                id_file_path=str(log_path) + ".id",
                pid_file_path=str(log_path) + ".pid",
                wait_seconds=20,
            )
        self.assertNotEqual(ctx.exception.code, 0)

    def test_the_poll_loop_ends_once_the_supervisor_exits_not_after_the_full_deadline(self):
        """The poll loop must notice the dead supervisor and stop, rather than spinning
        until wait_seconds runs out for a worker that will never start."""
        log_path = Path(self.tmpdir) / "startup-fail-timing.log"
        start = time.monotonic()
        with self.assertRaises(SystemExit):
            dispatch._spawn_supervisor(
                self._broken_params(log_path),
                id_file_path=str(log_path) + ".id",
                pid_file_path=str(log_path) + ".pid",
                wait_seconds=20,
            )
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 10.0, "the poll loop waited near the full deadline instead of ending when the supervisor exited")


class InternalSuperviseExitCodeTests(DispatchTestCase):
    def test_returns_the_workers_exit_code_not_always_zero(self):
        """_run_internal_supervise used to discard both pumps' return codes and always
        return 0, so a supervisor whose worker failed still reported success."""
        log_path = Path(self.tmpdir) / "supervise.log"
        last_path = Path(self.tmpdir) / "supervise.last.md"
        params = {
            "kind": "codex",
            "cmd": [self.codex_bin_path, "exec", "-C", str(self.work_dir), "-o", str(last_path), "--json"],
            "cwd": str(self.work_dir),
            "prompt_path": str(self.prompt_path),
            "log_path": str(log_path),
            "id_file_path": None,
            "result_path": str(last_path),
            "notify_thread": None,
            "notify_log_path": None,
            "name": "supervise-test",
            "codex_bin": self.codex_bin_path,
        }
        params_path = Path(self.tmpdir) / "supervise.pump.json"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        old_env = dict(os.environ)
        os.environ["DISPATCH_TEST_EXEC_STATUS"] = "9"
        try:
            status = dispatch._run_internal_supervise(str(params_path))
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        self.assertEqual(status, 9)


class WatchClaudeQueueFailureTests(DispatchTestCase):
    def test_rejected_queue_request_forces_a_non_zero_exit_and_is_recorded(self):
        """A rejected codex queue request used to be silently ignored (check=False with no
        return-code check), so the watcher exited 0 and delivered no notice at all."""
        result = self.run_watch_claude(
            [self.claude_bin_path, self.codex_bin_path, "claude-test", "watch-test", "origin-x",
             "claude logs claude-test", "claude attach claude-test"],
            extra_env={
                "DISPATCH_TEST_CLAUDE_SEQUENCE": "complete",
                "DISPATCH_TEST_CLAUDE_STATE_FILE": str(Path(self.tmpdir) / "state"),
                "DISPATCH_TEST_QUEUE_LOG": str(Path(self.tmpdir) / "unused-queue.log"),
                "DISPATCH_TEST_QUEUE_STATUS": "17",
                "DISPATCH_NOTIFY_INITIAL_SECONDS": "1", "DISPATCH_NOTIFY_MAX_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("codex queue exited 17", result.stderr)


class NotifyLogTruncationTests(DispatchTestCase):
    def test_send_notice_truncates_a_reused_notify_log(self):
        """A reused notify log path from an earlier run used to keep its old content if
        send_notice ever appended instead of truncating."""
        notify_log = Path(self.tmpdir) / "notify.log"
        notify_log.write_text("stale output from an earlier run\n" * 50, encoding="utf-8")
        queue_log = Path(self.tmpdir) / "queue.log"
        old_env = dict(os.environ)
        os.environ["DISPATCH_TEST_QUEUE_LOG"] = str(queue_log)
        try:
            watch_claude.send_notice(self.codex_bin_path, "origin-x", "fresh message", str(notify_log))
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        content = notify_log.read_text(encoding="utf-8")
        self.assertNotIn("stale output from an earlier run", content)


class OpenWindowResolvesWtTests(unittest.TestCase):
    def test_open_window_resolves_wt_through_shutil_which(self):
        """A bare "wt" only auto-resolves to wt.exe through Windows' own CreateProcess
        default extension search, which can never find a non-.exe stand-in placed on PATH.
        Popen must receive shutil.which's fully resolved path instead, the way every other
        executable in this file is resolved, so a test's own stub is what actually runs.
        subprocess.Popen is mocked here, so this can never spawn a real process even if the
        resolution regresses back to a bare name."""
        if os.name != "nt":
            self.skipTest("this resolution only matters on Windows")
        captured = {}

        def fake_which(name, *a, **kw):
            return f"C:\\stub\\{name}.cmd" if name == "wt" else shutil.which(name, *a, **kw)

        def fake_popen(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(pid=4242)

        with tempfile.TemporaryDirectory(prefix="open-window-") as d:
            prompt_path = str(Path(d) / "prompt.md")
            Path(prompt_path).write_text("x", encoding="utf-8")
            with unittest.mock.patch.object(dispatch, "resolve_git_bash", return_value="C:\\Git\\bin\\bash.exe"), \
                 unittest.mock.patch.object(dispatch.shutil, "which", side_effect=fake_which), \
                 unittest.mock.patch.object(dispatch.subprocess, "Popen", side_effect=fake_popen):
                dispatch.open_window("echo hi", d, prompt_path)
        self.assertEqual(captured["cmd"][0], "C:\\stub\\wt.cmd")


class DetachFlagsTests(unittest.TestCase):
    def test_uses_create_no_window_not_detached_process(self):
        """A console-app grandchild under DETACHED_PROCESS, such as node under a detached
        claude.cmd, can open its own visible console window whose closing kills whatever is
        attached to it. CREATE_NO_WINDOW never allocates one."""
        if os.name != "nt":
            self.skipTest("creation flags only apply on Windows")
        flags = dispatch._detach_kwargs()["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        self.assertFalse(flags & subprocess.DETACHED_PROCESS)


class CodexAttendedPromptTests(DispatchTestCase):
    def test_attended_window_substitutes_the_prompt_file_not_stdin(self):
        """Interactive codex (not `codex exec`) never reads a prompt from stdin, so the
        attended run file must substitute the prompt file into the command line the way
        dispatch.sh does."""
        if os.name != "nt":
            self.skipTest("this attach path is exercised on Windows here")
        if dispatch.resolve_git_bash() is None:
            self.skipTest("no Git Bash found to open a window with")
        wt_stub = _write_noop_launcher(self.bin_dir, "wt")
        self._assert_terminal_launcher_resolves_to_stub("wt", wt_stub)
        result = self.run_dispatch(
            ["--runner", "codex", "--attach", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        run_file = dispatch.strip_ext(str(self.prompt_path)) + ".run.sh"
        wait_for_file(run_file)
        content = Path(run_file).read_text(encoding="utf-8")
        self.assertIn(f'"$(< {shlex.quote(str(self.prompt_path))})"', content)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class StaleHandoffFileTests(DispatchTestCase):
    def test_stale_id_file_is_cleared_before_a_new_launch(self):
        """A stale .id file left by an earlier crashed or slow run at the same log path
        used to be read back as this run's session id before the new run ever wrote one."""
        log_path = Path(self.tmpdir) / "stale.log"
        stale_id_path = Path(str(log_path) + ".id")
        stale_id_path.write_text("stale-leftover-id\n", encoding="utf-8")
        result = self.run_dispatch(
            ["--runner", "codex", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path)],
            extra_env={"DISPATCH_TEST_CODEX_THREAD_ID": "fresh-thread-1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session: fresh-thread-1", result.stdout)
        self.assertNotIn("stale-leftover-id", result.stdout)


class ArgAbbreviationTests(DispatchTestCase):
    def test_full_is_rejected_not_treated_as_full_access(self):
        """allow_abbrev=False keeps an abbreviation such as --full from silently meaning
        --full-access and disabling the Codex sandbox."""
        result = self.run_dispatch(
            ["--runner", "codex", "--full", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)


class HelpTextTests(unittest.TestCase):
    def test_every_flag_has_a_help_string(self):
        parser = dispatch.build_parser()
        for action in parser._actions:
            if not action.option_strings or action.option_strings[0] == "-h":
                continue
            self.assertTrue(action.help, f"{action.option_strings} has no help text")

    def test_value_taking_flags_name_their_value_in_a_metavar(self):
        """Derived from the parser itself rather than a fixed list of flags, so a new
        value-taking flag with no metavar fails this test instead of shipping with a
        bare --flag VALUE placeholder in --help."""
        parser = dispatch.build_parser()
        for action in parser._actions:
            if not action.option_strings or action.nargs == 0:
                continue
            flag = action.option_strings[0]
            self.assertIsNotNone(action.metavar, f"{flag} must use a metavar to name its value")

    def test_help_strings_avoid_banned_punctuation_and_words(self):
        """A help string reaches a coordinator as plain prose, so it follows the same
        writing rules as any other user-facing text: no semicolon, no emdash, and none
        of should, may or might."""
        parser = dispatch.build_parser()
        banned_words = ("should", "may", "might")
        for action in parser._actions:
            if not action.option_strings or action.option_strings[0] == "-h":
                continue
            help_text = action.help or ""
            flag = action.option_strings[0]
            self.assertNotIn(";", help_text, f"{flag} help must not hold a semicolon")
            self.assertNotIn("—", help_text, f"{flag} help must not hold an emdash")
            lowered = help_text.lower()
            for word in banned_words:
                self.assertIsNone(
                    re.search(rf"\b{word}\b", lowered), f"{flag} help must not hold the word '{word}'"
                )

    def test_help_output_names_a_file_for_prompt(self):
        result = subprocess.run(
            [sys.executable, str(DISPATCH_PY), "--help"], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--prompt FILE", result.stdout)


class PromptValidationTests(DispatchTestCase):
    def test_newline_prompt_value_reports_the_prompt_text_message(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", "line one\nline two", "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prompt takes a file path, and this value looks like prompt text", result.stderr)

    def test_long_prompt_value_reports_the_prompt_text_message(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", "x" * 201, "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prompt takes a file path, and this value looks like prompt text", result.stderr)

    def test_short_missing_path_keeps_the_original_message(self):
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", "/no/such/file.md", "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prompt FILE is required and must exist", result.stderr)
        self.assertNotIn("looks like prompt text", result.stderr)

    def test_missing_path_with_spaces_keeps_the_original_message(self):
        """A real Windows path can hold spaces, so whitespace alone must not be read as
        prompt text (decision in the plan: only a newline or a long value trips the new
        message)."""
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", "/no/such/My Documents/file.md", "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prompt FILE is required and must exist", result.stderr)
        self.assertNotIn("looks like prompt text", result.stderr)


class WaitResultPrintingTests(DispatchTestCase):
    def test_codex_wait_prints_the_result_text_below_the_cap(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--- result", result.stdout)
        self.assertIn("durable result", result.stdout)

    def test_claude_read_only_wait_prints_its_log_as_the_result(self):
        """The Claude read-only path has no separate last-message file: its log is the
        result file for that mode, and --wait must print that file's text."""
        args_file = Path(self.tmpdir) / "claude-wait-args.log"
        result = self.run_dispatch(
            ["--runner", "claude", "--read-only", "--wait", "--prompt", str(self.prompt_path),
             "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CLAUDE_ARGS": str(args_file)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--- result", result.stdout)
        self.assertIn("read-only result", result.stdout)

    def test_codex_wait_reports_a_missing_result_file(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_SKIP_RESULT": "1", "DISPATCH_TEST_EXEC_STATUS": "3"},
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("no result file at", result.stdout)

    def test_codex_wait_reports_an_empty_result_file(self):
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_RESULT_TEXT": ""},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is empty", result.stdout)

    def test_codex_wait_over_the_cap_prints_the_path_and_size_not_the_text(self):
        big_text = "y" * 16001
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_RESULT_TEXT": big_text},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("16001 bytes", result.stdout)
        self.assertNotIn(big_text, result.stdout)

    def test_wait_exit_code_survives_a_missing_result_file(self):
        """The process must exit with the worker's own exit code even when there is
        nothing to print for the result, so a caller scripting on $? never sees success
        painted over a failed worker."""
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)],
            extra_env={"DISPATCH_TEST_CODEX_SKIP_RESULT": "1", "DISPATCH_TEST_EXEC_STATUS": "9"},
        )
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)

    def test_wait_survives_a_log_path_the_console_code_page_cannot_encode(self):
        """stdout used to be reconfigured for encode errors only inside
        _print_wait_result, right before the result text. Every record line printed
        ahead of that call, such as the log path here, still ran on the unpatched
        stream, so a character outside the console's code page raised
        UnicodeEncodeError on one of those earlier lines and the process exited on
        its own traceback instead of the worker's exit code."""
        log_path = Path(self.tmpdir) / "wait-日本.log"
        result = self.run_dispatch(
            ["--runner", "codex", "--wait", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir),
             "--log", str(log_path)],
            extra_env={"PYTHONIOENCODING": "cp1252", "DISPATCH_TEST_EXEC_STATUS": "6"},
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertIn("mode: writable waited", result.stdout)
        self.assertIn("--- result", result.stdout)
        self.assertIn("durable result", result.stdout)


CANARY_SUBJECT = "canary-commit-subject-7f3"
CANARY_OTHER_SUBJECT = "canary-second-repo-subject-9d2"
CANARY_OTHER_MARKER = "canary-second-repo-marker-4c1"

CANARY_PROMPT = """This is a permission test. A denial is an expected result, not a failure.
After a denial, go straight on to the next tool call. You must attempt all eight, one tool
call each, even after several denials in a row. Never stop early and never ask a question.

1. Run with the Bash tool: git log --oneline -1 | cat
2. Run with the Bash tool: git -C {other} log --oneline -1; wc -l {marker_file}
3. Run with the Bash tool: git commit --dry-run -m canary
4. Run with the Bash tool: git stash
5. Run with the Bash tool: git fetch origin
6. Run with the Bash tool: git show --output={output} HEAD
7. Use the Write tool to create the file {written} with the text canary.
8. Read the file {marker_file} with the Read tool.

Report eight lines. Each line says ran or denied. Quote the output of commands 1 and 2 in
full when they ran. Quote the content found by step 8 in full.
"""


def _claude_result_payload(log_path):
    """Return the JSON result object a Claude read-only run writes to its log. The log
    also carries the worker's stderr, so the scan takes the last parsable line."""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("type") == "result":
            return payload
    return None


def _canary_requested():
    """True only when DISPATCH_CANARY names a real value. DISPATCH_CANARY=0 must not run
    the canary, unlike a bare truthiness check on the environment string."""
    value = os.environ.get("DISPATCH_CANARY", "").strip().lower()
    return value not in ("", "0", "false")


def _canary_claude_bin():
    """Resolve the claude binary the way dispatch.py's resolve_bin does: CLAUDE_BIN first,
    the bare name on PATH otherwise."""
    override = os.environ.get("CLAUDE_BIN")
    return shutil.which(override) if override else shutil.which("claude")


def _check_second_repo_reached(report, denied, other_subject, other_posix):
    """Raise AssertionError when a denial names the second repository, or when the report
    lacks its commit subject. A live canary run and a synthetic-report unit test both drive
    this same check, so a change that weakens it fails without a CLI."""
    if [cmd for cmd in denied if other_posix in cmd]:
        raise AssertionError(
            f"a Bash read of the second repository was denied, so the session runs in manual "
            f"mode or a deny rule is too wide. Denials: {denied}")
    if other_subject not in report:
        raise AssertionError(
            f"the report lacks the second repository's commit subject, so git -C never "
            f"reached it. Denials: {denied}")


class ReadOnlyGitReachDetectionTests(unittest.TestCase):
    """Exercise _check_second_repo_reached with synthetic reports, so the check itself is
    proven to fail on a denied or missing cross-directory read without spending a model
    request on the live canary."""

    def test_check_fails_when_a_denial_names_the_second_repo(self):
        other_posix = "/tmp/dispatch-canary-other"
        report = f"1. ran: {CANARY_SUBJECT}\n2. denied\n"
        denied = [f"git -C {other_posix} log --oneline -1"]
        with self.assertRaises(AssertionError):
            _check_second_repo_reached(report, denied, CANARY_OTHER_SUBJECT, other_posix)

    def test_check_fails_when_the_report_lacks_the_second_repos_subject(self):
        with self.assertRaises(AssertionError):
            _check_second_repo_reached(f"1. ran: {CANARY_SUBJECT}\n", [], CANARY_OTHER_SUBJECT,
                                       "/tmp/dispatch-canary-other")


@unittest.skipUnless(_canary_requested(), "set DISPATCH_CANARY=1 to run the real-CLI canary")
class ReadOnlyGitCanaryTests(unittest.TestCase):
    """Measure what the installed Claude CLI lets a --read-only session run.

    The read-only contract in SKILL.md rests on auto mode and on how Claude Code matches
    deny rules. Both belong to the CLI and change between releases, and no stub can stand
    in for them. This canary drives the real binary and spends one model request, so it
    stays opt-in. Auto mode needs a model that supports it, so the default model is one
    that does: the launcher runs a model without it in dontAsk, and step 2 is then denied.
    """

    @classmethod
    def setUpClass(cls):
        if not _canary_claude_bin():
            raise RuntimeError(
                "DISPATCH_CANARY asked for the real-CLI canary, but no claude executable "
                "was found (set CLAUDE_BIN or put claude on PATH)")

    def test_a_read_only_session_runs_git_reads_and_denies_writes(self):
        tmpdir = tempfile.mkdtemp(prefix="dispatch-canary-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        repo = Path(tmpdir) / "repo"
        other = Path(tmpdir) / "other"
        repo.mkdir()
        other.mkdir()

        def git(target, *args):
            return subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True, check=True)

        git(repo, "init", "-q", ".")
        (repo / "file.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        git(repo, "add", "file.txt")
        git(repo, "-c", "user.name=Canary", "-c", "user.email=canary@example.com", "commit", "-qm", CANARY_SUBJECT)

        git(other, "init", "-q", ".")
        (other / "marker.txt").write_text(CANARY_OTHER_MARKER + "\n", encoding="utf-8")
        git(other, "add", "marker.txt")
        git(other, "-c", "user.name=Canary", "-c", "user.email=canary@example.com", "commit", "-qm",
            CANARY_OTHER_SUBJECT)

        written = repo / "written.txt"
        prompt = Path(tmpdir) / "prompt.md"
        prompt.write_text(
            CANARY_PROMPT.format(other=other.as_posix(), written=written.as_posix(),
                                  output=(repo / "output.patch").as_posix(),
                                  marker_file=(other / "marker.txt").as_posix()),
            encoding="utf-8",
        )
        log = Path(tmpdir) / "canary.log"
        model = os.environ.get("DISPATCH_CANARY_MODEL", "sonnet")

        result = subprocess.run(
            [sys.executable, str(DISPATCH_PY), "--runner", "claude", "--read-only", "--wait",
             "--model", model, "--cwd", str(repo), "--add-dir", str(other),
             "--prompt", str(prompt), "--log", str(log)],
            capture_output=True, text=True, timeout=900,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        payload = _claude_result_payload(log)
        self.assertIsNotNone(payload, Path(log).read_text(encoding="utf-8", errors="replace")[:2000])
        report = payload.get("result", "")
        denied = [entry.get("tool_input", {}).get("command", "") for entry in payload.get("permission_denials", [])]

        self.assertIn(CANARY_SUBJECT, report,
                      f"the session never reported a piped git read. Denials: {denied}")
        self.assertFalse([cmd for cmd in denied if cmd.startswith("git log")],
                         f"a plain git read was denied, so SKILL.md overstates the reach: {denied}")

        _check_second_repo_reached(report, denied, CANARY_OTHER_SUBJECT, other.as_posix())

        for command in ("git commit", "git stash", "git fetch", "git show --output="):
            self.assertTrue([cmd for cmd in denied if cmd.startswith(command)],
                            f"no denial for {command}, so the git deny rules no longer match. Denials: {denied}")
        self.assertFalse(written.exists(), "the Write tool created a file in a read-only session")

        self.assertIn(CANARY_OTHER_MARKER, report,
                      f"Read did not reach the second repository that --add-dir grants: {report}")

        for target, label in ((repo, "the first repository"), (other, "the second repository")):
            status = git(target, "status", "--porcelain")
            self.assertEqual(status.stdout.strip(), "", f"the session changed {label}")

class DeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipt = Receipt.create('parent', 'codex', 'worker', str(self.root / 'shared.log'),
                                      str(self.root / 'shared.md'), 'fake-codex')
        self.report = Path(self.receipt.read()['report'])
        self.report.write_text('report A', encoding='utf-8')
        self.event = self.receipt.observe('completed', 0)

    def queue(self, status=0):
        return unittest.mock.Mock(return_value=subprocess.CompletedProcess([], status))

    def consume(self, event=None):
        event = event or self.event
        launch = self.receipt.read()['launch']
        claim = self.receipt.claim('parent', launch, event['id'], 'handler')
        self.receipt.acknowledge('parent', launch, event['id'], 'handler', claim['token'], 'incorporated')
        return claim

    def test_consumed_callback_never_processes_or_submits_again(self):
        self.consume()
        queue = self.queue()
        self.assertEqual(self.receipt.send(self.event['id'], run=queue), 0)
        queue.assert_not_called()
        self.assertEqual(self.receipt.claim('parent', self.receipt.read()['launch'], self.event['id'], 'other'),
                         {'consumption': 'consumed', 'outcome': 'incorporated'})

    def test_successful_worker_does_not_hide_failed_delivery(self):
        import io
        prompt = self.root / 'prompt.md'
        prompt.write_text('bounded fixture', encoding='utf-8')
        for runner in ('codex', 'claude'):
            worker = unittest.mock.Mock()
            worker.stdout = io.StringIO('')
            worker.wait.return_value = 0
            with unittest.mock.patch.object(dispatch.subprocess, 'Popen', return_value=worker), \
                 unittest.mock.patch.object(watch_claude, 'send_notice', return_value=17):
                if runner == 'codex':
                    status = dispatch._codex_pump(['worker'], str(self.root), str(prompt),
                                                 str(self.root / 'log'), None, str(self.report),
                                                 'parent', None, 'worker', 'codex')[1]
                else:
                    status = dispatch._claude_readonly_pump(['worker'], str(self.root), str(prompt),
                                                           str(self.root / 'log'), str(self.report),
                                                           'parent', None, 'worker', 'codex')
            self.assertEqual(status, 1, runner)

    def test_unknown_acceptance_is_not_automatically_retried(self):
        queue = self.queue(17)
        self.assertEqual(self.receipt.send(self.event['id'], run=queue), 1)
        self.assertEqual(self.receipt.send(self.event['id'], run=queue), 1)
        queue.assert_called_once()
        state = self.receipt.read()['events'][-1]
        self.assertEqual(state['delivery'], 'unknown')
        self.assertEqual(state['status'], 0)
        self.assertEqual(state['consumption'], 'unread')

    def test_refresh_preserves_event_and_delivery_history(self):
        self.receipt.send(self.event['id'], run=self.queue())
        refreshed = self.receipt.refresh()
        self.assertEqual(refreshed['id'], self.event['id'])
        self.assertEqual(refreshed['delivery'], 'accepted')
        self.assertEqual(len(refreshed['attempts']), 1)

    def test_changed_report_remains_unread_after_old_acknowledgment(self):
        self.report.write_text('report B', encoding='utf-8')
        second = self.receipt.refresh()
        self.consume()
        self.assertNotEqual(second['id'], self.event['id'])
        self.assertEqual(Path(self.event['result']).read_text(), 'report A')
        self.assertEqual(self.receipt.read()['events'][-1]['consumption'], 'unread')

    def test_colliding_requested_paths_have_private_authoritative_reports(self):
        other = Receipt.create('parent', 'codex', 'worker', str(self.root / 'shared.log'),
                               str(self.root / 'shared.md'), 'fake-codex')
        self.assertNotEqual(other.read()['report'], self.receipt.read()['report'])
        self.assertEqual(other.observe('completed', 0)['availability'], 'missing')

    def test_acknowledgment_requires_origin_launch_event_and_claim(self):
        launch = self.receipt.read()['launch']
        for origin, identity in [('wrong', launch), ('parent', 'wrong')]:
            with self.assertRaises(ValueError):
                self.receipt.claim(origin, identity, self.event['id'], 'handler')
        with self.assertRaises(ValueError):
            self.receipt.acknowledge('parent', launch, self.event['id'], 'handler', 'wrong', 'incorporated')
        claim = self.receipt.claim('parent', launch, self.event['id'], 'handler')
        with self.assertRaises(ValueError):
            self.receipt.acknowledge('parent', launch, self.event['id'], 'handler', 'wrong', 'incorporated')
        with self.assertRaises(ValueError):
            self.receipt.claim('parent', launch, self.event['id'], 'other')
        self.assertEqual(self.receipt.claim('parent', launch, self.event['id'], 'handler')['token'], claim['token'])

    def test_concurrent_submission_keeps_one_attempt_and_allows_ack(self):
        entered, finish = threading.Event(), threading.Event()
        errors = []
        def queue(*args, **kwargs):
            entered.set()
            if not finish.wait(5):
                raise AssertionError('queue synchronization deadline')
            return subprocess.CompletedProcess([], 0)
        def sender():
            try:
                self.receipt.send(self.event['id'], run=queue)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=sender)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            other = self.queue()
            self.assertEqual(self.receipt.send(self.event['id'], run=other), 1)
            other.assert_not_called()
            self.consume()
        finally:
            finish.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        event = self.receipt.read()['events'][-1]
        self.assertEqual((event['delivery'], event['consumption']), ('accepted', 'consumed'))
        self.assertEqual(len(event['attempts']), 1)

    def test_interrupted_submission_remains_unknown_during_recovery(self):
        state = self.receipt.read()
        state['events'][-1].update(delivery='submitting', attempts=[{'id': 'interrupted', 'state': 'submitting'}])
        self.receipt.save(state)
        self.assertEqual(self.receipt.recover_delivery(), 1)
        current = self.receipt.read()['events'][-1]
        self.assertEqual(current['id'], self.event['id'])
        self.assertEqual(current['delivery'], 'unknown')
        self.assertEqual(len(current['attempts']), 1)

    def test_missing_malformed_and_empty_reports_preserve_evidence(self):
        self.report.unlink()
        missing = self.receipt.refresh()
        self.assertEqual(missing['availability'], 'missing')
        self.report.write_text('{bad json', encoding='utf-8')
        malformed = self.receipt.observe('completed', 0, report_format='claude-json')
        self.assertEqual(malformed['availability'], 'malformed')
        self.assertEqual(Path(malformed['result']).read_text(), '{bad json')
        self.report.write_text('', encoding='utf-8')
        self.assertEqual(self.receipt.refresh()['availability'], 'empty')

    def test_timeout_never_retries_uncertain_queue_acceptance(self):
        queue = unittest.mock.Mock(side_effect=subprocess.TimeoutExpired('queue', 30))
        self.assertEqual(self.receipt.send(self.event['id'], run=queue), 1)
        self.assertEqual(self.receipt.send(self.event['id'], run=queue), 1)
        queue.assert_called_once()
        self.assertEqual(self.receipt.read()['events'][-1]['delivery'], 'unknown')

    def test_monitor_lock_prevents_competing_recovery(self):
        with self.receipt.monitor():
            with self.assertRaises(TimeoutError):
                self.receipt.recover_monitor()

    def test_process_death_releases_monitor_lock(self):
        code = "import os,sys; from receipt import Receipt; r=Receipt(sys.argv[1]); lock=r.monitor(); lock.__enter__(); os._exit(0)"
        result = subprocess.run([sys.executable, '-c', code, str(self.receipt.root)],
                                cwd=str(SKILL_DIR), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.receipt.monitor():
            pass

    def test_registry_errors_never_establish_worker_success(self):
        self.receipt.update(runner='claude-writable', session='worker', claude_bin='fake-claude')
        snapshots = iter(['[{"id":"worker","state":"working"}]', '{bad', '{bad', '{bad'])
        def registry(cmd, **kwargs):
            if cmd[1] == 'agents':
                return subprocess.CompletedProcess(cmd, 0, stdout=next(snapshots))
            return subprocess.CompletedProcess(cmd, 1, stdout=b'')
        ticks = iter(range(100))
        with unittest.mock.patch.object(watch_claude.subprocess, 'run', side_effect=registry), \
             unittest.mock.patch.object(Receipt, 'deliver', return_value=0):
            result = watch_claude.run_watcher('fake-claude', 'fake-codex', 'worker', 'worker',
                                            'parent', str(self.report), 'attach', receipt=self.receipt,
                                            env={}, clock=lambda: next(ticks), sleep=lambda _: None)
        self.assertEqual(result, 1)
        event = self.receipt.read()['events'][-1]
        self.assertEqual((event['kind'], event['status']), ('monitoring-error', None))

    def test_monitor_recovery_resumes_stopped_writable_observations(self):
        cases = [('monitoring-error', '[{"id":"worker","state":"failed"}]', 'failed'),
                 ('attention', '[{"id":"worker","state":"failed"}]', 'failed'),
                 ('monitoring-error', '[]', 'unverified'),
                 ('monitoring-error', '{bad', 'monitoring-error')]
        for kind, snapshot, expected in cases:
            with self.subTest(kind=kind, snapshot=snapshot):
                self.receipt.update(runner='claude-writable', session='worker', claude_bin='fake-claude', registry_seen=True)
                old = self.receipt.observe(kind, None)
                self.receipt.send(old['id'], run=self.queue())
                self.consume(old)
                before = self.receipt.read()['events']
                def registry(cmd, **kwargs):
                    return subprocess.CompletedProcess(cmd, 0, stdout=snapshot if cmd[1] == 'agents' else b'logs')
                ticks = iter(range(100))
                watcher = watch_claude.run_watcher
                def run_watcher(*args, **kwargs):
                    return watcher(*args, **kwargs, env={}, clock=lambda: next(ticks), sleep=lambda _: None)
                with unittest.mock.patch.object(watch_claude.subprocess, 'run', side_effect=registry) as calls, \
                     unittest.mock.patch.object(watch_claude, 'run_watcher', side_effect=run_watcher), \
                     unittest.mock.patch.object(Receipt, 'deliver', return_value=0), \
                     unittest.mock.patch.object(dispatch.subprocess, 'Popen') as worker:
                    event = self.receipt.recover_monitor()
                self.assertEqual((event['kind'], event['status']), (expected, None))
                if expected == kind:
                    self.assertEqual(self.receipt.read()['events'], before)
                    self.assertEqual(event['id'], old['id'])
                else:
                    self.assertEqual(self.receipt.read()['events'][:-1], before)
                self.assertEqual(calls.call_args_list[0].args[0][1], 'agents')
                worker.assert_not_called()

    def test_malformed_registry_states_publish_bounded_diagnostics(self):
        for field in ('state', 'status'):
            for malformed in (['working'], {'value': 'working'}):
                with self.subTest(field=field, malformed=malformed):
                    snapshot = json.dumps([{'id': 'worker', field: malformed}])
                    def registry(cmd, **kwargs):
                        return subprocess.CompletedProcess(cmd, 0, stdout=snapshot if cmd[1] == 'agents' else b'diagnostic logs')
                    ticks = iter(range(100))
                    with unittest.mock.patch.object(watch_claude.subprocess, 'run', side_effect=registry) as calls, \
                         unittest.mock.patch.object(Receipt, 'deliver', return_value=0) as deliver:
                        status = watch_claude.run_watcher('fake-claude', 'fake-codex', 'worker', 'worker',
                                                        'parent', str(self.report), 'attach', receipt=self.receipt,
                                                        env={}, clock=lambda: next(ticks), sleep=lambda _: None)
                    self.assertEqual(status, 1)
                    event = self.receipt.read()['events'][-1]
                    self.assertEqual((event['kind'], event['status']), ('monitoring-error', None))
                    self.assertEqual(Path(event['diagnostic']).read_bytes(), b'diagnostic logs')
                    self.assertEqual(sum(call.args[0][1] == 'agents' for call in calls.call_args_list), 3)
                    deliver.assert_called_once_with(event['id'])

    def test_actual_registry_disappearance_retains_unknown_status(self):
        snapshots = iter(['[{"id":"worker","state":"working"}]', '[]'])
        def registry(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=next(snapshots) if cmd[1] == 'agents' else b'logs')
        ticks = iter(range(100))
        with unittest.mock.patch.object(watch_claude.subprocess, 'run', side_effect=registry), \
             unittest.mock.patch.object(Receipt, 'deliver', return_value=0):
            watch_claude.run_watcher('fake-claude', 'fake-codex', 'worker', 'worker', 'parent',
                                    str(self.report), 'attach', receipt=self.receipt, env={},
                                    clock=lambda: next(ticks), sleep=lambda _: None)
        event = self.receipt.read()['events'][-1]
        self.assertEqual((event['kind'], event['status']), ('unverified', None))
        self.assertTrue(self.receipt.read()['registry_seen'])


if __name__ == "__main__":
    unittest.main()
