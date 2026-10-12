#!/usr/bin/env python3
"""Run Arc's session cost command and check its JSON contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


SCHEMA_VERSION = 2


def normalize_target(target: str) -> str:
    """Remove a recognized discovery prefix from an exact session UUID."""
    match = re.fullmatch(r"(?:claude|claude-code|codex):([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", target)
    return match.group(1) if match else target


def default_repo() -> Path:
    """Return the Arc checkout beside the main checkout of this skills repository."""
    here = Path(__file__).resolve().parent
    try:
        common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return here.parents[3] / "agent-diff-review"
    return Path(common).parent.parent / "agent-diff-review"


DEFAULT_REPO = default_repo()
BUNDLE = Path("cli", "dist", "arc.cjs")
BUILD_HELP = ('npm --prefix "{repo}/cli" install', 'npm --prefix "{repo}/cli" run build')
NODE_OPTIONS_WITH_VALUE = {
    "-C", "-r", "--allow-fs-read", "--allow-fs-write", "--allow-net",
    "--build-snapshot-config", "--conditions", "--cpu-prof-dir", "--cpu-prof-interval",
    "--cpu-prof-name", "--debug-port", "--diagnostic-dir", "--disable-proto",
    "--dns-result-order", "--env-file", "--env-file-if-exists", "--experimental-config-file",
    "--experimental-loader", "--experimental-sea-config", "--heap-prof-dir",
    "--heap-prof-interval", "--heap-prof-name", "--heapsnapshot-near-heap-limit",
    "--heapsnapshot-signal", "--icu-data-dir", "--import", "--input-type", "--inspect-port",
    "--inspect-publish-uid", "--loader", "--localstorage-file", "--max-http-header-size",
    "--max-old-space-size-percentage", "--openssl-config", "--redirect-warnings", "--report-dir",
    "--report-directory", "--report-filename", "--report-signal", "--require", "--run",
    "--secure-heap", "--secure-heap-min", "--snapshot-blob", "--test-concurrency",
    "--test-coverage-branches", "--test-coverage-exclude", "--test-coverage-functions",
    "--test-coverage-include", "--test-coverage-lines", "--test-global-setup",
    "--test-isolation", "--test-name-pattern", "--test-reporter",
    "--test-reporter-destination", "--test-rerun-failures", "--test-shard",
    "--test-skip-pattern", "--test-timeout", "--title", "--tls-cipher-list", "--tls-keylog",
    "--trace-event-categories", "--trace-event-file-pattern", "--trace-require-module",
    "--unhandled-rejections", "--use-largepages", "--v8-pool-size", "--watch-kill-signal",
    "--watch-path",
}


def split_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parts = [part[1:-1] if len(part) > 1 and part[0] == part[-1] and part[0] in {'"', "'"} else part for part in parts]
    return parts


def command_for(env: dict[str, str] | None = None) -> tuple[list[str] | None, list[str], Path]:
    values = os.environ if env is None else env
    configured = values.get("ARC_STATUS")
    repo = Path(values.get("ARC_REPO") or DEFAULT_REPO).expanduser()
    bundle = repo / BUNDLE
    checked = [str(bundle)]
    if configured:
        checked.insert(0, f"ARC_STATUS={configured}")
        return split_command(configured), checked, repo
    if values.get("ARC_REPO"):
        if not bundle.is_file():
            return None, checked, repo
        refresh_build(repo)
        return ["node", str(bundle)], checked, repo
    executables = path_commands(values.get("PATH"))
    linked = next(filter(None, map(linked_checkout, executables)), None)
    if linked:
        refresh_build(linked)
        return ["node", str(linked / BUNDLE)], checked, linked
    if bundle.is_file():
        refresh_build(repo)
        return ["node", str(bundle)], checked, repo
    # Any other `arc` on PATH runs a build the adapter cannot name, such as a stale npm shim.
    installed = next(filter(None, map(editor_bundle, executables)), None)
    if installed:
        return ["node", str(installed)], checked, repo
    return None, checked, repo


def path_commands(path: str | None) -> list[str]:
    """Return every `arc` command on PATH in search order."""
    # The editor extension puts its own `arc` on PATH, often ahead of the one `npm link` adds.
    found: list[str] = []
    for folder in (os.environ.get("PATH", os.defpath) if path is None else path).split(os.pathsep):
        # On POSIX an empty PATH entry names the current directory.
        folder = folder or (os.curdir if os.name != "nt" else "")
        executable = shutil.which("arc", path=folder) if folder else None
        if executable and executable not in found:
            found.append(executable)
    return found


def linked_checkout(executable: str) -> Path | None:
    """Return the Arc checkout whose cli package `npm link` put on PATH."""
    command = Path(executable)
    # A POSIX link points at the bundle. A Windows shim sits beside the global node_modules folder.
    for candidate in (command, command.parent / "node_modules" / "@glin" / "arc" / BUNDLE.relative_to("cli")):
        try:
            bundle = candidate.resolve(strict=True)
        except OSError:
            continue
        if bundle.name != BUNDLE.name or bundle.parent.name != "dist" or bundle.parents[1].name != "cli":
            continue
        checkout = bundle.parents[2]
        if (checkout / ".git").exists():
            return checkout
    return None


def editor_bundle(executable: str) -> Path | None:
    """Return the bundle that the editor extension's `dist/bin/arc` command runs."""
    command = Path(executable)
    if command.stem.lower() != "arc" or command.parent.name != "bin" or command.parent.parent.name != "dist":
        return None
    bundle = command.parent.parent / "cli.cjs"
    return bundle if bundle.is_file() else None


# The bundle compiles these folders, so an uncommitted edit in them would ship under HEAD's commit.
BUNDLE_SOURCES = ("cli", "extension/src")


def refresh_build(repo: Path) -> None:
    """Rebuild a checkout's bundle when HEAD moved past the commit it was built from."""
    bundle = repo / BUNDLE
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        if head in bundle.read_text(encoding="utf-8", errors="replace"):
            return
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", *BUNDLE_SOURCES],
                                capture_output=True, text=True, check=True).stdout
        if status.strip():
            print(f"Arc at {repo} was built from another commit and has uncommitted changes in "
                  f"{' or '.join(BUNDLE_SOURCES)}, so it was not rebuilt.", file=sys.stderr)
            return
        npm = shutil.which("npm")
        if npm:
            subprocess.run([npm, "--prefix", str(repo / "cli"), "run", "build"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return


def missing_arc(checked: list[str], repo: Path) -> int:
    print("Arc cost command is unavailable.", file=sys.stderr)
    print("Checked: " + ", ".join(checked), file=sys.stderr)
    print(f"Install or locate the Arc checkout at {repo}.", file=sys.stderr)
    print("Build its command with:", file=sys.stderr)
    for command in BUILD_HELP:
        print(command.format(repo=repo), file=sys.stderr)
    return 2


def missing_node_entry(command: list[str], stderr: str) -> bool:
    if not command or Path(command[0]).name.lower() not in {"node", "node.exe"}:
        return False
    entry_arg = None
    index = 1
    while index < len(command):
        argument = command[index]
        if argument in {"-e", "--eval", "-p", "--print"}:
            return False
        if argument == "--":
            entry_arg = command[index + 1] if index + 1 < len(command) else None
            break
        if argument in NODE_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if not argument.startswith("-"):
            entry_arg = argument
            break
        index += 1
    if not entry_arg:
        return False
    entry = Path(entry_arg).expanduser().resolve()
    if entry.suffix not in {".js", ".cjs", ".mjs"} or entry.is_file():
        return False
    match = re.search(r"^\[?Error(?: \[ERR_MODULE_NOT_FOUND\])?: Cannot find module '(.+)'(?: imported from .*)?\]?(?: \{)?$",
                      stderr, re.MULTILINE)
    if not match:
        return False
    return entry == Path(match.group(1)).resolve()


def main(args: list[str] | None = None) -> int:
    command, checked, repo = command_for()
    if not command:
        return missing_arc(checked, repo)
    forwarded = list(sys.argv[1:] if args is None else args)
    if "--json" not in forwarded:
        forwarded.append("--json")
    try:
        result = subprocess.run([*command, "cost", *forwarded], capture_output=True, text=True,
                                encoding="utf-8", check=False)
    except OSError:
        return missing_arc(checked, repo)
    if result.returncode:
        if os.environ.get("ARC_STATUS") and missing_node_entry(command, result.stderr):
            return missing_arc(checked, repo)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        return result.returncode
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Arc returned invalid cost JSON.", file=sys.stderr)
        return 2
    if not isinstance(report, dict) or report.get("schemaVersion") != SCHEMA_VERSION:
        print(f"Unsupported Arc cost schema. Expected schemaVersion {SCHEMA_VERSION}.", file=sys.stderr)
        return 2
    output = result.stdout if result.stdout.endswith("\n") else result.stdout + "\n"
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(output.encode("utf-8"))
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
