import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock
import triage


class TriageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.save("round.json", {"candidate": "a" * 40})
        (self.root / "one.md").write_text("APPROVE\n")
        self.save("key.json", {"A": {"report": "one.md"}})
        self.run_op("initialize")

    def save(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def run_op(self, operation, **kw):
        return triage.operate(self.root, operation,
                              expected_key=triage.file_digest(self.root / "key.json"),
                              expected_target=triage.file_digest(self.root / "triage.json"), **kw)

    def test_decorated_calls_and_oversize_preserve_target(self):
        before = (self.root / "triage.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "primitive"):
            self.run_op("calls", data={"A": {"value": "APPROVE", "PSObject": {"Properties": []}}})
        self.assertEqual((self.root / "triage.json").read_bytes(), before)
        stream = io.BytesIO(b" " * (triage.LIMIT + 10))
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            triage.bounded(stream)
        self.assertEqual(stream.tell(), triage.LIMIT + 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        args = [sys.executable, str(Path(triage.__file__)), "calls", str(self.root),
                "--expected-key", triage.file_digest(self.root / "key.json"),
                "--expected-target", triage.file_digest(self.root / "triage.json")]
        result = subprocess.run(args, input=b" " * (triage.LIMIT + 1), capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"1 MiB", result.stderr)
        self.assertEqual((self.root / "triage.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_partial_calls_final_validation_and_finding_updates(self):
        with self.assertRaises(ValueError):
            triage.operate(self.root, "validate")
        self.run_op("calls", data={"A": "APPROVE"})
        finding = {"id": "one", "found_by": ["A"], "reported": {"A": "WARNING"},
                   "file": "code.py", "verdict": "accepted", "severity": "high", "fix_commit": None,
                   "extra": {"preserved": True}}
        self.run_op("findings", data=[finding])
        triage.operate(self.root, "validate")
        finding["fix_commit"] = "b" * 40
        self.assertEqual(self.run_op("findings", data=[finding])["findings"], [finding])

    def test_measurements_do_not_change_binding_and_reports_do(self):
        initial = triage.identity(self.root, triage.read_json(self.root / "key.json"))
        self.save("key.json", {"A": {"report": "one.md", "session_id": "later", "cost_usd": 12}})
        self.assertEqual(initial, triage.identity(self.root, triage.read_json(self.root / "key.json")))
        self.run_op("calls", data={"A": "APPROVE"})
        (self.root / "one.md").write_text("COMMENT\n")
        with self.assertRaisesRegex(ValueError, "identity binding mismatch"):
            triage.operate(self.root, "validate")

    def test_rekey_preserves_attribution_new_calls_and_retry(self):
        self.run_op("calls", data={"A": "COMMENT"})
        finding = {"found_by": ["A"], "reported": {"A": "WARN"}, "file": "f", "verdict": "deferred", "severity": "low", "reason": "later"}
        self.run_op("findings", data=[finding])
        self.run_op("snapshot")
        pending = self.root / "rekey" / "previous-key.json"
        with self.assertRaisesRegex(ValueError, "pending"):
            self.run_op("snapshot")
        (self.root / "two.md").write_text("APPROVE\n")
        self.run_op("publish-key", data={"A": {"report": "two.md"}, "B": {"report": "one.md"}})
        with self.assertRaisesRegex(ValueError, "identity"):
            triage.operate(self.root, "validate")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.run_op("rekey", old_key=pending, expected_old="wrong")
        self.assertTrue(pending.exists())
        payload = self.run_op("rekey", old_key=pending, expected_old=triage.file_digest(pending))
        self.assertEqual(payload["calls"], {"A": None, "B": "COMMENT"})
        self.assertEqual(payload["findings"][0]["found_by"], ["B"])
        self.assertEqual(payload["findings"][0]["reported"], {"B": "WARN"})
        self.assertFalse(pending.exists())

    def test_legacy_requires_explicit_verified_adoption(self):
        self.save("triage.json", {"calls": {"A": "APPROVE"}, "findings": []})
        triage.operate(self.root, "validate")
        with self.assertRaisesRegex(ValueError, "adoption"):
            self.run_op("calls", data={"A": "COMMENT"})
        projection = triage.identity(self.root, triage.read_json(self.root / "key.json"))
        self.run_op("adopt", verified_identity=triage.binding(projection)["sha256"])
        self.run_op("calls", data={"A": "COMMENT"})

    def test_stale_digest_and_change_during_publication_preserve_foreign_files(self):
        target = self.root / "triage.json"
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, "changed"):
            triage.operate(self.root, "calls", data={"A": "APPROVE"}, expected_key="wrong", expected_target=triage.digest(before))
        sibling = self.root / ".triage.json.foreign.tmp"
        sibling.write_text("foreign")
        real_digest = triage.file_digest
        count = 0
        def concurrent(path):
            nonlocal count
            if Path(path) == target:
                count += 1
                if count == 2:
                    target.write_text(' {"foreign": true}')
            return real_digest(path)
        expected = real_digest(target)
        with mock.patch.object(triage, "file_digest", side_effect=concurrent):
            with self.assertRaisesRegex(ValueError, "changed"):
                triage.operate(self.root, "calls", data={"A": "APPROVE"}, expected_key=real_digest(self.root / "key.json"), expected_target=expected)
        self.assertEqual(json.loads(target.read_text()), {"foreign": True})
        self.assertEqual(sibling.read_text(), "foreign")
        self.assertEqual(list(self.root.glob("*.tmp")), [sibling])

    def test_duplicate_and_orphaned_identity_refuse_rekey(self):
        self.run_op("findings", data=[{"found_by": ["A"], "file": "f", "verdict": "accepted", "severity": "high"}])
        self.run_op("snapshot")
        pending = self.root / "rekey" / "previous-key.json"
        (self.root / "two.md").write_text("COMMENT")
        before = (self.root / "triage.json").read_bytes()
        for key, message in [({"A": {"report": "one.md"}, "B": {"report": "one.md"}}, "ambiguous"),
                             ({"A": {"report": "two.md"}}, "orphan")]:
            self.save("key.json", {"A": {"report": "one.md"}})
            self.run_op("publish-key", data=key)
            with self.assertRaisesRegex(ValueError, message):
                self.run_op("rekey", old_key=pending, expected_old=triage.file_digest(pending))
            self.assertEqual((self.root / "triage.json").read_bytes(), before)
            self.assertTrue(pending.exists())

    def test_publish_key_requires_matching_preserved_snapshot(self):
        before = (self.root / "key.json").read_bytes()
        proposed = {"B": {"report": "one.md"}}
        with self.assertRaises(OSError):
            self.run_op("publish-key", data=proposed)
        self.assertEqual((self.root / "key.json").read_bytes(), before)
        self.run_op("snapshot")
        pending = self.root / "rekey" / "previous-key.json"
        snapshot = triage.read_json(pending)
        snapshot["key_sha256"] = "f" * 64
        self.save("rekey/previous-key.json", snapshot)
        with self.assertRaisesRegex(ValueError, "snapshot does not match"):
            self.run_op("publish-key", data=proposed)
        self.assertEqual((self.root / "key.json").read_bytes(), before)

    def test_cli_validation_preserves_bounded_legacy_unicode_and_compact_findings(self):
        for findings in [
            [{"file": "f", "found_by": ["A"], "verdict": "rejected", "reason": "é" * 200000 + "\ud800" + "\U0001f600"}],
            [{"file": "f", "found_by": ["A"], "verdict": "rejected", "reason": "x"}] * 12000,
        ]:
            payload = {"calls": {"A": "APPROVE"}, "findings": findings}
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")
            self.assertLess(len(raw), triage.LIMIT)
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                triage.encode(payload)
            (self.root / "triage.json").write_bytes(raw)
            result = subprocess.run([sys.executable, str(Path(triage.__file__)), "validate", str(self.root)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), payload)
            self.assertEqual((self.root / "triage.json").read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
