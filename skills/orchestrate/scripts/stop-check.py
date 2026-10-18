#!/usr/bin/env python3
"""Decide whether a coordinator continues past a numeric limit, and print its counters.

The verdict covers numeric limits only: spend against the ceiling and the absolute stop,
admission of a paid launch, unpriced usage, and the round rules for a fix round. Request and
context counts are checkpoints, so the script reports them and never stops on them. Every other
stop condition in the calling workflow still binds.

The cost source is the Arc report from session-cost-audit's arc_cost.py, schema version 2.
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
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

SCHEMA_VERSION = 2
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
    if not isinstance(report, dict) or report.get("schemaVersion") != SCHEMA_VERSION:
        raise CostError(f"the cost report is not Arc cost schema version {SCHEMA_VERSION}")
    return report


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
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


def load_dispatched(path: str, owned: set[str], window_end: Any) -> tuple[float, bool, bool]:
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
        if "until" in entry and not same_end(entry["until"], window_end):
            raise CostError(
                f"{path} prices {entry['sessionId']} to {entry['until']}, but the cost report ends at {window_end}"
            )
        source = str(entry["sessionId"])
        if source in seen:
            continue
        seen.add(source)
        if entry.get("costUsd") is None or entry.get("requestCountExact") is False:
            unpriced = True
        priced = number(entry.get("pricedCostUsd"))
        if priced is None:
            unpriced = True
            every_priced = False
            continue
        total += priced
    return total, unpriced, every_priced


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
        committed = sum(arguments.outstanding) + arguments.reserve + arguments.launch_allowance
        if spend + committed >= arguments.absolute_stop:
            return RULE_ADMISSION
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
    try:
        report = load_report(arguments)
    except CostError as error:
        try:
            opening = opening_context(arguments, None)
        except CostError:
            opening = None
        if opening is not None:
            counters["opening_context"] = opening
        return "error", None, counters, str(error)
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
        dispatched, dispatched_unpriced, dispatched_priced = (
            load_dispatched(arguments.dispatched_cost, owned_sources(report), window_end)
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
