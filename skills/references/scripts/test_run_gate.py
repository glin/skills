"""Tests for run-gate.py.

Each test builds a repository in its own temporary directory and removes that directory on
every exit. The receipt and the log sit beside the repository, outside its tree.

Run with: python3 -m pytest skills/references/scripts
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_land_preflight import GIT_ENV, git, write

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-gate.py")
PREFLIGHT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "land-preflight.py")


def python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


class RunGateTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory(prefix="run-gate-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.repository = os.path.join(self.root, "repository")
        os.mkdir(self.repository)
        git(self.repository, "init", "-q", "-b", "main")
        write(os.path.join(self.repository, ".gitignore"), "build/\n")
        write(os.path.join(self.repository, "base.txt"), "base\n")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "base")
        self.commit = git(self.repository, "rev-parse", "HEAD")
        self.receipt = os.path.join(self.root, "run", "gate-receipt.json")
        self.marker = os.path.join(self.root, "gate-ran")
        self.mark = python_command(f"open(r'{Path(self.marker).as_posix()}', 'w')")

    def run_gate(self, command: str, commit: str | None = None, receipt: str | None = None) -> int:
        return subprocess.run(
            [sys.executable, SCRIPT, "--tree", self.repository, "--commit", commit or self.commit,
             "--command", command, "--receipt", receipt or self.receipt],
            capture_output=True, text=True,
        ).returncode

    def test_writes_a_receipt_that_preflight_accepts(self) -> None:
        candidate_tree = os.path.join(self.root, "candidate")
        git(self.repository, "worktree", "add", "-q", "-b", "topic", candidate_tree, self.commit)
        write(os.path.join(candidate_tree, "feature.txt"))
        git(candidate_tree, "add", "feature.txt")
        git(candidate_tree, "commit", "-q", "-m", "feature")
        candidate = git(candidate_tree, "rev-parse", "HEAD")
        gate = subprocess.run(
            [sys.executable, SCRIPT, "--tree", candidate_tree, "--commit", candidate,
             "--command", python_command("print('ok')"), "--receipt", self.receipt],
            capture_output=True, text=True,
        )
        self.assertEqual(gate.returncode, 0, gate.stderr)
        with open(self.receipt, encoding="utf-8") as file:
            record = json.load(file)
        self.assertEqual(record["commit"], candidate)
        with open(record["log"], encoding="utf-8") as file:
            self.assertIn("ok", file.read())
        preflight = subprocess.run(
            [sys.executable, PREFLIGHT, "--repository", self.repository, "--commit", candidate,
             "--expected-branch", "main", "--gate-receipt", self.receipt,
             "--gate-command", python_command("print('ok')")],
            capture_output=True, text=True,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stdout)

    def test_a_failed_gate_removes_an_earlier_receipt(self) -> None:
        self.assertEqual(self.run_gate(python_command("pass")), 0)
        self.assertEqual(self.run_gate(python_command("import sys; sys.exit(3)")), 1)
        self.assertFalse(os.path.exists(self.receipt))

    def test_runs_the_command_under_sh_on_every_platform(self) -> None:
        self.assertEqual(self.run_gate("echo tests; exit 3"), 1)
        self.assertFalse(os.path.exists(self.receipt))

    def test_refuses_a_tree_that_holds_another_commit(self) -> None:
        write(os.path.join(self.repository, "next.txt"))
        git(self.repository, "add", "next.txt")
        git(self.repository, "commit", "-q", "-m", "next")
        self.assertEqual(self.run_gate(self.mark), 1)
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(self.receipt))

    def test_refuses_an_untracked_file(self) -> None:
        write(os.path.join(self.repository, "draft.txt"))
        self.assertEqual(self.run_gate(self.mark), 1)
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(self.receipt))

    def test_fails_a_gate_that_edits_a_tracked_file(self) -> None:
        edit = "open('base.txt', 'w').write('changed')"
        self.assertEqual(self.run_gate(python_command(edit)), 1)
        self.assertFalse(os.path.exists(self.receipt))

    def test_passes_a_gate_that_writes_ignored_output(self) -> None:
        build = "import os; os.makedirs('build'); open('build/out.txt', 'w').write('x')"
        self.assertEqual(self.run_gate(python_command(build)), 0)

    def test_refuses_a_receipt_inside_the_tree_and_keeps_that_file(self) -> None:
        inside = os.path.join(self.repository, "base.txt")
        self.assertEqual(self.run_gate(self.mark, receipt=inside), 2)
        self.assertFalse(os.path.exists(self.marker))
        with open(inside, encoding="utf-8") as file:
            self.assertEqual(file.read(), "base\n")


if __name__ == "__main__":
    unittest.main()
