#!/usr/bin/env python3
"""Check that each review finding's quote exists at its cited file and line.

  python quote_check.py --root DIR [--report FILE] [--window N] [--format json|text]

This is the mechanical half of the "Verify Findings Before Acting" step in
SKILL.md: does the quoted text exist at the cited file and line. It never
judges whether the quote means what the finding says; a human or a later
pass spends judgement only on findings whose quote is real.

## Reading findings out of a report

A reviewer's report ends each finding with one summary line:

  FINDING: SEVERITY | confidence | path/to/file.py:123 | one-line description
  FINDING: SEVERITY | confidence | path/to/file.py:123-130 | one-line description

This is the most structured, machine-readable marker a report carries, so
findings are keyed on it when it is present. A line that starts with
`FINDING:` but does not match this shape in full (a missing field, a
missing file:line) is still one record in the output, with verdict
`unparseable`, rather than being dropped.

A report can also carry no `FINDING:` line at all. SKILL.md's own Output
Format section asks a reviewer for severity, confidence, and a nearby quote,
but names no literal marker text, so real reports write it several ways: a
bold `**SEVERITY, confidence.**` marker on its own, or a numbered one with a
title folded in, `**1. SEVERITY, confidence: title.**` or
`**1. SEVERITY, confidence. title.**`. When the report has no `FINDING:`
line, this script reads any of these shapes instead, using the marker for
severity and confidence and the nearest backtick-wrapped `file:line`
citation after it for file and line. A plain label, `Severity: WARNING.
Confidence: high.` on one line or as two list items, takes the nearest citation
before it instead, and a bare `File: path:line` field counts as a citation.

Reviewers invent new layouts faster than this script can learn them, such as
`### 1. WARNING (confidence: high): title` or a severity folded into a
sentence. So a report with none of the shapes above falls back to checking
every quote that carries a citation, whatever the layout around it. Each such
quote becomes one record with no severity, confidence or description, since
no finding marker names them. When a quote carries several citations, the
record keeps the one whose file holds the quote. A quote with no citation is
skipped. A report with no finding and no cited quote yields an empty list.

## Finding the quote for each finding

The finding's summary line names a file and a line, but usually carries no
quote of its own. The quote lives elsewhere in the report, in a fenced code
block or an inline `Quote:` span:

  At `src/app.py:42`, the helper trims twice:

  ```py
      return value.strip().strip()
  ```

  Quote: `return value.strip().strip()`

  - `src/app.py:42`: `return value.strip().strip()`

The last shape, a citation immediately followed by its own quote, needs no
window search at all: the citation is part of the match, so this pairing
can never be stolen or ambiguous. The quote may also sit alone on the line
after its citation, with at most one blank line between, and may be wrapped
in double backticks when it holds a backtick itself.

A citation written as a Markdown link whose target carries the line, such as
`[plan.md](/repo/docs/plan.md:12)` or `[app.py](src/app.py#L12-L14)`, is read
as the backtick citation of its target before anything else is parsed.

A fenced block can open and close at any indentation, since reviewers
routinely nest one inside a list item. For every fence or inline quote, this
script first reads the citations (`` `path:line` `` or `` `path:line1-line2` ``)
in the 800 characters before it, stopping at the neighboring fence or inline
quote so two quotes never share a citation. When that leading text names
none, it reads the same span of text after the quote instead, since a
reviewer sometimes writes the citation as a trailing `(file.ts:399)` line.
A citation immediately followed by ", reached from `other:line`" names the
finding's own location first; the "reached from" half is a cross-reference
for the reader, not a second citation, so it is never treated as a label.

A citation read from the trailing side is weaker evidence than one read
from the leading side: the text after one quote is also the text before
the next one, so a trailing citation can belong to the following finding
instead of this quote. A quote paired through a trailing-only citation can
still come out `present` or `present-elsewhere`, since a real match is a
real match, but a miss on that quote never becomes `absent` or
`file-missing`; it becomes `unparseable`, since the miss may only mean the
citation belonged to a neighbor and never named this quote at all.

A quote pairs with a finding only when the finding's file and line overlap
exactly one candidate quote's citations, and that quote's citations overlap
no other finding. When a quote could belong to more than one finding, or a
finding's citation matches more than one quote, this script does not guess:
neither pairing is made, so a finding never steals a quote that belongs to
its neighbor. A finding whose own summary line embeds a `Quote:` span
(reviewers sometimes fold a short quote straight into the FINDING line)
pairs with that quote directly, since there is no ambiguity to resolve.

A finding that no quote can be paired with - because the report never
quotes it, or because the pairing above is ambiguous - gets verdict
`unparseable`: a missing quote is treated the same as a missing file or
line, per the rule above.

## Checking a quote against the file

The cited path resolves against `--root` (the reviewed tree). A leading
`a/`, `b/`, or `./` (git-diff conventions) is stripped before resolving. An
absolute path, including one with a drive letter, is resolved as written,
never stripped and rejoined under `--root`: it is accepted only when it
already lies inside `--root`, and `unparseable` otherwise. A relative path
is tried directly under `--root` first, and used when that exact file
exists. When it does not, the script looks for a file under `--root` whose
own path ends with the cited path on a path-component boundary (`src/app.py`
matches `pkg/src/app.py` but not `other_src/app.py`), comparing components
case-insensitively on every platform; a bare file name is the one-component
case of this same search. Exactly one match resolves to it; more than one is
`unparseable`, since the script cannot guess which the reviewer meant; only
zero matches is `file-missing`. This fallback search runs against a file
index built once per `--root` and reused for every finding. The index
prunes `.git`, `node_modules`, and any directory symlink or junction.

Otherwise the script reads a window around the cited line: `--window`
(default 3) lines of margin on each side, extended so the window is never
narrower than the quote itself - a multi-line quote cited by its first line
still fits. A cited range such as `12-18` extends the window from both
ends. The quote is read from that window first, because a wrong line
citation is exactly what this check exists to catch; only when the window
does not contain the quote does the script fall back to a full-file search,
so a citation that is merely off by a few lines is not confused with a
fabricated one.

The comparison collapses all whitespace, including newlines, to single
spaces on both sides before testing substring containment. This is what
makes a re-indented quote and a quote that spans a line break still count
as present. An empty or whitespace-only quote is `unparseable`, not a match
against everything.

## Output

One JSON record per finding on stdout, in report order:

  {"id": 1, "severity": "WARNING", "confidence": "medium",
   "file": "src/app.py", "line": "42", "description": "...",
   "quote": "    return value.strip()",
   "verdict": "present", "detail": "found within lines 39-45"}

`verdict` is one of `present`, `present-elsewhere`, `absent`, `file-missing`,
`unparseable`. `present-elsewhere` means the quote is real but sits outside
the cited window; it names the line where it was found and is not a
rejection. `--format text` prints one line per finding instead, for a quick
read.

## Exit codes

  0  every finding is present or present-elsewhere, and at least one exists
  1  at least one finding is absent, file-missing, or unparseable
  2  a usage or input error (bad --root, unreadable --report)
  3  the report has no finding and no cited quote to check
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys

DEFAULT_WINDOW = 3
MAX_CITATION_WINDOW = 800

WHITESPACE = re.compile(r"\s+")
FENCE = re.compile(r"[ \t]*```[\w-]*\n(.*?)\n[ \t]*```", re.S)
INLINE_QUOTE = re.compile(r"Quote:\s*(?:\([^)\n]*\)\s*:?\s*)?`([^`\n]+)`")
CITATION = re.compile(
    r"`([^`\n]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`(?:,\s*reached from\s*`[^`\n]+`)?"
)
# A citation immediately followed by its own quote, as in a "Quotes:" bullet list:
# `path/to/file.py:12`: `the quoted line`. The citation is embedded in the match, so
# this pairing needs no window search and cannot be stolen or ambiguous.
CITED_QUOTE = re.compile(
    r"`([^`\n]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`(?:,\s*reached from\s*`[^`\n]+`)?"
    r":\s*`([^`\n]+)`"
)
# The same pairing with the quote alone on the line after its citation, at most one
# blank line below it. Double backticks carry a quote that holds a backtick.
CITED_QUOTE_BELOW = re.compile(
    r"`([^`\n]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`[ \t]*:?[ \t]*\n(?:[ \t]*\n)?[ \t]*"
    r"(?:`` ?([^\n]+?) ?``|`([^`\n]+)`)[ \t]*$",
    re.M,
)
# A citation written as a Markdown link whose target carries the line:
# [plan.md](/repo/plan.md:12) or [app.py](src/app.py#L12-L14). check_report rewrites
# each one to the backtick form the patterns above read.
LINK_CITATION = re.compile(
    r"\[[^\]\n]*\]\(<?([^()\s<>]+\.[A-Za-z0-9]+)"
    r"(?::(\d+)(?:-(\d+))?|#L(\d+)(?:-L?(\d+))?)>?\)"
)
FINDING_LINE = re.compile(r"^FINDING:.*$", re.M)
FINDING_STRICT = re.compile(
    r"^FINDING:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?):(\d+)(?:-(\d+))?\s*\|\s*(.*)$"
)
SEVERITY_CONF = re.compile(
    r"\*\*(?:\d+\.\s*)?(CRITICAL|WARNING|INFO),?\s+(high|medium|low)\s+confidence"
    r"[^*]*\*\*"
)
# A plain-label marker, "Severity: WARNING. Confidence: high." on one line or as
# "- Severity: WARNING" and "- Confidence: high" on two. Its citation comes before it.
LABELED_MARKER = re.compile(
    r"Severity:\s*\**\s*(CRITICAL|WARNING|INFO)\b\**[^\n]*?(?:\n[ \t]*[-*]?[ \t]*)?"
    r"Confidence:\s*\**\s*(high|medium|low)\b",
    re.I,
)
# A "File: path:line" field with no backticks, rewritten to the backtick citation.
FILE_LABEL = re.compile(r"(File:[ \t]*)([^\s`]+\.[A-Za-z0-9]+:\d+(?:-\d+)?)(?=[\s.,;)]|$)", re.M)

REJECTED_VERDICTS = ("absent", "file-missing", "unparseable")


def normalize(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


def paths_match(a: str, b: str) -> bool:
    """True when the two paths are the same, or one is the other's path-component
    suffix, so a short prose form (`app.py`) matches the full repository-relative
    form (`src/app.py`)."""
    a = a.replace("\\", "/").strip("/")
    b = b.replace("\\", "/").strip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) <= min(a[1], b[1])


Span = tuple[int, int, str, "tuple[str, int, int] | None"]


def find_spans(text: str, exclude: list[tuple[int, int]]) -> list[Span]:
    """Every fenced code block and inline quote span in the report, as (start, end,
    quoted text, embedded citation or None), sorted by position. A `` `file:line`:
    `quote` `` bullet carries its citation with it, needing no window search. A span
    that falls inside one of `exclude` (a finding's own summary line, already
    paired with its own inline quote) is left out, so it is never offered to a
    different finding."""
    spans: list[Span] = []
    for match in CITED_QUOTE.finditer(text):
        path, line1, line2, quote = match.groups()
        cite = (path, int(line1), int(line2) if line2 else int(line1))
        spans.append((match.start(), match.end(), quote, cite))
    for match in CITED_QUOTE_BELOW.finditer(text):
        if any(s <= match.start() < e for s, e, _, _c in spans):
            continue
        path, line1, line2, double, single = match.groups()
        cite = (path, int(line1), int(line2) if line2 else int(line1))
        spans.append((match.start(), match.end(), double or single, cite))
    for match in FENCE.finditer(text):
        if any(s <= match.start() < e for s, e, _, _c in spans):
            continue
        spans.append((match.start(), match.end(), match.group(1), None))
    for match in INLINE_QUOTE.finditer(text):
        if any(s <= match.start() < e for s, e, _, _c in spans):
            continue
        spans.append((match.start(), match.end(), match.group(1), None))
    spans = [
        s for s in spans if not any(a <= s[0] < b for a, b in exclude)
    ]
    spans.sort(key=lambda s: s[0])
    return spans


def quote_candidates(
    text: str, exclude: list[tuple[int, int]]
) -> list[tuple[list[tuple[str, int, int]], str, bool]]:
    """One entry per fence or inline quote, holding the citations found within
    MAX_CITATION_WINDOW characters on either side of it, stopping at the
    neighboring quote, the quoted text itself, and whether those citations came
    only from the trailing side. A reviewer normally cites a quote once, either
    right before it or right after it. So the trailing side is read only when the
    leading side named nothing: reading both unconditionally would let this
    quote's trailing text double as the next quote's own leading citation, making
    both ambiguous. A trailing-only citation is weaker evidence, since that same
    text sits just before the next quote too; callers use the flag to keep a miss
    from reading as a rejection."""
    spans = find_spans(text, exclude)
    candidates = []
    for i, (start, end, quote, embedded) in enumerate(spans):
        if embedded is not None:
            candidates.append(([embedded], quote, False))
            continue
        prev_end = spans[i - 1][1] if i > 0 else 0
        next_start = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        leading = text[max(prev_end, start - MAX_CITATION_WINDOW):start]
        cites = [
            (c.group(1), int(c.group(2)), int(c.group(3)) if c.group(3) else int(c.group(2)))
            for c in CITATION.finditer(leading)
        ]
        trailing_only = False
        if not cites:
            trailing = text[end:min(next_start, end + MAX_CITATION_WINDOW)]
            cites = [
                (c.group(1), int(c.group(2)), int(c.group(3)) if c.group(3) else int(c.group(2)))
                for c in CITATION.finditer(trailing)
            ]
            trailing_only = True
        candidates.append((cites, quote, trailing_only))
    return candidates


def parse_findings_prose(text: str) -> list[dict]:
    """Findings written as a bold `**SEVERITY, confidence**` marker per finding,
    optionally numbered or with a title folded in, and with no `FINDING:` line.
    The nearest backtick-wrapped `file:line` citation after the marker supplies
    the file and line. A plain `Severity: X. Confidence: y.` label takes the
    nearest citation before it instead, back to the previous marker."""
    markers = sorted(
        [(m, False) for m in SEVERITY_CONF.finditer(text)]
        + [(m, True) for m in LABELED_MARKER.finditer(text)],
        key=lambda pair: pair[0].start(),
    )
    findings = []
    for i, (marker, labeled) in enumerate(markers, start=1):
        block_end = markers[i][0].start() if i < len(markers) else len(text)
        block = text[marker.end():block_end]
        raw = text[marker.start():min(block_end, marker.end() + 200)]
        cite = None
        if labeled:
            before_start = markers[i - 2][0].end() if i > 1 else 0
            before = list(CITATION.finditer(text[before_start:marker.start()]))
            cite = before[-1] if before else None
        cite = cite or CITATION.search(block)
        if not cite:
            findings.append({
                "id": i, "raw": raw, "parse_error":
                    "no file:line citation follows this finding marker",
            })
            continue
        path = cite.group(1)
        line1 = int(cite.group(2))
        line2 = int(cite.group(3)) if cite.group(3) else line1
        description = block.strip().split("\n", 1)[0].strip(" *.")
        findings.append({
            "id": i, "raw": raw, "parse_error": None,
            "severity": marker.group(1).upper(), "confidence": marker.group(2).lower(),
            "file": path, "line1": line1, "line2": line2,
            "description": description, "quote": None,
        })
    return findings


def parse_findings(text: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """Every finding in the report, and the text spans of any inline quote folded
    into a finding's own summary line (excluded from the shared quote pool since
    they already belong to that finding). Prefers the `FINDING:` line format; falls
    back to the bold severity/confidence marker format (see parse_findings_prose)
    when the report has no `FINDING:` line at all."""
    findings = []
    own_line_spans: list[tuple[int, int]] = []
    for i, line_match in enumerate(FINDING_LINE.finditer(text), start=1):
        raw = line_match.group(0)
        strict = FINDING_STRICT.match(raw)
        if not strict:
            findings.append({
                "id": i, "raw": raw, "parse_error":
                    "could not parse severity, confidence, file, line and description "
                    "from this FINDING line",
            })
            continue
        severity, confidence, path, line1, line2, description = strict.groups()
        finding = {
            "id": i, "raw": raw, "parse_error": None,
            "severity": severity.strip(), "confidence": confidence.strip(),
            "file": path.strip(), "line1": int(line1),
            "line2": int(line2) if line2 else int(line1),
            "description": description.strip(), "quote": None,
        }
        inline = INLINE_QUOTE.search(raw)
        if inline:
            finding["quote"] = inline.group(1)
            own_line_spans.append(
                (line_match.start() + inline.start(), line_match.start() + inline.end())
            )
        findings.append(finding)
    if not findings:
        findings = parse_findings_prose(text)
    return findings, own_line_spans


def attach_quotes(
    findings: list[dict],
    candidates: list[tuple[list[tuple[str, int, int]], str, bool]],
) -> None:
    """Pair each still-unpaired finding with the one quote whose citations overlap
    its file and line range, and only when that quote overlaps no other finding.
    Either side matching more than one candidate is left unpaired rather than
    guessed, so a finding never steals a quote meant for its neighbor. Records
    whether the winning pairing came from a trailing-only citation, so evaluate()
    can treat a miss on it as inconclusive rather than a rejection."""
    pending = [f for f in findings if not f["parse_error"] and f["quote"] is None]
    finding_hits: dict[int, list[int]] = {id(f): [] for f in pending}
    candidate_hits: list[list[int]] = [[] for _ in candidates]
    for f in pending:
        target = (f["line1"], f["line2"])
        for idx, (cites, _quote, _trailing) in enumerate(candidates):
            if any(paths_match(cp, f["file"]) and ranges_overlap((cl1, cl2), target)
                   for cp, cl1, cl2 in cites):
                finding_hits[id(f)].append(idx)
                candidate_hits[idx].append(id(f))
    for f in pending:
        hits = finding_hits[id(f)]
        if len(hits) != 1:
            continue
        idx = hits[0]
        if len(candidate_hits[idx]) != 1:
            continue
        f["quote"] = candidates[idx][1]
        f["quote_trailing"] = candidates[idx][2]


_FILE_INDEX_CACHE: dict[str, dict[tuple[str, ...], list[str]]] = {}
_PRUNED_DIR_NAMES = {".git", "node_modules"}


def _is_traversable_dir(path: str) -> bool:
    """False for a directory symlink or a junction, so the file index walk never
    follows one out of the tree it was asked to search."""
    if os.path.islink(path):
        return False
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        if isjunction(path):
            return False
    elif os.name == "nt":
        try:
            attrs = os.lstat(path).st_file_attributes
        except (AttributeError, OSError):
            attrs = 0
        if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True


def _build_file_index(root_abs: str) -> dict[tuple[str, ...], list[str]]:
    """Every file under root_abs, keyed by every casefolded path-component suffix
    of its path relative to root_abs, so a suffix search of any length is a dict
    lookup rather than a fresh walk. Prunes `.git`, `node_modules`, and any
    directory symlink or junction."""
    index: dict[tuple[str, ...], list[str]] = {}
    for dirpath, dirs, files in os.walk(root_abs):
        dirs[:] = [
            d for d in dirs
            if d not in _PRUNED_DIR_NAMES and _is_traversable_dir(os.path.join(dirpath, d))
        ]
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root_abs).replace("\\", "/")
            parts = tuple(part.casefold() for part in rel.split("/"))
            for start in range(len(parts)):
                index.setdefault(parts[start:], []).append(full)
    return index


def _file_index(root_abs: str) -> dict[tuple[str, ...], list[str]]:
    """The cached suffix index for root_abs, built on the first miss and reused
    for every later unresolved path in the same run."""
    cached = _FILE_INDEX_CACHE.get(root_abs)
    if cached is None:
        cached = _build_file_index(root_abs)
        _FILE_INDEX_CACHE[root_abs] = cached
    return cached


def resolve_path(root: str, raw_path: str) -> tuple[str | None, str | None]:
    """Resolve a finding's cited path against root. Strips git-diff `a/`, `b/` and
    `./` prefixes. An absolute path (a leading `/` or a drive letter) is resolved
    as written and accepted only when it already lies inside root; it is never
    stripped and rejoined under root. A relative path is tried directly under
    root first; when no file exists there, the search falls back to the cached
    file index for a file under root whose own path ends with the cited path on
    a path-component boundary, compared with casefold() so the match is
    case-insensitive on every platform; a bare file name is the one-component
    case of that same search. Returns (absolute path, None) on success or
    (None, reason) on failure."""
    root_abs = os.path.realpath(root)
    p = raw_path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    if not p:
        return None, f"{raw_path} names no file"

    is_absolute = bool(re.match(r"^[A-Za-z]:/", p)) or p.startswith("/")
    if is_absolute:
        candidate = os.path.realpath(p)
        try:
            common = os.path.commonpath([root_abs, candidate])
        except ValueError:
            return None, f"{raw_path} resolves outside {root}"
        if common != root_abs:
            return None, f"{raw_path} resolves outside {root}"
        return candidate, None

    direct = os.path.realpath(os.path.join(root_abs, p))
    try:
        common = os.path.commonpath([root_abs, direct])
    except ValueError:
        return None, f"{raw_path} resolves outside {root}"
    if common != root_abs:
        return None, f"{raw_path} resolves outside {root}"
    if os.path.isfile(direct):
        return direct, None

    p_parts_cmp = tuple(part.casefold() for part in p.split("/"))
    matches = _file_index(root_abs).get(p_parts_cmp, [])
    if len(matches) > 1:
        return None, f"more than one file under {root} matches {raw_path}"
    if len(matches) == 1:
        match = os.path.realpath(matches[0])
        try:
            common = os.path.commonpath([root_abs, match])
        except ValueError:
            return None, f"{raw_path} resolves outside {root}"
        if common != root_abs:
            return None, f"{raw_path} resolves outside {root}"
        return match, None
    # Zero matches: resolve to the direct path anyway. It does not exist, so the
    # isfile check in evaluate() reports file-missing rather than unparseable.
    return direct, None


def find_quote_line(lines: list[str], quote: str) -> int | None:
    """The first 1-based line where a window sized to the quote's own line count
    contains the quote after whitespace normalization, or None if it is nowhere in
    the file. Used only after the cited window misses, to tell a real quote at the
    wrong line from one that is not in the file at all."""
    norm_quote = normalize(quote)
    span = quote.count("\n") + 1 + 4
    n = len(lines)
    for start in range(1, n + 1):
        end = min(n, start + span - 1)
        snippet = "\n".join(lines[start - 1:end])
        if norm_quote in normalize(snippet):
            return start
    return None


def evaluate(root: str, finding: dict, window: int) -> tuple[str, str]:
    if finding["parse_error"]:
        return "unparseable", finding["parse_error"]
    if finding["quote"] is None:
        return "unparseable", "no quoted text in the report is tied to this citation"
    if not normalize(finding["quote"]):
        return "unparseable", "the quoted text is empty"

    verdict, detail = _locate_quote(root, finding, window)
    if verdict in ("absent", "file-missing") and finding.get("quote_trailing"):
        return (
            "unparseable",
            f"{detail} - but this quote's only citation trailed it, which may "
            "belong to the next finding instead",
        )
    return verdict, detail


def _locate_quote(root: str, finding: dict, window: int) -> tuple[str, str]:
    abs_path, err = resolve_path(root, finding["file"])
    if err:
        return "unparseable", err
    if not os.path.isfile(abs_path):
        return "file-missing", f"no file at {finding['file']} under {root}"

    with open(abs_path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    quote_line_count = finding["quote"].count("\n") + 1
    lo = max(1, finding["line1"] - window)
    hi = min(len(lines), finding["line2"] + window + max(0, quote_line_count - 1))
    if lo <= len(lines):
        snippet = "\n".join(lines[lo - 1:hi])
        if normalize(finding["quote"]) in normalize(snippet):
            return "present", f"found within lines {lo}-{hi}"

    found_at = find_quote_line(lines, finding["quote"])
    if found_at is not None:
        return (
            "present-elsewhere",
            f"found at line {found_at}, outside the cited window {lo}-{hi}",
        )
    return "absent", f"not found anywhere in {finding['file']}"


def line_label(finding: dict) -> str | None:
    if finding["parse_error"]:
        return None
    if finding["line2"] != finding["line1"]:
        return f"{finding['line1']}-{finding['line2']}"
    return str(finding["line1"])


def unlink_citations(text: str) -> str:
    """Rewrite every Markdown-link citation to the backtick `path:line` form."""
    def backtick(match: re.Match) -> str:
        path = match.group(1)
        first = match.group(2) or match.group(4)
        last = match.group(3) or match.group(5)
        return f"`{path}:{first}-{last}`" if last else f"`{path}:{first}`"
    return LINK_CITATION.sub(backtick, text)


VERDICT_RANK = ("present", "present-elsewhere", "unparseable", "absent", "file-missing")


def evaluate_cited_quotes(
    root: str,
    candidates: list[tuple[list[tuple[str, int, int]], str, bool]],
    window: int,
) -> list[tuple[dict, str, str]]:
    """The layout-free fallback: one finding per quote that carries a citation,
    evaluated against each of its citations, keeping the best verdict."""
    results = []
    for cites, quote, trailing in candidates:
        best = None
        for path, line1, line2 in cites:
            finding = {
                "parse_error": None, "severity": None, "confidence": None,
                "file": path, "line1": line1, "line2": line2,
                "description": None, "quote": quote, "quote_trailing": trailing,
            }
            verdict, detail = evaluate(root, finding, window)
            if best is None or VERDICT_RANK.index(verdict) < VERDICT_RANK.index(best[1]):
                best = (finding, verdict, detail)
        if best:
            best[0]["id"] = len(results) + 1
            results.append(best)
    return results


def check_report(text: str, root: str, window: int) -> list[dict]:
    text = FILE_LABEL.sub(lambda m: f"{m.group(1)}`{m.group(2)}`", unlink_citations(text))
    findings, own_line_spans = parse_findings(text)
    candidates = quote_candidates(text, own_line_spans)
    if findings:
        attach_quotes(findings, candidates)
        evaluated = [(f, *evaluate(root, f, window)) for f in findings]
    else:
        evaluated = evaluate_cited_quotes(root, candidates, window)
    records = []
    for finding, verdict, detail in evaluated:
        records.append({
            "id": finding["id"],
            "severity": finding.get("severity"),
            "confidence": finding.get("confidence"),
            "file": finding.get("file"),
            "line": line_label(finding),
            "description": finding.get("description"),
            "quote": finding.get("quote"),
            "verdict": verdict,
            "detail": detail,
        })
    return records


def render_text(records: list[dict]) -> str:
    lines = []
    for r in records:
        where = f"{r['file']}:{r['line']}" if r["file"] else "(unparseable)"
        lines.append(f"[{r['verdict']}] #{r['id']} {where} - {r['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Exit codes: 0 every finding present or present-elsewhere, 1 some "
               "finding absent/file-missing/unparseable, 2 usage or input error, "
               "3 the report has no findings to check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", required=True, help="the reviewed tree cited paths resolve against")
    parser.add_argument("--report", help="path to the review report text; reads stdin when omitted")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                         help=f"lines of margin read on each side of a cited line or range "
                              f"(default {DEFAULT_WINDOW})")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"quote_check: --root {args.root} is not a directory", file=sys.stderr)
        return 2
    try:
        if args.report:
            with open(args.report, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            # Read the raw bytes so encoding is always UTF-8 regardless of the
            # platform codepage, then normalize line endings by hand since a binary
            # read skips the universal-newline translation a text-mode read would do.
            text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
            text = text.replace("\r\n", "\n").replace("\r", "\n")
    except OSError as exc:
        print(f"quote_check: cannot read report: {exc}", file=sys.stderr)
        return 2

    records = check_report(text, args.root, args.window)
    if args.format == "json":
        print(json.dumps(records, indent=2))
    else:
        print(render_text(records))

    if not records:
        return 3
    if any(r["verdict"] in REJECTED_VERDICTS for r in records):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
