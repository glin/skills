#!/usr/bin/env python3
"""Run a gate command on one commit and write a receipt only when the gate passes.

The tree must hold the named commit with no tracked or untracked changes before and after
the run. The script deletes any earlier receipt before it starts, so a failed or interrupted
run leaves no receipt. The command runs under sh on every platform, as carry-review.sh runs
its --verify command, so one command string means the same thing in both scripts.
land-preflight.py --gate-receipt reads the receipt.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def git_text(tree: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(tree), *arguments], check=False, capture_output=True
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.decode("utf-8", "replace").strip()


def tree_problems(tree: Path, commit: str) -> list[str]:
    problems: list[str] = []
    head = git_text(tree, "rev-parse", "HEAD")
    if head != commit:
        problems.append(f"The tree holds {head}, not {commit}.")
    changes = git_text(tree, "status", "--porcelain", "--untracked-files=all")
    if changes:
        problems.append("The tree has uncommitted or untracked files:\n" + changes)
    return problems


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_tail(log: Path, lines: int = 40) -> str:
    text = log.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a gate command on one commit and write a receipt when it passes."
    )
    parser.add_argument("--tree", required=True, help="Worktree that holds the commit.")
    parser.add_argument("--commit", required=True, help="Commit the gate must run on.")
    parser.add_argument("--command", required=True, help="Full gate command, run by the shell.")
    parser.add_argument("--receipt", required=True, help="Receipt path, outside the tree.")
    parser.add_argument("--log", help="Gate output path. Defaults to gate.log beside the receipt.")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    receipt = Path(arguments.receipt).resolve()
    log = Path(arguments.log).resolve() if arguments.log else receipt.with_name("gate.log")
    try:
        tree = Path(git_text(Path(arguments.tree), "rev-parse", "--show-toplevel")).resolve()
        if receipt.is_relative_to(tree) or log.is_relative_to(tree):
            raise RuntimeError("The receipt and the log must sit outside the tree.")
        shell = shutil.which("sh")
        if shell is None:
            raise RuntimeError("sh is not on PATH.")
        receipt.unlink(missing_ok=True)
        commit = git_text(tree, "rev-parse", f"{arguments.commit}^{{commit}}")
        problems = tree_problems(tree, commit)
        if problems:
            print("gate not run:\n" + "\n".join(problems), file=sys.stderr)
            return 1
        log.parent.mkdir(parents=True, exist_ok=True)
        started = now()
        with log.open("wb") as output:
            exit_code = subprocess.run(
                [shell, "-c", arguments.command], cwd=tree, stdout=output, stderr=subprocess.STDOUT
            ).returncode
        finished = now()
        problems = tree_problems(tree, commit)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if exit_code != 0 or problems:
        print(log_tail(log), file=sys.stderr)
        print(f"gate failed: exit {exit_code}, log {log}", file=sys.stderr)
        if problems:
            print("the gate changed the tree:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    record = {
        "commit": commit,
        "tree": str(tree),
        "command": arguments.command,
        "exit_code": exit_code,
        "started": started,
        "finished": finished,
        "log": str(log),
    }
    partial = receipt.with_name(receipt.name + ".partial")
    partial.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, receipt)
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
