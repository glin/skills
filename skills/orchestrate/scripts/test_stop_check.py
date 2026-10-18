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
