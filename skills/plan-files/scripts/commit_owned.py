#!/usr/bin/env python3
"""Commit complete owned files without consuming another writer's staged changes.

See references/commit-owned.md for the admission and recovery contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import uuid


class Refusal(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args, index=None, data=None, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    env["GIT_EDITOR"] = ":"
    result = subprocess.run(["git", "-C", str(root), *map(str, args)], input=data,
                            capture_output=True, env=env)
    if check and result.returncode:
        raise Refusal(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout if check else result


def text(root, *args, **kwargs):
    return git(root, *args, **kwargs).decode().strip()


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise Refusal(f"Expected an ordinary file: {path}")
    return info


def no_links(path, boundary=None):
    boundary = boundary or path.parent
    if not path.is_relative_to(boundary):
        raise Refusal(f"Path is outside its admitted directory: {path}")
    for item in [path, *path.parents]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal(f"Directory links require explicit resolution: {item}")
        if item == boundary:
            break


def entries(root, index):
    result = {}
    for row in git(root, "ls-files", "--stage", "-z", index=index).split(b"\0"):
        if row:
            header, path = row.split(b"\t", 1)
            result.setdefault(path.decode(), []).append(header.decode())
    return result


def tree_entries(root, commit):
    result = {}
    for row in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if row:
            header, path = row.split(b"\t", 1)
            mode, kind, blob = header.decode().split()
            result[path.decode()] = [f"{mode} {blob} 0"]
    return result


def checkpoint(event, state):
    """Provide deterministic transaction boundaries for disposable tests."""


def save(folder, state):
    temporary = folder / "receipt.tmp"
    data = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
    if temporary.exists():
        regular(temporary)
        if temporary.read_bytes() != data:
            raise Refusal(f"An interrupted receipt output requires inspection: {temporary}")
        temporary.unlink()
    with temporary.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, folder / "receipt.json")


def remember(folder, state, name, data):
    (folder / name).write_bytes(data)
    state["members"][name] = digest(data)


def inventory(folder, state, *, partial=False):
    no_links(folder)
    allowed = {"message", "candidate.index", "shared.index", "prepared.index"} | {
        f"input-{number}" for number in range(len(state["baselines"]))}
    if set(state["members"]) - allowed:
        raise Refusal("The receipt names an unowned recovery member")
    expected = set(state["members"]) | {"receipt.json"}
    actual = set(p.name for p in folder.iterdir())
    if "receipt.tmp" in actual:
        regular(folder / "receipt.tmp")
        # Only complete, independently recognizable receipt output can be resumed.
        choices = []
        for phase in (state["phase"], "prepared", "published", "reconciled", "rolled_back", "disposing"):
            choice = dict(state, phase=phase)
            if phase in ("reconciled", "rolled_back"):
                choice.pop("lock_identity", None)
            if phase == "prepared" and "prepared.index" in state["members"]:
                choice["lock_digest"] = state["members"]["prepared.index"]
            choices.append((json.dumps(choice, sort_keys=True, indent=2) + "\n").encode())
        if (folder / "receipt.tmp").read_bytes() not in choices:
            raise Refusal("Unrecognized interrupted receipt output")
        actual.remove("receipt.tmp")
    if actual - expected or (not partial and actual != expected):
        raise Refusal(f"Unexpected recovery members: {folder}")
    for name, expected_digest in state["members"].items():
        path = folder / name
        try:
            regular(path)
        except FileNotFoundError:
            if partial:
                continue
            raise
        if digest(path.read_bytes()) != expected_digest:
            raise Refusal(f"Recovery input changed: {path}")


def lock_matches(state, *, incomplete=False):
    path = Path(state["index"] + ".lock")
    info = regular(path)
    if [info.st_dev, info.st_ino] != state["lock_identity"]:
        raise Refusal(f"The index lock has another owner: {path}")
    data = path.read_bytes()
    matches = digest(data) == state["lock_digest"]
    if incomplete and state.get("lock_write") and state["phase"] == "building":
        prepared = Path(state["index"]).parent / state["recovery_ref"].rsplit("/", 1)[1] / "prepared.index"
        matches = prepared.read_bytes().startswith(data)
    if not matches:
        raise Refusal(f"The owned index lock changed: {path}")
    return path


def verify_candidate(folder, state, *, disposing=False):
    root, candidate = state["root"], state["candidate"]
    recovery = git(root, "rev-parse", "--verify", state["recovery_ref"], check=False)
    if recovery.stdout.decode().strip() != candidate and not (disposing and recovery.returncode):
        raise Refusal("The recovery ref changed")
    if text(root, "rev-list", "--parents", "-n", "1", candidate).split() != [candidate, state["baseline"]]:
        raise Refusal("The owned commit has a different parent")
    message = bytes.fromhex(state["message_hex"])
    if digest(message) != state["members"]["message"]:
        raise Refusal("The retained message identity changed")
    validate_message(root, message)
    if git(root, "show", "-s", "--format=%B", candidate).rstrip(b"\n") != message.rstrip(b"\n"):
        raise Refusal("The owned commit message changed")
    changed = set(filter(None, git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", state["baseline"], candidate).decode().strip("\0").split("\0")))
    expected_changed = {p for p, e in state["outputs"].items() if e != state["baselines"].get(p)}
    if changed != expected_changed:
        raise Refusal("The owned commit changed unexpected paths")
    tree = tree_entries(root, candidate)
    for path, expected in state["outputs"].items():
        if tree.get(path) != expected:
            raise Refusal(f"The owned commit has a different file: {path}")


def current_owned(state):
    root = state["root"]
    if text(root, "symbolic-ref", "HEAD") != state["ref"]:
        raise Refusal("The checked-out branch changed")
    head = text(root, "rev-parse", state["ref"])
    if git(root, "merge-base", "--is-ancestor", state["candidate"], head, check=False).returncode:
        raise Refusal("The owned commit is no longer reachable from the admitted branch")
    tree = tree_entries(root, head)
    for path, expected in state["outputs"].items():
        if tree.get(path) != expected:
            raise Refusal(f"The current branch changed an owned file: {path}")


def dispose(folder, state):
    checkpoint("before_disposal", state)
    partial = state.get("phase") == "disposing"
    inventory(folder, state, partial=partial)
    verify_candidate(folder, state, disposing=partial)
    current_owned(state)
    expected = state["outputs"]
    index = entries(state["root"], state["index"])
    for path, entry in expected.items():
        if index.get(path) != entry:
            raise Refusal(f"An owned staged entry changed: {path}")
    if Path(state["index"] + ".lock").exists():
        raise Refusal("An index lock blocks disposal")
    inventory(folder, state, partial=partial)
    (folder / "receipt.tmp").unlink(missing_ok=True)
    state["phase"] = "disposing"
    save(folder, state)
    if git(state["root"], "show-ref", "--verify", state["recovery_ref"], check=False).returncode == 0:
        git(state["root"], "update-ref", "-d", state["recovery_ref"], state["candidate"])
    checkpoint("disposal_ref_removed", state)
    for name in state["members"]:
        (folder / name).unlink(missing_ok=True)
        checkpoint("disposal_member_removed", state)
    (folder / "receipt.json").unlink()
    folder.rmdir()


def validate_message(root, message):
    parsed = git(root, "interpret-trailers", "--parse", data=message).decode().splitlines()
    agents = [line[7:] for line in parsed if line.startswith("Agent: ")]
    sessions = [line[15:] for line in parsed if line.startswith("Agent-Session: ")]
    if (len(agents) != 1 or agents[0] == "unknown" or "(effort " not in agents[0] or
            ", " not in agents[0] or not agents[0].endswith(")") or len(sessions) != 1):
        raise Refusal("The message requires exact Agent and Agent-Session trailers")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sessions[0], re.I):
        raise Refusal("Agent-Session must contain the runtime UUID")


def commit(root, ref, baseline, files, message):
    root = Path(root).resolve()
    if Path(text(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise Refusal("Run from the exact repository root")
    if not ref.startswith("refs/heads/") or text(root, "symbolic-ref", "HEAD") != ref:
        raise Refusal("Name the current full branch ref")
    git(root, "check-ref-format", ref)
    if text(root, "rev-parse", ref) != baseline or text(root, "rev-parse", baseline + "^{commit}") != baseline:
        raise Refusal("The admitted baseline changed")
    validate_message(root, message)
    original = tree_entries(root, baseline)
    selected, captured = {}, []
    for item in files:
        path, sha, blob, oldmode, mode = item
        if (not path or "\\" in path or PurePosixPath(path).is_absolute() or
                any(part in ("", ".", "..", ".git") for part in path.split("/")) or ":" in path):
            raise Refusal(f"Name an exact repository-relative file: {path}")
        if path.casefold() in {p.casefold() for p in selected} or mode not in ("100644", "100755"):
            raise Refusal(f"Duplicate path or unsupported mode: {path}")
        expected = None if blob == "absent" and oldmode == "absent" else [f"{oldmode} {blob} 0"]
        if any(p != path and p.casefold() == path.casefold() for p in original):
            raise Refusal(f"The path aliases a baseline entry: {path}")
        if original.get(path) != expected:
            raise Refusal(f"The admitted baseline entry differs: {path}")
        source = root / path
        no_links(source, root)
        regular(source)
        data = source.read_bytes()
        if digest(data) != sha:
            raise Refusal(f"The working file changed: {path}")
        selected[path] = expected
        captured.append((path, mode, data))
    if not captured:
        raise Refusal("Name at least one complete owned file")
    index = str(Path(text(root, "rev-parse", "--path-format=absolute", "--git-path", "index")).resolve())
    folder = Path(index).parent / ("plan-commit-" + uuid.uuid4().hex)
    folder.mkdir()
    state = {"root": str(root), "ref": ref, "baseline": baseline, "index": index,
             "recovery_ref": "refs/plan-files/recovery/" + folder.name,
             "baselines": selected, "outputs": {}, "members": {}, "phase": "building",
             "message_hex": message.hex()}
    remember(folder, state, "message", message)
    save(folder, state)
    try:
        private = folder / "candidate.index"
        git(root, "read-tree", baseline, index=private)
        for number, (path, mode, data) in enumerate(captured):
            remember(folder, state, f"input-{number}", data)
            blob = text(root, "hash-object", "-w", "--path=" + path, "--stdin", data=data)
            state["outputs"][path] = [f"{mode} {blob} 0"]
            git(root, "update-index", "--add", "--cacheinfo", mode, blob, path, index=private)
        tree = text(root, "write-tree", index=private)
        for hook, args in [("pre-commit", []), ("prepare-commit-msg", [str(folder / "message"), "message"]),
                           ("commit-msg", [str(folder / "message")])]:
            git(root, "hook", "run", "--ignore-missing", hook, *(["--", *args] if args else []), index=private)
        validate_message(root, (folder / "message").read_bytes())
        if (folder / "message").read_bytes() != message or text(root, "write-tree", index=private) != tree:
            raise Refusal("A commit hook changed the admitted message or files")
        signed = git(root, "config", "--type=bool", "--get", "commit.gpgSign", check=False).stdout.strip() == b"true"
        state["candidate"] = text(root, "commit-tree", tree, "-p", baseline, *(["-S"] if signed else []), "-F", folder / "message")
        git(root, "update-ref", state["recovery_ref"], state["candidate"], "0" * len(baseline))
        state["members"]["candidate.index"] = digest(private.read_bytes())
        save(folder, state)
        verify_candidate(folder, state)
        checkpoint("captured", state)
        acquire_and_prepare(folder, state)
        checkpoint("before_publish", state)
        if text(root, "symbolic-ref", "HEAD") != ref:
            raise Refusal("The checked-out branch changed")
        git(root, "update-ref", ref, state["candidate"], baseline)
        state["phase"] = "published"
        save(folder, state)
        checkpoint("published", state)
        finish_index(folder, state)
        checkpoint("reconciled", state)
        dispose(folder, state)
        return state["candidate"]
    except Exception as error:
        # A published ref always retains the lock until an explicit recovery checks it.
        for name in ("candidate.index", "shared.index", "prepared.index"):
            item = folder / name
            if item.exists():
                regular(item)
                state["members"].setdefault(name, digest(item.read_bytes()))
        state["error"] = str(error)
        save(folder, state)
        if state.get("lock_identity") and state["phase"] not in ("reconciled", "disposing"):
            published = state.get("candidate") and git(root, "merge-base", "--is-ancestor", state["candidate"], ref, check=False).returncode == 0
            if state["phase"] != "published" and not published:
                lock_matches(state, incomplete=True).unlink()
                state.pop("lock_identity")
        save(folder, state)
        raise Refusal(f"{error}\nRecovery retained at {folder / 'receipt.json'}") from error


def acquire_and_prepare(folder, state):
    root, index = state["root"], Path(state["index"])
    lock = Path(str(index) + ".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    info = os.fstat(fd)
    state["lock_identity"] = [info.st_dev, info.st_ino]
    state["lock_digest"] = digest(b"")
    os.close(fd)
    save(folder, state)
    state["index_mode"] = stat.S_IMODE(regular(index).st_mode)
    snapshot = index.read_bytes()
    remember(folder, state, "shared.index", snapshot)
    before = entries(root, folder / "shared.index")
    for path, expected in state["baselines"].items():
        if before.get(path) != expected:
            raise Refusal(f"An owned staged entry differs from baseline: {path}")
    prepared = folder / "prepared.index"
    prepared.write_bytes(snapshot)
    for path, values in state["outputs"].items():
        mode, blob, _ = values[0].split()
        git(root, "update-index", "--add", "--cacheinfo", mode, blob, path, index=prepared)
    after = entries(root, prepared)
    if {p: e for p, e in before.items() if p not in state["outputs"]} != {p: e for p, e in after.items() if p not in state["outputs"]}:
        raise Refusal("An unrelated staged entry changed")
    data = prepared.read_bytes()
    state["members"]["prepared.index"] = digest(data)
    state["lock_write"] = True
    save(folder, state)
    checkpoint("before_lock_write", state)
    lock_matches(state)
    fd = os.open(lock, os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if not written:
                raise Refusal("The index lock write made no progress")
            remaining = remaining[written:]
            checkpoint("lock_write_progress", state)
        os.fsync(fd)
    finally:
        os.close(fd)
    state["lock_digest"] = digest(data)
    lock_matches(state)
    if entries(root, lock) != after:
        raise Refusal("The prepared index lock does not parse as expected")
    state["phase"] = "prepared"
    save(folder, state)


def finish_index(folder, state):
    inventory(folder, state)
    lock = lock_matches(state)
    if digest(Path(state["index"]).read_bytes()) not in (state["members"]["shared.index"], state["members"]["prepared.index"]):
        raise Refusal("The shared index changed while the owned lock was held")
    current_owned(state)
    (folder / "receipt.tmp").unlink(missing_ok=True)
    os.chmod(lock, state["index_mode"])
    os.replace(lock, state["index"])
    state["phase"] = "reconciled"
    state.pop("lock_identity")
    save(folder, state)


def discard_unpublished(receipt, state, receipt_sha256, *, dry_run=False):
    folder = receipt.parent
    if not receipt_sha256 or digest(receipt.read_bytes()) != receipt_sha256:
        raise Refusal("The admitted receipt identity changed")
    if (state["phase"] != "building" or
            any(key in state for key in ("lock_identity", "lock_digest", "lock_write")) or
            {"shared.index", "prepared.index"} & set(state["members"])):
        raise Refusal("Discard requires an unprepared transaction without an owned lock")
    root = Path(state["root"])
    if Path(text(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise Refusal("The receipt does not name the repository root")
    index = Path(text(root, "rev-parse", "--path-format=absolute", "--git-path", "index")).resolve()
    if (Path(state["index"]).resolve() != index or folder.parent != index.parent or
            state["recovery_ref"] != "refs/plan-files/recovery/" + folder.name):
        raise Refusal("The receipt does not name its repository recovery directory")
    if git(root, "symbolic-ref", "-q", state["recovery_ref"], check=False).returncode == 0:
        raise Refusal("A symbolic recovery ref requires explicit resolution")
    if not state["ref"].startswith("refs/heads/"):
        raise Refusal("The receipt does not name an admitted branch")
    if text(root, "for-each-ref", "--contains", state["candidate"], "--format=%(refname)", "refs/heads/"):
        raise Refusal("A published candidate requires completion or rollback")
    inventory(folder, state)
    verify_candidate(folder, state)
    if digest(receipt.read_bytes()) != receipt_sha256:
        raise Refusal("The admitted receipt identity changed")
    if dry_run:
        return
    git(root, "update-ref", "--no-deref", "-d", state["recovery_ref"], state["candidate"])
    for name, expected in state["members"].items():
        path = folder / name
        regular(path)
        if digest(path.read_bytes()) != expected:
            raise Refusal(f"Recovery input changed: {path}")
        path.unlink()
    regular(receipt)
    if digest(receipt.read_bytes()) != receipt_sha256:
        raise Refusal("The admitted receipt identity changed")
    receipt.unlink()
    folder.rmdir()


def recover(receipt, action="complete", *, receipt_sha256=None, dry_run=False):
    receipt = Path(receipt).absolute()
    regular(receipt)
    no_links(receipt.parent)
    receipt = receipt.resolve()
    state = json.loads(receipt.read_bytes())
    folder = receipt.parent
    expected = Path(state["index"]).parent / state["recovery_ref"].rsplit("/", 1)[1]
    if folder != expected or receipt.name != "receipt.json" or not folder.name.startswith("plan-commit-"):
        raise Refusal("The receipt does not name its owned scratch directory")
    if action == "discard":
        discard_unpublished(receipt, state, receipt_sha256, dry_run=dry_run)
        return
    if receipt_sha256 is not None or dry_run:
        raise Refusal("Receipt admission and dry run apply only to discard")
    inventory(folder, state, partial=state.get("phase") == "disposing")
    verify_candidate(folder, state, disposing=state.get("phase") == "disposing")
    if action == "rollback":
        lock = lock_matches(state, incomplete=True)
        if "shared.index" in state["members"] and digest(Path(state["index"]).read_bytes()) != state["members"]["shared.index"]:
            raise Refusal("The shared index changed before rollback")
        if text(state["root"], "symbolic-ref", "HEAD") != state["ref"]:
            raise Refusal("The checked-out branch changed")
        tip = text(state["root"], "rev-parse", state["ref"])
        if tip not in (state["candidate"], state["baseline"]):
            raise Refusal("The branch changed before rollback")
        if tip == state["baseline"] and state["phase"] == "published" and state.get("rollback_from") != state["candidate"]:
            raise Refusal("The branch changed before rollback")
        (folder / "receipt.tmp").unlink(missing_ok=True)
        if tip == state["candidate"]:
            state["rollback_from"] = tip
            save(folder, state)
            git(state["root"], "update-ref", state["ref"], state["baseline"], state["candidate"])
            checkpoint("rollback_ref_restored", state)
        elif tip != state["baseline"] or (state["phase"] == "published" and state.get("rollback_from") != state["candidate"]):
            raise Refusal("The branch changed before rollback")
        lock.unlink()
        state.pop("lock_identity")
        state["phase"] = "rolled_back"
        save(folder, state)
        # Keep the pinned candidate after rollback. It has no other proven reachable ref.
    elif action == "complete":
        if state["phase"] not in ("reconciled", "disposing"):
            lock = Path(state["index"] + ".lock")
            if not lock.exists():
                # Replacement can finish before the receipt update reaches disk.
                data = (folder / "prepared.index").read_bytes()
                if Path(state["index"]).read_bytes() != data:
                    raise Refusal("The index changed after the owned lock disappeared")
                current_owned(state)
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                try:
                    info = os.fstat(fd)
                    state["lock_identity"] = [info.st_dev, info.st_ino]
                    remaining = memoryview(data)
                    while remaining:
                        remaining = remaining[os.write(fd, remaining):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                state["lock_digest"] = digest(data)
                lock_matches(state)
                save(folder, state)
            finish_index(folder, state)
        dispose(folder, state)
    else:
        raise Refusal("Choose complete or rollback")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser("commit")
    create.add_argument("--repo", required=True)
    create.add_argument("--ref", required=True)
    create.add_argument("--baseline", required=True)
    create.add_argument("--message", required=True)
    create.add_argument("--file", action="append", nargs=5, required=True,
                        metavar=("PATH", "SHA256", "BASELINE_BLOB", "BASELINE_MODE", "MODE"))
    resume = commands.add_parser("recover")
    resume.add_argument("receipt")
    resume.add_argument("--action", choices=("complete", "rollback", "discard"), default="complete")
    resume.add_argument("--receipt-sha256")
    resume.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "commit":
            print(commit(args.repo, args.ref, args.baseline, args.file, Path(args.message).read_bytes()))
        else:
            recover(args.receipt, args.action, receipt_sha256=args.receipt_sha256, dry_run=args.dry_run)
        return 0
    except (OSError, Refusal, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
