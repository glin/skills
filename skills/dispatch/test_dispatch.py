"""Tests for dispatch.py and the Arc routes it shares with dispatch.sh.

Every launch scenario drives the real launcher as a subprocess against a stub arc
executable that records its arguments. The stub answers window discovery, arc start,
arc run, and the dispatch wait and result commands. test.sh covers how each launcher
maps its flags to arc run.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import venv
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DISPATCH_PY = SKILL_DIR / "dispatch.py"

sys.path.insert(0, str(SKILL_DIR))
import dispatch  # noqa: E402


ARC_STUB_SOURCE = '''import json, os, sys
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
elif args[0] == "run":
    if "--completion" in args and args[args.index("--completion") + 1] == "wait":
        print(json.dumps({"launchId": "launch-1", "state": "observed", "eventId": "event-1", "outcome": "succeeded"}))
    else:
        print(json.dumps({"launchId": "launch-1", "transport": "standalone", "state": "starting"}))
    if "--arc-dispatch" in args and case != "pending":
        print('ARC_DISPATCH_SESSION=' + json.dumps({"agent": args[args.index("--provider") + 1], "session_id": "child-1"}))
elif args[0] == "dispatch":
    if args[1] == "wait":
        counter = os.environ["DISPATCH_TEST_ARC_CALLS"]
        with open(counter, encoding="utf-8") as stream:
            waits = sum('"wait"' in line for line in stream)
        print(json.dumps({"launchId": "launch-1", "state": "deadline"} if waits < 3 else
                         {"launchId": "launch-1", "state": "observed", "eventId": "event-1", "outcome": "succeeded"}))
    elif args[1] == "status":
        print(json.dumps({"observation": {"exitCode": 0, "monitor": "finished"}}))
    else:
        print(json.dumps({"launchId": "launch-1", "eventId": "event-1", "report": {"path": "r.md", "bytes": 7}, "text": "report\\n"}))
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
'''


def _write_launcher(bin_dir, name, stub_path):
    launcher = bin_dir / (f"{name}.cmd" if os.name == "nt" else name)
    if os.name == "nt":
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{stub_path}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{stub_path}" "$@"\n', encoding="utf-8")
        os.chmod(launcher, 0o755)
    return launcher


def git_bash():
    """Git Bash on Windows, found from git's install location, because a bare bash on PATH
    can be WSL's. Any bash elsewhere."""
    if os.name != "nt":
        return shutil.which("bash")
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    for ancestor in list(Path(git_bin).resolve().parents)[:5]:
        for candidate in (ancestor / "bin" / "bash.exe", ancestor / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


class DispatchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dispatch-pytest-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.bin_dir = Path(self.tmpdir) / "bin"
        self.bin_dir.mkdir()
        self.work_dir = Path(self.tmpdir) / "work"
        self.work_dir.mkdir()
        self.prompt_path = Path(self.tmpdir) / "prompt.md"
        self.prompt_path.write_text("Finish the bounded test task.\n", encoding="utf-8")
        self.env = dict(os.environ)
        self.env["ARC_BIN"] = str(self.bin_dir / "missing-arc")
        for key in ("ARC_WINDOW", "CLAUDE_CODE_PROMPT_CACHE_TTL"):
            self.env.pop(key, None)
        # The launcher reads the Codex provider from the Codex config, so the machine's own
        # config must not reach it.
        codex_home = Path(self.tmpdir) / "codex-home"
        codex_home.mkdir()
        self.env["CODEX_HOME"] = str(codex_home)

    def run_dispatch(self, args, extra_env=None, timeout=60):
        env = {**self.env, **(extra_env or {})}
        return subprocess.run([sys.executable, str(DISPATCH_PY), *args], env=env, cwd=str(self.work_dir),
                              capture_output=True, text=True, timeout=timeout)


class ArcStubTestCase(DispatchTestCase):
    def setUp(self):
        super().setUp()
        self.arc_calls = Path(self.tmpdir) / "arc-calls.jsonl"
        stub = self.bin_dir / "arc_stub.py"
        stub.write_text(ARC_STUB_SOURCE, encoding="utf-8")
        self.env["ARC_BIN"] = str(_write_launcher(self.bin_dir, "arc", stub))
        if os.name == "nt":
            entry = self.bin_dir.parent / "cli.cjs"
            entry.write_text("const {spawnSync} = require('node:child_process');\n"
                             f"const child = spawnSync({json.dumps(sys.executable)}, "
                             f"[{json.dumps(str(stub))}, ...process.argv.slice(2)], {{stdio: 'inherit'}});\n"
                             "process.exit(child.status ?? 1);\n", encoding="utf-8")
            self.arc_entry = entry
        self.env["DISPATCH_TEST_ARC_CALLS"] = str(self.arc_calls)

    def launch(self, extra=(), case="accepted", shell=False, default_cwd=False, marked=True, observe=False):
        arguments = ["--runner", "claude", "--owned", "--arc-dispatch", "--name", "Inspect",
                     "--model", "sonnet", "--effort", "high",
                     "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir), *extra]
        if not marked:
            arguments.remove("--arc-dispatch")
        if observe:
            arguments = ["--wait-launch", "launch-1", *extra]
        if default_cwd:
            del arguments[arguments.index("--cwd"):arguments.index("--cwd") + 2]
        if not shell:
            return self.run_dispatch(arguments, {"DISPATCH_TEST_ARC_CASE": case})
        bash = git_bash()
        if not bash:
            self.skipTest("Bash unavailable for shell boundary")
        driver = Path(self.tmpdir) / "arc-route.sh"
        driver.write_text("exec " + " ".join(shlex.quote(value) for value in
                          [bash.replace("\\", "/"), str(SKILL_DIR / "dispatch.sh").replace("\\", "/"), *arguments]) + "\n",
                          encoding="utf-8")
        return subprocess.run([bash, str(driver).replace("\\", "/")], env={**self.env, "DISPATCH_TEST_ARC_CASE": case},
                              cwd=self.work_dir, capture_output=True, text=True, timeout=60)

    def calls(self):
        return [json.loads(line) for line in self.arc_calls.read_text().splitlines()] if self.arc_calls.exists() else []

    def verbs(self):
        return [call[0] for call in self.calls()]

    def run_call(self):
        runs = [call for call in self.calls() if call[0] == "run"]
        return runs[-1] if runs else None

    def value(self, call, flag):
        return call[call.index(flag) + 1]


class ArcStartTests(ArcStubTestCase):
    def test_both_launchers_accept_without_a_standalone_launch(self):
        # A legacy Arc lists --untagged in its help. A current Arc starts the reader's session without it.
        cases = [(shell, case, help_text) for shell in (False, True)
                 for case in ("accepted", "accepted-with-errors") for help_text in ("legacy", "current")]
        for shell, case, help_text in cases:
            with self.subTest(shell=shell, case=case, help=help_text):
                self.arc_calls.unlink(missing_ok=True)
                self.env["DISPATCH_TEST_ARC_HELP"] = help_text
                result = self.launch(["--model", "claude-haiku-5-5", "--effort", "low"], case, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(self.run_call())
                start = self.calls()[-1]
                self.assertEqual(start[0], "start")
                for flag, expected in (("--permissions", "auto"), ("--model", "claude-haiku-5-5"),
                                       ("--effort", "low"), ("--window", "a" * 24)):
                    self.assertEqual(self.value(start, flag), expected)
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
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(case=case, shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIsNone(self.run_call(), "unknown delivery started a second worker")
                    self.assertIn("Do not start another worker", result.stderr)
                    self.assertIn("request:", result.stdout)
                    if case == "stderr-only":
                        self.assertIn("The selected Arc window is unavailable.", result.stderr)

    def test_explicit_refusal_falls_back_with_requested_flags(self):
        self.env["DISPATCH_TEST_ARC_NONZERO"] = "1"
        for shell in (False, True):
            for override in ({}, {"remove": ["reason"], "set": {"requester": "parent-session"}}):
                with self.subTest(shell=shell, override=override):
                    self.arc_calls.unlink(missing_ok=True)
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(["--permission-mode", "acceptEdits"], "refused", shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    run = self.run_call()
                    self.assertEqual(self.value(run, "--permissions"), "acceptEdits")
                    self.assertEqual(self.value(run, "--model"), "sonnet")
                    self.assertIn("--agent-run=false", run)

    def test_malformed_refusals_never_start_a_standalone_launch_on_either_launcher(self):
        overrides = [{"set": {"sessionId": value}} for value in ([], False, 0, "", None, {}, "already-started")]
        overrides += [{"remove": [field]} for field in ("version", "requester", "requestId", "status")]
        overrides += [{"set": {field: value}} for field, values in (
            ("version", (True, 0, "1", None)),
            ("requester", (False, [], "", "x" * 257)),
            ("reason", (None, False, [], {})),
            ("status", (False, [], {})),
        ) for value in values]
        overrides.append({"set": {"provider": "codex"}})
        for shell in (False, True):
            for override in overrides:
                with self.subTest(shell=shell, override=override):
                    self.arc_calls.unlink(missing_ok=True)
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(case="refused", shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(self.verbs(), ["windows", "--help", "start"])
                    self.assertIn("mode: Arc delivery unknown", result.stdout)
                    self.assertIn("Do not start another worker", result.stderr)

    def test_unready_ambiguous_incomplete_or_wrong_folder_refuses_before_start(self):
        for case in ("unready", "ambiguous", "incomplete", "wrong-folder"):
            with self.subTest(case=case):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(case=case)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("refused before acceptance", result.stderr)
                self.assertEqual(self.verbs(), ["windows", "run"])

    def test_explicit_window_selects_one_from_ambiguous_discovery(self):
        self.env["ARC_WINDOW"] = "a" * 24
        result = self.launch(case="ambiguous")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.verbs()[-1], "start")

    def test_attach_selects_reader_owned_arc_without_a_standalone_launch(self):
        for shell in (False, True):
            with self.subTest(shell=shell):
                result = self.launch(["--attach"], shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.verbs()[-1], "start")
                self.assertIsNone(self.run_call())

    def test_read_only_owned_requests_run_standalone_without_contacting_a_window(self):
        result = self.launch(["--read-only", "--wait"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in self.calls()][1:], [["dispatch", "status"], ["dispatch", "result"]])
        self.assertEqual(self.verbs()[0], "run")
        self.assertIn("--read-only", self.run_call())
        self.assertEqual(self.value(self.run_call(), "--completion"), "wait")

    def test_codex_default_refuses_arc_and_full_access_maps_exactly(self):
        result = self.launch(["--runner", "codex"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex workspace-write with no approvals", result.stderr)
        self.assertEqual(self.verbs(), ["run"])
        self.assertNotIn("--permissions", self.run_call())
        for shell in (False, True):
            with self.subTest(shell=shell):
                self.arc_calls.unlink()
                result = self.launch(["--runner", "codex", "--full-access", "--model", "sol"], shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.verbs(), ["windows", "--help", "start"])
                start = self.calls()[-1]
                self.assertEqual(self.value(start, "--permissions"), "never")
                self.assertEqual(self.value(start, "--model"), "gpt-6.1-sol")
                self.assertIn('"owned":true', result.stdout.splitlines()[-1])
                self.assertIsNone(self.run_call())

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
                        self.assertEqual(self.value(start, "--name"), name)
                        self.assertEqual(self.value(start, "--model"), "%DISPATCH_TEST_EXPANSION%")
                        self.assertNotIn("Microsoft Windows", result.stdout)
                        self.assertIsNone(self.run_call())

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
                self.assertEqual(self.verbs(), ["windows", "--help", "start"])
                start = self.calls()[-1]
                for flag, expected in (("--name", name), ("--model", "gpt-6.1-sol"),
                                       ("--effort", "high"), ("--provider", "codex"),
                                       ("--permissions", "never"), ("--prompt-file", str(self.prompt_path))):
                    self.assertEqual(self.value(start, flag), expected)
                self.assertNotIn("--agent-run", start)
                self.assertIn('"owned":true', result.stdout.splitlines()[-1])

    @unittest.skipUnless(os.name == "nt", "native Windows cmd boundary")
    def test_unknown_windows_arc_wrapper_refuses_without_running_it(self):
        self.arc_entry.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot preserve literal arguments", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unsupported_claude_permissions_run_standalone_on_both_launchers(self):
        for shell in (False, True):
            for mode in ("dontAsk", "bypassPermissions"):
                with self.subTest(shell=shell, mode=mode):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(["--permission-mode", mode], shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    self.assertEqual(self.verbs(), ["run"])
                    self.assertEqual(self.value(self.run_call(), "--permissions"), mode)

    def test_local_prompt_incompatibilities_refuse_before_contact_on_both_launchers(self):
        for shell in (False, True):
            for content in (b"X" * (60 * 1024), b"", b" \n /command", b"\xef\xbb\xbf /command"):
                with self.subTest(shell=shell, size=len(content)):
                    self.arc_calls.unlink(missing_ok=True)
                    self.prompt_path.write_bytes(content)
                    result = self.launch(shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("refused before acceptance", result.stderr)
                    self.assertEqual(self.verbs(), ["run"])
            self.arc_calls.unlink(missing_ok=True)
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
                self.assertIsNone(self.run_call())
        finally:
            self.work_dir = folder
            os.rmdir(alias) if os.name == "nt" else alias.unlink()


class LaunchObservationTests(ArcStubTestCase):
    def test_explicit_log_keeps_owned_waited_launches_observable_with_a_ready_window(self):
        for shell in (False, True):
            for runner in ("claude", "codex"):
                with self.subTest(shell=shell, runner=runner):
                    self.arc_calls.unlink(missing_ok=True)
                    log = str(Path(self.tmpdir) / f"{runner}.log")
                    flags = ["--runner", runner, "--log", log]
                    if runner == "codex":
                        flags.append("--full-access")
                    result = self.launch(flags, shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.verbs(), ["run"])
                    launch = self.run_call()
                    self.assertIn("--agent-run=false", launch)
                    self.assertEqual(self.value(launch, "--completion"), "manual")
                    self.assertEqual(Path(self.value(launch, "--log")), Path(log))
                    if runner == "codex":
                        self.assertEqual(self.value(launch, "--permissions"), "danger-full-access")
                    self.assertIn("--wait-launch launch-1", result.stdout)
                    self.assertTrue(result.stdout.splitlines()[-1].startswith("ARC_DISPATCH_SESSION="))
                    result = self.launch(shell=shell, observe=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.verbs().count("run"), 1)
                    self.assertNotIn("start", self.verbs())
                    self.assertIn("outcome: succeeded", result.stdout)

    def test_printed_observer_commands_start_with_a_spaced_interpreter_and_keep_reports_blinded(self):
        bash = git_bash()
        if not bash:
            self.skipTest("Bash unavailable for observer command")
        interpreter_home = Path(self.tmpdir) / "Python with space's & literal"
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(interpreter_home)
        interpreter = interpreter_home / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        output = io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.env), \
                unittest.mock.patch.object(dispatch.sys, "executable", str(interpreter)), redirect_stdout(output):
            code = dispatch.main(["--read-only", "--arc-dispatch", "--model", "sonnet", "--effort", "high",
                                  "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)])
        self.assertEqual(code, 0)
        commands = {line.split(": ", 1)[0]: line.split(": ", 1)[1]
                    for line in output.getvalue().splitlines() if line.startswith("await")}
        shells = [("await", [bash, "-c"])]
        if os.name == "nt":
            shells.append(("await (PowerShell)", ["powershell", "-NoProfile", "-Command"]))
        for label, shell in shells:
            with self.subTest(shell=label):
                command = commands[label]
                self.assertIn("--read-only", command)
                result = subprocess.run([*shell, command], env=self.env, cwd=self.work_dir,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("outcome: succeeded", result.stdout)
                self.assertIn("result: r.md", result.stdout)
                self.assertNotIn("report\n", result.stdout)
        self.assertEqual(self.verbs().count("run"), 1)

    def test_final_interval_reply_keeps_outcome_and_deadline_cause_after_command_overhead(self):
        for observed in (False, True):
            with self.subTest(observed=observed):
                clock = [0.0]
                waits = []

                def run(command, **kwargs):
                    verb = command[1]
                    if verb == "wait":
                        deadline = int(command[-1]) / 1000
                        waits.append(deadline)
                        # The final outcome is published before Arc's deadline. Startup
                        # and reply delivery take another tenth of a second.
                        duration = 1.5 if len(waits) == 1 else deadline + 0.1
                        if duration > kwargs["timeout"]:
                            clock[0] += kwargs["timeout"]
                            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                        clock[0] += duration
                        reply = {"state": "observed", "eventId": "event-1", "outcome": "succeeded"} \
                            if observed and len(waits) == 2 else {"state": "deadline"}
                    elif verb == "status":
                        reply = {"observation": {"exitCode": 0, "monitor": "finished"}}
                    else:
                        reply = {"report": {"path": "r.md", "bytes": 7}, "text": "report\n"}
                    return subprocess.CompletedProcess(command, 0, json.dumps(reply), "")

                output, errors = io.StringIO(), io.StringIO()
                with unittest.mock.patch.object(dispatch, "wait_limit", return_value=2), \
                        unittest.mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                        unittest.mock.patch.object(dispatch.subprocess, "run", side_effect=run), \
                        redirect_stdout(output), redirect_stderr(errors):
                    code = dispatch.finish_wait([], ".", "launch-1", {"state": "deadline"})
                self.assertEqual(waits, [2, 0.5])
                self.assertEqual(code, 0 if observed else 2)
                self.assertNotIn("Arc wait failed", errors.getvalue())
                if observed:
                    self.assertIn("outcome: succeeded", output.getvalue())
                else:
                    self.assertIn("Arc published no outcome for launch launch-1 in 2 seconds", errors.getvalue())

    def test_missing_marker_refuses_before_arc_for_both_runners_and_launchers(self):
        for shell in (False, True):
            for runner in ("claude", "codex"):
                with self.subTest(shell=shell, runner=runner):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(["--runner", runner], shell=shell, marked=False)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("--arc-dispatch is required", result.stderr)
                    self.assertEqual(self.calls(), [])

    def test_foreground_marker_precedes_separate_observation_without_relaunch(self):
        for shell in (False, True):
            for runner in ("claude", "codex"):
                for case in ("accepted", "pending"):
                    with self.subTest(shell=shell, runner=runner, case=case):
                        self.arc_calls.unlink(missing_ok=True)
                        result = self.launch(["--runner", runner, "--read-only"], shell=shell, case=case)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(self.verbs(), ["run"])
                        self.assertEqual(self.value(self.run_call(), "--completion"), "manual")
                        self.assertIn("--wait-launch launch-1 --read-only", result.stdout)
                        if case == "accepted":
                            self.assertTrue(result.stdout.splitlines()[-1].startswith("ARC_DISPATCH_SESSION="))
                        else:
                            self.assertNotIn("ARC_DISPATCH_SESSION=", result.stdout)
                        result = self.launch(["--read-only"], shell=shell, observe=True)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(self.verbs().count("run"), 1)
                        waits = [call for call in self.calls() if call[:2] == ["dispatch", "wait"]]
                        self.assertEqual(len(waits), 3)
                        for call in waits:
                            self.assertEqual(call[2], "launch-1")
                            self.assertIn("--deadline-ms", call)
                        self.assertIn("result: r.md", result.stdout)
                        self.assertNotIn("report\n", result.stdout)

    def test_wait_uses_one_deadline_and_caps_the_next_arc_request(self):
        replies = [(0, ['{"state":"deadline"}'], ""), (0, ['{"state":"deadline"}'], "")]
        with unittest.mock.patch.object(dispatch, "wait_limit", return_value=2), \
                unittest.mock.patch.object(dispatch.time, "monotonic", side_effect=[0, 0, 1.5, 2]), \
                unittest.mock.patch.object(dispatch, "run_arc_json", side_effect=replies) as run:
            self.assertEqual(dispatch.finish_wait([], ".", "launch-1", {"state": "deadline"}), 2)
        self.assertEqual([call.args[1][-1] for call in run.call_args_list], ["2000", "500"])
        self.assertEqual([call.args[3] for call in run.call_args_list], [72, 70.5])

    def test_observation_needs_no_launch_flags_or_new_worker(self):
        for shell in (False, True):
            with self.subTest(shell=shell):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(["--read-only"], shell=shell, observe=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("run", self.verbs())
                self.assertIn("result: r.md", result.stdout)
                self.assertNotIn("--- result", result.stdout)
                self.assertNotIn("report\n", result.stdout)


class RemovedFlagTests(ArcStubTestCase):
    def test_worktree_and_callback_flags_refuse_before_any_launch_on_both_launchers(self):
        cases = ((["--worktree"], "--worktree is not a dispatch flag", "git worktree add"),
                 (["-w", "review-1"], "--worktree is not a dispatch flag", "--cwd <path>"),
                 (["--notify-thread", "019a0000-0000-7000-8000-000000000000"],
                  "--notify-thread is not a dispatch flag", "arc dispatch status"))
        for shell in (False, True):
            for extra, message, route in cases:
                with self.subTest(shell=shell, flag=extra[0]):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(extra, shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertIn(route, result.stderr)
                    self.assertEqual(self.calls(), [])


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
        prompt text. Only a newline or a long value trips the prompt-text message."""
        result = self.run_dispatch(
            ["--runner", "claude", "--prompt", "/no/such/My Documents/file.md", "--cwd", str(self.work_dir)],
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--prompt FILE is required and must exist", result.stderr)
        self.assertNotIn("looks like prompt text", result.stderr)


class WaitResultPrintingTests(unittest.TestCase):
    def printed(self, reply):
        stream = io.StringIO()
        with redirect_stdout(stream):
            dispatch.print_wait_result(reply)
        return stream.getvalue().splitlines()

    def test_report_text_prints_with_receipt_shaped_worker_lines_framed(self):
        lines = self.printed({"report": {"path": "r.md", "bytes": 30}, "text": "done\nARC_DISPATCH_SESSION={}\n"})
        self.assertEqual(lines, ["--- result", "done", "worker: ARC_DISPATCH_SESSION={}"])

    def test_report_without_inline_text_prints_its_path_and_size_not_its_text(self):
        lines = self.printed({"report": {"path": "r.md", "bytes": 20000}, "text": None})
        self.assertEqual(lines[1], "the result text is unavailable inline. Read r.md, 20000 bytes")

    def test_missing_and_empty_reports_say_so(self):
        self.assertEqual(self.printed({"report": None, "text": None})[1], "no report for this launch")
        self.assertEqual(self.printed({"report": {"path": "r.md", "bytes": 0}, "text": ""})[1], "the result at r.md is empty")


class ModelNameTests(unittest.TestCase):
    def resolve(self, model, config=None):
        with tempfile.TemporaryDirectory() as home:
            if config is not None:
                Path(home, "config.toml").write_text(config, encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                return dispatch.resolve_codex_model(model)

    def test_codex_shorthands_reach_arc_start_as_full_ids(self):
        for shorthand, model in (("sol", "gpt-6.1-sol"), ("sol6", "gpt-6-sol"), ("luna", "gpt-6-luna"),
                                 ("gpt-6.1-sol", "gpt-6.1-sol")):
            self.assertEqual(self.resolve(shorthand), model)

    def test_bedrock_takes_the_openai_prefix_from_the_top_level_table_only(self):
        self.assertEqual(self.resolve("sol", 'model_provider = "amazon-bedrock"\n'), "openai.gpt-6.1-sol")
        self.assertEqual(self.resolve("sol", '[model_providers.x]\nmodel_provider = "amazon-bedrock"\n'), "gpt-6.1-sol")


class DefaultLogTests(unittest.TestCase):
    def test_named_launches_sharing_one_prompt_keep_separate_logs(self):
        self.assertEqual(dispatch.default_log("/r/prompt.md", "review opus/5"), "/r/prompt.review-opus-5.log")
        self.assertEqual(dispatch.default_log("/r/prompt.md", ""), "/r/prompt.log")


if __name__ == "__main__":
    unittest.main()
