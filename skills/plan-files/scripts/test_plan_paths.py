"""Tests for plan_paths.py. Run with: python3 -m unittest discover -s scripts -p 'test_plan_paths.py'"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan_paths.py")
spec = importlib.util.spec_from_file_location("plan_paths", SCRIPT)
plan_paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan_paths)

AGENT = "claude-test[200k] (effort high, claude-code)"
SESSION = "11111111-2222-4333-8444-555555555555"
OTHER_SESSION = "99999999-8888-4777-8666-555555555555"
OLD_DATE = "2026-01-01T00:00:00+0000"
OLD_TIME = 1767225600  # OLD_DATE as Unix time, the mtime of settled run files
NOW = 1791547200  # 2026-10-09T12:00:00Z, the clock that the recent-activity tests hold
PRE_COMMIT = """#!/bin/sh
case "$(basename "${GIT_INDEX_FILE:-index}")" in
  index | index.lock) echo "commit from the shared index" >&2; exit 1 ;;
esac
exit 0
"""
COMMIT_MSG = """#!/bin/sh
grep -q '^Agent: ' "$1" && grep -q '^Agent-Session: ' "$1" && exit 0
echo "missing trailers" >&2
exit 1
"""
RUN = "2026-09-05-alpha/runs/2026-09-06-x-010203-0123456789abcdef"


class Interrupt(Exception):
    pass


def git(root: str, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, encoding="utf-8", env={**os.environ, **(env or {})}
    )
    if result.returncode:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout


def write(root: str, rel: str, text: str | bytes) -> None:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file:
        file.write(text.encode() if isinstance(text, str) else text)
    if "runs" in rel.split("/")[:-1]:
        os.utime(path, (OLD_TIME, OLD_TIME))


def read(root: str, rel: str) -> str:
    with open(os.path.join(root, *rel.split("/")), "rb") as file:
        return file.read().decode()


def commit_all(root: str, subject: str, session: str = OTHER_SESSION, date: str = OLD_DATE) -> None:
    git(root, "add", "-A")
    message = f"{subject}\n\nAgent: fixture (effort none, test)\nAgent-Session: {session}\n"
    git(root, "commit", "-q", "-m", message, env={"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date})


def init_repository(root: str) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.com")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "core.autocrlf", "false")


def install_hooks(root: str) -> None:
    write(root, ".githooks/pre-commit", PRE_COMMIT)
    write(root, ".githooks/commit-msg", COMMIT_MSG)
    commit_all(root, "chore: hooks")
    git(root, "config", "core.hooksPath", ".githooks")


def snapshot(root: str) -> tuple[dict[str, bytes], set[str]]:
    """Return every file under root, outside .git, and every empty folder."""
    files, empty = {}, set()
    for folder, names, filenames in os.walk(root):
        names[:] = [name for name in names if name != ".git"]
        rel = os.path.relpath(folder, root).replace(os.sep, "/")
        if not names and not filenames and rel != ".":
            empty.add(rel)
        for name in filenames:
            path = os.path.join(folder, name)
            with open(path, "rb") as file:
                files[os.path.relpath(path, root).replace(os.sep, "/")] = file.read()
    return files, empty


def run_main(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        code = plan_paths.main(list(argv), out=out)
    return code, out.getvalue()


def apply_args(root: str, scope: str) -> list[str]:
    return ["plan", root, "--scope", scope, "--apply", "--agent", AGENT, "--session", SESSION, "--owner-confirmed-quiet"]


def reparse_lstat(*boundaries: str):
    """Return an lstat that reports each boundary, a path relative to the plans root, as a reparse-point folder."""
    real = os.lstat
    tails = [boundary.split("/") for boundary in boundaries]

    def lstat(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        segments = os.fspath(path).replace(os.sep, "/").split("/")
        if any(segments[-len(tail) :] == tail for tail in tails):
            return types.SimpleNamespace(st_mode=info.st_mode, st_file_attributes=plan_paths.REPARSE_POINT, st_mtime=info.st_mtime)
        return info

    return lstat


class Fixture:
    """An embedded plans repository inside a checkout, with every reference form and another session's work."""

    def __init__(self, base: str, extra: dict[str, str] | None = None) -> None:
        self.checkout = os.path.join(base, "checkout")
        self.root = os.path.join(self.checkout, ".claude", "plans")
        os.makedirs(self.root)
        self.root = os.path.realpath(self.root)
        self.forward = self.root.replace("\\", "/")
        self.back = self.root.replace("/", "\\")
        self.json = self.back.replace("\\", "\\\\")
        write(self.checkout, "src/scripts/render.ts", "export {}\n")
        self.files = {
            ".gitignore": "*.log\n/2026-09-05-alpha/scratch/\n!2026-10-02-gamma/keep.log\n2026-09-25-ignored/\n/runs/\n",
            "ROADMAP.md": (
                "- [Alpha](2026-09-05-alpha/plan.md)\n"
                "- [Old](archive/2026-08-01-old/plan.md)\n"
                "- [Topic](reference/topic.md)\n"
                "- Beta lives at .claude/plans/2026-09-20-beta/plan.md.\n"
                "- Stale: C:/example/other-checkout/.claude/plans/2026-09-05-alpha/plan.md\n"
            ),
            "follow-ups.md": "Nothing yet.\n",
            "reference/topic.md": "Back to [alpha](../2026-09-05-alpha/plan.md) and [old](../archive/2026-08-01-old/plan.md).\n",
            "2026-09-05-alpha/plan.md": (
                "Same month: [beta](../2026-09-20-beta/plan.md)\n"
                "Cross month: [gamma](../2026-10-02-gamma/plan.md)\n"
                "Archived: [old](../archive/2026-08-01-old/plan.md)\n"
                "Reference: [topic](../reference/topic.md)\n"
                "Missing: [gone](../2026-09-09-missing/plan.md)\n"
                f"Absolute: {self.forward}/2026-10-02-gamma/plan.md\n"
                f"Backslash: {self.back}\\2026-10-02-gamma\\plan.md\n"
                "Own folder: [notes](./notes.md)\n"
            ),
            "2026-09-05-alpha/notes.md": "Notes.\n",
            "2026-09-05-alpha/helper.mjs": "export const helper = 1;\n",
            "2026-09-05-alpha/tool.mjs": (
                "import { render } from '../../../src/scripts/render.ts';\n"
                "import { helper } from './helper.mjs';\n"
                "const beta = '../2026-09-20-beta/plan.md';\n"
            ),
            "2026-09-05-alpha/data.json": (
                f'{{"gamma": "{self.json}\\\\2026-10-02-gamma\\\\plan.md", '
                '"old": ".claude\\\\plans\\\\archive\\\\2026-08-01-old"}\n'
            ),
            f"{RUN}/prompt.md": "Read ../../../2026-09-20-beta/plan.md and .claude/plans/2026-10-02-gamma/plan.md\n",
            "2026-09-05-alpha/evidence.md": "Evidence cites [gamma](../2026-10-02-gamma/plan.md).\n",
            "2026-09-20-beta/plan.md": "Back to [alpha](../2026-09-05-alpha/plan.md).\n",
            "2026-10-02-gamma/plan.md": "[alpha](../2026-09-05-alpha/plan.md)\n",
            "2026-10-02-gamma/notes.md": "Gamma notes.\n",
            "2026-10-02-gamma/keep.log": "kept\n",
            "archive/2026-08-01-old/plan.md": (
                "Active: [alpha](../../2026-09-05-alpha/plan.md)\nArchive: [older](../2026-07-15-older/plan.md)\n"
            ),
            "archive/2026-07-15-older/plan.md": "Next: [old](../2026-08-01-old/plan.md)\n",
            "retrospectives/2026-09-30-retro/retro.md": "[alpha](../../2026-09-05-alpha/plan.md)\n",
        }
        self.files.update(extra or {})
        evidence = self.files["2026-09-05-alpha/evidence.md"].encode()
        self.files["2026-09-05-alpha/digests.md"] = f"evidence.md {plan_paths.sha256(evidence)}\n"
        for rel, text in self.files.items():
            write(self.root, rel, text)
        init_repository(self.root)
        commit_all(self.root, "chore: fixture")
        install_hooks(self.root)
        # Another session's work, none of it staged.
        write(self.root, "2026-09-20-beta/plan.md", self.files["2026-09-20-beta/plan.md"] + "See [gamma](../2026-10-02-gamma/plan.md).\n")
        os.remove(os.path.join(self.root, "2026-10-02-gamma", "notes.md"))
        write(self.root, "2026-09-20-beta/draft.md", "Draft for [gamma](../2026-10-02-gamma/plan.md)\n")
        write(self.root, "2026-10-03-delta/notes.md", "[alpha](../2026-09-05-alpha/plan.md)\n")
        write(self.root, "2026-09-05-alpha/scratch/note.md", "[gamma](../../2026-10-02-gamma/plan.md)\n")
        write(self.root, "2026-09-25-ignored/notes.md", "[gamma](../2026-10-02-gamma/plan.md)\n")
        write(self.root, f"{RUN}/run.json", json.dumps({"source": "ship", "landed": "abc123", "date": "2026-09-06"}))
        os.makedirs(os.path.join(self.root, "2026-10-04-empty"))

    def normalize(self, data: bytes) -> bytes:
        for spelling in (self.json, self.back, self.forward):
            data = data.replace(spelling.encode(), b"<ROOT>")
        return data


def make_temp(test: unittest.TestCase | type) -> str:
    temporary = tempfile.TemporaryDirectory(prefix="plan-paths-test-", ignore_cleanup_errors=True)
    if isinstance(test, unittest.TestCase):
        test.addCleanup(temporary.cleanup)
    else:
        test.cleanups.append(temporary.cleanup)
    return os.path.realpath(temporary.name)


class NewPathTests(unittest.TestCase):
    def test_maps_a_dated_name_to_its_year_and_month(self) -> None:
        self.assertEqual(plan_paths.new_path("2026-09-05-alpha"), "2026/09/2026-09-05-alpha")

    def test_refuses_a_name_without_a_valid_date(self) -> None:
        for name in ("alpha", "2026-13-01-alpha", "2026-02-30-alpha"):
            with self.assertRaises(ValueError):
                plan_paths.new_path(name)


class RewriterTests(unittest.TestCase):
    """Rewrite text in memory against a plans repository with no commits, for the reference grammar."""

    cleanups: list = []
    GAMMA = "../2026-10-02-gamma/plan.md"
    NEW_GAMMA = "../../10/2026-10-02-gamma/plan.md"

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = os.path.join(make_temp(cls), "checkout", ".claude", "plans")
        os.makedirs(cls.root)
        init_repository(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        for cleanup in cls.cleanups:
            cleanup()

    def rewrite(self, source: str, text: str, fold: bool | None = None) -> plan_paths.Rewrite:
        repo = plan_paths.Repository(self.root)
        if fold is not None:
            repo.fold = fold
        moves = plan_paths.MoveMap(
            [
                ("2026-09-05-alpha", "2026/09/2026-09-05-alpha", "dir"),
                ("2026-10-02-gamma", "2026/10/2026-10-02-gamma", "dir"),
                ("2026-10-02-gamma notes.md", "2026/10/2026-10-02-gamma notes/plan.md", "file"),
            ]
        )
        tracked = ["2026-09-05-alpha/plan.md", "2026-10-02-gamma/plan.md", "reference/topic notes.md", "2026-10-02-gamma notes.md"]
        return plan_paths.Rewriter(repo, moves, plan_paths.Oracle(repo, moves, tracked)).rewrite(source, text.encode())

    def test_markdown_rewrites_only_fences_that_name_runnable_code(self) -> None:
        absolute = os.path.realpath(self.root).replace("\\", "/") + "/2026-10-02-gamma/plan.md"
        old, new = self.GAMMA, self.NEW_GAMMA
        kept = f"```text\nOutput: {old}\n{absolute}\n```\n```\n{old}\n```\n```console\n$ cat {old}\n```\n```diff\n- {old}\n```\n"
        text = f"Inline `{old}`.\n{kept}```bash\ncat {old}\n```\n~~~python\nopen(\"{old}\")\n~~~\n"
        expected = f"Inline `{new}`.\n{kept}```bash\ncat {new}\n```\n~~~python\nopen(\"{new}\")\n~~~\n"
        self.assertEqual(self.rewrite("2026-09-05-alpha/plan.md", text).new_data.decode(), expected)
        self.assertEqual(self.rewrite("2026-09-05-alpha/notes.txt", text).new_data.decode().count(self.NEW_GAMMA), 7)

    def test_a_fence_opened_in_a_list_item_follows_the_same_rule(self) -> None:
        old, new = self.GAMMA, self.NEW_GAMMA
        kept = (
            f"- ```text\n  {old}\n  ```\n"
            f"1. ~~~console\n   $ cat {old}\n   ~~~\n"
            f"> * ```\n>   {old}\n>   ```\n"
            f"  - ```diff\n    - {old}\n    ```\n"
        )
        runnable = f"- ```bash\n  cat {old}\n  ```\n"
        after = f"After the list: {old}\n"
        result = self.rewrite("2026-09-05-alpha/plan.md", kept + runnable + after).new_data.decode()
        self.assertEqual(result, kept + runnable.replace(old, new) + after.replace(old, new))

    def test_a_list_item_fence_ends_with_the_item_when_it_never_closes(self) -> None:
        old, new = self.GAMMA, self.NEW_GAMMA
        text = f"  - ```text\n    {old}\nOutside the item: {old}\n"
        expected = f"  - ```text\n    {old}\nOutside the item: {new}\n"
        self.assertEqual(self.rewrite("2026-09-05-alpha/plan.md", text).new_data.decode(), expected)

    def test_a_dated_file_name_with_spaces_and_no_folder_is_read_whole(self) -> None:
        text = '[gamma](<2026-10-02-gamma notes.md>) and "2026-10-02-gamma notes.md" and "2026-10-02-gamma meeting".\n'
        new = "2026/10/2026-10-02-gamma notes/plan.md"
        result = self.rewrite("ROADMAP.md", text)
        self.assertEqual(result.new_data.decode(), text.replace("2026-10-02-gamma notes.md", new))
        self.assertEqual(result.unresolved, [])

    def test_windows_matches_the_plans_anchor_without_letter_case(self) -> None:
        checkout = os.path.dirname(os.path.dirname(os.path.realpath(self.root))).replace("\\", "/")
        text = f"See {checkout}/.CLAUDE/PLANS/2026-09-05-alpha/plan.md here.\n"
        expected = f"See {checkout}/.CLAUDE/PLANS/2026/09/2026-09-05-alpha/plan.md here.\n"
        self.assertEqual(self.rewrite("ROADMAP.md", text, fold=True).new_data.decode(), expected)
        self.assertEqual(self.rewrite("ROADMAP.md", text, fold=False).new_data.decode(), text)

    def test_a_path_with_spaces_inside_angle_brackets_or_quotes_is_read_whole(self) -> None:
        text = '[topic](<../reference/topic notes.md>) and "../reference/topic notes.md".\n'
        result = self.rewrite("2026-09-05-alpha/plan.md", text)
        self.assertEqual(result.new_data.decode(), text.replace("../reference/", "../../../reference/"))
        self.assertEqual(result.unresolved, [])
        command = f'Run "{self.GAMMA} --check now".\n'
        self.assertEqual(self.rewrite("2026-09-05-alpha/plan.md", command).new_data.decode(), command.replace(self.GAMMA, self.NEW_GAMMA))


class MigrationTests(unittest.TestCase):
    """Migrate the archive, then the active plans, then archive one plan with `move`."""

    cleanups: list = []

    @classmethod
    def setUpClass(cls) -> None:
        cls.patch = mock.patch.object(plan_paths, "list_processes", lambda: [])
        cls.patch.start()
        cls.fixture = Fixture(make_temp(cls))
        cls.root = cls.fixture.root
        cls.baseline_status = git(cls.root, "status", "--porcelain=v1", "-uall")
        cls.dry_code, cls.dry = run_main("plan", cls.root, "--scope", "archive", "--dry-run")
        cls.archive_code, cls.archive_out = run_main(*apply_args(cls.root, "archive"))
        cls.archive_files = snapshot(cls.root)[0]
        cls.verify_archive = run_main("plan", cls.root, "--scope", "archive", "--verify")
        cls.active_code, cls.active_out = run_main(*apply_args(cls.root, "active"))
        cls.verify_active = run_main("plan", cls.root, "--scope", "active", "--verify")
        cls.verify_all = run_main("plan", cls.root, "--scope", "all", "--verify")
        cls.status = git(cls.root, "status", "--porcelain=v1", "-uall")
        cls.files, cls.empty = snapshot(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.patch.stop()
        for cleanup in cls.cleanups:
            cleanup()

    def text(self, rel: str) -> str:
        return self.files[rel].decode()

    def test_both_passes_apply_and_verify(self) -> None:
        self.assertEqual(self.archive_code, 0, self.archive_out)
        self.assertEqual(self.active_code, 0, self.active_out)
        for code, out in (self.verify_archive, self.verify_active, self.verify_all):
            self.assertEqual(code, 0, out)
            self.assertIn("verified", out)

    def test_dry_run_reports_without_changing_anything(self) -> None:
        self.assertEqual(self.dry_code, 0, self.dry)
        self.assertIn("items to move: 2", self.dry)
        self.assertIn("archive/2026-08-01-old -> archive/2026/08/2026-08-01-old", self.dry)
        self.assertIn("../archive/<slug>: 2 files, 2 references", self.dry)

    def test_items_move_into_year_and_month_folders(self) -> None:
        for rel in (
            "archive/2026/08/2026-08-01-old/plan.md",
            "archive/2026/07/2026-07-15-older/plan.md",
            "2026/09/2026-09-05-alpha/plan.md",
            "2026/10/2026-10-02-gamma/plan.md",
            "retrospectives/2026/09/2026-09-30-retro/retro.md",
            "2026/10/2026-10-03-delta/notes.md",
            "2026/09/2026-09-25-ignored/notes.md",
        ):
            self.assertIn(rel, self.files)
        self.assertIn("2026/10/2026-10-04-empty", self.empty)
        self.assertFalse(os.path.exists(os.path.join(self.root, "2026-09-05-alpha")))
        self.assertIn("archive/2026/08/2026-08-01-old/plan.md", git(self.root, "ls-files"))

    def test_same_month_link_is_unchanged(self) -> None:
        self.assertIn("Same month: [beta](../2026-09-20-beta/plan.md)\n", self.text("2026/09/2026-09-05-alpha/plan.md"))
        self.assertEqual(self.text("2026/09/2026-09-20-beta/plan.md").splitlines()[0], "Back to [alpha](../2026-09-05-alpha/plan.md).")

    def test_cross_month_link_climbs_to_the_year_folder(self) -> None:
        self.assertIn("Cross month: [gamma](../../10/2026-10-02-gamma/plan.md)\n", self.text("2026/09/2026-09-05-alpha/plan.md"))
        self.assertEqual(self.text("2026/10/2026-10-02-gamma/plan.md"), "[alpha](../../09/2026-09-05-alpha/plan.md)\n")
        self.assertEqual(self.text("archive/2026/07/2026-07-15-older/plan.md"), "Next: [old](../../08/2026-08-01-old/plan.md)\n")

    def test_links_between_active_and_archive_follow_both_passes(self) -> None:
        alpha = self.text("2026/09/2026-09-05-alpha/plan.md")
        self.assertIn("Archived: [old](../../../archive/2026/08/2026-08-01-old/plan.md)\n", alpha)
        old = self.text("archive/2026/08/2026-08-01-old/plan.md")
        self.assertEqual(
            old,
            "Active: [alpha](../../../../2026/09/2026-09-05-alpha/plan.md)\nArchive: [older](../../07/2026-07-15-older/plan.md)\n",
        )
        after_archive = self.fixture.normalize(self.archive_files["archive/2026/08/2026-08-01-old/plan.md"]).decode()
        self.assertIn("Active: [alpha](../../../../2026-09-05-alpha/plan.md)\n", after_archive)
        self.assertIn(
            "Archived: [old](../archive/2026/08/2026-08-01-old/plan.md)\n",
            self.archive_files["2026-09-05-alpha/plan.md"].decode(),
        )
        self.assertEqual(self.text("retrospectives/2026/09/2026-09-30-retro/retro.md"), "[alpha](../../../../2026/09/2026-09-05-alpha/plan.md)\n")

    def test_moved_file_keeps_links_to_unmoved_targets(self) -> None:
        alpha = self.text("2026/09/2026-09-05-alpha/plan.md")
        self.assertIn("Reference: [topic](../../../reference/topic.md)\n", alpha)
        self.assertIn("Own folder: [notes](./notes.md)\n", alpha)
        self.assertEqual(
            self.text("2026/09/2026-09-05-alpha/tool.mjs"),
            "import { render } from '../../../../../src/scripts/render.ts';\n"
            "import { helper } from './helper.mjs';\n"
            "const beta = '../2026-09-20-beta/plan.md';\n",
        )

    def test_unmoved_file_follows_moved_targets(self) -> None:
        self.assertEqual(
            self.text("reference/topic.md"),
            "Back to [alpha](../2026/09/2026-09-05-alpha/plan.md) and [old](../archive/2026/08/2026-08-01-old/plan.md).\n",
        )

    def test_root_file_links_and_stale_paths(self) -> None:
        self.assertEqual(
            self.text("ROADMAP.md"),
            "- [Alpha](2026/09/2026-09-05-alpha/plan.md)\n"
            "- [Old](archive/2026/08/2026-08-01-old/plan.md)\n"
            "- [Topic](reference/topic.md)\n"
            "- Beta lives at .claude/plans/2026/09/2026-09-20-beta/plan.md.\n"
            "- Stale: C:/example/other-checkout/.claude/plans/2026-09-05-alpha/plan.md\n",
        )

    def test_backslash_and_json_escaped_paths(self) -> None:
        alpha = self.text("2026/09/2026-09-05-alpha/plan.md")
        self.assertIn(f"Absolute: {self.fixture.forward}/2026/10/2026-10-02-gamma/plan.md\n", alpha)
        self.assertIn(f"Backslash: {self.fixture.back}\\2026\\10\\2026-10-02-gamma\\plan.md\n", alpha)
        self.assertEqual(
            self.text("2026/09/2026-09-05-alpha/data.json"),
            f'{{"gamma": "{self.fixture.json}\\\\2026\\\\10\\\\2026-10-02-gamma\\\\plan.md", '
            '"old": ".claude\\\\plans\\\\archive\\\\2026\\\\08\\\\2026-08-01-old"}\n',
        )

    def test_gitignore_rewrites_only_anchored_rules(self) -> None:
        self.assertEqual(
            self.text(".gitignore"),
            "*.log\n/2026/09/2026-09-05-alpha/scratch/\n!2026/10/2026-10-02-gamma/keep.log\n2026-09-25-ignored/\n/runs/\n",
        )
        ignored = git(self.root, "status", "--porcelain=v1", "--ignored", "-uall")
        self.assertIn("!! 2026/09/2026-09-05-alpha/scratch/note.md", ignored)
        self.assertIn("!! 2026/09/2026-09-25-ignored/notes.md", ignored)

    def test_frozen_evidence_keeps_its_bytes(self) -> None:
        base = "2026/09/" + RUN
        self.assertEqual(self.text(f"{base}/prompt.md"), self.fixture.files[f"{RUN}/prompt.md"])
        self.assertEqual(self.text("2026/09/2026-09-05-alpha/evidence.md"), self.fixture.files["2026-09-05-alpha/evidence.md"])
        committed = git(self.root, "show", f"HEAD:{base}/prompt.md")
        self.assertEqual(committed, self.fixture.files[f"{RUN}/prompt.md"])
        self.assertIn("frozen files that keep a moved path, by folder or name: 0", self.dry)

    def test_unresolved_link_is_reported_and_kept(self) -> None:
        self.assertIn("2026-09-05-alpha/plan.md: ../2026-09-09-missing/plan.md", self.dry)
        self.assertIn("Missing: [gone](../2026-09-09-missing/plan.md)\n", self.text("2026/09/2026-09-05-alpha/plan.md"))

    def test_untracked_and_ignored_files_are_rewritten_on_disk_only(self) -> None:
        self.assertEqual(self.text("2026/10/2026-10-03-delta/notes.md"), "[alpha](../../09/2026-09-05-alpha/plan.md)\n")
        self.assertEqual(self.text("2026/09/2026-09-20-beta/draft.md"), "Draft for [gamma](../../10/2026-10-02-gamma/plan.md)\n")
        self.assertEqual(self.text("2026/09/2026-09-05-alpha/scratch/note.md"), "[gamma](../../../10/2026-10-02-gamma/plan.md)\n")
        self.assertEqual(self.text("2026/09/2026-09-25-ignored/notes.md"), "[gamma](../../10/2026-10-02-gamma/plan.md)\n")
        self.assertEqual(json.loads(self.files[f"2026/09/{RUN}/run.json"])["landed"], "abc123")

    def test_other_sessions_changes_survive_unstaged(self) -> None:
        expected = (
            " M 2026/09/2026-09-20-beta/plan.md\n"
            " D 2026/10/2026-10-02-gamma/notes.md\n"
            f"?? 2026/09/{RUN}/run.json\n"
            "?? 2026/09/2026-09-20-beta/draft.md\n"
            "?? 2026/10/2026-10-03-delta/notes.md\n"
        )
        self.assertEqual(sorted(self.status.splitlines()), sorted(expected.splitlines()))
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "")
        beta = self.text("2026/09/2026-09-20-beta/plan.md")
        self.assertEqual(beta, "Back to [alpha](../2026-09-05-alpha/plan.md).\nSee [gamma](../../10/2026-10-02-gamma/plan.md).\n")

    def test_history_follows_a_moved_plan(self) -> None:
        log = git(self.root, "log", "--follow", "--format=%s", "--", "2026/09/2026-09-05-alpha/plan.md").splitlines()
        self.assertEqual(log[-1], "chore: fixture")
        subjects = git(self.root, "log", "--format=%s", "-4").splitlines()
        self.assertEqual(
            subjects,
            [
                "docs(plans): repoint plan paths at the nested folders",
                "refactor(plans): nest plan folders by year and month",
                "docs(plans): repoint plan paths at the nested folders",
                "refactor(plans): nest plan folders by year and month",
            ],
        )
        self.assertIn(f"Agent-Session: {SESSION}", git(self.root, "log", "-1", "--format=%B"))

    def test_resolve_finds_an_old_path_by_folder_name(self) -> None:
        cases = {
            ".claude/plans/2026-09-05-alpha/plan.md": ".claude/plans/2026/09/2026-09-05-alpha/plan.md",
            ".claude\\plans\\archive\\2026-08-01-old": ".claude/plans/archive/2026/08/2026-08-01-old",
            "2026-09-30-retro/retro.md": "retrospectives/2026/09/2026-09-30-retro/retro.md",
        }
        for old, new in cases.items():
            self.assertEqual(run_main("resolve", self.root, old), (0, new + "\n"))
        stale = "C:/example/other-checkout/.claude/plans/2026-09-05-alpha/plan.md"
        self.assertEqual(run_main("resolve", self.root, stale), (0, f"{self.fixture.forward}/2026/09/2026-09-05-alpha/plan.md\n"))
        self.assertEqual(run_main("resolve", self.root, ".claude/plans/2026-09-09-missing/plan.md")[0], 1)


class DigestAndExcludeTests(unittest.TestCase):
    """A recorded digest freezes a file except plan.md and plans-root files, and --exclude keeps its paths."""

    HISTORY = "notes/history/log.md"
    UNTRACKED = "notes/history/draft.md"
    GLOB = "notes/history/*"
    LINK = "[alpha](../../2026-09-05-alpha/plan.md)\n"

    @classmethod
    def setUpClass(cls) -> None:
        cls.cleanups = []
        cls.patch = mock.patch.object(plan_paths, "list_processes", lambda: [])
        cls.patch.start()
        living = "Back to [gamma](../2026-10-02-gamma/plan.md)\n"
        cited = "Cited [gamma](../2026-10-02-gamma/plan.md)\n"
        summary = "See [alpha](2026-09-05-alpha/plan.md)\n"
        digests = {"plan.md": living, "cited.md": cited}
        extra = {
            "2026-09-12-living/plan.md": living,
            "2026-09-12-living/cited.md": cited,
            "2026-09-12-living/digests.md": "".join(f"{name} {plan_paths.sha256(text.encode())}\n" for name, text in digests.items()),
            "SUMMARY.md": summary,
            "notes/digests.md": f"SUMMARY.md {plan_paths.sha256(summary.encode())}\n",
            "notes/current.md": "[alpha](../2026-09-05-alpha/plan.md)\n",
            cls.HISTORY: "[alpha](../../2026-09-05-alpha/plan.md)\n",
        }
        cls.fixture = Fixture(make_temp(cls), extra)
        cls.root = cls.fixture.root
        write(cls.root, cls.UNTRACKED, "[alpha](../../2026-09-05-alpha/plan.md)\n")
        cls.dry_code, cls.dry = run_main("plan", cls.root, "--scope", "active", "--dry-run", "--exclude", cls.GLOB)
        cls.code, cls.out = run_main(*apply_args(cls.root, "active"), "--exclude", cls.GLOB)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.patch.stop()
        for cleanup in cls.cleanups:
            cleanup()

    def test_a_recorded_digest_leaves_plan_md_and_plans_root_files_rewritten(self) -> None:
        self.assertEqual(self.code, 0, self.out)
        self.assertEqual(read(self.root, "2026/09/2026-09-12-living/plan.md"), "Back to [gamma](../../10/2026-10-02-gamma/plan.md)\n")
        self.assertEqual(read(self.root, "SUMMARY.md"), "See [alpha](2026/09/2026-09-05-alpha/plan.md)\n")

    def test_a_recorded_digest_still_freezes_other_files(self) -> None:
        self.assertEqual(read(self.root, "2026/09/2026-09-12-living/cited.md"), "Cited [gamma](../2026-10-02-gamma/plan.md)\n")

    def test_an_excluded_file_keeps_its_bytes_while_a_sibling_is_rewritten(self) -> None:
        self.assertEqual(self.dry_code, 0, self.dry)
        self.assertEqual(read(self.root, self.HISTORY), self.fixture.files[self.HISTORY])
        self.assertEqual(read(self.root, self.UNTRACKED), self.LINK)
        self.assertEqual(read(self.root, "notes/current.md"), "[alpha](../2026/09/2026-09-05-alpha/plan.md)\n")
        self.assertEqual(git(self.root, "show", f"HEAD:{self.HISTORY}"), self.fixture.files[self.HISTORY])

    def test_the_dry_run_counts_excluded_files_and_the_journal_records_the_globs(self) -> None:
        self.assertIn("excluded files that keep a moved path, by --exclude: 2", self.dry)
        with open(os.path.join(self.root, ".git", plan_paths.JOURNAL), encoding="utf-8") as file:
            self.assertEqual(json.load(file)["exclude"], [self.GLOB])


class MoveTests(unittest.TestCase):
    def test_move_archives_one_plan_and_repoints_its_links(self) -> None:
        with mock.patch.object(plan_paths, "list_processes", lambda: []):
            fixture = Fixture(make_temp(self))
            root = fixture.root
            self.assertEqual(run_main(*apply_args(root, "active"))[0], 0)
            code, out = run_main(
                "move", root, "2026/10/2026-10-02-gamma", "archive/2026/10/2026-10-02-gamma",
                "--apply", "--agent", AGENT, "--session", SESSION, "--owner-confirmed-quiet",
            )
        self.assertEqual(code, 0, out)
        self.assertIn("Cross month: [gamma](../../../archive/2026/10/2026-10-02-gamma/plan.md)\n", read(root, "2026/09/2026-09-05-alpha/plan.md"))
        self.assertEqual(read(root, "archive/2026/10/2026-10-02-gamma/plan.md"), "[alpha](../../../../2026/09/2026-09-05-alpha/plan.md)\n")
        self.assertIn("!archive/2026/10/2026-10-02-gamma/keep.log\n", read(root, ".gitignore"))
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "docs(plans): repoint paths at archive/2026/10/2026-10-02-gamma\n")


class CollisionTests(unittest.TestCase):
    def test_a_folder_and_a_file_with_one_slug_refuse_before_any_commit(self) -> None:
        root = os.path.join(make_temp(self), "plans")
        write(root, "2026-09-05-alpha/plan.md", "Folder plan.\n")
        write(root, "2026-09-05-alpha.md", "File plan.\n")
        init_repository(root)
        commit_all(root, "chore: fixture")
        head = git(root, "rev-parse", "HEAD")
        before = snapshot(root)
        with mock.patch.object(plan_paths, "list_processes", lambda: []):
            dry_code, dry = run_main("plan", root, "--scope", "active", "--dry-run")
            code, out = run_main(*apply_args(root, "active"))
        self.assertEqual(dry_code, 1, dry)
        self.assertIn("would both move to 2026/09/2026-09-05-alpha", dry)
        self.assertEqual(code, 1, out)
        self.assertIn("would both move to", out)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head)
        self.assertEqual(snapshot(root), before)


class EnclosingRepositoryTests(unittest.TestCase):
    def test_dated_markdown_file_in_a_subfolder_plans_root(self) -> None:
        checkout = os.path.join(make_temp(self), "notes")
        write(checkout, ".claude/plans/2026-07-31-tooling.md", "# Tooling\n")
        write(checkout, "docs/index.md", "See .claude/plans/2026-07-31-tooling.md and [tooling](../.claude/plans/2026-07-31-tooling.md).\n")
        init_repository(checkout)
        commit_all(checkout, "chore: fixture")
        root = os.path.join(checkout, ".claude", "plans")
        with mock.patch.object(plan_paths, "list_processes", lambda: []):
            code, out = run_main(*apply_args(root, "active"))
        self.assertEqual(code, 0, out)
        self.assertEqual(read(checkout, ".claude/plans/2026/07/2026-07-31-tooling/plan.md"), "# Tooling\n")
        self.assertEqual(
            read(checkout, "docs/index.md"),
            "See .claude/plans/2026/07/2026-07-31-tooling/plan.md and [tooling](../.claude/plans/2026/07/2026-07-31-tooling/plan.md).\n",
        )
        self.assertEqual(git(checkout, "status", "--porcelain"), "")
        self.assertEqual(
            run_main("resolve", root, ".claude/plans/2026-07-31-tooling.md"),
            (0, ".claude/plans/2026/07/2026-07-31-tooling/plan.md\n"),
        )


class SharedIndexTests(unittest.TestCase):
    def test_a_staged_entry_outside_the_moved_paths_stays_staged_and_verifies(self) -> None:
        fixture = Fixture(make_temp(self))
        root = fixture.root
        write(root, "follow-ups.md", "Staged by another session.\n")
        git(root, "add", "follow-ups.md")
        staged_blob = git(root, "ls-files", "-s", "follow-ups.md")
        with mock.patch.object(plan_paths, "list_processes", lambda: []):
            code, out = run_main(*apply_args(root, "active"))
        self.assertEqual(code, 0, out)
        self.assertEqual(git(root, "diff", "--cached", "--name-only"), "follow-ups.md\n")
        self.assertEqual(git(root, "ls-files", "-s", "follow-ups.md"), staged_blob)
        self.assertEqual(git(root, "show", "HEAD:follow-ups.md"), "Nothing yet.\n")
        code, out = run_main("plan", root, "--scope", "active", "--verify")
        self.assertEqual(code, 0, out)


class PreflightTests(unittest.TestCase):
    cleanups: list = []

    @classmethod
    def setUpClass(cls) -> None:
        cls.template = Fixture(make_temp(cls)).root

    @classmethod
    def tearDownClass(cls) -> None:
        for cleanup in cls.cleanups:
            cleanup()

    def setUp(self) -> None:
        self.root = copy_repository(self.template, make_temp(self))
        self.processes = mock.patch.object(plan_paths, "list_processes", lambda: [])
        self.processes.start()
        self.addCleanup(self.processes.stop)

    def refusals(self) -> list[str]:
        """Return the preflight refusals with every path counted as a migration path."""
        repo = plan_paths.Repository(self.root)
        moves = plan_paths.MoveMap([])
        rewriter = plan_paths.Rewriter(repo, moves, plan_paths.Oracle(repo, moves, []))
        everywhere = types.SimpleNamespace(touches=lambda rel: True, items=set(), files=set())
        return plan_paths.preflight(repo, SESSION, rewriter, {}, everywhere)[1]

    @staticmethod
    def at_now():
        """Hold the preflight's clock at NOW, so the recent-commit window is exact."""
        return mock.patch.object(plan_paths.time, "time", return_value=NOW)

    @staticmethod
    def recent(root: str, rel: str, data: str | bytes = "Recent.\n", age: int = 60) -> None:
        """Write a run file whose mtime is age seconds older than NOW."""
        write(root, rel, data)
        os.utime(os.path.join(root, *rel.split("/")), (NOW - age, NOW - age))

    def test_a_quiet_repository_passes(self) -> None:
        self.assertEqual(self.refusals(), [])

    def test_without_quiet_check_apply_prints_and_exits_2(self) -> None:
        before = snapshot(self.root)
        head = git(self.root, "rev-parse", "HEAD")
        code, out = run_main("plan", self.root, "--scope", "active", "--apply", "--agent", AGENT, "--session", SESSION)
        self.assertEqual(code, 2, out)
        self.assertIn("--verified-quiet", out)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), head)

    def test_refuses_a_staged_entry(self) -> None:
        git(self.root, "add", "2026-09-20-beta/draft.md")
        self.assertTrue(any("staged" in refusal for refusal in self.refusals()))

    def test_refuses_a_recent_commit_from_another_session(self) -> None:
        write(self.root, "follow-ups.md", "Changed.\n")
        git(self.root, "add", "follow-ups.md")
        message = f"docs: x\n\nAgent: a (effort b, c)\nAgent-Session: {OTHER_SESSION}"
        git(self.root, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message)
        self.assertTrue(any("last 15 minutes" in refusal for refusal in self.refusals()))

    def test_an_unrelated_agent_process_is_informational(self) -> None:
        me = os.getpid()
        table = [
            {"pid": me, "ppid": 500, "name": "python.exe", "command": "python plan_paths.py"},
            {"pid": 500, "ppid": 1, "name": "claude.exe", "command": "claude"},
            {"pid": 510, "ppid": 500, "name": "node.exe", "command": "node @anthropic-ai/claude-code/mcp.js"},
            {"pid": 900, "ppid": 1, "name": "codex.exe", "command": "codex exec"},
            {"pid": 901, "ppid": 900, "name": "codex.exe", "command": "codex helper"},
        ]
        with mock.patch.object(plan_paths, "list_processes", lambda: table):
            refusals = [refusal for refusal in self.refusals() if "agent process" in refusal]
        self.assertEqual(refusals, [])

    def test_verified_quiet_archives_and_rolls_back_without_owner_confirmation(self) -> None:
        before = snapshot(self.root)
        args = self.move_args(confirmed=False) + ["--verified-quiet"]
        table = [{"pid": 900, "ppid": 1, "name": "codex.exe", "command": "codex exec unrelated work"}]
        with mock.patch.object(plan_paths, "list_processes", lambda: table):
            code, out = run_main(*args)
            self.assertEqual(code, 0, out)
            self.assertIn("preflight: agent process 900", out)
            self.assertTrue(os.path.isfile(os.path.join(self.root, "archive/2026-10-02-gamma/plan.md")))
            code, out = run_main(*["--rollback" if arg == "--apply" else arg for arg in args])
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.root), before)

    def test_verified_quiet_refuses_an_agent_naming_the_moved_plan(self) -> None:
        path = os.path.join(self.root, "2026-10-02-gamma", "plan.md")
        table = [{"pid": 900, "ppid": 1, "name": "codex.exe", "command": f'codex exec "{path}"'}]
        before = snapshot(self.root)
        with mock.patch.object(plan_paths, "list_processes", lambda: table):
            code, out = run_main(*self.move_args(confirmed=False), "--verified-quiet")
        self.assertEqual(code, 1, out)
        self.assertIn("preflight refuses: agent process 900", out)
        self.assertEqual(snapshot(self.root), before)

    def test_refuses_an_open_ship_run_until_its_plan_records_a_stop(self) -> None:
        run = "2026-10-02-gamma/runs/2026-10-03-fix-010203-00112233445566aa/run.json"
        write(self.root, run, json.dumps({"source": "ship", "landed": None, "date": "2026-10-03"}))
        self.assertTrue(any("task run 2026-10-02-gamma/runs/2026-10-03-fix" in refusal for refusal in self.refusals()))
        stop = "[alpha](../2026-09-05-alpha/plan.md)\nstopped spend: 2026-10-03T01:02:03Z 2.50\n"
        write(self.root, "2026-10-02-gamma/plan.md", stop)
        self.assertEqual(self.refusals(), [])

    def test_a_stop_line_closes_only_the_run_with_its_run_start(self) -> None:
        stopped = "2026-10-02-gamma/runs/2026-10-03-fix-010000-00112233445566aa/run.json"
        still_open = "2026-10-02-gamma/runs/2026-10-03-fix-120000-00112233445566bb/run.json"
        for run in (stopped, still_open):
            write(self.root, run, json.dumps({"source": "ship", "landed": None, "date": "2026-10-03"}))
        write(self.root, "2026-10-02-gamma/plan.md", "stopped spend: 2026-10-03T01:00:00Z 2.50\n")
        refusals = [refusal for refusal in self.refusals() if "task run" in refusal]
        self.assertEqual(len(refusals), 1, refusals)
        self.assertIn("2026-10-03-fix-120000", refusals[0])

    def test_a_run_whose_metadata_cannot_be_read_refuses(self) -> None:
        real = os.lstat

        def deny(path, *args, **kwargs):
            if "runs" in os.fspath(path).replace("\\", "/").split("/"):
                raise PermissionError(13, "Access is denied", os.fspath(path))
            return real(path, *args, **kwargs)

        with self.at_now(), mock.patch.object(plan_paths.os, "lstat", side_effect=deny):
            refusals = self.refusals()
        self.assertTrue(any("cannot be read" in refusal for refusal in refusals), refusals)

    def test_quiet_confirmation_never_bypasses_an_open_same_plan_run(self) -> None:
        run = "2026-10-02-gamma/runs/2026-10-03-fix-010203-00112233445566aa/run.json"
        write(self.root, run, json.dumps({"source": "ship", "landed": None, "date": "2026-10-03"}))
        table = [{"pid": 900, "ppid": 1, "name": "codex.exe", "command": "codex exec"}]
        head = git(self.root, "rev-parse", "HEAD")
        with mock.patch.object(plan_paths, "list_processes", lambda: table):
            for flag in ("--owner-confirmed-quiet", "--verified-quiet"):
                with self.subTest(flag=flag):
                    code, out = run_main(*self.move_args(confirmed=False), flag)
                    self.assertEqual(code, 1, out)
                    self.assertIn("preflight: agent process 900", out)
                    self.assertIn("preflight refuses: task run 2026-10-02-gamma/runs/2026-10-03-fix", out)
                    self.assertEqual(git(self.root, "rev-parse", "HEAD"), head)

    def move_args(self, confirmed: bool = True) -> list[str]:
        """Archive gamma. Its migration paths include alpha's plan, which links to it, and beta's untracked draft."""
        args = ["move", self.root, "2026-10-02-gamma", "archive/2026-10-02-gamma", "--apply", "--agent", AGENT, "--session", SESSION]
        return args + ["--owner-confirmed-quiet"] if confirmed else args

    @staticmethod
    def foreign_commit(root: str, rel: str) -> str:
        """Append a line to rel, commit it from another session one minute before NOW, and return the commit."""
        old = read(root, rel) if os.path.exists(os.path.join(root, *rel.split("/"))) else ""
        write(root, rel, old + "Changed by another session.\n")
        git(root, "add", rel)
        message = f"docs: x\n\nAgent: a (effort b, c)\nAgent-Session: {OTHER_SESSION}"
        date = f"@{NOW - 60} +0000"
        git(root, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message, env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
        return git(root, "rev-parse", "HEAD").strip()

    def test_owner_confirmed_quiet_still_refuses_staged_and_foreign_work(self) -> None:
        cases = {
            "staged rewritten file": lambda root: git(root, "add", "2026-09-20-beta/draft.md"),
            "staged rename out of the moved plan": lambda root: git(root, "mv", "2026-10-02-gamma/plan.md", "gamma-plan.md"),
            "foreign commit in the moved plan": lambda root: self.foreign_commit(root, "2026-10-02-gamma/plan.md"),
            "foreign commit to a rewritten file": lambda root: self.foreign_commit(root, "2026-09-05-alpha/plan.md"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.root = copy_repository(self.template, make_temp(self))
                change(self.root)
                state = (snapshot(self.root), git(self.root, "rev-parse", "HEAD"), git(self.root, "ls-files", "-s"))
                with self.at_now():
                    code, out = run_main(*self.move_args())
                self.assertEqual(code, 1, out)
                self.assertIn("preflight refuses", out)
                after = (snapshot(self.root), git(self.root, "rev-parse", "HEAD"), git(self.root, "ls-files", "-s"))
                self.assertEqual(after, state)

    def test_a_run_file_changed_just_now_in_the_moved_plan_does_not_stop_the_move(self) -> None:
        self.recent(self.root, "2026-10-02-gamma/runs/2026-10-09-x-010203-0123456789abcdef/out.log")
        with self.at_now():
            code, out = run_main(*self.move_args())
        self.assertEqual(code, 0, out)
        self.assertNotIn("refuses", out)
        self.assertIn("moved", out)

    def test_staged_foreign_and_open_run_work_outside_the_migration_paths_are_lines(self) -> None:
        foreign = self.foreign_commit(self.root, "notes/other.md")
        write(self.root, "follow-ups.md", "Staged by another session.\n")
        git(self.root, "add", "follow-ups.md")
        task = "2026-09-05-alpha/runs/2026-10-03-fix-010203-00112233445566aa"
        write(self.root, f"{task}/run.json", json.dumps({"source": "ship", "landed": None, "date": "2026-10-03"}))
        expected = [
            "preflight: 1 staged entries in the shared index outside the migration paths stay staged: follow-ups.md",
            f"preflight: commit {foreign[:12]} landed in the last 15 minutes from another session. It changed no migration path",
            f"preflight: task run {task} is open",
        ]
        with self.at_now():
            unconfirmed_code, unconfirmed = run_main(*self.move_args(confirmed=False))
            code, out = run_main(*self.move_args())
        self.assertEqual(unconfirmed_code, 2, unconfirmed)
        self.assertEqual(code, 0, out)
        for text in (unconfirmed, out):
            self.assertNotIn("refuses", text)
            for line in expected:
                self.assertIn(line, text)
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "follow-ups.md\n")
        self.assertEqual(git(self.root, "log", "-3", "--format=%H").split()[2], foreign)

    def test_an_inaccessible_root_refuses_even_when_isdir_says_missing(self) -> None:
        write(self.root, "runs/2026-10-09-x-010203-0123456789abcdef/out.log", "Output")
        real_isdir, real_lstat = os.path.isdir, os.lstat
        state = (snapshot(self.root), git(self.root, "rev-parse", "HEAD"), git(self.root, "ls-files", "-s"))
        for target in ("archive", "runs"):

            def denied(path) -> bool:
                return os.fspath(path).replace(os.sep, "/").split("/")[-1] == target

            def deny(path, *args, **kwargs):
                if denied(path):
                    raise PermissionError(13, "Access is denied", os.fspath(path))
                return real_lstat(path, *args, **kwargs)

            with self.subTest(target), self.at_now():
                with mock.patch.object(plan_paths.os.path, "isdir", lambda path: not denied(path) and real_isdir(path)), mock.patch.object(plan_paths.os, "lstat", side_effect=deny):
                    refusals = self.refusals()
                    code, out = run_main(*apply_args(self.root, "active"))
            self.assertTrue(any(f"{target} cannot be read" in refusal for refusal in refusals), refusals)
            self.assertNotEqual(code, 0, out)
            self.assertEqual((snapshot(self.root), git(self.root, "rev-parse", "HEAD"), git(self.root, "ls-files", "-s")), state)

    def test_a_linked_boundary_is_never_entered_by_either_scan(self) -> None:
        linked = "2026-10-09-linked-010203-00112233445566cc"
        cases = {
            "year": ("2026", f"2026/09/2026-09-05-linked/runs/{linked}"),
            "month": ("2026/09", f"2026/09/2026-09-05-linked/runs/{linked}"),
            "plan": ("2026-10-02-gamma", f"2026-10-02-gamma/runs/{linked}"),
            "runs": ("2026-09-05-alpha/runs", f"2026-09-05-alpha/runs/{linked}"),
            "run": (f"2026-09-05-alpha/runs/{linked}", f"2026-09-05-alpha/runs/{linked}"),
        }
        real_scandir, real_listdir = os.scandir, os.listdir

        def guard(real, parts: list[str], boundary: str):
            def listed(path=".", *args, **kwargs):
                segments = os.fspath(path).replace(os.sep, "/").split("/")
                if any(segments[i : i + len(parts)] == parts for i in range(len(segments))):
                    raise AssertionError(f"listed {path} below linked {boundary}")
                return real(path, *args, **kwargs)

            return listed

        for name, (boundary, run) in cases.items():
            self.root = copy_repository(self.template, make_temp(self))
            write(self.root, f"{run}/run.json", json.dumps({"source": "ship", "landed": None, "date": "2026-10-09"}))
            write(self.root, f"{run}/out.log", "Output.\n")
            parts = boundary.split("/")
            with self.subTest(name), self.at_now():
                with mock.patch.object(plan_paths.os, "lstat", side_effect=reparse_lstat(boundary)), mock.patch.object(
                    plan_paths.os, "scandir", side_effect=guard(real_scandir, parts, boundary)
                ), mock.patch.object(plan_paths.os, "listdir", side_effect=guard(real_listdir, parts, boundary)):
                    self.assertEqual(self.refusals(), [])

    def test_a_linked_run_json_is_never_read_or_counted_as_open(self) -> None:
        run = "2026-10-02-gamma/runs/2026-10-03-fix-010203-00112233445566aa"
        write(self.root, f"{run}/run.json", json.dumps({"source": "ship", "landed": None, "date": "2026-10-03"}))
        real_open = open

        def guarded_open(path, *args, **kwargs):
            if os.fspath(path).replace(os.sep, "/").endswith(f"{run}/run.json"):
                raise AssertionError(f"read {path}")
            return real_open(path, *args, **kwargs)

        with self.at_now(), mock.patch.object(plan_paths.os, "lstat", side_effect=reparse_lstat(f"{run}/run.json")), mock.patch("builtins.open", side_effect=guarded_open):
            self.assertEqual(self.refusals(), [])


def copy_repository(template: str, base: str) -> str:
    target = os.path.join(base, "plans")
    shutil.copytree(template, target)
    return target


class InterruptionTests(unittest.TestCase):
    """Interrupt an archive pass after each journal operation, then resume it or roll it back."""

    cleanups: list = []

    @classmethod
    def setUpClass(cls) -> None:
        cls.patch = mock.patch.object(plan_paths, "list_processes", lambda: [])
        cls.patch.start()
        cls.template = os.path.join(make_temp(cls), "plans")
        files = {
            "2026-09-05-alpha/plan.md": "[old](../archive/2026-08-01-old/plan.md)\n",
            "archive/2026-08-01-old/plan.md": "[alpha](../../2026-09-05-alpha/plan.md)\n",
            "archive/2026-08-01-old/notes.md": "Notes.\n",
        }
        for rel, text in files.items():
            write(cls.template, rel, text)
        init_repository(cls.template)
        commit_all(cls.template, "chore: fixture")
        draft = files["2026-09-05-alpha/plan.md"] + "Draft: [old](../archive/2026-08-01-old/notes.md)\n"
        write(cls.template, "2026-09-05-alpha/plan.md", draft)
        os.remove(os.path.join(cls.template, "archive", "2026-08-01-old", "notes.md"))
        write(cls.template, "archive/2026-08-01-old/draft.md", "[alpha](../../2026-09-05-alpha/plan.md)\n")
        reference = copy_repository(cls.template, make_temp(cls))
        code, out = run_main(*apply_args(reference, "archive"))
        assert code == 0, out
        cls.reference = cls.state(reference)
        cls.operations = len(plan_paths.Journal(plan_paths.Repository(reference)).load()["ops"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.patch.stop()
        for cleanup in cls.cleanups:
            cleanup()

    @staticmethod
    def state(root: str) -> tuple:
        files, empty = snapshot(root)
        status = git(root, "status", "--porcelain=v1", "-uall")
        return files, empty, status

    def interrupted(self, after: int) -> tuple[str, tuple, str]:
        root = copy_repository(self.template, make_temp(self))
        before = self.state(root)
        head_tree = git(root, "rev-parse", "HEAD^{tree}")

        def stop(index: int, op: dict) -> None:
            if index == after:
                raise Interrupt(op["op"])

        with mock.patch.object(plan_paths, "checkpoint", stop):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))
        return root, before, head_tree

    def test_the_reference_run_moves_rewrites_and_keeps_other_work(self) -> None:
        files, _, status = self.reference
        self.assertEqual(
            files["2026-09-05-alpha/plan.md"],
            b"[old](../archive/2026/08/2026-08-01-old/plan.md)\nDraft: [old](../archive/2026/08/2026-08-01-old/notes.md)\n",
        )
        self.assertEqual(files["archive/2026/08/2026-08-01-old/draft.md"], b"[alpha](../../../../2026-09-05-alpha/plan.md)\n")
        self.assertEqual(
            sorted(status.splitlines()),
            [" D archive/2026/08/2026-08-01-old/notes.md", " M 2026-09-05-alpha/plan.md", "?? archive/2026/08/2026-08-01-old/draft.md"],
        )
        self.assertEqual(self.operations, 5)

    def test_resume_after_each_operation_reaches_the_same_result(self) -> None:
        for after in range(self.operations):
            with self.subTest(after=after):
                root, _, _ = self.interrupted(after)
                code, out = run_main(*apply_args(root, "archive"))
                self.assertEqual(code, 0, out)
                self.assertIn("resuming", out)
                self.assertEqual(self.state(root), self.reference)

    def test_rollback_after_each_operation_restores_the_start(self) -> None:
        for after in range(self.operations):
            with self.subTest(after=after):
                root, before, head_tree = self.interrupted(after)
                code, out = run_main(
                    "plan", root, "--scope", "archive", "--rollback",
                    "--agent", AGENT, "--session", SESSION, "--owner-confirmed-quiet",
                )
                self.assertEqual(code, 0, out)
                self.assertEqual(self.state(root), before)
                self.assertEqual(git(root, "rev-parse", "HEAD^{tree}"), head_tree)

    def fresh(self) -> str:
        return copy_repository(self.template, make_temp(self))

    @staticmethod
    def rollback_args(root: str) -> list[str]:
        return ["plan", root, "--scope", "archive", "--rollback", "--agent", AGENT, "--session", SESSION, "--owner-confirmed-quiet"]

    @staticmethod
    def temporaries(root: str) -> list[str]:
        return [path for path in snapshot(root)[0] if path.endswith(".plan-paths.tmp")]

    def resume_reaches_the_reference(self, root: str) -> None:
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state(root), self.reference)

    def interrupt_inside(self, root: str, name: str, replacement) -> None:
        with mock.patch.object(plan_paths, name, replacement):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))

    def test_a_file_named_like_a_temporary_file_survives_the_rewrite(self) -> None:
        root = self.fresh()
        name = "2026-09-05-alpha/plan.md.plan-paths.tmp"
        write(root, name, "Another session's file.\n")
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 0, out)
        self.assertEqual(read(root, name), "Another session's file.\n")

    def test_resume_refuses_an_entry_staged_inside_a_moved_folder(self) -> None:
        root, _, _ = self.interrupted(1)
        extra = "archive/2026/08/2026-08-01-old/extra.md"
        write(root, extra, "Staged by another session.\n")
        git(root, "add", extra)
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 1, out)
        self.assertIn("staged", out)
        self.assertIn(extra, git(root, "diff", "--cached", "--name-only"))

    def test_rollback_refuses_a_later_commit_under_a_moved_folder(self) -> None:
        root = self.fresh()
        self.assertEqual(run_main(*apply_args(root, "archive"))[0], 0)
        later = "archive/2026/08/2026-08-01-old/new-work.md"
        write(root, later, "Later work.\n")
        git(root, "add", later)
        git(root, "commit", "-q", "-m", f"docs: later work\n\nAgent-Session: {SESSION}")
        before, head = self.state(root), git(root, "rev-parse", "HEAD")
        code, out = run_main(*self.rollback_args(root))
        self.assertEqual(code, 1, out)
        self.assertIn(later, out)
        self.assertEqual((self.state(root), git(root, "rev-parse", "HEAD")), (before, head))

    def test_rollback_refuses_when_an_old_path_exists_again(self) -> None:
        root = self.fresh()
        self.assertEqual(run_main(*apply_args(root, "archive"))[0], 0)
        write(root, "archive/2026-08-01-old/recreated.md", "Written to the old path.\n")
        before, head = self.state(root), git(root, "rev-parse", "HEAD")
        code, out = run_main(*self.rollback_args(root))
        self.assertEqual(code, 1, out)
        self.assertIn("exists again", out)
        self.assertEqual((self.state(root), git(root, "rev-parse", "HEAD")), (before, head))

    def test_an_interrupted_rollback_blocks_apply_and_a_second_rollback_finishes(self) -> None:
        root, before, head_tree = self.interrupted(1)
        real = plan_paths.remove_empty_parents

        def stop(folder: str, top: str) -> None:
            real(folder, top)
            raise Interrupt("after the first undone move")

        with mock.patch.object(plan_paths, "remove_empty_parents", stop):
            with self.assertRaises(Interrupt):
                run_main(*self.rollback_args(root))
        middle = self.state(root)
        code, out = run_main(*apply_args(root, "archive"))
        self.assertNotEqual(code, 0, out)
        self.assertEqual(self.state(root), middle)
        code, out = run_main(*self.rollback_args(root))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state(root), before)
        self.assertEqual(git(root, "rev-parse", "HEAD^{tree}"), head_tree)

    def test_verify_fails_while_a_run_is_unfinished(self) -> None:
        root, _, _ = self.interrupted(0)
        code, out = run_main("plan", root, "--scope", "archive", "--verify")
        self.assertEqual(code, 1, out)
        self.assertIn("unfinished", out)

    def test_resume_after_a_rename_that_the_journal_did_not_record(self) -> None:
        root = self.fresh()

        def stop(folder: str, top: str) -> None:
            raise Interrupt("after the rename")

        self.interrupt_inside(root, "remove_empty_parents", stop)
        self.resume_reaches_the_reference(root)

    def test_resume_after_an_interrupted_file_replacement(self) -> None:
        root = self.fresh()
        real = os.replace

        def stop(source, target) -> None:
            if os.fspath(source).endswith(".plan-paths.tmp"):
                raise Interrupt("during the replacement")
            real(source, target)

        with mock.patch.object(plan_paths.os, "replace", stop):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))
        self.assertEqual(self.temporaries(root), [])
        self.resume_reaches_the_reference(root)

    def test_a_locked_file_stops_the_run_unchanged_and_a_rerun_finishes(self) -> None:
        root = self.fresh()
        real = os.replace
        locked = os.path.normcase(os.path.join(root, "2026-09-05-alpha", "plan.md"))
        original = read(root, "2026-09-05-alpha/plan.md")

        def deny(source, target) -> None:
            if os.path.normcase(os.fspath(target)) == locked:
                raise PermissionError(13, "Access is denied", os.fspath(target))
            real(source, target)

        with mock.patch.object(plan_paths.os, "replace", deny):
            code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 2, out)
        self.assertEqual(read(root, "2026-09-05-alpha/plan.md"), original)
        self.assertEqual(self.temporaries(root), [])
        self.resume_reaches_the_reference(root)

    def test_resume_after_an_interrupted_index_reset(self) -> None:
        root = self.fresh()
        real = plan_paths.Repository.git

        def flaky(repo, *args, **kwargs):
            result = real(repo, *args, **kwargs)
            if "reset" in args:
                raise Interrupt("after the reset")
            return result

        self.interrupt_inside_git(root, flaky)
        self.resume_reaches_the_reference(root)

    def test_resume_after_the_reference_commit_landed_unrecorded(self) -> None:
        root = self.fresh()
        real = plan_paths.Repository.git
        commits = []

        def flaky(repo, *args, **kwargs):
            result = real(repo, *args, **kwargs)
            if args[:1] == ("update-ref",):
                commits.append(args)
                if len(commits) == 2:
                    raise Interrupt("after the reference commit")
            return result

        self.interrupt_inside_git(root, flaky)
        self.resume_reaches_the_reference(root)
        self.assertEqual(len(git(root, "log", "--format=%s").splitlines()), 3)

    def test_a_commit_that_lands_before_the_ref_update_stops_the_run(self) -> None:
        root = self.fresh()
        real = plan_paths.Repository.git
        foreign = []

        def racing(repo, *args, **kwargs):
            result = real(repo, *args, **kwargs)
            if args[:1] == ("write-tree",) and not foreign:
                write(root, "other.md", "Another session's commit.\n")
                git(root, "add", "other.md")
                git(root, "commit", "-q", "-m", f"docs: other\n\nAgent-Session: {OTHER_SESSION}")
                foreign.append(git(root, "rev-parse", "HEAD").strip())
            return result

        with mock.patch.object(plan_paths.Repository, "git", racing):
            code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 2, out)
        self.assertEqual(git(root, "rev-parse", "HEAD").strip(), foreign[0])
        self.assertEqual(git(root, "log", "--format=%s").splitlines(), ["docs: other", "chore: fixture"])
        self.assertEqual(git(root, "show", "HEAD:other.md"), "Another session's commit.\n")
        self.assertTrue(os.path.isdir(os.path.join(root, "archive", "2026-08-01-old")))

    def hooked(self, name: str, body: str) -> str:
        """Return a fresh copy whose hook `name` runs the shell lines in body."""
        root = self.fresh()
        write(root, f".hooks/{name}", "#!/bin/sh\n" + body)
        hook = os.path.join(root, ".hooks", name)
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        git(root, "config", "core.hooksPath", ".hooks")
        return root

    def test_a_failing_commit_msg_hook_stops_the_run_before_any_commit(self) -> None:
        root = self.hooked("commit-msg", "echo rejected >&2\nexit 1\n")
        head = git(root, "rev-parse", "HEAD")
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 2, out)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head)

    def test_a_pre_commit_hook_that_changes_the_index_stops_the_run_before_any_commit(self) -> None:
        root = self.hooked("pre-commit", "echo hooked > hooked.txt\ngit add hooked.txt\n")
        head = git(root, "rev-parse", "HEAD")
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 2, out)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head)

    def test_post_commit_runs_on_the_private_index_with_no_editor(self) -> None:
        root = self.hooked("post-commit", 'printf "%s" "$GIT_EDITOR" > hook-editor.txt\ngit read-tree HEAD\n')
        write(root, "other.md", "Staged by another session.\n")
        git(root, "add", "other.md")
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 0, out)
        self.assertEqual(git(root, "diff", "--cached", "--name-only"), "other.md\n")
        self.assertEqual(read(root, "hook-editor.txt"), ":")

    def test_migration_and_rollback_commits_are_signed_when_commit_gpgsign_is_true(self) -> None:
        root = self.fresh()
        git(root, "config", "commit.gpgSign", "true")
        real = plan_paths.Repository.git
        built = []

        def unsigned(repo, *args, **kwargs):
            if args[:1] == ("commit-tree",):
                built.append(args)
                args = tuple(arg for arg in args if arg != "-S")
            return real(repo, *args, **kwargs)

        with mock.patch.object(plan_paths.Repository, "git", unsigned):
            self.assertEqual(run_main(*apply_args(root, "archive"))[0], 0)
            code, out = run_main(*self.rollback_args(root))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(built), 3, built)
        self.assertTrue(all("-S" in args for args in built), built)

    def test_resume_refuses_a_staged_file_that_the_journal_rewrote_on_disk(self) -> None:
        root = self.fresh()
        write(root, "links.md", "[old](archive/2026-08-01-old/plan.md)\n")

        def stop(index: int, op: dict) -> None:
            if op["op"] == "rewrite-disk":
                raise Interrupt(op["op"])

        with mock.patch.object(plan_paths, "checkpoint", stop):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))
        self.assertEqual(read(root, "links.md"), "[old](archive/2026/08/2026-08-01-old/plan.md)\n")
        git(root, "add", "links.md")
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 1, out)
        self.assertIn("preflight refuses", out)
        self.assertIn("links.md", out)

    def test_rollback_refuses_a_foreign_commit_between_the_migration_commits(self) -> None:
        root = self.fresh()
        extra = "archive/2026/08/2026-08-01-old/extra.md"

        def foreign(index: int, op: dict) -> None:
            if op["op"] == "move":
                write(root, extra, "Another session's work.\n")
                git(root, "add", extra)
                old = {"GIT_AUTHOR_DATE": OLD_DATE, "GIT_COMMITTER_DATE": OLD_DATE}
                git(root, "commit", "-q", "-m", f"docs: extra\n\nAgent-Session: {OTHER_SESSION}", "--", extra, env=old)

        with mock.patch.object(plan_paths, "checkpoint", foreign):
            self.assertEqual(run_main(*apply_args(root, "archive"))[0], 0)
        before, head = self.state(root), git(root, "rev-parse", "HEAD")
        code, out = run_main(*self.rollback_args(root))
        self.assertEqual(code, 1, out)
        self.assertIn(f"which changed {extra}", out)
        self.assertEqual((self.state(root), git(root, "rev-parse", "HEAD")), (before, head))

    def interrupt_inside_git(self, root: str, flaky) -> None:
        with mock.patch.object(plan_paths.Repository, "git", flaky):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))

    def test_resume_finds_a_commit_that_landed_before_the_journal_recorded_it(self) -> None:
        root = copy_repository(self.template, make_temp(self))
        real = plan_paths.Repository.git

        def flaky(repo, *args, **kwargs):
            result = real(repo, *args, **kwargs)
            if args[:1] == ("update-ref",):
                raise Interrupt("after the ref update")
            return result

        with mock.patch.object(plan_paths.Repository, "git", flaky):
            with self.assertRaises(Interrupt):
                run_main(*apply_args(root, "archive"))
        code, out = run_main(*apply_args(root, "archive"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state(root), self.reference)
        subjects = git(root, "log", "--format=%s").splitlines()
        self.assertEqual(
            subjects,
            ["docs(plans): repoint plan paths at the nested folders", "refactor(plans): nest plan folders by year and month", "chore: fixture"],
        )


if __name__ == "__main__":
    unittest.main()
