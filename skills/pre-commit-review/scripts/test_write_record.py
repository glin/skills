#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("write_record.py")
sys.path.insert(0, str(SCRIPT.resolve().parents[2] / "references" / "scripts"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class WriteRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        helper_env = mock.patch.dict(os.environ, {"TRIAGE_SHARED_ROOT": str(SCRIPT.resolve().parents[2])})
        helper_env.start()
        self.addCleanup(helper_env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "code.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", "code.txt")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "code.txt").write_text("candidate\n", encoding="utf-8")
        git(self.repo, "add", "code.txt")
        git(self.repo, "commit", "-q", "-m", "candidate")
        self.candidate = git(self.repo, "rev-parse", "HEAD")
        self.run_dir = root / "run"
        self.round_dir = self.run_dir / "round-1"
        (self.round_dir / "reports").mkdir(parents=True)
        self.report = self.round_dir / "reports" / "A.md"
        self.report.write_text("No findings.\nAPPROVE\n", encoding="utf-8")
        (self.round_dir / "prompt.md").write_text("Review candidate.\n", encoding="utf-8")
        self.meta = {"date": "2026-09-27", "run_id": "120000-0123456789abcdef", "project": "demo",
                     "repository": str(self.repo), "base": self.base, "candidate": self.candidate,
                     "plan": None, "landed": None, "judge": {"model": "test-model", "blind": False},
                     "skill_version": self.base}
        save(self.run_dir / "run.json", self.meta)
        save(self.round_dir / "round.json", {"base": self.base, "candidate": self.candidate})
        save(self.round_dir / "key.json", {"A": {"report": "reports/A.md", "model": "test-model",
                                                  "effort": "high", "harness": "test", "session": "session-1"}})
        save(self.round_dir / "triage.json", {"calls": {"A": "APPROVE"}, "findings": []})
        self.out = root / f"2026-09-27-demo-{self.candidate[:8]}-120000-0123456789abcdef.json"

    def write_record(self, out: Path | None = None, *, default: bool = False) -> subprocess.CompletedProcess[str]:
        args = [sys.executable, str(SCRIPT), str(self.run_dir)]
        if not default:
            args.extend(["--out", str(out or self.out)])
        return subprocess.run(args, capture_output=True, text=True)

    def test_writes_record_from_immutable_candidate_and_saved_report(self) -> None:
        result = self.write_record(self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(record["candidate"], self.candidate)
        self.assertEqual(record["repository"], str(self.repo.resolve()))
        self.assertEqual(record["rounds"][0]["reviewers"][0]["call"], "APPROVE")
        self.assertEqual(record["rounds"][0]["reviewers"][0]["requests"], None)
        self.assertEqual(len(record["prompt_sha256"]), 64)
        self.assertEqual(self.write_record(self.out).returncode, 1)

    def test_control_size_and_identity_mismatch_prevent_recording(self) -> None:
        from triage import operate, file_digest
        target = self.round_dir / "triage.json"
        target.unlink()
        operate(self.round_dir, "initialize", expected_key=file_digest(self.round_dir / "key.json"), expected_target="absent")
        operate(self.round_dir, "calls", data={"A": "APPROVE"}, expected_key=file_digest(self.round_dir / "key.json"), expected_target=file_digest(target))
        self.report.write_text("Changed original report.\nAPPROVE\n")
        result = self.write_record()
        self.assertIn("identity binding mismatch", result.stderr)
        self.assertFalse(self.out.exists())
        target.write_bytes(b" " * (1024 * 1024 + 1))
        result = self.write_record()
        self.assertIn("1 MiB", result.stderr)
        self.assertFalse(self.out.exists())

    def test_default_location_and_collision_preserve_first_record(self) -> None:
        default = self.repo / ".claude" / "review-records" / self.out.name
        result = self.write_record(default=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), default)
        first = default.read_bytes()
        result = self.write_record(default=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("record already exists", result.stderr)
        self.assertEqual(default.read_bytes(), first)
        self.assertFalse(self.out.exists())

    def test_default_record_uses_portable_repository_reference(self) -> None:
        default = self.repo / ".claude" / "review-records" / self.out.name
        result = self.write_record(default=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = default.read_text(encoding="utf-8")
        record = json.loads(payload)
        self.assertNotIn(str(self.repo.resolve().parent), payload.replace("\\\\", "\\"))
        self.assertFalse(Path(record["repository"]).is_absolute())
        self.assertEqual((default.parent / record["repository"]).resolve(), self.repo.resolve())

    def test_explicit_output_overrides_default_directory(self) -> None:
        result = self.write_record(self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), self.out)
        self.assertFalse((self.repo / ".claude" / "review-records" / self.out.name).exists())

    def test_rejects_uncommitted_candidate(self) -> None:
        self.meta["candidate"] = "HEAD"
        save(self.run_dir / "run.json", self.meta)
        result = self.write_record(default=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidate must be a full commit SHA", result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.repo / ".claude" / "review-records").exists())

    def test_default_rejects_relative_repository(self) -> None:
        self.meta["repository"] = "repo"
        save(self.run_dir / "run.json", self.meta)
        result = self.write_record(default=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("repository must be an absolute path", result.stderr)
        self.assertFalse((self.repo / ".claude" / "review-records").exists())

    def test_rejects_report_changed_after_triage(self) -> None:
        self.report.write_text("No findings.\nREQUEST_CHANGES\n", encoding="utf-8")
        result = self.write_record()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("call differs", result.stderr)
        self.assertFalse(self.out.exists())

    def test_records_brief_dispute_and_fix_round(self) -> None:
        self.report.write_text("The brief conflicts with code.txt.\nREQUEST_CHANGES\n", encoding="utf-8")
        save(self.round_dir / "triage.json", {"calls": {"A": "REQUEST_CHANGES"}, "findings": [
            {"found_by": ["A"], "file": "code.txt", "line": 1, "quote_ok": True,
             "reported": {"A": "WARNING"}, "severity": "medium", "verdict": "accepted",
             "reason": "", "fix_commit": None, "disputes_brief": True}]})
        (self.repo / "code.txt").write_text("replacement\n", encoding="utf-8")
        git(self.repo, "add", "code.txt")
        git(self.repo, "commit", "-q", "-m", "replacement")
        replacement = git(self.repo, "rev-parse", "HEAD")
        triage = json.loads((self.round_dir / "triage.json").read_text(encoding="utf-8"))
        triage["findings"][0]["fix_commit"] = replacement
        save(self.round_dir / "triage.json", triage)
        self.meta["landed"] = replacement
        save(self.run_dir / "run.json", self.meta)
        followup = self.run_dir / "round-2"
        (followup / "reports").mkdir(parents=True)
        (followup / "reports" / "B.md").write_text("Fix confirmed.\nAPPROVE\n", encoding="utf-8")
        save(followup / "round.json", {"base": self.base, "candidate": replacement})
        save(followup / "key.json", {"B": {"report": "reports/B.md", "model": "test-model",
                                               "effort": "high", "harness": "test", "session": "session-2"}})
        save(followup / "triage.json", {"calls": {"B": "APPROVE"}, "findings": []})
        result = self.write_record()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(record["landed"], replacement)
        self.assertEqual(record["rounds"][0]["findings"][0]["fix_commit"], replacement)
        self.assertTrue(record["rounds"][0]["findings"][0]["disputes_brief"])
        self.assertEqual(record["rounds"][1]["candidate"], replacement)

    def test_copied_installation_uses_captured_source_version(self) -> None:
        copied = Path(self.temp.name) / "installed" / "write_record.py"
        copied.parent.mkdir()
        shutil.copyfile(SCRIPT, copied)
        result = subprocess.run([sys.executable, str(copied), str(self.run_dir), "--out", str(self.out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["skill_version"], self.base)

    def test_copied_installation_requires_source_version_without_history(self) -> None:
        copied = Path(self.temp.name) / "installed" / "write_record.py"
        copied.parent.mkdir()
        shutil.copyfile(SCRIPT, copied)
        self.meta.pop("skill_version")
        save(self.run_dir / "run.json", self.meta)
        result = subprocess.run([sys.executable, str(copied), str(self.run_dir), "--out", str(self.out)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skill_version needs", result.stderr)
        self.assertFalse(self.out.exists())

    def test_rejects_invalid_source_version(self) -> None:
        self.meta["skill_version"] = "HEAD"
        save(self.run_dir / "run.json", self.meta)
        result = self.write_record()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skill_version", result.stderr)
        self.assertFalse(self.out.exists())

    def test_discovers_version_in_source_checkout(self) -> None:
        self.meta.pop("skill_version")
        save(self.run_dir / "run.json", self.meta)
        result = self.write_record()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = git(SCRIPT.parent.parent, "log", "-1", "--format=%H", "--", "SKILL.md")
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["skill_version"], expected)

    @unittest.skipUnless(os.name == "nt", "Git Bash drive paths need Windows Python")
    def test_git_bash_paths_for_repository_run_and_output(self) -> None:
        def bash_path(path: Path) -> str:
            return "/" + path.drive[0].lower() + path.as_posix()[2:]

        self.meta["repository"] = bash_path(self.repo)
        save(self.run_dir / "run.json", self.meta)
        result = subprocess.run([sys.executable, str(SCRIPT), bash_path(self.run_dir), "--out", bash_path(self.out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["repository"], str(self.repo))

    @unittest.skipUnless(os.name == "nt", "Git Bash drive paths need Windows Python")
    def test_git_bash_paths_with_default_output(self) -> None:
        def bash_path(path: Path) -> str:
            return "/" + path.drive[0].lower() + path.as_posix()[2:]

        self.meta["repository"] = bash_path(self.repo)
        save(self.run_dir / "run.json", self.meta)
        result = subprocess.run([sys.executable, str(SCRIPT), bash_path(self.run_dir)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        default = self.repo / ".claude" / "review-records" / self.out.name
        self.assertEqual(Path(result.stdout.strip()), default)

    def test_rejects_schema_invalid_finding_fields(self) -> None:
        finding = {"found_by": ["A"], "file": "code.txt", "line": 1, "quote_ok": True,
                   "reported": {"A": "WARNING"}, "severity": None, "verdict": "rejected",
                   "reason": "not a bug", "fix_commit": None}
        cases = (("found_by", "A"), ("quote_ok", 1), ("severity", "urgent"))
        for field, value in cases:
            with self.subTest(field=field):
                changed = dict(finding, **{field: value})
                save(self.round_dir / "triage.json", {"calls": {"A": "APPROVE"}, "findings": [changed]})
                result = self.write_record()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.out.exists())
        key = json.loads((self.round_dir / "key.json").read_text(encoding="utf-8"))
        key["A"]["shell_denied"] = 1
        save(self.round_dir / "key.json", key)
        save(self.round_dir / "triage.json", {"calls": {"A": "APPROVE"}, "findings": []})
        result = self.write_record()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())

    def test_failed_publish_leaves_no_record_and_can_retry(self) -> None:
        publish = runpy.run_path(str(SCRIPT))["publish"]
        with mock.patch("os.link", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError):
                publish(self.out, "partial")
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.out.parent.glob(f".{self.out.name}.*.tmp")), [])
        result = self.write_record()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cleanup_failure_after_publication_reports_success(self) -> None:
        main = runpy.run_path(str(SCRIPT))["main"]
        arguments = [str(SCRIPT), str(self.run_dir), "--out", str(self.out)]
        with (mock.patch.object(sys, "argv", arguments),
              mock.patch.object(Path, "unlink", side_effect=OSError("cleanup denied")),
              mock.patch("builtins.print")):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["candidate"], self.candidate)


if __name__ == "__main__":
    unittest.main()
