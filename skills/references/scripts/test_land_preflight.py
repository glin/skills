"""Tests for land-preflight.py.

Each test builds a repository in its own temporary directory and removes that directory on
every exit. The main branch stands in for the landing target, and the reviewed commits sit on
a topic branch built from it.

Run with: python3 -m pytest skills/references/scripts
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "land-preflight.py")
GATE = "npm test"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(directory: str, *arguments: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", directory, *arguments], check=check, capture_output=True, text=True
    ).stdout.strip()


def write(path: str, text: str = "x\n") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n") as file:
        file.write(text)
    return path


class LandPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory(prefix="land-preflight-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.repository = os.path.join(os.path.realpath(temporary.name), "repository")
        os.mkdir(self.repository)
        git(self.repository, "init", "-q", "-b", "main")
        self.commit_file("base.txt", "base\n", "base")
        self.base = git(self.repository, "rev-parse", "HEAD")

    def commit_file(self, name: str, text: str, message: str) -> str:
        write(os.path.join(self.repository, name), text)
        git(self.repository, "add", name)
        git(self.repository, "commit", "-q", "-m", message)
        return git(self.repository, "rev-parse", "HEAD")

    def build_topic(self, *files: str) -> str:
        """Commit one file per name on a topic branch from the base, then return to main."""
        git(self.repository, "switch", "-q", "-c", "topic", self.base)
        for index, name in enumerate(files):
            head = self.commit_file(name, f"topic {index}\n", f"topic {index}")
        git(self.repository, "switch", "-q", "main")
        return head

    def run_script(self, commit: str, *extra: str, branch: str = "main") -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, SCRIPT, "--repository", self.repository, "--commit", commit,
             "--expected-branch", branch, *extra],
            capture_output=True, text=True,
        )
        return result.returncode, json.loads(result.stdout) if result.stdout.strip() else {}

    def assert_blocked(self, code: int, report: dict, blocker: str) -> None:
        self.assertEqual(code, 1, report)
        self.assertIn(blocker, report["blockers"])
        self.assertFalse(report["can_fast_forward"])

    def test_passes_a_single_commit_on_the_target_head(self) -> None:
        candidate = self.build_topic("feature.txt")
        code, report = self.run_script(candidate)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["can_fast_forward"])
        self.assertTrue(report["parent_matches_target"])
        self.assertEqual(report["commit_paths"], ["feature.txt"])
        self.assertIsNone(report["base"])

    def test_passes_a_dirty_path_the_commit_does_not_touch(self) -> None:
        candidate = self.build_topic("feature.txt")
        write(os.path.join(self.repository, "base.txt"), "edited\n")
        write(os.path.join(self.repository, "notes.txt"), "untracked\n")
        code, report = self.run_script(candidate)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["unstaged_paths"], ["base.txt"])

    def test_blocks_a_detached_head(self) -> None:
        candidate = self.build_topic("feature.txt")
        git(self.repository, "switch", "-q", "--detach", "main")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "The target checkout has a detached HEAD.")

    def test_blocks_the_wrong_branch(self) -> None:
        candidate = self.build_topic("feature.txt")
        git(self.repository, "switch", "-q", "-c", "other")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "The target checkout is on other, not expected branch main.")

    def test_blocks_a_merge_commit(self) -> None:
        self.build_topic("feature.txt")
        self.commit_file("main.txt", "main\n", "main moves")
        git(self.repository, "merge", "-q", "--no-ff", "-m", "merge", "topic")
        merge = git(self.repository, "rev-parse", "HEAD")
        git(self.repository, "reset", "-q", "--hard", "HEAD~1")
        code, report = self.run_script(merge)
        self.assert_blocked(code, report, "The reviewed commit does not have exactly one parent.")

    def test_blocks_a_parent_that_is_not_the_target_head(self) -> None:
        candidate = self.build_topic("feature.txt")
        self.commit_file("main.txt", "main\n", "main moves")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "The reviewed commit parent does not equal the target HEAD.")
        self.assertFalse(report["parent_matches_target"])
        self.assertEqual(report["reviewed_commit_parent"], self.base)

    def test_blocks_staged_files(self) -> None:
        candidate = self.build_topic("feature.txt")
        write(os.path.join(self.repository, "base.txt"), "edited\n")
        git(self.repository, "add", "base.txt")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "The target index contains staged files.")

    def test_blocks_a_dirty_path_the_commit_changes(self) -> None:
        candidate = self.build_topic("base.txt")
        write(os.path.join(self.repository, "base.txt"), "edited\n")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "Dirty tracked paths overlap reviewed paths.")
        self.assertEqual(report["overlapping_tracked_paths"], ["base.txt"])

    def test_blocks_an_untracked_path_under_a_committed_path(self) -> None:
        candidate = self.build_topic("tool")
        write(os.path.join(self.repository, "tool", "draft.txt"), "untracked\n")
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "Untracked paths collide with reviewed paths.")
        self.assertEqual(report["colliding_untracked_paths"], ["tool/draft.txt"])

    def test_blocks_unmerged_files(self) -> None:
        candidate = self.build_topic("feature.txt")
        git(self.repository, "switch", "-q", "-c", "side", self.base)
        self.commit_file("base.txt", "side\n", "side")
        git(self.repository, "switch", "-q", "main")
        self.commit_file("base.txt", "main\n", "main moves")
        git(self.repository, "merge", "-q", "side", check=False)
        code, report = self.run_script(candidate)
        self.assert_blocked(code, report, "The target checkout contains unmerged files.")
        self.assertEqual(report["unmerged_paths"], ["base.txt"])

    def test_reports_an_unknown_commit_as_an_error(self) -> None:
        code, report = self.run_script("no-such-ref")
        self.assertEqual(code, 2)
        self.assertEqual(report, {})

    def test_base_passes_a_fix_commit_on_top_of_the_candidate(self) -> None:
        fix = self.build_topic("feature.txt", "fix.txt")
        code, report = self.run_script(fix, "--base", self.base)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["base_matches_target"])
        self.assertIsNone(report["parent_matches_target"])
        self.assertEqual(report["commit_paths"], ["feature.txt", "fix.txt"])
        self.assertEqual(len(report["range_commits"]), 2)
        self.assertEqual(report["range_commits"][-1], fix)

    def test_base_blocks_a_base_that_is_not_the_target_head(self) -> None:
        fix = self.build_topic("feature.txt", "fix.txt")
        self.commit_file("main.txt", "main\n", "main moves")
        code, report = self.run_script(fix, "--base", self.base)
        self.assert_blocked(code, report, "The base does not equal the target HEAD.")
        self.assertFalse(report["base_matches_target"])

    def test_base_blocks_a_merge_commit_in_the_range(self) -> None:
        git(self.repository, "switch", "-q", "-c", "side", self.base)
        self.commit_file("side.txt", "side\n", "side")
        git(self.repository, "switch", "-q", "-c", "topic", self.base)
        self.commit_file("feature.txt", "feature\n", "candidate")
        git(self.repository, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        fix = self.commit_file("fix.txt", "fix\n", "fix")
        git(self.repository, "switch", "-q", "main")
        code, report = self.run_script(fix, "--base", self.base)
        self.assert_blocked(
            code, report,
            "The range from the base to the reviewed commit holds a commit without exactly one parent.",
        )
        self.assertEqual(len(report["range_merge_commits"]), 1)

    def test_base_blocks_a_dirty_path_only_an_earlier_commit_changes(self) -> None:
        fix = self.build_topic("base.txt", "fix.txt")
        write(os.path.join(self.repository, "base.txt"), "edited\n")
        code, report = self.run_script(fix, "--base", self.base)
        self.assert_blocked(code, report, "Dirty tracked paths overlap reviewed paths.")
        self.assertEqual(report["overlapping_tracked_paths"], ["base.txt"])

    def test_base_blocks_a_dirty_old_name_of_a_renamed_path(self) -> None:
        git(self.repository, "switch", "-q", "-c", "topic", self.base)
        self.commit_file("feature.txt", "feature\n", "candidate")
        git(self.repository, "mv", "base.txt", "renamed.txt")
        git(self.repository, "commit", "-q", "-m", "fix renames base.txt")
        fix = git(self.repository, "rev-parse", "HEAD")
        git(self.repository, "switch", "-q", "main")
        write(os.path.join(self.repository, "base.txt"), "edited\n")
        code, report = self.run_script(fix, "--base", self.base)
        self.assert_blocked(code, report, "Dirty tracked paths overlap reviewed paths.")
        self.assertEqual(report["overlapping_tracked_paths"], ["base.txt"])

    def test_base_blocks_an_untracked_path_under_a_path_an_earlier_commit_adds(self) -> None:
        fix = self.build_topic("tool", "fix.txt")
        write(os.path.join(self.repository, "tool", "draft.txt"), "untracked\n")
        code, report = self.run_script(fix, "--base", self.base)
        self.assert_blocked(code, report, "Untracked paths collide with reviewed paths.")
        self.assertEqual(report["colliding_untracked_paths"], ["tool/draft.txt"])

    def test_base_blocks_a_base_that_is_not_an_ancestor(self) -> None:
        fix = self.build_topic("feature.txt", "fix.txt")
        git(self.repository, "switch", "-q", "-c", "elsewhere", self.base)
        elsewhere = self.commit_file("other.txt", "other\n", "elsewhere")
        git(self.repository, "switch", "-q", "main")
        git(self.repository, "reset", "-q", "--hard", elsewhere)
        code, report = self.run_script(fix, "--base", elsewhere)
        self.assert_blocked(code, report, "The base is not an ancestor of the reviewed commit.")

    def test_base_blocks_an_empty_range(self) -> None:
        code, report = self.run_script(self.base, "--base", self.base)
        self.assert_blocked(
            code, report, "The range from the base to the reviewed commit holds no commit."
        )

    def write_receipt(self, commit: str, exit_code: int = 0, command: str = GATE) -> str:
        receipt = os.path.join(os.path.dirname(self.repository), "gate-receipt.json")
        write(receipt, json.dumps({"commit": commit, "exit_code": exit_code, "command": command}))
        return receipt

    def run_gated(self, commit: str, receipt: str) -> tuple[int, dict]:
        return self.run_script(commit, "--gate-receipt", receipt, "--gate-command", GATE)

    def test_gate_receipt_passes_a_passing_gate_on_the_reviewed_commit(self) -> None:
        candidate = self.build_topic("feature.txt")
        receipt = self.write_receipt(candidate)
        code, report = self.run_gated(candidate, receipt)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["gate_receipt"], receipt)

    def test_gate_receipt_blocks_a_missing_receipt(self) -> None:
        candidate = self.build_topic("feature.txt")
        missing = os.path.join(os.path.dirname(self.repository), "none.json")
        code, report = self.run_gated(candidate, missing)
        self.assert_blocked(code, report, "The gate receipt is missing.")

    def test_gate_receipt_blocks_a_failed_gate(self) -> None:
        candidate = self.build_topic("feature.txt")
        code, report = self.run_gated(candidate, self.write_receipt(candidate, exit_code=1))
        self.assert_blocked(code, report, "The gate receipt does not record a passing gate.")

    def test_gate_receipt_blocks_a_narrowed_gate_command(self) -> None:
        candidate = self.build_topic("feature.txt")
        receipt = self.write_receipt(candidate, command="npm run test:cli")
        code, report = self.run_gated(candidate, receipt)
        self.assert_blocked(code, report, "The gate receipt records another gate command.")

    def test_gate_receipt_blocks_a_receipt_for_the_commit_before_a_rebase(self) -> None:
        candidate = self.build_topic("feature.txt")
        receipt = self.write_receipt(candidate)
        self.commit_file("main.txt", "main\n", "main moves")
        git(self.repository, "switch", "-q", "topic")
        git(self.repository, "rebase", "-q", "main")
        rebased = git(self.repository, "rev-parse", "HEAD")
        git(self.repository, "switch", "-q", "main")
        code, report = self.run_gated(rebased, receipt)
        self.assert_blocked(code, report, "The gate receipt names another commit.")

    def test_gate_receipt_requires_the_gate_command(self) -> None:
        candidate = self.build_topic("feature.txt")
        code, report = self.run_script(candidate, "--gate-receipt", self.write_receipt(candidate))
        self.assertEqual(code, 2)
        self.assertEqual(report, {})

if __name__ == "__main__":
    unittest.main()
