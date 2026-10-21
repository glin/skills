#!/usr/bin/env python3
"""Nest dated plan folders by year and month, and repoint every path that names them.

  plan_paths.py plan <plans-root> --scope archive|active|all [--exclude <glob>]... (--dry-run | --apply | --verify | --rollback)
  plan_paths.py move <plans-root> <from> <to> [--exclude <glob>]... (--dry-run | --apply | --rollback)
  plan_paths.py resolve <plans-root> <old path>

`plan` moves each dated item in the scope to `<root>/YYYY/MM/YYYY-MM-DD-<slug>/`. A dated item is a
folder or a Markdown file named `YYYY-MM-DD-<slug>`. A dated Markdown file becomes `plan.md` in a
folder of its own name. The active scope covers the plans root and `retrospectives/`. The archive
scope covers `archive/`. `move` moves one folder or file, such as a plan into `archive/`. `resolve`
prints the current path of the plan that an old path names, found by its folder name.

The plans root and the Git root can differ: a plans folder tracked by its enclosing repository works
the same way as an embedded plans repository.

`--apply` runs these steps under a journal in the Git directory:

  1. Commit the renames from a private index read from HEAD.
  2. Rename each item on disk. A rename carries untracked and ignored files.
  3. Commit the rewritten references from a private index.
  4. Rewrite the working-tree copies, untracked and ignored files included, without staging them.
  5. Reset the shared index at the paths the two commits changed, so it matches the second commit.

Each commit runs the pre-commit, prepare-commit-msg and commit-msg hooks, then `git commit-tree` on
the HEAD that its private index was read from. A hook that changes the private index stops the run.
The commit is signed when `commit.gpgSign` is true. The branch moves only while HEAD still names
that commit. When another session commits in between, the run stops and no ref moves.

Another session's unstaged changes, untracked files and staged entries at other paths stay as they
were. A second `--apply` resumes from the first step not done. A resumed run refuses any staged
entry at a migration path that the migration did not cause, inside a moved folder too. Two items
that would land on one path stop the run before the first commit.

The migration paths are each moved item, at its old and its new path, and each file that the
reference rewrite changes. The preflight refuses a staged entry, a commit from another session in
the last 15 minutes, and an open task run only at a migration path. Elsewhere each one is a
preflight line. A recently changed file in a run folder never refuses, because a writer whose path
moved finds the new one with `resolve`.

`--rollback` undoes the steps that were done, under the same journal. It refuses when a commit after
the baseline that the migration did not make changed a migration path, or when an old path exists
again. A
second `--rollback` resumes an interrupted rollback, and `--apply` refuses until it finishes.

A reference changes when its source or its target moves. A relative path is recalculated from both
ends and written back with the same separators. A path inside an angle-bracket Markdown destination
or a quoted string is read whole, spaces included. A path that names the plans root, such as
`.claude/plans/<slug>` or an absolute path in either slash form, has its plan segment replaced. On
Windows that match ignores letter case. A `.gitignore` rule changes only when it is anchored. A
relative path that does not resolve is reported and left as it is.

In a Markdown file, a fenced code block changes only when its info string names runnable code, such
as `bash`, `python` or `json`. An untagged fence and any other tag, such as `text`, `console` or
`diff`, keep their bytes.

Frozen evidence keeps its bytes: any path with a `runs/`, `reports/` or `blind/` folder, any file
named `prompt.md`, and any file whose SHA-256 another tracked text file records. A recorded digest
never freezes a file named `plan.md` or a file that sits directly in the plans root.

`--exclude <glob>` repeats. A path that matches any glob never changes, tracked or not. The glob
matches the path relative to the Git root, written with forward slashes, by `fnmatch`. The dry run
counts the excluded files that a rewrite would have changed. `--apply` records the globs in the
journal, and a resumed run uses that set.

`--verify` fails while the journal holds an unfinished run or rollback, on any inventory problem, and on
any dated item left at the old depth in the scope.

`--apply` and `--rollback` need `--agent` and `--session` for the commit trailers. They print the
preflight and exit 2 until `--verified-quiet` confirms the executor checked for conflicting writers.
`--owner-confirmed-quiet` also accepts an owner's confirmation. Neither flag bypasses a refusal.
An unrelated agent process is informational. An agent naming an absolute migration path refuses.
Every run ends with its elapsed time and peak memory on stderr.

Exit status: 0 on success, 1 when the preflight refuses, a check fails or an old path does not
resolve, 2 for a usage error or a stop that requires action.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import datetime
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

DATED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-.+")
YEAR = re.compile(r"^\d{4}$")
MONTH = re.compile(r"^\d{2}$")
SCOPES = {"archive": ("archive",), "active": ("", "retrospectives"), "all": ("", "archive", "retrospectives")}
FROZEN_FOLDERS = frozenset({"runs", "reports", "blind"})
SKIP_FOLDERS = frozenset({".git", "node_modules"})
BINARY_SUFFIXES = frozenset(
    ".png .webp .jpg .jpeg .gif .bmp .ico .gz .zip .7z .tar .pdf .woff .woff2 .ttf .otf .exe .dll "
    ".node .wasm .mp4 .webm .mov .mp3 .wav .sqlite .db .pyc".split()
)
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_RUN_FILE_BYTES = 2 * 1024 * 1024
RECENT_SECONDS = 15 * 60
REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a symlink or a Windows junction
JOURNAL = "plan-paths-journal.json"
PRIVATE_INDEX = "plan-paths-index"
WORK_FOLDER = "plan-paths-work"
LIST_LIMIT = 20
QUIET_PROMPT = (
    "Check that no other writer is changing the moved items or the files that name them. "
    "Then rerun with --verified-quiet. Ask the owner only about a conflicting writer or unresolved ownership."
)
SESSION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
AGENT = re.compile(r"^\S.*\(effort \S+, \S.*\)$")
AGENT_NAMES = frozenset({"claude", "claude.exe", "codex", "codex.exe"})
AGENT_PACKAGES = ("@anthropic-ai/claude-code", "@openai/codex")
# A task stop line names its run by the full run start, and a run folder ends in the run id
# `HHMMSS-<16 hex digits>` that the run start's clock time forms.
STOP_LINE = re.compile(rb"stopped spend:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
RUN_FOLDER = re.compile(r"^(\d{4}-\d{2}-\d{2})-.*?(\d{6})-[0-9a-f]{16}$")
RUN_ID = re.compile(r"^(\d{6})-[0-9a-f]{16}$")
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
RUNNABLE_FENCES = frozenset(
    "bash sh shell zsh powershell ps1 pwsh cmd bat python py js mjs javascript ts typescript json jsonc yaml yml toml".split()
)
# A fence line: indent and quote marks, then any list markers (`-`, `*`, `+`, `1.`, `1)`), then the fence.
FENCE = re.compile(
    rb"^(?P<lead>[ \t>]*(?P<items>(?:(?:[-*+]|\d{1,9}[.)])[ \t]+[ \t>]*)*))(?P<fence>`{3,}|~{3,})(?P<info>.*)$"
)
INDENT = re.compile(rb"[ \t>]*")

# Path bytes: ASCII word characters, a few punctuation marks that file names use, and every byte
# of a multi-byte UTF-8 character.
PATH_BYTES = rb"\w.~+@%\x80-\xff-"
SEPARATOR = rb"(?:\\\\|\\|/)"
NOT_AFTER_PATH = rb"(?<![\w.~/\\\x80-\xff-])"
RELATIVE = re.compile(NOT_AFTER_PATH + rb"(?:\.\.?" + SEPARATOR + rb")+[" + PATH_BYTES + rb"/\\]*")
DATED_TOKEN = re.compile(
    NOT_AFTER_PATH
    + rb"(?:(?:archive|retrospectives)"
    + SEPARATOR
    + rb")?\d{4}-\d{2}-\d{2}-["
    + PATH_BYTES
    + rb"]+(?:"
    + SEPARATOR
    + rb"[" + PATH_BYTES + rb"/\\]*)?"
)
# A whole angle-bracket Markdown destination, or a whole quoted string that starts like a relative or
# dated path. Either may hold spaces.
DELIMITED = re.compile(
    rb"\]\(<(?P<angle>[^<>\r\n]+)>"
    rb"|(?P<quote>[\"'`])(?P<quoted>(?:\.{1,2}[/\\]|(?:(?:archive|retrospectives)[/\\])?\d{4}-\d{2}-\d{2}-)[^\"'`\r\n]*?)(?P=quote)"
)
DELIMITED_START = re.compile(rb"^(?:\.{1,2}[/\\]|(?:(?:archive|retrospectives)[/\\])?\d{4}-\d{2}-\d{2}-)")
DIGEST = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")
GLOB = re.compile(rb"[*?\[\\]")
PREFIX_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.~+@%-/\\:") | frozenset(
    range(0x80, 0x100)
)


class Stop(Exception):
    """A condition that ends the run and needs the owner."""


def checkpoint(index: int, op: dict) -> None:
    """Called after each journal operation or rollback step is saved. Tests replace it to interrupt a run."""


def new_path(name: str) -> str:
    """Map `YYYY-MM-DD-slug` to `YYYY/MM/YYYY-MM-DD-slug`. Raise ValueError for a name without a valid date."""
    match = DATED.match(name)
    if not match:
        raise ValueError(f"{name} does not start with a YYYY-MM-DD- date")
    year, month, day = (int(group) for group in match.groups())
    try:
        datetime.date(year, month, day)
    except ValueError:
        raise ValueError(f"{name} does not start with a valid date") from None
    return f"{match.group(1)}/{match.group(2)}/{name}"


def to_text(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def to_bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_env(index: str | None = None) -> dict[str, str]:
    drop = {"GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX"}
    env = {key: value for key, value in os.environ.items() if key not in drop}
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def posix_absolute(path: str) -> str:
    path = path.replace("\\", "/")
    return path if path.startswith("/") else "/" + path


def native(path: str) -> str:
    return path[1:] if os.name == "nt" and re.match(r"^/[A-Za-z]:", path) else path


def normal_prefix(text: str) -> str:
    """Lower-case a folder path with forward slashes, for comparing two spellings of one folder."""
    text = text.replace("\\", "/")
    text = re.sub(r"^file:/*", "/", text, flags=re.IGNORECASE)
    if text.startswith("~"):
        text = os.path.expanduser("~").replace("\\", "/") + text[1:]
    text = re.sub(r"/+", "/", text)
    text = re.sub(r"^/([A-Za-z]:)", r"\1", text)
    text = re.sub(r"^/([A-Za-z])(/|$)", r"\1:/", text)
    return text.rstrip("/").lower()


def is_absolute(text: str) -> bool:
    return text.startswith("/") or bool(re.match(r"^[a-z]:", text))


def frozen_by_path(path: str) -> bool:
    parts = path.split("/")
    return parts[-1] == "prompt.md" or any(part in FROZEN_FOLDERS for part in parts[:-1])


def binary_suffix(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in BINARY_SUFFIXES


def is_text(data: bytes) -> bool:
    return b"\0" not in data[:8192]


class Repository:
    """A plans root and the Git repository that tracks it."""

    def __init__(self, plans_root: str) -> None:
        if not os.path.isdir(plans_root):
            raise Stop(f"{plans_root} is not a folder")
        self.plans_root = os.path.realpath(plans_root)
        result = subprocess.run(
            ["git", "-C", self.plans_root, "rev-parse", "--show-toplevel", "--absolute-git-dir"],
            capture_output=True,
            env=clean_env(),
        )
        if result.returncode:
            raise Stop(f"{plans_root} is not inside a Git repository")
        top, git_dir = to_text(result.stdout).splitlines()[:2]
        self.git_root = os.path.realpath(top)
        self.git_dir = os.path.realpath(git_dir)
        prefix = os.path.relpath(self.plans_root, self.git_root).replace(os.sep, "/")
        self.prefix = "" if prefix == "." else prefix
        self.root = posix_absolute(self.git_root)
        parent = os.path.dirname(self.plans_root)
        self.anchor = (to_bytes(os.path.basename(parent)), to_bytes(os.path.basename(self.plans_root)))
        self.anchor_base = normal_prefix(os.path.dirname(parent))
        self.fold = os.name == "nt"
        self.exclude: list[str] = []
        self.trees: dict[str, dict] = {}
        self.head_line: list[str] | None = None

    # Paths: `rel` is relative to the Git root with forward slashes, `absolute` is a posix form.

    def rel(self, absolute: str) -> str | None:
        a, r = (absolute.casefold(), self.root.casefold()) if self.fold else (absolute, self.root)
        if a == r:
            return ""
        if a.startswith(r + "/"):
            return absolute[len(self.root) + 1 :]
        return None

    def absolute(self, rel: str) -> str:
        return self.root + "/" + rel if rel else self.root

    def disk(self, rel: str) -> str:
        return os.path.join(self.git_root, *rel.split("/")) if rel else self.git_root

    def excluded(self, rel: str) -> bool:
        """Return whether a path relative to the Git root matches an --exclude glob."""
        key = rel.casefold() if self.fold else rel
        return any(fnmatch.fnmatchcase(key, glob.casefold() if self.fold else glob) for glob in self.exclude)

    def directly_in_plans(self, rel: str) -> bool:
        """Return whether a path is a file that sits in the plans root itself."""
        return self.in_plans(rel) and rel != self.prefix and "/" not in self.plans_rel(rel)

    def in_plans(self, rel: str) -> bool:
        return not self.prefix or rel == self.prefix or rel.startswith(self.prefix + "/")

    def join_plans(self, rel: str) -> str:
        return posixpath.join(self.prefix, rel) if self.prefix and rel else (rel or self.prefix)

    def plans_rel(self, rel: str) -> str:
        return rel[len(self.prefix) + 1 :] if self.prefix else rel

    def work(self, *parts: str) -> str:
        return os.path.join(self.git_dir, WORK_FOLDER, *parts)

    # Git

    def git(self, *args: str, data: bytes | None = None, index: str | None = None, check: bool = True) -> bytes:
        env = clean_env(index)
        if args[:1] == ("hook",):
            # Git runs commit hooks with this editor when the message comes from a file.
            env["GIT_EDITOR"] = ":"
        result = subprocess.run(["git", "-C", self.git_root, *args], input=data, capture_output=True, env=env)
        if args[:1] == ("update-ref",):
            self.head_line = None
        if check and result.returncode:
            message = to_text(result.stderr).strip() or to_text(result.stdout).strip()
            raise Stop(f"git {' '.join(args[:3])} failed: {message}")
        return result.stdout

    def head_fields(self) -> list[str]:
        """Return HEAD, its tree and its first parent, read once until the next commit."""
        if self.head_line is None:
            self.head_line = to_text(self.git("log", "-1", "--format=%H %T %P", "HEAD")).split()
        return self.head_line

    def head(self) -> str:
        return self.head_fields()[0]

    def tree_of(self, commit: str) -> str:
        if commit == self.head():
            return self.head_fields()[1]
        return to_text(self.git("rev-parse", f"{commit}^{{tree}}")).strip()

    def ls_tree(self, rev: str) -> dict[str, tuple[str, str, str, int]]:
        """Return path -> (mode, type, object, size) for every entry in rev."""
        if rev == "HEAD":
            rev = self.head()
        if rev in self.trees:
            return self.trees[rev]
        entries: dict[str, tuple[str, str, str, int]] = {}
        for record in self.git("ls-tree", "-r", "-z", "-l", "--full-tree", rev).split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, obj, size = meta.split()
            entries[to_text(path)] = (to_text(mode), to_text(kind), to_text(obj), int(size) if size != b"-" else 0)
        self.trees[rev] = entries
        return entries

    def read_blobs(self, objects: list[str]) -> dict[str, bytes]:
        """Read many blobs through one `git cat-file --batch` process."""
        unique = list(dict.fromkeys(objects))
        if not unique:
            return {}
        process = subprocess.Popen(
            ["git", "-C", self.git_root, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=clean_env(),
        )

        def feed() -> None:
            try:
                for obj in unique:
                    process.stdin.write(obj.encode() + b"\n")
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        blobs: dict[str, bytes] = {}
        for obj in unique:
            header = process.stdout.readline().split()
            if len(header) < 3:
                process.kill()
                raise Stop(f"git cat-file cannot read {obj}")
            size = int(header[2])
            blobs[obj] = process.stdout.read(size)
            process.stdout.read(1)
        writer.join()
        process.stdout.close()
        process.wait()
        return blobs

    def write_blobs(self, contents: list[bytes]) -> list[str]:
        """Write many blobs through one `git hash-object -w --stdin-paths` process."""
        if not contents:
            return []
        folder = self.work("blobs")
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        paths = []
        for number, data in enumerate(contents):
            path = os.path.join(folder, str(number))
            with open(path, "wb") as file:
                file.write(data)
            paths.append(path)
        listing = "\n".join(paths).encode() + b"\n"
        objects = to_text(self.git("hash-object", "-w", "--no-filters", "--stdin-paths", data=listing)).split()
        shutil.rmtree(folder, ignore_errors=True)
        if len(objects) != len(contents):
            raise Stop("git hash-object returned the wrong number of objects")
        return objects

    def private_index(self, commit: str) -> str:
        index = os.path.join(self.git_dir, PRIVATE_INDEX)
        if os.path.exists(index):
            os.remove(index)
        self.git("read-tree", commit, index=index)
        return index

    def update_index(self, index: str, records: list[str]) -> None:
        if records:
            self.git("update-index", "-z", "--index-info", data=b"".join(to_bytes(r) + b"\0" for r in records), index=index)

    def staged_paths(self) -> list[str]:
        """Return each path whose shared-index entry differs from HEAD. A staged rename lists its source and destination."""
        return [to_text(p) for p in self.git("diff", "--cached", "--no-renames", "--name-only", "-z").split(b"\0") if p]

    def diff_names(self, commit: str) -> list[str]:
        """Return each path that commit changed. A merge lists the paths it changed against any parent."""
        out = self.git("diff-tree", "-r", "-m", "-z", "--no-renames", "--name-only", "--no-commit-id", "--root", commit)
        return [to_text(name) for name in out.split(b"\0") if name]

    def grep_names(self, names: list[str]) -> set[str]:
        """Return each file, tracked, untracked or ignored, whose text holds one of names, from one git grep."""
        if not names:
            return set()
        with tempfile.TemporaryDirectory(prefix="plan-paths-") as folder:
            patterns = os.path.join(folder, "names.txt")
            with open(patterns, "wb") as file:
                file.write(b"".join(to_bytes(name) + b"\n" for name in sorted(set(names))))
            command = ["grep", "-z", "-l", "-I", "-F", "--untracked", "--no-exclude-standard", "-f", patterns]
            result = subprocess.run(
                ["git", "-C", self.git_root, *command, "--", ".", ":(exclude,glob)**/node_modules/**"],
                capture_output=True,
                env=clean_env(),
            )
        if result.returncode > 1:
            raise Stop(f"git grep failed: {to_text(result.stderr).strip()}")
        return {to_text(path) for path in result.stdout.split(b"\0") if path}

    def find_commit(self, parent: str, tree: str) -> str | None:
        """Return the commit after parent whose first parent is parent and whose tree is tree."""
        for line in to_text(self.git("log", "--format=%H %T %P", f"{parent}..HEAD")).splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == tree and fields[2] == parent:
                return fields[0]
        return None

    def index_entries(self) -> dict[str, tuple[str, str] | str]:
        """Return path -> (mode, object) for each shared-index entry, or 'conflict' for an unmerged path."""
        entries: dict[str, tuple[str, str] | str] = {}
        for record in self.git("ls-files", "-s", "-z").split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, obj, stage = meta.split()
            entries[to_text(path)] = (to_text(mode), to_text(obj)) if stage == b"0" else "conflict"
        return entries

    def status(self) -> list[tuple[str, str]]:
        fields = self.git("status", "--porcelain=v1", "-z", "-uall").split(b"\0")
        entries = []
        position = 0
        while position < len(fields):
            field = fields[position]
            position += 1
            if not field:
                continue
            code = to_text(field[:2])
            entries.append((code, to_text(field[3:])))
            if code[0] in "RC":
                position += 1
        return entries


class MoveMap:
    """Old and new paths, relative to the Git root, for every moved folder and file."""

    def __init__(self, moves: list[tuple[str, str, str]] | list[list[str]]) -> None:
        self.moves = [tuple(move) for move in moves]
        self.forward = {old.casefold(): (new, kind) for old, new, kind in self.moves}
        self.backward = {new.casefold(): (old, kind) for old, new, kind in self.moves}
        self.forward_depth = max((old.count("/") + 1 for old, _, _ in self.moves), default=0)
        self.backward_depth = max((new.count("/") + 1 for _, new, _ in self.moves), default=0)

    @staticmethod
    def _apply(path: str, table: dict, depth: int) -> str:
        if not table:
            return path
        parts = path.split("/")
        for count in range(min(depth, len(parts)), 0, -1):
            hit = table.get("/".join(parts[:count]).casefold())
            if hit is None:
                continue
            target, kind = hit
            if kind == "file" and count != len(parts):
                return path
            return "/".join([target, *parts[count:]])
        return path

    def map(self, path: str) -> str:
        return self._apply(path, self.forward, self.forward_depth)

    def unmap(self, path: str) -> str:
        return self._apply(path, self.backward, self.backward_depth)

    def lookup(self, old: str) -> tuple[str, str] | None:
        return self.forward.get(old.casefold())


class Footprint:
    """The migration paths: each moved item at its old and new path, and each file that the reference rewrite changes."""

    def __init__(self, repo: Repository, moves: MoveMap, files) -> None:
        self.key = (lambda path: path.casefold()) if repo.fold else (lambda path: path)
        self.items = {self.key(path) for old, new, _ in moves.moves for path in (old, new)}
        self.files = {self.key(form) for path in files for form in (path, moves.map(path), moves.unmap(path))}

    def touches(self, rel: str | None) -> bool:
        """Return whether a path relative to the Git root is a migration path or sits inside a moved item."""
        if rel is None:
            return False
        key = self.key(rel)
        if key in self.files:
            return True
        while key:
            if key in self.items:
                return True
            key = key.rpartition("/")[0]
        return False


class Oracle:
    """Answers whether a path existed before the moves, whether or not the moves have run."""

    def __init__(self, repo: Repository, moves: MoveMap, tracked: list[str]) -> None:
        self.repo = repo
        self.moves = moves
        self.files: set[str] = set()
        self.folders: set[str] = set()
        for path in tracked:
            key = path.casefold()
            self.files.add(key)
            while "/" in key:
                key = key.rsplit("/", 1)[0]
                if key in self.folders:
                    break
                self.folders.add(key)
        self.cache: dict[str, bool] = {}

    def exists(self, target: str) -> bool:
        if target not in self.cache:
            rel = self.repo.rel(target)
            if rel is None:
                found = os.path.exists(native(target))
            elif rel == "" or rel.casefold() in self.files or rel.casefold() in self.folders:
                found = True
            else:
                mapped = self.moves.map(rel)
                found = os.path.exists(self.repo.disk(rel)) or (mapped != rel and os.path.exists(self.repo.disk(mapped)))
            self.cache[target] = found
        return self.cache[target]


class Rewrite:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.edits: list[tuple[int, int, bytes, str]] = []
        self.unresolved: list[str] = []
        self.rules: list[str] = []

    @property
    def new_data(self) -> bytes:
        pieces = []
        position = 0
        for start, end, replacement, _ in sorted(self.edits):
            if start < position:
                continue
            pieces += [self.data[position:start], replacement]
            position = end
        pieces.append(self.data[position:])
        return b"".join(pieces)

    @property
    def forms(self) -> list[str]:
        return [form for _, _, _, form in self.edits]


class Rewriter:
    """Rewrites the references in one file, given the file's path before the moves."""

    def __init__(self, repo: Repository, moves: MoveMap, oracle: Oracle, anchored_only: bool = False) -> None:
        self.repo = repo
        self.moves = moves
        self.oracle = oracle
        self.anchored_only = anchored_only
        parent, root = repo.anchor
        self.anchored = re.compile(
            re.escape(parent) + SEPARATOR + re.escape(root) + rb"(?P<sep>\\\\|\\|/)(?P<rest>[" + PATH_BYTES + rb"/\\]+)",
            re.IGNORECASE if repo.fold else 0,
        )
        prefix_depth = repo.prefix.count("/") + 1 if repo.prefix else 0
        self.anchored_depth = max(moves.forward_depth - prefix_depth, 0)

    def rewrite(self, source: str, data: bytes) -> Rewrite:
        result = Rewrite(data)
        if not self.moves.moves and self.anchored_only:
            return result
        if posixpath.basename(source) == ".gitignore":
            if not self.anchored_only:
                self._gitignore(source, data, result)
            return result
        kept = Spans(kept_fences(data) if os.path.splitext(source)[1].lower() in MARKDOWN_SUFFIXES else [])
        for match in self.anchored.finditer(data):
            if kept.holds(match.start()):
                continue
            edit = self._anchored(data, match)
            if edit:
                result.edits.append(edit)
        if self.anchored_only:
            return result
        old_dir = self.repo.absolute(posixpath.dirname(source))
        new_dir = self.repo.absolute(posixpath.dirname(self.moves.map(source)))
        whole = []
        for match in DELIMITED.finditer(data):
            group = "angle" if match.group("angle") is not None else "quoted"
            token, start = match.group(group), match.start(group)
            dated = not token.startswith(b".")
            if b" " not in token or kept.holds(start) or not DELIMITED_START.match(token):
                continue
            if dated and b"/" not in token and b"\\" not in token and not token.endswith(b".md"):
                continue
            if self._relative(data, token, start, old_dir, new_dir, dated, result, whole=True):
                whole.append((start, start + len(token)))
        claimed = Spans(whole)
        for pattern, dated in ((RELATIVE, False), (DATED_TOKEN, True)):
            for match in pattern.finditer(data):
                if kept.holds(match.start()) or claimed.holds(match.start()):
                    continue
                self._relative(data, match.group(0), match.start(), old_dir, new_dir, dated, result)
        return result

    def _prefix_kind(self, prefix: bytes) -> str | None:
        if not prefix:
            return "bare"
        text = to_text(prefix)
        if text.startswith("."):
            return None
        norm = normal_prefix(text)
        if is_absolute(norm):
            return "absolute" if norm == self.repo.anchor_base else None
        return "partial" if self.repo.anchor_base.endswith("/" + norm) else None

    def _anchored(self, data: bytes, match: re.Match) -> tuple[int, int, bytes, str] | None:
        begin = match.start()
        while begin > 0 and data[begin - 1] in PREFIX_BYTES:
            begin -= 1
        kind = self._prefix_kind(data[begin : match.start()])
        if kind is None:
            return None
        sep = match.group("sep")
        segments = match.group("rest").split(sep)
        for count in range(min(self.anchored_depth, len(segments)), 0, -1):
            parts = segments[:count]
            if count == len(segments):
                parts = parts[:-1] + [parts[-1].rstrip(b".")]
            if not parts[-1]:
                continue
            hit = self.moves.lookup(self.repo.join_plans("/".join(to_text(part) for part in parts)))
            if hit is None or (hit[1] == "file" and count != len(segments)):
                continue
            replacement = sep.join(to_bytes(part) for part in self.repo.plans_rel(hit[0]).split("/"))
            start = match.start("rest")
            s = to_text(sep)
            parent, root = (to_text(name) for name in self.repo.anchor)
            sub = to_text(segments[0]) + s if count > 1 and segments[0] in (b"archive", b"retrospectives") else ""
            form = f"{'absolute ' if kind == 'absolute' else ''}{parent}{s}{root}{s}{sub}<slug>"
            return start, start + len(sep.join(parts)), replacement, form
        return None

    def _relative(
        self, data: bytes, token: bytes, start: int, old_dir: str, new_dir: str, dated: bool, result: Rewrite, whole: bool = False
    ) -> bool:
        """Rewrite one token when it resolves. Return whether it resolved. A whole delimited token that does not
        resolve is left to the ordinary scan and never reported."""
        while not whole and token.endswith(b".") and not token.endswith(b".."):
            token = token[:-1]
        if dated and b"/" not in token and b"\\" not in token:
            if not token.endswith(b".md") and data[max(0, start - 2) : start] != b"](":
                return False
        sep = b"\\\\" if b"\\\\" in token else b"\\" if b"\\" in token else b"/"
        posix = to_text(token.replace(sep, b"/"))
        segments = [segment for segment in posix.split("/") if segment]
        if not segments or all(segment in (".", "..") for segment in segments):
            return False
        target = posixpath.normpath(old_dir + "/" + posix)
        rel = self.repo.rel(target)
        if not self.oracle.exists(target):
            if not whole and not dated and ((rel is not None and self.repo.in_plans(rel)) or any(DATED.match(s) for s in segments)):
                result.unresolved.append(to_text(token))
            return False
        new_target = self.repo.absolute(self.moves.map(rel)) if rel is not None else target
        if new_target == target and new_dir == old_dir:
            return True
        if posixpath.normpath(new_dir + "/" + posix) == new_target:
            return True
        replacement = posixpath.relpath(new_target, new_dir)
        if posix.startswith(".") and not replacement.startswith("../"):
            replacement = "./" + replacement
        if posix.endswith("/"):
            replacement += "/"
        result.edits.append((start, start + len(token), to_bytes(replacement).replace(b"/", sep), relative_form(posix, dated)))
        return True

    def _gitignore(self, source: str, data: bytes, result: Rewrite) -> None:
        base_old = posixpath.dirname(source)
        base_new = posixpath.dirname(self.moves.map(source))
        offset = 0
        for line in data.splitlines(keepends=True):
            edit = self._rule(line.rstrip(b"\r\n"), base_old, base_new, result)
            if edit:
                result.edits.append((offset + edit[0], offset + edit[1], edit[2], ".gitignore anchored rule"))
            offset += len(line)

    def _rule(self, body: bytes, base_old: str, base_new: str, result: Rewrite) -> tuple[int, int, bytes] | None:
        if not body or body.startswith(b"#"):
            return None
        start = 1 if body.startswith(b"!") else 0
        lead = body[start : start + 1] == b"/"
        core = body[start + 1 :] if lead else body[start:]
        if core.startswith(b"**") or (not lead and b"/" not in core.rstrip(b"/")):
            return None
        literal: list[bytes] = []
        for segment in core.split(b"/"):
            if not segment or GLOB.search(segment):
                break
            literal.append(segment)
        if not literal:
            return None
        old = posixpath.normpath(posixpath.join(base_old, *(to_text(segment) for segment in literal)))
        new = self.moves.map(old)
        if new == old and base_new == base_old:
            return None
        replacement = posixpath.relpath(self.repo.absolute(new), self.repo.absolute(base_new))
        if replacement.startswith(".."):
            result.rules.append(to_text(body))
            return None
        literal_bytes = b"/".join(literal)
        if to_bytes(replacement) == literal_bytes:
            return None
        begin = start + (1 if lead else 0)
        return begin, begin + len(literal_bytes), to_bytes(replacement)


class Spans:
    """Sorted, disjoint byte ranges, each a (start, end) pair with end excluded."""

    def __init__(self, spans: list[tuple[int, int]]) -> None:
        self.spans = sorted(spans)
        self.starts = [start for start, _ in self.spans]

    def holds(self, position: int) -> bool:
        at = bisect.bisect_right(self.starts, position) - 1
        return at >= 0 and position < self.spans[at][1]


def kept_fences(data: bytes) -> list[tuple[int, int]]:
    """Return the byte range of each fenced code block whose info string does not name runnable code.

    A fence opens on a line of three or more backticks or tildes, after any indent, quote marker or list
    marker. It closes on a line of the same character, at least as long, with nothing after it. A fence
    opened in a list item closes at the item's indentation or deeper, and a line indented less than the
    item ends it. A fence that never closes runs to the end of the file.
    """
    spans = []
    offset = 0
    # Fence character, fence length, start offset, whether the block keeps its bytes, and the indentation
    # of the list item that holds the fence (None outside a list item).
    opened: tuple[bytes, int, int, bool, int | None] | None = None
    for line in data.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        match = FENCE.match(body)
        if opened is not None and opened[4] is not None and body.strip() and indent_width(body) < opened[4]:
            if opened[3]:
                spans.append((opened[2], offset))
            opened = None
        if opened is None:
            if match and not (match.group("fence").startswith(b"`") and b"`" in match.group("info")):
                words = match.group("info").split()
                language = to_text(words[0]).lower() if words else ""
                item = indent_width(body) if match.group("items") else None
                opened = (match.group("fence")[:1], len(match.group("fence")), offset, language not in RUNNABLE_FENCES, item)
        elif (
            match
            and not match.group("items")
            and match.group("fence")[:1] == opened[0]
            and len(match.group("fence")) >= opened[1]
            and not match.group("info").strip()
        ):
            if opened[3]:
                spans.append((opened[2], offset + len(line)))
            opened = None
        offset += len(line)
    if opened is not None and opened[3]:
        spans.append((opened[2], len(data)))
    return spans


def indent_width(line: bytes) -> int:
    """Return the column width of the indent and quote marks that start a line, with a tab as four columns."""
    return len(INDENT.match(line).group(0).expandtabs(4))


def relative_form(posix: str, dated: bool) -> str:
    if dated:
        return "<slug>/ from a file beside it"
    if posix.startswith("./"):
        return "./"
    ups = 0
    rest = posix
    while rest.startswith("../"):
        ups += 1
        rest = rest[3:]
    head = "../" * ups if ups <= 2 else "../../../+"
    return head + ("archive/" if rest.startswith("archive/") else "") + "<slug>"


class Survey:
    """The tracked text files of one commit, read once, with frozen evidence marked."""

    def __init__(self, repo: Repository, tree: dict[str, tuple[str, str, str, int]]) -> None:
        self.repo = repo
        self.tree = tree
        candidates = {
            path: entry[2]
            for path, entry in tree.items()
            if entry[1] == "blob" and entry[0] != "120000" and entry[3] <= MAX_TEXT_BYTES and not binary_suffix(path)
        }
        blobs = repo.read_blobs(list(candidates.values()))
        self.texts = {path: blobs[obj] for path, obj in candidates.items() if is_text(blobs[obj])}
        self.digest_of = {path: sha256(data) for path, data in self.texts.items()}
        self.holders: dict[str, set[str]] = {}
        for path, data in self.texts.items():
            for token in set(DIGEST.findall(data)):
                holders = self.holders.setdefault(to_text(token).lower(), set())
                if len(holders) < 2:
                    holders.add(path)
        self.frozen = {path for path in self.texts if self.is_frozen(path, self.digest_of[path])}

    def frozen_by_digest(self, digest: str, path: str) -> bool:
        """Return whether another tracked file records the digest. A living plan.md or a plans-root file stays free."""
        if posixpath.basename(path) == "plan.md" or self.repo.directly_in_plans(path):
            return False
        return bool(self.holders.get(digest, set()) - {path})

    def is_frozen(self, path: str, digest: str) -> bool:
        return frozen_by_path(path) or self.frozen_by_digest(digest, path)


class Scan:
    """The rewrite of every tracked text file in a survey."""

    def __init__(self, survey: Survey, rewriter: Rewriter, moves: MoveMap) -> None:
        self.changed: dict[str, Rewrite] = {}
        self.frozen_hits: list[str] = []
        self.excluded_hits: list[str] = []
        self.unresolved: list[tuple[str, str]] = []
        self.rules: list[tuple[str, str]] = []
        for path, data in survey.texts.items():
            result = rewriter.rewrite(moves.unmap(path), data)
            if survey.repo.excluded(path) or survey.repo.excluded(moves.unmap(path)):
                if result.edits:
                    self.excluded_hits.append(path)
                continue
            if path in survey.frozen:
                if result.edits:
                    self.frozen_hits.append(path)
                continue
            self.unresolved += [(path, token) for token in result.unresolved]
            self.rules += [(path, rule) for rule in result.rules]
            if result.edits:
                self.changed[path] = result


def walk_files(repo: Repository, start: str, enter) -> list[tuple[str, int]]:
    """List regular files under start, relative to the Git root. Never follows a link or enters a nested repository."""
    found = []
    pending = [start]
    while pending:
        rel = pending.pop()
        try:
            entries = list(os.scandir(repo.disk(rel)))
        except OSError:
            continue
        for entry in entries:
            child = f"{rel}/{entry.name}" if rel else entry.name
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIP_FOLDERS or os.path.lexists(os.path.join(entry.path, ".git")):
                        continue
                    if enter(child, entry.name):
                        pending.append(child)
                elif entry.is_file(follow_symlinks=False):
                    found.append((child, entry.stat(follow_symlinks=False).st_size))
            except OSError:
                continue
    return found


def item_names(moves: MoveMap) -> list[str]:
    """Return the folder name of each moved item, which every reference to the item contains."""
    names = []
    for old, _, kind in moves.moves:
        name = posixpath.basename(old)
        names.append(name[:-3] if kind == "file" and name.lower().endswith(".md") else name)
    return names


def modified_paths(status: list, place) -> list[str]:
    """Return the tracked files that another session modified, from git status entries."""
    return [place(path) for code, path in status if code != "??" and "D" not in code]


def disk_rewrites(
    repo: Repository,
    survey: Survey,
    rewriter: Rewriter,
    moves: MoveMap,
    candidates: set[str],
    hits: set[str],
    excluded: list[str] | None = None,
) -> list[tuple[str, bytes, bytes, bool]]:
    """Return (path, bytes, new bytes, tracked) for each working-tree file that the rewrite changes.

    A file that matches an --exclude glob is never returned. When `excluded` is a list, it receives the
    path of each such file that the rewrite would have changed.

    Opening a file is the slow step, so this opens only the files that can change. Those are the tracked
    candidates (files the reference commit rewrites, and files another session modified), untracked and
    ignored files that name a moved item, and untracked and ignored files inside a moved folder.
    """
    tracked = {path.casefold() for path in survey.tree}
    paths = dict.fromkeys(candidates)
    for path in sorted(hits):
        if path.casefold() not in tracked:
            paths.setdefault(path)
    for old, new, kind in moves.moves:
        if kind != "dir":
            continue
        current = new if os.path.lexists(repo.disk(new)) else old
        for path, _ in walk_files(repo, current, lambda rel, name: name not in FROZEN_FOLDERS):
            if path.casefold() not in tracked:
                paths.setdefault(path)
    changes = []
    for path in paths:
        if binary_suffix(path) or frozen_by_path(path) or path in survey.frozen:
            continue
        try:
            if os.path.getsize(repo.disk(path)) > MAX_TEXT_BYTES:
                continue
            with open(repo.disk(path), "rb") as file:
                data = file.read()
        except OSError:
            continue
        if not is_text(data) or survey.frozen_by_digest(sha256(data), path):
            continue
        result = rewriter.rewrite(moves.unmap(path), data)
        if repo.excluded(path) or repo.excluded(moves.unmap(path)):
            if result.edits and excluded is not None:
                excluded.append(path)
            continue
        if result.edits:
            changes.append((path, data, result.new_data, path.casefold() in tracked))
    return changes


def write_file(path: str, data: bytes) -> None:
    """Replace path through a new, uniquely named file beside it. On failure, remove only that new file."""
    folder, name = os.path.split(path)
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".plan-paths.tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        with contextlib.suppress(OSError):
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise


def replace_file(repo: Repository, rel: str, data: bytes) -> None:
    """Write data to a working-tree file. Raise Stop, with the file unchanged, when it cannot be replaced."""
    try:
        write_file(repo.disk(rel), data)
    except OSError as error:
        raise Stop(f"{rel} cannot be replaced ({error.strerror or error}). Free the file, then rerun the same command to resume") from None


def remove_empty_parents(folder: str, stop: str) -> None:
    while os.path.normcase(folder) != os.path.normcase(stop) and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)


# Inventory


def tree_children(repo: Repository, tree, base: str) -> dict[str, str]:
    """Return name -> 'dir' or 'file' for the tracked entries directly in base."""
    children: dict[str, str] = {}
    lead = base + "/" if base else ""
    for path in tree:
        if lead and not path.startswith(lead):
            continue
        rest = path[len(lead) :]
        name, _, more = rest.partition("/")
        if more:
            children[name] = "dir"
        else:
            children.setdefault(name, "file")
    return children


def inventory(repo: Repository, tree, scope: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return the moves for every dated item at the old depth in the scope, and the problems found."""
    moves, problems = [], []
    occupied = {path.casefold() for path in tree}
    for path in list(occupied):
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            occupied.add(path)
    for root in SCOPES[scope]:
        base = repo.join_plans(root)
        children = tree_children(repo, tree, base)
        folder = repo.disk(base)
        if os.path.isdir(folder):
            for entry in os.scandir(folder):
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    if DATED.match(entry.name):
                        problems.append(f"{posixpath.join(base, entry.name)} is a link and is not moved")
                    continue
                kind = "dir" if entry.is_dir() else "file"
                if children.get(entry.name, kind) != kind:
                    problems.append(f"{posixpath.join(base, entry.name)} is a {kind} on disk and a {children[entry.name]} in HEAD")
                children[entry.name] = kind
        for name, kind in sorted(children.items()):
            if not DATED.match(name):
                continue
            try:
                if kind == "file":
                    if not name.lower().endswith(".md"):
                        problems.append(f"{posixpath.join(base, name)} is a dated file that is not Markdown")
                        continue
                    target = new_path(name[:-3]) + "/plan.md"
                else:
                    target = new_path(name)
            except ValueError as error:
                problems.append(str(error))
                continue
            old, new = posixpath.join(base, name), posixpath.join(base, target)
            if os.path.lexists(repo.disk(new)) or new.casefold() in occupied:
                problems.append(f"{new} already exists, so {old} cannot move there")
                continue
            moves.append((old, new, kind))
    return moves, problems


def clean_relative(text: str) -> str:
    text = text.replace("\\", "/").strip().strip("/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        raise Stop(f"{text} must be a path inside the plans root")
    return text


def single_move(repo: Repository, tree, source: str, target: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    old, new = repo.join_plans(clean_relative(source)), repo.join_plans(clean_relative(target))
    if os.path.isdir(repo.disk(old)):
        kind = "dir"
    elif os.path.isfile(repo.disk(old)) or old in tree:
        kind = "file"
    elif any(path.startswith(old + "/") for path in tree):
        kind = "dir"
    else:
        return [], [f"{old} does not exist"]
    if os.path.lexists(repo.disk(new)) or new in tree or any(path.startswith(new + "/") for path in tree):
        return [], [f"{new} already exists"]
    return [(old, new, kind)], []


def count_items(repo: Repository) -> dict[str, int]:
    """Count dated items at the old depth and in year and month folders, for each root."""
    counts = {}
    for root in ("", "archive", "retrospectives"):
        folder = repo.disk(repo.join_plans(root))
        flat = nested = 0
        if os.path.isdir(folder):
            for entry in os.scandir(folder):
                if DATED.match(entry.name):
                    flat += 1
                elif YEAR.match(entry.name) and entry.is_dir():
                    for month in os.scandir(entry.path):
                        if MONTH.match(month.name) and month.is_dir():
                            nested += sum(1 for item in os.scandir(month.path) if DATED.match(item.name))
        counts[root or "active"] = flat + nested
        counts[(root or "active") + " at the old depth"] = flat
    return counts


def plan_folders(repo: Repository, problems: list[str]) -> list[str]:
    """Return every dated plan folder, at either depth, in every root. Links are never entered."""
    folders = []
    for root in ("", "archive", "retrospectives"):
        base = repo.disk(repo.join_plans(root))
        for path, info in child_entries(base, problems, lambda name: DATED.match(name) or YEAR.match(name)):
            if not stat.S_ISDIR(info.st_mode):
                continue
            if DATED.match(os.path.basename(path)):
                folders.append(path)
                continue
            for month, month_info in child_entries(path, problems, MONTH.match):
                if stat.S_ISDIR(month_info.st_mode):
                    folders += [item for item, item_info in child_entries(month, problems, DATED.match) if stat.S_ISDIR(item_info.st_mode)]
    return folders


# Preflight


def list_processes() -> list[dict]:
    """Return pid, ppid, name and command for each process. Tests replace it."""
    if os.name == "nt":
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True)
        try:
            rows = json.loads(result.stdout.decode("utf-8", "replace") or "[]")
        except ValueError:
            return []
        rows = rows if isinstance(rows, list) else [rows]
        return [
            {"pid": r.get("ProcessId"), "ppid": r.get("ParentProcessId"), "name": r.get("Name") or "", "command": r.get("CommandLine") or ""}
            for r in rows
        ]
    result = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,args="], capture_output=True, text=True)
    processes = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 3 and fields[0].isdigit() and fields[1].isdigit():
            name = os.path.basename(fields[2].split()[0])
            processes.append({"pid": int(fields[0]), "ppid": int(fields[1]), "name": name, "command": fields[2]})
    return processes


def is_agent(process: dict) -> bool:
    command = (process.get("command") or "").lower().replace("\\", "/")
    return (process.get("name") or "").lower() in AGENT_NAMES or any(package in command for package in AGENT_PACKAGES)


def other_agents(processes: list[dict], me: int) -> list[dict]:
    """Return the top agent process of every session except the one this script runs under."""
    by_pid = {p["pid"]: p for p in processes}
    ancestors, pid = [], me
    while pid in by_pid and pid not in ancestors:
        ancestors.append(pid)
        pid = by_pid[pid]["ppid"]
    excluded = set(ancestors)
    root = next((pid for pid in ancestors if is_agent(by_pid[pid])), None)
    if root is not None:
        children: dict[int, list[int]] = {}
        for p in processes:
            children.setdefault(p["ppid"], []).append(p["pid"])
        pending = [root]
        while pending:
            pid = pending.pop()
            for child in children.get(pid, []):
                if child not in excluded:
                    excluded.add(child)
                    pending.append(child)
    agents = [p for p in processes if is_agent(p) and p["pid"] not in excluded]
    agent_pids = {p["pid"] for p in agents}
    return [p for p in agents if p["ppid"] not in agent_pids]


def run_folders(repo: Repository) -> tuple[list[tuple[str | None, str, os.stat_result]], list[str]]:
    """Return (plan folder, run entry, lstat) for each legacy and plan runs entry, and the refusals for unreadable paths.

    Links are skipped at every boundary. Plan folders come from plan_folders, which checks each boundary.
    """
    problems: list[str] = []
    runs = [(None, path, info) for path, info in child_entries(os.path.join(repo.plans_root, "runs"), problems)]
    for plan in plan_folders(repo, problems):
        runs += [(plan, path, info) for path, info in child_entries(os.path.join(plan, "runs"), problems)]
    return runs, problems


def open_task_runs(repo: Repository, runs: list[tuple[str | None, str, os.stat_result]]) -> list[tuple[str, str]]:
    """Return (run folder, label) for each task run whose run.json has no landed commit and whose plan has no stop
    line for the run's date.

    A run.json that is a link or reparse point is never read.
    """
    found = []
    stops: dict[str | None, set[str]] = {}
    for plan_folder, run, info in runs:
        record = os.path.join(run, "run.json")
        if not stat.S_ISDIR(info.st_mode) or not plain_file(record):
            continue
        label = os.path.relpath(run, repo.plans_root).replace(os.sep, "/")
        try:
            with open(record, "rb") as file:
                data = json.loads(file.read().decode("utf-8-sig"))
        except (OSError, ValueError):
            found.append((run, f"{label} (run.json cannot be read)"))
            continue
        if not isinstance(data, dict) or data.get("source", "ship") != "ship" or data.get("landed"):
            continue
        if plan_folder not in stops:
            stops[plan_folder] = set()
            plan_file = os.path.join(plan_folder, "plan.md") if plan_folder else None
            if plan_file and plain_file(plan_file):
                with open(plan_file, "rb") as file:
                    stops[plan_folder] = {to_text(start) for start in STOP_LINE.findall(file.read())}
        if run_start(os.path.basename(run), data) not in stops[plan_folder]:
            found.append((run, label))
    return found


def run_start(folder: str, data: dict) -> str | None:
    """Return a task run's start as `YYYY-MM-DDTHH:MM:SSZ`, from run.json or its folder name, or None."""
    named = RUN_FOLDER.match(folder)
    date = data.get("date") if isinstance(data.get("date"), str) else named.group(1) if named else None
    run_id = RUN_ID.match(data["run_id"]) if isinstance(data.get("run_id"), str) else None
    clock = run_id.group(1) if run_id else named.group(2) if named else None
    if not date or not re.match(r"^\d{4}-\d{2}-\d{2}$", date[:10]) or not clock:
        return None
    return f"{date[:10]}T{clock[:2]}:{clock[2:4]}:{clock[4:]}Z"


def is_reparse(info: os.stat_result) -> bool:
    """Return whether a stat result is a symlink or a Windows reparse point, such as a junction."""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def link_info(path: str) -> os.stat_result | None:
    """Return the no-follow metadata of path, or None when it is missing. Other errors propagate."""
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def unreadable(path: str, error: OSError) -> str:
    return f"{path} cannot be read ({error.strerror or error}), so open or recent runs cannot be ruled out"


def plain_file(path: str) -> bool:
    """Return whether path is a regular file, not a link or reparse point. Unreadable metadata raises Stop."""
    try:
        info = link_info(path)
    except OSError as error:
        raise Stop(unreadable(path, error)) from None
    return info is not None and stat.S_ISREG(info.st_mode) and not is_reparse(info)


def child_entries(folder: str, problems: list[str], keep=lambda name: True) -> list[tuple[str, os.stat_result]]:
    """Return the path and lstat of each entry of a real folder whose name passes keep. Links are skipped.

    The folder is checked with lstat before it is listed, and each entry with lstat after. Unreadable paths
    add a refusal to problems. A missing folder has no entries.
    """
    try:
        info = link_info(folder)
        if info is None or not stat.S_ISDIR(info.st_mode) or is_reparse(info):
            return []
        names = os.listdir(folder)
    except OSError as error:
        problems.append(unreadable(folder, error))
        return []
    found = []
    for name in names:
        path = os.path.join(folder, name)
        if not keep(name):
            continue
        try:
            info = link_info(path)
        except OSError as error:
            problems.append(unreadable(path, error))
            continue
        if info is not None and not is_reparse(info):
            found.append((path, info))
    return found


def preflight(
    repo: Repository,
    session: str | None,
    rewriter: Rewriter,
    allowed: dict[str, list],
    footprint: Footprint,
    hits: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return the lines to show the owner and the reasons the run must not start.

    `footprint` holds the migration paths. A staged entry, a commit from another session in the last 15
    minutes, and an open task run refuse the run only at a migration path. Elsewhere each one is a line.
    A commit counts at a migration path when it changed one. A run counts when its folder sits inside a
    moved item.

    `allowed` maps each path that an unfinished run's commits changed to the shared-index entries the run
    itself leaves there. Any other staged entry at a migration path, inside a moved folder too, refuses the run.
    An unrelated agent process is a line. An agent naming an absolute migration path refuses.
    A folder above the runs whose metadata cannot be read refuses wherever it sits.
    """
    lines, refusals = [], []
    outside = "It sits outside the migration paths"
    staged = unexpected_staged(repo, allowed)
    ours = [path for path in staged if footprint.touches(path)]
    others = [path for path in staged if not footprint.touches(path)]
    if ours:
        refusals.append(f"{len(ours)} staged entries in the shared index at migration paths: " + ", ".join(ours[:LIST_LIMIT]))
    if others:
        lines.append(
            f"{len(others)} staged entries in the shared index outside the migration paths stay staged: " + ", ".join(others[:LIST_LIMIT])
        )
    now = time.time()
    since = int(now) - RECENT_SECONDS
    stamp = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    log = to_text(repo.git("log", f"--since={stamp}", "--format=%H%x1f%ct%x1f%(trailers:key=Agent-Session,valueonly,separator=%x1d)%x1e"))
    for record in log.split("\x1e"):
        fields = record.strip().split("\x1f")
        if len(fields) < 3 or int(fields[1]) < since:
            continue
        sessions = {value.strip() for value in fields[2].split("\x1d") if value.strip()}
        if not session or session not in sessions:
            reason = f"commit {fields[0][:12]} landed in the last 15 minutes from another session"
            changed = next((path for path in repo.diff_names(fields[0]) if footprint.touches(path)), None)
            if changed:
                refusals.append(f"{reason} and changed {changed}")
            else:
                lines.append(f"{reason}. It changed no migration path")
    for process in other_agents(list_processes(), os.getpid()):
        command = (process.get("command") or "").replace("\\", "/")
        named_path = next((
            path for path in sorted(footprint.items | footprint.files)
            if re.search(
                r"(?<![\w./:-])" + re.escape(repo.disk(path).replace("\\", "/")) + r'''(?=$|[/\s"'])''',
                command, re.IGNORECASE if repo.fold else 0,
            )
        ), None)
        reason = f"agent process {process['pid']} is running: {process['name']} {(process['command'] or '')[:160]}"
        if named_path:
            refusals.append(f"{reason}. It names migration path {named_path}")
        else:
            lines.append(reason)
    runs, problems = run_folders(repo)
    refusals += problems
    for run, label in open_task_runs(repo, runs):
        reason = f"task run {label} is open: its run.json has no landed commit and its plan has no stop line for its run start"
        if footprint.touches(repo.rel(posix_absolute(run))):
            refusals.append(reason)
        else:
            lines.append(f"{reason}. {outside}")
    worktrees = [
        to_text(line[len(b"worktree ") :])
        for line in repo.git("worktree", "list", "--porcelain").splitlines()
        if line.startswith(b"worktree ")
    ][1:]
    lines += [f"worktree: {path}" for path in worktrees]
    tracked = {path.casefold() for path in repo.ls_tree("HEAD")} if rewriter.moves.moves else set()
    if rewriter.moves.moves:
        if hits is None:
            hits = repo.grep_names(item_names(rewriter.moves))
        for path in sorted(hits):
            if "runs" not in path.split("/")[:-1] or path.casefold() in tracked or binary_suffix(path):
                continue
            try:
                if os.path.getsize(repo.disk(path)) > MAX_RUN_FILE_BYTES:
                    continue
                with open(repo.disk(path), "rb") as file:
                    data = file.read()
            except OSError:
                continue
            if is_text(data) and rewriter.rewrite(path, data).edits:
                lines.append(f"untracked run file keeps a flat plan path: {path}")
    return lines, refusals


# Journal


class Journal:
    def __init__(self, repo: Repository) -> None:
        self.path = os.path.join(repo.git_dir, JOURNAL)

    def load(self) -> dict | None:
        if not os.path.isfile(self.path):
            return None
        with open(self.path, encoding="utf-8") as file:
            return json.load(file)

    def save(self, state: dict) -> None:
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=1)
        os.replace(temporary, self.path)


def migration_commits(repo: Repository, state: dict) -> list[str]:
    """Return the move and reference commits that landed, including one that landed before the journal recorded it."""
    commits = []
    for op in state["ops"]:
        if op["op"] in ("commit-a", "commit-b"):
            commit = op.get("commit") or (op.get("tree") and repo.find_commit(op["parent"], op["tree"]))
            if commit:
                commits.append(commit)
    return commits


def changed_paths(repo: Repository, state: dict) -> list[str]:
    """Return every path that the migration commits changed. Only these paths differ from HEAD because of the run."""
    return list(dict.fromkeys(path for commit in migration_commits(repo, state) for path in repo.diff_names(commit)))


def journaled_rewrites(state: dict) -> list[str]:
    """Return each working-tree file that the journal records for the disk rewrite."""
    return [entry["path"] for op in state["ops"] if op["op"] == "rewrite-disk" for entry in op.get("files", [])]


def journal_footprint(repo: Repository, state: dict, moves: MoveMap) -> Footprint:
    """Return the migration paths that the migration commits and the journal record."""
    return Footprint(repo, moves, [*changed_paths(repo, state), *journaled_rewrites(state)])


def expected_index(repo: Repository, state: dict) -> dict[str, list]:
    """Map each path the migration commits changed to the shared-index entries that the run itself can leave there.

    The run never stages anything until a reset, so before it the shared index holds the baseline entry. After
    a reset it holds the entry of a migration commit. An absent entry is None.
    """
    commits = migration_commits(repo, state)
    trees = [repo.ls_tree(state["baseline"])] + [repo.ls_tree(commit) for commit in commits]
    allowed: dict[str, list] = {}
    for path in changed_paths(repo, state):
        allowed[path] = [(tree[path][0], tree[path][2]) if path in tree else None for tree in trees]
    return allowed


def unexpected_staged(repo: Repository, allowed: dict[str, list], paths: list[str] | None = None) -> list[str]:
    """Return each staged path, or each of paths that differs from HEAD, whose index entry the run did not cause."""
    if paths is None:
        paths = repo.staged_paths()
    if not paths:
        return []
    index = repo.index_entries()
    head = repo.ls_tree("HEAD")
    found = []
    for path in paths:
        entry = index.get(path)
        current = (head[path][0], head[path][2]) if path in head else None
        if entry != current and entry not in allowed.get(path, []):
            found.append(path)
    return found


def message(state: dict, subject: str, body: str, agent: str, session: str) -> bytes:
    return f"{subject}\n\n{body}\n\nAgent: {agent}\nAgent-Session: {session}\n".encode()


def subjects(state: dict) -> tuple[str, str, str]:
    if state["command"] == "move":
        source, target = state["key"]["from"], state["key"]["to"]
        return (
            f"refactor(plans): move {source} to {target}",
            f"docs(plans): repoint paths at {target}",
            f"revert(plans): move {target} back to {source}",
        )
    return (
        "refactor(plans): nest plan folders by year and month",
        "docs(plans): repoint plan paths at the nested folders",
        "revert(plans): return plan folders to the flat layout",
    )


def commit_op(repo: Repository, journal: Journal, state: dict, op: dict, build, subject: str, body: str) -> None:
    head = repo.head()
    if op.get("tree"):
        found = repo.find_commit(op["parent"], op["tree"])
        if found:
            op["commit"] = found
            return
        if head != op["parent"]:
            raise Stop(f"HEAD moved to {head[:12]} after the {op['op']} commit was prepared on {op['parent'][:12]}")
    op["parent"] = head
    index = repo.private_index(head)
    build(index, op)
    tree = to_text(repo.git("write-tree", index=index)).strip()
    if tree == repo.tree_of(head):
        op["commit"] = None
        return
    op["tree"] = tree
    journal.save(state)
    text = message(state, subject, body, state["agent"], state["session"])
    advice = "Rerun the same command to resume. When it reports that HEAD moved, run --rollback, then --apply again"
    op["commit"] = land(repo, index, head, tree, text, op["op"], advice)


def land(repo: Repository, index: str, parent: str, tree: str, text: bytes, label: str, advice: str) -> str:
    """Commit tree on parent, then move HEAD to the commit only while HEAD still names parent. Return the commit.

    The pre-commit, prepare-commit-msg and commit-msg hooks run first with the private index, as they do for
    `git commit`, and post-commit runs after HEAD moves. A hook that changes the private index stops the run. The
    commit is signed when `commit.gpgSign` is true. When another session moved HEAD after parent was read, the
    compare-and-swap in `git update-ref` fails, no ref moves, and the run stops with advice.
    """
    os.makedirs(repo.work(), exist_ok=True)
    message_file = repo.work("message.txt")
    with open(message_file, "wb") as file:
        file.write(text)
    hook_path = message_file.replace("\\", "/")
    repo.git("hook", "run", "--ignore-missing", "pre-commit", index=index)
    repo.git("hook", "run", "--ignore-missing", "prepare-commit-msg", "--", hook_path, "message", index=index)
    repo.git("hook", "run", "--ignore-missing", "commit-msg", "--", hook_path, index=index)
    if to_text(repo.git("write-tree", index=index)).strip() != tree:
        raise Stop(f"a commit hook changed the index of the prepared {label} commit, and no ref moved. {advice}")
    sign = to_text(repo.git("config", "--type=bool", "--get", "commit.gpgSign", check=False)).strip() == "true"
    commit = to_text(repo.git("commit-tree", tree, "-p", parent, *(["-S"] if sign else []), "-F", message_file)).strip()
    with open(message_file, "rb") as file:
        subject = to_text(file.read()).split("\n", 1)[0]
    try:
        repo.git("update-ref", "-m", f"commit: {subject}", "HEAD", commit, parent)
    except Stop as error:
        raise Stop(f"the {label} commit did not land on {parent[:12]}, and no ref moved ({error}). {advice}") from None
    repo.git("hook", "run", "--ignore-missing", "post-commit", index=index, check=False)
    return commit


def run_ops(repo: Repository, journal: Journal, state: dict) -> None:
    repo.exclude = state.get("exclude", [])
    moves = MoveMap(state["moves"])
    subject_a, subject_b, _ = subjects(state)
    body = f"plan_paths.py {state['command']} moved {len(state['moves'])} items."

    def build_a(index: str, op: dict) -> None:
        tree = repo.ls_tree("HEAD")
        zero = "0" * len(next(iter(tree.values()))[2]) if tree else "0" * 40
        records = []
        for path, (mode, _, obj, _) in tree.items():
            new = moves.map(path)
            if new != path:
                records += [f"0 {zero}\t{path}", f"{mode} {obj}\t{new}"]
        op["renamed"] = len(records) // 2
        repo.update_index(index, records)

    def build_b(index: str, op: dict) -> None:
        tree = repo.ls_tree("HEAD")
        survey = Survey(repo, tree)
        rewriter = Rewriter(repo, moves, Oracle(repo, moves, [moves.unmap(path) for path in tree]))
        scan = Scan(survey, rewriter, moves)
        paths = list(scan.changed)
        objects = repo.write_blobs([scan.changed[path].new_data for path in paths])
        repo.update_index(index, [f"{tree[path][0]} {obj}\t{path}" for path, obj in zip(paths, objects)])
        op["rewritten"] = len(paths)

    for number, op in enumerate(state["ops"]):
        if op["state"] == "done":
            continue
        kind = op["op"]
        if kind == "commit-a":
            commit_op(repo, journal, state, op, build_a, subject_a, body)
        elif kind == "commit-b":
            commit_op(repo, journal, state, op, build_b, subject_b, body)
        elif kind == "move":
            move_op(repo, op)
        elif kind == "rewrite-disk":
            rewrite_disk_op(repo, journal, state, op, moves)
        elif kind == "reconcile":
            reconcile_run(repo, state)
        op["state"] = "done"
        journal.save(state)
        checkpoint(number, op)
    state["complete"] = True
    journal.save(state)


def move_op(repo: Repository, op: dict) -> None:
    source, target = repo.disk(op["from"]), repo.disk(op["to"])
    has_source, has_target = os.path.lexists(source), os.path.lexists(target)
    if has_target and not has_source:
        remove_empty_parents(os.path.dirname(source), repo.plans_root)
        return
    if has_source and has_target:
        raise Stop(f"both {op['from']} and {op['to']} exist. Another writer recreated the old path")
    if not has_source:
        op["absent"] = True
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.rename(source, target)
    remove_empty_parents(os.path.dirname(source), repo.plans_root)


def rewrite_disk_op(repo: Repository, journal: Journal, state: dict, op: dict, moves: MoveMap) -> None:
    folder = repo.work("disk")
    if "files" not in op:
        tree = repo.ls_tree("HEAD")
        survey = Survey(repo, tree)
        rewriter = Rewriter(repo, moves, Oracle(repo, moves, [moves.unmap(path) for path in tree]))
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        files = []
        commit = next(o.get("commit") for o in state["ops"] if o["op"] == "commit-b")
        candidates = set(repo.diff_names(commit)) if commit else set()
        candidates |= set(modified_paths(state["status"], moves.map))
        hits = repo.grep_names(item_names(moves))
        for slot, (path, before, after, tracked) in enumerate(disk_rewrites(repo, survey, rewriter, moves, candidates, hits)):
            for suffix, data in (("before", before), ("after", after)):
                with open(os.path.join(folder, f"{slot}.{suffix}"), "wb") as file:
                    file.write(data)
            files.append({"path": path, "slot": slot, "before": sha256(before), "after": sha256(after), "tracked": tracked})
        op["files"] = files
        journal.save(state)
    conflicts = []
    for entry in op["files"]:
        path = repo.disk(entry["path"])
        try:
            with open(path, "rb") as file:
                current = sha256(file.read())
        except OSError:
            current = None
        if current == entry["after"]:
            continue
        if current == entry["before"]:
            with open(os.path.join(folder, f"{entry['slot']}.after"), "rb") as file:
                replace_file(repo, entry["path"], file.read())
        else:
            conflicts.append(entry["path"])
    op["conflicts"] = conflicts


def reconcile(repo: Repository, paths: list[str]) -> None:
    """Make the shared index match HEAD at these paths and leave every other entry alone."""
    if not paths:
        return
    os.makedirs(repo.work(), exist_ok=True)
    listing = repo.work("pathspec")
    with open(listing, "wb") as file:
        file.write(b"".join(to_bytes(path) + b"\0" for path in paths))
    repo.git("--literal-pathspecs", "reset", "-q", f"--pathspec-from-file={listing}", "--pathspec-file-nul")


def reconcile_run(repo: Repository, state: dict) -> None:
    """Reset the shared index at the paths the migration commits changed. Stop on staged work the run did not cause."""
    paths = changed_paths(repo, state)
    stray = unexpected_staged(repo, expected_index(repo, state), paths)
    if stray:
        raise Stop(
            f"another writer staged {len(stray)} entries that the reset would unstage, such as {stray[0]}. "
            "Commit or unstage them, then rerun the same command to resume"
        )
    reconcile(repo, paths)


def rollback_refusals(repo: Repository, state: dict, moves: MoveMap, commits: list[str], done: set[str]) -> list[str]:
    """Return the reasons a rollback must not start or continue, checked before it changes anything."""
    refusals = []
    progress = state.get("rollback")
    moved = bool(progress) and "revert" not in done and repo.head() != progress["head"]
    if moved:
        prepared = progress.get("revert_tree") and repo.find_commit(progress["revert_parent"], progress["revert_tree"])
        if not prepared:
            refusals.append(f"HEAD moved to {repo.head()[:12]} after the rollback started on {progress['head'][:12]}")
    if commits and "revert" not in done and not moved:
        footprint = journal_footprint(repo, state, moves)
        later = []
        for commit in to_text(repo.git("rev-list", f"{state['baseline']}..HEAD")).split():
            changed = None if commit in commits else next((p for p in repo.diff_names(commit) if footprint.touches(p)), None)
            if changed:
                later.append((commit, changed))
        if later:
            commit, changed = later[-1]
            refusals.append(
                f"{len(later)} commits after the baseline that the migration did not make changed a migration path, "
                f"such as {commit[:12]}, which changed {changed}. Move that work out or revert it first"
            )
    for op in state["ops"]:
        if op["op"] == "move" and f"unmove {op['from']}" not in done:
            if os.path.lexists(repo.disk(op["from"])) and os.path.lexists(repo.disk(op["to"])):
                refusals.append(f"{op['from']} exists again beside {op['to']}, so the move cannot be undone")
    return refusals


def rollback(repo: Repository, journal: Journal, state: dict, out) -> int:
    """Undo the done steps in reverse. Each inverse step is journalled, so a second --rollback resumes."""
    moves = MoveMap(state["moves"])
    commits = migration_commits(repo, state)
    progress = state.get("rollback")
    if progress:
        print("resuming the unfinished rollback in the journal", file=out)
    done = set(progress["done"]) if progress else set()
    refusals = rollback_refusals(repo, state, moves, commits, done)
    if refusals:
        for refusal in refusals:
            print(f"rollback refuses: {refusal}", file=out)
        return 1
    if not progress:
        progress = state["rollback"] = {"head": repo.head(), "done": [], "conflicts": []}
        journal.save(state)

    def finish(step: str) -> None:
        progress["done"].append(step)
        done.add(step)
        journal.save(state)
        checkpoint(len(progress["done"]) - 1, {"op": f"rollback {step}"})

    ops = {op["op"]: op for op in state["ops"] if op["op"] != "move"}
    if "disk" not in done:
        conflicts = []
        for entry in ops["rewrite-disk"].get("files", []):
            try:
                with open(repo.disk(entry["path"]), "rb") as file:
                    current = sha256(file.read())
            except OSError:
                current = None
            if current == entry["after"]:
                with open(repo.work("disk", f"{entry['slot']}.before"), "rb") as file:
                    replace_file(repo, entry["path"], file.read())
            elif current != entry["before"]:
                conflicts.append(f"{entry['path']} changed after the rewrite and keeps its current bytes")
        progress["conflicts"] = conflicts
        finish("disk")
    for op in reversed([op for op in state["ops"] if op["op"] == "move"]):
        step = f"unmove {op['from']}"
        if step in done:
            continue
        source, target = repo.disk(op["from"]), repo.disk(op["to"])
        if os.path.lexists(source) and os.path.lexists(target):
            raise Stop(f"{op['from']} exists again beside {op['to']}, so the move cannot be undone")
        if os.path.lexists(target):
            os.makedirs(os.path.dirname(source), exist_ok=True)
            os.rename(target, source)
        remove_empty_parents(os.path.dirname(target), repo.plans_root)
        finish(step)
    if "revert" not in done:
        if commits:
            revert_commit(repo, journal, state, progress, commits)
        finish("revert")
    if "reconcile" not in done:
        paths = changed_paths(repo, state)
        stray = unexpected_staged(repo, expected_index(repo, state), paths)
        if stray:
            raise Stop(f"another writer staged {stray[0]}, which the rollback would unstage. Commit or unstage it, then rerun --rollback")
        reconcile(repo, paths)
        finish("reconcile")
    state["rolled_back"] = True
    journal.save(state)
    for line in progress["conflicts"]:
        print(f"conflict: {line}", file=out)
    print(f"rolled back {len(state['moves'])} moves" + (f", revert commit {state['revert'][:12]}" if state.get("revert") else ""), file=out)
    return 1 if progress["conflicts"] else 0


def revert_commit(repo: Repository, journal: Journal, state: dict, progress: dict, commits: list[str]) -> None:
    """Commit the baseline entries back at every path the migration commits changed, from a private index."""
    if progress.get("revert_tree"):
        found = repo.find_commit(progress["revert_parent"], progress["revert_tree"])
        if found:
            state["revert"] = found
            return
    baseline = repo.ls_tree(state["baseline"])
    ours = repo.ls_tree(commits[-1])
    head = repo.head()
    current = repo.ls_tree(head)
    zero = "0" * len(head)
    records = []
    for path in changed_paths(repo, state):
        if current.get(path) != ours.get(path):
            progress["conflicts"].append(f"{path} changed in HEAD after the migration and keeps its HEAD version")
            continue
        if path in baseline:
            mode, _, obj, _ = baseline[path]
            records.append(f"{mode} {obj}\t{path}")
        else:
            records.append(f"0 {zero}\t{path}")
    index = repo.private_index(head)
    repo.update_index(index, records)
    tree = to_text(repo.git("write-tree", index=index)).strip()
    if tree == repo.tree_of(head):
        state["revert"] = head
        return
    progress["revert_parent"], progress["revert_tree"] = head, tree
    journal.save(state)
    text = message(state, subjects(state)[2], "plan_paths.py --rollback undid the moves.", state["agent"], state["session"])
    state["revert"] = land(repo, index, head, tree, text, "revert", "Rerun --rollback to resume")


# Commands


def summarize(label: str, items: list[str], out) -> None:
    print(f"{label}: {len(items)}", file=out)
    for item in items[:LIST_LIMIT]:
        print(f"  {item}", file=out)
    if len(items) > LIST_LIMIT:
        print(f"  ... and {len(items) - LIST_LIMIT} more", file=out)


def by_form(results: dict[str, Rewrite]) -> list[str]:
    files: dict[str, set[str]] = {}
    references: dict[str, int] = {}
    for path, result in results.items():
        for form in result.forms:
            files.setdefault(form, set()).add(path)
            references[form] = references.get(form, 0) + 1
    return [f"{form}: {len(files[form])} files, {references[form]} references" for form in sorted(files)]


def clean_globs(globs: list[str] | None) -> list[str]:
    return list(dict.fromkeys(glob.replace("\\", "/") for glob in globs or []))


def command_key(args) -> dict:
    if args.command == "move":
        return {"from": clean_relative(args.source), "to": clean_relative(args.target)}
    return {"scope": args.scope}


def collisions(repo: Repository, tree, moves: list[tuple[str, str, str]]) -> list[str]:
    """Return a problem for each pair of moves whose paths would meet, so the whole mapping stays one-to-one.

    Two items meet when one lands on or inside the other's new path, or inside an item that moves. Every
    tracked path, mapped through the moves, must stay distinct too. Windows compares paths without letter case.
    """
    key = (lambda path: path.casefold()) if repo.fold else (lambda path: path)
    problems = []

    def within(path: str, base: str) -> bool:
        return path == base or path.startswith(base + "/")

    olds = {}
    for old, _, _ in moves:
        if old.casefold() in olds:
            problems.append(f"{olds[old.casefold()]} and {old} differ only in letter case")
        olds.setdefault(old.casefold(), old)
    for number, (old, new, _) in enumerate(moves):
        for other_old, other_new, _ in moves[number + 1 :]:
            if within(key(new), key(other_new)) or within(key(other_new), key(new)):
                problems.append(f"{old} and {other_old} would both move to {min(new, other_new, key=len)}")
        for other_old, _, _ in moves:
            if within(key(new), key(other_old)) or within(key(other_old), key(new)):
                problems.append(f"{old} would move to {new}, which overlaps the moving item {other_old}")
    if not problems:
        mapping = MoveMap(moves)
        landed: dict[str, str] = {}
        for path in tree:
            target = key(mapping.map(path))
            if target in landed:
                problems.append(f"{landed[target]} and {path} would both move to {mapping.map(path)}")
            landed.setdefault(target, path)
    return problems


def planned_moves(repo: Repository, tree, args) -> tuple[list[tuple[str, str, str]], list[str]]:
    if args.command == "move":
        moves, problems = single_move(repo, tree, args.source, args.target)
    else:
        moves, problems = inventory(repo, tree, args.scope)
    return moves, problems + collisions(repo, tree, moves)


def dry_run(repo: Repository, args, out) -> int:
    repo.exclude = clean_globs(args.exclude)
    tree = repo.ls_tree("HEAD")
    moves_list, problems = planned_moves(repo, tree, args)
    moves = MoveMap(moves_list)
    survey = Survey(repo, tree)
    rewriter = Rewriter(repo, moves, Oracle(repo, moves, list(tree)))
    scan = Scan(survey, rewriter, moves)
    hits = repo.grep_names(item_names(moves))
    candidates = set(scan.changed) | set(modified_paths(repo.status(), lambda path: path))
    kept: list[str] = []
    rewrites = disk_rewrites(repo, survey, rewriter, moves, candidates, hits, kept)
    untracked = [path for path, _, _, tracked in rewrites if not tracked]
    summarize("items to move", [f"{old} -> {new}" for old, new, _ in moves_list], out)
    print(f"tracked files to rewrite: {len(scan.changed)}, references: {sum(len(r.edits) for r in scan.changed.values())}", file=out)
    for line in by_form(scan.changed):
        print(f"  {line}", file=out)
    summarize("untracked or ignored files to rewrite on disk", untracked, out)
    by_path = [path for path in scan.frozen_hits if frozen_by_path(path)]
    summarize("frozen files that keep a moved path, by folder or name", by_path, out)
    summarize("frozen files that keep a moved path, by a recorded digest", [p for p in scan.frozen_hits if p not in by_path], out)
    summarize("excluded files that keep a moved path, by --exclude", sorted(set(scan.excluded_hits) | set(kept)), out)
    summarize("unresolved relative references", [f"{path}: {token}" for path, token in scan.unresolved], out)
    summarize(".gitignore rules that cannot be rewritten", [f"{path}: {rule}" for path, rule in scan.rules], out)
    summarize("problems", problems, out)
    footprint = Footprint(repo, moves, [*scan.changed, *(path for path, *_ in rewrites)])
    lines, refusals = preflight(repo, args.session, rewriter, {}, footprint, hits)
    for line in lines:
        print(f"preflight: {line}", file=out)
    for refusal in refusals:
        print(f"preflight would refuse: {refusal}", file=out)
    return 1 if problems else 0


def check_identity(args) -> None:
    if not args.agent or not AGENT.match(args.agent):
        raise Stop("--agent must read '<model>[<context>] (effort <effort>, <harness>)'")
    if not args.session or not SESSION.match(args.session):
        raise Stop("--session must be the session's UUID")


def apply(repo: Repository, args, out) -> int:
    check_identity(args)
    journal = Journal(repo)
    state = journal.load()
    key = {"command": args.command, **command_key(args)}
    if state and state.get("rollback") and not state.get("rolled_back"):
        raise Stop(f"a rollback of {state['command']} {state['key']} is unfinished in {journal.path}. Run the same --rollback again to finish it")
    if state and not state.get("complete") and not state.get("rolled_back"):
        if {"command": state["command"], **state["key"]} != key:
            raise Stop(f"an unfinished run of {state['command']} {state['key']} is in {journal.path}. Resume it or roll it back first")
        moves = MoveMap(state["moves"])
        allowed = expected_index(repo, state)
        repo.exclude = state.get("exclude", [])
        print("resuming the unfinished run in the journal", file=out)
    else:
        state = None
        repo.exclude = clean_globs(args.exclude)
        tree = repo.ls_tree("HEAD")
        moves_list, problems = planned_moves(repo, tree, args)
        if problems:
            summarize("problems", problems, out)
            return 1
        if not moves_list:
            print("nothing to move", file=out)
            return 0
        moves = MoveMap(moves_list)
        allowed = {}
    tree = repo.ls_tree("HEAD")
    rewriter = Rewriter(repo, moves, Oracle(repo, moves, [moves.unmap(path) for path in tree]))
    survey = Survey(repo, tree)
    scan = Scan(survey, rewriter, moves)
    hits = repo.grep_names(item_names(moves))
    status = repo.status()
    candidates = set(scan.changed) | set(modified_paths(status, lambda path: path))
    disk = [path for path, *_ in disk_rewrites(repo, survey, rewriter, moves, candidates, hits)]
    footprint = Footprint(repo, moves, [*scan.changed, *disk, *allowed, *(journaled_rewrites(state) if state else [])])
    lines, refusals = preflight(repo, args.session, rewriter, allowed, footprint, hits)
    for line in lines:
        print(f"preflight: {line}", file=out)
    if refusals:
        for refusal in refusals:
            print(f"preflight refuses: {refusal}", file=out)
        return 1
    if not (args.verified_quiet or args.owner_confirmed_quiet):
        print(QUIET_PROMPT, file=out)
        return 2
    if state is None:
        state = {
            "version": 1,
            "command": args.command,
            "key": command_key(args),
            "agent": args.agent,
            "session": args.session,
            "baseline": repo.head(),
            "status": status,
            "counts": count_items(repo),
            "unresolved": len(scan.unresolved),
            "exclude": list(repo.exclude),
            "frozen": {path: survey.digest_of[path] for path in sorted(survey.frozen)},
            "moves": [list(move) for move in moves.moves],
            "ops": [{"op": "commit-a", "state": "pending"}]
            + [{"op": "move", "from": old, "to": new, "kind": kind, "state": "pending"} for old, new, kind in moves.moves]
            + [{"op": name, "state": "pending"} for name in ("commit-b", "rewrite-disk", "reconcile")],
        }
        journal.save(state)
    run_ops(repo, journal, state)
    ops = {op["op"]: op for op in state["ops"]}
    print(f"moved {len(state['moves'])} items", file=out)
    for name in ("commit-a", "commit-b"):
        commit = ops[name].get("commit")
        print(f"{name}: {commit[:12] if commit else 'nothing to commit'}", file=out)
    disk = ops["rewrite-disk"]
    print(f"working-tree files rewritten: {len(disk.get('files', []))}", file=out)
    for path in disk.get("conflicts", []):
        print(f"conflict: {path} changed during the run and was not rewritten", file=out)
    print("Run --verify next.", file=out)
    return 1 if disk.get("conflicts") else 0


def verify(repo: Repository, args, out) -> int:
    failures, notes = [], []
    state = Journal(repo).load()
    finished = bool(state and state.get("complete") and state["command"] == "plan" and state["key"].get("scope") == args.scope)
    repo.exclude = state.get("exclude", []) if finished else []
    if state and not state.get("rolled_back") and (state.get("rollback") or not state.get("complete")):
        action = "rollback" if state.get("rollback") else "run"
        failures.append(f"the journal holds an unfinished {action} of {state['command']} {state['key']}")
    tree = repo.ls_tree("HEAD")
    leftovers, problems = inventory(repo, tree, args.scope)
    if leftovers:
        failures.append(f"{len(leftovers)} dated items in scope {args.scope} still sit at the old depth, such as {leftovers[0][0]}")
    failures += [f"inventory: {problem}" for problem in problems]
    survey = Survey(repo, tree)
    identity = MoveMap([])
    scan = Scan(survey, Rewriter(repo, identity, Oracle(repo, identity, list(tree))), identity)
    notes.append(f"unresolved relative references: {len(scan.unresolved)}")
    counts = count_items(repo)
    notes += [f"{name}: {value}" for name, value in counts.items()]
    for root in SCOPES[args.scope]:
        flat = counts[f"{root or 'active'} at the old depth"]
        if flat:
            failures.append(f"{flat} dated entries sit at the old depth in {repo.join_plans(root) or 'the plans root'}")
    if finished:
        moves = MoveMap(state["moves"])
        for root in ("active", "archive", "retrospectives"):
            if counts[root] != state["counts"][root]:
                failures.append(f"{root} holds {counts[root]} dated items, and the baseline held {state['counts'][root]}")
        if len(scan.unresolved) > state["unresolved"]:
            failures.append(f"{len(scan.unresolved)} unresolved relative references, more than the baseline {state['unresolved']}")
        for path, digest in state["frozen"].items():
            new = moves.map(path)
            if survey.digest_of.get(new) != digest:
                failures.append(f"frozen file {new} changed or is missing")
        expected = sorted((code, moves.map(path)) for code, path in state["status"])
        actual = sorted(repo.status())
        if expected != actual:
            missing = sorted(set(expected) - set(actual))[:5]
            extra = sorted(set(actual) - set(expected))[:5]
            failures.append(f"git status differs from the baseline: missing {missing}, new {extra}")
        footprint = journal_footprint(repo, state, moves)
        staged = [path for path in repo.staged_paths() if footprint.touches(path)]
        if staged:
            failures.append(f"{len(staged)} entries are staged at migration paths, such as {staged[0]}")
        ops = {op["op"]: op for op in state["ops"]}
        ours = {ops[name].get("commit") for name in ("commit-a", "commit-b")}
        sample = next((new + "/plan.md" for _, new, kind in state["moves"] if kind == "dir" and new + "/plan.md" in tree), None)
        if sample:
            history = to_text(repo.git("log", "--follow", "--format=%H", "--", sample)).split()
            if not [commit for commit in history if commit not in ours]:
                failures.append(f"git log --follow does not trace {sample} past the move")
        stale = Scan(survey, Rewriter(repo, moves, Oracle(repo, moves, list(tree)), anchored_only=True), identity)
        if stale.changed:
            failures.append(f"{len(stale.changed)} tracked files still name an old plan path, such as {next(iter(stale.changed))}")
    else:
        notes.append("no finished journal for this scope, so the baseline checks did not run")
    for note in notes:
        print(note, file=out)
    for failure in failures:
        print(f"FAIL: {failure}", file=out)
    print("verified" if not failures else f"{len(failures)} checks failed", file=out)
    return 1 if failures else 0


def resolve(repo: Repository, old: str) -> str | None:
    """Return the current path of the plan item that an old path names, or None."""
    text = old.replace("\\\\", "/").replace("\\", "/")
    parent, root = (to_text(name) for name in repo.anchor)
    marker = f"{parent}/{root}/"
    position = text.lower().find(marker.lower())
    prefix, rest = (text[: position + len(marker)], text[position + len(marker) :]) if position >= 0 else ("", text)
    if is_absolute(normal_prefix(text)):
        prefix = repo.plans_root.replace(os.sep, "/").rstrip("/") + "/"
    segments = [segment for segment in rest.split("/") if segment]
    index = next((i for i, segment in enumerate(segments) if DATED.match(segment)), None)
    if index is None:
        return None
    name, tail = segments[index], segments[index + 1 :]
    given = segments[0] if index > 0 and segments[0] in ("archive", "retrospectives") else ""
    stem, is_file = (name[:-3], True) if name.lower().endswith(".md") else (name, False)
    try:
        nested = new_path(stem)
    except ValueError:
        return None
    for base in dict.fromkeys([given, "archive", "", "retrospectives"]):
        for candidate in (nested, stem, name):
            location = posixpath.join(base, candidate) if base else candidate
            if os.path.isdir(os.path.join(repo.plans_root, *location.split("/"))):
                parts = [location, "plan.md"] if is_file and candidate != name else [location]
                return prefix + "/".join(parts + tail)
            if is_file and candidate == name and os.path.isfile(os.path.join(repo.plans_root, *location.split("/"))):
                return prefix + "/".join([location] + tail)
    return None


def peak_memory_mb() -> float | None:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize / (1024 * 1024)
    return None


def parser() -> argparse.ArgumentParser:
    main_parser = argparse.ArgumentParser(description="Nest dated plan folders by year and month and repoint their paths.")
    commands = main_parser.add_subparsers(dest="command", required=True)

    def exclusions(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--exclude",
            action="append",
            metavar="GLOB",
            help="Never rewrite a path that matches this fnmatch glob, relative to the Git root. Repeatable.",
        )

    def identity(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--agent", help="The Agent trailer value: '<model>[<context>] (effort <effort>, <harness>)'.")
        sub.add_argument("--session", help="The Agent-Session trailer value: the session UUID.")
        sub.add_argument(
            "--verified-quiet",
            action="store_true",
            help="The executor checked that no other writer is changing the moved items or the files that name them.",
        )
        sub.add_argument(
            "--owner-confirmed-quiet",
            action="store_true",
            help="The owner confirmed that no other writer is changing the moved items or the files that name them.",
        )

    plan = commands.add_parser("plan", help="Move every dated item in a scope.")
    plan.add_argument("plans_root")
    plan.add_argument("--scope", choices=sorted(SCOPES), required=True)
    modes = plan.add_mutually_exclusive_group(required=True)
    for flag in ("--dry-run", "--apply", "--verify", "--rollback"):
        modes.add_argument(flag, action="store_true")
    identity(plan)
    exclusions(plan)

    move = commands.add_parser("move", help="Move one folder or file, such as a plan into archive/.")
    move.add_argument("plans_root")
    move.add_argument("source", metavar="from")
    move.add_argument("target", metavar="to")
    modes = move.add_mutually_exclusive_group(required=True)
    for flag in ("--dry-run", "--apply", "--rollback"):
        modes.add_argument(flag, action="store_true")
    identity(move)
    exclusions(move)

    find = commands.add_parser("resolve", help="Print the current path of the plan an old path names.")
    find.add_argument("plans_root")
    find.add_argument("path")
    return main_parser


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    started = time.perf_counter()
    args = parser().parse_args(argv)
    try:
        repo = Repository(args.plans_root)
        if args.command == "resolve":
            found = resolve(repo, args.path)
            if found is None:
                print(f"plan_paths: no plan folder matches {args.path}", file=sys.stderr)
                code = 1
            else:
                print(found, file=out)
                code = 0
        elif args.dry_run:
            code = dry_run(repo, args, out)
        elif getattr(args, "verify", False):
            code = verify(repo, args, out)
        elif args.apply:
            code = apply(repo, args, out)
        else:
            check_identity(args)
            journal = Journal(repo)
            state = journal.load()
            if not state or state.get("rolled_back") or {"command": state["command"], **state["key"]} != {
                "command": args.command,
                **command_key(args),
            }:
                print("nothing to roll back for this command", file=out)
                code = 0
            else:
                moves = MoveMap(state["moves"])
                oracle = Oracle(repo, moves, list(repo.ls_tree("HEAD")))
                lines, refusals = preflight(
                    repo, args.session, Rewriter(repo, moves, oracle), expected_index(repo, state), journal_footprint(repo, state, moves),
                )
                for line in lines:
                    print(f"preflight: {line}", file=out)
                for refusal in refusals:
                    print(f"preflight refuses: {refusal}", file=out)
                if refusals:
                    code = 1
                elif not (args.verified_quiet or args.owner_confirmed_quiet):
                    print(QUIET_PROMPT, file=out)
                    code = 2
                else:
                    code = rollback(repo, journal, state, out)
    except Stop as error:
        print(f"plan_paths: {error}", file=sys.stderr)
        code = 2
    peak = peak_memory_mb()
    memory = f"{peak:.1f} MB" if peak is not None else "unavailable"
    print(f"elapsed: {time.perf_counter() - started:.2f} s, peak memory: {memory}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
