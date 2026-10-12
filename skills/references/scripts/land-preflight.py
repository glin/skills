#!/usr/bin/env python3
"""Report whether a reviewed commit can safely fast-forward a dirty checkout.

Without --base, the reviewed commit must be one commit whose parent is the target HEAD.
With --base, the reviewed commit may sit on a chain of commits above that base, such as a
fix commit on top of a reviewed candidate. The base must equal the target HEAD, every commit
from the base to the reviewed commit must have exactly one parent, and the dirty-path checks
read the paths the whole range changes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


def run_git(repository: Path, arguments: Sequence[str], *, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        command = "git " + " ".join(arguments)
        raise RuntimeError(f"{command} failed: {detail}")
    return result.stdout


def git_text(repository: Path, *arguments: str) -> str:
    return run_git(repository, arguments).decode("utf-8", "replace").strip()


def git_paths(repository: Path, *arguments: str) -> list[str]:
    output = run_git(repository, (arguments[0], "-z", *arguments[1:]))
    return sorted(
        {
            value.decode("utf-8", "surrogateescape")
            for value in output.split(b"\0")
            if value
        }
    )


def repository_ignores_case(repository: Path) -> bool:
    output = run_git(repository, ("config", "--bool", "core.ignorecase"), check=False)
    value = output.decode("ascii", "replace").strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    return os.name == "nt"


def path_key(path: str, ignore_case: bool) -> str:
    return path.casefold() if ignore_case else path


def paths_collide(left: str, right: str, ignore_case: bool) -> bool:
    left_key = path_key(left, ignore_case)
    right_key = path_key(right, ignore_case)
    if left_key == right_key:
        return True
    return left_key.startswith(right_key.rstrip("/") + "/") or right_key.startswith(
        left_key.rstrip("/") + "/"
    )


def build_report(
    repository: Path, commit: str, expected_branch: str, base: str | None = None
) -> dict[str, object]:
    root = Path(git_text(repository, "rev-parse", "--show-toplevel")).resolve()
    branch = git_text(root, "branch", "--show-current") or None
    target_head = git_text(root, "rev-parse", "HEAD")
    reviewed_commit = git_text(root, "rev-parse", f"{commit}^{{commit}}")
    parent_parts = git_text(
        root, "rev-list", "--parents", "-n", "1", reviewed_commit
    ).split()
    reviewed_parent = parent_parts[1] if len(parent_parts) == 2 else None

    base_commit: str | None = None
    range_commits: list[str] = []
    range_merges: list[str] = []
    base_is_ancestor: bool | None = None
    if base is None:
        commit_paths = git_paths(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            reviewed_commit,
        )
    else:
        base_commit = git_text(root, "rev-parse", f"{base}^{{commit}}")
        base_is_ancestor = (
            subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", base_commit, reviewed_commit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
        for line in git_text(
            root, "rev-list", "--parents", f"{base_commit}..{reviewed_commit}"
        ).splitlines():
            parts = line.split()
            range_commits.append(parts[0])
            if len(parts) != 2:
                range_merges.append(parts[0])
        range_commits.reverse()
        range_merges.reverse()
        # Plumbing diff-tree ignores diff.renames, so a rename reports both of its paths.
        commit_paths = git_paths(
            root, "diff-tree", "--name-only", "-r", base_commit, reviewed_commit
        )
    unstaged_paths = git_paths(root, "diff", "--name-only")
    staged_paths = git_paths(root, "diff", "--cached", "--name-only")
    untracked_paths = git_paths(root, "ls-files", "--others", "--exclude-standard")
    unmerged_paths = git_paths(root, "diff", "--name-only", "--diff-filter=U")

    ignore_case = repository_ignores_case(root)
    commit_keys = {path_key(path, ignore_case) for path in commit_paths}
    dirty_tracked_paths = sorted(set(unstaged_paths) | set(staged_paths))
    overlapping_tracked = [
        path
        for path in dirty_tracked_paths
        if path_key(path, ignore_case) in commit_keys
    ]
    colliding_untracked = [
        untracked
        for untracked in untracked_paths
        if any(paths_collide(untracked, committed, ignore_case) for committed in commit_paths)
    ]

    blockers: list[str] = []
    if branch is None:
        blockers.append("The target checkout has a detached HEAD.")
    elif branch != expected_branch:
        blockers.append(
            f"The target checkout is on {branch}, not expected branch {expected_branch}."
        )
    if base_commit is None:
        if len(parent_parts) != 2:
            blockers.append("The reviewed commit does not have exactly one parent.")
        if reviewed_parent is not None and reviewed_parent != target_head:
            blockers.append("The reviewed commit parent does not equal the target HEAD.")
    else:
        if base_commit != target_head:
            blockers.append("The base does not equal the target HEAD.")
        if not base_is_ancestor:
            blockers.append("The base is not an ancestor of the reviewed commit.")
        elif not range_commits:
            blockers.append("The range from the base to the reviewed commit holds no commit.")
        if range_merges:
            blockers.append(
                "The range from the base to the reviewed commit holds a commit without exactly one parent."
            )
    if staged_paths:
        blockers.append("The target index contains staged files.")
    if overlapping_tracked:
        blockers.append("Dirty tracked paths overlap reviewed paths.")
    if colliding_untracked:
        blockers.append("Untracked paths collide with reviewed paths.")
    if unmerged_paths:
        blockers.append("The target checkout contains unmerged files.")

    return {
        "repository": str(root),
        "branch": branch,
        "expected_branch": expected_branch,
        "target_head": target_head,
        "reviewed_commit": reviewed_commit,
        "reviewed_commit_parent": reviewed_parent,
        "parent_matches_target": None if base_commit is not None else reviewed_parent == target_head,
        "commit_paths": commit_paths,
        "unstaged_paths": unstaged_paths,
        "staged_paths": staged_paths,
        "untracked_paths": untracked_paths,
        "unmerged_paths": unmerged_paths,
        "overlapping_tracked_paths": overlapping_tracked,
        "colliding_untracked_paths": colliding_untracked,
        "base": base_commit,
        "base_matches_target": None if base_commit is None else base_commit == target_head,
        "range_commits": range_commits,
        "range_merge_commits": range_merges,
        "blockers": blockers,
        "can_fast_forward": not blockers,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether a reviewed commit can safely fast-forward a checkout."
    )
    parser.add_argument("--repository", default=".", help="Target checkout path.")
    parser.add_argument("--commit", required=True, help="Reviewed commit or ref.")
    parser.add_argument(
        "--expected-branch", required=True, help="Target branch recorded before implementation."
    )
    parser.add_argument(
        "--base",
        help=(
            "Commit the reviewed work was built on. Checks the whole range from it to the"
            " reviewed commit instead of the reviewed commit alone."
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        report = build_report(
            Path(arguments.repository),
            arguments.commit,
            arguments.expected_branch,
            arguments.base,
        )
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
