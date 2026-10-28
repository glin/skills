#!/usr/bin/env python3
"""Decide whether a coordinator continues past a numeric limit, and print its counters.

The verdict covers numeric limits only: spend against the ceiling and the absolute stop,
admission of a paid launch, unpriced usage, and the round rules for a fix round. Request and
context counts are checkpoints, so the script reports them and never stops on them. Every other
stop condition in the calling workflow still binds.

The cost source is the Arc report from session-cost-audit's arc_cost.py, schema version 2 or 3.
Pass --cost-json to read a saved report instead of running arc_cost.py.

The cache_expiry line reports the report's combined cacheExpiry.extraCostAfterExpiryUsd: what
requests that returned after their prompt cache expired paid to resend the previous context,
beyond a cache read of the same tokens. It reads unknown when the report lacks that figure or its
coverage is incomplete. An unknown premium still lists the returns, their cache lifetimes, the
longest gap and the reason the premium is unpriced. Dispatched sources carry no cache-expiry
figure, so the line counts the Arc report's sessions only. The line ends with flag: true when the
premium passes 2 USD or 10 percent of the run's priced spend, flag: false below both, and flag:
unknown when the premium is unknown.

A request count that Arc marks as not exact prints with its qualifier: identified bills or
observed snapshots. The request counter itself stays unavailable, so a ledger figure never takes
a partial count. When usage is unpriced, a coverage detail line names each summary that is not
reconciled, where its unresolved interval starts and why, any unpriced models, and the arc_cost.py
command that reproduces the report. A child without a coverage class counts as complete only when
its request count is exact and its cost is priced. Otherwise the line names it as coverage unknown.
These display lines never change the verdict.

An empty window that Arc reports without coverage fields and without cost fields prices at 0 USD
with complete coverage. An empty window with a coverage defect stays unpriced.

Exit 0 means continue, 1 means stop, and 2 means an error. On exit 2 the counter block still
prints, and each figure the script cannot read shows as unavailable.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

SCHEMA_VERSIONS = (2, 3)
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
# A rebuild premium above either threshold sets flag: true on the cache_expiry line.
EXPIRY_FLAG_USD = 2.0
EXPIRY_FLAG_SHARE = 0.10
# Arc coverage classes whose request counts are bills with known response IDs.
IDENTIFIED_COVERAGE = ("recorded-reconciled", "recorded-gaps", "billed-with-unknown-coverage")
LEGACY_COVERAGE = "legacy-snapshots"
OPENING_FILE = "stop-check-opening.json"
BLOCK_FILE = "stop-check.txt"
RECOVERY_FILE = "snapshot-recovery.json"
OPERATIONS = (
    "checkpoint",
    "fix-round",
    "re-review",
    "carry-review",
    "supplemental-review",
    "rebase",
    "landing",
)
ROUND_KINDS = ("review", "correction", "carry")

RULE_ABSOLUTE = "absolute stop reached"
RULE_ADMISSION = "admission would pass the absolute stop"
RULE_UNPRICED = "unpriced usage blocks paid work"
RULE_DIAGNOSE = "diagnose"
RULE_NOT_CONVERGING = "not converging"
RULE_CEILING = "ceiling before a fix round that is not converging"


class CostError(Exception):
    """A cost source is unusable, or the run folder refused a write."""


def money(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text} is negative. A dollar amount is 0 or more.")
    return value


def tokens(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text} is negative. A token count is 0 or more.")
    return value


def review_round(text: str) -> tuple[str, int, int]:
    """Parse KIND:FINDINGS[:REGRESSIONS], such as correction:2:1."""
    parts = text.split(":")
    if len(parts) not in (2, 3) or parts[0] not in ROUND_KINDS:
        raise argparse.ArgumentTypeError(
            f"{text} is not KIND:FINDINGS[:REGRESSIONS] with KIND one of {', '.join(ROUND_KINDS)}"
        )
    try:
        findings = int(parts[1])
        regressions = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text} holds a count that is not a whole number") from None
    if findings < 0 or regressions < 0 or regressions > findings:
        raise argparse.ArgumentTypeError(
            f"{text} needs 0 or more findings and no more regressions than findings"
        )
    return parts[0], findings, regressions


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_argument_group("cost source")
    source.add_argument("--session", help="Coordinator session id, priced with arc_cost.py.")
    source.add_argument("--since", help="Run start in UTC, passed to arc_cost.py as --since.")
    source.add_argument("--until", help="Pricing end in UTC, passed to arc_cost.py as --until.")
    source.add_argument("--arc-cost", help="Path to arc_cost.py. Defaults to the session-cost-audit skill beside this one.")
    source.add_argument("--cost-json", help="Read a saved Arc cost report instead of running arc_cost.py.")
    source.add_argument("--dispatched-cost", help="dispatched-cost.json from the run folder.")
    source.add_argument("--snapshot-recovery", choices=("first", "observe", "retry"), help="Retain evidence, observe a named request, or retry the frozen window with automatic request discovery.")
    source.add_argument("--recovery-request", help="Completed Codex response ID after the first observation.")
    source.add_argument("--no-worker-launches", action="store_true", help="Confirm no worker launched between observations.")
    source.add_argument("--recovery-commitments", help="JSON dollar bounds for recovery, coordinator, check, verification, workers and close.")
    source.add_argument("--post-cutoff-reserve", type=money, default=0.0, help="Conservative reserve for all work after the frozen cutoff.")
    limits = parser.add_argument_group("limits")
    limits.add_argument("--ceiling", type=money, required=True, help="Route ceiling in USD.")
    limits.add_argument("--absolute-stop", type=money, help="Absolute stop in USD. Defaults to twice the ceiling.")
    limits.add_argument("--earlier-spend", type=money, default=0.0, help="Stopped spend of earlier runs on this phase, in USD.")
    limits.add_argument("--opening-context", type=tokens, help="Coordinator context at Brief, in tokens.")
    limits.add_argument("--run-folder", help=f"Run folder. The first call saves the opening context in {OPENING_FILE}. Every call appends its block to {BLOCK_FILE}.")
    admission = parser.add_argument_group("admission")
    admission.add_argument("--outstanding", type=money, action="append", default=[], help="Allowance of one running worker, in USD. Repeat per worker.")
    admission.add_argument("--reserve", type=money, default=0.0, help="Reserve for required verification and Close, in USD.")
    admission.add_argument("--launch-allowance", type=money, help="Allowance of the paid launch about to start, in USD.")
    admission.add_argument("--unpriced-allowance-left", type=money, help="What remains of the bounded allowance the author granted for paid work while usage is unpriced, in USD. The coordinator subtracts each admitted launch from it.")
    rounds = parser.add_argument_group("rounds")
    rounds.add_argument("--operation", choices=OPERATIONS, default="checkpoint", help="The step about to run.")
    rounds.add_argument("--round", type=review_round, action="append", default=[], dest="rounds", metavar="KIND:FINDINGS[:REGRESSIONS]", help="One review round in order. KIND is review, correction or carry. REGRESSIONS counts accepted findings the preceding correction caused.")
    rounds.add_argument("--continuing-authorization", action="store_true", help="The author granted continued or unlimited rounds.")
    rounds.add_argument("--replan-done", action="store_true", help="A replan or diagnosis was completed after the latest round stop.")
    parser.add_argument("--json", action="store_true", help="Print one JSON object instead of text.")
    arguments = parser.parse_args(argv)
    if arguments.cost_json is None and not (arguments.session and arguments.since):
        parser.error("pass --cost-json, or --session and --since")
    if arguments.absolute_stop is None:
        arguments.absolute_stop = 2 * arguments.ceiling
    return arguments


def default_arc_cost() -> Path:
    # Take the skills directory from the text of this path, so a linked skill folder stays put.
    script = Path(os.path.abspath(__file__))
    return script.parent.parent.parent / "session-cost-audit" / "scripts" / "arc_cost.py"


def load_report(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.cost_json is not None:
        try:
            text = Path(arguments.cost_json).read_text(encoding="utf-8")
        except OSError as error:
            raise CostError(f"cannot read {arguments.cost_json}: {error.strerror}") from None
    else:
        script = Path(arguments.arc_cost) if arguments.arc_cost else default_arc_cost()
        command = [sys.executable, str(script), arguments.session, "--since", arguments.since]
        if arguments.until:
            command += ["--until", arguments.until]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
        except OSError as error:
            raise CostError(f"cannot run {script}: {error.strerror}") from None
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise CostError(f"arc_cost.py exited {result.returncode}: {detail[0] if detail else 'no output'}")
        text = result.stdout
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        raise CostError("the cost report is not JSON") from None
    if not isinstance(report, dict) or report.get("schemaVersion") not in SCHEMA_VERSIONS:
        raise CostError(f"the cost report is not Arc cost schema version 2 or 3")
    return report


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def owned_sources(report: dict[str, Any]) -> set[str]:
    owned = set()
    source = report.get("source")
    if isinstance(source, dict) and source.get("sessionId"):
        owned.add(str(source["sessionId"]))
    for child in report.get("children") or []:
        if isinstance(child, dict) and child.get("sessionId"):
            owned.add(str(child["sessionId"]))
    return owned


def moment(value: Any) -> datetime | str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)


def same_end(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return moment(left) == moment(right)


def load_dispatched(path: str, owned: set[str], window_end: Any, window_start: Any = None, strict: bool = False,
                    retained: list[dict] | None = None) -> tuple[float, bool, bool]:
    """Return the priced sum of unowned dispatched sources, whether any source is unpriced, and
    whether every unowned source has a priced figure."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise CostError(f"cannot read {path}: {error.strerror}") from None
    except json.JSONDecodeError:
        raise CostError(f"{path} is not JSON") from None
    entries = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise CostError(f"{path} holds no list of sources")
    total = 0.0
    unpriced = False
    every_priced = True
    seen = set(owned)
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("sessionId"):
            raise CostError(f"{path} holds a source without a sessionId")
        if ("since" in entry and not same_end(entry["since"], window_start)) or (strict and "since" not in entry):
            raise CostError(f"{path} prices {entry['sessionId']} from a different window start")
        if strict and "until" not in entry:
            raise CostError(f"{path} omits the window end for {entry['sessionId']}")
        if "until" in entry and not same_end(entry["until"], window_end):
            raise CostError(
                f"{path} prices {entry['sessionId']} to {entry['until']}, but the cost report ends at {window_end}"
            )
        source = str(entry["sessionId"])
        if source in seen:
            continue
        seen.add(source)
        if retained is not None:
            retained.append(dict(entry))
        if entry.get("costUsd") is None or entry.get("requestCountExact") is False:
            unpriced = True
        priced = number(entry.get("pricedCostUsd"))
        if priced is None:
            unpriced = True
            every_priced = False
            continue
        total += priced
    return total, unpriced, every_priced


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recovery_sources(report: dict[str, Any], previous: list[dict] | None = None) -> list[dict]:
    """Keep source bytes and selected bill identities separate from Arc's monetary totals."""
    sources = [dict(report["parent"], **report["source"]), *(report.get("children") or [])]
    result = []
    for index, source in enumerate(sources):
        kind = source.get("kind")
        claude_child = index > 0 and kind in ("claude-subagent", "claude-session")
        if (kind not in ("codex", "codex-thread") and not claude_child) or not source.get("file") or not source.get("sessionId"):
            raise CostError("snapshot recovery requires identified Codex sources or complete Claude child files")
        path = Path(source["file"])
        stat = path.stat()
        raw = path.read_bytes()
        saved = previous[index] if previous and index < len(previous) else None
        if saved and (saved.get("kind") != kind or saved["sessionId"] != source["sessionId"] or saved["file"] != str(path)
                      or saved["identity"] != [stat.st_dev, stat.st_ino] or len(raw) < saved["size"]
                      or hashlib.sha256(raw[:saved["size"]]).hexdigest() != saved["sha256"]):
            raise CostError("snapshot recovery source was replaced or rewritten")
        if claude_child:
            if (child_doubts(source) or source.get("coverage") not in (None, "recorded-reconciled")
                    or source.get("coverageDefect") or whole(source.get("requests")) is None
                    or whole(source.get("pricedRequests")) != source["requests"]
                    or source.get("unpricedRequests") != 0 or source.get("unpricedModels")
                    or number(source.get("pricedCostUsd")) is None or source["pricedCostUsd"] < 0
                    or number(source.get("costUsd")) != number(source.get("pricedCostUsd"))):
                raise CostError("snapshot recovery requires complete priced Claude children")
            if saved and (len(raw) != saved["size"] or source != saved.get("summary")):
                raise CostError("snapshot recovery completed Claude child changed")
            result.append({"kind": kind, "sessionId": source["sessionId"], "file": str(path),
                           "identity": [stat.st_dev, stat.st_ino], "size": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest(), "summary": source, "bills": {}, "later": {}})
            continue
        bills = {}
        later = {}
        for line in raw.splitlines():
            row = json.loads(line)
            if row.get("type") != "token_usage_record":
                continue
            payload = row.get("payload") or {}
            key = payload.get("response_id")
            if not key or payload.get("thread_id") != source["sessionId"]:
                continue
            stamp = moment(row.get("timestamp"))
            if not isinstance(stamp, datetime):
                raise CostError("snapshot recovery bill has no timestamp")
            value = {"timestamp": row["timestamp"], "payload": payload}
            if moment(report["window"]["since"]) <= stamp < moment(report["window"]["until"]):
                if key in bills and bills[key] != value:
                    raise CostError("snapshot recovery has contradictory request evidence")
                bills[key] = value
            else:
                later[key] = value
        if len(bills) != whole(source.get("requests")):
            raise CostError("snapshot recovery cannot identify every selected request")
        result.append({"kind": kind, "sessionId": source["sessionId"], "file": str(path), "identity": [stat.st_dev, stat.st_ino],
                       "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "bills": bills, "later": later})
    if previous and len(previous) != len(result):
        raise CostError("snapshot recovery source set changed")
    return result


def save_recovery(arguments: argparse.Namespace, state: dict, error: str | None = None) -> None:
    if error:
        state["observations"][-1]["error"] = error
    path = Path(arguments.run_folder) / RECOVERY_FILE
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def begin_recovery(arguments: argparse.Namespace) -> dict | None:
    """Consume the later observation before reading any report or running the adapter."""
    if not arguments.snapshot_recovery:
        if arguments.run_folder and arguments.cost_json is None and not arguments.until:
            if (Path(arguments.run_folder) / RECOVERY_FILE).exists():
                arguments.snapshot_recovery = "retry"
            else:
                arguments.until = moment(now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if not arguments.snapshot_recovery:
            return None
    if not arguments.run_folder:
        raise CostError("snapshot recovery requires a run folder")
    path = Path(arguments.run_folder) / RECOVERY_FILE
    if arguments.snapshot_recovery == "retry":
        state = json.loads(path.read_text(encoding="utf-8"))
        if len(state.get("observations", [])) != 1:
            raise CostError("snapshot recovery already used its later observation")
        if not state.get("firstComplete") or state.get("recoveryUnavailable"):
            raise CostError("snapshot recovery lacks a usable first observation")
        first = state["observations"][0]
        old = first["report"]
        for key in ("since", "until"):
            value = getattr(arguments, key)
            if value is not None and not same_end(value, old["window"][key]):
                raise CostError("snapshot recovery moved the frozen window")
            setattr(arguments, key, old["window"][key])
        if arguments.session and arguments.session != old["source"]["sessionId"]:
            raise CostError("snapshot recovery coordinator changed")
        if not arguments.no_worker_launches:
            raise CostError("snapshot recovery requires no intervening worker launches")
        sources = recovery_sources(old, first["sources"])
        candidates = [key for key, bill in sources[0]["later"].items()
                      if key not in first["sources"][0]["later"]
                      and moment(bill["timestamp"]) > moment(first["observedAt"])]
        if not candidates:
            raise CostError("snapshot recovery pending: complete a useful coordinator request without worker launches, then retry the same command")
        arguments.recovery_request = candidates[0]
        return state
    if arguments.snapshot_recovery == "first":
        if path.exists():
            raise CostError("snapshot recovery already retained its first observation")
        if not all(isinstance(moment(getattr(arguments, key)), datetime) for key in ("since", "until")):
            raise CostError("snapshot recovery requires a frozen start and end before the first check")
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if len(state.get("observations", [])) != 1:
        raise CostError("snapshot recovery already used its later observation")
    state["observations"].append({"observedAt": now(), "error": "the observation did not return usable evidence"})
    save_recovery(arguments, state)
    return state


def recovery_pair(old: dict, report: dict, previous: list[dict]) -> list[dict]:
    """Check the same selected bills and prices while allowing appended snapshot evidence."""
    if report.get("source") != old.get("source") or report.get("buildCommit") != old.get("buildCommit"):
        raise CostError("snapshot recovery source or Arc build changed")
    if not all(same_end(report["window"].get(key), old["window"].get(key)) for key in ("since", "until")):
        raise CostError("snapshot recovery moved the frozen window")
    sources = recovery_sources(report, previous)
    if not previous or [row["bills"] for row in sources] != [row["bills"] for row in previous]:
        raise CostError("snapshot recovery selected requests changed")
    for index, (before, after) in enumerate(zip([old["parent"], *old.get("children", []), old["combined"]],
                                               [report["parent"], *report.get("children", []), report["combined"]], strict=True)):
        if 0 < index < len(sources) and sources[index]["kind"] in ("claude-subagent", "claude-session"):
            continue
        if any(before.get(key) != after.get(key) for key in ("requests", "pricedCostUsd", "unpricedRequests", "unpricedModels")):
            raise CostError("snapshot recovery request counts or priced dollars changed")
        defect = before.get("coverageDefect") or {}
        if defect and defect.get("cause") != "snapshot-unmatched":
            raise CostError("snapshot recovery cannot resolve an earlier genuine coverage gap")
        if empty_and_complete(before) and not defect:
            if not empty_and_complete(after) or after.get("coverageDefect"):
                raise CostError("snapshot recovery empty source changed")
            continue
        if (before is not old["combined"] and before.get("coverage") != "recorded-reconciled"
                and (before.get("coverage") != "billed-with-unknown-coverage" or defect.get("cause") != "snapshot-unmatched")):
            raise CostError("snapshot recovery cannot resolve an earlier genuine coverage gap")
        if (after.get("coverage") != "recorded-reconciled" or after.get("requestCountExact") is False
                or number(after.get("pricedCostUsd")) is None
                or number(after.get("costUsd")) != number(after.get("pricedCostUsd"))
                or after.get("unpricedRequests") != 0 or after.get("unpricedModels") or after.get("coverageDefect")):
            raise CostError("snapshot recovery still has unresolved coverage or prices")
    return sources


def recovery_dispatched(arguments: argparse.Namespace, report: dict, observation: dict,
                        previous: list[dict] | None = None) -> tuple[float, bool, bool]:
    """Retain each supplemental source's Arc report and the evidence that owns its selected bills."""
    entries: list[dict] = []
    totals = (0.0, False, True)
    if arguments.dispatched_cost:
        totals = load_dispatched(arguments.dispatched_cost, owned_sources(report), report["window"]["until"],
                                 report["window"]["since"], True, entries)
    entries.sort(key=lambda entry: str(entry["sessionId"]))
    retained: list[dict] = []
    observation["supplemental"] = retained
    old = {item["entry"]["sessionId"]: item for item in previous or []}
    if previous is not None and [entry["sessionId"] for entry in entries] != sorted(old):
        raise CostError("snapshot recovery supplemental source ownership changed")
    for entry in entries:
        if not entry.get("report"):
            raise CostError("snapshot recovery requires each supplemental Arc report")
        raw = load_report(argparse.Namespace(cost_json=entry["report"]))
        item = {"entry": entry, "report": raw}
        retained.append(item)
        if raw.get("buildCommit") in (None, "unknown", ""):
            raise CostError("snapshot recovery requires a known supplemental Arc build")
        if not all(same_end(raw["window"].get(key), report["window"].get(key)) for key in ("since", "until")):
            raise CostError("snapshot recovery supplemental report window differs")
        summaries = [dict(raw["parent"], sessionId=raw["source"]["sessionId"]), *raw.get("children", [])]
        selected = next((row for row in summaries if row.get("sessionId") == entry["sessionId"]), None)
        if selected is None or any(entry.get(key) != selected.get(key) for key in ("pricedCostUsd", "costUsd", "coverage")):
            raise CostError("snapshot recovery supplemental price differs from its Arc report")
        if (entry.get("requestCountExact") is False) != (selected.get("requestCountExact") is False):
            raise CostError("snapshot recovery supplemental request coverage differs")
        if previous is None:
            item["sources"] = recovery_sources(raw)
        else:
            before = old[entry["sessionId"]]
            if entry["report"] != before["entry"]["report"]:
                raise CostError("snapshot recovery supplemental report was replaced")
            item["sources"] = recovery_pair(before["report"], raw, before["sources"])
    return totals


def recovery_observation(arguments: argparse.Namespace, report: dict[str, Any], attempt: dict | None = None) -> dict[str, Any] | None:
    """Retain one fixed-window pair. A failed second observation still consumes that observation."""
    mode = arguments.snapshot_recovery
    summaries = [report.get("parent") or {}, *(report.get("children") or [])]
    defects = [summary.get("coverageDefect") or {} for summary in summaries]
    if not mode:
        if (not arguments.run_folder or not any(defect.get("cause") == "snapshot-unmatched" for defect in defects)
                or (Path(arguments.run_folder) / RECOVERY_FILE).exists()):
            return None
        mode = "first"
    if not arguments.run_folder:
        raise CostError("snapshot recovery requires a run folder")
    path = Path(arguments.run_folder) / RECOVERY_FILE
    stamp = attempt["observations"][-1]["observedAt"] if attempt else now()
    observation = {"observedAt": stamp, "report": report}
    if mode == "first":
        state = {"observations": [observation]}
        with path.open("x", encoding="utf-8") as output:
            json.dump(state, output, indent=2)
    else:
        state = attempt
        if not state:
            raise CostError("snapshot recovery has no reserved later observation")
        state["observations"][-1].update(observation)
        state["observations"][-1].pop("error", None)
        save_recovery(arguments, state)
    window = report.get("window") or {}
    if not all(isinstance(moment(window.get(key)), datetime) for key in ("since", "until")):
        if mode == "first" and not arguments.snapshot_recovery:
            state["recoveryUnavailable"] = "the first observation has no frozen start and end"
            save_recovery(arguments, state)
            return None
        raise CostError("snapshot recovery requires a frozen start and end")
    if not all(same_end(getattr(arguments, key), window[key]) for key in ("since", "until")):
        raise CostError("snapshot recovery command and report windows differ")
    if moment(window["until"]) > moment(stamp) or moment(window["since"]) >= moment(window["until"]):
        raise CostError("snapshot recovery window is invalid")
    if report.get("buildCommit") in (None, "unknown", ""):
        raise CostError("snapshot recovery requires a known Arc build")
    first = state["observations"][0]
    old = first["report"]
    if state.get("recoveryUnavailable"):
        raise CostError(state["recoveryUnavailable"])
    if mode == "first":
        if not any(defect.get("cause") == "snapshot-unmatched" for defect in defects):
            raise CostError("snapshot recovery requires a missing matching snapshot")
        first["sources"] = recovery_sources(report)
        try:
            recovery_dispatched(arguments, report, first)
            state["firstComplete"] = True
        finally:
            save_recovery(arguments, state)
        return None
    if not state.get("firstComplete"):
        raise CostError("snapshot recovery lacks a usable first observation")
    if not arguments.no_worker_launches or moment(stamp) <= moment(first["observedAt"]):
        raise CostError("snapshot recovery requires a later request without intervening worker launches")
    sources = recovery_pair(old, report, first.get("sources") or [])
    later = sources[0]["later"].get(arguments.recovery_request)
    if (not later or arguments.recovery_request in first["sources"][0]["later"]
            or moment(later["timestamp"]) <= moment(first["observedAt"])):
        raise CostError("snapshot recovery requires one completed later model request")
    if "supplemental" not in first:
        raise CostError("snapshot recovery lacks the first supplemental source evidence")
    try:
        dispatched = recovery_dispatched(arguments, report, state["observations"][-1], first["supplemental"])
    finally:
        save_recovery(arguments, state)
    commitments = json.loads(Path(arguments.recovery_commitments or "").read_text(encoding="utf-8"))
    keys = ("recovery", "coordinator", "check", "verification", "workers", "close")
    if any(number(commitments.get(key)) is None or commitments[key] < (0 if key == "workers" else 0.000001) for key in keys):
        raise CostError("snapshot recovery requires conservative bounds for every current commitment")
    if (not math.isfinite(arguments.post_cutoff_reserve) or arguments.post_cutoff_reserve < sum(commitments[key] for key in keys)
            or commitments["workers"] < sum(arguments.outstanding)):
        raise CostError("snapshot recovery post-cutoff reserve is insufficient")
    return {"until": window["until"], "reserve": arguments.post_cutoff_reserve, "dispatched": dispatched}


def opening_context(arguments: argparse.Namespace, report: dict[str, Any] | None) -> int | None:
    if arguments.opening_context is not None:
        return arguments.opening_context
    if not arguments.run_folder:
        return None
    path = Path(arguments.run_folder) / OPENING_FILE
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8")).get("contextFirst")
        except (OSError, ValueError, AttributeError):
            return None
        return saved if isinstance(saved, int) and not isinstance(saved, bool) else None
    parent = (report or {}).get("parent") or {}
    first = parent.get("contextFirst")
    if not isinstance(first, int) or isinstance(first, bool):
        return None
    try:
        path.write_text(
            json.dumps({"contextFirst": first, "since": arguments.since}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as error:
        raise CostError(f"cannot write {path}: {error.strerror}") from None
    return first


def whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def expiry_premium(summary: Any) -> tuple[float, int] | None:
    """Return a summary's rebuild premium after cache expiry and its returns, or None when the
    summary lacks either figure or its coverage is incomplete."""
    expiry = summary.get("cacheExpiry") if isinstance(summary, dict) else None
    if not isinstance(expiry, dict) or expiry.get("coverage") != "complete":
        return None
    premium = number(expiry.get("extraCostAfterExpiryUsd"))
    returns = whole(expiry.get("returns"))
    if premium is None or returns is None:
        return None
    return premium, returns


def cache_expiry(report: dict[str, Any], run_spend: float, incomplete: bool) -> dict[str, Any] | str:
    """Return the run's rebuild premium after cache expiry, or unknown."""
    combined = report.get("combined") if isinstance(report.get("combined"), dict) else {}
    if incomplete:
        return UNKNOWN
    total = expiry_premium(combined)
    if total is None:
        return UNKNOWN
    premium, returns = total
    largest: dict[str, Any] | str | None = None
    for child in report.get("children") or []:
        figure = expiry_premium(child)
        if figure is None or not isinstance(child, dict) or not child.get("sessionId"):
            largest = UNKNOWN
            break
        if figure[0] > 0 and (largest is None or figure[0] > largest["premium_usd"]):
            largest = {"session": str(child["sessionId"]), "premium_usd": round(figure[0], 4)}
    if run_spend > 0:
        share = premium / run_spend
    elif premium == 0:
        share = 0.0
    else:
        return UNKNOWN
    return {
        "premium_usd": round(premium, 4),
        "returns": returns,
        "share": round(share, 4),
        "largest_child": largest,
        "flag": premium > EXPIRY_FLAG_USD or share > EXPIRY_FLAG_SHARE,
    }


def empty_and_complete(combined: dict[str, Any]) -> bool:
    """Arc omits coverage and cost fields for an empty window that no coverage defect intersects."""
    return (
        whole(combined.get("requests")) == 0
        and whole(combined.get("unpricedRequests")) == 0
        and combined.get("coverage") is None
        and not combined.get("coverageDefect")
        and not combined.get("unpricedModels")
        and combined.get("requestCountExact") is not False
        and "costUsd" not in combined
        and "pricedCostUsd" not in combined
    )


def requests_shown(parent: dict[str, Any]) -> str | None:
    """Return the parent's request count with its qualifier when Arc marks the count not exact."""
    requests = whole(parent.get("requests"))
    if requests is None or parent.get("requestCountExact") is not False:
        return None
    coverage = parent.get("coverage")
    if coverage in IDENTIFIED_COVERAGE:
        return f"{requests:,} identified, count not exact"
    if coverage == LEGACY_COVERAGE:
        return f"{requests:,} observed snapshots, count not exact"
    return f"{UNAVAILABLE} (parent coverage {coverage})" if coverage else None


def context_reason(parent: dict[str, Any]) -> str | None:
    """Return why the parent's peak context is missing, when the report says."""
    if whole(parent.get("contextMax")) is not None:
        return None
    coverage = parent.get("coverage")
    if coverage and coverage != "recorded-reconciled":
        return f"parent coverage {coverage}"
    if whole(parent.get("requests")) == 0:
        return "no parent request in the window"
    return None


def expiry_reason(combined: dict[str, Any], incomplete: bool) -> str | None:
    """Return the returns, lifetimes, longest gap and missing premium behind an unknown premium."""
    expiry = combined.get("cacheExpiry")
    returns = whole(expiry.get("returns")) if isinstance(expiry, dict) else None
    if returns is None:
        return None
    lifetimes = expiry.get("byLifetime") if isinstance(expiry.get("byLifetime"), dict) else {}
    tiers = [str(tier) for tier, total in lifetimes.items() if whole(total)]
    parts = [f"{returns} returns after {' and '.join(tiers)} lifetimes" if returns and tiers else f"{returns} returns"]
    longest = whole(expiry.get("longestGapSeconds"))
    if longest is not None:
        parts.append(f"longest {longest} s")
    premium = number(expiry.get("extraCostAfterExpiryUsd"))
    if expiry.get("coverage") != "complete":
        parts.append(f"premium unpriced: {expiry.get('coverage') or 'coverage not reported'}")
    elif premium is None:
        parts.append("premium unpriced: unpriced returns")
    elif incomplete:
        parts.append(f"partial premium {premium:.4f} USD, combined cost incomplete")
    else:
        parts.append(f"premium {premium:.4f} USD without priced spend")
    return ", ".join(parts)


def reproduce(arguments: argparse.Namespace, report: dict[str, Any]) -> str:
    """Return the arc_cost.py command that prices the report's session and window."""
    script = Path(arguments.arc_cost) if arguments.arc_cost else default_arc_cost()
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    session = arguments.session or source.get("sessionId") or source.get("file") or "<session>"
    words = ["python" if os.name == "nt" else "python3", f'"{script.as_posix()}"', str(session)]
    for flag, value in (("--since", arguments.since or window.get("since")), ("--until", arguments.until or window.get("until"))):
        if value:
            words += [flag, str(value)]
    return " ".join(words)


def child_doubts(summary: dict[str, Any]) -> list[str]:
    """Return why a child summary without a coverage defect still cannot count as complete."""
    doubts = []
    if summary.get("requestCountExact") is False:
        doubts.append("request count not exact")
    if "costUsd" in summary and summary["costUsd"] is None:
        doubts.append("cost unpriced")
    return doubts


def coverage_detail(arguments: argparse.Namespace, report: dict[str, Any], dispatched_unpriced: bool) -> str:
    """Name each summary that is not reconciled, unpriced usage, and the command that reproduces it."""
    parent = report.get("parent") if isinstance(report.get("parent"), dict) else {}
    combined = report.get("combined") if isinstance(report.get("combined"), dict) else {}
    children = [child for child in report.get("children") or [] if isinstance(child, dict)]
    parts = []
    complete_children = 0
    for name, summary in [("parent", parent)] + [(f"child {child.get('sessionId')}", child) for child in children]:
        coverage = summary.get("coverage")
        if not coverage or coverage == "recorded-reconciled":
            if name == "parent":
                continue
            doubts = child_doubts(summary)
            if not doubts:
                complete_children += 1
                continue
            parts.append(f"{name} {coverage or 'coverage unknown'} ({', '.join(doubts)})")
            continue
        text = f"{name} {coverage}"
        defect = summary.get("coverageDefect")
        if isinstance(defect, dict):
            if defect.get("since"):
                text += f" since {defect['since']}"
            if defect.get("cause"):
                text += f" ({defect['cause']})"
        parts.append(text)
    models = combined.get("unpricedModels") if isinstance(combined.get("unpricedModels"), list) else []
    unpriced_requests = whole(combined.get("unpricedRequests"))
    if unpriced_requests or models:
        text = f"{unpriced_requests or 0} unpriced requests"
        parts.append(text + (f" from {', '.join(str(model) for model in models)}" if models else ""))
    if dispatched_unpriced:
        parts.append("a dispatched source is unpriced")
    if not parts:
        parts.append("combined total unpriced")
    if children:
        parts.append(f"{complete_children} of {len(children)} children with complete coverage")
    return f"{', '.join(parts)}. Reproduce: {reproduce(arguments, report)}"


def not_converging(rounds: Sequence[tuple[str, int, int]]) -> bool:
    counted = [findings for kind, findings, _ in rounds if kind != "carry"]
    return len(counted) >= 2 and counted[-1] >= counted[-2]


def regressions_twice(rounds: Sequence[tuple[str, int, int]]) -> bool:
    corrections = [regressions for kind, _, regressions in rounds if kind == "correction"]
    return len(corrections) >= 2 and corrections[-1] > 0 and corrections[-2] > 0


def decide(arguments: argparse.Namespace, counters: dict[str, Any], unpriced: bool) -> str | None:
    """Return the rule that stops the run, or None to continue."""
    spend = counters["spend"]
    if spend >= arguments.absolute_stop:
        return RULE_ABSOLUTE
    if arguments.launch_allowance is not None:
        committed = sum(arguments.outstanding) + arguments.reserve + arguments.launch_allowance + counters.get("post_cutoff_reserve", 0)
        if spend + committed >= arguments.absolute_stop:
            return RULE_ADMISSION
        if counters.get("recovery") and spend + committed >= arguments.ceiling:
            return "snapshot recovery commitments reach the ceiling"
        # Running workers already took their share of the allowance when they were admitted.
        needed = arguments.reserve + arguments.launch_allowance
        if unpriced and (arguments.unpriced_allowance_left is None or needed > arguments.unpriced_allowance_left):
            return RULE_UNPRICED
    if arguments.operation == "fix-round":
        released = arguments.continuing_authorization and arguments.replan_done
        if regressions_twice(arguments.rounds) and not released:
            return RULE_DIAGNOSE
        converging = not not_converging(arguments.rounds)
        if not converging and not released:
            return RULE_NOT_CONVERGING
        if not converging and counters["run_spend"] > arguments.ceiling:
            return RULE_CEILING
    return None


def build(arguments: argparse.Namespace) -> tuple[str, str | None, dict[str, Any], str | None]:
    """Return the verdict, its rule, the counters and any error."""
    counters: dict[str, Any] = {
        "requests": UNAVAILABLE,
        "context_growth": UNAVAILABLE,
        "opening_context": UNAVAILABLE,
        "spend": UNAVAILABLE,
        "run_spend": UNAVAILABLE,
        "dispatched_spend": UNAVAILABLE,
        "earlier_spend": arguments.earlier_spend,
        "outstanding": sum(arguments.outstanding) + arguments.reserve,
        "launch_allowance": arguments.launch_allowance if arguments.launch_allowance is not None else 0.0,
        "ceiling": arguments.ceiling,
        "absolute_stop": arguments.absolute_stop,
        "coverage": UNAVAILABLE,
        "cache_expiry": UNKNOWN,
        # Display only. decide() never reads these.
        "shown": {"requests": None, "context_reason": None, "coverage_detail": None, "expiry_reason": None},
    }
    report: dict[str, Any] | None = None
    attempt = None
    try:
        attempt = begin_recovery(arguments)
        report = load_report(arguments)
    except (CostError, OSError, ValueError, KeyError, TypeError) as error:
        if attempt:
            if arguments.snapshot_recovery == "retry":
                attempt["observations"].append({"observedAt": now()})
            save_recovery(arguments, attempt, str(error))
        try:
            opening = opening_context(arguments, None)
        except CostError:
            opening = None
        if opening is not None:
            counters["opening_context"] = opening
        return "error", None, counters, str(error)
    try:
        if arguments.snapshot_recovery == "retry":
            summaries = [report.get("parent") or {}, *(report.get("children") or [])]
            if any((summary.get("coverageDefect") or {}).get("cause") == "snapshot-unmatched" for summary in summaries):
                return "error", None, counters, "snapshot recovery pending: matching snapshots have not completed. Retry after a useful coordinator request without worker launches"
            attempt["observations"].append({"observedAt": now()})
            save_recovery(arguments, attempt)
            arguments.snapshot_recovery = "observe"
        recovery = recovery_observation(arguments, report, attempt)
    except (CostError, OSError, ValueError, KeyError, TypeError) as error:
        if attempt:
            save_recovery(arguments, attempt, str(error))
        return "error", None, counters, f"snapshot recovery refused: {error}"
    if recovery:
        counters["recovery"] = recovery
        counters["post_cutoff_reserve"] = recovery["reserve"]
        counters["outstanding"] += recovery["reserve"]
    parent = report.get("parent") if isinstance(report.get("parent"), dict) else {}
    combined = report.get("combined") if isinstance(report.get("combined"), dict) else {}
    requests = parent.get("requests")
    if isinstance(requests, int) and not isinstance(requests, bool) and parent.get("requestCountExact") is not False:
        counters["requests"] = requests
    shown = counters["shown"]
    shown["requests"] = requests_shown(parent)
    shown["context_reason"] = context_reason(parent)
    try:
        opening = opening_context(arguments, report)
    except CostError as error:
        return "error", None, counters, str(error)
    if opening is not None:
        counters["opening_context"] = opening
    peak = parent.get("contextMax")
    if opening is not None and isinstance(peak, int) and not isinstance(peak, bool):
        counters["context_growth"] = max(0, peak - opening)
    unpriced_requests = combined.get("unpricedRequests")
    empty = empty_and_complete(combined)
    incomplete = not empty and (combined.get("costUsd") is None or combined.get("requestCountExact") is False)
    coverage = combined.get("coverage") or ("incomplete" if incomplete else "complete")
    # Incomplete coverage blocks admission as unpriced requests do.
    unpriced = incomplete or not (
        isinstance(unpriced_requests, int) and not isinstance(unpriced_requests, bool) and unpriced_requests == 0
    )
    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    window_end = window["until"] if "until" in window else arguments.until
    try:
        dispatched, dispatched_unpriced, dispatched_priced = recovery["dispatched"] if recovery else (
            load_dispatched(arguments.dispatched_cost, owned_sources(report), window_end, window.get("since"), bool(recovery))
            if arguments.dispatched_cost
            else (0.0, False, True)
        )
    except CostError as error:
        return "error", None, counters, str(error)
    if dispatched_unpriced:
        unpriced = True
        coverage = "incomplete"
    counters["coverage"] = coverage
    if unpriced:
        shown["coverage_detail"] = coverage_detail(arguments, report, dispatched_unpriced)
    priced = 0.0 if empty else number(combined.get("pricedCostUsd"))
    if priced is None:
        return "error", None, counters, "the cost report has no combined.pricedCostUsd"
    counters["run_spend"] = round(priced + dispatched, 4)
    counters["dispatched_spend"] = round(dispatched, 4) if dispatched_priced else UNAVAILABLE
    counters["spend"] = round(priced + dispatched + arguments.earlier_spend, 4)
    counters["cache_expiry"] = cache_expiry(report, priced, incomplete)
    if counters["cache_expiry"] == UNKNOWN:
        shown["expiry_reason"] = expiry_reason(combined, incomplete)
    rule = decide(arguments, counters, unpriced)
    return ("stop" if rule else "continue"), rule, counters, None


def usd(value: Any) -> str:
    return UNAVAILABLE if value == UNAVAILABLE else f"{value:.2f} USD"


def count(value: Any, unit: str = "") -> str:
    return UNAVAILABLE if value == UNAVAILABLE else f"{value:,}{unit}"


def expiry_text(value: Any, reason: str | None = None) -> str:
    if not isinstance(value, dict):
        return f"{UNKNOWN} ({reason}), flag: {UNKNOWN}" if reason else f"{UNKNOWN}, flag: {UNKNOWN}"
    largest = value["largest_child"]
    if isinstance(largest, dict):
        child = f"{largest['session']} {usd(largest['premium_usd'])}"
    else:
        child = UNKNOWN if largest == UNKNOWN else "none"
    return (
        f"{usd(value['premium_usd'])} over {count(value['returns'])} returns, "
        f"{value['share'] * 100:.1f}% of the Arc report's priced spend, largest child {child}, "
        f"flag: {'true' if value['flag'] else 'false'}"
    )


def error_head(error: str | None, rule: str | None) -> str:
    return f"error: {error}. Stop rule: {rule}" if rule else f"error: {error}"


def render_text(verdict: str, rule: str | None, counters: dict[str, Any], error: str | None) -> str:
    head = f"stop: {rule}" if verdict == "stop" else (error_head(error, rule) if verdict == "error" else "continue")
    shown = counters["shown"]
    growth = f"context growth: {count(counters['context_growth'], ' tokens')} above opening {count(counters['opening_context'], ' tokens')}"
    if counters["context_growth"] == UNAVAILABLE and shown["context_reason"]:
        growth += f" ({shown['context_reason']})"
    lines = [
        head,
        f"requests: {shown['requests'] or count(counters['requests'])}",
        growth,
        f"priced spend: {usd(counters['spend'])} (this run {usd(counters['run_spend'])}, of it dispatched {usd(counters['dispatched_spend'])}, earlier runs {usd(counters['earlier_spend'])})",
        f"outstanding allowances: {usd(counters['outstanding'])}, this launch {usd(counters['launch_allowance'])}",
        f"ceiling: {usd(counters['ceiling'])}",
        f"absolute stop: {usd(counters['absolute_stop'])}",
        f"coverage: {counters['coverage']}",
    ]
    if shown["coverage_detail"]:
        lines.append(f"coverage detail: {shown['coverage_detail']}")
    if counters.get("recovery"):
        lines.append(f"cost as of: {counters['recovery']['until']}, post-cutoff reserve {usd(counters['post_cutoff_reserve'])}, Close must reconcile later work")
    lines.append(f"cache_expiry: {expiry_text(counters['cache_expiry'], shown['expiry_reason'])}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    verdict, rule, counters, error = build(arguments)
    if arguments.run_folder:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = Path(arguments.run_folder) / BLOCK_FILE
        try:
            with open(path, "a", encoding="utf-8") as log:
                log.write(f"{stamp} {arguments.operation}\n{render_text(verdict, rule, counters, error)}\n")
        except OSError as failure:
            # A block that never reaches the run folder cannot continue the run.
            # A stop keeps its rule so the printed block still names it.
            if verdict != "error":
                verdict, error = "error", f"cannot write {path}: {failure.strerror}"
    if arguments.json:
        output = json.dumps({"verdict": verdict, "rule": rule, "error": error, **counters}, indent=2) + "\n"
    else:
        output = render_text(verdict, rule, counters, error)
    sys.stdout.write(output)
    return {"continue": 0, "stop": 1}.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())
