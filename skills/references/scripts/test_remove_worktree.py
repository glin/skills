"""Tests for remove-worktree.py.

Each test builds a repository, a linked worktree and a shared folder in its own temporary
directory, and removes that directory on every exit. The shared folder stands in for a main
checkout's dependency folder, and the worktree reaches it through a directory junction on
Windows or a symbolic link elsewhere.

Run with: python3 -m unittest discover -s skills/references/scripts
"""

from __future__ import annotations

import importlib.util
import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remove-worktree.py")
spec = importlib.util.spec_from_file_location("remove_worktree", SCRIPT)
remove_worktree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remove_worktree)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

# Windows reparse tags: a cloud sync online-only placeholder and a directory junction.
CLOUD_TAG = 0x9000701A
JUNCTION_TAG = 0xA0000003


def git(directory: str, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", directory, *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_link(link: str, target: str) -> None:
    if os.name == "nt":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
            check=True,
        )
    else:
        os.symlink(target, link, target_is_directory=True)


def write(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as file:
        file.write(text)
    return path


def count_all(root: str) -> int:
    return sum(len(directories) + len(files) for _, directories, files in os.walk(root))


def same(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class RemoveWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory(prefix="remove-worktree-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.repository = os.path.join(self.root, "repository")
        os.mkdir(self.repository)
        git(self.repository, "init", "-q", "-b", "main")
        write(os.path.join(self.repository, ".gitignore"), "deps/\nnode_modules/\nscratch/\n")
        git(self.repository, "add", ".gitignore")
        git(self.repository, "commit", "-q", "-m", "base")
        self.shared = os.path.join(self.root, "shared")
        for name in ("a.js", "b.js", os.path.join("package", "lib", "c.js")):
            write(os.path.join(self.shared, name))
        self.shared_count = count_all(self.shared)
        self.tree = os.path.join(self.root, "worktrees", "run-one")

    def add_tree(self, *extra: str, path: str | None = None) -> None:
        path = path or self.tree
        git(self.repository, "worktree", "add", "-q", *extra, path)
        make_link(os.path.join(path, "deps"), self.shared)
        os.makedirs(os.path.join(path, "tool"))
        make_link(os.path.join(path, "tool", "node_modules"), self.shared)

    def registered(self) -> list[str]:
        listing = git(self.repository, "worktree", "list", "--porcelain")
        return [os.path.normcase(os.path.realpath(line[9:])) for line in listing.splitlines() if line.startswith("worktree ")]

    def run_script(self, *arguments: str) -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, SCRIPT, "--repository", self.repository, *arguments],
            capture_output=True, text=True,
        )
        return result.returncode, json.loads(result.stdout) if result.stdout.strip() else {}

    def run_in_process(self, *arguments: str) -> tuple[int, dict]:
        return remove_worktree.run(["--repository", self.repository, *arguments])

    def assert_kept_intact(self, code: int, report: dict, reason: str) -> None:
        record = report["worktrees"][0]
        self.assertEqual(code, 1, report)
        self.assertEqual(record["action"], "failed", report)
        self.assertIn(reason, record["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, ".git")))
        self.assertIn(os.path.normcase(self.tree), self.registered())
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_removes_a_tree_and_leaves_the_link_target_intact(self) -> None:
        self.add_tree("-b", "topic")
        code, report = self.run_script("--target", "main", self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertEqual(len(report["worktrees"][0]["links"]), 2)
        self.assertFalse(os.path.lexists(self.tree))
        self.assertNotIn(os.path.normcase(self.tree), self.registered())
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_stops_when_a_link_survives(self) -> None:
        self.add_tree("-b", "topic")
        with mock.patch.object(remove_worktree, "unlink", lambda link: None):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "a link survived")

    def test_keeps_a_dirty_tree_unless_forced(self) -> None:
        self.add_tree("-b", "topic")
        write(os.path.join(self.tree, "draft.txt"), "unsaved")
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "kept")
        self.assertIn("uncommitted or untracked", report["worktrees"][0]["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "draft.txt")))
        code, report = self.run_script("--force", self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertFalse(os.path.lexists(self.tree))
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_archives_a_detached_head_before_removal(self) -> None:
        self.add_tree("--detach")
        write(os.path.join(self.tree, "work.txt"), "only here")
        git(self.tree, "add", "work.txt")
        git(self.tree, "commit", "-q", "-m", "detached work")
        head = git(self.tree, "rev-parse", "HEAD")
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["archived_as"], "archive/run-one")
        self.assertEqual(git(self.repository, "rev-parse", "archive/run-one"), head)
        self.assertFalse(os.path.lexists(self.tree))

    def test_dry_run_changes_nothing(self) -> None:
        self.add_tree("--detach")
        code, report = self.run_script("--dry-run", self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "would remove")
        self.assertTrue(os.path.isdir(self.tree))
        self.assertIn(os.path.normcase(self.tree), self.registered())

    def test_refuses_a_worktree_whose_own_path_is_a_link(self) -> None:
        self.add_tree("-b", "topic")
        real = os.path.join(self.root, "moved-tree")
        os.rename(self.tree, real)
        make_link(self.tree, real)
        code, report = self.run_script(self.tree)
        record = report["worktrees"][0]
        self.assertEqual(code, 1, report)
        self.assertEqual(record["action"], "failed")
        self.assertIn("is a link", record["reason"])
        self.assertTrue(os.path.isfile(os.path.join(real, ".git")))
        self.assertTrue(os.path.lexists(os.path.join(real, "deps")))
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_keeps_a_registered_worktree_nested_inside_the_target(self) -> None:
        self.add_tree("-b", "topic")
        inner = os.path.join(self.tree, "scratch", "inner")
        git(self.repository, "worktree", "add", "-q", "-b", "inner", inner)
        saved = write(os.path.join(inner, "unsaved.txt"), "keep")
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "kept")
        self.assertIn("another registered worktree", report["worktrees"][0]["reason"])
        self.assertTrue(os.path.isfile(saved))
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_refuses_a_nested_checkout_from_another_repository(self) -> None:
        self.add_tree("-b", "topic")
        other = os.path.join(self.root, "other")
        os.mkdir(other)
        git(other, "init", "-q", "-b", "main")
        write(os.path.join(other, "committed.txt"))
        git(other, "add", "committed.txt")
        git(other, "commit", "-q", "-m", "other repository")
        inner = os.path.join(self.tree, "scratch", "inner")
        subprocess.run(["git", "clone", "-q", other, inner], check=True)
        saved = write(os.path.join(inner, "unsaved.txt"), "keep")
        code, report = self.run_script(self.tree)
        self.assert_kept_intact(code, report, "nested Git checkout")
        self.assertTrue(os.path.isfile(saved))

    def test_refuses_a_nested_linked_worktree_from_another_repository(self) -> None:
        self.add_tree("-b", "topic")
        other = os.path.join(self.root, "other")
        os.mkdir(other)
        git(other, "init", "-q", "-b", "main")
        write(os.path.join(other, "committed.txt"))
        git(other, "add", "committed.txt")
        git(other, "commit", "-q", "-m", "other repository")
        inner = os.path.join(self.tree, "scratch", "inner")
        git(other, "worktree", "add", "-q", "-b", "inner", inner)
        saved = write(os.path.join(inner, "unsaved.txt"), "keep")
        code, report = self.run_script(self.tree)
        self.assert_kept_intact(code, report, "nested Git checkout")
        self.assertTrue(os.path.isfile(saved))

    def test_refuses_a_nested_checkout_with_uppercase_git_metadata(self) -> None:
        self.add_tree("-b", "topic")
        inner = os.path.join(self.tree, "scratch", "inner")
        os.makedirs(inner)
        git(inner, "init", "-q", "-b", "main")
        marker = os.path.join(inner, ".git")
        temporary_marker = os.path.join(inner, "metadata-tmp")
        os.rename(marker, temporary_marker)
        os.rename(temporary_marker, os.path.join(inner, ".GIT"))
        saved = write(os.path.join(inner, "unsaved.txt"), "keep")
        code, report = self.run_script(self.tree)
        self.assert_kept_intact(code, report, "nested Git checkout")
        self.assertTrue(os.path.isfile(saved))

    def test_refuses_a_nested_checkout_before_archiving_detached_head(self) -> None:
        self.add_tree("--detach")
        write(os.path.join(self.tree, "unique.txt"), "only here")
        git(self.tree, "add", "unique.txt")
        git(self.tree, "commit", "-q", "-m", "detached work")
        inner = os.path.join(self.tree, "scratch", "inner")
        os.makedirs(inner)
        git(inner, "init", "-q", "-b", "main")
        code, report = self.run_script(self.tree)
        self.assert_kept_intact(code, report, "nested Git checkout")
        archived = subprocess.run(
            ["git", "-C", self.repository, "rev-parse", "--verify", "refs/heads/archive/run-one"],
            capture_output=True,
        )
        self.assertNotEqual(archived.returncode, 0)

    def test_an_unreadable_external_target_keeps_the_worktree(self) -> None:
        self.add_tree("-b", "topic")
        link = os.path.join(self.tree, "deps")
        scandir = os.scandir

        def unreadable(path: str):
            if same(path) == same(self.shared):
                raise PermissionError(13, "Access is denied", path)
            return scandir(path)

        with mock.patch.object(remove_worktree.os, "scandir", unreadable):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "cannot read linked target")
        self.assertTrue(os.path.lexists(link))

    def test_a_deep_link_target_loss_stops_before_deletion(self) -> None:
        self.add_tree("-b", "topic")
        deep = os.path.join(self.shared, "package", "lib")
        lost = write(os.path.join(deep, "lost.txt"), "gone")
        original = write(os.path.join(deep, "original.txt"), "keep")
        unlink = remove_worktree.unlink

        def lose_from_target(link: str) -> None:
            unlink(link)
            if os.path.exists(lost):
                os.remove(lost)

        with mock.patch.object(remove_worktree, "unlink", lose_from_target):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["worktrees"][0]["action"], "failed")
        self.assertIn("lost entries", report["worktrees"][0]["reason"])
        self.assertIn("lost.txt", report["worktrees"][0]["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, ".gitignore")))
        self.assertTrue(os.path.isfile(original))

    def test_files_another_process_adds_or_changes_in_a_target_do_not_stop_removal(self) -> None:
        # A test run in another checkout writes and deletes its own cache files in a shared target.
        self.add_tree("-b", "topic")
        cache = os.path.join(self.shared, ".vite-temp")
        changed = write(os.path.join(self.shared, "package", "lib", "changed.txt"), "before")
        unlink = remove_worktree.unlink
        target_state = remove_worktree.target_state
        walks = []

        def write_into_target(link: str) -> None:
            unlink(link)
            write(os.path.join(cache, "config.timestamp.mjs"), "temporary")
            write(changed, "after, and longer")

        def walk_while_a_file_vanishes(path: str):
            walks.append(path)
            if len(walks) == 1:
                vanishing = write(os.path.join(cache, "vanishing.mjs"), "temporary")
                lstat = os.lstat

                def vanish(current, *args, **kwargs):
                    if same(current) == same(vanishing):
                        os.remove(vanishing)
                    return lstat(current, *args, **kwargs)

                with mock.patch.object(remove_worktree.os, "lstat", vanish):
                    return target_state(path)
            return target_state(path)

        with mock.patch.object(remove_worktree, "unlink", write_into_target), mock.patch.object(
            remove_worktree, "target_state", walk_while_a_file_vanishes
        ):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertFalse(os.path.lexists(self.tree))
        self.assertTrue(os.path.isfile(changed))
        counts = list(report["worktrees"][0]["link_targets"].values())
        self.assertTrue(counts and all(isinstance(count, int) and count > 0 for count in counts), report)

    def test_a_target_loss_stops_removal_at_each_later_check(self) -> None:
        for when, call in (("before deleting files", 2), ("after the delete", 3)):
            with self.subTest(when):
                tree = os.path.join(self.root, "worktrees", f"run-{call}")
                self.add_tree("-b", f"topic-{call}", path=tree)
                lost = write(os.path.join(self.shared, "package", f"lost-{call}.txt"), "gone")
                check_targets = remove_worktree.check_targets
                calls = []

                def lose_then_check(targets, phase, lost=lost, call=call) -> None:
                    calls.append(phase)
                    if len(calls) == call:
                        os.remove(lost)
                    check_targets(targets, phase)

                with mock.patch.object(remove_worktree, "check_targets", lose_then_check):
                    code, report = self.run_in_process(tree)
                self.assertEqual(code, 1, report)
                self.assertIn(f"lost entries {when}: ", report["worktrees"][0]["reason"])

    def test_a_target_entry_that_changes_kind_stops_removal(self) -> None:
        self.add_tree("-b", "topic")
        entry = write(os.path.join(self.shared, "package", "entry"), "file")
        unlink = remove_worktree.unlink

        def replace_with_directory(link: str) -> None:
            unlink(link)
            if os.path.isfile(entry):
                os.remove(entry)
                os.mkdir(entry)

        with mock.patch.object(remove_worktree, "unlink", replace_with_directory):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 1, report)
        self.assertIn("lost entries after unlinking", report["worktrees"][0]["reason"])

    def test_a_recorded_directory_that_vanishes_during_a_check_stops_removal(self) -> None:
        self.add_tree("-b", "topic")
        empty = os.path.join(self.shared, "package", "empty")
        os.makedirs(empty)
        scandir = os.scandir
        walks = []
        target_state = remove_worktree.target_state

        def count_walks(path: str):
            walks.append(path)
            return target_state(path)

        def vanish_on_second_walk(path, *args, **kwargs):
            if len(walks) == 2 and same(path) == same(empty) and os.path.isdir(empty):
                os.rmdir(empty)
            return scandir(path, *args, **kwargs)

        with mock.patch.object(remove_worktree, "target_state", count_walks), mock.patch.object(
            remove_worktree.os, "scandir", vanish_on_second_walk
        ):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 1, report)
        self.assertIn("lost entries after unlinking", report["worktrees"][0]["reason"])
        self.assertIn("empty", report["worktrees"][0]["reason"])

    @unittest.skipUnless(os.name == "nt", "Windows paths ignore letter case")
    def test_a_case_only_rename_in_a_target_does_not_stop_removal(self) -> None:
        self.add_tree("-b", "topic")
        lower = os.path.join(self.shared, "casefold")
        write(os.path.join(lower, "kept.txt"), "kept")
        unlink = remove_worktree.unlink

        def rename_case(link: str) -> None:
            unlink(link)
            if os.path.isdir(lower) and "casefold" in os.listdir(self.shared):
                os.rename(lower, os.path.join(self.shared, "CaseFold"))

        with mock.patch.object(remove_worktree, "unlink", rename_case):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertIn("CaseFold", os.listdir(self.shared))

    def test_keeps_an_uppercase_git_marker_until_other_files_are_deleted(self) -> None:
        root = os.path.join(self.root, "simulated")
        scan = remove_worktree.Scan()
        scan.directories = [root]
        scan.identities[root] = (0, 0)
        scan.files[root] = [".GIT", "tracked.txt"]
        deleted = []

        def fail_after_first_file(path: str) -> None:
            if os.path.basename(path) == "tracked.txt":
                raise OSError(errno.EIO, "deletion failed")
            deleted.append(os.path.basename(path))

        with (
            mock.patch.object(remove_worktree, "confirm_directory"),
            mock.patch.object(remove_worktree, "delete_file", fail_after_first_file),
        ):
            with self.assertRaises(OSError):
                remove_worktree.delete_contents(root, scan, remove_worktree.Waits())
        self.assertNotIn(".GIT", deleted)

    @unittest.skipUnless(os.name == "nt", "Windows accepts an uppercase worktree Git marker")
    def test_a_failed_delete_keeps_an_uppercase_worktree_git_marker(self) -> None:
        self.add_tree("-b", "topic")
        marker = os.path.join(self.tree, ".git")
        temporary_marker = os.path.join(self.tree, "metadata-tmp")
        os.rename(marker, temporary_marker)
        marker = os.path.join(self.tree, ".GIT")
        os.rename(temporary_marker, marker)
        delete_file = remove_worktree.delete_file

        def fail_after_gitignore(path: str) -> None:
            if os.path.basename(path) == ".gitignore":
                raise OSError(errno.EIO, "deletion failed")
            delete_file(path)

        with mock.patch.object(remove_worktree, "delete_file", fail_after_gitignore):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["worktrees"][0]["action"], "failed")
        self.assertTrue(os.path.isfile(marker))
        self.assertEqual(git(self.tree, "rev-parse", "--abbrev-ref", "HEAD"), "topic")

    def test_stops_on_an_entry_it_cannot_read(self) -> None:
        self.add_tree("-b", "topic")
        hidden = os.path.join(self.tree, "scratch", "hidden")
        write(os.path.join(hidden, "x.txt"))
        sibling = write(os.path.join(self.tree, "scratch", "sibling.txt"))
        listdir = os.listdir

        def refuse_hidden(path: str = ".") -> list[str]:
            if same(path) == same(hidden):
                raise PermissionError(13, "Access is denied", path)
            return listdir(path)

        with mock.patch.object(remove_worktree.os, "listdir", refuse_hidden):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "cannot read")
        self.assertTrue(os.path.isfile(sibling))
        self.assertTrue(os.path.lexists(os.path.join(self.tree, "deps")))

    def test_a_cloud_placeholder_is_not_a_link_and_does_not_block_removal(self) -> None:
        self.assertFalse(remove_worktree.is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=CLOUD_TAG)))
        self.assertTrue(remove_worktree.is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=JUNCTION_TAG)))
        self.add_tree("-b", "topic")
        synced = os.path.join(self.tree, "scratch", "synced")
        placeholders = {same(synced), same(write(os.path.join(synced, "online-only.txt")))}
        lstat = os.lstat

        def tagged(path, *arguments, **options):
            info = lstat(path, *arguments, **options)
            if same(path) not in placeholders:
                return info
            return SimpleNamespace(
                st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino, st_reparse_tag=CLOUD_TAG
            )

        with mock.patch.object(remove_worktree.os, "lstat", tagged):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertFalse(os.path.lexists(self.tree))

    def test_stops_when_a_directory_becomes_a_link_before_its_files_are_deleted(self) -> None:
        self.add_tree("-b", "topic")
        notes = os.path.join(self.tree, "scratch", "notes")
        write(os.path.join(notes, "keep.txt"))
        victim = os.path.join(self.root, "victim")
        write(os.path.join(victim, "keep.txt"))
        check_targets = remove_worktree.check_targets

        def swap_then_check(targets: dict, when: str) -> None:
            if when == "after unlinking":
                os.rename(notes, os.path.join(self.root, "notes-moved"))
                make_link(notes, victim)
            check_targets(targets, when)

        with mock.patch.object(remove_worktree, "check_targets", swap_then_check):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "changed during removal")
        self.assertTrue(os.path.isfile(os.path.join(victim, "keep.txt")))

    def test_keeps_a_file_saved_after_the_clean_check(self) -> None:
        self.add_tree("-b", "topic")
        draft = os.path.join(self.tree, "draft.txt")
        unlink = remove_worktree.unlink

        def save_then_unlink(link: str) -> None:
            if not os.path.exists(draft):
                write(draft, "saved during removal")
            unlink(link)

        with mock.patch.object(remove_worktree, "unlink", save_then_unlink):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "draft.txt changed during removal")
        self.assertTrue(os.path.isfile(draft))

    def test_keeps_a_commit_made_after_the_head_check(self) -> None:
        self.add_tree("--detach")
        unlink = remove_worktree.unlink
        made: list[str] = []

        def commit_then_unlink(link: str) -> None:
            if not made:
                write(os.path.join(self.tree, "late.txt"), "committed during removal")
                git(self.tree, "add", "late.txt")
                git(self.tree, "commit", "-q", "-m", "late work")
                made.append(git(self.tree, "rev-parse", "HEAD"))
            unlink(link)

        with mock.patch.object(remove_worktree, "unlink", commit_then_unlink):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "HEAD moved")
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), made[0])

    def started(self) -> str:
        return os.path.join(git(self.tree, "rev-parse", "--absolute-git-dir"), remove_worktree.STARTED)

    def test_a_failed_delete_leaves_git_so_a_later_run_finishes_its_deletions(self) -> None:
        self.add_tracked_tree()
        locked = os.path.join(self.tree, "zz.txt")
        delete_file = remove_worktree.delete_file

        def refuse_locked(path: str) -> None:
            if same(path) == same(locked):
                raise PermissionError(13, "The file is in use", path)
            delete_file(path)

        with (
            mock.patch.object(remove_worktree, "delete_file", refuse_locked),
            mock.patch.object(remove_worktree.time, "sleep"),
        ):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "in use")
        self.assertEqual({code for code, _ in remove_worktree.changed_paths(self.tree)}, {" D"})
        self.assertTrue(os.path.isfile(self.started()))
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed", report)
        self.assertFalse(os.path.lexists(self.tree))
        self.assertNotIn(os.path.normcase(self.tree), self.registered())

    def test_keeps_a_deliberate_deletion_without_force(self) -> None:
        self.add_tracked_tree()
        os.remove(os.path.join(self.tree, "src", "one.txt"))
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "kept", report)
        self.assertIn("uncommitted or untracked", report["worktrees"][0]["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "src", "two.txt")))

    def test_keeps_a_staged_deletion_after_a_failed_run(self) -> None:
        # A force-added ignored file, edited and then removed from the index, is a staged
        # deletion whose edited copy stays on disk as an ignored file.
        self.add_tracked_tree()
        kept = write(os.path.join(self.tree, "scratch", "kept.txt"), "tracked")
        git(self.tree, "add", "--force", "scratch/kept.txt")
        git(self.tree, "commit", "-q", "-m", "force-added file")
        write(kept, "edited")
        git(self.tree, "rm", "-q", "--cached", "scratch/kept.txt")
        write(self.started(), "left by a failed run")
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "kept", report)
        with open(kept) as file:
            self.assertEqual(file.read(), "edited")

    def test_the_wait_budget_stops_retries_across_many_blocked_files(self) -> None:
        self.add_tree("-b", "topic")
        blocked = {same(write(os.path.join(self.tree, "scratch", f"{index:02}.txt"))): 0 for index in range(15)}
        delete_file = remove_worktree.delete_file

        def release_after_five_tries(path: str) -> None:
            key = same(path)
            if key in blocked and blocked[key] < len(remove_worktree.RETRY_DELAYS):
                blocked[key] += 1
                raise PermissionError(13, "The file is in use", path)
            delete_file(path)

        with (
            mock.patch.object(remove_worktree, "delete_file", release_after_five_tries),
            mock.patch.object(remove_worktree.time, "sleep") as sleep,
        ):
            code, report = self.run_in_process(self.tree)
        waited = sum(call.args[0] for call in sleep.call_args_list)
        self.assertLessEqual(waited, remove_worktree.RETRY_BUDGET + 1e-9)
        self.assert_kept_intact(code, report, "in use")
        self.assertTrue(os.path.isfile(self.started()))

    def add_tracked_tree(self) -> None:
        """Add a tree on branch topic with tracked files in a folder and at the root."""
        self.add_tree("-b", "topic")
        for name in ("src/one.txt", "src/two.txt", "zz.txt"):
            write(os.path.join(self.tree, name), "tracked")
        git(self.tree, "add", "src", "zz.txt")
        git(self.tree, "commit", "-q", "-m", "tracked files")

    def hold_open(self, path: str):
        """Open a file the way a sync client does. On Windows the handle blocks its delete."""
        handle = open(path, "rb")
        self.addCleanup(handle.close)
        return handle

    @unittest.skipUnless(os.name == "nt", "only Windows refuses to delete an open file")
    def test_a_delete_blocked_part_way_finishes_on_a_later_run_without_force(self) -> None:
        self.add_tracked_tree()
        handle = self.hold_open(os.path.join(self.tree, "zz.txt"))
        with mock.patch.object(remove_worktree.time, "sleep"):
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["worktrees"][0]["action"], "failed", report)
        self.assertFalse(os.path.exists(os.path.join(self.tree, "src", "one.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.tree, ".git")))
        handle.close()
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed", report)
        self.assertFalse(os.path.lexists(self.tree))
        self.assertNotIn(os.path.normcase(self.tree), self.registered())
        self.assertEqual(count_all(self.shared), self.shared_count)

    @unittest.skipUnless(os.name == "nt", "only Windows refuses to delete an open file")
    def test_a_lock_released_during_the_wait_lets_the_first_run_finish(self) -> None:
        self.add_tracked_tree()
        handle = self.hold_open(os.path.join(self.tree, "zz.txt"))
        with mock.patch.object(remove_worktree.time, "sleep", side_effect=lambda _: handle.close()) as sleep:
            code, report = self.run_in_process(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed", report)
        self.assertGreater(sleep.call_count, 0)
        self.assertFalse(os.path.lexists(self.tree))
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_keeps_a_tree_with_an_edit_beside_deleted_files(self) -> None:
        self.add_tracked_tree()
        os.remove(os.path.join(self.tree, "src", "one.txt"))
        write(os.path.join(self.tree, "zz.txt"), "edited")
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "kept", report)
        self.assertIn("uncommitted or untracked", report["worktrees"][0]["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "zz.txt")))

    def test_keeps_an_edit_made_beside_deleted_files_after_the_clean_check(self) -> None:
        self.add_tracked_tree()
        edited = os.path.join(self.tree, "zz.txt")
        unlink = remove_worktree.unlink

        def edit_then_unlink(link: str) -> None:
            if os.path.exists(os.path.join(self.tree, "src", "one.txt")):
                os.remove(os.path.join(self.tree, "src", "one.txt"))
                write(edited, "edited during removal")
            unlink(link)

        with mock.patch.object(remove_worktree, "unlink", edit_then_unlink):
            code, report = self.run_in_process(self.tree)
        self.assert_kept_intact(code, report, "zz.txt")
        with open(edited) as file:
            self.assertEqual(file.read(), "edited during removal")

    def test_removes_a_link_whose_target_is_inside_the_tree(self) -> None:
        self.add_tree("-b", "topic")
        inner = os.path.join(self.tree, "scratch", "real")
        write(os.path.join(inner, "x.txt"))
        make_link(os.path.join(self.tree, "scratch", "alias"), inner)
        code, report = self.run_script(self.tree)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["worktrees"][0]["action"], "removed")
        self.assertFalse(os.path.lexists(self.tree))
        self.assertNotIn(os.path.normcase(self.tree), self.registered())
        self.assertEqual(count_all(self.shared), self.shared_count)

    def test_all_keeps_a_locked_tree_and_a_busy_tree(self) -> None:
        locked = os.path.join(self.root, "worktrees", "locked-one")
        busy = os.path.join(self.root, "worktrees", "busy-one")
        self.add_tree("--detach", path=locked)
        self.add_tree("--detach", path=busy)
        git(self.repository, "worktree", "lock", locked)
        write(os.path.join(git(busy, "rev-parse", "--absolute-git-dir"), "MERGE_HEAD"), git(busy, "rev-parse", "HEAD"))
        code, report = self.run_script("--all", "--target", "main", "--idle-hours", "0")
        self.assertEqual(code, 0, report)
        records = {os.path.basename(os.path.normpath(record["path"])): record for record in report["worktrees"]}
        self.assertEqual(records["locked-one"]["action"], "kept", report)
        self.assertEqual(records["locked-one"]["reason"], "it is locked")
        self.assertEqual(records["busy-one"]["action"], "kept", report)
        self.assertIn("MERGE_HEAD", records["busy-one"]["reason"])
        self.assertTrue(os.path.isfile(os.path.join(locked, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(busy, ".git")))
        self.assertEqual(count_all(self.shared), self.shared_count)


if __name__ == "__main__":
    unittest.main()
