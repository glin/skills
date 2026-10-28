"""Tests for stop-check.py.

Each test writes an Arc cost report fixture to a temporary directory and calls the script's
main with --cost-json, so no test reads a real session. The pricing failure test runs a
stand-in for arc_cost.py that exits with an error.

Run with: python -I test_stop_check.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop-check.py")
# Loading the script by path would otherwise leave a bytecode cache in the skill folder.
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("stop_check", SCRIPT)
stop_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stop_check)


def report(
    *,
    requests: int = 10,
    context_first: int | None = 30000,
    context_max: int = 40000,
    priced: float = 5.0,
    unpriced_requests: int = 0,
    children: tuple[str, ...] = (),
    request_count_exact: bool = True,
    window_until: str | None = None,
) -> dict:
    parent = {
        "requests": requests,
        "unpricedRequests": unpriced_requests,
        "contextFirst": context_first,
        "contextMax": context_max,
        "pricedCostUsd": priced,
    }
    if not request_count_exact:
        parent["requestCountExact"] = False
    return {
        "schemaVersion": 2,
        "source": {"sessionId": "coordinator"},
        "window": {"since": "2026-01-01T00:00:00Z", "until": window_until},
        "parent": parent,
        "children": [{"sessionId": child, "pricedCostUsd": 0.0} for child in children],
        "combined": {
            "requests": requests,
            "unpricedRequests": unpriced_requests,
            "pricedCostUsd": priced,
            "costUsd": None if unpriced_requests else priced,
        },
    }


class StopCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="stop-check-test-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name

    def write(self, name: str, content: object) -> str:
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8", newline="\n") as file:
            file.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def run_script(self, fixture: dict | None, *arguments: str) -> tuple[int, list[str]]:
        argv = list(arguments)
        if fixture is not None:
            argv += ["--cost-json", self.write("cost.json", fixture)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = stop_check.main(argv)
        return code, output.getvalue().splitlines()

    def assert_verdict(self, code: int, lines: list[str], expected: str) -> None:
        self.assertEqual(lines[0], expected, lines)
        self.assertEqual(code, 0 if expected == "continue" else 1, lines)

    def recovery_fixture(self) -> tuple[dict, dict, list[str]]:
        folder = os.path.join(self.directory, "recovery")
        os.mkdir(folder)
        bill = {"type": "token_usage_record", "timestamp": "2026-01-01T00:01:00Z",
                "payload": {"thread_id": "coordinator", "response_id": "first", "usage": {"total_tokens": 10}}}
        source = self.write("rollout.jsonl", json.dumps(bill) + "\n")
        first = report(requests=1, priced=5, window_until="2026-01-01T00:02:00Z")
        first["source"].update(kind="codex", file=source)
        first["buildCommit"] = "a" * 40
        for summary in (first["parent"], first["combined"]):
            summary.update(costUsd=None, requestCountExact=False, coverage="billed-with-unknown-coverage")
        first["parent"]["coverageDefect"] = {"cause": "snapshot-unmatched", "since": bill["timestamp"]}
        second = json.loads(json.dumps(first))
        for summary in (second["parent"], second["combined"]):
            summary.update(costUsd=5, requestCountExact=True, coverage="recorded-reconciled")
            summary.pop("coverageDefect", None)
        commitments = self.write("commitments.json", dict(recovery=1, coordinator=1, check=1, verification=1, workers=0, close=1))
        arguments = ["--ceiling", "20", "--run-folder", folder, "--since", first["window"]["since"],
                     "--until", first["window"]["until"], "--launch-allowance", "2", "--reserve", "1",
                     "--post-cutoff-reserve", "5", "--recovery-commitments", commitments]
        return first, second, arguments

    def observe_first(self, first: dict, arguments: list[str]) -> list[str]:
        with patch.object(stop_check, "now", return_value="2026-01-01T00:03:00Z", create=True):
            code, lines = self.run_script(first, *arguments, "--snapshot-recovery", "first")
        self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
        self.append_later_request(first)
        return lines

    def append_later_request(self, first: dict) -> None:
        with open(first["source"]["file"], "a", encoding="utf-8") as source:
            source.write(json.dumps({"type": "token_usage_record", "timestamp": "2026-01-01T00:04:00Z",
                                    "payload": {"thread_id": first["source"]["sessionId"], "response_id": "later", "usage": {"total_tokens": 20}}}) + "\n")

    def recover(self, second: dict, arguments: list[str]) -> tuple[int, list[str]]:
        with patch.object(stop_check, "now", return_value="2026-01-01T00:05:00Z", create=True):
            return self.run_script(second, *arguments, "--snapshot-recovery", "observe",
                                   "--recovery-request", "later", "--no-worker-launches")

    def test_delayed_snapshot_recovers_once_without_adding_the_window_twice(self) -> None:
        first, second, arguments = self.recovery_fixture()
        self.observe_first(first, arguments)
        code, lines = self.recover(second, arguments)
        self.assert_verdict(code, lines, "continue")
        self.assertIn("priced spend: 5.00 USD (this run 5.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)", lines)
        self.assertIn("cost as of: 2026-01-01T00:02:00Z, post-cutoff reserve 5.00 USD, Close must reconcile later work", lines)
        folder = arguments[arguments.index("--run-folder") + 1]
        with open(os.path.join(folder, "snapshot-recovery.json"), encoding="utf-8") as saved:
            evidence = json.load(saved)
        self.assertEqual([row["observedAt"] for row in evidence["observations"]],
                         ["2026-01-01T00:03:00Z", "2026-01-01T00:05:00Z"])
        self.assertEqual([row["report"] for row in evidence["observations"]], [first, second])
        self.assertNotEqual(self.recover(second, arguments)[0], 0)

    def test_live_checkpoint_freezes_before_read_and_retry_discovers_later_request(self) -> None:
        first, second, arguments = self.recovery_fixture()
        first["window"]["until"] = second["window"]["until"] = "2026-01-01T00:02:00.123Z"
        del arguments[arguments.index("--until"):arguments.index("--until") + 2]
        arguments += ["--session", "coordinator"]
        observed = []

        def adapter(args):
            observed.append(args.until)
            return first if len(observed) <= 2 else second

        with patch.object(stop_check, "load_report", side_effect=adapter):
            with patch.object(stop_check, "now", return_value="2026-01-01T00:02:00.123456Z"):
                code, lines = self.run_script(None, *arguments)
            self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
            folder = arguments[arguments.index("--run-folder") + 1]
            path = os.path.join(folder, stop_check.RECOVERY_FILE)
            with open(path, "rb") as saved:
                original = saved.read()
            retry = [*arguments, "--snapshot-recovery", "retry", "--no-worker-launches"]
            with patch.object(stop_check, "now", return_value="2026-01-01T00:05:00Z"):
                for _ in range(2):
                    code, lines = self.run_script(None, *retry)
                    self.assertEqual(code, 2, lines)
                    self.assertIn("pending", lines[0])
                    with open(path, "rb") as saved:
                        self.assertEqual(saved.read(), original)
                self.assertEqual(len(observed), 1)
                self.append_later_request(first)
                code, lines = self.run_script(None, *retry)
                self.assertEqual(code, 2, lines)
                self.assertIn("pending", lines[0])
                with open(path, "rb") as saved:
                    self.assertEqual(saved.read(), original)
                code, lines = self.run_script(None, *retry)
                self.assert_verdict(code, lines, "continue")
                code, lines = self.run_script(None, *retry)
                self.assertEqual(code, 2, lines)
                self.assertIn("already used", lines[0])
            self.assertEqual(observed, [first["window"]["until"]] * 3)
        with open(path, encoding="utf-8") as saved:
            state = json.load(saved)
        self.assertEqual([item["report"] for item in state["observations"]], [first, second])

    def test_retry_refuses_first_report_without_source_witnesses(self) -> None:
        first, _, arguments = self.recovery_fixture()
        folder = arguments[arguments.index("--run-folder") + 1]
        path = os.path.join(folder, stop_check.RECOVERY_FILE)
        content = json.dumps({"observations": [{"observedAt": "2026-01-01T00:03:00Z", "report": first}]})
        with open(path, "w", encoding="utf-8") as saved:
            saved.write(content)
        self.append_later_request(first)
        with patch.object(stop_check, "load_report") as loader:
            code, lines = self.run_script(None, *arguments, "--session", "coordinator",
                                          "--snapshot-recovery", "retry", "--no-worker-launches")
        self.assertEqual(code, 2, lines)
        self.assertIn("lacks a usable first observation", lines[0])
        loader.assert_not_called()
        with open(path, encoding="utf-8") as saved:
            self.assertEqual(saved.read(), content)

    def test_supported_retry_keeps_gaps_prices_and_reserve_refusals(self) -> None:
        for case in ("gap", "price", "reserve", "launch", "window"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as directory:
                original, self.directory = self.directory, directory
                try:
                    first, second, arguments = self.recovery_fixture()
                    if case == "gap":
                        first["combined"]["coverageDefect"] = {"cause": "compaction-unmatched"}
                    if case == "price":
                        first["combined"].update(unpricedRequests=1, unpricedModels=["unknown"])
                    self.observe_first(first, arguments)
                    if case == "price":
                        second["combined"].update(unpricedRequests=1, unpricedModels=["unknown"], costUsd=None)
                    if case == "reserve":
                        arguments[arguments.index("--post-cutoff-reserve") + 1] = "1"
                    if case == "window":
                        arguments[arguments.index("--until") + 1] = "2026-01-01T00:01:30Z"
                    flags = [] if case == "launch" else ["--no-worker-launches"]
                    with patch.object(stop_check, "now", return_value="2026-01-01T00:05:00Z"):
                        code, lines = self.run_script(second, *arguments, "--snapshot-recovery", "retry", *flags)
                    self.assertEqual(code, 2, lines)
                finally:
                    self.directory = original

    def test_normal_cost_check_retains_the_actual_first_report_for_one_later_observation(self) -> None:
        first, second, arguments = self.recovery_fixture()
        with patch.object(stop_check, "load_report", side_effect=[first, second]) as loader:
            with patch.object(stop_check, "now", return_value="2026-01-01T00:03:00Z"):
                code, lines = self.run_script(None, *arguments, "--session", "coordinator")
            self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
            self.append_later_request(first)
            with patch.object(stop_check, "now", return_value="2026-01-01T00:05:00Z"):
                code, lines = self.run_script(None, *arguments, "--session", "coordinator", "--snapshot-recovery", "observe",
                                              "--recovery-request", "later", "--no-worker-launches")
            self.assert_verdict(code, lines, "continue")
            self.assertEqual(loader.call_count, 2)
        folder = arguments[arguments.index("--run-folder") + 1]
        with open(os.path.join(folder, "snapshot-recovery.json"), encoding="utf-8") as saved:
            observations = json.load(saved)["observations"]
        self.assertEqual([item["report"] for item in observations], [first, second])
        self.assertEqual([item["observedAt"] for item in observations],
                         ["2026-01-01T00:03:00Z", "2026-01-01T00:05:00Z"])

    def test_failed_later_load_consumes_recovery_before_loading_again(self) -> None:
        for failure in ("adapter", "malformed", "schema", "missing"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    self.observe_first(first, arguments)
                    path = self.write("failed.json", "{" if failure == "malformed" else {"schemaVersion": 999})
                    if failure == "missing":
                        os.remove(path)
                    flags = ["--cost-json", path] if failure != "adapter" else [
                        "--session", "coordinator", "--arc-cost", self.write("failure.py", "raise SystemExit('unavailable')")]
                    real_loader = stop_check.load_report
                    with patch.object(stop_check, "load_report", wraps=real_loader) as loader:
                        code, lines = self.recover(None, [*arguments, *flags])
                        self.assertEqual(code, 2, lines)
                        folder = arguments[arguments.index("--run-folder") + 1]
                        with open(os.path.join(folder, stop_check.RECOVERY_FILE), encoding="utf-8") as saved:
                            observations = json.load(saved)["observations"]
                        self.assertEqual(len(observations), 2)
                        self.assertEqual(observations[1]["observedAt"], "2026-01-01T00:05:00Z")
                        self.assertTrue(observations[1]["error"])
                        self.assertEqual(observations[0]["report"], first)
                        code, lines = self.recover(second, arguments)
                        self.assertEqual(code, 2, lines)
                        self.assertIn("already used", lines[0])
                        self.assertEqual(loader.call_count, 1)
                finally:
                    self.directory = original

    def test_explicit_initial_recovery_needs_finite_bounds_before_loading(self) -> None:
        first, _, arguments = self.recovery_fixture()
        del arguments[arguments.index("--until"):arguments.index("--until") + 2]
        with patch.object(stop_check, "load_report", return_value=first) as loader:
            code, lines = self.run_script(None, *arguments, "--session", "coordinator", "--snapshot-recovery", "first")
        self.assertEqual(code, 2, lines)
        loader.assert_not_called()

    def test_open_ended_accounting_keeps_first_evidence_without_recovery(self) -> None:
        first, second, arguments = self.recovery_fixture()
        first["window"]["until"] = None
        unbounded = list(arguments)
        del unbounded[unbounded.index("--until"):unbounded.index("--until") + 2]
        with patch.object(stop_check, "now", return_value="2026-01-01T00:03:00Z"):
            code, lines = self.run_script(first, *unbounded)
        self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
        self.assertIn("priced spend: 5.00 USD (this run 5.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)", lines)
        self.append_later_request(first)
        code, lines = self.run_script(second, *arguments)
        self.assert_verdict(code, lines, "continue")
        code, lines = self.recover(second, arguments)
        self.assertEqual(code, 2, lines)
        folder = arguments[arguments.index("--run-folder") + 1]
        with open(os.path.join(folder, stop_check.RECOVERY_FILE), encoding="utf-8") as saved:
            evidence = json.load(saved)
        self.assertEqual(evidence["observations"][0]["report"], first)
        self.assertTrue(evidence["recoveryUnavailable"])
        code, lines = self.run_script(second, *arguments, "--snapshot-recovery", "first")
        self.assertEqual(code, 2, lines)

    def supplemental_fixture(self, first: dict, price: float) -> tuple[dict, dict, str]:
        raw = json.loads(json.dumps(first))
        source = self.write("worker.jsonl", json.dumps({"type": "token_usage_record", "timestamp": "2026-01-01T00:01:00Z",
                                                       "payload": {"thread_id": "worker", "response_id": "worker-first"}}) + "\n")
        raw["source"].update(sessionId="worker", file=source)
        for summary in (raw["parent"], raw["combined"]):
            summary.update(pricedCostUsd=price, costUsd=price, requestCountExact=True, coverage="recorded-reconciled")
            summary.pop("coverageDefect", None)
        report_path = self.write("worker-report.json", raw)
        entry = dict(sessionId="worker", report=report_path, pricedCostUsd=price, costUsd=price,
                     coverage="recorded-reconciled", requestCountExact=True, **first["window"])
        path = self.write("dispatched.json", {"sources": [entry, dict(entry)]})
        return raw, entry, path

    def test_recovery_retains_supplemental_spending_and_source_evidence(self) -> None:
        cases = ("zero", "omitted flag", "omitted entry", "source", "report", "build", "bill", "replacement", "valid")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    raw, entry, dispatched = self.supplemental_fixture(first, 30 if case != "valid" else 1)
                    initial = [*arguments, "--dispatched-cost", dispatched]
                    initial_lines = self.observe_first(first, initial)
                    if case != "valid":
                        self.assertIn("priced spend: 35.00 USD (this run 35.00 USD, of it dispatched 30.00 USD, earlier runs 0.00 USD)", initial_lines)
                    observe = list(initial)
                    if case == "zero":
                        entry.update(pricedCostUsd=0, costUsd=0)
                        for summary in (raw["parent"], raw["combined"]):
                            summary.update(pricedCostUsd=0, costUsd=0)
                    elif case == "omitted flag":
                        observe = arguments
                    elif case == "source":
                        entry["sessionId"] = "other"
                    elif case == "report":
                        entry["report"] = self.write("different-report.json", raw)
                    elif case == "build":
                        raw["buildCommit"] = "b" * 40
                    elif case == "bill":
                        with open(raw["source"]["file"], "a", encoding="utf-8") as source:
                            source.write(json.dumps({"type": "token_usage_record", "timestamp": "2026-01-01T00:01:30Z",
                                                    "payload": {"thread_id": "worker", "response_id": "extra"}}) + "\n")
                    elif case == "replacement":
                        path = raw["source"]["file"]
                        os.rename(path, path + ".old")
                        with open(path + ".old", "rb") as source, open(path, "wb") as replacement:
                            replacement.write(source.read())
                    self.write("worker-report.json", raw)
                    self.write("dispatched.json", {"sources": [] if case == "omitted entry" else [entry, dict(entry)]})
                    with patch.object(stop_check, "load_dispatched", wraps=stop_check.load_dispatched) as loader:
                        code, lines = self.recover(second, observe)
                    if case == "valid":
                        self.assertEqual(loader.call_count, 1)
                    self.assertEqual(code, 0 if case == "valid" else 2, lines)
                    if case == "valid":
                        self.assertIn("priced spend: 6.00 USD (this run 6.00 USD, of it dispatched 1.00 USD, earlier runs 0.00 USD)", lines)
                        folder = arguments[arguments.index("--run-folder") + 1]
                        with open(os.path.join(folder, stop_check.RECOVERY_FILE), encoding="utf-8") as saved:
                            observations = json.load(saved)["observations"]
                        self.assertEqual([item["supplemental"][0]["report"] for item in observations], [raw, raw])
                        self.assertEqual(observations[0]["supplemental"][0]["sources"][0]["bills"].keys(), {"worker-first"})
                finally:
                    self.directory = original

    def test_incomplete_first_supplemental_evidence_cannot_be_omitted_for_admission(self) -> None:
        first, second, arguments = self.recovery_fixture()
        _, entry, dispatched = self.supplemental_fixture(first, 30)
        os.remove(entry["report"])
        with patch.object(stop_check, "now", return_value="2026-01-01T00:03:00Z"):
            code, lines = self.run_script(first, *arguments, "--dispatched-cost", dispatched)
        self.assertEqual(code, 2, lines)
        self.append_later_request(first)
        code, lines = self.recover(second, arguments)
        self.assertEqual(code, 2, lines)
        self.assertIn("usable first observation", lines[0])

    def test_recovery_preserves_complete_empty_parent_and_child_contract(self) -> None:
        for empty_owner in ("parent", "child"):
            for defect in (None, "compaction-unmatched", "snapshot-unmatched", "unknown request", "unknown model"):
                with self.subTest(empty_owner=empty_owner, defect=defect), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                    original, self.directory = self.directory, folder
                    try:
                        first, second, arguments = self.recovery_fixture()
                        empty = dict(requests=0, pricedRequests=0, unpricedRequests=0)
                        if empty_owner == "child":
                            empty.update(sessionId="empty", kind="codex-thread", file=self.write("empty.jsonl", ""))
                            for fixture in (first, second):
                                fixture["children"] = [dict(empty)]
                        else:
                            child = dict(first["parent"], sessionId="coordinator", kind="codex-thread", file=first["source"]["file"])
                            complete = dict(second["parent"], sessionId="coordinator", kind="codex-thread", file=first["source"]["file"])
                            first["children"], second["children"] = [child], [complete]
                            for fixture in (first, second):
                                fixture["source"].update(sessionId="empty", file=self.write("empty.jsonl", ""))
                                fixture["parent"] = dict(empty)
                        if defect:
                            target = first["parent"] if empty_owner == "parent" else first["children"][0]
                            if defect.startswith("unknown"):
                                other = second["parent"] if empty_owner == "parent" else second["children"][0]
                                for summary in (target, other):
                                    summary.update({"unpricedRequests": 1} if defect == "unknown request" else {"unpricedModels": ["unknown"]})
                            else:
                                target["coverageDefect"] = {"cause": defect}
                        self.observe_first(first, arguments)
                        code, lines = self.recover(second, arguments)
                        self.assertEqual(code, 2 if defect else 0, lines)
                        if not defect:
                            self.assertIn("priced spend: 5.00 USD (this run 5.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)", lines)
                    finally:
                        self.directory = original

    def test_recovery_refuses_changed_evidence_and_unresolved_costs(self) -> None:
        cases = ("missing snapshot", "unknown price", "contradictory counters", "build", "source", "rewrite", "replacement",
                 "request identities", "unidentified request", "dollars", "count", "earlier gap", "start", "end", "older end")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original = self.directory
                self.directory = folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    if case in ("earlier gap", "unidentified request"):
                        if case == "earlier gap":
                            first["parent"]["coverageDefect"]["cause"] = "compaction-unmatched"
                        else:
                            first["parent"]["requests"] = 2
                        code, lines = self.run_script(first, *arguments, "--snapshot-recovery", "first")
                        self.assertEqual(code, 2, lines)
                        continue
                    self.observe_first(first, arguments)
                    if case == "missing snapshot":
                        second = first
                    elif case == "unknown price":
                        second["combined"].update(unpricedRequests=1, unpricedModels=["unknown"])
                    elif case == "contradictory counters":
                        second["parent"]["coverageDefect"] = {"cause": "bill-conflict"}
                    elif case == "build":
                        second["buildCommit"] = "b" * 40
                    elif case == "source":
                        second["source"]["sessionId"] = "other"
                    elif case == "rewrite":
                        with open(first["source"]["file"], "rb+") as source:
                            source.write(b" ")
                    elif case == "replacement":
                        path = first["source"]["file"]
                        os.rename(path, path + ".old")
                        with open(path + ".old", "rb") as source, open(path, "wb") as replacement:
                            replacement.write(source.read())
                    elif case == "request identities":
                        with open(first["source"]["file"], "a", encoding="utf-8") as source:
                            source.write(json.dumps({"type": "token_usage_record", "timestamp": "2026-01-01T00:01:30Z",
                                                    "payload": {"thread_id": "coordinator", "response_id": "extra"}}) + "\n")
                    elif case == "dollars":
                        second["combined"]["pricedCostUsd"] = 6
                    elif case == "count":
                        second["combined"]["requests"] = 2
                    else:
                        key = "since" if case == "start" else "until"
                        value = {"start": "2026-01-01T00:00:30Z", "end": "2026-01-01T00:02:30Z",
                                 "older end": "2026-01-01T00:01:30Z"}[case]
                        second["window"][key] = value
                        arguments[arguments.index("--" + key) + 1] = value
                    code, lines = self.recover(second, arguments)
                    self.assertEqual(code, 2, lines)
                    self.assertIn("snapshot recovery refused", lines[0])
                    self.assertNotEqual(self.recover(second, arguments)[0], 0)
                finally:
                    self.directory = original

    def test_recovery_needs_a_later_model_request_and_no_intervening_launch(self) -> None:
        for case in ("same request", "no completed request", "worker launched", "same observation time"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    self.observe_first(first, arguments)
                    request = "first" if case == "same request" else "missing" if case == "no completed request" else "later"
                    flags = [] if case == "worker launched" else ["--no-worker-launches"]
                    stamp = "2026-01-01T00:03:00Z" if case == "same observation time" else "2026-01-01T00:05:00Z"
                    with patch.object(stop_check, "now", return_value=stamp):
                        code, lines = self.run_script(second, *arguments, "--snapshot-recovery", "observe",
                                                      "--recovery-request", request, *flags)
                    self.assertEqual(code, 2, lines)
                finally:
                    self.directory = original

    def test_recovery_reserves_every_current_commitment_before_admission(self) -> None:
        for case in ("recovery", "coordinator", "check", "verification", "workers", "close", "negative bound", "insufficient", "ceiling", "absolute"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    self.observe_first(first, arguments)
                    if case in ("recovery", "coordinator", "check", "verification", "workers", "close", "negative bound"):
                        path = arguments[arguments.index("--recovery-commitments") + 1]
                        with open(path, encoding="utf-8") as file:
                            commitments = json.load(file)
                        if case == "negative bound":
                            commitments["coordinator"] = -1
                        else:
                            commitments.pop(case)
                        self.write("commitments.json", commitments)
                    elif case == "insufficient":
                        arguments[arguments.index("--post-cutoff-reserve") + 1] = "4"
                    else:
                        arguments[arguments.index("--ceiling") + 1] = "13" if case == "ceiling" else "6.5"
                    code, lines = self.recover(second, arguments)
                    self.assertEqual(code, 1 if case in ("ceiling", "absolute") else 2, lines)
                finally:
                    self.directory = original

    def test_recovery_deduplicates_dispatched_sources_and_requires_both_bounds(self) -> None:
        for case in ("same window", "wrong start", "wrong end", "missing bound"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.recovery_fixture()
                    _, entry, dispatched = self.supplemental_fixture(first, 1)
                    arguments += ["--dispatched-cost", dispatched]
                    self.observe_first(first, arguments)
                    if case == "wrong start":
                        entry["since"] = "2025-12-31T23:00:00Z"
                    elif case == "wrong end":
                        entry["until"] = "2026-01-01T00:01:30Z"
                    elif case == "missing bound":
                        entry.pop("since")
                    self.write("dispatched.json", {"sources": [entry, entry, dict(entry, sessionId="coordinator")]})
                    code, lines = self.recover(second, arguments)
                    self.assertEqual(code, 0 if case == "same window" else 2, lines)
                    if case == "same window":
                        self.assertIn("priced spend: 6.00 USD (this run 6.00 USD, of it dispatched 1.00 USD, earlier runs 0.00 USD)", lines)
                finally:
                    self.directory = original

    def linked_recovery_fixture(self) -> tuple[dict, dict, list[str]]:
        first, second, arguments = self.recovery_fixture()
        child_file = self.write("child.jsonl", json.dumps({"type": "token_usage_record", "timestamp": "2026-01-01T00:01:00Z",
                                                        "payload": {"thread_id": "child", "response_id": "child-request"}}) + "\n")
        child = dict(sessionId="child", kind="codex-thread", file=child_file, requests=1, unpricedRequests=0,
                     pricedCostUsd=1, costUsd=1, coverage="recorded-reconciled")
        for fixture in (first, second):
            fixture["children"] = [dict(child)]
            fixture["combined"].update(requests=2, pricedCostUsd=6)
        second["combined"]["costUsd"] = 6
        return first, second, arguments

    def test_recovery_keeps_linked_codex_threads_in_the_same_window(self) -> None:
        first, second, arguments = self.linked_recovery_fixture()
        self.observe_first(first, arguments)
        code, lines = self.recover(second, arguments)
        self.assert_verdict(code, lines, "continue")
        self.assertIn("priced spend: 6.00 USD (this run 6.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)", lines)

    def test_recovery_accepts_an_identified_codex_child_only_snapshot_defect(self) -> None:
        first, second, arguments = self.linked_recovery_fixture()
        first["parent"] = dict(second["parent"])
        first["children"][0].update(costUsd=None, requestCountExact=False, coverage="billed-with-unknown-coverage",
                                     coverageDefect={"cause": "snapshot-unmatched", "since": "2026-01-01T00:01:00Z"})
        self.observe_first(first, arguments)
        code, lines = self.recover(second, arguments)
        self.assert_verdict(code, lines, "continue")

    def mixed_recovery_fixture(self, kind: str = "claude-session") -> tuple[dict, dict, list[str]]:
        first, second, arguments = self.recovery_fixture()
        message = {"type": "assistant", "timestamp": "2026-01-01T00:01:30Z", "sessionId": "reviewer",
                   "message": {"id": "claude-request", "model": "claude-sonnet-5-20260810",
                               "usage": {"input_tokens": 100, "output_tokens": 50,
                                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
        child = dict(kind=kind, sessionId="reviewer", file=self.write("claude.jsonl", json.dumps(message) + "\n"),
                     requests=1, pricedRequests=1, unpricedRequests=0, pricedCostUsd=1, costUsd=1)
        for fixture in (first, second):
            fixture["children"] = [dict(child)]
            fixture["combined"].update(requests=2, pricedCostUsd=6)
        second["combined"]["costUsd"] = 6
        return first, second, arguments

    def test_mixed_claude_child_allows_delayed_codex_snapshot_admission(self) -> None:
        for kind in ("claude-session", "claude-subagent"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.mixed_recovery_fixture(kind)
                    with patch.object(stop_check, "now", return_value="2026-01-01T00:03:00Z"):
                        code, lines = self.run_script(first, *arguments)
                    self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
                    with open(first["source"]["file"], "a", encoding="utf-8") as source:
                        source.write(json.dumps({"type": "event_msg", "timestamp": "2026-01-01T00:03:01Z",
                                                 "payload": {"type": "token_count", "info": {
                                                     "last_token_usage": {"total_tokens": 10},
                                                     "total_token_usage": {"total_tokens": 10}}}}) + "\n")
                    self.append_later_request(first)
                    code, lines = self.recover(second, arguments)
                    self.assert_verdict(code, lines, "continue")
                    self.assertIn("priced spend: 6.00 USD (this run 6.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)", lines)
                    with open(os.path.join(folder, "recovery", stop_check.RECOVERY_FILE), encoding="utf-8") as saved:
                        evidence = json.load(saved)
                    self.assertEqual([row["report"] for row in evidence["observations"]], [first, second])
                    self.assertEqual(evidence["observations"][0]["sources"][1]["summary"], first["children"][0])
                    self.assertNotEqual(self.recover(second, arguments)[0], 0)
                finally:
                    self.directory = original

    def test_mixed_recovery_refuses_changed_or_incomplete_claude_children(self) -> None:
        cases = ("append", "rewrite", "replacement", "identity", "file", "kind", "count", "priced count", "dollars",
                 "usage", "model", "coverage", "defect", "inexact", "unpriced", "missing price", "summary",
                 "omitted child", "missing snapshot", "build", "reserve")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, second, arguments = self.mixed_recovery_fixture()
                    self.observe_first(first, arguments)
                    child = second["children"][0]
                    path = child["file"]
                    if case == "append":
                        with open(path, "a", encoding="utf-8") as source:
                            source.write("{}\n")
                    elif case == "rewrite":
                        with open(path, "rb+") as source:
                            source.write(b" ")
                    elif case == "replacement":
                        os.rename(path, path + ".old")
                        with open(path + ".old", "rb") as source, open(path, "wb") as replacement:
                            replacement.write(source.read())
                    elif case == "identity":
                        child["sessionId"] = "other"
                    elif case == "file":
                        with open(path, encoding="utf-8") as source:
                            child["file"] = self.write("other.jsonl", source.read())
                    elif case == "kind":
                        child["kind"] = "claude-subagent"
                    elif case == "count":
                        child.update(requests=2, pricedRequests=2)
                    elif case == "priced count":
                        child["pricedRequests"] = 0
                    elif case == "dollars":
                        child.update(costUsd=2, pricedCostUsd=2)
                    elif case == "usage":
                        child["unpricedRequests"] = 1
                    elif case == "model":
                        child["unpricedModels"] = ["unknown"]
                    elif case == "coverage":
                        child["coverage"] = "recorded-gaps"
                    elif case == "defect":
                        child["coverageDefect"] = {"cause": "snapshot-unmatched"}
                    elif case == "inexact":
                        child["requestCountExact"] = False
                    elif case == "unpriced":
                        child["costUsd"] = None
                    elif case == "missing price":
                        child.pop("costUsd")
                    elif case == "summary":
                        child["outputTokens"] = 999
                    elif case == "omitted child":
                        second["children"] = []
                    elif case == "missing snapshot":
                        second = first
                    elif case == "build":
                        second["buildCommit"] = "b" * 40
                    elif case == "reserve":
                        arguments[arguments.index("--post-cutoff-reserve") + 1] = "4"
                    code, lines = self.recover(second, arguments)
                    self.assertEqual(code, 2, lines)
                    self.assertIn("snapshot recovery refused", lines[0])
                finally:
                    self.directory = original

    def test_mixed_recovery_requires_complete_claude_children_at_first_observation(self) -> None:
        for case in ("unknown provider", "unidentified", "inexact", "unpriced", "missing price", "unknown model",
                     "coverage gap", "coverage defect", "priced count", "negative price", "claude parent"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                original, self.directory = self.directory, folder
                try:
                    first, _, arguments = self.mixed_recovery_fixture()
                    child = first["children"][0]
                    if case == "unknown provider":
                        child["kind"] = "unknown"
                    elif case == "unidentified":
                        child.pop("sessionId")
                    elif case == "inexact":
                        child["requestCountExact"] = False
                    elif case == "unpriced":
                        child["costUsd"] = None
                    elif case == "missing price":
                        child.pop("pricedCostUsd")
                    elif case == "unknown model":
                        child["unpricedModels"] = ["unknown"]
                    elif case == "coverage gap":
                        child["coverage"] = "recorded-gaps"
                    elif case == "coverage defect":
                        child["coverageDefect"] = {"cause": "compaction-unmatched"}
                    elif case == "priced count":
                        child["pricedRequests"] = 0
                    elif case == "negative price":
                        child.update(costUsd=-1, pricedCostUsd=-1)
                    elif case == "claude parent":
                        first["source"].update(kind="claude", file=child["file"], sessionId=child["sessionId"])
                    code, lines = self.run_script(first, *arguments, "--snapshot-recovery", "first")
                    self.assertEqual(code, 2, lines)
                    self.assertIn("snapshot recovery refused", lines[0])
                finally:
                    self.directory = original

    def test_continues_at_51_requests_and_70000_tokens_under_the_ceiling(self) -> None:
        code, lines = self.run_script(
            report(requests=51, context_first=30000, context_max=100000, priced=12.0),
            "--ceiling", "20", "--opening-context", "30000",
        )
        self.assert_verdict(code, lines, "continue")
        self.assertIn("requests: 51", lines)
        self.assertIn("context growth: 70,000 tokens above opening 30,000 tokens", lines)

    def test_accepts_arc_cost_schema_versions_2_and_3_with_the_same_verdict(self) -> None:
        outputs = []
        for version in (2, 3):
            with self.subTest(schemaVersion=version):
                fixture = report(requests=51, priced=12.0)
                fixture["schemaVersion"] = version
                code, lines = self.run_script(fixture, "--ceiling", "20", "--opening-context", "30000")
                self.assert_verdict(code, lines, "continue")
                outputs.append(lines)
        self.assertEqual(outputs[0], outputs[1])

    def test_rejects_other_arc_cost_schema_versions(self) -> None:
        for version in (1, 4, 999):
            with self.subTest(schemaVersion=version):
                fixture = report()
                fixture["schemaVersion"] = version
                code, lines = self.run_script(fixture, "--ceiling", "20")
                self.assertEqual(code, 2, lines)
                self.assertIn("schema version 2 or 3", lines[0])

    def test_stops_at_twice_the_ceiling(self) -> None:
        code, lines = self.run_script(report(priced=40.0), "--ceiling", "20")
        self.assert_verdict(code, lines, "stop: absolute stop reached")

    def test_earlier_spend_counts_toward_twice_the_ceiling(self) -> None:
        code, lines = self.run_script(report(priced=25.0), "--ceiling", "20", "--earlier-spend", "15")
        self.assert_verdict(code, lines, "stop: absolute stop reached")

    def test_stops_a_non_converging_fix_round_above_the_ceiling(self) -> None:
        code, lines = self.run_script(
            report(priced=25.0), "--ceiling", "20", "--operation", "fix-round",
            "--round", "review:3", "--round", "correction:3",
            "--continuing-authorization", "--replan-done",
        )
        self.assert_verdict(code, lines, "stop: ceiling before a fix round that is not converging")

    def test_continues_other_steps_above_the_ceiling(self) -> None:
        for operation, rounds in (("re-review", ["--round", "review:3", "--round", "correction:3"]),
                                  ("fix-round", ["--round", "review:3", "--round", "correction:2"])):
            with self.subTest(operation=operation):
                code, lines = self.run_script(
                    report(priced=25.0), "--ceiling", "20", "--operation", operation, *rounds,
                )
                self.assert_verdict(code, lines, "continue")

    def test_stops_a_non_converging_fix_round_without_continuing_authorization(self) -> None:
        code, lines = self.run_script(
            report(priced=5.0), "--ceiling", "20", "--operation", "fix-round",
            "--round", "review:3", "--round", "carry:0", "--round", "correction:3",
        )
        self.assert_verdict(code, lines, "stop: not converging")

    def test_continues_a_non_converging_fix_round_after_a_granted_replan(self) -> None:
        code, lines = self.run_script(
            report(priced=5.0), "--ceiling", "20", "--operation", "fix-round",
            "--round", "review:3", "--round", "correction:3",
            "--continuing-authorization", "--replan-done",
        )
        self.assert_verdict(code, lines, "continue")

    def test_two_correction_caused_regressions_stop_to_diagnose(self) -> None:
        code, lines = self.run_script(
            report(priced=5.0), "--ceiling", "20", "--operation", "fix-round",
            "--round", "review:5", "--round", "correction:3:1", "--round", "correction:2:1",
        )
        self.assert_verdict(code, lines, "stop: diagnose")

    def test_admission_stops_a_third_launch_past_the_absolute_stop(self) -> None:
        code, lines = self.run_script(
            report(priced=35.0), "--ceiling", "40", "--absolute-stop", "80",
            "--outstanding", "25", "--outstanding", "25", "--launch-allowance", "25",
        )
        self.assert_verdict(code, lines, "stop: admission would pass the absolute stop")
        self.assertIn("outstanding allowances: 50.00 USD, this launch 25.00 USD", lines)

    def test_admission_continues_a_launch_that_fits(self) -> None:
        code, lines = self.run_script(
            report(priced=35.0), "--ceiling", "40", "--absolute-stop", "80",
            "--outstanding", "25", "--launch-allowance", "15",
        )
        self.assert_verdict(code, lines, "continue")

    def test_unpriced_usage_stops_paid_work_until_an_allowance_covers_it(self) -> None:
        fixture = report(priced=5.0, unpriced_requests=2)
        code, lines = self.run_script(fixture, "--ceiling", "20", "--launch-allowance", "4")
        self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
        self.assertIn("coverage: incomplete", lines)
        code, lines = self.run_script(
            fixture, "--ceiling", "20", "--launch-allowance", "4", "--unpriced-allowance-left", "5",
        )
        self.assert_verdict(code, lines, "continue")

    def test_a_running_worker_admitted_from_the_allowance_does_not_count_twice(self) -> None:
        fixture = report(priced=5.0, unpriced_requests=2)
        code, lines = self.run_script(
            fixture, "--ceiling", "20", "--outstanding", "4", "--launch-allowance", "4",
            "--unpriced-allowance-left", "5",
        )
        self.assert_verdict(code, lines, "continue")
        code, lines = self.run_script(
            fixture, "--ceiling", "20", "--outstanding", "4", "--launch-allowance", "4",
            "--unpriced-allowance-left", "1",
        )
        self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")

    def test_incomplete_coverage_blocks_a_paid_launch(self) -> None:
        unknown_cost = report(priced=5.0)
        unknown_cost["combined"]["costUsd"] = None
        inexact_count = report(priced=5.0)
        inexact_count["combined"]["requestCountExact"] = False
        for name, fixture in (("costUsd null", unknown_cost), ("requestCountExact false", inexact_count)):
            with self.subTest(name):
                code, lines = self.run_script(fixture, "--ceiling", "20", "--launch-allowance", "4")
                self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")

    def test_dispatched_cost_from_another_window_stops_admission(self) -> None:
        dispatched = self.write("dispatched-cost.json", {"sources": [
            {"sessionId": "codex-review", "pricedCostUsd": 3.0, "costUsd": 3.0,
             "since": "2026-01-01T00:00:00Z", "until": "2026-01-01T01:00:00Z"},
        ]})
        code, lines = self.run_script(
            report(priced=10.0, window_until="2026-01-01T02:00:00Z"), "--ceiling", "20",
            "--dispatched-cost", dispatched, "--launch-allowance", "4",
        )
        self.assertEqual(code, 2, lines)
        self.assertIn("but the cost report ends at 2026-01-01T02:00:00Z", lines[0])

    def test_a_dispatched_source_without_a_price_prints_unavailable(self) -> None:
        dispatched = self.write("dispatched-cost.json", {"sources": [
            {"sessionId": "codex-review", "pricedCostUsd": None, "costUsd": None},
        ]})
        code, lines = self.run_script(
            report(priced=10.0), "--ceiling", "20", "--dispatched-cost", dispatched,
        )
        self.assert_verdict(code, lines, "continue")
        self.assertIn(
            "priced spend: 10.00 USD (this run 10.00 USD, of it dispatched unavailable, earlier runs 0.00 USD)",
            lines,
        )

    def test_a_missing_run_folder_exits_2_with_the_block(self) -> None:
        missing = os.path.join(self.directory, "missing")
        for fixture in (report(), report(context_first=None)):
            with self.subTest(context_first=fixture["parent"]["contextFirst"]):
                code, lines = self.run_script(fixture, "--ceiling", "20", "--run-folder", missing)
                self.assertEqual(code, 2, lines)
                self.assertTrue(lines[0].startswith("error: cannot write"), lines)
                self.assertIn("ceiling: 20.00 USD", lines)

    def test_a_stop_keeps_its_rule_when_the_block_cannot_be_saved(self) -> None:
        folder = os.path.join(self.directory, "run")
        os.makedirs(os.path.join(folder, stop_check.BLOCK_FILE))
        code, lines = self.run_script(report(priced=40.0), "--ceiling", "20", "--run-folder", folder)
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("error: cannot write"), lines)
        self.assertIn("Stop rule: absolute stop reached", lines[0])

    def test_dispatched_sources_count_once(self) -> None:
        dispatched = self.write("dispatched-cost.json", {"sources": [
            {"sessionId": "linked-child", "pricedCostUsd": 7.0, "costUsd": 7.0},
            {"sessionId": "codex-review", "pricedCostUsd": 3.0, "costUsd": 3.0,
             "since": "2026-01-01T00:00:00Z", "until": "2026-01-01T02:00:00.000Z"},
            {"sessionId": "codex-review", "pricedCostUsd": 3.0, "costUsd": 3.0},
        ]})
        code, lines = self.run_script(
            report(priced=10.0, children=("linked-child",), window_until="2026-01-01T02:00:00Z"),
            "--ceiling", "20", "--dispatched-cost", dispatched,
        )
        self.assert_verdict(code, lines, "continue")
        self.assertIn(
            "priced spend: 13.00 USD (this run 13.00 USD, of it dispatched 3.00 USD, earlier runs 0.00 USD)",
            lines,
        )

    def test_inexact_request_count_prints_unavailable(self) -> None:
        code, lines = self.run_script(report(request_count_exact=False), "--ceiling", "20")
        self.assert_verdict(code, lines, "continue")
        self.assertIn("requests: unavailable", lines)

    def test_run_folder_keeps_the_opening_context_from_the_first_call(self) -> None:
        folder = os.path.join(self.directory, "run")
        os.mkdir(folder)
        self.run_script(report(context_first=30000, context_max=30000), "--ceiling", "20", "--run-folder", folder)
        code, lines = self.run_script(
            report(context_first=90000, context_max=95000), "--ceiling", "20", "--run-folder", folder,
        )
        self.assert_verdict(code, lines, "continue")
        self.assertIn("context growth: 65,000 tokens above opening 30,000 tokens", lines)
        with open(os.path.join(folder, "stop-check.txt"), encoding="utf-8") as log:
            self.assertEqual(log.read().count("\ncontinue\n"), 2)

    def test_cache_expiry_names_the_rebuild_premium_and_the_largest_child(self) -> None:
        def expiry(returns: int, premium: float | None, coverage: str = "complete") -> dict:
            return {"coverage": coverage, "returns": returns, "extraCostAfterExpiryUsd": premium,
                    "inputCostAfterExpiryUsd": None if premium is None else premium + 1.0}

        fixture = report(priced=50.0, children=("waiting-reviewer", "quick-reviewer"))
        fixture["children"][0]["cacheExpiry"] = expiry(24, 28.0)
        fixture["children"][1]["cacheExpiry"] = expiry(1, 0.5)
        fixture["combined"]["cacheExpiry"] = expiry(25, 28.5)
        code, lines = self.run_script(fixture, "--ceiling", "60")
        self.assert_verdict(code, lines, "continue")
        self.assertEqual(
            lines[-1],
            "cache_expiry: 28.50 USD over 25 returns, 57.0% of the Arc report's priced spend, "
            "largest child waiting-reviewer 28.00 USD, flag: true",
        )
        # Dispatched spend carries no premium, so it stays out of the share.
        dispatched = self.write("dispatched-cost.json", {"sources": [
            {"sessionId": "codex-review", "pricedCostUsd": 50.0, "costUsd": 50.0,
             "since": "2026-01-01T00:00:00Z", "until": "2026-01-01T01:00:00Z"},
        ]})
        fixture["window"]["until"] = "2026-01-01T01:00:00Z"
        code, lines = self.run_script(fixture, "--ceiling", "200", "--dispatched-cost", dispatched)
        self.assertIn("57.0% of the Arc report's priced spend", lines[-1])
        # Each threshold sets the flag alone, and a premium below both clears it.
        for name, priced, premium, share, flag in (
            ("share alone", 10.0, 1.5, "15.0%", "true"),
            ("dollars alone", 100.0, 3.0, "3.0%", "true"),
            ("neither", 50.0, 1.5, "3.0%", "false"),
        ):
            with self.subTest(name):
                small = report(priced=priced, children=("quick-reviewer",))
                small["children"][0]["cacheExpiry"] = expiry(2, premium)
                small["combined"]["cacheExpiry"] = expiry(2, premium)
                code, lines = self.run_script(small, "--ceiling", "300")
                self.assertEqual(
                    lines[-1],
                    f"cache_expiry: {premium:.2f} USD over 2 returns, {share} of the Arc report's priced spend, "
                    f"largest child quick-reviewer {premium:.2f} USD, flag: {flag}",
                )
        unknown_coverage = json.loads(json.dumps(fixture))
        unknown_coverage["combined"]["cacheExpiry"] = expiry(25, None, "unknown-request-coverage")
        partial_cost = json.loads(json.dumps(small))
        partial_cost["combined"]["costUsd"] = None
        for name, case, expected in (
            ("no cacheExpiry", report(priced=50.0), "unknown, flag: unknown"),
            ("coverage incomplete", unknown_coverage,
             "unknown (25 returns, premium unpriced: unknown-request-coverage), flag: unknown"),
            ("combined cost incomplete", partial_cost,
             "unknown (2 returns, partial premium 1.5000 USD, combined cost incomplete), flag: unknown"),
        ):
            with self.subTest(name):
                code, lines = self.run_script(case, "--ceiling", "60")
                self.assert_verdict(code, lines, "continue")
                self.assertEqual(lines[-1], f"cache_expiry: {expected}")

    def test_saved_codex_report_shows_identified_requests_and_why_figures_are_unknown(self) -> None:
        # Minimized from the codex-skill-011245 run's qualification-5-run-cost.json.
        def codex_report(defect: bool) -> dict:
            expiry = {"coverage": "unknown-request-coverage", "returns": 3, "inputCostAfterExpiryUsd": None,
                      "extraCostAfterExpiryUsd": None, "byLifetime": {"5m": 0, "30m": 3, "1h": 0},
                      "longestGapSeconds": 4303}
            parent = {"requests": 450, "pricedRequests": 450, "unpricedRequests": 0, "requestCountExact": False,
                      "coverage": "billed-with-unknown-coverage", "costUsd": None, "pricedCostUsd": 13.064,
                      "contextMean": None, "contextMax": None, "contextFirst": None, "unpricedModels": [],
                      "cacheExpiry": dict(expiry, returns=1, byLifetime={"5m": 0, "30m": 1, "1h": 0})}
            if defect:
                parent["coverageDefect"] = {"kind": "billed-with-unknown-coverage",
                                            "since": "2026-10-18T02:32:51.021Z", "cause": "compaction-unmatched"}
            child = {"sessionId": "01a14ca0", "requests": 18, "unpricedRequests": 0, "coverage": "recorded-reconciled",
                     "costUsd": 2.8809, "pricedCostUsd": 2.8809, "contextMax": 120902, "unpricedModels": []}
            return {
                "schemaVersion": 2,
                "source": {"kind": "codex", "sessionId": "01a14c8e"},
                "window": {"since": "2026-10-18T01:09:19.000Z", "until": "2026-10-18T08:14:26.000Z"},
                "parent": parent,
                "children": [child],
                "combined": {"requests": 468, "unpricedRequests": 0, "requestCountExact": False,
                             "coverage": "billed-with-unknown-coverage", "costUsd": None, "pricedCostUsd": 15.9449,
                             "unpricedModels": [], "cacheExpiry": expiry},
            }

        window = ("--since", "2026-10-18T01:09:19Z", "--until", "2026-10-18T08:14:26Z")
        for name, defect, detail in (
            ("with coverageDefect", True,
             "parent billed-with-unknown-coverage since 2026-10-18T02:32:51.021Z (compaction-unmatched)"),
            ("older report", False, "parent billed-with-unknown-coverage"),
        ):
            with self.subTest(name):
                code, lines = self.run_script(
                    codex_report(defect), "--ceiling", "40", "--opening-context", "28667",
                    "--arc-cost", "C:/skills/arc_cost.py", *window,
                )
                self.assert_verdict(code, lines, "continue")
                self.assertIn("requests: 450 identified, count not exact", lines)
                self.assertIn(
                    "context growth: unavailable above opening 28,667 tokens "
                    "(parent coverage billed-with-unknown-coverage)",
                    lines,
                )
                self.assertIn(
                    "priced spend: 15.94 USD (this run 15.94 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)",
                    lines,
                )
                self.assertIn("coverage: billed-with-unknown-coverage", lines)
                shown = [line for line in lines if line.startswith("coverage detail: ")]
                self.assertEqual(len(shown), 1, lines)
                self.assertTrue(
                    shown[0].startswith(f"coverage detail: {detail}, 1 of 1 children with complete coverage. Reproduce: "),
                    shown[0],
                )
                self.assertTrue(
                    shown[0].endswith(
                        '"C:/skills/arc_cost.py" 01a14c8e --since 2026-10-18T01:09:19Z --until 2026-10-18T08:14:26Z'
                    ),
                    shown[0],
                )
                self.assertEqual(
                    lines[-1],
                    "cache_expiry: unknown (3 returns after 30m lifetimes, longest 4303 s, "
                    "premium unpriced: unknown-request-coverage), flag: unknown",
                )
        # The display lines move no gate and no ledger figure.
        code, lines = self.run_script(codex_report(True), "--ceiling", "40", "--launch-allowance", "2", *window)
        self.assert_verdict(code, lines, "stop: unpriced usage blocks paid work")
        code, lines = self.run_script(
            codex_report(True), "--ceiling", "40", "--launch-allowance", "2", "--opening-context", "28667",
            "--json", *window,
        )
        figures = json.loads("\n".join(lines))
        self.assertEqual(code, 1, lines)
        self.assertEqual(figures["rule"], "unpriced usage blocks paid work")
        for key, value in (("requests", "unavailable"), ("context_growth", "unavailable"), ("cache_expiry", "unknown"),
                           ("spend", 15.9449), ("coverage", "billed-with-unknown-coverage")):
            self.assertEqual(figures[key], value, key)

    def test_request_qualifiers_follow_the_parent_coverage(self) -> None:
        for coverage, expected in (
            ("legacy-snapshots", "requests: 12 observed snapshots, count not exact"),
            ("recorded-gaps", "requests: 12 identified, count not exact"),
            ("unknown-request-coverage", "requests: unavailable (parent coverage unknown-request-coverage)"),
        ):
            with self.subTest(coverage):
                fixture = report(requests=12, request_count_exact=False)
                fixture["parent"]["coverage"] = coverage
                fixture["combined"]["coverage"] = coverage
                fixture["combined"]["costUsd"] = None
                if coverage == "legacy-snapshots":
                    fixture["parent"]["coverageDefect"] = {"kind": coverage, "since": None, "cause": "legacy-snapshots"}
                code, lines = self.run_script(fixture, "--ceiling", "20")
                self.assert_verdict(code, lines, "continue")
                self.assertIn(expected, lines)
                if coverage == "legacy-snapshots":
                    self.assertTrue(
                        any(line.startswith("coverage detail: parent legacy-snapshots (legacy-snapshots). ")
                            for line in lines),
                        lines,
                    )

    def test_unpriced_models_name_the_coverage_detail(self) -> None:
        fixture = report(priced=5.0, unpriced_requests=2)
        fixture["combined"]["unpricedModels"] = ["gpt-9-preview"]
        code, lines = self.run_script(fixture, "--ceiling", "20", "--since", "2026-01-01T00:00:00Z")
        self.assert_verdict(code, lines, "continue")
        self.assertTrue(
            any(line.startswith("coverage detail: 2 unpriced requests from gpt-9-preview. Reproduce: ")
                for line in lines),
            lines,
        )

    def test_a_child_without_coverage_but_with_an_inexact_count_is_not_complete(self) -> None:
        fixture = report(priced=5.0, unpriced_requests=2)
        fixture["children"] = [
            {"sessionId": "old1", "requests": 4, "requestCountExact": False, "pricedCostUsd": 0.0},
            {"sessionId": "old2", "requests": 3, "costUsd": None, "pricedCostUsd": 0.0},
            {"sessionId": "good", "requests": 2, "coverage": "recorded-reconciled", "costUsd": 1.0,
             "pricedCostUsd": 1.0},
        ]
        code, lines = self.run_script(fixture, "--ceiling", "20", "--since", "2026-01-01T00:00:00Z")
        self.assert_verdict(code, lines, "continue")
        self.assertTrue(
            any(line.startswith(
                "coverage detail: child old1 coverage unknown (request count not exact), "
                "child old2 coverage unknown (cost unpriced), 2 unpriced requests, "
                "1 of 3 children with complete coverage. Reproduce: ") for line in lines),
            lines,
        )

    def test_an_empty_window_prices_at_zero_unless_a_coverage_defect_intersects_it(self) -> None:
        # Arc's output for a window that holds no request and no coverage defect.
        expiry = {"coverage": "complete", "returns": 0, "inputCostAfterExpiryUsd": 0, "extraCostAfterExpiryUsd": 0,
                  "byLifetime": {"5m": 0, "30m": 0, "1h": 0}, "longestGapSeconds": None, "largest": []}
        empty = {"requests": 0, "pricedRequests": 0, "unpricedRequests": 0, "failedToolCalls": 0,
                 "failedToolCallRequests": 0, "failedToolCallCostUsd": 0, "fastModeUnpricedRequests": 0,
                 "cacheExpiry": expiry}
        fixture = {"schemaVersion": 2, "source": {"kind": "claude", "sessionId": "coordinator"},
                   "window": {"since": "2026-10-18T11:06:07.000Z", "until": None},
                   "parent": dict(empty), "children": [], "combined": dict(empty)}
        for arguments in ((), ("--launch-allowance", "4")):
            with self.subTest(arguments=arguments):
                code, lines = self.run_script(fixture, "--ceiling", "40", *arguments)
                self.assert_verdict(code, lines, "continue")
                self.assertIn("requests: 0", lines)
                self.assertIn(
                    "priced spend: 0.00 USD (this run 0.00 USD, of it dispatched 0.00 USD, earlier runs 0.00 USD)",
                    lines,
                )
                self.assertIn("coverage: complete", lines)
                self.assertEqual(
                    lines[-1],
                    "cache_expiry: 0.00 USD over 0 returns, 0.0% of the Arc report's priced spend, "
                    "largest child none, flag: false",
                )
        # An intersecting defect keeps a null total and an uncertain count with no selected request.
        defect = json.loads(json.dumps(fixture))
        for summary in (defect["parent"], defect["combined"]):
            summary.update({"coverage": "billed-with-unknown-coverage", "requestCountExact": False, "costUsd": None})
        defect["parent"]["coverageDefect"] = {"kind": "billed-with-unknown-coverage",
                                              "since": "2026-10-18T10:58:00.000Z", "cause": "snapshot-unmatched"}
        code, lines = self.run_script(defect, "--ceiling", "40")
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines[0], "error: the cost report has no combined.pricedCostUsd")
        self.assertTrue(any(line.startswith("priced spend: unavailable") for line in lines), lines)
        self.assertIn("coverage: billed-with-unknown-coverage", lines)
        self.assertTrue(
            any(line.startswith("coverage detail: parent billed-with-unknown-coverage since 2026-10-18T10:58:00.000Z "
                                "(snapshot-unmatched). Reproduce: ") for line in lines),
            lines,
        )

    def test_pricing_failure_exits_2_and_prints_unavailable_fields(self) -> None:
        failing = self.write("arc_cost.py", "import sys\nsys.exit('Arc cost command is unavailable.')\n")
        code, lines = self.run_script(
            None, "--session", "coordinator", "--since", "2026-01-01T00:00:00Z",
            "--arc-cost", failing, "--ceiling", "20",
        )
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("error: arc_cost.py exited 1"), lines)
        self.assertIn("requests: unavailable", lines)
        self.assertIn("context growth: unavailable above opening unavailable", lines)
        self.assertIn("coverage: unavailable", lines)
        self.assertIn("cache_expiry: unknown, flag: unknown", lines)
        self.assertTrue(any(line.startswith("priced spend: unavailable") for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
