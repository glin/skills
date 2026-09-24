#!/usr/bin/env python3
"""Print a bounded AgentsView usage summary for one agent session."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from typing import Any


def read_usage(session_id: str, own_only: bool) -> dict[str, Any]:
    command = ["agentsview", "session", "usage", session_id, "--json"]
    if own_only:
        command.append("--own-only")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "AgentsView returned no error text"
        raise RuntimeError(detail)
    return json.loads(result.stdout)


def row_tokens(row: dict[str, Any]) -> dict[str, int]:
    return {
        "uncached_input": int(row.get("input_tokens") or 0),
        "cache_read": int(row.get("cache_read_input_tokens") or 0),
        "cache_write": int(row.get("cache_creation_input_tokens") or 0),
        "output": int(row.get("output_tokens") or 0),
    }


def add_tokens(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: left.get(key, 0) + right.get(key, 0) for key in row_tokens({})}


def usage_totals(data: dict[str, Any]) -> dict[str, Any]:
    rows = data.get("breakdown") or []
    tokens = row_tokens({})
    for row in rows:
        tokens = add_tokens(tokens, row_tokens(row))
    return {
        "requests": len(rows),
        "cost_usd": data.get("cost_usd"),
        "tokens": tokens,
    }


def subtract_usage(full: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    full_tokens = full["tokens"]
    own_tokens = own["tokens"]
    full_cost = full.get("cost_usd")
    own_cost = own.get("cost_usd")
    return {
        "requests": max(0, full["requests"] - own["requests"]),
        "cost_usd": round(full_cost - own_cost, 6) if isinstance(full_cost, (int, float)) and isinstance(own_cost, (int, float)) else None,
        "tokens": {key: max(0, full_tokens[key] - own_tokens[key]) for key in full_tokens},
    }


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = [parse_time(row["timestamp"]) for row in rows if row.get("timestamp")]
    gaps = [(right - left).total_seconds() for left, right in zip(times, times[1:])]
    return {
        "elapsed_minutes": round((times[-1] - times[0]).total_seconds() / 60, 1) if len(times) > 1 else 0,
        "median_gap_seconds": round(statistics.median(gaps), 1) if gaps else None,
        "gaps_under_30_seconds": sum(gap < 30 for gap in gaps),
        "gaps_over_30_minutes": sum(gap > 1800 for gap in gaps),
    }


def input_size(row: dict[str, Any]) -> int:
    tokens = row_tokens(row)
    return tokens["uncached_input"] + tokens["cache_read"] + tokens["cache_write"]


def bucket_rows(rows: list[dict[str, Any]], bucket_count: int = 10) -> list[dict[str, Any]]:
    if not rows:
        return []
    width = max(1, (len(rows) + bucket_count - 1) // bucket_count)
    buckets = []
    for start in range(0, len(rows), width):
        group = rows[start:start + width]
        totals = row_tokens({})
        for row in group:
            totals = add_tokens(totals, row_tokens(row))
        costs = [row.get("cost", {}).get("microdollars") for row in group]
        priced = [value for value in costs if isinstance(value, int)]
        buckets.append({
            "ordinals": [group[0].get("ordinal"), group[-1].get("ordinal")],
            "requests": len(group),
            "average_input": round(sum(input_size(row) for row in group) / len(group)),
            "tokens": totals,
            "cost_usd": round(sum(priced) / 1_000_000, 6) if priced else None,
        })
    return buckets


def costliest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: row.get("cost", {}).get("microdollars") or 0, reverse=True)
    return [{
        "ordinal": row.get("ordinal"),
        "timestamp": row.get("timestamp"),
        "model": row.get("model"),
        "input": input_size(row),
        "output": int(row.get("output_tokens") or 0),
        "cost_usd": round((row.get("cost", {}).get("microdollars") or 0) / 1_000_000, 6),
    } for row in ranked[:5]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", help="AgentsView session ID, including the agent prefix")
    args = parser.parse_args()
    try:
        full_data = read_usage(args.session_id, own_only=False)
        own_data = read_usage(args.session_id, own_only=True)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"usage collection failed: {error}", file=sys.stderr)
        return 1

    rows = full_data.get("breakdown") or []
    full = usage_totals(full_data)
    own = usage_totals(own_data)
    summary = {
        "session_id": full_data.get("session_id"),
        "agent": full_data.get("agent"),
        "project": full_data.get("project"),
        "models": full_data.get("models") or [],
        "has_cost": full_data.get("has_cost"),
        "cost_source": full_data.get("cost_source"),
        "peak_context_tokens": full_data.get("peak_context_tokens"),
        "full": full,
        "parent": own,
        "delegated": subtract_usage(full, own),
        "timing": timing(rows),
        "request_buckets": bucket_rows(rows),
        "costliest_requests": costliest(rows),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
