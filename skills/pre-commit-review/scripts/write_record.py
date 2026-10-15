#!/usr/bin/env python3
"""Write a review run record from reports and the coordinator's triage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
RUN_ID = re.compile(r"[0-9]{6}-[0-9a-f]{16}\Z")
PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CALLS = {"APPROVE", "COMMENT", "REQUEST_CHANGES"}
VERDICTS = {"accepted", "rejected", "dropped", "deferred"}
SEVERITIES = {"high", "medium", "low"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
COUNTERS = ("cost_usd", "minutes", "requests", "output_tokens", "thinking_tokens", "tool_calls", "shell_calls")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def native_path(value: str | Path) -> Path:
    text = str(value)
    if os.name == "nt":
        text = re.sub(r"^/([a-zA-Z])/(?=.)", r"\1:/", text)
    return Path(text)


def text_value(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def optional_bool(value: object) -> bool:
    return value is None or type(value) is bool


def git(directory: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(directory: Path, value: str, field: str) -> str:
    if not isinstance(value, str) or not FULL_SHA.fullmatch(value):
        raise ValueError(f"{field} must be a full commit SHA")
    resolved = git(directory, "rev-parse", "--verify", f"{value}^{{commit}}")
    if resolved != value:
        raise ValueError(f"{field} does not name an immutable commit")
    return value


def report_path(round_dir: Path, name: str) -> Path:
    path = (round_dir / name).resolve()
    if not path.is_relative_to(round_dir.resolve()) or not path.is_file():
        raise ValueError(f"review report is missing from {round_dir}: {name}")
    return path


def build(run_dir: Path, out: Path | None) -> tuple[dict, Path]:
    meta = read_json(run_dir / "run.json")
    repository = native_path(meta["repository"])
    if not repository.is_absolute():
        raise ValueError("repository must be an absolute path")
    repo = repository.resolve()
    base = commit(repo, meta["base"], "base")
    candidate = commit(repo, meta["candidate"], "candidate")
    if not DATE.fullmatch(meta["date"]):
        raise ValueError("date must be YYYY-MM-DD")
    if not PROJECT.fullmatch(meta["project"]):
        raise ValueError("project must be safe for a file name")
    if not RUN_ID.fullmatch(meta["run_id"]):
        raise ValueError("run_id must be a UTC time followed by 16 lowercase hexadecimal digits")
    if not isinstance(meta["judge"].get("model"), str) or not meta["judge"]["model"] or not isinstance(meta["judge"].get("blind"), bool):
        raise ValueError("judge needs a model and a blind true or false value")
    expected = f'{meta["date"]}-{meta["project"].lower()}-{candidate[:8]}-{meta["run_id"]}.json'
    repository_in_record = str(repo)
    if out is None:
        out = repo / ".claude" / "review-records" / expected
        repository_in_record = Path(os.path.relpath(repo, out.parent)).as_posix()
    if out.name != expected:
        raise ValueError(f"record file name must be {expected}")
    if meta.get("landed") is not None:
        commit(repo, meta["landed"], "landed")

    round_dirs = sorted((p for p in run_dir.iterdir() if re.fullmatch(r"round-[0-9]+", p.name)), key=lambda p: int(p.name[6:]))
    if not round_dirs:
        raise ValueError("run has no review rounds")
    rounds = []
    for number, directory in enumerate(round_dirs, 1):
        if directory.name != f"round-{number}":
            raise ValueError("review rounds must be consecutive")
        reviewed = read_json(directory / "round.json")
        round_base = commit(repo, reviewed["base"], f"round {number} base")
        round_candidate = commit(repo, reviewed["candidate"], f"round {number} candidate")
        if number == 1 and (round_base, round_candidate) != (base, candidate):
            raise ValueError("first round must match the run's base and candidate")
        key = read_json(directory / "key.json")
        triage = read_json(directory / "triage.json")
        if meta["judge"]["blind"] and any(details.get("blinded") is not True for details in key.values()):
            raise ValueError("named triage cannot claim a blind judge")
        reviewers = []
        for label, details in key.items():
            if not re.fullmatch(r"[A-Z]", label):
                raise ValueError(f"reviewer label must be one capital letter: {label}")
            report = report_path(directory, details["report"])
            call = report.read_text(encoding="utf-8").strip().splitlines()[-1]
            if call not in CALLS or triage["calls"].get(label) != call:
                raise ValueError(f"reviewer {label} call differs from its report or triage")
            if not all(details.get(field) for field in ("model", "effort", "harness")):
                raise ValueError(f"reviewer {label} needs a model, effort and harness")
            if details["effort"] not in EFFORTS or not optional_bool(details.get("shell_denied")):
                raise ValueError(f"reviewer {label} has an invalid effort or shell_denied value")
            reviewer = {"label": label, "model": details["model"], "effort": details["effort"],
                        "harness": details["harness"], "session": details.get("session", details.get("session_id")),
                        "call": call, "shell_denied": details.get("shell_denied")}
            if details.get("agent_id") and reviewer["session"]:
                reviewer["session"] += "/" + details["agent_id"]
            for field in COUNTERS:
                value = details.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                    raise ValueError(f"reviewer {label} {field} must be a nonnegative number or null")
                reviewer[field] = value
            reviewers.append(reviewer)
        if not reviewers:
            raise ValueError(f"round {number} has no reviewers")
        findings = []
        seen_ids = {finding["id"] for round_data in rounds for finding in round_data["findings"]}
        for index, finding in enumerate(triage["findings"], 1):
            item = {"id": finding.get("id", f"r{number}-f{index}"), "found_by": finding["found_by"],
                    "file": finding["file"], "line": finding.get("line"), "quote_ok": finding.get("quote_ok"),
                    "reported": finding.get("reported", {}), "severity": finding.get("severity"),
                    "verdict": finding["verdict"], "reason": finding.get("reason", ""),
                    "fix_commit": finding.get("fix_commit")}
            if "disputes_brief" in finding:
                item["disputes_brief"] = finding["disputes_brief"]
            if item["id"] in seen_ids:
                raise ValueError(f'{item["id"]} is used twice')
            seen_ids.add(item["id"])
            if not text_value(item["file"]):
                raise ValueError(f'{item["id"]} needs a file')
            if item["line"] is not None and (isinstance(item["line"], bool) or not isinstance(item["line"], int)):
                raise ValueError(f'{item["id"]} needs a line number or null')
            if not optional_bool(item["quote_ok"]):
                raise ValueError(f'{item["id"]} has an invalid quote check')
            if (not isinstance(item["found_by"], list) or not item["found_by"] or
                    any(not isinstance(label, str) or label not in key for label in item["found_by"])):
                raise ValueError(f'{item["id"]} names an unknown reviewer')
            if (not isinstance(item["reported"], dict) or
                    any(label not in key for label in item["reported"])):
                raise ValueError(f'{item["id"]} reports an unknown reviewer')
            if item["verdict"] not in VERDICTS:
                raise ValueError(f'{item["id"]} has an invalid verdict')
            if item["verdict"] in {"accepted", "deferred"} and item["severity"] not in SEVERITIES:
                raise ValueError(f'{item["id"]} needs a severity')
            if item["severity"] is not None and item["severity"] not in SEVERITIES:
                raise ValueError(f'{item["id"]} has an invalid severity')
            if item["verdict"] != "accepted" and not text_value(item["reason"]):
                raise ValueError(f'{item["id"]} needs a reason')
            if "disputes_brief" in item and (not isinstance(item["disputes_brief"], bool) or
                                              (item["disputes_brief"] and item["verdict"] != "accepted")):
                raise ValueError(f'{item["id"]} has an invalid disputes_brief value')
            if item["fix_commit"] is not None:
                commit(repo, item["fix_commit"], f'{item["id"]} fix_commit')
            if meta.get("landed") is not None and item["verdict"] == "accepted":
                commit(repo, item["fix_commit"], f'{item["id"]} fix_commit')
            findings.append(item)
        rounds.append({"round": number, "kind": "first" if number == 1 else "followup",
                       "base": round_base, "candidate": round_candidate, "reviewers": reviewers, "findings": findings})

    prompt = round_dirs[0] / "prompt.md"
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest() if prompt.is_file() else None
    skill_version = meta.get("skill_version")
    if skill_version is None:
        skill_dir = Path(__file__).resolve().parent.parent
        try:
            skill_version = git(skill_dir, "log", "-1", "--format=%H", "--", "SKILL.md")
        except subprocess.CalledProcessError as error:
            raise ValueError("skill_version needs the source skill's full commit SHA") from error
    if not isinstance(skill_version, str) or not FULL_SHA.fullmatch(skill_version):
        raise ValueError("skill_version must be the source skill's full commit SHA")
    record = {"schema": 2, "date": meta["date"], "run_id": meta["run_id"], "skill_version": skill_version,
            "project": meta["project"], "repository": repository_in_record, "source": "pre-commit-review",
            "plan": meta.get("plan"), "base": base, "candidate": candidate, "landed": meta.get("landed"),
            "prompt_sha256": prompt_hash, "judge": meta["judge"], "rounds": rounds}
    validate_record(record)
    return record, out


def validate_record(record: dict) -> None:
    if record["schema"] != 2 or record["source"] != "pre-commit-review":
        raise ValueError("record needs schema 2 and a pre-commit-review source")
    if (not DATE.fullmatch(record["date"]) or not PROJECT.fullmatch(record["project"]) or
            not RUN_ID.fullmatch(record["run_id"]) or not text_value(record["repository"]) or
            not FULL_SHA.fullmatch(record["skill_version"])):
        raise ValueError("record has invalid identity fields")
    if record["plan"] is not None and not text_value(record["plan"]):
        raise ValueError("plan must be text or null")
    for field in ("base", "candidate"):
        if not FULL_SHA.fullmatch(record[field]):
            raise ValueError(f"{field} must be a full commit SHA")
    if record["landed"] is not None and not FULL_SHA.fullmatch(record["landed"]):
        raise ValueError("landed must be null or a full commit SHA")
    if record["prompt_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", record["prompt_sha256"]):
        raise ValueError("prompt_sha256 must be null or a SHA-256 digest")
    if not text_value(record["judge"]["model"]) or type(record["judge"]["blind"]) is not bool:
        raise ValueError("judge needs a model and a boolean blind value")
    if not record["rounds"]:
        raise ValueError("record needs a review round")
    ids = set()
    for number, round_data in enumerate(record["rounds"], 1):
        if (round_data["round"] != number or round_data["kind"] != ("first" if number == 1 else "followup") or
                not FULL_SHA.fullmatch(round_data["base"]) or not FULL_SHA.fullmatch(round_data["candidate"])):
            raise ValueError(f"round {number} has invalid identity fields")
        if number == 1 and (round_data["base"], round_data["candidate"]) != (record["base"], record["candidate"]):
            raise ValueError("first round does not match the record")
        if not round_data["reviewers"] or not isinstance(round_data["findings"], list):
            raise ValueError(f"round {number} needs reviewers and a findings list")
        labels = set()
        for reviewer in round_data["reviewers"]:
            if not isinstance(reviewer["label"], str) or not re.fullmatch(r"[A-Z]", reviewer["label"]) or reviewer["label"] in labels:
                raise ValueError("reviewer labels must be unique capital letters")
            labels.add(reviewer["label"])
            if not all(text_value(reviewer[field]) for field in ("model", "harness")):
                raise ValueError("reviewer needs a model and harness")
            if reviewer["effort"] not in EFFORTS or reviewer["call"] not in CALLS:
                raise ValueError("reviewer needs a valid effort and call")
            if reviewer["session"] is not None and not text_value(reviewer["session"]):
                raise ValueError("session must be text or null")
            if not optional_bool(reviewer["shell_denied"]):
                raise ValueError("shell_denied must be true, false or null")
            for field in COUNTERS:
                value = reviewer[field]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                          not math.isfinite(value) or value < 0):
                    raise ValueError(f"reviewer {field} must be a nonnegative number or null")
        for finding in round_data["findings"]:
            if not text_value(finding["id"]) or finding["id"] in ids:
                raise ValueError("finding needs a unique id")
            ids.add(finding["id"])
            if (not isinstance(finding["found_by"], list) or not finding["found_by"] or
                    any(not isinstance(label, str) or label not in labels for label in finding["found_by"])):
                raise ValueError(f'{finding["id"]} found_by must be a nonempty list')
            if not isinstance(finding["reported"], dict) or any(label not in labels for label in finding["reported"]):
                raise ValueError(f'{finding["id"]} reported names an unknown reviewer')
            if not text_value(finding["file"]) or (finding["line"] is not None and type(finding["line"]) is not int):
                raise ValueError(f'{finding["id"]} needs a file and line number or null')
            if not optional_bool(finding["quote_ok"]):
                raise ValueError(f'{finding["id"]} quote_ok must be true, false or null')
            if finding["verdict"] not in VERDICTS:
                raise ValueError(f'{finding["id"]} has an invalid verdict')
            if finding["severity"] is not None and finding["severity"] not in SEVERITIES:
                raise ValueError(f'{finding["id"]} has an invalid severity')
            if finding["verdict"] in {"accepted", "deferred"} and finding["severity"] is None:
                raise ValueError(f'{finding["id"]} needs a severity')
            if finding["verdict"] != "accepted" and not text_value(finding["reason"]):
                raise ValueError(f'{finding["id"]} needs a reason')
            if finding["fix_commit"] is not None and not FULL_SHA.fullmatch(finding["fix_commit"]):
                raise ValueError(f'{finding["id"]} has an invalid fix_commit')
            if record["landed"] is not None and finding["verdict"] == "accepted" and finding["fix_commit"] is None:
                raise ValueError(f'{finding["id"]} needs a fix_commit')
            if "disputes_brief" in finding and (type(finding["disputes_brief"]) is not bool or
                                               (finding["disputes_brief"] and finding["verdict"] != "accepted")):
                raise ValueError(f'{finding["id"]} has an invalid disputes_brief value')


def publish(out: Path, payload: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=f".{out.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, out)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=native_path)
    parser.add_argument("--out", type=native_path)
    args = parser.parse_args()
    try:
        record, out = build(args.run_dir, args.out)
        if out.exists():
            raise ValueError(f"record already exists: {out}")
        publish(out, json.dumps(record, indent=2) + "\n")
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.CalledProcessError) as error:
        print(f"write_record: {error}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
