#!/usr/bin/env python3
"""Remove finished Git worktrees without losing a commit, a file, or a linked directory.

  remove-worktree.py --repository <checkout> [--target <branch>] [--force] <worktree>...
  remove-worktree.py --repository <checkout> --target <branch> --all --idle-hours <hours>

A named worktree is removed when it is unlocked, has no merge, rebase, cherry-pick or revert
in progress, and holds no uncommitted or untracked file. --force removes it with such files.
A HEAD that no branch carries first gets the branch archive/<worktree folder name>.
With --all, a worktree also needs a HEAD that the target branch contains and no Git
activity within --idle-hours.

Removal never follows a link. On Windows, `git worktree remove` and a recursive delete can
walk through a directory junction and delete the files behind it. This script instead:

  1. refuses a worktree whose own path is a link,
  2. walks the tree with lstat, never descends into a symbolic link, junction or mount
     point, and stops on any entry it cannot read or any nested Git checkout,
  3. records every file and directory below each external link target without following links,
  4. unlinks every link, then walks again and stops if one remains,
  5. checks HEAD and the uncommitted files again, and keeps the tree when either changed,
  6. deletes the tree bottom-up, checks each directory again before it deletes the
     directory's entries, and deletes the worktree's .git last. A delete that another
     process blocks for a moment, such as a sync client that holds a new file open, is
     tried again for about three seconds. The waits for one tree stop after 30 seconds in
     all, and then each blocked delete fails at once,
  7. stops, after unlinking, before deleting files and after the delete, when an entry
     recorded below an external link target is gone or has another kind, or when a target
     cannot be checked. An entry that another process adds, or a file it rewrites, does not
     count, because a removal that followed a link would only delete. On Windows, a rename
     that changes only letter case does not count either,
  8. runs `git worktree prune`.

Before the first delete, the script writes the file remove-worktree-started in the worktree's
Git directory. A run that fails part way leaves .git and that file in place, so Git still
knows the checkout and a later run can finish the removal. While the file exists, a tracked
file deleted from disk, with the index still matching HEAD, counts as clean. A deletion
staged in the index never does, because the file can still be on disk as an ignored file.

With --target, the worktree's branch is then deleted with `git branch -d`, which keeps a
branch that is not merged.

--dry-run reports every decision without changing anything. The report is JSON on stdout.
Exit status: 0 when nothing failed, 1 when a removal failed, 2 for a usage or Git error.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import subprocess
import sys
import time
from typing import Callable

# A Windows reparse tag with this bit set names another path: a symbolic link, a directory
# junction or a volume mount point. Other reparse points, such as cloud placeholders, are
# ordinary files or directories.
NAME_SURROGATE = 0x20000000
# The waits between tries of a delete that another process blocks. They total 3.1 seconds.
RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)
# The seconds of waiting that one tree's removal may spend on all its blocked deletes.
RETRY_BUDGET = 30.0
# The file in a worktree's Git directory that shows a removal started deleting the tree.
STARTED = "remove-worktree-started"
# Windows reports a sharing violation as EACCES and a folder that holds a file with a pending
# delete as ENOTEMPTY. POSIX reports a busy file as EBUSY and a held one as EPERM.
TRANSIENT = frozenset((errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY))
IN_PROGRESS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


class Refused(RuntimeError):
    """The removal stopped before it could lose data."""


def git(directory: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", directory, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result


def same_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def inside(path: str, root: str) -> bool:
    path, root = same_path(path), same_path(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def registered_worktrees(repository: str) -> list[dict]:
    entries: list[dict] = []
    for line in git(repository, "worktree", "list", "--porcelain").stdout.splitlines():
        field, _, value = line.partition(" ")
        if field == "worktree":
            entries.append(
                {"path": value, "head": None, "branch": None, "locked": False, "prunable": False}
            )
        elif entries and field == "HEAD":
            entries[-1]["head"] = value
        elif entries and field == "branch":
            entries[-1]["branch"] = value.removeprefix("refs/heads/")
        elif entries and field in ("locked", "prunable"):
            entries[-1][field] = True
    return entries


def is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_reparse_tag", 0) & NAME_SURROGATE)


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def check_root(root: str) -> os.stat_result:
    """Return the worktree root's lstat result, or stop when the root is a link."""
    try:
        info = os.lstat(root)
    except OSError as error:
        raise Refused(f"cannot read {root}: {error}") from error
    if is_link(info):
        raise Refused(f"{root} is a link, and removing through it would delete the files it points at")
    if not stat.S_ISDIR(info.st_mode):
        raise Refused(f"{root} is not a directory")
    return info


class Scan:
    """What one walk found under a root. Directories come parent first, the root first of all."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.directories: list[str] = []
        self.identities: dict[str, tuple[int, int]] = {}
        self.files: dict[str, list[str]] = {}


def walk(root: str) -> Scan:
    """Record the links, directories and files under root, without following a link.

    Any entry that cannot be read stops the walk.
    """
    info = check_root(root)
    scan = Scan()
    scan.directories.append(root)
    scan.identities[root] = identity(info)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            names = sorted(os.listdir(directory))
        except OSError as error:
            raise Refused(f"cannot read {directory}: {error}") from error
        files = scan.files.setdefault(directory, [])
        for name in names:
            path = os.path.join(directory, name)
            if name.casefold() == ".git" and directory != root:
                raise Refused(f"it contains a nested Git checkout: {directory}")
            try:
                entry = os.lstat(path)
            except OSError as error:
                raise Refused(f"cannot read {path}: {error}") from error
            if is_link(entry):
                scan.links.append(path)
            elif stat.S_ISDIR(entry.st_mode):
                if entry.st_dev != info.st_dev:
                    raise Refused(f"{path} is a mount point")
                scan.directories.append(path)
                scan.identities[path] = identity(entry)
                pending.append(path)
            else:
                files.append(name)
    scan.links.sort()
    return scan


def target_state(path: str) -> set[tuple[str, str]]:
    """Record the relative path and kind of every entry below a linked target without following links.

    Paths are compared in the platform's letter case, so on Windows a rename that changes only case
    keeps the entry. An entry that disappears while the walk reads it is left out. The target itself
    must still exist.
    """
    entries: set[tuple[str, str]] = set()
    pending = [(path, "")]
    while pending:
        current, relative = pending.pop()
        try:
            info = os.lstat(current)
        except FileNotFoundError as error:
            if not relative:
                raise Refused(f"linked target {path} is gone") from error
            continue
        except OSError as error:
            raise Refused(f"cannot read linked target {current}: {error}") from error
        kind = "link" if is_link(info) else "dir" if stat.S_ISDIR(info.st_mode) else "file"
        if kind != "dir":
            entries.add((os.path.normcase(relative), kind))
            continue
        try:
            with os.scandir(current) as listing:
                names = [entry.name for entry in listing]
        except FileNotFoundError as error:
            if not relative:
                raise Refused(f"linked target {path} is gone") from error
            continue
        except OSError as error:
            raise Refused(f"cannot read linked target {current}: {error}") from error
        entries.add((os.path.normcase(relative), kind))
        pending.extend((os.path.join(current, name), os.path.join(relative, name)) for name in names)
    return entries


def unlink(link: str) -> None:
    """Remove a link and never what it points at."""
    try:
        os.unlink(link)
    except (IsADirectoryError, PermissionError):
        os.rmdir(link)


def delete_file(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


class Waits:
    """The seconds of waiting left for one tree's removal."""

    def __init__(self, seconds: float = RETRY_BUDGET) -> None:
        self.left = seconds


def retried(action: Callable[[str], None], path: str, waits: Waits) -> None:
    """Run a delete, and try again while another process blocks it for a moment.

    Each wait comes out of waits. When none is left, a blocked delete fails at once.
    """
    for delay in RETRY_DELAYS:
        try:
            return action(path)
        except OSError as error:
            if error.errno not in TRANSIENT or waits.left <= 0:
                raise
        delay = min(delay, waits.left)
        waits.left -= delay
        time.sleep(delay)
    return action(path)


def check_targets(targets: dict[str, set[tuple[str, str]]], when: str) -> None:
    """Stop when an entry recorded below a linked target is gone or changed its kind."""
    for target, before in targets.items():
        lost = sorted(relative for relative, _ in before - target_state(target))
        if lost:
            shown = ", ".join(lost[:5]) + (f" and {len(lost) - 5} more" if len(lost) > 5 else "")
            raise Refused(f"linked target {target} lost entries {when}: {shown}")


def confirm_directory(directory: str, expected: tuple[int, int]) -> None:
    """Stop when a directory the walk recorded is now a link or another directory."""
    try:
        info = os.lstat(directory)
    except OSError as error:
        raise Refused(f"cannot read {directory}: {error}") from error
    if is_link(info) or not stat.S_ISDIR(info.st_mode) or identity(info) != expected:
        raise Refused(f"{directory} changed during removal and can now lead outside the worktree")


def delete_contents(root: str, scan: Scan, waits: Waits) -> None:
    """Delete the scanned tree bottom-up, with the worktree's .git last."""
    marker_names = scan.files.get(root, ()) + [
        os.path.basename(path)
        for path in scan.directories
        if os.path.dirname(path) == root and os.path.basename(path).casefold() == ".git"
    ]
    marker_name = ".git" if ".git" in marker_names else next(
        (name for name in marker_names if name.casefold() == ".git"), ".git"
    )
    marker = os.path.join(root, marker_name)

    def clear(directory: str, keep: tuple[str, ...] = ()) -> None:
        confirm_directory(directory, scan.identities[directory])
        for name in scan.files.get(directory, ()):
            if name not in keep:
                retried(delete_file, os.path.join(directory, name), waits)

    def in_marker(path: str) -> bool:
        return path == marker or path.startswith(marker + os.sep)

    below = [directory for directory in reversed(scan.directories) if directory != root]
    for directory in [path for path in below if not in_marker(path)]:
        clear(directory)
        retried(os.rmdir, directory, waits)
    clear(root, keep=(os.path.basename(marker),))
    for directory in [path for path in below if in_marker(path)]:
        clear(directory)
        retried(os.rmdir, directory, waits)
    if os.path.basename(marker) in scan.files.get(root, ()):
        confirm_directory(root, scan.identities[root])
        retried(delete_file, marker, waits)
    retried(os.rmdir, root, waits)


def delete_tree(root: str, before_delete: Callable[[list[str]], None] | None = None) -> dict:
    """Unlink every link under root, then delete root without following anything.

    before_delete runs after the links are gone and before the first file is deleted. It
    receives the links, and it stops the removal by raising.
    """
    first = walk(root)
    targets: dict[str, set[tuple[str, str]]] = {}
    for link in first.links:
        target = os.path.realpath(link)
        if not inside(target, root) and target not in targets:
            targets[target] = target_state(target)
    waits = Waits()
    for link in first.links:
        retried(unlink, link, waits)
    scan = walk(root)
    if scan.links:
        raise Refused("a link survived unlinking: " + ", ".join(scan.links))
    check_targets(targets, "after unlinking")
    if before_delete:
        before_delete(first.links)
    check_targets(targets, "before deleting files")
    delete_contents(root, scan, waits)
    check_targets(targets, "after the delete")
    return {"links": first.links, "link_targets": {target: len(entries) for target, entries in targets.items()}}


def changed_paths(path: str, ignored: set[str] = frozenset()) -> list[tuple[str, str]]:
    """Return the status code and path of each uncommitted or untracked path in a worktree.

    Paths in ignored are left out.
    """
    fields = git(path, "status", "--porcelain", "-z", "--untracked-files=all").stdout.split("\0")
    changed: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if field[0] in "RC":
            index += 1
        name = field[3:]
        if name.rstrip("/") not in ignored:
            changed.append((field[:2], name))
    return changed


def unsaved_paths(path: str, git_dir: str, ignored: set[str] = frozenset()) -> list[str]:
    """Return the changed paths in a worktree that removing it would lose.

    A removal that stopped part way leaves tracked files deleted from disk, and leaves the
    STARTED file in git_dir. While that file exists and every change is such a deletion,
    nothing is returned. The removal archived an unbranched HEAD before it deleted anything,
    so a branch holds each deleted file.
    """
    changed = changed_paths(path, ignored)
    finishing = os.path.exists(os.path.join(git_dir, STARTED))
    if changed and finishing and all(code == " D" for code, _ in changed):
        return []
    return [name for _, name in changed]


def last_activity(git_dir: str) -> float:
    """Return when Git last moved HEAD or wrote the index in this worktree.

    Git maintenance can rewrite logs/HEAD for every worktree at once, so that file's time
    shows nothing. The time recorded in its newest entry shows the last HEAD move.
    """
    paths = [os.path.join(git_dir, name) for name in ("HEAD", "index", "ORIG_HEAD", "FETCH_HEAD")]
    times = [os.path.getmtime(path) for path in paths if os.path.exists(path)]
    try:
        with open(os.path.join(git_dir, "logs", "HEAD"), encoding="utf-8", errors="replace") as log:
            entries = log.read().splitlines()
        if entries:
            times.append(float(entries[-1].split("\t", 1)[0].split()[-2]))
    except (OSError, ValueError, IndexError):
        pass
    return max(times, default=0.0)


def archive_name(entry: dict) -> str:
    return "archive/" + os.path.basename(os.path.normpath(entry["path"]))


def nested_worktree(repository: str, path: str) -> str | None:
    root = same_path(path)
    for entry in registered_worktrees(repository):
        other = same_path(entry["path"])
        if other != root and inside(other, root):
            return entry["path"]
    return None


def keep_reason(repository: str, arguments: argparse.Namespace, entry: dict) -> str | None:
    path = entry["path"]
    nested = nested_worktree(repository, path)
    if nested:
        return f"it contains another registered worktree: {nested}"
    if entry["prunable"] or not os.path.isdir(path):
        return "its directory is missing, so git worktree prune owns it"
    if entry["locked"]:
        return "it is locked"
    if not entry["head"]:
        return "it has no HEAD commit"
    if arguments.all:
        walk(path)
    git_dir = git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    if arguments.idle_hours is not None:
        idle = (time.time() - last_activity(git_dir)) / 3600
        if idle < arguments.idle_hours:
            return f"Git touched it {idle:.1f} hours ago"
    busy = [name for name in IN_PROGRESS if os.path.exists(os.path.join(git_dir, name))]
    if busy:
        return "an operation is in progress: " + ", ".join(busy)
    head = entry["head"]
    if arguments.all:
        if git(repository, "merge-base", "--is-ancestor", head, arguments.target, check=False).returncode:
            return f"{arguments.target} does not contain its HEAD"
    elif not carried(repository, head):
        name = archive_name(entry)
        if git(repository, "check-ref-format", "--branch", name, check=False).returncode:
            return f"its HEAD is on no branch, and {name} is not a valid branch name"
        existing = git(repository, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False)
        if existing.returncode == 0:
            return f"its HEAD is on no branch, and {name} already names another commit"
    if not arguments.force and unsaved_paths(path, git_dir):
        return "it holds uncommitted or untracked files"
    return None


def carried(repository: str, head: str) -> bool:
    return bool(git(repository, "branch", "--contains", head, "--format=%(refname:short)").stdout.strip())


def remove(repository: str, arguments: argparse.Namespace, entry: dict, record: dict) -> None:
    path = entry["path"]
    nested = nested_worktree(repository, path)
    if nested:
        raise Refused(f"it contains another registered worktree: {nested}")
    check_root(path)
    walk(path)
    if not carried(repository, entry["head"]):
        name = archive_name(entry)
        git(repository, "branch", name, entry["head"])
        record["archived_as"] = name
    git_dir = git(path, "rev-parse", "--absolute-git-dir").stdout.strip()

    def unchanged(links: list[str]) -> None:
        # The checks in keep_reason ran before both walks. A commit or a saved file since then
        # is still only in this tree, so check again just before the first delete.
        kept = "so the tree was kept without its dependency links"
        head = git(path, "rev-parse", "HEAD").stdout.strip()
        if head != entry["head"]:
            raise Refused(f"its HEAD moved to {head} during removal, {kept}")
        if not arguments.force:
            ignored = {os.path.relpath(link, path).replace(os.sep, "/") for link in links}
            changed = unsaved_paths(path, git_dir, ignored)
            if changed:
                raise Refused(f"{', '.join(changed)} changed during removal, {kept}")
        # git worktree prune deletes git_dir with this file once the tree is gone. A run that
        # fails part way leaves it for the next run.
        with open(os.path.join(git_dir, STARTED), "w", encoding="utf-8") as started:
            started.write(entry["head"] + "\n")

    record.update(delete_tree(path, unchanged))
    git(repository, "worktree", "prune")
    if any(same_path(item["path"]) == same_path(path) for item in registered_worktrees(repository)):
        raise Refused(f"git worktree prune left {path} registered")
    record["action"] = "removed"
    if arguments.target and entry["branch"] and entry["branch"] != arguments.target:
        deleted = git(repository, "branch", "-d", entry["branch"], check=False)
        record["branch_result"] = "deleted" if deleted.returncode == 0 else "kept, not merged"


def run(argv: list[str] | None = None) -> tuple[int, dict]:
    parser = argparse.ArgumentParser(description="Remove finished Git worktrees without losing work.")
    parser.add_argument("--repository", default=".", help="Any checkout of the repository.")
    parser.add_argument("--target", help="The branch the work landed on. Needed with --all.")
    parser.add_argument("--all", action="store_true", help="Consider every linked worktree.")
    parser.add_argument("--idle-hours", type=float, help="Keep a worktree Git touched more recently.")
    parser.add_argument("--force", action="store_true", help="Remove a tree with uncommitted files.")
    parser.add_argument("--dry-run", action="store_true", help="Report without removing anything.")
    parser.add_argument("worktrees", nargs="*", help="Paths of the worktrees to remove.")
    arguments = parser.parse_args(argv)
    if arguments.all == bool(arguments.worktrees):
        parser.error("name worktrees or pass --all, not both")
    if arguments.all and (arguments.idle_hours is None or not arguments.target):
        parser.error("--all needs --target and --idle-hours")
    if arguments.all and arguments.force:
        parser.error("--force works only on named worktrees")

    try:
        if arguments.target:
            git(arguments.repository, "rev-parse", "--verify", f"refs/heads/{arguments.target}")
        entries = registered_worktrees(arguments.repository)
    except RuntimeError as error:
        return 2, {"error": str(error)}

    main_path = same_path(entries[0]["path"])
    by_path = {same_path(entry["path"]): entry for entry in entries[1:]}
    report: list[dict] = []
    chosen: list[dict] = []
    if arguments.all:
        chosen = entries[1:]
    else:
        for path in arguments.worktrees:
            entry = by_path.get(same_path(path))
            if entry is None or same_path(path) == main_path:
                report.append({"path": path, "action": "kept", "reason": "it is not a linked worktree"})
            else:
                chosen.append(entry)

    failed = False
    for entry in chosen:
        record: dict = {"path": entry["path"], "branch": entry["branch"]}
        try:
            reason = keep_reason(arguments.repository, arguments, entry)
        except RuntimeError as error:
            reason = str(error)
        if reason:
            record.update(action="kept", reason=reason)
        elif arguments.dry_run:
            try:
                record.update(action="would remove", links=walk(entry["path"]).links)
                if not carried(arguments.repository, entry["head"]):
                    record["would_archive_as"] = archive_name(entry)
            except (OSError, RuntimeError) as error:
                record.update(action="kept", reason=str(error))
        else:
            try:
                remove(arguments.repository, arguments, entry, record)
            except (OSError, RuntimeError) as error:
                record.update(action="failed", reason=str(error))
                failed = True
        report.append(record)

    counts: dict[str, int] = {}
    for record in report:
        counts[record["action"]] = counts.get(record["action"], 0) + 1
    return (1 if failed else 0), {"counts": counts, "worktrees": report}


def main() -> int:
    code, report = run()
    if code == 2:
        print(f"remove-worktree: {report['error']}", file=sys.stderr)
        return 2
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
