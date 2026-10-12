"""Check compatibility commands preserve Arc's cost contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).parent
SESSION = "12345678-1234-1234-1234-123456789abc"

class AdapterContractTest(unittest.TestCase):
    def test_commands_preserve_nulls_coverage_windows_and_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / "arc fixture.py"
            report = {"schemaVersion": 2, "parent": {"costUsd": None, "pricedCostUsd": 1.25,
                "coverage": "recorded-gaps", "requestCountExact": False}, "children": [], "combined": {"costUsd": None}}
            stub.write_text("import json, sys" + chr(10) + "assert sys.argv[1:] == " + repr([
                "cost", SESSION, "--since", "2026-10-02T00:00:00Z", "--until", "2026-10-03T00:00:00Z",
                "--by-tool-kind", "--until-text", "finding", "--window-agent", "child", "--json"]) + chr(10)
                + "print(" + repr(json.dumps(report)) + ")", encoding="utf-8")
            env = {**os.environ, "ARC_STATUS": '"' + sys.executable + '" "' + str(stub) + '"'}
            for name in ["summarize_usage.py", "transcript_usage.py"]:
                for prefix in ["claude", "claude-code", "codex"]:
                    result = subprocess.run([sys.executable, str(SCRIPTS / name), prefix + ":" + SESSION,
                        "--since", "2026-10-02T00:00:00Z", "--until", "2026-10-03T00:00:00Z",
                        "--by-tool-kind", "--until-text", "finding", "--window-agent", "child"],
                        capture_output=True, text=True, encoding="utf-8", env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), report)

    def test_unrecognized_prefix_and_windows_path_are_unchanged(self):
        from arc_cost import normalize_target
        for value in ["other:" + SESSION, "codex:not-a-uuid", "C:/sessions/session.jsonl"]:
            self.assertEqual(normalize_target(value), value)

    def test_absent_arc_never_falls_back_to_local_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ["summarize_usage.py", "transcript_usage.py"]:
                result = subprocess.run([sys.executable, str(SCRIPTS / name), SESSION], capture_output=True,
                    text=True, env={**os.environ, "ARC_STATUS": "", "ARC_REPO": directory})
                self.assertEqual(result.returncode, 2)
                self.assertIn("Arc cost command is unavailable", result.stderr)

if __name__ == "__main__":
    unittest.main()
