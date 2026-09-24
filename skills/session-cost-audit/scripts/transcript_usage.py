#!/usr/bin/env python3
"""Summarize one Claude Code or Codex session from its transcript.

Use this when AgentsView has not indexed the session. Sessions started in a
worktree, and sessions a launcher detached, are often missing from it.

  python transcript_usage.py <session-id|transcript-path> [--since TIME] [--until TIME]

The JSON report includes request counts, token categories, context statistics,
tool-call counts, failed tool-call counts, and linked subagent usage, with
subagents in order of their first request. It includes a list-price estimate
only when every recorded model has a supported rate.

Claude charges a failed tool call to the request that issued it. It marks the
failure with `is_error` on the tool_result block that answers it. Codex marks
a failure with `status: "failed"` on the item_completed event the call
produced, never on the call's own output record. That event names no
request. A Codex transcript reports `failed_tool_calls` as a count and
reports `failed_tool_call_cost_usd` as null.

--since and --until price one part of a session, such as one task. Every figure
then counts only the requests inside that window, in the parent and in each
subagent.

Claude writes one request as several transcript entries that share a message id, and
the reader prices each request once and unions its tool calls across those entries. A
parent transcript repeats the finished usage, so the reader keeps the first entry. A
subagent transcript writes the entries while the response streams, and its output grows
from one entry to the next, so the reader keeps the last entry. A `<synthetic>` entry
records no request and the reader skips it.

Codex writes one `token_usage_record` per response. Its input total includes
cached reads and cache writes, so the reader separates those categories before
calculating uncached input.

The `cache_expiry` object counts a request that paid cold input after an idle gap
reached the lifetime of the cache before it. It prices the input on that request
alone. That amount is the cost recorded after an expiry, not a cost the gap caused.

--by-tool-kind attributes each Claude request's cost to the tool results it carries for
the first time (git, file read, search, test or build, subagent launch, other, or no
tool result), split evenly across kinds when a request carries several. --until-text
REGEX further reports the by-kind cost of only the requests before a reviewer's first
matching finding, whether that finding sits in an assistant text block or inside a
tool call's own input, such as a hand-back tool's message field. --window-agent
AGENT_ID prices the parent's own requests, never a
subagent's, from the request that launched agent-AGENT_ID.jsonl through --until,
read inclusive for this window alone.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import statistics
from datetime import date, datetime, timezone
from typing import Any, NamedTuple

# (uncached input, output, 5m write, 1h write, 30m write, cache read) in dollars per million
# tokens. A 30m write of None means the model sells no 30-minute write, and a request that
# records one stays unpriced.
Rate = tuple[float, float, float, float, float | None, float]


class PriceRow(NamedTuple):
    """What one model charges, and how long its provider keeps a prompt cache alive.

    The lifetime sits beside the rate because the two are halves of one fact. The rate says
    what a cached read costs, and the lifetime says how long that read stays on offer. A
    model added to one table and forgotten in the other would read as a deliberate silence.
    """

    rate: Rate
    # A Claude request records the lifetime it bought, so every Claude row names none here.
    lifetime: str | None = None
    # The published fast-mode rate, on a row whose model sells fast mode. It holds across the
    # whole context window, and the cache multiples apply on top of its input rate. Each model
    # publishes its own ratio, so the rate is written out rather than derived.
    fast: Rate | None = None


# The owner's price table is extension/src/pricing.ts in the Arc repository.
# test_transcript_usage.py compares this table with it whenever that checkout exists.


def price_row(input_rate: float, output: float, write_30m: float | None = None,
              read: float | None = None, lifetime: str | None = None, fast: Rate | None = None) -> PriceRow:
    # A 5m write costs 1.25 times input, a 1h write twice input, and a read a tenth of input
    # unless the provider publishes its own read rate.
    return PriceRow((input_rate, output, input_rate * 1.25, input_rate * 2, write_30m,
                     input_rate * 0.1 if read is None else read), lifetime, fast)


def scaled_rate(base: Rate, multiplier: float) -> Rate:
    return tuple(None if value is None else round(value * multiplier, 12) for value in base)  # type: ignore[return-value]


def scaled(base: PriceRow, multiplier: float) -> PriceRow:
    return PriceRow(scaled_rate(base.rate, multiplier), base.lifetime)


RATES: dict[str, PriceRow] = {
    "fable-5-1": price_row(10.0, 50.0, read=0.25),
    "fable-5": price_row(10.0, 50.0),
    # Fast mode is sold on the first-party Claude API alone, for Opus 5.5, Opus 5 and Opus 4.8.
    # https://platform.claude.com/docs/en/about-claude/pricing#fast-mode-pricing
    "opus-5-5": price_row(4.0, 20.0, read=0.2, fast=price_row(8.0, 40.0, read=0.4).rate),
    "opus-5": price_row(5.0, 25.0, fast=price_row(10.0, 50.0).rate),
    "opus-4": price_row(5.0, 25.0),
    "sonnet-5": price_row(2.0, 10.0),
    "sonnet-4": price_row(3.0, 15.0),
    "haiku-4.5": price_row(1.0, 5.0),
    "gpt-6-astra": price_row(10.0, 50.0, 12.5, 1.0, "30m"),
    "gpt-6-sol": price_row(2.0, 10.0, 2.5, 0.2, "30m"),
    "gpt-6-luna": price_row(0.1, 0.5, 0.125, 0.01, "30m"),
    "gpt-5.5": price_row(5.0, 30.0, None, 0.5, "5m"),
    "gpt-5.4": price_row(2.5, 15.0, None, 0.25, "5m"),
    # Promotional through SOL_PROMO_UNTIL. The standard rate is $5 input and $30 output.
    "gpt-5.6-sol": price_row(4.0, 20.0, 5.0, 0.4, "30m"),
    "gpt-5.6-terra": price_row(2.0, 12.0, 2.5, 0.2, "30m"),
    "gpt-5.6-luna": price_row(0.20, 1.20, 0.25, 0.02, "30m"),
    # The Codex rate card prices auto review at GPT-5.4 and charges no cache writes.
    "codex-auto-review": PriceRow((2.5, 15.0, 0.0, 0.0, 0.0, 0.25), "5m"),
}

CLAUDE_SUFFIX = r"(?:-\d{8})?(?:\[[^\]]+\])?$"
MODEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [(re.compile(pattern), name) for pattern, name in [
    (r"^claude-(?:fable|mythos)-5-1" + CLAUDE_SUFFIX, "fable-5-1"),
    (r"^claude-(?:fable|mythos)-5" + CLAUDE_SUFFIX, "fable-5"),
    (r"^claude-opus-5-5" + CLAUDE_SUFFIX, "opus-5-5"),
    (r"^claude-opus-(?:5|4-8)" + CLAUDE_SUFFIX, "opus-5"),
    (r"^claude-opus-(?:4-7|4-6|4-5)" + CLAUDE_SUFFIX, "opus-4"),
    (r"^claude-sonnet-5" + CLAUDE_SUFFIX, "sonnet-5"),
    (r"^claude-sonnet-4-[56]" + CLAUDE_SUFFIX, "sonnet-4"),
    (r"^claude-haiku-4-5" + CLAUDE_SUFFIX, "haiku-4.5"),
    (r"^gpt-6-astra$", "gpt-6-astra"),
    (r"^gpt-6-sol$", "gpt-6-sol"),
    (r"^gpt-6-luna$", "gpt-6-luna"),
    (r"^gpt-5\.5(?:-2026-04-23)?$", "gpt-5.5"),
    (r"^gpt-5\.4(?:-2026-03-05)?$", "gpt-5.4"),
    (r"^gpt-5\.6(?:-sol)?$", "gpt-5.6-sol"),
    (r"^gpt-5\.6-terra$", "gpt-5.6-terra"),
    (r"^gpt-5\.6-luna$", "gpt-5.6-luna"),
    (r"^codex-auto-review$", "codex-auto-review"),
]]

# Arc's Claude reader records no provider on a Claude request, so a Claude row prices only an
# id in the first-party spelling. A Bedrock id such as us.anthropic.claude-sonnet-4-5-20250929-v1:0
# and a Vertex id such as claude-sonnet-4-5@20250929 match no pattern and stay unpriced, as in Arc.

# A Claude request served in-region costs 1.1 times its rate, at either speed, on these models.
MODERN_CLAUDE = re.compile(r"^claude-(?:fable|mythos|opus|sonnet|haiku)-(?:5|4-[5678])\b")
IN_REGION_MULTIPLIER = 1.1

# Amazon Bedrock bills a Claude request on every inference profile except `global.` at 1.1 times
# list. A Bedrock request has a msg_bdrk_ message id, and the session's latest `model` attachment
# names the profile. Claude Code's Bedrock default is a `us.` profile, so an unknown profile pays too.
BEDROCK_MESSAGE_PREFIX = "msg_bdrk_"
BEDROCK_REGIONAL_MULTIPLIER = 1.1

# Bedrock ids name GPT-5.6 or GPT-6 with an optional region prefix. A global id costs the
# OpenAI list rate, with no promotion end, and a regional id costs 10% more.
BEDROCK_MODEL = re.compile(r"^(?:(global|[a-z]{2})\.)?openai\.gpt-(5\.6-(?:sol|terra|luna)|6-(?:sol|luna))$")

SOL_PROMO_UNTIL = date(2026, 11, 21)
# Past this many request input tokens, OpenAI bills input, cache writes and reads twice and
# output one and a half times, for the whole request.
OPENAI_LONG_CONTEXT_THRESHOLD = 272_000

# Each cache-write field names its lifetime and the seconds that write survives unread.
LIFETIMES: dict[str, tuple[str, float]] = {
    "write_5m": ("5m", 300.0), "write_30m": ("30m", 1800.0), "write_1h": ("1h", 3600.0),
}
LIFETIME_SECONDS: dict[str, float] = dict(LIFETIMES.values())
LIFETIME_NAMES = list(LIFETIME_SECONDS)
BOUNDARIES = ["after_a_user_turn", "inside_one_turn", "turn_unknown"]

# Claude records a prompt the reader sees as a `user` entry. These openers mark text the
# harness injected under the same entry type, and the reader counts no turn for them.
CLAUDE_INJECTED = ("<task-notification>", "<local-command-caveat>", "<local-command-stdout>",
                   "Another Claude session sent a message:")

# item_completed item types that carry a status field, each the completion of a tool call the
# reader already tracked as a pending name. A reasoning step, a chat message, a compaction and
# a subagent-activity marker carry no status field at all, so they never fail on their own.
CODEX_TOOL_ITEM_TYPES = {"CommandExecution", "FileChange", "McpToolCall", "CollabAgentToolCall"}


def event_date(timestamp: str | None) -> date | None:
    try:
        return date.fromisoformat((timestamp or "")[:10])
    except ValueError:
        return None


def parse_time(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if text[-1:] in {"Z", "z"}:
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def window_time(value: str) -> datetime:
    moment = parse_time(value)
    if moment is None:
        raise argparse.ArgumentTypeError("%s is not an ISO 8601 time with a Z suffix or an offset" % value)
    return moment


def utc_text(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def in_window(rows: list[dict[str, Any]], since: datetime | None, until: datetime | None) -> list[dict[str, Any]]:
    if since is None and until is None:
        return rows
    kept = []
    for row in rows:
        moment = parse_time(row.get("timestamp"))
        if moment is not None and (since is None or moment >= since) and (until is None or moment < until):
            kept.append(row)
    return kept


def first_request_time(rows: list[dict[str, Any]]) -> datetime | None:
    moments = [moment for moment in (parse_time(row.get("timestamp")) for row in rows) if moment is not None]
    return min(moments, default=None)


def rate_key(model: str | None) -> str | None:
    """Name the table row a model id resolves to, or None where the table holds none."""
    return next((key for pattern, key in MODEL_PATTERNS if pattern.match(model or "")), None)


def row_for(model: str | None) -> PriceRow | None:
    """Find a model's price row, in every spelling the table accepts."""
    name = model or ""
    bedrock = BEDROCK_MODEL.match(name)
    if bedrock:
        base = RATES["gpt-" + bedrock.group(2)]
        return base if bedrock.group(1) == "global" else scaled(base, 1.1)
    key = rate_key(name)
    return RATES[key] if key else None


def documented_lifetime(model: str | None) -> str | None:
    """Name the cache lifetime this model's provider documents, where it documents one."""
    row = row_for(model)
    return row.lifetime if row else None


def rate_for(model: str | None, timestamp: str | None = None) -> Rate | None:
    row = row_for(model)
    if row is None:
        return None
    if rate_key(model) == "gpt-5.6-sol":
        recorded = event_date(timestamp)
        if recorded is not None and recorded > SOL_PROMO_UNTIL:
            return None
    return row.rate


def has_long_context_tier(model: str | None) -> bool:
    name = model or ""
    return name.startswith("gpt-") or name == "codex-auto-review" or bool(BEDROCK_MODEL.match(name))


def request_rate(model: str | None, timestamp: str | None, context: int,
                 speed: str = "standard", inference_class: str | None = None,
                 bedrock_regional: bool = False) -> Rate | None:
    rate = speed_rate(model, timestamp, context, speed)
    if rate is None or not MODERN_CLAUDE.match(model or ""):
        return rate
    # The Bedrock charge replaces the in-region one, so a request never pays both.
    if bedrock_regional:
        return scaled_rate(rate, BEDROCK_REGIONAL_MULTIPLIER)
    if inference_class == "in-region":
        return scaled_rate(rate, IN_REGION_MULTIPLIER)
    return rate


def speed_rate(model: str | None, timestamp: str | None, context: int, speed: str) -> Rate | None:
    if speed == "fast":
        # A fast rate holds across the whole context window. Bedrock sells no fast mode.
        row = None if BEDROCK_MODEL.match(model or "") else row_for(model)
        return row.fast if row else None
    if speed != "standard":
        return None
    base = rate_for(model, timestamp)
    if base is None or not has_long_context_tier(model) or context <= OPENAI_LONG_CONTEXT_THRESHOLD:
        return base
    doubled = scaled_rate(base, 2)
    return doubled[:1] + (round(base[1] * 1.5, 12),) + doubled[2:]  # type: ignore[return-value]


def default_projects_dir() -> str:
    configured = os.environ.get("CLAUDE_PROJECTS_DIR")
    return configured or os.path.join(os.path.expanduser("~"), ".claude", "projects")


def default_codex_sessions_dir() -> str:
    configured = os.environ.get("CODEX_HOME")
    root = configured or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(root, "sessions")


def find_transcript(target: str, projects: str, codex_sessions: str) -> str:
    if os.path.isfile(target):
        return os.path.abspath(target)
    matches = glob.glob(os.path.join(projects, "*", target + ".jsonl"))
    matches += glob.glob(os.path.join(codex_sessions, "*", "*", "*", "rollout-*-" + target + ".jsonl"))
    if not matches:
        raise SystemExit("no transcript found for %s" % target)
    if len(matches) > 1:
        raise SystemExit("more than one transcript matched %s" % target)
    return os.path.abspath(matches[0])


def read_events(path: str) -> list[dict[str, Any]]:
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def entry_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(block.get("text") or "" for block in content if isinstance(block, dict))


def claude_user_turn(event: dict[str, Any]) -> bool:
    """Report whether this entry is a prompt the reader of the conversation saw."""
    if event.get("type") != "user" or "toolUseResult" in event:
        return False
    if event.get("isSidechain") or event.get("isMeta") or event.get("isCompactSummary"):
        return False
    if event.get("isVisibleInTranscriptOnly") or event.get("promptSource") == "system":
        return False
    origin = event.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in {None, "human"}:
        return False
    message = event.get("message") or {}
    content = message.get("content")
    if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return False
    return not entry_text(message).lstrip().startswith(CLAUDE_INJECTED)


def claude_compaction(event: dict[str, Any], boundaries_recorded: bool) -> bool:
    # Claude writes a `compact_boundary` system entry and a compaction summary for one
    # compaction. The reader counts one of the two so the running total stays exact.
    if boundaries_recorded:
        return event.get("subtype") == "compact_boundary"
    return bool(event.get("isCompactSummary"))


def codex_compaction(event: dict[str, Any], replacements_recorded: bool) -> bool:
    if replacements_recorded:
        return event.get("type") == "compacted"
    payload = event.get("payload") or {}
    return isinstance(payload, dict) and payload.get("type") in {"compaction", "context_compacted"}


def claude_inference_class(value: Any) -> str | None:
    """Read usage.inference_geo the way Arc reads it: us is in-region, and any other named region is geo."""
    if not isinstance(value, str):
        return None
    geo = value.lower()
    if geo == "global":
        return "global"
    if geo in ("us", "us-only", "in-region"):
        return "in-region"
    if geo in ("not_available", "not-available"):
        return None
    return "geo" if geo else None


def claude_requests(events: list[dict[str, Any]], child: bool = False) -> list[dict[str, Any]]:
    order: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    tool_owner: dict[str, str] = {}
    boundaries = any(event.get("subtype") == "compact_boundary" for event in events)
    turns = 0
    compactions = 0
    profile: str | None = None
    for event in events:
        attachment = event.get("attachment")
        if event.get("type") == "attachment" and isinstance(attachment, dict) and attachment.get("type") == "model":
            # A sidechain attachment in a parent transcript names a child agent's model, not the parent's.
            if child or not event.get("isSidechain"):
                profile = (attachment.get("identity") or {}).get("modelId") or profile
            continue
        if claude_compaction(event, boundaries):
            compactions += 1
            continue
        if claude_user_turn(event):
            turns += 1
            continue
        if event.get("type") == "user":
            # A tool result names the tool_use_id it answers. The reader charges a failure to
            # the request that issued the call, never to the request that reads the result back.
            content = (event.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        owner = rows.get(tool_owner.get(block.get("tool_use_id") or ""))
                        if owner is not None:
                            owner["failed_tools"] += 1
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        if message.get("model") == "<synthetic>":
            continue
        request_id = message.get("id") or event.get("requestId")
        if not request_id:
            continue
        usage = message.get("usage") or {}
        if usage and (request_id not in rows or child):
            creation = usage.get("cache_creation") or {}
            write_5m = creation.get("ephemeral_5m_input_tokens")
            write_1h = creation.get("ephemeral_1h_input_tokens")
            if write_5m is None and write_1h is None:
                write_5m = usage.get("cache_creation_input_tokens") or 0
                write_1h = 0
            if request_id not in rows:
                rows[request_id] = {"tools": [], "failed_tools": 0, "turn_ordinal": turns,
                                    "compactions_before": compactions, "request_id": request_id}
                order.append(request_id)
            speed = usage.get("speed")
            bedrock = (message.get("id") or "").startswith(BEDROCK_MESSAGE_PREFIX)
            rows[request_id].update({
                "model": message.get("model"), "timestamp": event.get("timestamp"),
                "uncached_input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                "reasoning_output": 0, "cache_read": usage.get("cache_read_input_tokens") or 0,
                "write_5m": write_5m or 0, "write_1h": write_1h or 0, "write_30m": 0,
                # A fast request is priced at its model's fast-mode rate. An older transcript
                # records no speed at all, and that reads the same as "standard".
                "speed": speed if isinstance(speed, str) and speed else "standard",
                # A Bedrock request never pays the in-region charge. A global. one pays list.
                "inference_class": None if bedrock else claude_inference_class(usage.get("inference_geo")),
                "bedrock_regional": bedrock and not (profile or "").startswith("global."),
            })
        if request_id not in rows:
            continue
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                rows[request_id]["tools"].append(block.get("name"))
                tool_use_id = block.get("id")
                if tool_use_id:
                    tool_owner[tool_use_id] = request_id
    found = [rows[key] for key in order]
    if not turns:
        for row in found:
            row["turn_ordinal"] = None
    return found


SHELL_TOOL_NAMES = {"Bash", "PowerShell"}
SUBAGENT_TOOL_NAMES = {"Agent", "Task"}
NO_TOOL_RESULT_KIND = "no tool result"
KIND_ORDER = ["git", "file read", "search", "test or build", "subagent launch", "other", NO_TOOL_RESULT_KIND]

# A PowerShell assignment is always its own statement, such as `$env:VAR = 'x'; git diff`, so
# the reader skips a whole segment that matches this. A Bash assignment instead prefixes the
# real command in the same segment, such as `VAR=x git diff` or `MSG="a b" git commit`, so the
# reader skips leading words that match this one at a time.
POWERSHELL_ASSIGNMENT = re.compile(r"^\$(?:env:)?[A-Za-z_]\w*\s*=")
BASH_ASSIGNMENT_WORD = re.compile(r"^[A-Za-z_]\w*=")
SKIP_PROGRAMS = {"cd", "set-location"}

# A heredoc body can hold its own `&&`, `;`, `||`, or newlines, none of which end the command
# that opens it. This matches the opening line through the line that repeats the same marker
# word after `<<` or `<<-`, so the reader can blank the body out before splitting on those.
HEREDOC_BODY = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.MULTILINE | re.DOTALL)

SIMPLE_SHELL_KIND: dict[str, str] = {
    "git": "git",
    "cat": "file read", "head": "file read", "tail": "file read",
    "less": "file read", "type": "file read", "get-content": "file read",
    "rg": "search", "grep": "search", "find": "search", "ls": "search",
    "select-string": "search", "get-childitem": "search",
    "npm": "test or build", "npx": "test or build", "pnpm": "test or build", "yarn": "test or build",
    "pytest": "test or build", "vitest": "test or build", "tsc": "test or build", "make": "test or build",
    "cargo": "test or build", "dotnet": "test or build",
}

# A `cat` or `tee` that redirects to a file writes it instead of reading it, such as
# `cat > f <<EOF` or `cat >> f`. These programs stay in SIMPLE_SHELL_KIND for the plain
# read case and drop to "other" here when a redirect is present.
FILE_WRITER_PROGRAMS = {"cat", "tee"}


def strip_heredoc_bodies(command: str) -> str:
    """Blank out each heredoc body, so a literal `&&`, `;`, `||`, or newline inside it never
    splits the command it belongs to. Keeps the opening line, which still names the program."""
    return HEREDOC_BODY.sub(lambda match: match.group(1), command)


def command_program_and_args(command: str) -> tuple[str, list[str]] | None:
    """Name the first real program a shell command runs, and the words that follow it.

    Walks past `&&`, `;`, `||`, and a newline to skip a leading `cd`, `Set-Location`, or an
    environment assignment, so `cd x && git diff` and `cd x\\ngit diff` both name `git`. A
    segment is tokenized with `shlex`, so a quoted assignment such as `MSG="a b" git commit`
    is skipped as one word rather than split apart by the space inside its quotes.
    """
    for segment in re.split(r"&&|\|\||;|\r?\n", strip_heredoc_bodies(command or "")):
        segment = segment.strip()
        if not segment or POWERSHELL_ASSIGNMENT.match(segment):
            continue
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        index = 0
        while index < len(words) and BASH_ASSIGNMENT_WORD.match(words[index]):
            index += 1
        if index >= len(words):
            continue
        program = words[index]
        if program.lower() in SKIP_PROGRAMS:
            continue
        name = program.replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower().endswith(".exe"):
            name = name[:-4]
        return name, words[index + 1:]
    return None


def classify_shell_command(command: str) -> str:
    """Name the cost kind of one Bash or PowerShell command, by its first real program.

    A handful of programs run more than one kind of work, and the reader tells them apart by
    the words that follow: `sed` reads a file only with `-n`, `python` and `node` build or
    test only with a named module or flag, `go` builds or tests only with `test`, and `cat` or
    `tee` reads only when it carries no `>` or `>>` redirect to a file.
    """
    found = command_program_and_args(command)
    if found is None:
        return "other"
    program, rest = found
    key = program.lower()
    if key in FILE_WRITER_PROGRAMS and any(word.startswith(">") for word in rest):
        return "other"
    if key == "sed":
        return "file read" if "-n" in rest else "other"
    if key in {"python", "python3"}:
        return "test or build" if rest[:2] in (["-m", "pytest"], ["-m", "unittest"]) else "other"
    if key == "go":
        return "test or build" if rest[:1] == ["test"] else "other"
    if key == "node":
        return "test or build" if "--test" in rest else "other"
    return SIMPLE_SHELL_KIND.get(key, "other")


def classify_tool_use(name: str | None, tool_input: dict[str, Any] | None) -> str:
    """Name the cost kind of one tool call, by its tool name and, for a shell tool, its command."""
    if name in SUBAGENT_TOOL_NAMES:
        return "subagent launch"
    if name == "Read":
        return "file read"
    if name in {"Grep", "Glob"}:
        return "search"
    if name in SHELL_TOOL_NAMES:
        return classify_shell_command((tool_input or {}).get("command") or "")
    return "other"


def flatten_strings(value: Any) -> list[str]:
    """Collect every string leaf inside a JSON-like value, so a regex can search all of them.

    A tool call's own input can be the only place a model states a finding, such as a hand-back
    tool whose "message" field carries the finding text and whose call raises no separate
    assistant text block at all.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in flatten_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in flatten_strings(item)]
    return []


def claude_request_tool_info(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pair each Claude request with the tool results it carries for the first time, and its own text.

    Extends the tool_use/tool_result id pairing claude_requests keeps for failed-tool
    attribution. A tool_result answers a tool_use from an earlier request and appears in a
    later user event, and the assistant request that follows carries that result into its
    prompt for the first time. The reader traces each new result back to its tool_use to
    learn the tool's name and input, and records the assistant's own text, together with
    every string field inside its own tool calls' input, so a caller can find a reviewer's
    first finding even when it is stated only inside a hand-back tool call's input.
    """
    tool_index: dict[str, tuple[str | None, dict[str, Any] | None]] = {}
    seen_results: set[str] = set()
    pending: list[str] = []
    seen_requests: set[str] = set()
    info: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = event.get("type")
        if etype == "user":
            content = (event.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id")
                        if tool_use_id and tool_use_id not in seen_results:
                            seen_results.add(tool_use_id)
                            pending.append(tool_use_id)
            continue
        if etype != "assistant":
            continue
        message = event.get("message") or {}
        if message.get("model") == "<synthetic>":
            continue
        request_id = message.get("id") or event.get("requestId")
        if not request_id:
            continue
        if request_id not in seen_requests:
            seen_requests.add(request_id)
            info[request_id] = {
                "new_results": [tool_index.get(tool_use_id, (None, None)) for tool_use_id in pending],
                "text": "",
            }
            pending = []
        info[request_id]["text"] += entry_text(message)
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_id = block.get("id")
                tool_input = block.get("input")
                if tool_use_id:
                    tool_index[tool_use_id] = (block.get("name"), tool_input)
                for text in flatten_strings(tool_input):
                    info[request_id]["text"] += "\n" + text
    return info


def tool_kind_costs(rows: list[dict[str, Any]], info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Attribute each request's cost to the tool results it carries for the first time.

    A request with two or more new results splits its cost equally across their kinds. The
    requests figure counts a request once in every kind it touches, not a fraction of one,
    so a split request's per-kind counts can sum to more than the request total. A whole
    number is what a reader compares against a manual tally, and the split is still visible
    from the row-level cost.
    """
    totals = {kind: 0.0 for kind in KIND_ORDER}
    request_counts = {kind: 0 for kind in KIND_ORDER}
    unpriced_requests = 0
    for row in rows:
        cost = cost_of(row)
        entry = info.get(row.get("request_id")) or {}
        results = entry.get("new_results") or []
        kinds = [classify_tool_use(name, tool_input) for name, tool_input in results] or [NO_TOOL_RESULT_KIND]
        for kind in set(kinds):
            request_counts[kind] += 1
        if cost is None:
            unpriced_requests += 1
            continue
        share = cost / len(kinds)
        for kind in kinds:
            totals[kind] += share
    total_cost = sum(totals.values())
    rows_out = [{
        "kind": kind, "requests": request_counts[kind], "cost_usd": round(totals[kind], 4),
        "share_of_cost": round(totals[kind] / total_cost, 4) if total_cost else None,
    } for kind in KIND_ORDER if request_counts[kind] or totals[kind]]
    return {
        "rows": rows_out, "unpriced_requests": unpriced_requests,
        "requests_note": ("requests counts a request once for every kind it touches, so a request "
                          "split across kinds is counted once in each and the per-kind counts can "
                          "sum to more than the request total"),
    }


def until_text_report(rows: list[dict[str, Any]], info: dict[str, dict[str, Any]], pattern: str) -> dict[str, Any]:
    """Report the by-kind cost of the requests before a reviewer's first finding.

    Scans each request's own assistant text, together with every string field inside that
    request's own tool calls' input, in order, for the first match of `pattern`
    (case-sensitive, multiline). A finding stated only inside a hand-back tool call's input,
    with no separate text block, still marks the cutoff. Only the requests strictly before the
    match are priced here, since the matching request is the finding itself, not the cost of
    reaching it.
    """
    matcher = re.compile(pattern, re.MULTILINE)
    cutoff = None
    for index, row in enumerate(rows):
        text = (info.get(row.get("request_id")) or {}).get("text", "")
        if matcher.search(text):
            cutoff = index
            break
    matched = cutoff is not None
    kept = rows[:cutoff] if matched else rows
    result = {"pattern": pattern, "matched": matched, "by_tool_kind": tool_kind_costs(kept, info)}
    if not matched:
        result["note"] = "no assistant text matched the pattern, so every request is counted"
    return result


AGENT_ID_PATTERN = re.compile(r"agentId:\s*(\S+)")


def find_agent_launch_request(events: list[dict[str, Any]], agent_id: str,
                              subagents_dir: str | None = None) -> str | None:
    """Find the request id whose Agent tool_use launched the subagent named by this id.

    Prefers the structured `agentId` Claude Code stamps in a tool-result event's own
    `toolUseResult` field. Some real launches carry no such field, so the reader falls back to
    the `toolUseId` recorded in the subagent's own `agent-<id>.meta.json` file, when
    `subagents_dir` names where one would sit, and only then to the `agentId:` trailer inside
    the result text, checking every match in that text rather than only the first, since an
    earlier quoted agent id can appear before the real one. Each path traces a tool_use_id
    back to the request that made the call, the same id pairing claude_requests keeps for a
    failed tool_result.
    """
    tool_owner: dict[str, str] = {}
    text_match: str | None = None
    for event in events:
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            request_id = message.get("id") or event.get("requestId")
            for block in message.get("content") or []:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") in SUBAGENT_TOOL_NAMES):
                    tool_use_id = block.get("id")
                    if tool_use_id and request_id:
                        tool_owner[tool_use_id] = request_id
            continue
        if event.get("type") != "user":
            continue
        content = (event.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        result = event.get("toolUseResult")
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            owner = tool_owner.get(block.get("tool_use_id") or "")
            if not owner:
                continue
            if isinstance(result, dict) and result.get("agentId") == agent_id:
                return owner
            if text_match is None:
                text = entry_text({"content": block.get("content")})
                for match in AGENT_ID_PATTERN.finditer(text):
                    if match.group(1).strip(".,;:()") == agent_id:
                        text_match = owner
                        break
    if subagents_dir:
        meta_path = os.path.join(subagents_dir, "agent-%s.meta.json" % agent_id)
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            meta = {}
        owner = tool_owner.get(meta.get("toolUseId") or "")
        if owner:
            return owner
    return text_match


def parent_window_report(rows: list[dict[str, Any]], start_request_id: str, until: datetime | None,
                         info: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Price a parent transcript's own requests from a launch request through an inclusive end.

    The window starts at the request that launched a subagent, found by --window-agent, and
    ends at --until. This --until reads inclusive, unlike the exclusive --until the rest of
    the report uses, because a request stamped exactly at the analyst's named boundary must
    still count, and the launching request itself always opens the window.
    """
    start_index = next((index for index, row in enumerate(rows) if row.get("request_id") == start_request_id), None)
    window_rows = [] if start_index is None else rows[start_index:]
    if until is not None:
        kept = []
        for row in window_rows:
            moment = parse_time(row.get("timestamp"))
            if moment is not None and moment <= until:
                kept.append(row)
        window_rows = kept
    costs = [cost_of(row) for row in window_rows]
    all_priced = all(cost is not None for cost in costs)
    contexts = [context_of(row) for row in window_rows]
    result: dict[str, Any] = {
        "agent_id_start_request": start_request_id, "until": utc_text(until),
        "requests": len(window_rows), "cost_usd": round(sum(costs), 4) if all_priced else None,
        "context_at_start": contexts[0] if contexts else None,
        "context_at_peak": max(contexts) if contexts else None,
    }
    if info is not None:
        result["by_tool_kind"] = tool_kind_costs(window_rows, info)
    return result


def codex_requests(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models: dict[str, str | None] = {}
    current_model: str | None = None
    pending_tools: list[str | None] = []
    order: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    replacements = any(event.get("type") == "compacted" for event in events)
    compactions = 0
    for event in events:
        if codex_compaction(event, replacements):
            compactions += 1
            continue
        kind = event.get("type")
        payload = event.get("payload") or {}
        if kind == "turn_context":
            current_model = payload.get("model") or current_model
            turn_id = payload.get("turn_id")
            if turn_id:
                models[turn_id] = current_model
            continue
        if kind == "response_item" and payload.get("type") in {"function_call", "custom_tool_call"}:
            pending_tools.append(payload.get("name"))
            continue
        if kind != "token_usage_record":
            continue
        request_id = payload.get("response_id")
        usage = payload.get("usage") or {}
        if not request_id or request_id in rows or not usage:
            pending_tools.clear()
            continue
        total_input = usage.get("input_tokens") or 0
        cached = usage.get("cached_input_tokens") or 0
        written = usage.get("cache_write_input_tokens") or 0
        rows[request_id] = {
            "model": models.get(payload.get("turn_id"), current_model), "timestamp": event.get("timestamp"),
            "uncached_input": max(0, total_input - cached - written), "output": usage.get("output_tokens") or 0,
            "reasoning_output": usage.get("reasoning_output_tokens") or 0, "cache_read": cached,
            "write_5m": 0, "write_1h": 0, "write_30m": written, "tools": list(pending_tools),
            # A Codex rollout marks no prompt the reader saw, so it carries no turn identity.
            "turn_ordinal": None, "compactions_before": compactions,
        }
        pending_tools.clear()
        order.append(request_id)
    return [rows[key] for key in order]


def codex_failed_tool_items(events: list[dict[str, Any]], since: datetime | None = None,
                            until: datetime | None = None) -> int:
    """Count a Codex tool item that completed as failed, inside --since and --until.

    Codex marks a failed tool call on the item_completed event, never on the call's own
    function_call_output or custom_tool_call_output record, and neither record names the
    other. No join links a failed item back to the request that issued its call, so the
    reader counts the failure for the transcript and charges it to no request. The
    item_completed event carries its own timestamp, filtered the same way in_window
    filters a request row.
    """
    count = 0
    for event in events:
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload") or {}
        if payload.get("type") != "item_completed":
            continue
        item = payload.get("item") or {}
        if item.get("type") not in CODEX_TOOL_ITEM_TYPES or item.get("status") != "failed":
            continue
        if since is not None or until is not None:
            moment = parse_time(event.get("timestamp"))
            if moment is None or (since is not None and moment < since) or (until is not None and moment >= until):
                continue
        count += 1
    return count


def is_subagent_path(path: str) -> bool:
    """True when path names one subagent's own transcript file, addressed directly.

    Matches the same `subagents` directory the parent report globs to find its own children,
    so a subagent file read on its own prices its streamed requests the same way the parent
    report prices that file as a child: keeping each one's final usage row, not its first.
    """
    return os.path.basename(os.path.dirname(path)) == "subagents"


def read_requests(path: str, child: bool = False, since: datetime | None = None,
                   until: datetime | None = None) -> tuple[list[dict[str, Any]], int | None]:
    events = read_events(path)
    if any(event.get("type") == "token_usage_record" for event in events):
        return codex_requests(events), codex_failed_tool_items(events, since, until)
    return claude_requests(events, child or is_subagent_path(path)), None


def context_of(row: dict[str, Any]) -> int:
    return row["uncached_input"] + row["cache_read"] + row["write_5m"] + row["write_1h"] + row["write_30m"]


def row_rate(row: dict[str, Any]) -> Rate | None:
    """Find the rate one request billed at, from its model, date, size, speed and inference class."""
    return request_rate(row["model"], row.get("timestamp"), context_of(row), row.get("speed", "standard"),
                        row.get("inference_class"), row.get("bedrock_regional", False))


def cost_of(row: dict[str, Any]) -> float | None:
    # A fast request on a model with no fast rate, or a speed the table does not know, stays
    # unpriced rather than quote the standard rate for a request that billed more.
    rate = row_rate(row)
    if rate is None:
        return None
    uncached, output, write_5m, write_1h, write_30m, read = rate
    if write_30m is None:
        if row["write_30m"]:
            return None
        write_30m = 0.0
    return (row["uncached_input"] * uncached + row["output"] * output
            + row["write_5m"] * write_5m + row["write_1h"] * write_1h
            + row["write_30m"] * write_30m + row["cache_read"] * read) / 1_000_000


def input_cost_of(row: dict[str, Any]) -> float | None:
    """Price the uncached input and every cache write on one request, and nothing else."""
    rate = row_rate(row)
    if rate is None:
        return None
    uncached, _output, write_5m, write_1h, write_30m, _read = rate
    if write_30m is None:
        if row["write_30m"]:
            return None
        write_30m = 0.0
    return (row["uncached_input"] * uncached + row["write_5m"] * write_5m
            + row["write_1h"] * write_1h + row["write_30m"] * write_30m) / 1_000_000


def read_cost_of(rows: list[dict[str, Any]]) -> float:
    """Sum cache-read cost at each priced request's own rate."""
    total = 0.0
    for row in rows:
        rate = row_rate(row)
        if rate is not None:
            total += row["cache_read"] * rate[5] / 1_000_000
    return total


def cache_lifetime(row: dict[str, Any]) -> tuple[str, float] | None:
    """Name the lifetime of the cache one request leaves behind, and the seconds it survives.

    A single recorded write tier names the lifetime outright, which is how a Claude request
    answers. A record naming two tiers names no single lifetime, and the documented figure
    must not settle it. Where the record names no tier, the model's documented lifetime
    answers, which is how a Codex request answers. A request that neither read a cache nor
    wrote one left no cache behind, so it names no lifetime at all.
    """
    tiers = [LIFETIMES[field] for field in LIFETIMES if row[field]]
    if tiers:
        return tiers[0] if len(tiers) == 1 else None
    if not row["cache_read"]:
        return None
    name = documented_lifetime(row["model"])
    return None if name is None else (name, LIFETIME_SECONDS[name])


def cache_expiry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count the requests that paid cold input after an idle gap reached a cache lifetime."""
    by_lifetime = {name: 0 for name in LIFETIME_NAMES}
    by_boundary = {name: 0 for name in BOUNDARIES}
    found: list[dict[str, Any]] = []
    costs: list[float | None] = []
    unavailable = 0
    ambiguous = 0
    for before, after in zip(rows, rows[1:]):
        written = parse_time(before.get("timestamp"))
        returned = parse_time(after.get("timestamp"))
        cold = after["uncached_input"] > 0 or any(after[field] for field in LIFETIMES)
        if written is None or returned is None or not cold:
            continue
        resolved = cache_lifetime(before)
        if resolved is None:
            unavailable += 1
            continue
        name, seconds = resolved
        gap = (returned - written).total_seconds()
        if gap < seconds:
            continue
        compacted = before.get("compactions_before", 0) != after.get("compactions_before", 0)
        if before["model"] != after["model"] or compacted:
            ambiguous += 1
            continue
        first, second = before.get("turn_ordinal"), after.get("turn_ordinal")
        if first is None or second is None:
            boundary = "turn_unknown"
        else:
            boundary = "after_a_user_turn" if second > first else "inside_one_turn"
        cost = input_cost_of(after)
        by_lifetime[name] += 1
        by_boundary[boundary] += 1
        costs.append(cost)
        found.append({
            "timestamp": after.get("timestamp"), "gap_seconds": round(gap), "lifetime": name,
            "boundary": boundary, "model": after["model"],
            "input_cost_usd": None if cost is None else round(cost, 4),
        })
    priced = all(cost is not None for cost in costs)
    ranked = sorted(zip(costs, found), key=lambda pair: (pair[0] is None, -(pair[0] or 0.0)))
    return {
        "returns": len(found),
        "input_cost_after_expiry_usd": round(sum(cost or 0.0 for cost in costs), 4) if priced else None,
        "by_lifetime": by_lifetime, "by_boundary": by_boundary,
        "longest_gap_seconds": max((item["gap_seconds"] for item in found), default=None),
        "unavailable_lifetime": unavailable, "excluded_ambiguous": ambiguous,
        "largest": [item for _, item in ranked[:5]],
    }


def summarize(rows: list[dict[str, Any]], codex_failed_tool_calls: int | None = None) -> dict[str, Any]:
    costs = [cost_of(row) for row in rows]
    if codex_failed_tool_calls is not None:
        # Codex names no request for a failed item, so the reader reports a transcript-level
        # count and never prices it. Zero here is a real count, not an unpriced measurement,
        # so the dollar figure still reads null rather than 0.
        failed_tool_calls = codex_failed_tool_calls
        failed_tool_call_requests = 0
        failed_tool_call_cost_usd = None
    else:
        failed_costs = [cost for row, cost in zip(rows, costs) if row.get("failed_tools", 0) > 0]
        failed_priced = all(cost is not None for cost in failed_costs)
        failed_tool_calls = sum(row.get("failed_tools", 0) for row in rows)
        failed_tool_call_requests = len(failed_costs)
        failed_tool_call_cost_usd = round(sum(cost or 0.0 for cost in failed_costs), 4) if failed_priced else None
    if not rows:
        # A window can leave a transcript with no request row inside it while a failure still
        # sits inside it, so the failed-tool-call keys stay in the report even here. The return
        # carries every key the non-empty return carries. A sum over zero rows is a true zero,
        # a list over zero rows is empty, and a rate, a mean, or a first value has no zero-row
        # meaning and reads None.
        return {
            "requests": 0, "priced_requests": 0, "unpriced_requests": 0,
            "cost_usd": 0, "priced_cost_usd": 0,
            "cost_per_request": None,
            "cost_per_request_by_fifth": [], "context_mean": None,
            "context_max": None, "context_first": None,
            "uncached_input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "cache_read_share_of_cost": None,
            "tool_calls": 0,
            "requests_with_two_or_more_tools": 0,
            "failed_tool_calls": failed_tool_calls,
            "failed_tool_call_requests": failed_tool_call_requests,
            "failed_tool_call_cost_usd": failed_tool_call_cost_usd,
            "models": [],
            "unpriced_models": [],
            "fast_mode_unpriced_requests": 0,
            "cache_expiry": cache_expiry(rows),
        }
    contexts = [context_of(row) for row in rows]
    priced = [cost for cost in costs if cost is not None]
    all_priced = len(priced) == len(rows)
    total = sum(priced)
    fifth = max(1, len(rows) // 5)
    parts = []
    for start in range(0, len(rows), fifth):
        chunk = costs[start:start + fifth]
        parts.append(round(sum(cost for cost in chunk if cost is not None) / len(chunk), 4)
                     if all(cost is not None for cost in chunk) else None)
    read_cost = read_cost_of(rows)
    fast_mode_unpriced_requests = sum(1 for row, cost in zip(rows, costs)
                                       if cost is None and row.get("speed", "standard") != "standard")
    return {
        "requests": len(rows), "priced_requests": len(priced), "unpriced_requests": len(rows) - len(priced),
        "cost_usd": round(total, 4) if all_priced else None, "priced_cost_usd": round(total, 4),
        "cost_per_request": round(total / len(rows), 4) if all_priced else None,
        "cost_per_request_by_fifth": parts[:5], "context_mean": round(statistics.mean(contexts)),
        "context_max": max(contexts), "context_first": contexts[0],
        "uncached_input_tokens": sum(row["uncached_input"] for row in rows),
        "cached_input_tokens": sum(row["cache_read"] for row in rows),
        "cache_write_input_tokens": sum(row["write_5m"] + row["write_1h"] + row["write_30m"] for row in rows),
        "output_tokens": sum(row["output"] for row in rows),
        "reasoning_output_tokens": sum(row["reasoning_output"] for row in rows),
        "cache_read_share_of_cost": round(read_cost / total, 3) if all_priced and total else None,
        "tool_calls": sum(len(row["tools"]) for row in rows),
        "requests_with_two_or_more_tools": sum(1 for row in rows if len(row["tools"]) >= 2),
        "failed_tool_calls": failed_tool_calls,
        "failed_tool_call_requests": failed_tool_call_requests,
        "failed_tool_call_cost_usd": failed_tool_call_cost_usd,
        "models": sorted({row["model"] for row in rows if row["model"]}),
        # A fast request on a model with no rate carries two gaps, so the model is listed
        # whenever the request would still go unpriced at the standard speed.
        "unpriced_models": sorted({row["model"] or "unknown" for row, cost in zip(rows, costs)
                                    if cost is None and cost_of({**row, "speed": "standard"}) is None}),
        "fast_mode_unpriced_requests": fast_mode_unpriced_requests,
        "cache_expiry": cache_expiry(rows),
    }


def session_meta(path: str) -> dict[str, Any] | None:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "session_meta":
                return event.get("payload") or {}
            if event.get("type") not in {"world_state", "inter_agent_communication_metadata"}:
                return None
    return None


def codex_child_transcripts(path: str, sessions_root: str) -> list[tuple[str, dict[str, Any]]]:
    parent = session_meta(path)
    if not parent or not parent.get("id"):
        return []
    by_parent: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    pattern = os.path.join(sessions_root, "*", "*", "*", "rollout-*.jsonl")
    for candidate in glob.glob(pattern):
        meta = session_meta(candidate)
        if not meta:
            continue
        parent_id = meta.get("parent_thread_id")
        if parent_id:
            by_parent.setdefault(parent_id, []).append((candidate, meta))
    found: list[tuple[str, dict[str, Any]]] = []
    queue = [parent["id"]]
    seen = {os.path.abspath(path)}
    while queue:
        parent_id = queue.pop(0)
        for candidate, meta in sorted(by_parent.get(parent_id, [])):
            absolute = os.path.abspath(candidate)
            if absolute in seen:
                continue
            seen.add(absolute)
            found.append((absolute, meta))
            child_id = meta.get("id")
            if child_id:
                queue.append(child_id)
    return found


def child_label(meta: dict[str, Any]) -> str | None:
    source = meta.get("source")
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return None
    spawn = subagent.get("thread_spawn")
    if isinstance(spawn, dict):
        return spawn.get("agent_path")
    return subagent.get("other")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="session id, or a path to a transcript")
    parser.add_argument("--projects", default=default_projects_dir(), help="Claude projects directory")
    parser.add_argument("--codex-sessions", default=default_codex_sessions_dir(), help="Codex sessions directory")
    parser.add_argument("--since", type=window_time,
                        help="count only requests at or after this ISO 8601 time, with a Z suffix or an offset")
    parser.add_argument("--until", type=window_time,
                        help="count only requests before this ISO 8601 time, with a Z suffix or an offset")
    parser.add_argument("--by-tool-kind", action="store_true",
                        help="attribute each request's cost to the tool results it carries for the "
                             "first time (git, file read, search, test or build, subagent launch, "
                             "other, or no tool result), split evenly when a request carries several. "
                             "A row's requests figure counts a request once for every kind it touches, "
                             "not a fraction, so per-kind counts can sum to more than the request total. "
                             "Reads a Claude Code transcript; a Codex transcript reports every request "
                             "as no tool result. Point the target at one subagents/agent-*.jsonl file "
                             "to price that subagent's own tool-kind costs.")
    parser.add_argument("--until-text", metavar="REGEX",
                        help="with --by-tool-kind, also report the by-kind cost of only the requests "
                             "before the first assistant text block, or tool call input string field, "
                             "matching this regex (case-sensitive, multiline); reports and uses every "
                             "request when nothing matches")
    parser.add_argument("--window-agent", metavar="AGENT_ID",
                        help="price the parent transcript's own requests from the request that launched "
                             "the subagent named agent-AGENT_ID.jsonl through --until, inclusive; never "
                             "includes subagent requests; exits non-zero when the agent id is not found")
    args = parser.parse_args(argv)
    windowed = args.since is not None or args.until is not None
    path = find_transcript(args.target, args.projects, args.codex_sessions)
    parent_rows, parent_failed = read_requests(path, since=args.since, until=args.until)
    parent = summarize(in_window(parent_rows, args.since, args.until), parent_failed)
    started: list[tuple[datetime | None, dict[str, Any]]] = []
    child_dir = os.path.join(os.path.dirname(path), os.path.basename(path)[:-6], "subagents")
    claude_children = [(child, {}) for child in sorted(glob.glob(os.path.join(child_dir, "*.jsonl")))]
    codex_children = codex_child_transcripts(path, args.codex_sessions)
    for child, meta in claude_children + codex_children:
        child_rows, child_failed = read_requests(child, child=True, since=args.since, until=args.until)
        rows = in_window(child_rows, args.since, args.until)
        if windowed and not rows and not child_failed:
            # A window can leave a child with no request row while an in-window failure still
            # belongs to it, so only a child with neither is left out of the report.
            continue
        entry = summarize(rows, child_failed)
        entry["file"] = os.path.basename(child)
        label = child_label(meta)
        if label:
            entry["agent"] = label
        started.append((first_request_time(rows), entry))
    started.sort(key=lambda item: (item[0] is None, item[0] or datetime.min.replace(tzinfo=timezone.utc)))
    children = [entry for _, entry in started]
    summaries = [parent] + children
    combined_requests = sum(summary.get("requests", 0) for summary in summaries)
    combined_priced = sum(summary.get("priced_requests", 0) for summary in summaries)
    combined_cost = sum(summary.get("priced_cost_usd", 0) for summary in summaries)
    all_priced = combined_priced == combined_requests
    expiries = [summary["cache_expiry"] for summary in summaries]
    dollars = [entry["input_cost_after_expiry_usd"] for entry in expiries]
    report: dict[str, Any] = {"transcript": path}
    if windowed:
        report["window"] = {"since": utc_text(args.since), "until": utc_text(args.until)}
    report.update({
        "parent": parent, "children": children,
        "combined": {
            "cost_usd": round(combined_cost, 4) if all_priced else None,
            "priced_cost_usd": round(combined_cost, 4), "requests": combined_requests,
            "priced_requests": combined_priced, "unpriced_requests": combined_requests - combined_priced,
            "cost_per_request": round(combined_cost / combined_requests, 4) if all_priced and combined_requests else None,
            "cache_expiry": {
                "returns": sum(entry["returns"] for entry in expiries),
                "input_cost_after_expiry_usd": (round(sum(value or 0.0 for value in dollars), 4)
                                                if all(value is not None for value in dollars) else None),
                "by_lifetime": {name: sum(entry["by_lifetime"][name] for entry in expiries)
                                for name in LIFETIME_NAMES},
                "by_boundary": {name: sum(entry["by_boundary"][name] for entry in expiries) for name in BOUNDARIES},
            },
        },
        "rate_note": "List-price estimate, not a subscription charge. GPT-5.6 Sol uses its recorded promotional rate through 2026-11-21. Later Sol requests remain unpriced until the table is updated.",
        "cache_expiry_note": "A Codex rollout records no cache write, so the model's documented lifetime answers instead. A Codex rollout also marks no prompt the reader saw. A Claude subagent transcript marks every prompt as a sidechain, so its returns count under turn_unknown.",
    })
    needs_events = args.by_tool_kind or args.window_agent
    events = read_events(path) if needs_events else None
    info = claude_request_tool_info(events) if args.by_tool_kind else None
    if args.by_tool_kind:
        windowed_rows = in_window(parent_rows, args.since, args.until)
        report["by_tool_kind"] = tool_kind_costs(windowed_rows, info)
        if args.until_text:
            report["until_text"] = until_text_report(windowed_rows, info, args.until_text)
    if args.window_agent:
        start_id = find_agent_launch_request(events, args.window_agent, child_dir)
        if start_id is None:
            raise SystemExit("no Agent launch found for agent id %r in %s" % (args.window_agent, path))
        report["parent_window"] = parent_window_report(parent_rows, start_id, args.until, info)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
