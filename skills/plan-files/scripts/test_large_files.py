"""Tests for large-files.py. Run with: python3 -m unittest discover -s scripts"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "large-files.py")
spec = importlib.util.spec_from_file_location("large_files", SCRIPT)
large_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(large_files)
MB = 1024 * 1024


def make_link(link: str, target: str) -> None:
    if os.name == "nt":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
            check=True,
        )
    else:
        os.symlink(target, link, target_is_directory=True)


class LargeFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="large-files-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.folder = os.path.join(self.root, "plan")
        os.mkdir(self.folder)

    def write(self, name: str, size: int, base: str | None = None) -> str:
        path = os.path.join(base or self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.truncate(size)
        return path

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, SCRIPT, self.folder], capture_output=True, text=True)

    def test_lists_a_file_over_two_megabytes_and_ignores_a_smaller_one(self) -> None:
        large = self.write(os.path.join("probes", "trace.json"), 3 * MB)
        small = self.write("capture.png", 1 * MB)
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"3.0\t{large}"])
        self.assertNotIn(small, result.stdout)

    def test_reports_nothing_for_small_files(self) -> None:
        self.write("plan.md", 4096)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_lists_a_folder_whose_small_files_add_up_past_five_megabytes(self) -> None:
        for index in range(4):
            self.write(os.path.join("runs", f"stream-{index}.jsonl"), int(1.5 * MB))
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"6.0\t{self.folder}\tfolder total"])

    def test_never_enters_a_link_or_junction(self) -> None:
        outside = os.path.join(self.root, "dependencies")
        self.write("bundle.js", 3 * MB, base=outside)
        make_link(os.path.join(self.folder, "node_modules"), outside)
        self.write("plan.md", 4096)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_fails_on_a_folder_it_cannot_read(self) -> None:
        self.write(os.path.join("locked", "trace.json"), 3 * MB)
        locked = os.path.normcase(os.path.join(self.folder, "locked"))
        listdir, scandir = os.listdir, os.scandir

        def refuse(real):
            def read(path="."):
                if os.path.normcase(os.path.normpath(os.fspath(path))) == locked:
                    raise PermissionError(13, "Access is denied", os.fspath(path))
                return real(path)
            return read

        stderr = io.StringIO()
        with mock.patch.object(large_files.os, "listdir", refuse(listdir)), \
                mock.patch.object(large_files.os, "scandir", refuse(scandir)), \
                contextlib.redirect_stderr(stderr):
            code = large_files.main([self.folder])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
