#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(__file__), "digest.py")
SPEC = importlib.util.spec_from_file_location("digest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MANY = 250


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()


def write(repo: str, path: str, text: str) -> None:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class DigestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = tempfile.mkdtemp(prefix="digest-test-")
        git(cls.repo, "init", "-q")
        git(cls.repo, "config", "user.email", "t@example.com")
        git(cls.repo, "config", "user.name", "t")
        git(cls.repo, "config", "core.autocrlf", "false")
        write(cls.repo, "src/store.ts", "export function oldLoader(key: string) {\n  return key;\n}\n")
        write(cls.repo, "src/view.ts", "import { oldLoader } from './store';\noldLoader('a');\n")
        write(cls.repo, "docs/notes.md", "The `oldLoader` reads a key.\n")
        write(cls.repo, "src/legacy.ts", "// freshLoader will replace this loader.\n")
        write(cls.repo, "src/config.ts", "export const limit = 5;\n")
        write(cls.repo, "src/use.ts", "if (count > limit) stop();\n")
        write(cls.repo, "package-lock.json", '{"lockfileVersion": 3}\n')
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "base")
        cls.base = git(cls.repo, "rev-parse", "HEAD")
        # The candidate renames oldLoader to freshLoader in store.ts only, changes the line
        # that declares limit, adds a caller of the new name in a new file, declares many
        # names in one new file, and changes the lock file. legacy.ts already names
        # freshLoader and stays untouched.
        write(cls.repo, "src/store.ts", "export function freshLoader(key: string) {\n  return key.trim();\n}\n")
        write(cls.repo, "src/config.ts", "export const limit = 6;\n")
        write(cls.repo, "src/extra.ts", "export const unrelatedValue = 1;\n")
        write(cls.repo, "src/caller.ts", "freshLoader('b');\n")
        write(cls.repo, "src/many.py", "".join(f"name_{i:03d} = {i}\n" for i in range(MANY)))
        write(cls.repo, "src/form.txt", "first\x0cstill first\nsecond\n")
        write(cls.repo, "package-lock.json", '{"lockfileVersion": 4}\n')
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "candidate")
        cls.candidate = git(cls.repo, "rev-parse", "HEAD")
        cls.digest, cls.stats = MODULE.build(cls.repo, cls.base, cls.candidate)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.repo, ignore_errors=True)

    def section(self, title: str, digest: str | None = None) -> str:
        digest = digest or self.digest
        start = digest.index(f"\n## {title}\n")
        rest = digest[start + 1:]
        end = rest.find("\n## ", 1)
        return rest if end == -1 else rest[:end]

    def test_sections_appear_in_the_fixed_order(self) -> None:
        titles = [line[3:] for line in self.digest.split("\n") if line.startswith("## ")]
        self.assertEqual(titles, MODULE.SECTIONS)

    def test_a_deleted_symbol_lists_the_consumers_the_diff_left_behind(self) -> None:
        refs = self.section("Symbol references")
        self.assertIn("### `oldLoader` (deleted, 3 lines)", refs)
        self.assertIn("src/view.ts:1:import { oldLoader } from './store';", refs)
        self.assertIn("src/view.ts:2:oldLoader('a');", refs)
        self.assertIn("docs/notes.md:1:The `oldLoader` reads a key.", refs)

    def test_an_added_symbol_lists_references_outside_the_changed_files_only(self) -> None:
        refs = self.section("Symbol references")
        self.assertIn("### `freshLoader` (added, 1 line)", refs)
        self.assertIn("src/legacy.ts:1:// freshLoader will replace this loader.", refs)
        # caller.ts is itself a changed file, so its text sits under the full files instead.
        self.assertNotIn("src/caller.ts:", refs)
        self.assertNotIn("src/store.ts:", refs)

    def test_a_plain_name_whose_declaration_line_changed_is_a_symbol(self) -> None:
        refs = self.section("Symbol references")
        self.assertIn("### `limit` (changed, 1 line)", refs)
        self.assertIn("src/use.ts:1:if (count > limit) stop();", refs)

    def test_a_symbol_with_no_outside_reference_is_named_once(self) -> None:
        self.assertIn("No reference outside the changed files: ", self.section("Symbol references"))
        self.assertIn("`unrelatedValue`", self.section("Symbol references"))

    def test_every_declared_name_is_searched_however_many_there_are(self) -> None:
        self.assertGreaterEqual(self.stats["symbols"], MANY)
        self.assertIn(f"`name_{MANY - 1:03d}`", self.section("Symbol references"))

    def test_keywords_and_called_names_are_not_symbols(self) -> None:
        refs = self.section("Symbol references")
        for word in ("export", "function", "string", "return", "trim", "stop"):
            self.assertNotIn(f"`{word}`", refs)

    def test_a_symbol_lists_capped_lines_and_counts_the_rest(self) -> None:
        with mock.patch.object(MODULE, "MAX_HITS_PER_SYMBOL", 1):
            digest, _ = MODULE.build(self.repo, self.base, self.candidate)
        refs = self.section("Symbol references", digest)
        self.assertIn("### `oldLoader` (deleted, 3 lines)", refs)
        self.assertIn("... 2 more lines", refs)

    def test_changed_files_carry_full_text_numbered_as_git_numbers_lines(self) -> None:
        files = self.section("Files at the candidate")
        self.assertIn("     2    return key.trim();", files)
        # A form feed inside a line does not start a new line.
        self.assertIn("     2  second", files)

    def test_the_lock_file_gets_one_line_and_nothing_else(self) -> None:
        self.assertIn("`package-lock.json` M +1 -1 (generated, not included)", self.section("Changed files"))
        self.assertNotIn("package-lock.json", self.section("Files at the candidate"))
        self.assertNotIn("lockfileVersion", self.section("Diff"))

    def test_the_digest_and_its_not_included_list_stay_within_the_budget(self) -> None:
        full = len(self.digest.encode("utf-8"))
        for cap in range(full // 3, full, full // 10):
            digest, stats = MODULE.build(self.repo, self.base, self.candidate, max_bytes=cap)
            self.assertLessEqual(stats["bytes"], cap)
            self.assertEqual(stats["bytes"], len(digest.encode("utf-8")))
            self.assertEqual(len(stats["omitted"]), self.section("Not included", digest).count("\n- "))

    def test_full_files_leave_first_and_the_largest_before_the_smaller(self) -> None:
        small, stats = MODULE.build(self.repo, self.base, self.candidate, max_bytes=len(self.digest.encode("utf-8")) - 60)
        self.assertEqual(stats["omitted"], ["Files at the candidate: src/many.py"])
        self.assertIn("- Files at the candidate: `src/many.py`", small)
        self.assertIn("### `src/store.ts`", self.section("Files at the candidate", small))

    def test_a_failed_search_stops_the_script(self) -> None:
        with self.assertRaises(RuntimeError):
            MODULE.symbol_hits(self.repo, "0" * 40, ["freshLoader"], set())

    def test_the_command_line_writes_the_file_and_reports_its_size(self) -> None:
        out = os.path.join(self.repo, "..", os.path.basename(self.repo) + "-digest.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, "--repo", self.repo, "--base", self.base, "--candidate", self.candidate, "--out", out],
            check=True, capture_output=True, text=True,
        )
        try:
            stats = json.loads(result.stderr.strip().splitlines()[-1])
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().encode("utf-8")), stats["bytes"])
            self.assertEqual(stats["omitted"], [])
        finally:
            os.remove(out)


if __name__ == "__main__":
    unittest.main()
