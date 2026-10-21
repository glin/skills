#!/usr/bin/env python3
"""List the files in a plan folder that are too large to keep.

  large-files.py <folder>... [--limit-mb 2]

Prints one line per file above the limit, largest first: the size in MB, a tab, and the
path. The walk skips `.git` and never enters or counts a symbolic link or a Windows
junction, because the files behind a link are not plan content. A plans repository that
tracks only Markdown never shows these files in `git status`, so this list is the only place
they appear.

Exit status: 0 when no file is above the limit, 1 when at least one is, 2 for a usage error
or an entry the walk cannot read.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import sys

MB = 1024 * 1024
# A Windows reparse tag with this bit set names another path: a symbolic link, a directory
# junction or a volume mount point. Other reparse points, such as cloud placeholders, are
# ordinary files or directories.
NAME_SURROGATE = 0x20000000


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & NAME_SURROGATE)


def large_files(folder: str, limit: int) -> list[tuple[int, str]]:
    """Return the files above limit under folder. Raise OSError for an entry it cannot read."""
    found: list[tuple[int, str]] = []
    pending = [folder]
    while pending:
        directory = pending.pop()
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if is_link(info):
                continue
            if stat.S_ISDIR(info.st_mode):
                if name != ".git":
                    pending.append(path)
            elif info.st_size > limit:
                found.append((info.st_size, path))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List files in a plan folder above a size limit.")
    parser.add_argument("folders", nargs="*", help="Plan folders to check.")
    parser.add_argument("--limit-mb", type=float, default=2.0, help="The size limit in MB.")
    parser.add_argument("--manifest", help="Explicitly adopted disposition manifest. Enables the shared closure check.")
    parser.add_argument("--plan", help="Plan containing the generated Runtime resources view.")
    parser.add_argument("--resource-view", help="Standalone generated resource view for a run without a plan.")
    parser.add_argument("--owner", help="Check only this independently owned child.")
    arguments = parser.parse_args(argv)
    if arguments.manifest:
        if arguments.folders or bool(arguments.plan) == bool(arguments.resource_view):
            parser.error("--manifest requires exactly one of --plan or --resource-view and no positional folders")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_closure.py")
        spec = importlib.util.spec_from_file_location("check_closure", script)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        command = ["check", "--manifest", arguments.manifest]
        command.extend(["--plan", arguments.plan] if arguments.plan else ["--resource-view", arguments.resource_view])
        if arguments.owner:
            command.extend(["--owner", arguments.owner])
        return adapter.main(command)
    if not arguments.folders or arguments.plan or arguments.resource_view or arguments.owner:
        parser.error("positional folders are required without --manifest")
    found: list[tuple[int, str]] = []
    for folder in arguments.folders:
        if not os.path.isdir(folder):
            print(f"large-files: {folder} is not a folder", file=sys.stderr)
            return 2
        try:
            found.extend(large_files(folder, int(arguments.limit_mb * MB)))
        except OSError as error:
            print(f"large-files: cannot read {error.filename}: {error.strerror}", file=sys.stderr)
            return 2
    for size, path in sorted(found, reverse=True):
        print(f"{size / MB:.1f}\t{path}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
