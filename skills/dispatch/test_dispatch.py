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
elif args[:2] == ["start", "--help"]:
    print("Usage: arc start --prompt-file PATH (--setup KEY | --provider claude|codex) [OPTIONS]")
    if not os.environ.get("DISPATCH_TEST_ARC_NO_COMPLETION"):
        print("  --completion manual|wait  With --arc-dispatch, select retained retrieval or waited completion.")
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
                      errors=["Name save failed"] if case == "accepted-with-errors" else [], link=None)
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
        for key in ("ARC_WINDOW", "CLAUDE_CODE_PROMPT_CACHE_TTL", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID",
                    "DISPATCH_WAIT_LIMIT_SECONDS"):
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


class ServiceTierTests(ArcStubTestCase):
    def test_hosted_tier_survives_python_and_bash_bridge(self):
        for shell in (False, True):
            for tier in (None, "default", "fast", "priority", "ultrafast"):
                with self.subTest(shell=shell, tier=tier):
                    self.arc_calls.unlink(missing_ok=True)
                    flags = ["--runner", "codex", "--model", "sol", "--full-access"]
                    if tier is not None:
                        flags += ["--service-tier", tier]
                    result = self.launch(flags, shell=shell)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIsNone(self.run_call())
                    start = self.calls()[-1]
                    self.assertEqual(start[0], "start")
                    self.assertEqual(start.count("--service-tier"), int(tier is not None))
                    if tier is not None:
                        self.assertEqual(self.value(start, "--service-tier"), tier)
                    self.assertEqual(self.value(start, "--model"), "gpt-6.1-sol")
                    self.assertEqual(self.value(start, "--effort"), "high")
                    self.assertEqual(self.value(start, "--permissions"), "never")
                    self.assertIn('"owned":true', result.stdout.splitlines()[-1])

    def test_invalid_tier_never_contacts_arc(self):
        cases = [(["--runner", "codex", "--service-tier", value], False)
                 for value in ("", "auto", "ultra", "FAST")]
        cases += [(["--runner", "codex", "--service-tier", "fast", "--service-tier=fast"], False),
                  (["--runner", "codex", "--service-tier"], False),
                  (["--service-tier", "default"], False),
                  (["--service-tier", "fast"], True)]
        for shell in (False, True):
            for flags, observe in cases:
                with self.subTest(shell=shell, flags=flags, observe=observe):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(flags, shell=shell, observe=observe)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(self.calls(), [])

    def test_refused_fallback_preserves_tier_and_unknown_delivery_stops(self):
        for shell in (False, True):
            for case in ("refused", "stderr-only"):
                with self.subTest(shell=shell, case=case):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(["--runner", "codex", "--full-access", "--service-tier=ultrafast"],
                                         case=case, shell=shell)
                    start = [call for call in self.calls() if call[0] == "start" and "--help" not in call][0]
                    self.assertEqual(self.value(start, "--service-tier"), "ultrafast")
                    if case == "refused":
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(self.value(self.run_call(), "--service-tier"), "ultrafast")
                        self.assertEqual(self.run_call().count("--service-tier"), 1)
                    else:
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIsNone(self.run_call())
                        self.assertIn("Do not start another worker", result.stderr)


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

    def test_arc_link_prints_unchanged_before_the_receipt(self):
        host, session = "a" * 24, "12345678-1234-1234-1234-123456789abc"
        arc_link = "[Arc label](arc-session:literal%252f%21%27%28%29%2A~)"
        rows = [
            ("string", "accepted", host, {"set": {"link": arc_link}}, arc_link),
            ("accepted with errors", "accepted-with-errors", host, {"set": {"link": arc_link}}, arc_link),
            ("no invoking window", "accepted", None, {"set": {"link": arc_link}}, arc_link),
            ("empty string", "accepted", host, {"set": {"link": ""}}, ""),
            ("null", "accepted", host, {"set": {"link": None}}, None),
            ("absent", "accepted", host, {"remove": ["link"]}, None),
            ("non-string", "accepted", host, {"set": {"link": 42}}, None),
        ]
        for subject, case, window, override, expected in rows:
            for shell in (False, True):
                with self.subTest(subject=subject, shell=shell):
                    self.arc_calls.unlink(missing_ok=True)
                    self.env.pop("ARC_WINDOW", None)
                    if window:
                        self.env["ARC_WINDOW"] = window
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(case=case, shell=shell)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIsNone(self.run_call())
                    start = self.calls()[-1]
                    request = self.value(start, "--request-id")
                    reply = {"version": 1, "requestId": request, "status": case, "requester": None,
                             "sessionId": session, "provider": "claude",
                             "errors": ["Name save failed"] if case == "accepted-with-errors" else [], "link": None}
                    reply.update(override.get("set", {}))
                    for key in override.get("remove", []):
                        reply.pop(key, None)
                    lines = result.stdout.splitlines()
                    self.assertEqual(lines[:2], [f"window: {host}", f"request: {request}"])
                    self.assertEqual(json.loads(lines[2]), reply)
                    receipt = {"agent": "claude", "session_id": session, "name": "Inspect", "owned": True}
                    self.assertEqual(lines[3:], ["mode: Arc session", "runner: claude",
                                                f"tree: {self.work_dir}", "name: Inspect", f"id: {session}",
                                                *([f"link: {expected}"] if expected is not None else []),
                                                "ARC_DISPATCH_SESSION=" + json.dumps(receipt, separators=(",", ":"))])

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

    def test_explicit_refusal_ends_owned_claude_and_falls_back_for_codex_or_attach(self):
        self.env["DISPATCH_TEST_ARC_NONZERO"] = "1"
        for shell in (False, True):
            for override in ({}, {"remove": ["reason"], "set": {"requester": "parent-session"}}):
                with self.subTest(shell=shell, override=override, request="claude"):
                    self.arc_calls.unlink(missing_ok=True)
                    self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps(override)
                    result = self.launch(["--permission-mode", "acceptEdits"], "refused", shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    reason = "Arc refused the launch" if override else "The requested setup is unavailable"
                    self.assertIn(f"Reader-owned launch refused: Arc refused the launch: {reason}. No worker", result.stderr)
                    self.assertIn("add --attach", result.stderr)
                    self.assertIsNone(self.run_call())
                for flags, permissions in ((["--runner", "codex", "--full-access"], "danger-full-access"),
                                           (["--attach", "--permission-mode", "acceptEdits"], "acceptEdits")):
                    with self.subTest(shell=shell, override=override, request=flags[1]):
                        self.arc_calls.unlink(missing_ok=True)
                        result = self.launch(flags, "refused", shell)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("refused before acceptance", result.stderr)
                        run = self.run_call()
                        self.assertEqual(self.value(run, "--permissions"), permissions)
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
                    self.assertEqual(self.verbs(), ["windows", "--help", "start", "start"])
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

    def test_codex_default_runs_standalone_and_full_access_maps_exactly(self):
        result = self.launch(["--runner", "codex"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Arc cannot keep Codex workspace-write with no approvals", result.stderr)
        self.assertNotIn("cannot send", result.stderr)
        self.assertEqual(self.verbs(), ["run"])
        self.assertNotIn("--permissions", self.run_call())
        for shell in (False, True):
            with self.subTest(shell=shell):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(["--runner", "codex", "--full-access", "--model", "sol"], shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.verbs(), ["windows", "--help", "start", "start"])
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
        for published in (False, True):
            if published:
                active_entry = launcher.parent / "run.cjs"
                active_entry.write_bytes(entry.read_bytes())
                entry.unlink()
            for shell in (False, True):
                with self.subTest(shell=shell, published=published):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(["--runner", "codex", "--full-access", "--service-tier", "fast", "--model", "sol",
                                          "--effort", "high", "--name", name], shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("mode: Arc session", result.stdout)
                    self.assertEqual(self.verbs(), ["windows", "--help", "start", "start"])
                    start = self.calls()[-1]
                    for flag, expected in (("--name", name), ("--model", "gpt-6.1-sol"),
                                           ("--effort", "high"), ("--provider", "codex"), ("--service-tier", "fast"),
                                           ("--permissions", "never"), ("--prompt-file", str(self.prompt_path))):
                        self.assertEqual(self.value(start, flag), expected)
                    self.assertNotIn("--agent-run", start)
                    self.assertIn('"owned":true', result.stdout.splitlines()[-1])
                    self.arc_calls.unlink()
                    result = self.launch(["--runner", "codex", "--read-only", "--service-tier", "fast",
                                          "--model", "gpt-6.1-sol", "--name", name], shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.verbs(), ["run"])
                    run = self.run_call()
                    self.assertEqual(self.value(run, "--service-tier"), "fast")
                    self.assertEqual(self.value(run, "--name"), name)
                    self.assertIn("--read-only", run)
                    self.assertNotIn("--permissions", run)

    @unittest.skipUnless(os.name == "nt", "native Windows cmd boundary")
    def test_unknown_windows_arc_wrapper_refuses_without_running_it(self):
        self.arc_entry.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot preserve literal arguments", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unsupported_claude_permissions_refuse_without_a_worker_on_both_launchers(self):
        for shell in (False, True):
            for mode in ("dontAsk", "bypassPermissions"):
                with self.subTest(shell=shell, mode=mode):
                    self.arc_calls.unlink(missing_ok=True)
                    result = self.launch(["--permission-mode", mode], shell=shell)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(f"Arc cannot keep Claude --permission-mode {mode}", result.stderr)
                    self.assertIn("add --attach", result.stderr)
                    self.assertEqual(self.calls(), [])

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
    def test_owned_launches_stay_observable_in_a_ready_window(self):
        self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps({"set": {"launchId": "launch-1"}})
        for shell in (False, True):
            for runner in ("claude", "codex"):
                with self.subTest(shell=shell, runner=runner):
                    self.arc_calls.unlink(missing_ok=True)
                    flags = ["--runner", runner]
                    if runner == "codex":
                        flags.append("--full-access")
                    result = self.launch(flags, shell=shell)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.verbs(), ["windows", "--help", "start", "start"])
                    self.assertEqual(self.value(self.calls()[-1], "--completion"), "wait")
                    self.assertIn("mode: Arc session", result.stdout)
                    self.assertIn("--wait-launch launch-1", result.stdout)
                    self.assertTrue(result.stdout.splitlines()[-1].startswith("ARC_DISPATCH_SESSION="))
                    result = self.launch(shell=shell, observe=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(sum(call[0] == "start" and "--help" not in call for call in self.calls()), 1)
                    self.assertIsNone(self.run_call())
                    self.assertIn("outcome: succeeded", result.stdout)

    def test_waited_arc_session_without_a_launch_id_exits_2_without_a_second_worker(self):
        for shell in (False, True):
            with self.subTest(shell=shell):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(["--wait"], shell=shell)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(self.verbs(), ["windows", "--help", "start", "start"])
                self.assertIn("Arc returned no launch ID for session", result.stderr)
                self.assertNotIn("--wait-launch", result.stdout)
                self.assertIn('"owned":true', result.stdout.splitlines()[-1])

    def test_arc_start_without_completion_keeps_manual_start_and_waits_standalone(self):
        self.env["DISPATCH_TEST_ARC_NO_COMPLETION"] = "1"
        self.env["DISPATCH_TEST_ARC_REPLY_OVERRIDE"] = json.dumps({"set": {"launchId": "launch-1"}})
        for shell in (False, True):
            with self.subTest(shell=shell, wait=False):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(shell=shell)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("--completion", self.calls()[-1])
                self.assertIn("mode: Arc session", result.stdout)
                self.assertNotIn("--wait-launch", result.stdout)
            with self.subTest(shell=shell, wait=True):
                self.arc_calls.unlink(missing_ok=True)
                result = self.launch(["--wait"], shell=shell)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("the installed arc start cannot wait for the session", result.stderr)
                self.assertEqual(self.verbs()[:4], ["windows", "--help", "start", "run"])
                self.assertEqual(self.value(self.run_call(), "--completion"), "wait")

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

    def test_copied_resume_command_keeps_an_inline_deadline_override(self):
        bash = git_bash()
        if not bash:
            self.skipTest("Bash unavailable for observer command")
        result = self.run_dispatch(["--wait-launch", "launch-1"],
                                   {"DISPATCH_WAIT_LIMIT_SECONDS": "0", "CODEX_THREAD_ID": "thread-1"})
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        commands = {line.split(": ", 1)[0]: line.split(": ", 1)[1] for line in lines if line.startswith("await") and ": " in line}
        shells = [("await", [bash, "-c"])]
        if os.name == "nt":
            shells.append(("await (PowerShell)", ["powershell", "-NoProfile", "-Command"]))
        start = lines[lines.index("await (Codex cell):") + 2]
        cell_command = json.loads(start.split("{cmd: ", 1)[1].split(", yield_time_ms", 1)[0])
        self.assertEqual(cell_command, commands[shells[-1][0]])
        # The copied command runs without the variable, as in a fresh shell.
        for label, shell in shells:
            with self.subTest(shell=label):
                copied = subprocess.run([*shell, commands[label]], env=self.env, cwd=self.work_dir,
                                        capture_output=True, text=True, timeout=60)
                self.assertIn("outcome: deadline", copied.stdout, copied.stdout + copied.stderr)
                self.assertNotIn("outcome: succeeded", copied.stdout)
        self.assertEqual(self.calls(), [])

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

    def wait_with_clock(self, environ, arguments):
        """Run dispatch.main in this process against a clock that each Arc wait advances by
        its whole deadline. Arc never publishes an outcome. Return the exit code, stdout,
        stderr and the seconds that passed."""
        clock = [0.0]

        def run(command, **kwargs):
            if command[1] == "run":
                reply = {"launchId": "launch-1", "state": "deadline"}
            else:
                clock[0] += int(command[-1]) / 1000
                reply = {"launchId": "launch-1", "state": "deadline"}
            return subprocess.CompletedProcess(command, 0, json.dumps(reply), "")

        output, errors = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, environ, clear=True), \
                unittest.mock.patch.object(dispatch, "resolve_bin", return_value="arc"), \
                unittest.mock.patch.object(dispatch, "arc_command", return_value=["arc"]), \
                unittest.mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                unittest.mock.patch.object(dispatch.subprocess, "run", side_effect=run), \
                redirect_stdout(output), redirect_stderr(errors):
            code = dispatch.main(list(arguments))
        return code, output.getvalue(), errors.getvalue(), clock[0]

    def test_observation_ends_before_the_parent_cache_expires_and_prints_its_resume_command(self):
        launch = ["--runner", "claude", "--read-only", "--wait", "--arc-dispatch", "--model", "sonnet",
                  "--effort", "high", "--prompt", str(self.prompt_path), "--cwd", str(self.work_dir)]
        five, hour = {"CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"}, {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}
        codex = {"CODEX_THREAD_ID": "thread-1"}
        # Each case: the parent's environment, the command, and the deadline, where None
        # keeps the 25 hour bound.
        cases = (
            (five, ["--wait-launch", "launch-1"], 240),
            (hour, ["--wait-launch", "launch-1", "--read-only"], 3540),
            ({**five, **codex, "CLAUDE_CODE_SESSION_ID": "session-1"}, ["--wait-launch", "launch-1"], 240),
            ({**hour, "DISPATCH_WAIT_LIMIT_SECONDS": "3300"}, ["--wait-launch", "launch-1"], 3300),
            ({**codex, "DISPATCH_WAIT_LIMIT_SECONDS": "600"}, ["--wait-launch", "launch-1"], 600),
            (five, launch, 240),
            ({}, ["--wait-launch", "launch-1"], None),
            ({"CLAUDE_CODE_PROMPT_CACHE_TTL": "10m"}, ["--wait-launch", "launch-1"], None),
            ({**five, **codex}, ["--wait-launch", "launch-1"], None),
            # The launch sets the worker's 5 minute lifetime in the environment after the
            # deadline reads the parent's.
            ({}, launch, None),
        )
        for environ, arguments, deadline in cases:
            with self.subTest(environ=environ, arguments=arguments[:3]):
                code, output, errors, elapsed = self.wait_with_clock(environ, arguments)
                if deadline is None:
                    self.assertEqual((code, elapsed), (2, dispatch.WAIT_LIMIT_SECONDS), errors)
                    self.assertNotIn("outcome: deadline", output)
                    self.assertIn("Arc published no outcome", errors)
                    continue
                self.assertEqual((code, elapsed), (75, deadline), errors)
                lines = output.splitlines()
                self.assertIn("outcome: deadline", lines)
                after = lines[lines.index("outcome: deadline") + 1:]
                expected = ["--wait-launch", "launch-1", *(["--read-only"] if "--read-only" in arguments else [])]
                override = environ.get("DISPATCH_WAIT_LIMIT_SECONDS")
                prefix = [f"DISPATCH_WAIT_LIMIT_SECONDS={override}"] if override else []
                resume = next(line for line in after if line.startswith("await: "))[len("await: "):]
                words = shlex.split(resume)
                self.assertEqual(words[:len(prefix)], prefix)
                self.assertEqual(words[len(prefix) + 2:], expected)
                powershell = None
                if os.name == "nt":
                    label = "await (PowerShell): "
                    powershell = next(line for line in after if line.startswith(label))[len(label):]
                    self.assertTrue(powershell.startswith(
                        f"$env:DISPATCH_WAIT_LIMIT_SECONDS = '{override}'; & " if override else "& "), powershell)
                    self.assertTrue(powershell.endswith(" ".join(f"'{word}'" for word in expected)), powershell)
                self.assertEqual("await (Codex cell):" in after, dispatch.codex_parent(environ))
                if "await (Codex cell):" in after:
                    start = after[after.index("await (Codex cell):") + 2]
                    cell_command = json.loads(start.split("{cmd: ", 1)[1].split(", yield_time_ms", 1)[0])
                    self.assertEqual(cell_command, powershell or resume)
                self.assertIn("Never relaunch the worker", errors)

    def test_slow_arc_replies_end_the_observation_at_its_deadline(self):
        """A 5 minute parent's observation returns by its 240 second deadline when an Arc
        wait, the status reads or the result read run slow."""
        observed = {"launchId": "launch-1", "state": "observed", "eventId": "event-1", "outcome": "succeeded"}
        unsaved, saved = {"observation": {"monitor": "observing"}}, {"observation": {"exitCode": 0, "monitor": "finished"}}
        report = {"report": {"path": "r.md", "bytes": 7}, "text": "report\n"}
        # Each case: the seconds each Arc verb takes, and its reply. A slow wait spends a
        # minute of startup past the deadline it asks Arc for.
        cases = {
            "wait": {"wait": (lambda ms: ms / 1000 + 60, {"state": "deadline"})},
            "status": {"wait": (lambda ms: 100, observed), "status": (lambda ms: 30, unsaved)},
            "result": {"wait": (lambda ms: 100, observed), "status": (lambda ms: 1, saved),
                       "result": (lambda ms: 200, report)},
        }
        for slow, verbs in cases.items():
            with self.subTest(slow=slow):
                clock = [0.0]

                def run(command, **kwargs):
                    duration, reply = verbs[command[2]]
                    seconds = duration(int(command[-1]) if command[2] == "wait" else 0)
                    if seconds > kwargs["timeout"]:
                        clock[0] += kwargs["timeout"]
                        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                    clock[0] += seconds
                    return subprocess.CompletedProcess(command, 0, json.dumps(reply), "")

                output, errors = io.StringIO(), io.StringIO()
                with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"}, clear=True), \
                        unittest.mock.patch.object(dispatch, "resolve_bin", return_value="arc"), \
                        unittest.mock.patch.object(dispatch, "arc_command", return_value=["arc"]), \
                        unittest.mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                        unittest.mock.patch.object(dispatch.time, "sleep",
                                                   side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                        unittest.mock.patch.object(dispatch.subprocess, "run", side_effect=run), \
                        redirect_stdout(output), redirect_stderr(errors):
                    code = dispatch.main(["--wait-launch", "launch-1"])
                lines = output.getvalue().splitlines()
                self.assertEqual(code, 75, output.getvalue() + errors.getvalue())
                self.assertLessEqual(clock[0], 240)
                self.assertEqual([line for line in lines if line.startswith("outcome: ")], ["outcome: deadline"])
                self.assertTrue(any(line.startswith("await: ") for line in lines))

    def test_codex_parent_gets_one_cell_that_waits_until_the_observation_exits(self):
        node = shutil.which("node")
        for codex in (False, True):
            for claude in (False, True):
                environ = {**({"CODEX_THREAD_ID": "thread-1"} if codex else {}),
                           **({"CLAUDE_CODE_SESSION_ID": "session-1"} if claude else {})}
                with self.subTest(environ=environ):
                    output = io.StringIO()
                    with unittest.mock.patch.dict(os.environ, environ, clear=True), redirect_stdout(output):
                        dispatch.print_observer("launch-1", True)
                    lines = output.getvalue().splitlines()
                    self.assertEqual("await (Codex cell):" in lines, codex and not claude)
                    if "await (Codex cell):" not in lines:
                        continue
                    cell = lines[lines.index("await (Codex cell):") + 1:]
                    self.assertEqual(cell[-1], "")
                    cell = cell[:-1]
                    self.assertEqual(cell[0], '// @exec: {"yield_time_ms": 1500000}')
                    label = "await (PowerShell): " if os.name == "nt" else "await: "
                    command = next(line for line in lines if line.startswith(label))[len(label):]
                    self.assertTrue(command.endswith("'--wait-launch' 'launch-1' '--read-only'" if os.name == "nt"
                                                     else "--wait-launch launch-1 --read-only"))
                    if not node:
                        continue
                    # Run the cell against tools that answer one start and two reads.
                    harness = ("const calls = []; let printed = null;\n"
                               "const replies = [{session_id: 7, output: 'a'}, {session_id: 7, output: 'b'}, {output: 'c', exit_code: 75}];\n"
                               "const tools = {exec_command: async (a) => { calls.push(['exec', a.cmd]); return replies.shift(); },\n"
                               "  write_stdin: async (a) => { calls.push(['read', a.session_id, a.chars, a.yield_time_ms]); return replies.shift(); }};\n"
                               "const text = (value) => { printed = value; };\n"
                               "(async () => {\n" + "\n".join(cell) + "\nconsole.log(JSON.stringify({calls, printed}));\n})();\n")
                    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    ran = json.loads(result.stdout)
                    self.assertEqual(ran["calls"], [["exec", command], ["read", 7, "", 300000], ["read", 7, "", 300000]])
                    self.assertEqual(ran["printed"], "abcexit code: 75")

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
        for shorthand, model in (("sol", "gpt-6.1-sol"), ("terra", "gpt-6.1-terra"), ("gpt-5.6-terra", "gpt-5.6-terra"), ("luna", "gpt-6-luna"),
                                 ("gpt-6.1-sol", "gpt-6.1-sol")):
            self.assertEqual(self.resolve(shorthand), model)

    def test_shared_model_reference_matches_dispatch_shorthands(self):
        reference = (SKILL_DIR.parent / "references" / "models.md").read_text(encoding="utf-8")
        for shorthand, model in dispatch.CODEX_MODEL_SHORTHANDS.items():
            self.assertIn(f"`{shorthand}` selects `{model}`", reference)
            label = model.replace("gpt-", "GPT-", 1).replace(f"-{shorthand}", f" {shorthand.title()}")
            self.assertRegex(reference, rf"(?m)^\| `{re.escape(shorthand)}` \| {re.escape(label)} \|")

    @unittest.skipUnless(os.environ.get("ARC_REPO"), "set ARC_REPO to check Arc model shorthands")
    def test_arc_launch_shorthands_match_dispatch(self):
        launch_policy = (Path(os.environ["ARC_REPO"]) / "extension/src/launchpolicy.ts").read_text(encoding="utf-8")
        table = re.search(r"codexModelShorthands[^=]*=\s*\{([^}]*)\}", launch_policy, re.DOTALL)
        self.assertIsNotNone(table, "Arc shorthand table not found")
        for shorthand, model in dispatch.CODEX_MODEL_SHORTHANDS.items():
            self.assertRegex(table.group(1), rf"(?m)^\s*{re.escape(shorthand)}:\s*['\"]{re.escape(model)}['\"]\s*,")

    def test_bedrock_takes_the_openai_prefix_from_the_top_level_table_only(self):
        self.assertEqual(self.resolve("sol", 'model_provider = "amazon-bedrock"\n'), "openai.gpt-6.1-sol")
        self.assertEqual(self.resolve("sol", '[model_providers.x]\nmodel_provider = "amazon-bedrock"\n'), "gpt-6.1-sol")


class DefaultLogTests(unittest.TestCase):
    def test_named_launches_sharing_one_prompt_keep_separate_logs(self):
        self.assertEqual(dispatch.default_log("/r/prompt.md", "review opus/5"), "/r/prompt.review-opus-5.log")
        self.assertEqual(dispatch.default_log("/r/prompt.md", ""), "/r/prompt.log")


if __name__ == "__main__":
    unittest.main()
