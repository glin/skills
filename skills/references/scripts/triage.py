#!/usr/bin/env python3
"""Bounded triage publication. Participating writers require one coordinator per round.

Digest checks detect observed changes. External writers that ignore the round lock
can still change a file after the final check.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import sys
import tempfile

LIMIT = 1024 * 1024
CALLS = json.loads(Path(__file__).with_name("triage-values.json").read_text(encoding="utf-8"))


def bounded(stream):
    data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("control JSON exceeds 1 MiB")
    return data


def read_bytes(path):
    with Path(path).open("rb") as stream:
        return bounded(stream)


def read_json(path):
    return json.loads(read_bytes(path))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(read_bytes(path)) if Path(path).exists() else "absent"


def encode(value):
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(data) > LIMIT:
        raise ValueError("control JSON exceeds 1 MiB")
    return data


def identity(round_dir, key):
    candidate = read_json(round_dir / "round.json")["candidate"]
    if not isinstance(candidate, str) or not re.fullmatch("[0-9a-f]{40}", candidate):
        raise ValueError("round candidate must be a full commit SHA")
    if not isinstance(key, dict) or not key:
        raise ValueError("key needs reviewer labels")
    result = []
    for label, reviewer in sorted(key.items()):
        if not re.fullmatch("[A-Z]", label):
            raise ValueError("reviewer label must be one capital letter")
        reference = posixpath.normpath(reviewer["report"].replace(chr(92), "/"))
        report = (round_dir / reference).resolve()
        if not report.is_relative_to(round_dir.resolve()):
            raise ValueError("report must remain inside its round")
        hasher = hashlib.sha256()
        with report.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                hasher.update(block)
        result.append({"label": label, "candidate": candidate, "report": reference, "sha256": hasher.hexdigest()})
    return result


def binding(projection):
    return {"version": 1, "reviewers": projection,
            "sha256": digest(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode())}


def validate(payload, key, projection, *, legacy=True, complete=False):
    if not isinstance(payload, dict):
        raise ValueError("triage must be an object")
    if "identity" in payload:
        if payload["identity"] != binding(projection):
            raise ValueError("triage identity binding mismatch. Explicit adoption or re-key is required")
    elif not legacy:
        raise ValueError("legacy triage requires verified identity adoption before updates")
    calls = payload.get("calls")
    if not isinstance(calls, dict) or set(calls) != set(key):
        raise ValueError("calls must name exactly the current reviewer labels")
    for value in calls.values():
        if value is None and not complete:
            continue
        if type(value) is not str or value not in CALLS:
            raise ValueError("calls must be primitive APPROVE, COMMENT or REQUEST_CHANGES strings")
    if payload.get("kind", "first") not in ("first", "followup"):
        raise ValueError("invalid triage kind")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    ids = set()
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("finding must be an object")
        if "id" in item:
            if not isinstance(item["id"], str) or not item["id"].strip() or item["id"] in ids:
                raise ValueError("finding id must be unique text")
            ids.add(item["id"])
        if not isinstance(item.get("file"), str) or not item["file"].strip():
            raise ValueError("finding needs a file")
        if item.get("line") is not None and type(item["line"]) is not int:
            raise ValueError("finding line must be an integer or null")
        if item.get("quote_ok") is not None and type(item["quote_ok"]) is not bool:
            raise ValueError("finding quote_ok must be boolean or null")
        labels = item.get("found_by")
        if not isinstance(labels, list) or not labels or any(type(x) is not str or x not in key for x in labels):
            raise ValueError("finding found_by names an unknown reviewer")
        reported = item.get("reported", {})
        if not isinstance(reported, dict) or any(x not in key for x in reported):
            raise ValueError("finding reported names an unknown reviewer")
        verdict = item.get("verdict")
        if verdict not in ("accepted", "rejected", "dropped", "deferred"):
            raise ValueError("invalid finding verdict")
        severity = item.get("severity")
        if (severity is not None or verdict in ("accepted", "deferred")) and severity not in ("high", "medium", "low"):
            raise ValueError("invalid finding severity")
        if verdict != "accepted" and (not isinstance(item.get("reason"), str) or not item["reason"].strip()):
            raise ValueError("finding needs a reason")
        if item.get("fix_commit") is not None and (type(item["fix_commit"]) is not str or not re.fullmatch("[0-9a-f]{40}", item["fix_commit"])):
            raise ValueError("finding fix_commit must be a full commit SHA or null")
        if "disputes_brief" in item and (type(item["disputes_brief"]) is not bool or (item["disputes_brief"] and verdict != "accepted")):
            raise ValueError("invalid disputes_brief")
    return payload


def publish(path, value, checks):
    data = encode(value)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for source, expected in checks:
            if file_digest(source) != expected:
                raise ValueError(f"control file changed: {source}")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def operate(round_dir, operation, *, expected_key=None, expected_target=None, data=None,
            old_key=None, expected_old=None, verified_identity=None, expected_round=None):
    round_dir = Path(round_dir)
    key_path, target = round_dir / "key.json", round_dir / "triage.json"
    if operation == "check-key":
        return binding(identity(round_dir, data))
    read_checks = [(source, expected) for source, expected in
                   [(key_path, expected_key), (target, expected_target), (round_dir / "round.json", expected_round)]
                   if expected is not None]
    def check_reads():
        for source, expected in read_checks:
            if file_digest(source) != expected:
                raise ValueError(f"control file changed: {source}")
    check_reads()
    key = read_json(key_path)
    projection = identity(round_dir, key)
    if operation == "validate":
        payload = read_json(target)
        result = validate(payload, key, projection, complete=True) if "identity" in payload else payload
        check_reads()
        return result
    if expected_key is None or expected_target is None:
        raise ValueError("mutations require expected key and target digests (or absent)")
    lock = round_dir / ".triage.lock"
    with lock.open("x") as lock_handle:
        try:
            checks = [(key_path, expected_key), (target, expected_target)]
            for source, expected in checks:
                if file_digest(source) != expected:
                    raise ValueError(f"control file changed: {source}")
            pending = round_dir / "rekey" / "previous-key.json"
            if operation == "snapshot":
                if pending.exists():
                    raise ValueError("pending re-key slot already exists. Complete or recover it first")
                payload = read_json(target)
                validate(payload, key, projection, legacy=False)
                pending.parent.mkdir(exist_ok=True)
                snapshot = {"key": key, "key_sha256": expected_key, "key_bytes": base64.b64encode(read_bytes(key_path)).decode(), "identity": binding(projection)}
                publish(pending, snapshot, checks + [(pending, "absent")])
                return snapshot
            if operation == "publish-key":
                snapshot_digest = file_digest(pending)
                snapshot = read_json(pending)
                saved_bytes = base64.b64decode(snapshot["key_bytes"], validate=True)
                if (snapshot["key_sha256"] != expected_key or digest(saved_bytes) != expected_key
                        or json.loads(saved_bytes) != key or snapshot["key"] != key
                        or snapshot["identity"] != binding(projection)):
                    raise ValueError("previous key snapshot does not match the current key")
                identity(round_dir, data)
                publish(key_path, data, checks + [(pending, snapshot_digest)])
                return data
            payload = read_json(target) if target.exists() else None
            if operation == "initialize":
                if payload is not None:
                    return validate(payload, key, projection, legacy=False)
                payload = {"calls": dict.fromkeys(key), "findings": [], "identity": binding(projection)}
            elif operation == "adopt":
                if verified_identity != binding(projection)["sha256"]:
                    raise ValueError("adoption requires the independently verified identity digest")
                validate(payload, key, projection)
                payload["identity"] = binding(projection)
            elif operation == "rekey":
                if old_key is None or expected_old is None:
                    raise ValueError("re-key requires old key snapshot and its expected digest")
                old_key = Path(old_key)
                if old_key.resolve() != pending.resolve():
                    raise ValueError("re-key must use the round's pending previous key")
                if file_digest(old_key) != expected_old:
                    raise ValueError("previous key snapshot changed")
                snapshot = read_json(old_key)
                saved_bytes = base64.b64decode(snapshot["key_bytes"], validate=True)
                if digest(saved_bytes) != snapshot["key_sha256"] or json.loads(saved_bytes) != snapshot["key"]:
                    raise ValueError("previous key bytes differ from their saved digest")
                previous = snapshot["identity"]["reviewers"]
                if snapshot["identity"] != binding(previous):
                    raise ValueError("previous identity digest differs")
                if {x["candidate"] for x in previous} != {x["candidate"] for x in projection}:
                    raise ValueError("re-key must remain in the same reviewed candidate")
                if payload.get("identity") == binding(projection):
                    validate(payload, key, projection, legacy=False)
                    pending.unlink()
                    return payload
                validate(payload, snapshot["key"], previous, legacy=False)
                def stable(entry):
                    return (entry["candidate"], entry["report"], entry["sha256"])
                old_ids = [stable(x) for x in previous]
                new_ids = [stable(x) for x in projection]
                if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
                    raise ValueError("ambiguous duplicate reviewer identity")
                remap = {x["label"]: projection[new_ids.index(stable(x))]["label"] for x in previous if stable(x) in new_ids}
                updated = copy.deepcopy(payload)
                updated["calls"] = dict.fromkeys(key)
                for label, call in payload["calls"].items():
                    if label in remap:
                        updated["calls"][remap[label]] = call
                for finding in updated["findings"]:
                    if any(x not in remap for x in finding["found_by"] + list(finding.get("reported", {}))):
                        raise ValueError("re-key would orphan a finding")
                    finding["found_by"] = [remap[x] for x in finding["found_by"]]
                    finding["reported"] = {remap[x]: v for x, v in finding.get("reported", {}).items()}
                updated["identity"] = binding(projection)
                payload = updated
                checks.append((old_key, expected_old))
            else:
                validate(payload, key, projection, legacy=False)
                if operation == "calls":
                    if not isinstance(data, dict) or any(x not in key for x in data):
                        raise ValueError("call update names an unknown reviewer")
                    payload["calls"].update(data)
                elif operation == "findings":
                    payload["findings"] = data
                else:
                    raise ValueError("unknown triage operation")
            validate(payload, key, projection, legacy=False)
            publish(target, payload, checks)
            if operation == "rekey":
                validate(read_json(target), key, projection, legacy=False)
                if file_digest(pending) != expected_old:
                    raise ValueError("previous key changed after publication. Preserve recovery slot")
                pending.unlink()
            return payload
        finally:
            lock_handle.close()
            lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog="initialize creates null calls. calls updates a subset. findings replaces decisions while preserving calls. adopt requires a verified identity digest. snapshot reserves previous-key.json. publish-key requires a matching previous-key snapshot and preserves triage. rekey remaps stable report identities and clears the pending slot after validation. validate requires complete calls and accepts expected file digests. check-key validates a proposed key on stdin. identity prints the immutable projection. Updates require expected file digests, independently of identity.")
    parser.add_argument("operation", choices=["initialize", "calls", "findings", "adopt", "rekey", "snapshot", "publish-key", "validate", "identity", "check-key"])
    parser.add_argument("round", type=Path)
    parser.add_argument("--expected-key")
    parser.add_argument("--expected-target")
    parser.add_argument("--expected-round")
    parser.add_argument("--old-key", type=Path)
    parser.add_argument("--expected-old")
    parser.add_argument("--verified-identity")
    parser.add_argument("--call", action="append", default=[], metavar="LABEL=CALL", help="scalar partial call update, null clears a call")
    args = parser.parse_args()
    try:
        if args.operation == "identity":
            result = binding(identity(args.round, read_json(args.round / "key.json")))
        else:
            data = None
            if args.operation in ("calls", "findings", "publish-key", "check-key"):
                if args.call:
                    if args.operation != "calls":
                        raise ValueError("--call requires calls operation")
                    data = dict(x.split("=", 1) for x in args.call)
                    data = {k: None if v == "null" else v for k, v in data.items()}
                else:
                    data = json.loads(bounded(sys.stdin.buffer))
            result = operate(args.round, args.operation, expected_key=args.expected_key, expected_target=args.expected_target,
                             data=data, old_key=args.old_key, expected_old=args.expected_old, verified_identity=args.verified_identity,
                             expected_round=args.expected_round)
        if args.operation == "validate":
            # Transport preserves bounded legacy input without publication formatting expansion.
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8", "backslashreplace"))
        else:
            sys.stdout.buffer.write(encode(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"triage: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
