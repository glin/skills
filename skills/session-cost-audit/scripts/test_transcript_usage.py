#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(__file__), "transcript_usage.py")
SPEC = importlib.util.spec_from_file_location("transcript_usage", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

class TranscriptUsageTest(unittest.TestCase):
    def test_claude_repeated_request_counts_usage_once_and_keeps_all_tools(self) -> None:
        usage = {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 4}
        events = [
            {"type": "assistant", "requestId": "request-1", "message": {
                "model": "claude-sonnet-5", "usage": usage,
                "content": [{"type": "tool_use", "name": "Read"}],
            }},
            {"type": "assistant", "requestId": "request-1", "message": {
                "model": "claude-sonnet-5", "usage": usage,
                "content": [{"type": "tool_use", "name": "Grep"}],
            }},
        ]
        rows = MODULE.claude_requests(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tools"], ["Read", "Grep"])

    def test_claude_failed_tool_result_charges_the_request_that_made_it(self) -> None:
        usage = {"input_tokens": 10_000, "output_tokens": 2_000}
        events = [
            {"type": "assistant", "requestId": "request-1", "message": {
                "id": "msg-1", "model": "claude-sonnet-5", "usage": usage,
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash"}],
            }},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True, "content": "boom"},
            ]}},
            {"type": "assistant", "requestId": "request-2", "message": {
                "id": "msg-2", "model": "claude-sonnet-5", "usage": usage,
                "content": [{"type": "tool_use", "id": "toolu_2", "name": "Read"}],
            }},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_2", "is_error": False, "content": "ok"},
            ]}},
        ]
        rows = MODULE.claude_requests(events)
        self.assertEqual([row["failed_tools"] for row in rows], [1, 0])
        summary = MODULE.summarize(rows)
        self.assertEqual(summary["failed_tool_calls"], 1)
        self.assertEqual(summary["failed_tool_call_requests"], 1)
        self.assertAlmostEqual(summary["failed_tool_call_cost_usd"], (10_000 * 2 + 2_000 * 10) / 1_000_000)

    def test_codex_failed_tool_call_reports_a_count_with_no_cost(self) -> None:
        # Codex marks the failure on the item_completed event, never on the call's own output
        # record, and neither record names the other. No row can be charged for it.
        events = [
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6-terra"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec"}},
            {"type": "token_usage_record", "timestamp": "2026-09-12T10:00:00Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1",
                "usage": {"input_tokens": 100_000, "output_tokens": 20_000},
            }},
            {"type": "event_msg", "payload": {"type": "item_completed",
                                              "item": {"type": "CommandExecution", "status": "failed"}}},
        ]
        rows = MODULE.codex_requests(events)
        self.assertNotIn("failed_tools", rows[0])
        failed = MODULE.codex_failed_tool_items(events)
        self.assertEqual(failed, 1)
        summary = MODULE.summarize(rows, failed)
        self.assertEqual(summary["failed_tool_calls"], 1)
        self.assertEqual(summary["failed_tool_call_requests"], 0)
        self.assertIsNone(summary["failed_tool_call_cost_usd"])

    def test_codex_failed_tool_call_count_honors_the_window(self) -> None:
        # One failure sits before the window's since boundary, and one sits after it.
        events = [
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6-terra"}},
            {"type": "event_msg", "timestamp": "2026-09-12T09:00:00Z", "payload": {"type": "item_completed",
                                              "item": {"type": "CommandExecution", "status": "failed"}}},
            {"type": "token_usage_record", "timestamp": "2026-09-12T10:00:00Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1",
                "usage": {"input_tokens": 100_000, "output_tokens": 20_000},
            }},
            {"type": "event_msg", "timestamp": "2026-09-12T11:00:00Z", "payload": {"type": "item_completed",
                                              "item": {"type": "CommandExecution", "status": "failed"}}},
        ]
        since = MODULE.parse_time("2026-09-12T09:30:00Z")
        self.assertEqual(MODULE.codex_failed_tool_items(events), 2)
        self.assertEqual(MODULE.codex_failed_tool_items(events, since=since), 1)
        rows = MODULE.codex_requests(events)
        summary = MODULE.summarize(rows, MODULE.codex_failed_tool_items(events, since=since))
        self.assertEqual(summary["failed_tool_calls"], 1)

    def test_summarize_reports_failed_tool_calls_when_the_window_holds_no_requests(self) -> None:
        # A window can exclude every request row while a Codex failure still sits inside it.
        # The count that read_requests already computed for the window must still surface.
        summary = MODULE.summarize([], 3)
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["failed_tool_calls"], 3)
        self.assertEqual(summary["failed_tool_call_requests"], 0)
        self.assertIsNone(summary["failed_tool_call_cost_usd"])

    def test_summarize_zero_rows_with_a_real_codex_zero_count_prices_no_failure(self) -> None:
        # A Codex transcript reports a real zero failure count, never None, even when the
        # window holds no rows. Codex never charges a failure to a request, so the dollar
        # figure still reads null.
        summary = MODULE.summarize([], 0)
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["failed_tool_calls"], 0)
        self.assertEqual(summary["failed_tool_call_requests"], 0)
        self.assertIsNone(summary["failed_tool_call_cost_usd"])

    def test_summarize_zero_rows_with_no_codex_count_prices_a_zero_failure(self) -> None:
        # A pure Claude transcript never passes a Codex count. The empty window then
        # computes its own failure figures over zero rows and produces a priced zero.
        summary = MODULE.summarize([])
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["failed_tool_calls"], 0)
        self.assertEqual(summary["failed_tool_call_requests"], 0)
        self.assertEqual(summary["failed_tool_call_cost_usd"], 0.0)

    def test_summarize_zero_row_return_carries_the_same_keys_as_the_non_empty_return(self) -> None:
        # A field added to one return and not the other must fail this comparison.
        events = [claude_record(1, "2026-09-12T10:00:00Z", input_tokens=10, output_tokens=2)]
        rows = MODULE.claude_requests(events)
        self.assertEqual(set(MODULE.summarize([]).keys()), set(MODULE.summarize(rows).keys()))

    def test_codex_failed_non_tool_item_does_not_raise_the_count(self) -> None:
        events = [
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6-terra"}},
            {"type": "event_msg", "payload": {"type": "item_completed",
                                              "item": {"type": "AgentMessage", "status": "failed"}}},
            {"type": "token_usage_record", "timestamp": "2026-09-12T10:00:00Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1",
                "usage": {"input_tokens": 100_000, "output_tokens": 20_000},
            }},
        ]
        rows = MODULE.codex_requests(events)
        failed = MODULE.codex_failed_tool_items(events)
        self.assertEqual(failed, 0)
        summary = MODULE.summarize(rows, failed)
        self.assertEqual(summary["failed_tool_calls"], 0)
        self.assertEqual(summary["failed_tool_call_requests"], 0)
        self.assertIsNone(summary["failed_tool_call_cost_usd"])

    def test_codex_records_split_input_and_count_one_response_once(self) -> None:
        events = [
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6-sol"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "spawn_agent"}},
            {"type": "token_usage_record", "timestamp": "2026-09-12T10:00:00Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1", "usage": {
                    "input_tokens": 100, "cached_input_tokens": 70, "cache_write_input_tokens": 10,
                    "output_tokens": 20, "reasoning_output_tokens": 8,
                },
            }},
            {"type": "token_usage_record", "timestamp": "2026-09-12T10:00:01Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1", "usage": {"input_tokens": 999},
            }},
        ]
        rows = MODULE.codex_requests(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uncached_input"], 20)
        self.assertEqual(rows[0]["cache_read"], 70)
        self.assertEqual(rows[0]["write_30m"], 10)
        self.assertEqual(rows[0]["tools"], ["exec", "spawn_agent"])
        summary = MODULE.summarize(rows)
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["reasoning_output_tokens"], 8)
        self.assertEqual(summary["context_mean"], 100)
        self.assertIsNotNone(summary["cost_usd"])

    def test_unknown_model_reports_usage_without_inventing_a_price(self) -> None:
        summary = MODULE.summarize([{
            "model": "gpt-unknown", "timestamp": "2026-09-12T10:00:00Z",
            "uncached_input": 10, "cache_read": 0, "write_5m": 0, "write_1h": 0,
            "write_30m": 0, "output": 2, "reasoning_output": 0, "tools": [],
        }])
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["unpriced_requests"], 1)
        self.assertIsNone(summary["cost_usd"])
        self.assertEqual(summary["unpriced_models"], ["gpt-unknown"])

    def test_codex_auto_review_uses_gpt_5_4_rates_with_free_cache_writes(self) -> None:
        auto_review = MODULE.rate_for("codex-auto-review")
        gpt_5_4 = MODULE.RATES["gpt-5.4"].rate
        self.assertEqual((auto_review[0], auto_review[1], auto_review[5]), (gpt_5_4[0], gpt_5_4[1], gpt_5_4[5]))
        self.assertEqual(auto_review[2:5], (0.0, 0.0, 0.0))

    def test_gpt_6_sol_and_luna_price_short_and_long_context_requests(self) -> None:
        expected = {
            "gpt-6-sol": (2.0, 10.0, 2.5, 4.0, 2.5, 0.2),
            "gpt-6-luna": (0.1, 0.5, 0.125, 0.2, 0.125, 0.01),
        }
        for model, rate in expected.items():
            with self.subTest(model=model):
                self.assertEqual(MODULE.rate_key(model), model)
                self.assertEqual(MODULE.rate_for(model), rate)
                self.assertEqual(MODULE.documented_lifetime(model), "30m")
                self.assertEqual(MODULE.request_rate(model, None, 272_001),
                                 (rate[0] * 2, rate[1] * 1.5, rate[2] * 2, rate[3] * 2,
                                  rate[4] * 2, rate[5] * 2))
                self.assertIsNone(MODULE.rate_for(model + "-20260922"))
        self.assertIsNone(MODULE.rate_for("gpt-6-terra"))

    def test_long_context_cache_read_share_uses_the_long_rate(self) -> None:
        summary = MODULE.summarize([{
            "model": "gpt-6-sol", "timestamp": "2026-09-23T00:00:00Z",
            "uncached_input": 0, "cache_read": 300_000,
            "write_5m": 0, "write_1h": 0, "write_30m": 0,
            "output": 1_000, "reasoning_output": 0, "tools": [],
        }])
        self.assertEqual(summary["cost_usd"], 0.135)
        self.assertEqual(summary["cache_read_share_of_cost"], 0.889)

    def test_bedrock_gpt_6_sol_and_luna_use_provider_rates_and_cache_lifetime(self) -> None:
        expected = {
            "sol": (2.2, 11.0, 2.75, 4.4, 2.75, 0.22),
            "luna": (0.11, 0.55, 0.1375, 0.22, 0.1375, 0.011),
        }
        for family, regional in expected.items():
            with self.subTest(family=family):
                model = f"openai.gpt-6-{family}"
                self.assertEqual(MODULE.rate_for(model), regional)
                self.assertEqual(MODULE.rate_for("us." + model), regional)
                self.assertEqual(MODULE.rate_for("global." + model), MODULE.rate_for("gpt-6-" + family))
                self.assertEqual(MODULE.documented_lifetime(model), "30m")
                self.assertEqual(MODULE.request_rate(model, None, 272_001)[1], round(regional[1] * 1.5, 12))
                self.assertIsNone(MODULE.rate_for(model + "-extra"))

    def test_fable_subagent_request_is_priced(self) -> None:
        summary = MODULE.summarize([{
            "model": "claude-fable-5-1", "timestamp": "2026-09-17T03:00:00Z",
            "uncached_input": 1_000_000, "cache_read": 1_000_000, "write_5m": 0, "write_1h": 1_000_000,
            "write_30m": 0, "output": 1_000_000, "reasoning_output": 0, "tools": [],
        }])
        self.assertEqual(summary["unpriced_requests"], 0)
        self.assertAlmostEqual(summary["cost_usd"], 10 + 0.25 + 20 + 50)

    def test_fast_mode_turns_are_priced_at_the_fast_rate_with_cache_reads_and_writes(self) -> None:
        # Fast mode sells Opus 5.5 at $8 and $40, and Opus 5 and Opus 4.8 at $10 and $50. The
        # cache multiples apply on top of the fast input rate, including Opus 5.5's 0.05x read.
        for model, expected in [("claude-opus-5-5", 8 + 40 + 10 + 16 + 0.4),
                                ("claude-opus-5", 10 + 50 + 12.5 + 20 + 1),
                                ("claude-opus-4-8", 10 + 50 + 12.5 + 20 + 1)]:
            with self.subTest(model=model):
                events = [claude_record(1, "2026-09-22T10:00:00Z", model, input_tokens=1_000_000,
                                         output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                                         write_5m=1_000_000, write_1h=1_000_000, speed="fast")]
                rows = MODULE.claude_requests(events)
                self.assertEqual(rows[0]["speed"], "fast")
                self.assertAlmostEqual(MODULE.cost_of(rows[0]), expected)
                summary = MODULE.summarize(rows)
                self.assertEqual(summary["unpriced_requests"], 0)
                self.assertEqual(summary["fast_mode_unpriced_requests"], 0)
                self.assertEqual(summary["unpriced_models"], [])

    def test_fast_mode_turn_on_a_model_without_a_fast_rate_stays_unpriced(self) -> None:
        # Opus 4.7 sells no fast mode, so a fast row on it must not take the standard rate.
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-4-7",
                                 input_tokens=1_000_000, output_tokens=1_000_000, speed="fast")]
        rows = MODULE.claude_requests(events)
        self.assertIsNone(MODULE.cost_of(rows[0]))
        self.assertIsNone(MODULE.input_cost_of(rows[0]))
        summary = MODULE.summarize(rows)
        self.assertEqual(summary["unpriced_requests"], 1)
        # The model has a standard rate. Speed is why the turn went unpriced, so the model must
        # not appear in unpriced_models, which the reader takes as a list of rate gaps to fill in.
        self.assertEqual(summary["unpriced_models"], [])
        self.assertEqual(summary["fast_mode_unpriced_requests"], 1)

    def test_an_unknown_speed_stays_unpriced(self) -> None:
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5",
                                 input_tokens=1_000_000, output_tokens=1_000_000, speed="turbo")]
        self.assertIsNone(MODULE.cost_of(MODULE.claude_requests(events)[0]))

    def test_fast_mode_turn_on_an_unlisted_model_reports_both_gaps(self) -> None:
        # Fast mode and a missing rate are separate reasons, and one request can carry both.
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-unknown-9",
                                 input_tokens=1_000_000, output_tokens=1_000_000, speed="fast")]
        summary = MODULE.summarize(MODULE.claude_requests(events))
        self.assertEqual(summary["unpriced_requests"], 1)
        self.assertEqual(summary["unpriced_models"], ["claude-unknown-9"])
        self.assertEqual(summary["fast_mode_unpriced_requests"], 1)

    def test_read_cost_of_prices_a_fast_row_at_the_fast_read_rate(self) -> None:
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5",
                                 output_tokens=1_000_000, cache_read_input_tokens=1_000_000, speed="fast")]
        rows = MODULE.claude_requests(events)
        self.assertAlmostEqual(MODULE.read_cost_of(rows), 0.4)

    def test_input_cost_of_prices_a_fast_row_at_the_fast_input_rate(self) -> None:
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5",
                                 input_tokens=1_000_000, output_tokens=1_000_000, speed="fast")]
        rows = MODULE.claude_requests(events)
        self.assertAlmostEqual(MODULE.input_cost_of(rows[0]), 8)

    def test_a_request_with_no_speed_field_still_prices_standard(self) -> None:
        # An older transcript recorded no speed field at all, and that must still read as a
        # standard turn rather than an unpriced one.
        events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5",
                                 input_tokens=1_000_000, output_tokens=1_000_000)]
        rows = MODULE.claude_requests(events)
        self.assertEqual(rows[0]["speed"], "standard")
        self.assertAlmostEqual(MODULE.cost_of(rows[0]), 4 + 20)

    def test_an_in_region_request_is_priced_at_the_in_region_multiplier_at_either_speed(self) -> None:
        # Arc reads an inference_geo of "us" as in-region and charges 1.1 times every rate
        # component, cache reads and writes included, at standard speed and at fast speed.
        for speed, list_price in [("standard", 4 + 20 + 5 + 8 + 0.2), ("fast", 8 + 40 + 10 + 16 + 0.4)]:
            for geo, multiplier in [("us", 1.1), ("global", 1), ("eu", 1), ("not_available", 1)]:
                with self.subTest(speed=speed, geo=geo):
                    events = [claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5", input_tokens=1_000_000,
                                             output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                                             write_5m=1_000_000, write_1h=1_000_000, speed=speed, inference_geo=geo)]
                    summary = MODULE.summarize(MODULE.claude_requests(events))
                    self.assertEqual(summary["unpriced_requests"], 0)
                    self.assertAlmostEqual(summary["cost_usd"], list_price * multiplier)

    def test_a_bedrock_request_pays_the_regional_charge_unless_its_profile_is_global(self) -> None:
        # Bedrock bills every profile except global. at 1.1 times list. Claude Code's Bedrock default
        # is a us. profile, so a request with no model attachment before it pays the charge too.
        list_price = 4 + 20 + 5 + 8 + 0.2
        for profile, multiplier in [(None, 1.1), ("us.anthropic.claude-opus-5-5[1m]", 1.1),
                                    ("apac.anthropic.claude-opus-5-5", 1.1), ("global.anthropic.claude-opus-5-5", 1)]:
            with self.subTest(profile=profile):
                events = ([model_attachment(profile)] if profile else []) + [bedrock_record(1, "2026-09-23T10:00:00Z")]
                self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events))["cost_usd"],
                                       list_price * multiplier)

    def test_a_bedrock_request_read_as_in_region_pays_one_charge(self) -> None:
        events = [bedrock_record(1, "2026-09-23T10:00:00Z", inference_geo="us")]
        self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events))["cost_usd"], (4 + 20 + 5 + 8 + 0.2) * 1.1)

    def test_a_global_bedrock_request_read_as_in_region_pays_list(self) -> None:
        events = [model_attachment("global.anthropic.claude-opus-5-5"),
                  bedrock_record(1, "2026-09-23T10:00:00Z", inference_geo="us")]
        self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events))["cost_usd"], 4 + 20 + 5 + 8 + 0.2)

    def test_a_sidechain_model_attachment_leaves_the_parent_profile(self) -> None:
        # A parent transcript can carry a child agent's model attachment as a sidechain line.
        sidechain = dict(model_attachment("us.anthropic.claude-opus-5-5"), isSidechain=True)
        events = [model_attachment("global.anthropic.claude-opus-5-5"), sidechain, bedrock_record(1, "2026-09-23T10:00:00Z")]
        self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events))["cost_usd"], 4 + 20 + 5 + 8 + 0.2)
        self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events, child=True))["cost_usd"],
                               (4 + 20 + 5 + 8 + 0.2) * 1.1)

    def test_a_model_switch_reprices_the_bedrock_requests_after_it(self) -> None:
        # /model writes a new model attachment, and each request pays by the latest one before it.
        events = [model_attachment("global.anthropic.claude-opus-5-5"), bedrock_record(1, "2026-09-23T10:00:00Z"),
                  model_attachment("us.anthropic.claude-opus-5-5"), bedrock_record(2, "2026-09-23T10:01:00Z")]
        rows = MODULE.claude_requests(events)
        self.assertEqual([row["bedrock_regional"] for row in rows], [False, True])
        self.assertAlmostEqual(MODULE.summarize(rows)["cost_usd"], (4 + 20 + 5 + 8 + 0.2) * 2.1)

    def test_a_fast_bedrock_request_pays_the_regional_charge_on_the_fast_rate(self) -> None:
        # Opus 5.5 fast is $8 input and $40 output, a 0.05x read, and 1.25x or 2x writes.
        events = [bedrock_record(1, "2026-09-23T10:00:00Z", speed="fast")]
        self.assertAlmostEqual(MODULE.summarize(MODULE.claude_requests(events))["cost_usd"],
                               (8 + 40 + 0.4 + 10 + 16) * 1.1)

    def test_a_bedrock_response_spread_over_several_entries_pays_once(self) -> None:
        # Claude Code 2.1.281 on Bedrock wrote one response as three entries with one msg_bdrk_ id
        # and no requestId: thinking, then each tool call. Claude Code priced the session at
        # $0.1585482 list, and Bedrock bills 1.1 times that on a us. profile.
        first = {"input_tokens": 2, "write_5m": 29161, "write_1h": 0, "cache_read_input_tokens": 0,
                 "output_tokens": 230}
        events = [model_attachment("us.anthropic.claude-opus-5-5[1m]")]
        events += [bedrock_record(1, "2026-09-24T00:08:07Z", **first) for _ in range(3)]
        events.append(bedrock_record(2, "2026-09-24T00:08:09Z", input_tokens=2, write_5m=323, write_1h=0,
                                     cache_read_input_tokens=29161, output_tokens=34))
        rows = MODULE.claude_requests(events)
        self.assertEqual(len(rows), 2)
        # The summary rounds its total to four places.
        self.assertAlmostEqual(MODULE.summarize(rows)["cost_usd"], 0.1585482 * 1.1, places=4)

    def test_a_claude_request_routed_through_bedrock_or_vertex_stays_unpriced(self) -> None:
        # Arc records no provider on a Claude request and prices only the first-party spelling of
        # a Claude id. A Bedrock or Vertex id is a rate gap at either speed.
        for model in ROUTED_CLAUDE_IDS:
            for speed in ["standard", "fast"]:
                with self.subTest(model=model, speed=speed):
                    events = [claude_record(1, "2026-09-22T10:00:00Z", model, input_tokens=1_000_000,
                                             output_tokens=1_000_000, speed=speed, inference_geo="us")]
                    summary = MODULE.summarize(MODULE.claude_requests(events))
                    self.assertEqual(summary["unpriced_requests"], 1)
                    self.assertEqual(summary["unpriced_models"], [model])

    def test_sol_promotion_does_not_price_later_requests(self) -> None:
        self.assertIsNotNone(MODULE.rate_for("gpt-5.6-sol", "2026-11-21T23:59:59Z"))
        self.assertIsNone(MODULE.rate_for("gpt-5.6-sol", "2026-11-22T00:00:00Z"))

    def test_codex_session_metadata_finds_linked_child_rollout(self) -> None:
        parent = os.path.abspath("parent.jsonl")
        child = os.path.abspath("child.jsonl")
        metadata = {
            parent: {"id": "parent-id"},
            child: {
                "id": "child-id", "parent_thread_id": "parent-id",
                "source": {"subagent": {"thread_spawn": {"agent_path": "/root/reviewer"}}},
            },
        }
        with mock.patch.object(MODULE.glob, "glob", return_value=[parent, child]), \
                mock.patch.object(MODULE, "session_meta", side_effect=metadata.get):
            children = MODULE.codex_child_transcripts(parent, "sessions")
        self.assertEqual(len(children), 1)
        self.assertEqual(MODULE.child_label(children[0][1]), "/root/reviewer")


def write_claude_transcript(path: str, requests: list[tuple[str, int]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for index, (timestamp, input_tokens) in enumerate(requests):
            handle.write(json.dumps({
                "type": "assistant", "requestId": "%s-%d" % (os.path.basename(path), index), "timestamp": timestamp,
                "message": {"model": "claude-opus-5", "usage": {"input_tokens": input_tokens, "output_tokens": 0}},
            }) + "\n")


class SessionWindowTest(unittest.TestCase):
    """One session holds two tasks. The first starts at 10:00 UTC and the second at 11:00 UTC."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.transcript = os.path.join(self.root, "projects", "project", "session.jsonl")
        subagents = os.path.join(self.root, "projects", "project", "session", "subagents")
        write_claude_transcript(self.transcript, [
            ("2026-09-12T10:00:00.000Z", 10_000), ("2026-09-12T10:30:00.000Z", 10_000),
            ("2026-09-12T11:00:00.000Z", 20_000),
        ])
        # The file names sort in the reverse of start order.
        write_claude_transcript(os.path.join(subagents, "agent-f1.jsonl"), [
            ("2026-09-12T10:05:00.000Z", 1_000_000), ("2026-09-12T10:10:00.000Z", 1_000_000),
        ])
        write_claude_transcript(os.path.join(subagents, "agent-a2.jsonl"), [("2026-09-12T11:05:00.000Z", 100_000)])

    def report(self, *options: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", os.path.join(self.root, "projects"),
                         "--codex-sessions", os.path.join(self.root, "codex"), *options])
        return json.loads(output.getvalue())

    def test_since_excludes_the_first_task(self) -> None:
        report = self.report("--since", "2026-09-12T11:00:00Z")
        self.assertEqual(report["window"], {"since": "2026-09-12T11:00:00Z", "until": None})
        self.assertEqual([child["file"] for child in report["children"]], ["agent-a2.jsonl"])
        self.assertEqual(report["parent"]["requests"], 1)
        self.assertEqual(report["combined"]["requests"], 2)
        self.assertAlmostEqual(report["combined"]["cost_usd"], 0.6)

    def test_until_excludes_the_second_task(self) -> None:
        report = self.report("--until", "2026-09-12T12:00:00+01:00")
        self.assertEqual(report["window"], {"since": None, "until": "2026-09-12T11:00:00Z"})
        self.assertEqual([child["file"] for child in report["children"]], ["agent-f1.jsonl"])
        self.assertEqual(report["parent"]["requests"], 2)
        self.assertEqual(report["combined"]["requests"], 4)
        self.assertAlmostEqual(report["combined"]["cost_usd"], 10.1)

    def test_children_follow_their_first_request(self) -> None:
        report = self.report()
        self.assertNotIn("window", report)
        self.assertEqual([child["file"] for child in report["children"]], ["agent-f1.jsonl", "agent-a2.jsonl"])
        self.assertEqual(report["combined"]["requests"], 6)


class StreamedRecordsTest(unittest.TestCase):
    """A parent transcript holds a synthetic record, and its subagent streams one request over three records."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.transcript = os.path.join(self.projects, "project", "session.jsonl")
        child = os.path.join(self.projects, "project", "session", "subagents", "agent-a1.jsonl")

        def record(message_id: str, model: str, output: int, tool: str | None = None) -> dict:
            content = [{"type": "tool_use", "name": tool}] if tool else [{"type": "text", "text": "..."}]
            return {"type": "assistant", "requestId": "req-" + message_id, "timestamp": "2026-09-12T10:00:00.000Z",
                    "message": {"id": message_id, "model": model, "content": content,
                                "usage": {"input_tokens": 1000, "output_tokens": output}}}

        synthetic = {"type": "assistant", "timestamp": "2026-09-12T10:01:00.000Z", "message": {
            "id": "synthetic-1", "model": "<synthetic>", "content": [{"type": "text", "text": "No response requested."}],
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}}}
        for path, records in [
            (self.transcript, [record("msg-parent", "claude-opus-5", 40), record("msg-parent", "claude-opus-5", 40), synthetic]),
            (child, [record("msg-child", "claude-opus-5", 8), record("msg-child", "claude-opus-5", 8, "Read"),
                     record("msg-child", "claude-opus-5", 400, "Grep")]),
        ]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(json.dumps(item) + "\n" for item in records)

    def report(self) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", self.projects,
                         "--codex-sessions", os.path.join(self.projects, "codex")])
        return json.loads(output.getvalue())

    def test_subagent_request_is_priced_at_its_final_output(self) -> None:
        child = self.report()["children"][0]
        self.assertEqual(child["requests"], 1)
        self.assertEqual(child["output_tokens"], 400)
        self.assertEqual(child["tool_calls"], 2)
        self.assertAlmostEqual(child["cost_usd"], (1000 * 5 + 400 * 25) / 1_000_000)

    def test_synthetic_record_leaves_the_session_priced(self) -> None:
        report = self.report()
        self.assertEqual(report["parent"]["requests"], 1)
        self.assertEqual(report["parent"]["unpriced_requests"], 0)
        self.assertIsNotNone(report["combined"]["cost_usd"])
        self.assertAlmostEqual(report["combined"]["cost_usd"], (1000 * 5 + 40 * 25 + 1000 * 5 + 400 * 25) / 1_000_000)


class SingleSubagentFileTargetedDirectlyTest(unittest.TestCase):
    """A subagent transcript addressed directly must price the same as the parent report prices

    it as a child. Its one streamed request updates across three records sharing message id
    "msg-child", ending at 400 output tokens after two records of 8, so a target read as its
    own parent (never a child) must still keep the final row, not the first.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.child = os.path.join(self.projects, "project", "session", "subagents", "agent-a1.jsonl")

        def record(output: int, tool: str | None = None) -> dict:
            content = [{"type": "tool_use", "name": tool}] if tool else [{"type": "text", "text": "..."}]
            return {"type": "assistant", "requestId": "req-child", "timestamp": "2026-09-12T10:00:00.000Z",
                    "message": {"id": "msg-child", "model": "claude-opus-5", "content": content,
                                "usage": {"input_tokens": 1000, "output_tokens": output}}}

        records = [record(8), record(8, "Read"), record(400, "Grep")]
        os.makedirs(os.path.dirname(self.child), exist_ok=True)
        with open(self.child, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(item) + "\n" for item in records)

    def report(self, *options: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.child, "--projects", self.projects,
                         "--codex-sessions", os.path.join(self.projects, "codex"), *options])
        return json.loads(output.getvalue())

    def test_a_subagent_file_targeted_directly_prices_its_final_usage_row(self) -> None:
        parent = self.report()["parent"]
        self.assertEqual(parent["requests"], 1)
        self.assertEqual(parent["output_tokens"], 400)
        self.assertAlmostEqual(parent["cost_usd"], (1000 * 5 + 400 * 25) / 1_000_000)

    def test_by_tool_kind_on_a_directly_targeted_subagent_file_uses_the_same_price(self) -> None:
        by_kind = self.report("--by-tool-kind")["by_tool_kind"]
        self.assertAlmostEqual(sum(row["cost_usd"] for row in by_kind["rows"]), (1000 * 5 + 400 * 25) / 1_000_000)


def tool_use_assistant_event(message_id: str, timestamp: str, input_tokens: int, output_tokens: int,
                              tool_uses: tuple = (), text: str | None = None) -> dict:
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    for tool_id, name, tool_input in tool_uses:
        content.append({"type": "tool_use", "id": tool_id, "name": name, "input": tool_input})
    return {"type": "assistant", "timestamp": timestamp, "message": {
        "id": message_id, "model": "claude-sonnet-5",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}, "content": content}}


def tool_result_event(timestamp: str, *results: tuple) -> dict:
    content = [{"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}]}
               for tool_id, text in results]
    return {"type": "user", "timestamp": timestamp, "message": {"content": content}}


class ByToolKindTest(unittest.TestCase):
    """Six requests on claude-sonnet-5 ($2 input, $10 output per million tokens, no cache).

    msg-0 opens with no tool result yet and calls Bash `git status` (g1). msg-1 carries g1's
    result (git) and calls Read (r1) and Bash `npm test` (r2). msg-2 carries both r1 and r2 at
    once, a two-new-result request that splits between file read and test or build, and calls
    Agent (a1), whose result names agent id `test-agent-1`. msg-3 carries a1 (subagent launch)
    and calls Grep (s1). msg-4 carries s1 (search), calls Bash `docker ps` (o1, other), and its
    own text is the reviewer's first finding. msg-5 carries o1 (other) and calls nothing.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.transcript = os.path.join(self.projects, "project", "session.jsonl")
        os.makedirs(os.path.dirname(self.transcript), exist_ok=True)
        events = [
            tool_use_assistant_event("msg-0", "2026-09-12T10:00:00Z", 100_000, 10_000,
                                     tool_uses=[("g1", "Bash", {"command": "git status"})]),
            tool_result_event("2026-09-12T10:00:30Z", ("g1", "On branch main")),
            tool_use_assistant_event("msg-1", "2026-09-12T10:01:00Z", 50_000, 5_000,
                                     tool_uses=[("r1", "Read", {"file_path": "notes.txt"}),
                                                ("r2", "Bash", {"command": "npm test"})]),
            tool_result_event("2026-09-12T10:01:30Z", ("r1", "file contents"), ("r2", "5 passed")),
            tool_use_assistant_event("msg-2", "2026-09-12T10:02:00Z", 40_000, 4_000,
                                     tool_uses=[("a1", "Agent", {"subagent_type": "general-purpose",
                                                                 "prompt": "review the change"})]),
            tool_result_event("2026-09-12T10:02:30Z",
                              ("a1", "Async agent launched successfully.\nagentId: test-agent-1 (internal id)")),
            tool_use_assistant_event("msg-3", "2026-09-12T10:03:00Z", 30_000, 3_000,
                                     tool_uses=[("s1", "Grep", {"pattern": "TODO"})]),
            tool_result_event("2026-09-12T10:03:30Z", ("s1", "3 matches")),
            tool_use_assistant_event("msg-4", "2026-09-12T10:04:00Z", 60_000, 2_000,
                                     tool_uses=[("o1", "Bash", {"command": "docker ps"})],
                                     text="I found a bug: two colliding inputs cause a crash."),
            tool_result_event("2026-09-12T10:04:30Z", ("o1", "CONTAINER ID ...")),
            tool_use_assistant_event("msg-5", "2026-09-12T10:05:00Z", 10_000, 1_000),
        ]
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(event) + "\n" for event in events)

    def report(self, *options: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", self.projects,
                         "--codex-sessions", os.path.join(self.projects, "codex"), *options])
        return json.loads(output.getvalue())

    def rows_by_kind(self, by_tool_kind: dict) -> dict:
        return {row["kind"]: row for row in by_tool_kind["rows"]}

    def test_each_kind_gets_its_own_cost_and_a_split_request_counts_once_per_kind(self) -> None:
        rows = self.rows_by_kind(self.report("--by-tool-kind")["by_tool_kind"])
        expected = {
            "no tool result": 0.30, "git": 0.15, "file read": 0.06, "test or build": 0.06,
            "subagent launch": 0.09, "search": 0.14, "other": 0.03,
        }
        for kind, cost in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(rows[kind]["requests"], 1)
                self.assertAlmostEqual(rows[kind]["cost_usd"], cost)
        self.assertAlmostEqual(sum(row["cost_usd"] for row in rows.values()), 0.83)

    def test_until_text_prices_only_the_requests_before_the_first_finding(self) -> None:
        until_text = self.report("--by-tool-kind", "--until-text", "found a bug")["until_text"]
        self.assertTrue(until_text["matched"])
        rows = self.rows_by_kind(until_text["by_tool_kind"])
        self.assertEqual(set(rows), {"no tool result", "git", "file read", "test or build", "subagent launch"})
        self.assertAlmostEqual(sum(row["cost_usd"] for row in rows.values()), 0.66)

    def test_until_text_with_no_match_reports_so_and_uses_every_request(self) -> None:
        until_text = self.report("--by-tool-kind", "--until-text", "no such finding")["until_text"]
        self.assertFalse(until_text["matched"])
        self.assertIn("no assistant text matched", until_text["note"])
        self.assertAlmostEqual(sum(row["cost_usd"] for row in until_text["by_tool_kind"]["rows"]), 0.83)

    def test_window_agent_starts_at_the_launching_request_and_never_prices_before_it(self) -> None:
        window = self.report("--window-agent", "test-agent-1", "--by-tool-kind")["parent_window"]
        self.assertEqual(window["agent_id_start_request"], "msg-2")
        self.assertEqual(window["requests"], 4)
        self.assertAlmostEqual(window["cost_usd"], 0.38)
        self.assertEqual(window["context_at_start"], 40_000)
        self.assertEqual(window["context_at_peak"], 60_000)
        self.assertNotIn("no tool result", self.rows_by_kind(window["by_tool_kind"]))

    def test_window_until_is_inclusive_of_a_request_stamped_exactly_at_the_bound(self) -> None:
        window = self.report("--window-agent", "test-agent-1", "--until", "2026-09-12T10:03:00Z")["parent_window"]
        self.assertEqual(window["requests"], 2)
        self.assertAlmostEqual(window["cost_usd"], 0.21)
        self.assertEqual(window["context_at_start"], 40_000)
        self.assertEqual(window["context_at_peak"], 40_000)

    def test_an_unknown_agent_id_exits_non_zero_with_a_clear_message(self) -> None:
        with self.assertRaises(SystemExit) as failure:
            self.report("--window-agent", "no-such-agent")
        self.assertIn("no-such-agent", str(failure.exception))


class UntilTextMatchesToolCallInputTest(unittest.TestCase):
    """A reviewer states its finding only inside a hand-back tool call's input, never in a text block.

    msg-0 costs (5_000 * 2 + 1_000 * 10) / 1e6 = 0.02 and carries no tool call. msg-1 costs
    (3_000 * 2 + 300 * 10) / 1e6 = 0.009 and calls a SubagentHandback tool whose "message"
    input names the finding, with no separate assistant text block at all.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.transcript = os.path.join(self.projects, "project", "session.jsonl")
        os.makedirs(os.path.dirname(self.transcript), exist_ok=True)
        events = [
            tool_use_assistant_event("msg-0", "2026-09-12T10:00:00Z", 5_000, 1_000),
            tool_use_assistant_event("msg-1", "2026-09-12T10:01:00Z", 3_000, 300,
                                     tool_uses=[("h1", "SubagentHandback",
                                                 {"message": "I found a bug: handback finding"})]),
        ]
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(event) + "\n" for event in events)

    def test_a_finding_inside_a_tool_calls_input_still_marks_the_cutoff(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", self.projects,
                         "--codex-sessions", os.path.join(self.projects, "codex"),
                         "--by-tool-kind", "--until-text", "found a bug"])
        until_text = json.loads(output.getvalue())["until_text"]
        self.assertTrue(until_text["matched"])
        rows = {row["kind"]: row for row in until_text["by_tool_kind"]["rows"]}
        self.assertEqual(set(rows), {"no tool result"})
        self.assertAlmostEqual(rows["no tool result"]["cost_usd"], 0.02)


class WindowAgentStructuredIdTest(unittest.TestCase):
    def test_a_launch_with_only_a_structured_agent_id_is_still_found(self) -> None:
        # The result carries no "agentId:" trailer in its text at all, only the structured field.
        events = [
            {"type": "assistant", "message": {"id": "msg-0", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {}}]}},
            {"type": "user", "toolUseResult": {"isAsync": True, "agentId": "a1b2c3"}, "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": [{"type": "text", "text": "Async agent launched successfully."}]},
            ]}},
        ]
        self.assertEqual(MODULE.find_agent_launch_request(events, "a1b2c3"), "msg-0")


class ClassifyShellCommandTest(unittest.TestCase):
    def test_a_cat_redirect_to_a_file_is_other_not_a_read(self) -> None:
        self.assertEqual(MODULE.classify_shell_command("cat > notes.txt <<EOF\nsome text\nEOF"), "other")
        self.assertEqual(MODULE.classify_shell_command("cat >> notes.txt"), "other")

    def test_a_plain_cat_still_reads(self) -> None:
        self.assertEqual(MODULE.classify_shell_command("cat notes.txt"), "file read")

    def test_a_newline_separates_commands_the_same_as_a_double_ampersand(self) -> None:
        self.assertEqual(MODULE.classify_shell_command("cd x\ngit diff"), "git")

    def test_a_double_pipe_also_separates_commands(self) -> None:
        self.assertEqual(MODULE.classify_shell_command("cd x || git status"), "git")

    def test_strip_heredoc_bodies_blanks_the_marked_region(self) -> None:
        command = "cat > f.txt <<EOF\nsome && content\nmore ; lines\nEOF"
        self.assertEqual(MODULE.strip_heredoc_bodies(command), "cat > f.txt <<EOF")

    def test_a_quoted_assignment_is_skipped_as_one_word(self) -> None:
        self.assertEqual(MODULE.classify_shell_command('MSG="a b" git commit'), "git")


class CodexChildFailureWindowTest(unittest.TestCase):
    """A Codex child's only request sits before the window, and its only failure sits inside it."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.codex_sessions = os.path.join(temporary.name, "codex")
        self.transcript = os.path.join(self.projects, "project", "session.jsonl")
        os.makedirs(os.path.dirname(self.transcript), exist_ok=True)
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "session_meta", "payload": {"id": "parent-thread-id"}}) + "\n")
        child_dir = os.path.join(self.codex_sessions, "2026", "09", "12")
        os.makedirs(child_dir, exist_ok=True)
        self.child = os.path.join(child_dir, "rollout-child.jsonl")
        events = [
            {"type": "session_meta", "payload": {"id": "child-id", "parent_thread_id": "parent-thread-id"}},
            {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6-terra"}},
            {"type": "token_usage_record", "timestamp": "2026-09-12T09:00:00Z", "payload": {
                "turn_id": "turn-1", "response_id": "response-1",
                "usage": {"input_tokens": 100_000, "output_tokens": 20_000},
            }},
            {"type": "event_msg", "timestamp": "2026-09-12T10:30:00Z", "payload": {"type": "item_completed",
                                              "item": {"type": "CommandExecution", "status": "failed"}}},
        ]
        with open(self.child, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(event) + "\n" for event in events)

    def report(self, *options: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", self.projects,
                         "--codex-sessions", self.codex_sessions, *options])
        return json.loads(output.getvalue())

    def test_a_child_with_no_in_window_requests_still_reports_its_in_window_failure(self) -> None:
        report = self.report("--since", "2026-09-12T10:00:00Z")
        self.assertEqual(len(report["children"]), 1)
        child = report["children"][0]
        self.assertEqual(child["requests"], 0)
        self.assertEqual(child["failed_tool_calls"], 1)


def claude_record(index: int, timestamp: str, model: str = "claude-opus-5", **usage: int) -> dict:
    """Write one Claude request. Name a cache write with write_5m or write_1h."""
    creation = {"ephemeral_5m_input_tokens": usage.pop("write_5m", 0),
                "ephemeral_1h_input_tokens": usage.pop("write_1h", 0)}
    tokens = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation": creation}
    tokens.update(usage)
    return {"type": "assistant", "requestId": "request-%d" % index, "timestamp": timestamp,
            "message": {"id": "msg-%d" % index, "model": model, "usage": tokens}}


def bedrock_record(index: int, timestamp: str, **usage: object) -> dict:
    """Write one Claude request as Bedrock records it: a msg_bdrk_ id and no requestId."""
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
              "write_5m": 1_000_000, "write_1h": 1_000_000, "inference_geo": ""}
    tokens.update(usage)
    record = claude_record(index, timestamp, "claude-opus-5-5", **tokens)
    del record["requestId"]
    record["message"]["id"] = "msg_bdrk_%d" % index
    return record


def model_attachment(model_id: str) -> dict:
    """Write the attachment Claude Code records at a session start and at each /model switch."""
    return {"type": "attachment", "attachment": {"type": "model", "identity": {"modelId": model_id}}}


def claude_turn(timestamp: str) -> dict:
    return {"type": "user", "timestamp": timestamp, "origin": {"kind": "human"},
            "message": {"role": "user", "content": "carry on"}}


def claude_compaction_records(timestamp: str) -> list[dict]:
    return [{"type": "system", "subtype": "compact_boundary", "timestamp": timestamp,
             "compactMetadata": {"trigger": "manual"}},
            {"type": "user", "timestamp": timestamp, "isCompactSummary": True,
             "isVisibleInTranscriptOnly": True, "message": {"role": "user", "content": "Summary: ..."}}]


def codex_record(index: int, timestamp: str, model: str, **usage: int) -> list[dict]:
    tokens = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0}
    tokens.update(usage)
    turn = "turn-%d" % index
    return [{"type": "turn_context", "timestamp": timestamp, "payload": {"turn_id": turn, "model": model}},
            {"type": "token_usage_record", "timestamp": timestamp,
             "payload": {"turn_id": turn, "response_id": "response-%d" % index, "usage": tokens}}]


class CacheExpiryTest(unittest.TestCase):
    """One request writes a cache tier, the session idles, and the next request pays cold input."""

    def scan(self, events: list[dict], reader=None) -> dict:
        rows = (reader or MODULE.claude_requests)(events)
        return MODULE.summarize(rows)["cache_expiry"]

    def pair(self, returned: str, lifetime: str = "write_1h", **later: int) -> dict:
        written = {lifetime: 40_000}
        cold = {"input_tokens": 30_000} if not later else later
        return self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", **written),
                          claude_record(1, returned, **cold)])

    def codex_pair(self, returned: str, model: str = "gpt-5.6-terra", **written: int) -> dict:
        events = codex_record(0, "2026-09-12T10:00:00Z", model, **written)
        events += codex_record(1, returned, model, input_tokens=30_000)
        return self.scan(events, MODULE.codex_requests)

    def hour_pair(self, model: str, returned: str) -> dict:
        return self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", model=model, write_1h=40_000,
                                        cache_read_input_tokens=40_000),
                          claude_record(1, returned, model=model, input_tokens=30_000)])

    def test_one_hour_lifetime_returns_after_sixty_one_minutes(self) -> None:
        expiry = self.pair("2026-09-12T11:01:00.000Z")
        self.assertEqual(expiry["returns"], 1)
        self.assertEqual(expiry["by_lifetime"]["1h"], 1)
        self.assertEqual(expiry["longest_gap_seconds"], 3660)

    def test_fifty_nine_minutes_reaches_no_lifetime(self) -> None:
        expiry = self.pair("2026-09-12T10:59:00.000Z")
        self.assertEqual((expiry["returns"], expiry["excluded_ambiguous"], expiry["unavailable_lifetime"]), (0, 0, 0))

    def test_five_minute_lifetime_returns_after_six_minutes(self) -> None:
        expiry = self.pair("2026-09-12T10:06:00.000Z", lifetime="write_5m")
        self.assertEqual(expiry["returns"], 1)
        self.assertEqual(expiry["by_lifetime"]["5m"], 1)

    def test_codex_thirty_minute_lifetime_returns_after_thirty_one_minutes(self) -> None:
        events = codex_record(0, "2026-09-12T10:00:00Z", "gpt-5.6-terra",
                              input_tokens=40_000, cache_write_input_tokens=40_000)
        events += codex_record(1, "2026-09-12T10:31:00Z", "gpt-5.6-terra", input_tokens=30_000)
        expiry = self.scan(events, MODULE.codex_requests)
        self.assertEqual(expiry["returns"], 1)
        self.assertEqual(expiry["by_lifetime"]["30m"], 1)
        self.assertEqual(expiry["by_boundary"]["turn_unknown"], 1)

    def test_a_warm_return_is_no_return(self) -> None:
        expiry = self.pair("2026-09-12T11:01:00.000Z", cache_read_input_tokens=40_000, output_tokens=500)
        self.assertEqual((expiry["returns"], expiry["excluded_ambiguous"], expiry["unavailable_lifetime"]), (0, 0, 0))

    def test_a_model_change_excludes_the_pair(self) -> None:
        expiry = self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", write_1h=40_000),
                            claude_record(1, "2026-09-12T11:01:00.000Z", model="claude-sonnet-5",
                                          input_tokens=30_000)])
        self.assertEqual((expiry["returns"], expiry["excluded_ambiguous"]), (0, 1))

    def test_a_compaction_excludes_the_pair(self) -> None:
        events = [claude_record(0, "2026-09-12T10:00:00.000Z", write_1h=40_000)]
        events += claude_compaction_records("2026-09-12T10:30:00.000Z")
        events.append(claude_record(1, "2026-09-12T11:01:00.000Z", input_tokens=30_000))
        expiry = self.scan(events)
        self.assertEqual((expiry["returns"], expiry["excluded_ambiguous"]), (0, 1))

    def test_two_written_tiers_leave_the_lifetime_unavailable(self) -> None:
        expiry = self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", write_5m=1_000, write_1h=40_000),
                            claude_record(1, "2026-09-12T11:01:00.000Z", input_tokens=30_000)])
        self.assertEqual((expiry["returns"], expiry["unavailable_lifetime"]), (0, 1))

    def test_no_written_tier_leaves_the_lifetime_unavailable(self) -> None:
        expiry = self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", cache_read_input_tokens=40_000),
                            claude_record(1, "2026-09-12T11:01:00.000Z", input_tokens=30_000)])
        self.assertEqual((expiry["returns"], expiry["unavailable_lifetime"]), (0, 1))

    def test_a_codex_read_takes_the_documented_lifetime(self) -> None:
        expiry = self.codex_pair("2026-09-12T10:31:00Z", input_tokens=40_000, cached_input_tokens=10_000)
        self.assertEqual(expiry["returns"], 1)
        self.assertEqual(expiry["by_lifetime"]["30m"], 1)
        self.assertEqual(expiry["unavailable_lifetime"], 0)

    def test_a_codex_return_inside_the_documented_lifetime_is_no_return(self) -> None:
        expiry = self.codex_pair("2026-09-12T10:29:00Z", input_tokens=40_000, cached_input_tokens=10_000)
        self.assertEqual((expiry["returns"], expiry["excluded_ambiguous"], expiry["unavailable_lifetime"]), (0, 0, 0))

    def test_a_codex_request_that_cached_nothing_leaves_the_lifetime_unavailable(self) -> None:
        expiry = self.codex_pair("2026-09-12T10:31:00Z", input_tokens=40_000)
        self.assertEqual((expiry["returns"], expiry["unavailable_lifetime"]), (0, 1))

    def test_two_written_tiers_never_take_a_documented_lifetime(self) -> None:
        # The second model documents thirty minutes, and two recorded tiers still name none.
        for model in ("claude-opus-5", "gpt-5.6-terra"):
            with self.subTest(model=model):
                expiry = self.scan([
                    claude_record(0, "2026-09-12T10:00:00.000Z", model=model, write_5m=1_000, write_1h=40_000,
                                  cache_read_input_tokens=40_000),
                    claude_record(1, "2026-09-12T11:01:00.000Z", model=model, input_tokens=30_000)])
                self.assertEqual((expiry["returns"], expiry["unavailable_lifetime"]), (0, 1))

    def test_a_claude_record_names_the_lifetime_its_own_pair_uses(self) -> None:
        expiry = self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", write_5m=40_000,
                                          cache_read_input_tokens=40_000),
                            claude_record(1, "2026-09-12T10:06:00.000Z", input_tokens=30_000)])
        self.assertEqual((expiry["returns"], expiry["by_lifetime"]["5m"]), (1, 1))

    def test_a_recorded_tier_outranks_a_documented_lifetime(self) -> None:
        # gpt-5.6-terra documents thirty minutes, and this record bought an hour.
        self.assertEqual(self.hour_pair("gpt-5.6-terra", "2026-09-12T10:31:00.000Z")["returns"], 0)
        expired = self.hour_pair("gpt-5.6-terra", "2026-09-12T11:01:00.000Z")
        self.assertEqual((expired["returns"], expired["by_lifetime"]["1h"]), (1, 1))

    def test_a_gap_inside_one_turn_keeps_its_own_count(self) -> None:
        expiry = self.scan([claude_turn("2026-09-12T09:59:00.000Z"),
                            claude_record(0, "2026-09-12T10:00:00.000Z", write_1h=40_000),
                            claude_record(1, "2026-09-12T11:01:00.000Z", input_tokens=30_000)])
        self.assertEqual(expiry["by_boundary"], {"after_a_user_turn": 0, "inside_one_turn": 1, "turn_unknown": 0})

    def test_a_gap_after_a_user_turn_keeps_its_own_count(self) -> None:
        expiry = self.scan([claude_turn("2026-09-12T09:59:00.000Z"),
                            claude_record(0, "2026-09-12T10:00:00.000Z", write_1h=40_000),
                            claude_turn("2026-09-12T11:00:00.000Z"),
                            claude_record(1, "2026-09-12T11:01:00.000Z", input_tokens=30_000)])
        self.assertEqual(expiry["by_boundary"], {"after_a_user_turn": 1, "inside_one_turn": 0, "turn_unknown": 0})

    def test_the_amount_leaves_out_cache_reads_and_output(self) -> None:
        expiry = self.pair("2026-09-12T11:01:00.000Z", input_tokens=200_000, write_1h=100_000,
                           cache_read_input_tokens=1_000_000, output_tokens=1_000_000)
        self.assertEqual(expiry["returns"], 1)
        self.assertAlmostEqual(expiry["input_cost_after_expiry_usd"], (200_000 * 5 + 100_000 * 10) / 1_000_000)
        self.assertAlmostEqual(expiry["largest"][0]["input_cost_usd"], 2.0)

    def test_an_unpriced_model_leaves_the_amount_null(self) -> None:
        expiry = self.scan([claude_record(0, "2026-09-12T10:00:00.000Z", model="gpt-unknown", write_1h=40_000),
                            claude_record(1, "2026-09-12T11:01:00.000Z", model="gpt-unknown", input_tokens=30_000)])
        self.assertEqual(expiry["returns"], 1)
        self.assertIsNone(expiry["input_cost_after_expiry_usd"])
        self.assertIsNone(expiry["largest"][0]["input_cost_usd"])


class CacheExpiryWindowTest(unittest.TestCase):
    """Three requests each write a one-hour tier and each pay cold input an hour later."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = os.path.join(temporary.name, "projects")
        self.transcript = os.path.join(self.projects, "project", "session.jsonl")
        os.makedirs(os.path.dirname(self.transcript), exist_ok=True)
        stamps = ["2026-09-12T10:00:00.000Z", "2026-09-12T11:01:00.000Z", "2026-09-12T12:02:00.000Z"]
        with open(self.transcript, "w", encoding="utf-8") as handle:
            for index, stamp in enumerate(stamps):
                handle.write(json.dumps(claude_record(index, stamp, input_tokens=10_000, write_1h=40_000)) + "\n")

    def expiry(self, *options: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            MODULE.main([self.transcript, "--projects", self.projects,
                         "--codex-sessions", os.path.join(self.projects, "codex"), *options])
        return json.loads(output.getvalue())["combined"]["cache_expiry"]

    def returns(self, *options: str) -> int:
        return self.expiry(*options)["returns"]

    def test_the_window_restricts_the_scan(self) -> None:
        self.assertEqual(self.returns(), 2)
        self.assertEqual(self.returns("--since", "2026-09-12T11:00:00Z"), 1)
        self.assertEqual(self.returns("--until", "2026-09-12T11:30:00Z"), 1)

    def test_the_combined_rollup_splits_the_returns_by_lifetime(self) -> None:
        self.assertEqual(self.expiry()["by_lifetime"], {"5m": 0, "30m": 0, "1h": 2})


ARC_PRICING = os.path.join(
    os.environ.get("ARC_REPO") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "..", "agent-diff-review"),
    "extension", "src", "pricing.ts")
ARC_TRANSCRIPT = os.path.join(os.path.dirname(ARC_PRICING), "transcript.ts")

# Claude ids as Bedrock and Vertex spell them. Arc prices none of them.
ROUTED_CLAUDE_IDS = [
    "anthropic.claude-opus-4-5-20251101-v1:0", "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-sonnet-4-5@20250929", "claude-opus-4-5@20251101",
]

# Every id the Arc table prices, in each spelling it accepts, and near misses it refuses.
PRICE_TABLE_IDS = [
    "claude-fable-5-1", "claude-fable-5-1[1m]", "claude-mythos-5-1-20260901", "claude-fable-5", "claude-mythos-5",
    "claude-opus-5-5", "claude-opus-5-5[1m]", "claude-opus-5-5-20260922",
    "claude-opus-5", "claude-opus-5[1m]", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-opus-4-5-20251101", "claude-opus-4-1", "claude-opus-4-88", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929", "claude-sonnet-4", "claude-haiku-4-5-20251001", "claude-haiku-3-5",
    "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.5", "gpt-5.5-2026-04-23", "gpt-5.4", "gpt-5.4-2026-03-05", "gpt-5.3-codex",
    "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "codex-auto-review",
    "openai.gpt-5.6-sol", "global.openai.gpt-5.6-terra", "us.openai.gpt-5.6-luna",
    "openai.gpt-6-sol", "global.openai.gpt-6-sol", "us.openai.gpt-6-sol",
    "openai.gpt-6-luna", "global.openai.gpt-6-luna", "us.openai.gpt-6-luna",
    *ROUTED_CLAUDE_IDS,
]

# Reads how Arc's Claude reader derives the provider, inference class and speed of one record.
# transcript.ts does not load under Node's type stripping, so this takes the reader's helpers out
# of its source, and fails when the reader stops using them. Every script below starts with it,
# and takes pricing.ts and transcript.ts as its first two arguments.
ARC_CLAUDE_READER = r"""
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { pathToFileURL } from 'node:url';
const pricing = await import(pathToFileURL(process.argv[1]).href);
// A checkout with CRLF line endings holds the same code, so every match below reads LF text.
const source = readFileSync(process.argv[2], 'utf8').replace(/\r\n?/g, '\n');
const definition = (name, exported) => {
  const found = source.match(new RegExp(`\\n${exported ? 'export ' : ''}function ${name}\\([\\s\\S]*?\\n}\\n`))?.[0];
  if (!found) throw new Error(`transcript.ts no longer defines ${exported ? 'the exported ' : ''}function ${name}`);
  return found;
};
const reader = definition('reduceClaudeUsageRecord', true);
const readerLines = ['provider: null,',
  'const inferenceClass = claudeRequestInferenceClass(messageId, state.modelProfile, usage.inference_geo);',
  'const serviceTier = claudeServiceTier(usage);'];
for (const line of readerLines) {
  if (!reader.includes(line)) throw new Error(`reduceClaudeUsageRecord in transcript.ts no longer writes ${line}`);
}
const bedrockPrefix = source.match(/\nconst BEDROCK_MESSAGE_PREFIX = [^\n]*\n/)?.[0];
if (!bedrockPrefix) throw new Error('transcript.ts no longer defines BEDROCK_MESSAGE_PREFIX');
const helpers = ['claudeInferenceClass', 'claudeRequestInferenceClass', 'claudeServiceTier']
  .map((name) => definition(name, false));
const { claudeRequestInferenceClass, claudeServiceTier } = new Function(
  `${stripTypeScriptTypes(bedrockPrefix + helpers.join(''))}\nreturn { claudeRequestInferenceClass, claudeServiceTier };`)();
// The Claude reader records no provider, whatever the id names. A Bedrock OpenAI id comes from a
// Codex session on Bedrock, which names amazon-bedrock.
const CLAUDE_PROVIDER = null;
const providerFor = (model) => model.startsWith('claude-') || model.includes('anthropic.')
  ? CLAUDE_PROVIDER : model.includes('openai.') ? 'amazon-bedrock' : null;
"""

# Prices one million tokens of each category through Arc's own quote function, for each
# inference class a Claude record can carry.
ARC_QUOTES = ARC_CLAUDE_READER + r"""
const ids = JSON.parse(process.argv[3]);
const categories = ['input', 'output', 'cacheWrite5m', 'cacheWrite1h', 'cacheWrite30m', 'cacheRead'];
const empty = { requests: 1, input: 0, output: 0, cacheWrite5m: 0, cacheWrite1h: 0, cacheWrite30m: 0, cacheRead: 0, thinking: 0 };
const quotes = {};
for (const model of ids) {
  const provider = providerFor(model);
  for (const requestTokens of [1000, 300000]) {
    for (const speed of ['standard', 'fast']) {
      for (const inferenceClass of [null, 'in-region', 'geo', 'global']) {
        quotes[[model, requestTokens, speed, inferenceClass].join('@')] = categories.map((category) => pricing.quoteUsage({
          model, provider, requestTokens, serviceTier: speed, inferenceClass, usage: { ...empty, [category]: 1000000 },
        })?.cost ?? null);
      }
    }
  }
}
console.log(JSON.stringify({ quotes, promotions: pricing.PROMOTIONS.map(({ promo }) => promo.until) }));
"""

# Reads the cache lifetime Arc documents for each id, which is the other half of a price row.
ARC_LIFETIMES = ARC_CLAUDE_READER + r"""
const lifetimes = {};
for (const model of JSON.parse(process.argv[3])) {
  lifetimes[model] = pricing.cacheLifetimeFor(model, providerFor(model)) ?? null;
}
console.log(JSON.stringify(lifetimes));
"""

# Prices whole Claude transcript records, read from standard input, the way Arc's Claude reader
# and quote function price them. A record's `profile` stands for the latest model attachment
# before it.
ARC_RECORD_QUOTES = ARC_CLAUDE_READER + r"""
const records = JSON.parse(readFileSync(0, 'utf8'));
console.log(JSON.stringify(records.map(({ message, profile }) => {
  const usage = message.usage;
  const creation = usage.cache_creation;
  const counts = {
    requests: 1, input: usage.input_tokens, output: usage.output_tokens, cacheRead: usage.cache_read_input_tokens,
    cacheWrite5m: creation.ephemeral_5m_input_tokens, cacheWrite1h: creation.ephemeral_1h_input_tokens,
    cacheWrite30m: 0, thinking: 0,
  };
  return pricing.quoteUsage({
    usage: counts, model: message.model, provider: providerFor(message.model),
    inferenceClass: claudeRequestInferenceClass(message.id ?? null, profile ?? null, usage.inference_geo),
    serviceTier: claudeServiceTier(usage),
    requestTokens: counts.input + counts.cacheRead + counts.cacheWrite5m + counts.cacheWrite1h,
  })?.cost ?? null;
})));
"""


class ArcScriptError(Exception):
    pass


def run_arc(script: str, *arguments: str, stdin: str | None = None, transcript: str = ARC_TRANSCRIPT) -> object:
    result = subprocess.run(["node", "--no-warnings", "--input-type=module", "-e", script, ARC_PRICING, transcript,
                             *arguments], input=stdin, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        errors = [line for line in result.stderr.splitlines() if line.startswith("Error")]
        raise ArcScriptError(errors[-1] if errors else result.stderr.strip())
    return json.loads(result.stdout)


@unittest.skipUnless(os.path.exists(ARC_PRICING) and shutil.which("node"), "the Arc checkout or Node is missing")
class ArcPriceTableTest(unittest.TestCase):
    """Arc's pricing.ts is the owner's price table. This script's table must agree with it."""

    def test_every_rate_matches_arc(self) -> None:
        arc = run_arc(ARC_QUOTES, json.dumps(PRICE_TABLE_IDS))
        for key, quotes in arc["quotes"].items():
            # A Vertex id carries its own @, so the fields split from the right. Arc joins a null
            # inference class as an empty field.
            model, context, speed, inference_class = key.rsplit("@", 3)
            ours = MODULE.request_rate(model, "2026-09-01T00:00:00Z", int(context), speed, inference_class or None)
            with self.subTest(model=model, request_tokens=context, speed=speed, inference_class=inference_class):
                if all(quote is None for quote in quotes):
                    self.assertIsNone(ours)
                    continue
                self.assertIsNotNone(ours)
                for category, (theirs, mine) in enumerate(zip(quotes, ours)):
                    if theirs is None:
                        self.assertIsNone(mine, "category %d" % category)
                    else:
                        self.assertIsNotNone(mine, "category %d" % category)
                        self.assertAlmostEqual(mine, theirs, places=9, msg="category %d" % category)
        self.assertEqual(arc["promotions"], [MODULE.SOL_PROMO_UNTIL.isoformat()])

    def test_every_documented_lifetime_matches_arc(self) -> None:
        for model, theirs in run_arc(ARC_LIFETIMES, json.dumps(PRICE_TABLE_IDS)).items():
            with self.subTest(model=model):
                self.assertEqual(MODULE.documented_lifetime(model), theirs)

    def test_every_claude_record_matches_arc(self) -> None:
        # Arc reads the provider, inference class and speed from the record itself, so this
        # compares whole records rather than rates.
        records = []
        for model in ["claude-opus-5-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                      "claude-opus-4-1", *ROUTED_CLAUDE_IDS]:
            for speed in [None, "standard", "fast"]:
                for geo in [None, "us", "US", "us-only", "in-region", "global", "eu", "not_available", ""]:
                    usage = {} if speed is None else {"speed": speed}
                    usage.update({} if geo is None else {"inference_geo": geo})
                    records.append(claude_record(len(records), "2026-09-22T10:00:00Z", model, input_tokens=1_000_000,
                                                 output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                                                 write_5m=1_000_000, write_1h=1_000_000, **usage))
        arc = run_arc(ARC_RECORD_QUOTES, stdin=json.dumps(records))
        self.assertEqual(len(arc), len(records))
        for record, theirs in zip(records, arc):
            usage = record["message"]["usage"]
            with self.subTest(model=record["message"]["model"], speed=usage.get("speed"), geo=usage.get("inference_geo")):
                ours = MODULE.cost_of(MODULE.claude_requests([record])[0])
                if theirs is None:
                    self.assertIsNone(ours)
                else:
                    self.assertIsNotNone(ours)
                    self.assertAlmostEqual(ours, theirs, places=9)

    def test_every_bedrock_record_matches_arc(self) -> None:
        # Arc reads the Bedrock profile from the latest model attachment, as this script does.
        cases = []
        for profile in [None, "us.anthropic.claude-opus-5-5[1m]", "apac.anthropic.claude-opus-5-5",
                        "anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5"]:
            for speed in ["standard", "fast"]:
                for geo in ["", "us", "global"]:
                    cases.append((profile, bedrock_record(len(cases), "2026-09-23T10:00:00Z", speed=speed,
                                                          inference_geo=geo)))
        arc = run_arc(ARC_RECORD_QUOTES, stdin=json.dumps([dict(record, profile=profile)
                                                            for profile, record in cases]))
        self.assertEqual(len(arc), len(cases))
        for (profile, record), theirs in zip(cases, arc):
            usage = record["message"]["usage"]
            with self.subTest(profile=profile, speed=usage["speed"], geo=usage["inference_geo"]):
                events = ([model_attachment(profile)] if profile else []) + [record]
                self.assertAlmostEqual(MODULE.cost_of(MODULE.claude_requests(events)[0]), theirs, places=9)

    def reader_quotes(self, source: str) -> object:
        """Price in-region fast and standard records through a copy of transcript.ts that holds the given text."""
        records = [claude_record(0, "2026-09-22T10:00:00Z", "claude-opus-5-5", input_tokens=1_000_000,
                                 inference_geo="us", speed="fast"),
                   claude_record(1, "2026-09-22T10:00:00Z", "claude-opus-5-5", output_tokens=1_000_000)]
        with tempfile.TemporaryDirectory() as directory:
            copy = os.path.join(directory, "transcript.ts")
            with open(copy, "w", encoding="utf-8", newline="") as handle:
                handle.write(source)
            return run_arc(ARC_RECORD_QUOTES, stdin=json.dumps(records), transcript=copy)

    def test_the_reader_reads_a_checkout_with_crlf_line_endings(self) -> None:
        with open(ARC_TRANSCRIPT, encoding="utf-8", newline="") as handle:
            lf = handle.read().replace("\r\n", "\n")
        self.assertEqual(self.reader_quotes(lf.replace("\n", "\r\n")), self.reader_quotes(lf))

    def test_a_changed_reader_names_what_changed(self) -> None:
        with open(ARC_TRANSCRIPT, encoding="utf-8", newline="") as handle:
            lf = handle.read().replace("\r\n", "\n")
        cases = {
            "function claudeServiceTier(": ("function claudeSpeedTier(", "no longer defines function claudeServiceTier"),
            "export function reduceClaudeUsageRecord(": (
                "function reduceClaudeUsageRecord(", "no longer defines the exported function reduceClaudeUsageRecord"),
            "provider: null,": ("provider: undefined,", "reduceClaudeUsageRecord in transcript.ts no longer writes provider: null,"),
        }
        for needle, (replacement, message) in cases.items():
            with self.subTest(needle=needle):
                self.assertIn(needle, lf)
                with self.assertRaisesRegex(ArcScriptError, re.escape(message)):
                    self.reader_quotes(lf.replace(needle, replacement, 1))

    def test_every_arc_pattern_has_a_compared_id(self) -> None:
        with open(ARC_PRICING, encoding="utf-8") as handle:
            source = handle.read()
        patterns = re.findall(r"pattern: /(.+?)/,", source) + re.findall(r"const BEDROCK_MODEL = /(.+?)/;", source)
        self.assertTrue(patterns)
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(any(re.search(pattern, model) for model in PRICE_TABLE_IDS))


if __name__ == "__main__":
    unittest.main()
