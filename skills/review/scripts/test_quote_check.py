#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(__file__), "quote_check.py")
SPEC = importlib.util.spec_from_file_location("quote_check", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write(root: str, path: str, text: str) -> None:
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class QuoteCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = tempfile.mkdtemp(prefix="quote-check-test-")
        # Line numbers below matter: several tests cite an exact line.
        app_lines = [""] * 30
        app_lines[0] = "UNIQUE_MARKER_TEXT_FAR_AWAY"          # line 1
        app_lines[9] = "    return value.strip()"              # line 10
        app_lines[14] = "def combine(a, b):"                    # line 15
        app_lines[15] = "    return a + b"                      # line 16
        app_lines[19] = "def block(x):"                         # line 20
        app_lines[20] = "    y = x + 1"                         # line 21
        app_lines[21] = "    z = y * 2"                         # line 22
        app_lines[22] = "    return z"                           # line 23
        app_lines[23] = ""                                       # line 24
        write(cls.root, "src/app.py", "\n".join(app_lines) + "\n")
        write(cls.root, "nested/deep/util.py", "def helper():\n    return 1\n")
        write(cls.root, "lib/shared/util.py", "# lib copy\n")
        write(cls.root, "vendor/shared/util.py", "# vendor copy\n")
        write(cls.root, "docs/notes.md", "Run `make` first.\n")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def check(self, report: str, window: int = MODULE.DEFAULT_WINDOW) -> list[dict]:
        return MODULE.check_report(report, self.root, window)

    def test_present_quote_at_the_cited_line(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | src/app.py:10 | a plain match\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_reindented_quote_still_matches(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\nreturn value.strip()\n```\n\n"
            "FINDING: INFO | high | src/app.py:10 | re-indented in the report\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_quote_spanning_a_line_break_still_matches(self) -> None:
        report = (
            "At `src/app.py:15`:\n\n```py\ndef combine(a, b):\n    return a + b\n```\n\n"
            "FINDING: INFO | high | src/app.py:15 | spans a line break\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_absent_quote_is_rejected(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\nthis text is nowhere in the file\n```\n\n"
            "FINDING: WARNING | medium | src/app.py:10 | fabricated text\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "absent")

    def test_quote_present_elsewhere_reports_the_real_line(self) -> None:
        # The marker text is real, at line 1, but the finding cites line 23. The cited
        # window misses it, so this is a wrong citation, not a fabrication.
        report = (
            "At `src/app.py:23`:\n\n```py\nUNIQUE_MARKER_TEXT_FAR_AWAY\n```\n\n"
            "FINDING: WARNING | medium | src/app.py:23 | wrong line citation\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present-elsewhere")
        self.assertIn("line 1", records[0]["detail"])

    def test_indented_fence_still_closes(self) -> None:
        # A fence nested inside a list item is indented on both its opening and
        # closing lines. It must still pair and close correctly.
        report = (
            "- At `src/app.py:10`:\n\n  ```py\n      return value.strip()\n  ```\n\n"
            "FINDING: INFO | high | src/app.py:10 | quote fenced inside a list item\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_citation_after_the_fence_is_read(self) -> None:
        report = (
            "```py\n    return value.strip()\n```\nSee `src/app.py:10`.\n\n"
            "FINDING: INFO | high | src/app.py:10 | citation trails the fence\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_inline_quote_line_is_read(self) -> None:
        report = (
            "At `src/app.py:10`, the helper trims its input.\n\n"
            "Quote: `return value.strip()`\n\n"
            "FINDING: INFO | high | src/app.py:10 | inline quote, no fence\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_inline_quote_on_the_finding_line_itself_is_read(self) -> None:
        report = (
            "FINDING: INFO | high | src/app.py:10 | trims input. "
            "Quote: `return value.strip()`\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_cited_quote_bullet_carries_its_own_citation(self) -> None:
        # A "Quotes:" bullet list cites each quote inline: `file:line`: `quote`.
        # This needs no window search, and two such bullets side by side must not
        # go ambiguous with each other the way two window-searched quotes could.
        report = (
            "- Quotes:\n"
            "  - `src/app.py:10`: `return value.strip()`\n"
            "  - `src/app.py:16`: `return a + b`\n\n"
            "FINDING: INFO | high | src/app.py:10 | first bullet\n"
            "FINDING: INFO | high | src/app.py:16 | second bullet\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[1]["verdict"], "present")

    def test_unquoted_finding_does_not_steal_a_neighbors_fence(self) -> None:
        # Finding 1 cites line 16, with no quote of its own anywhere in the report.
        # Finding 2's real quote follows right after, cited at line 10, but the
        # fence's 800-character lookback window also reaches back far enough to see
        # finding 1's line-16 citation. The single fence is now ambiguous between
        # the two findings, and pairing it with either would be a guess. Neither
        # finding may claim it: finding 1 must not steal it and come out
        # `absent` (a real quote, wrongly said to be missing at line 16); finding 2
        # must not silently keep it either, since the ambiguity is real.
        report = (
            "Also see `src/app.py:16`, unrelated to the next point.\n\n"
            "Quote (`src/app.py:10`):\n```py\n    return value.strip()\n```\n\n"
            "FINDING: WARNING | low | src/app.py:16 | cites a real line, quotes nothing\n"
            "FINDING: INFO | high | src/app.py:10 | has a real quote nearby\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")
        self.assertIsNone(records[0]["quote"])
        self.assertEqual(records[1]["verdict"], "unparseable")
        self.assertIsNone(records[1]["quote"])

    def test_trailing_only_citation_never_rejects_the_next_finding(self) -> None:
        # Finding 1's quote has no leading citation of its own; the only nearby
        # citation is finding 2's, sitting between the two. The trailing-window
        # search used to let finding 2 steal finding 1's quote, and since that
        # quote's text is nowhere near finding 2's cited line, finding 2 came out
        # `absent`: a real finding rejected. A citation read only from the
        # trailing side must never turn a miss into `absent` or `file-missing`.
        report = (
            "Quote: `totally_unique_snippet_xyz`\n\n"
            "See `src/app.py:16` for the next point.\n\n"
            "FINDING: WARNING | medium | src/app.py:10 | quote has no leading citation\n"
            "FINDING: INFO | high | src/app.py:16 | must not steal the other quote\n"
        )
        records = self.check(report)
        self.assertNotIn(records[1]["verdict"], ("absent", "file-missing"))

    def test_multiline_quote_cited_by_its_first_line_is_present(self) -> None:
        quote = "def block(x):\n    y = x + 1\n    z = y * 2\n    return z"
        report = (
            f"At `src/app.py:20`:\n\n```py\n{quote}\n```\n\n"
            "FINDING: INFO | high | src/app.py:20 | multi-line quote, single-line citation\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_empty_quote_is_unparseable(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\n   \n```\n\n"
            "FINDING: INFO | high | src/app.py:10 | fenced block is blank\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")

    def test_skill_output_format_with_no_finding_line_parses(self) -> None:
        # SKILL.md's own Output Format section asks for a severity/confidence marker
        # and a citation, with no machine-readable FINDING: line at all.
        report = (
            "### `src/app.py`: a stripped return value\n\n"
            "**INFO, high confidence.** The helper trims its input.\n\n"
            "Quote (`src/app.py:10`):\n```py\n    return value.strip()\n```\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["severity"], "INFO")

    def test_numbered_bold_marker_with_title_parses(self) -> None:
        # Real reports title each finding inside the marker itself, e.g.
        # "**1. WARNING, high confidence: title.**". SKILL.md's Output Format
        # section asks for severity and confidence but names no literal marker
        # text, so the parser must recognize this shape too, not just the bare
        # "**SEVERITY, confidence.**" form.
        report = (
            "**1. WARNING, high confidence: a stripped return value.**\n\n"
            "The helper trims its input twice.\n\n"
            "Quote (`src/app.py:10`):\n```py\n    return value.strip()\n```\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["severity"], "WARNING")
        self.assertEqual(records[0]["confidence"], "high")

    def test_numbered_bold_marker_with_period_before_title_parses(self) -> None:
        # A second real title shape: a period, not a colon, separates
        # "confidence" from the title inside the same bold marker.
        report = (
            "**2. INFO, low confidence. a stripped return value.**\n\n"
            "The helper trims its input twice.\n\n"
            "Quote (`src/app.py:10`):\n```py\n    return value.strip()\n```\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["severity"], "INFO")
        self.assertEqual(records[0]["confidence"], "low")

    def test_labeled_severity_after_a_link_citation_parses(self) -> None:
        # A GPT-6.1 Sol report shape: the link citation opens the paragraph, a
        # "Severity: X. Confidence: y." label follows it, and the quote is a fence.
        report = (
            f"[src/app.py:16]({self.absolute('src/app.py')}:16) adds where the caller "
            "expects a product. Severity: WARNING. Confidence: high.\n\n"
            "```text\n    return a + b\n```\n\n"
            f"The same mistake recurs at [app.py:10]({self.absolute('src/app.py')}:10).\n\n"
            f"[src/app.py:10]({self.absolute('src/app.py')}:10) strips twice. "
            "Severity: INFO. Confidence: medium.\n\n"
            "```text\n    return value.strip()\n```\n\nVerdict: COMMENT.\n"
        )
        records = self.check(report)
        self.assertEqual([r["verdict"] for r in records], ["present", "present"])
        self.assertEqual([r["line"] for r in records], ["16", "10"])
        self.assertEqual(records[1]["confidence"], "medium")

    def test_labeled_fields_with_a_bare_file_line_parse(self) -> None:
        # The field-list shape: "- File: path:line" with no backticks, then
        # "- Severity:" and "- Confidence:" on their own lines, then the quote.
        report = (
            "### Finding 1: a sum where a product belongs\n"
            "- File: src/app.py:16\n- Severity: WARNING\n- Confidence: high\n"
            "- Quote:\n```\n    return a + b\n```\n"
            "- Problem: adds.\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["severity"], "WARNING")

    def absolute(self, path: str) -> str:
        return os.path.join(self.root, path).replace("\\", "/")

    def test_link_citation_followed_by_its_quote_is_read(self) -> None:
        # A Codex report shape: the citation is a Markdown link whose target holds
        # an absolute path and the line, and the quote follows on the same line.
        report = (
            "1. **WARNING, high confidence.** "
            f"[app.py]({self.absolute('src/app.py')}:16): `    return a + b` "
            "adds where the caller expects a product.\n\nVerdict: **REQUEST_CHANGES**.\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")

    def test_link_citation_with_its_quote_on_a_later_line_is_read(self) -> None:
        # A second Codex report shape: the link sits alone on its line, the quote
        # alone below it, and the prose after it cites other files as links too.
        report = (
            "1. **WARNING, high confidence. The sum is wrong.**\n\n"
            f"   [app.py:16]({self.absolute('src/app.py')}:16)\n\n"
            "   `return a + b`\n\n"
            f"   [util.py:2]({self.absolute('nested/deep/util.py')}:2) returns a constant.\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["line"], "16")

    def test_line_anchor_link_with_a_double_backtick_quote_is_read(self) -> None:
        report = (
            "**INFO, medium confidence. The note names a command.**\n\n"
            "[notes.md](docs/notes.md#L1)\n\n"
            "``Run `make` first.``\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "present")

    def test_link_citation_with_a_fabricated_quote_is_rejected(self) -> None:
        report = (
            "**WARNING, high confidence.** "
            f"[app.py]({self.absolute('src/app.py')}:16): `    return a * b`\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "absent")

    def test_non_ascii_quote_from_stdin(self) -> None:
        report = (
            "At `src/app.py:1`:\n\n```py\nUNIQUE_MARKER_TEXT_FAR_AWAY\n```\n\n"
            "FINDING: INFO | high | src/app.py:1 | café — naïve ASCII check\n"
        )
        result = subprocess.run(
            [sys.executable, SCRIPT, "--root", self.root],
            input=report, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)
        self.assertEqual(records[0]["verdict"], "present")

    def test_missing_file_is_rejected(self) -> None:
        report = (
            "At `src/nope.py:1`:\n\n```py\nanything\n```\n\n"
            "FINDING: WARNING | medium | src/nope.py:1 | file does not exist\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "file-missing")

    def test_path_with_a_prefix_resolves(self) -> None:
        report = (
            "At `a/src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | a/src/app.py:10 | git-diff a/ prefix\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_path_with_b_prefix_resolves(self) -> None:
        report = (
            "At `b/src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | b/src/app.py:10 | git-diff b/ prefix\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_path_with_dot_slash_prefix_resolves(self) -> None:
        report = (
            "At `./src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | ./src/app.py:10 | dot-slash prefix\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_absolute_path_inside_root_resolves(self) -> None:
        # A finding citing the reviewed file by its absolute path (as some
        # reviewers do) must still resolve to that file, not to the same
        # relative path rejoined under root from an arbitrary starting point.
        abs_path = os.path.join(self.root, "src", "app.py").replace("\\", "/")
        report = (
            f"At `{abs_path}:10`:\n\n```py\n    return value.strip()\n```\n\n"
            f"FINDING: INFO | high | {abs_path}:10 | absolute path inside root\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_absolute_path_outside_root_is_unparseable(self) -> None:
        report = (
            "At `/definitely/outside/root/app.py:10`:\n\n```py\nanything\n```\n\n"
            "FINDING: WARNING | medium | /definitely/outside/root/app.py:10 | escapes root\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")

    def test_bare_file_name_resolves_to_its_single_match(self) -> None:
        report = (
            "At `app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | app.py:10 | bare file name\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_partial_path_suffix_with_one_match_resolves(self) -> None:
        report = (
            "At `deep/util.py:1`:\n\n```py\ndef helper():\n```\n\n"
            "FINDING: INFO | high | deep/util.py:1 | suffix match, single hit\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_partial_path_suffix_with_two_matches_is_unparseable(self) -> None:
        report = (
            "At `shared/util.py:1`:\n\n```py\nanything\n```\n\n"
            "FINDING: WARNING | medium | shared/util.py:1 | suffix match, ambiguous\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")

    def test_partial_path_suffix_matches_case_insensitively(self) -> None:
        report = (
            "At `deep/Util.py:1`:\n\n```py\ndef helper():\n```\n\n"
            "FINDING: INFO | high | deep/Util.py:1 | suffix match, different case\n"
        )
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "present")

    def test_file_index_builds_once_and_skips_a_pruned_directory(self) -> None:
        # Two findings that both need the suffix-search fallback must share one
        # walk of the tree, not repeat it per finding. A same-named file planted
        # under a pruned directory (node_modules) must never join the index: if
        # it did, the single real match below would become an ambiguous two.
        root = tempfile.mkdtemp(prefix="quote-check-index-test-")
        try:
            write(root, "src/marker_one.py", "MARKER_ONE\n")
            write(root, "lib/marker_two.py", "MARKER_TWO\n")
            write(root, "node_modules/marker_one.py", "MARKER_ONE\n")
            report = (
                "At `marker_one.py:1`:\n\n```py\nMARKER_ONE\n```\n\n"
                "FINDING: INFO | high | marker_one.py:1 | first unresolved path\n\n"
                "At `marker_two.py:1`:\n\n```py\nMARKER_TWO\n```\n\n"
                "FINDING: INFO | high | marker_two.py:1 | second unresolved path\n"
            )
            walk_calls = []
            real_walk = os.walk

            def counting_walk(path, *args, **kwargs):
                walk_calls.append(path)
                return real_walk(path, *args, **kwargs)

            MODULE._FILE_INDEX_CACHE.clear()
            with mock.patch.object(MODULE.os, "walk", side_effect=counting_walk):
                records = MODULE.check_report(report, root, MODULE.DEFAULT_WINDOW)
            self.assertEqual(len(walk_calls), 1)
            self.assertEqual(records[0]["verdict"], "present")
            self.assertEqual(records[1]["verdict"], "present")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_finding_with_no_associated_quote_is_unparseable(self) -> None:
        # A FINDING line with a real file and line, but the report never quotes anything
        # at that citation, so there is no text to check.
        report = "FINDING: INFO | low | src/app.py:10 | no quote anywhere in this report\n"
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")
        self.assertIsNone(records[0]["quote"])

    def test_malformed_finding_line_is_unparseable(self) -> None:
        report = "FINDING: this line has no pipes or file colon line\n"
        records = self.check(report)
        self.assertEqual(records[0]["verdict"], "unparseable")
        self.assertIsNone(records[0]["file"])

    def test_line_range_citation_reads_from_both_ends(self) -> None:
        quote = "def block(x):\n    y = x + 1\n    z = y * 2\n    return z"
        report = (
            f"At `src/app.py:20-23`:\n\n```py\n{quote}\n```\n\n"
            "FINDING: INFO | high | src/app.py:20-23 | a multi-line block\n"
        )
        records = self.check(report, window=0)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[0]["line"], "20-23")

    def test_a_generic_report_in_the_real_format_parses(self) -> None:
        # Shaped like a real reviewer report: prose per-file findings with inline
        # citations and fenced quotes, then a FINDING summary line per finding. Content
        # is invented, not copied from any real review.
        report = (
            "**Verdict: COMMENT. Overall risk is low.**\n\n"
            "## Per-file findings\n\n"
            "### `src/app.py`: a stripped return value\n\n"
            "**INFO, high confidence.** The helper trims its input.\n\n"
            "Quote (`src/app.py:10`):\n```py\n    return value.strip()\n```\n\n"
            "### `src/app.py`: an unverifiable claim\n\n"
            "**WARNING, low confidence.** This finding cites text the report never quotes.\n\n"
            "## Coverage sweep\n\nNothing else to report.\n\n"
            "FINDING: INFO | high | src/app.py:10 | the helper trims its input\n"
            "FINDING: WARNING | low | src/app.py:16 | no quote backs this one\n"
        )
        records = self.check(report)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["verdict"], "present")
        self.assertEqual(records[1]["verdict"], "unparseable")

    def test_report_with_no_finding_lines_yields_no_records(self) -> None:
        self.assertEqual(self.check("No issues found. Verdict: APPROVE.\n"), [])

    def run_cli(self, report: str, *extra_args: str) -> subprocess.CompletedProcess:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(report)
            path = fh.name
        try:
            return subprocess.run(
                [sys.executable, SCRIPT, "--root", self.root, "--report", path, *extra_args],
                capture_output=True, text=True,
            )
        finally:
            os.remove(path)

    def test_cli_exits_zero_when_every_finding_is_present(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | src/app.py:10 | ok\n"
        )
        result = self.run_cli(report)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)
        self.assertEqual(records[0]["verdict"], "present")

    def test_cli_exits_one_when_a_finding_is_rejected(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\nnowhere\n```\n\n"
            "FINDING: WARNING | medium | src/app.py:10 | bad\n"
        )
        result = self.run_cli(report)
        self.assertEqual(result.returncode, 1)

    def test_cli_text_format_prints_one_line_per_finding(self) -> None:
        report = (
            "At `src/app.py:10`:\n\n```py\n    return value.strip()\n```\n\n"
            "FINDING: INFO | high | src/app.py:10 | ok\n"
        )
        result = self.run_cli(report, "--format", "text")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[present] #1 src/app.py:10", result.stdout)

    def test_cli_exits_two_when_root_is_not_a_directory(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("FINDING: INFO | high | src/app.py:10 | ok\n")
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, SCRIPT, "--root", os.path.join(self.root, "does-not-exist"),
                 "--report", path],
                capture_output=True, text=True,
            )
        finally:
            os.remove(path)
        self.assertEqual(result.returncode, 2)

    def test_cli_exits_three_when_the_report_has_no_findings(self) -> None:
        result = self.run_cli("No issues found. Verdict: APPROVE.\n")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout), [])

    def test_cli_exits_two_when_the_report_file_is_missing(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT, "--root", self.root, "--report",
             os.path.join(self.root, "no-such-report.txt")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
