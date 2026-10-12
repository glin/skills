"""Tests for carry-review.sh.

Each test builds a repository in its own temporary directory and removes that directory on
every exit. A candidate commit sits on an old base, the target then moves to a new base, and
a rebased commit carries the candidate onto the new base. The tests skip when no bash runs.
On Windows they use the bash that ships with Git for Windows.

Run with: python3 -m pytest skills/references/scripts
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carry-review.sh")

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

LINES = "".join(f"line {number}\n" for number in range(1, 41))


def find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    # System32 bash.exe starts WSL, which cannot read these Windows paths. Git for Windows
    # keeps its bash in the bin folder of its install root, above git.exe.
    git = shutil.which("git")
    if git is None:
        return None
    for folder in Path(git).resolve().parents:
        candidate = folder / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


BASH = find_bash()


def git(directory: str, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", directory, *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(path: str, text: str) -> None:
    with open(path, "w", newline="\n") as file:
        file.write(text)


def posix(path: str) -> str:
    return path.replace("\\", "/")


@unittest.skipIf(BASH is None, "bash is not available")
class CarryReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory(prefix="carry-review-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.repository = os.path.join(self.root, "repository")
        self.out = os.path.join(self.root, "evidence")
        os.mkdir(self.repository)
        git(self.repository, "init", "-q", "-b", "main")
        for name in ("a.txt", "b.txt"):
            write(os.path.join(self.repository, name), LINES)
        self.old_base = self.commit("base")
        self.candidate = self.edit_and_commit(
            "a.txt", "line 2\n", "line 2 candidate\n", "candidate", start=self.old_base
        )

    def commit(self, message: str) -> str:
        git(self.repository, "add", "-A")
        git(self.repository, "commit", "-q", "-m", message)
        return git(self.repository, "rev-parse", "HEAD")

    def edit_and_commit(self, name: str, old: str, new: str, message: str, *, start: str) -> str:
        git(self.repository, "switch", "-q", "--detach", start)
        path = os.path.join(self.repository, name)
        with open(path, newline="") as file:
            text = file.read()
        self.assertIn(old, text)
        write(path, text.replace(old, new, 1))
        return self.commit(message)

    def move_target(self, name: str, old: str, new: str) -> str:
        return self.edit_and_commit(name, old, new, "target moves", start=self.old_base)

    def rebase_candidate(self, new_base: str) -> str:
        git(self.repository, "switch", "-q", "--detach", new_base)
        git(self.repository, "cherry-pick", self.candidate)
        return git(self.repository, "rev-parse", "HEAD")

    def run_script(
        self, new_base: str, rebased: str, *extra: str, out: bool = True, repo: str | None = None
    ) -> tuple[int, str]:
        arguments = [
            BASH, SCRIPT, "--repo", posix(self.repository if repo is None else repo), "--old-base", self.old_base,
            "--candidate", self.candidate, "--new-base", new_base, "--rebased", rebased, *extra,
        ]
        if out:
            arguments += ["--out", posix(self.out)]
        result = subprocess.run(arguments, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    def assert_verdict(self, code: int, output: str, verdict: str) -> None:
        self.assertIn(f"VERDICT={verdict}\n", output)
        self.assertEqual(code, 0 if verdict == "disjoint" else 1, output)

    def disjoint_rebase(self) -> tuple[str, str]:
        new_base = self.move_target("b.txt", "line 30\n", "line 30 target\n")
        return new_base, self.rebase_candidate(new_base)

    def test_disjoint_when_the_target_changed_other_files_and_the_gate_passed(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "true")
        self.assert_verdict(code, output, "disjoint")
        self.assertIn("patch: identical across the rebase", output)

    def test_unverified_when_no_gate_ran(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased)
        self.assert_verdict(code, output, "unverified")

    def test_verify_failed_when_the_gate_fails(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "false")
        self.assert_verdict(code, output, "verify-failed")
        self.assertTrue(os.path.isfile(os.path.join(self.out, "verify.log")))

    def test_conflict_when_the_rebase_needed_a_hand_resolution(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "true", "--conflicted")
        self.assert_verdict(code, output, "conflict")

    def test_conflict_outranks_a_failed_gate(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "false", "--conflicted")
        self.assert_verdict(code, output, "conflict")

    def test_overlap_when_both_sides_changed_the_same_file(self) -> None:
        new_base = self.move_target("a.txt", "line 30\n", "line 30 target\n")
        rebased = self.rebase_candidate(new_base)
        code, output = self.run_script(new_base, rebased, "--verify", "true")
        self.assert_verdict(code, output, "overlap")
        self.assertIn("  overlap: a.txt", output)

    def test_patch_differs_when_the_rebased_patch_changed(self) -> None:
        new_base = self.move_target("b.txt", "line 30\n", "line 30 target\n")
        rebased = self.edit_and_commit(
            "a.txt", "line 2\n", "line 2 edited during the rebase\n", "rebased", start=new_base
        )
        code, output = self.run_script(new_base, rebased, "--verify", "true")
        self.assert_verdict(code, output, "patch-differs")
        self.assertTrue(os.path.isfile(os.path.join(self.out, "patch-delta.txt")))

    def test_unverified_outranks_an_overlap_when_no_gate_ran(self) -> None:
        new_base = self.move_target("a.txt", "line 30\n", "line 30 target\n")
        rebased = self.rebase_candidate(new_base)
        code, output = self.run_script(new_base, rebased)
        self.assert_verdict(code, output, "unverified")
        self.assertIn("  overlap: a.txt", output)

    def test_unverified_outranks_a_changed_patch_when_no_gate_ran(self) -> None:
        new_base = self.move_target("b.txt", "line 30\n", "line 30 target\n")
        rebased = self.edit_and_commit(
            "a.txt", "line 2\n", "line 2 edited during the rebase\n", "rebased", start=new_base
        )
        code, output = self.run_script(new_base, rebased)
        self.assert_verdict(code, output, "unverified")
        self.assertIn("patch: changed across the rebase", output)

    def test_a_failed_gate_outranks_an_overlap(self) -> None:
        new_base = self.move_target("a.txt", "line 30\n", "line 30 target\n")
        rebased = self.rebase_candidate(new_base)
        code, output = self.run_script(new_base, rebased, "--verify", "false")
        self.assert_verdict(code, output, "verify-failed")

    def test_default_evidence_folder_sits_in_the_git_directory(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "true", out=False)
        self.assert_verdict(code, output, "disjoint")
        short = git(self.repository, "rev-parse", "--short=12", rebased)
        folder = os.path.join(self.repository, ".git", "carry-review", short)
        self.assertTrue(os.path.isfile(os.path.join(folder, "candidate.diff")), output)

    def test_usage_error_when_a_required_argument_is_missing(self) -> None:
        result = subprocess.run(
            [BASH, SCRIPT, "--repo", posix(self.repository)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--old-base is required", result.stderr)

    def test_usage_error_on_an_unknown_argument(self) -> None:
        code, output = self.run_script(self.old_base, self.candidate, "--verify", "true", "--bogus")
        self.assertEqual(code, 2, output)
        self.assertIn("unknown argument --bogus", output)
        self.assertFalse(os.path.exists(self.out))

    def test_usage_error_when_the_repository_cannot_be_entered(self) -> None:
        missing = os.path.join(self.root, "missing")
        code, output = self.run_script(self.old_base, self.candidate, "--verify", "true", repo=missing)
        self.assertEqual(code, 2, output)
        self.assertIn(f"cannot enter {posix(missing)}", output)
        self.assertFalse(os.path.exists(self.out))

    def test_usage_error_when_the_rebased_revision_is_not_a_commit(self) -> None:
        for out in (True, False):
            with self.subTest(out=out):
                code, output = self.run_script(self.old_base, "no-such-commit", "--verify", "true", out=out)
                self.assertEqual(code, 2, output)
                self.assertIn("no-such-commit is not a commit", output)
                self.assertNotIn("VERDICT=", output)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(os.path.join(self.repository, ".git", "carry-review")))

    def test_a_rerun_into_the_default_folder_replaces_the_earlier_evidence(self) -> None:
        new_base, rebased = self.disjoint_rebase()
        short = git(self.repository, "rev-parse", "--short=12", rebased)
        folder = os.path.join(self.repository, ".git", "carry-review", short)
        code, output = self.run_script(new_base, rebased, "--verify", "false", out=False)
        self.assert_verdict(code, output, "verify-failed")
        self.assertTrue(os.path.isfile(os.path.join(folder, "verify.log")), output)
        code, output = self.run_script(new_base, rebased, out=False)
        self.assert_verdict(code, output, "unverified")
        self.assertFalse(os.path.exists(os.path.join(folder, "verify.log")), output)
        self.assertTrue(os.path.isfile(os.path.join(folder, "candidate.diff")), output)

    def test_verify_failed_when_the_gate_outlives_its_timeout(self) -> None:
        probe = subprocess.run([BASH, "-c", "command -v timeout"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("the timeout command is not available")
        new_base, rebased = self.disjoint_rebase()
        code, output = self.run_script(new_base, rebased, "--verify", "sleep 5", "--verify-timeout", "1")
        self.assert_verdict(code, output, "verify-failed")
        self.assertIn("verify: TIMEOUT after 1s", output)


if __name__ == "__main__":
    unittest.main()
