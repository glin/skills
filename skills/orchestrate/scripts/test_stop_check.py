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

    def test_continues_at_51_requests_and_70000_tokens_under_the_ceiling(self) -> None:
        code, lines = self.run_script(
            report(requests=51, context_first=30000, context_max=100000, priced=12.0),
            "--ceiling", "20", "--opening-context", "30000",
        )
        self.assert_verdict(code, lines, "continue")
        self.assertIn("requests: 51", lines)
        self.assertIn("context growth: 70,000 tokens above opening 30,000 tokens", lines)

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
        self.assertTrue(any(line.startswith("priced spend: unavailable") for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
