"""Disposable Git repositories exercise publication, staging and recovery boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("commit_owned", Path(__file__).with_name("commit_owned.py"))
owned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owned)
MESSAGE = b"docs(plan): update owned plan\n\nAgent: fixture (effort high, test)\nAgent-Session: 11111111-2222-4333-8444-555555555555\n"


class CommitOwnedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="plan-owned-fixture-", dir=os.environ.get("PLAN_COMMIT_TEST_ROOT"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.com")
        self.git("config", "core.autocrlf", "false")
        self.write("plan.md", b"old\n")
        self.write("other.md", b"other\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD").strip()
        self.blob = self.git("rev-parse", "HEAD:plan.md").strip()
        self.write("plan.md", b"new\n")
        self.files = [["plan.md", owned.digest(b"new\n"), self.blob, "100644", "100644"]]

    def git(self, *args, check=True, data=None, index=None):
        result = owned.git(self.root, *args, check=check, data=data, index=index)
        return result.decode() if check else result

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def run_commit(self, callback=None):
        with mock.patch.object(owned, "checkpoint", callback or (lambda *args: None)):
            return owned.commit(self.root, "refs/heads/main", self.baseline, self.files, MESSAGE)

    def receipts(self):
        return list((self.root / ".git").glob("plan-commit-*/receipt.json"))

    def receipt(self):
        receipts = self.receipts()
        self.assertEqual(len(receipts), 1)
        return receipts[0], json.loads(receipts[0].read_bytes())

    def failed(self, event, mutate=None):
        def stop(current, state):
            if current == event:
                if mutate:
                    mutate(state)
                raise owned.Refusal("fixture interrupted")
        with self.assertRaises(owned.Refusal):
            self.run_commit(stop)
        receipt, state = self.receipt()
        owned.inventory(receipt.parent, state)
        return receipt, state

    def assert_clean(self):
        self.assertEqual(list((self.root / ".git").glob("plan-commit-*")), [])
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/plan-files/recovery"), "")
        self.assertFalse((self.root / ".git/index.lock").exists())

    def foreign_commit(self, state, *, missing=False):
        private = self.root / ".git/foreign.index"
        self.git("read-tree", "HEAD", index=private)
        blob = self.git("hash-object", "-w", "--stdin", data=b"foreign\n").strip()
        self.git("update-index", "--add", "--cacheinfo", "100644", blob, "foreign.md", index=private)
        if missing:
            self.git("update-index", "--force-remove", "plan.md", index=private)
        tree = self.git("write-tree", index=private).strip()
        parent = self.git("rev-parse", "HEAD").strip()
        commit = self.git("commit-tree", tree, "-p", parent, data=b"foreign\n").strip()
        self.git("update-ref", "refs/heads/main", commit, parent)
        private.unlink()
        return commit

    def test_success_disposes_only_owned_scratch_and_keeps_unstaged_work(self):
        self.write("other.md", b"unstaged\n")
        commit = self.run_commit()
        self.assertEqual(self.git("show", f"{commit}:plan.md"), "new\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual((self.root / "other.md").read_bytes(), b"unstaged\n")
        self.assert_clean()

    def test_current_locked_snapshot_preserves_staging_after_capture(self):
        def stage(event, state):
            if event == "captured":
                self.write("other.md", b"staged late\n")
                self.git("add", "other.md")
                self.write("other.md", b"unstaged later\n")
        self.run_commit(stage)
        self.assertEqual(self.git("show", ":other.md"), "staged late\n")
        self.assertEqual((self.root / "other.md").read_bytes(), b"unstaged later\n")
        self.assert_clean()

    def test_foreign_advance_before_publish_preserves_its_commit(self):
        foreign = []
        def advance(event, state):
            if event == "captured":
                foreign.append(self.foreign_commit(state))
        with self.assertRaises(owned.Refusal):
            self.run_commit(advance)
        receipt, state = self.receipt()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), foreign[0])
        self.assertEqual(self.git("show", "HEAD:foreign.md"), "foreign\n")
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.assertEqual(set(p.name for p in receipt.parent.iterdir()),
                         {"receipt.json", "message", "input-0", "candidate.index", "shared.index", "prepared.index"})
        owned.inventory(receipt.parent, state)
        owned.verify_candidate(receipt.parent, state)

    def test_foreign_advance_after_publish_preserves_all_content(self):
        def advance(event, state):
            if event == "published":
                self.foreign_commit(state)
        self.write("other.md", b"staged\n")
        self.git("add", "other.md")
        self.write("other.md", b"unstaged\n")
        self.run_commit(advance)
        self.assertEqual(self.git("show", "HEAD:foreign.md"), "foreign\n")
        self.assertEqual(self.git("show", "HEAD:plan.md"), "new\n")
        self.assertEqual(self.git("show", ":other.md"), "staged\n")
        self.assertEqual((self.root / "other.md").read_bytes(), b"unstaged\n")
        self.assert_clean()

    def test_real_full_index_commit_is_blocked_across_publication(self):
        attempts = []
        def compete(event, state):
            if event == "published":
                result = self.git("commit", "--allow-empty", "-qm", "foreign", check=False)
                attempts.append(result)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"index.lock", result.stderr)
        self.run_commit(compete)
        self.assertNotEqual(attempts[0].returncode, 0)
        self.assertIn(b"index.lock", attempts[0].stderr)
        self.git("commit", "--allow-empty", "-qm", "after release")
        self.assertEqual(self.git("show", "HEAD:plan.md"), "new\n")
        self.assert_clean()

    def test_windows_binary_lock_roundtrips_lf_bytes_and_parses_before_publish(self):
        # A filename supplies a deterministic LF byte in a valid Git index.
        if os.name == "nt":
            # Windows disallows LF filenames. A staged blob object ID supplies LF instead.
            for number in range(1000):
                self.write("other.md", f"{number}\n".encode())
                blob = self.git("hash-object", "other.md").strip()
                if b"\n" in bytes.fromhex(blob):
                    self.git("add", "other.md")
                    break
        else:
            self.write("line\nname", b"line\n")
            self.git("add", "line\nname")
        observed = []
        real_open = os.open
        flags_used = []
        def opening(path, flags, *args, **kwargs):
            if str(path).endswith("index.lock"):
                flags_used.append(flags)
            return real_open(path, flags, *args, **kwargs)
        def inspect(event, state):
            if event == "before_publish":
                lock = Path(state["index"] + ".lock").read_bytes()
                prepared = (Path(state["index"]).parent / state["recovery_ref"].split("/")[-1] / "prepared.index").read_bytes()
                self.assertIn(b"\n", lock)
                self.assertEqual(lock, prepared)
                self.assertEqual(owned.entries(self.root, state["index"] + ".lock")["plan.md"], state["outputs"]["plan.md"])
                observed.append(True)
        with mock.patch.object(owned.os, "open", opening):
            self.run_commit(inspect)
        self.assertEqual(observed, [True])
        if os.name == "nt":
            self.assertTrue(all(flags & os.O_BINARY for flags in flags_used))

    def test_prepublication_failure_retains_inputs_candidate_and_receipt(self):
        receipt, state = self.failed("before_publish")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        self.assertEqual((receipt.parent / "input-0").read_bytes(), b"new\n")
        self.assertEqual((receipt.parent / "message").read_bytes(), MESSAGE)
        self.assertFalse((self.root / ".git/index.lock").exists())
        owned.verify_candidate(receipt.parent, state)

    def test_publication_interruption_retains_lock_then_completes(self):
        receipt, state = self.failed("published")
        self.assertTrue((self.root / ".git/index.lock").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), state["candidate"])
        owned.recover(receipt)
        self.assertEqual(self.git("show", ":plan.md"), "new\n")
        self.assert_clean()

    def test_actual_replacement_refusal_retains_state_and_recovery_does_not_bypass_it(self):
        real = os.replace
        def deny(source, target):
            if str(target) == str(self.root / ".git/index"):
                raise PermissionError("fixture permission refusal")
            return real(source, target)
        with mock.patch.object(owned.os, "replace", deny):
            with self.assertRaises(owned.Refusal):
                self.run_commit()
            receipt, state = self.receipt()
            snapshot = (self.root / ".git/index").read_bytes()
            with self.assertRaises(PermissionError):
                owned.recover(receipt)
            self.assertEqual((self.root / ".git/index").read_bytes(), snapshot)
            self.assertTrue((self.root / ".git/index.lock").exists())
            self.assertEqual(self.git("rev-parse", "HEAD").strip(), state["candidate"])
        owned.recover(receipt)
        self.assert_clean()

    def test_rollback_only_moves_unchanged_owned_tip_and_preserves_recovery(self):
        receipt, state = self.failed("published")
        owned.recover(receipt, "rollback")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        self.assertEqual(self.git("show", ":plan.md"), "old\n")
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.assertTrue(receipt.exists())
        self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), state["candidate"])

    def test_recovery_does_not_rollback_a_foreign_tip(self):
        receipt, state = self.failed("published")
        foreign = self.foreign_commit(state)
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt, "rollback")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), foreign)
        self.assertTrue((self.root / ".git/index.lock").exists())
        owned.recover(receipt)
        self.assert_clean()

    def test_reachable_commit_with_missing_current_owned_blob_refuses_disposal(self):
        def remove(event, state):
            if event == "reconciled":
                self.foreign_commit(state, missing=True)
        with self.assertRaises(owned.Refusal):
            self.run_commit(remove)
        receipt, state = self.receipt()
        self.assertEqual(self.git("merge-base", "--is-ancestor", state["candidate"], "HEAD"), "")
        with self.assertRaisesRegex(owned.Refusal, "current branch changed"):
            owned.recover(receipt)
        self.assertTrue(receipt.exists())

    def test_selected_staging_before_publication_refuses_and_preserves_it(self):
        self.write("plan.md", b"foreign staging\n")
        self.git("add", "plan.md")
        self.write("plan.md", b"new\n")
        with self.assertRaisesRegex(owned.Refusal, "staged entry differs.*plan[.]md"):
            self.run_commit()
        self.assertEqual(self.git("show", ":plan.md"), "foreign staging\n")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        self.receipt()

    def test_selected_staging_after_reconcile_retains_receipt(self):
        def stage(event, state):
            if event == "reconciled":
                self.write("plan.md", b"foreign stage\n")
                self.git("add", "plan.md")
        with self.assertRaisesRegex(owned.Refusal, "staged entry changed"):
            self.run_commit(stage)
        receipt, _ = self.receipt()
        self.assertTrue(receipt.exists())
        self.assertEqual(self.git("show", ":plan.md"), "foreign stage\n")

    def test_changed_shared_index_blocks_recovery(self):
        receipt, _ = self.failed("published")
        data = (self.root / ".git/index").read_bytes()
        (self.root / ".git/index").write_bytes(data + b"foreign")
        with self.assertRaisesRegex(owned.Refusal, "shared index changed"):
            owned.recover(receipt)
        self.assertTrue((self.root / ".git/index.lock").exists())

    def test_foreign_lock_is_never_cleared(self):
        lock = self.root / ".git/index.lock"
        lock.write_bytes(b"foreign lock")
        with self.assertRaises(owned.Refusal):
            self.run_commit()
        self.assertEqual(lock.read_bytes(), b"foreign lock")
        self.receipt()

    def test_discard_unprepared_transaction_preserves_foreign_lock_and_staging(self):
        self.write("other.md", b"foreign staged input\n")
        self.git("add", "other.md")
        self.write("other.md", b"foreign working input\n")
        lock = self.root / ".git/index.lock"
        lock.write_bytes(b"foreign lock")
        with self.assertRaises(owned.Refusal):
            self.run_commit()
        receipt, state = self.receipt()
        self.assertNotIn("prepared.index", state["members"])
        index_before = (self.root / ".git/index").read_bytes()
        head_before = self.git("rev-parse", "HEAD")
        other = self.root / ".git/plan-commit-foreign"
        other.mkdir()
        (other / "keep").write_bytes(b"foreign recovery")
        args = ["recover", str(receipt), "--action", "discard",
                "--receipt-sha256", owned.digest(receipt.read_bytes())]
        self.assertEqual(owned.main([*args, "--dry-run"]), 0)
        self.assertTrue(receipt.exists())
        self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), state["candidate"])
        self.assertEqual(owned.main(args), 0)
        self.assertFalse(receipt.parent.exists())
        self.assertNotEqual(self.git("show-ref", "--verify", state["recovery_ref"], check=False).returncode, 0)
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
        self.assertEqual(self.git("rev-parse", "HEAD"), head_before)
        self.assertEqual(lock.read_bytes(), b"foreign lock")
        self.assertEqual(self.git("show", ":other.md"), "foreign staged input\n")
        self.assertEqual((self.root / "other.md").read_bytes(), b"foreign working input\n")
        self.assertEqual((other / "keep").read_bytes(), b"foreign recovery")

    def test_discard_requires_exact_receipt_members_and_recovery_ref(self):
        receipt, state = self.failed("captured")
        sha = owned.digest(receipt.read_bytes())
        with self.assertRaisesRegex(owned.Refusal, "receipt identity"):
            owned.recover(receipt, "discard", receipt_sha256="0" * 64)
        member = receipt.parent / "input-0"
        original = member.read_bytes()
        member.write_bytes(b"changed")
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt, "discard", receipt_sha256=sha)
        member.write_bytes(original)
        extra = receipt.parent / "foreign"
        extra.write_bytes(b"keep")
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt, "discard", receipt_sha256=sha)
        extra.unlink()
        self.git("update-ref", state["recovery_ref"], self.baseline, state["candidate"])
        with self.assertRaisesRegex(owned.Refusal, "recovery ref changed"):
            owned.recover(receipt, "discard", receipt_sha256=sha)
        self.assertTrue(receipt.exists())
        self.assertEqual(member.read_bytes(), original)
        self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), self.baseline)

    def test_discard_prepared_after_failed_publication_preserves_foreign_state(self):
        self.write("other.md", b"foreign staged input\n")
        self.git("add", "other.md")
        self.write("other.md", b"foreign working input\n")
        def advance(event, state):
            if event == "before_publish":
                self.foreign_commit(state)
        with self.assertRaises(owned.Refusal):
            self.run_commit(advance)
        receipt, state = self.receipt()
        self.assertEqual(state["phase"], "prepared")
        self.assertNotIn("lock_identity", state)
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.write("other.md", b"later foreign staging\n")
        self.git("add", "other.md")
        self.write("other.md", b"later foreign working input\n")
        index_before = (self.root / ".git/index").read_bytes()
        head_before = self.git("rev-parse", "HEAD")
        retained = {p.name: p.read_bytes() for p in receipt.parent.iterdir()}
        other = self.root / ".git/plan-commit-foreign"
        other.mkdir()
        (other / "keep").write_bytes(b"foreign recovery")
        args = ["recover", str(receipt), "--action", "discard-prepared",
                "--receipt-sha256", owned.digest(receipt.read_bytes())]
        self.assertEqual(owned.main([*args, "--dry-run"]), 0)
        self.assertEqual({p.name: p.read_bytes() for p in receipt.parent.iterdir()}, retained)
        self.assertEqual(owned.main(args), 0)
        self.assertFalse(receipt.parent.exists())
        self.assertNotEqual(self.git("show-ref", "--verify", state["recovery_ref"], check=False).returncode, 0)
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
        self.assertEqual(self.git("rev-parse", "HEAD"), head_before)
        self.assertEqual(self.git("show", ":other.md"), "later foreign staging\n")
        self.assertEqual((self.root / "other.md").read_bytes(), b"later foreign working input\n")
        self.assertEqual((self.root / "plan.md").read_bytes(), b"new\n")
        self.assertEqual((other / "keep").read_bytes(), b"foreign recovery")

    def test_discard_prepared_refuses_current_locks_interrupted_output_and_publication(self):
        receipt, state = self.failed("before_publish")
        sha = owned.digest(receipt.read_bytes())
        retained = {p.name: p.read_bytes() for p in receipt.parent.iterdir()}
        lock = self.root / ".git/index.lock"
        lock.write_bytes(b"foreign lock")
        with self.assertRaisesRegex(owned.Refusal, "index lock"):
            owned.recover(receipt, "discard-prepared", receipt_sha256=sha)
        self.assertEqual(lock.read_bytes(), b"foreign lock")
        lock.unlink()
        temporary = receipt.parent / "receipt.tmp"
        temporary.write_bytes(receipt.read_bytes())
        with self.assertRaisesRegex(owned.Refusal, "interrupted receipt"):
            owned.recover(receipt, "discard-prepared", receipt_sha256=sha)
        self.assertEqual(temporary.read_bytes(), retained["receipt.json"])
        temporary.unlink()
        self.git("update-ref", "refs/heads/published", state["candidate"])
        with self.assertRaisesRegex(owned.Refusal, "published"):
            owned.recover(receipt, "discard-prepared", receipt_sha256=sha)
        self.assertEqual({p.name: p.read_bytes() for p in receipt.parent.iterdir()}, retained)
        self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), state["candidate"])

    def test_discard_prepared_requires_released_preparation_metadata(self):
        receipt, state = self.failed("before_publish")
        for update in ({"phase": "building"}, {"lock_identity": [1, 2]},
                       {"lock_write": False}, {"lock_digest": "0" * 64}):
            with self.subTest(update=update):
                owned.save(receipt.parent, dict(state, **update))
                sha = owned.digest(receipt.read_bytes())
                with self.assertRaisesRegex(owned.Refusal, "released lock ownership"):
                    owned.recover(receipt, "discard-prepared", receipt_sha256=sha)
                self.assertTrue(receipt.exists())
                self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), state["candidate"])

    def test_discard_prepared_checks_captured_baselines_outputs_and_unrelated_entries(self):
        receipt, state = self.failed("before_publish")
        originals = {name: (receipt.parent / name).read_bytes()
                     for name in ("shared.index", "prepared.index")}
        blob = self.git("hash-object", "-w", "--stdin", data=b"changed captured entry\n").strip()
        for name, path, reason in (("shared.index", "plan.md", "baseline entry"),
                                   ("prepared.index", "plan.md", "prepared entry"),
                                   ("prepared.index", "other.md", "unrelated captured")):
            with self.subTest(index=name, path=path):
                for member, data in originals.items():
                    (receipt.parent / member).write_bytes(data)
                index = receipt.parent / name
                self.git("update-index", "--add", "--cacheinfo", "100644", blob, path, index=index)
                changed = json.loads(json.dumps(state))
                changed["members"][name] = owned.digest(index.read_bytes())
                changed["lock_digest"] = changed["members"]["prepared.index"]
                owned.save(receipt.parent, changed)
                sha = owned.digest(receipt.read_bytes())
                with self.assertRaisesRegex(owned.Refusal, reason):
                    owned.recover(receipt, "discard-prepared", receipt_sha256=sha)
                self.assertTrue(receipt.exists())
                self.assertEqual(self.git("rev-parse", state["recovery_ref"]).strip(), state["candidate"])

    def test_discard_refuses_symbolic_recovery_ref_and_preserves_its_target(self):
        receipt, state = self.failed("captured")
        foreign = "refs/tags/foreign-candidate"
        self.git("update-ref", foreign, state["candidate"])
        self.git("symbolic-ref", state["recovery_ref"], foreign)
        with self.assertRaisesRegex(owned.Refusal, "symbolic"):
            owned.recover(receipt, "discard", receipt_sha256=owned.digest(receipt.read_bytes()))
        self.assertEqual(self.git("rev-parse", foreign).strip(), state["candidate"])
        self.assertTrue(receipt.exists())

    def test_discard_refuses_published_candidate_and_prepared_transaction(self):
        receipt, state = self.failed("captured")
        sha = owned.digest(receipt.read_bytes())
        self.git("update-ref", "refs/heads/published", state["candidate"])
        with self.assertRaisesRegex(owned.Refusal, "published"):
            owned.recover(receipt, "discard", receipt_sha256=sha)
        self.assertTrue(receipt.exists())
        self.git("update-ref", "-d", "refs/heads/published", state["candidate"])
        state["lock_identity"] = [1, 2]
        owned.save(receipt.parent, state)
        with self.assertRaisesRegex(owned.Refusal, "unprepared"):
            owned.recover(receipt, "discard", receipt_sha256=owned.digest(receipt.read_bytes()))
        self.assertTrue(receipt.exists())

    def test_changed_owned_lock_blocks_recovery(self):
        receipt, _ = self.failed("published")
        lock = self.root / ".git/index.lock"
        lock.write_bytes(b"foreign lock")
        with self.assertRaisesRegex(owned.Refusal, "lock changed"):
            owned.recover(receipt)
        self.assertEqual(lock.read_bytes(), b"foreign lock")

    def test_unexpected_member_blocks_all_deletion(self):
        def add(event, state):
            if event == "reconciled":
                folder = Path(state["index"]).parent / state["recovery_ref"].split("/")[-1]
                (folder / "foreign").write_bytes(b"keep")
        with self.assertRaisesRegex(owned.Refusal, "Unexpected recovery members"):
            self.run_commit(add)
        receipt, state = self.receipt()
        self.assertTrue((receipt.parent / "input-0").exists())
        self.assertEqual((receipt.parent / "foreign").read_bytes(), b"keep")

    def test_junction_member_is_not_followed(self):
        receipt, state = self.failed("published")
        target = self.root / "target"
        target.mkdir()
        (target / "keep").write_bytes(b"safe")
        link = receipt.parent / "foreign-link"
        if os.name == "nt":
            link_arg, target_arg = str(link).replace("'", "''"), str(target).replace("'", "''")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"New-Item -ItemType Junction -Path '{link_arg}' -Target '{target_arg}'"],
                           capture_output=True, check=True)
            self.addCleanup(lambda: subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True))
        else:
            link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt)
        self.assertEqual((target / "keep").read_bytes(), b"safe")
        self.assertTrue((receipt.parent / "input-0").exists())

    def test_false_completion_of_unpublished_candidate_cannot_delete(self):
        receipt, state = self.failed("before_publish")
        with self.assertRaises((owned.Refusal, FileNotFoundError)):
            owned.recover(receipt)
        self.assertTrue(receipt.exists())
        owned.verify_candidate(receipt.parent, state)

    def test_normalized_git_blobs_and_executable_mode(self):
        self.write(".gitattributes", b"plan.md text eol=lf\n")
        self.write("plan.md", b"new\r\n")
        self.files[0][1] = owned.digest(b"new\r\n")
        self.files[0][4] = "100755"
        self.run_commit()
        self.assertEqual(self.git("show", "HEAD:plan.md"), "new\n")
        self.assertTrue(self.git("ls-tree", "HEAD", "plan.md").startswith("100755"))

    def test_exact_inputs_and_provenance_are_required(self):
        for mutation in (lambda: self.files[0].__setitem__(1, "wrong"),
                         lambda: self.files[0].__setitem__(2, "wrong"),
                         lambda: self.files[0].__setitem__(0, "../plan.md")):
            original = [list(item) for item in self.files]
            mutation()
            with self.assertRaises(owned.Refusal):
                self.run_commit()
            self.files = original
        with self.assertRaises(owned.Refusal):
            owned.commit(self.root, "refs/heads/main", self.baseline, self.files, b"no provenance\n")
        self.assert_clean()

    def test_new_file_uses_explicit_absence(self):
        self.write("new.md", b"added\n")
        self.files = [["new.md", owned.digest(b"added\n"), "absent", "absent", "100644"]]
        self.run_commit()
        self.assertEqual(self.git("show", "HEAD:new.md"), "added\n")
        self.assert_clean()

    def test_split_index_preserves_unrelated_staging(self):
        self.git("update-index", "--split-index")
        self.write("other.md", b"split staged\n")
        self.git("add", "other.md")
        self.run_commit()
        self.assertEqual(self.git("show", ":other.md"), "split staged\n")
        self.assert_clean()

    def test_recovery_after_index_replace_before_receipt_update(self):
        real = owned.save
        def interrupt(folder, state):
            if state["phase"] == "reconciled":
                raise KeyboardInterrupt("replacement completed before receipt update")
            return real(folder, state)
        with mock.patch.object(owned, "save", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_commit()
        receipt, state = self.receipt()
        self.assertEqual(state["phase"], "published")
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.assertEqual(self.git("show", ":plan.md"), "new\n")
        owned.recover(receipt)
        self.assert_clean()

    def test_partial_lock_write_and_fsync_failure_keep_complete_recovery(self):
        real_write = os.write
        real_fsync = os.fsync
        for fault in ("partial", "fsync"):
            with self.subTest(fault=fault):
                calls = []
                def write(fd, data):
                    if not calls:
                        calls.append(True)
                        return real_write(fd, data[:8])
                    raise OSError("fixture partial write")
                def fsync(fd):
                    lock = self.root / ".git/index.lock"
                    if lock.exists() and os.fstat(fd).st_ino == lock.stat().st_ino:
                        raise OSError("fixture fsync failure")
                    return real_fsync(fd)
                with mock.patch.object(owned.os, "write", write if fault == "partial" else real_write), \
                     mock.patch.object(owned.os, "fsync", fsync if fault == "fsync" else real_fsync):
                    with self.assertRaises((owned.Refusal, OSError)):
                        self.run_commit()
                receipt, state = self.receipt()
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
                owned.inventory(receipt.parent, state)
                self.assertEqual(set(state["members"]), {"message", "input-0", "candidate.index", "shared.index", "prepared.index"})
                if (self.root / ".git/index.lock").exists():
                    owned.recover(receipt, "rollback")
                # Retained recovery belongs to this case, separate from the next case.
                if fault == "partial":
                    self.setUp()

    def test_unpublished_hard_interruption_recovers_only_owned_partial_lock(self):
        real_write = os.write
        def write(fd, data):
            return real_write(fd, data[:8])
        def interrupt(event, state):
            if event == "lock_write_progress":
                raise KeyboardInterrupt("fixture stopped during lock write")
        with mock.patch.object(owned.os, "write", write):
            with self.assertRaises(KeyboardInterrupt):
                self.run_commit(interrupt)
        receipt, state = self.receipt()
        owned.inventory(receipt.parent, state)
        self.assertEqual((self.root / ".git/index.lock").stat().st_size, 8)
        owned.recover(receipt, "rollback")
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        self.assertTrue(receipt.exists())

    def test_rollback_retry_after_its_ref_cas_checks_index_and_lock(self):
        receipt, state = self.failed("published")
        def interrupt(event, state):
            if event == "rollback_ref_restored":
                raise KeyboardInterrupt("fixture stopped after rollback CAS")
        with mock.patch.object(owned, "checkpoint", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                owned.recover(receipt, "rollback")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        original = (self.root / ".git/index").read_bytes()
        (self.root / ".git/index").write_bytes(original + b"foreign")
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt, "rollback")
        (self.root / ".git/index").write_bytes(original)
        owned.recover(receipt, "rollback")
        self.assertFalse((self.root / ".git/index.lock").exists())
        self.assertTrue(receipt.exists())

    def test_disposal_resumes_after_ref_and_member_removal(self):
        for event in ("disposal_ref_removed", "disposal_member_removed"):
            with self.subTest(event=event):
                receipt, state = self.failed("reconciled")
                def interrupt(current, state):
                    if current == event:
                        raise KeyboardInterrupt("fixture disposal interrupted")
                with mock.patch.object(owned, "checkpoint", interrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        owned.recover(receipt)
                (receipt.parent / "foreign").write_bytes(b"keep")
                remaining = {p.name: p.read_bytes() for p in receipt.parent.iterdir()}
                with self.assertRaises(owned.Refusal):
                    owned.recover(receipt)
                self.assertEqual({p.name: p.read_bytes() for p in receipt.parent.iterdir()}, remaining)
                (receipt.parent / "foreign").unlink()
                owned.recover(receipt)
                self.assert_clean()
                if event == "disposal_ref_removed":
                    self.setUp()

    def test_false_disposal_phase_does_not_grant_deletion(self):
        receipt, state = self.failed("before_publish")
        state["phase"] = "disposing"
        owned.save(receipt.parent, state)
        before = {p.name: p.read_bytes() for p in receipt.parent.iterdir()}
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt)
        self.assertEqual({p.name: p.read_bytes() for p in receipt.parent.iterdir()}, before)

    def test_partial_disposal_receipt_cannot_claim_a_foreign_member(self):
        receipt, state = self.failed("reconciled")
        foreign = receipt.parent.parent / "foreign-recovery-input"
        foreign.write_bytes(b"keep")
        state["phase"] = "disposing"
        state["members"]["../foreign-recovery-input"] = owned.digest(b"keep")
        owned.save(receipt.parent, state)
        before = {p.name: p.read_bytes() for p in receipt.parent.iterdir()}
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt)
        self.assertEqual(foreign.read_bytes(), b"keep")
        self.assertEqual({p.name: p.read_bytes() for p in receipt.parent.iterdir()}, before)

    def test_interrupted_receipt_output_is_exact_or_retained(self):
        receipt, state = self.failed("published")
        output = receipt.parent / "receipt.tmp"
        output.write_bytes(b"foreign partial output")
        before = output.read_bytes()
        with self.assertRaises(owned.Refusal):
            owned.recover(receipt)
        self.assertEqual(output.read_bytes(), before)
        # A completed write of the current persisted receipt is independently recognizable.
        output.write_bytes(receipt.read_bytes())
        owned.recover(receipt)
        self.assert_clean()

    def test_baseline_case_alias_refuses_before_scratch(self):
        self.files[0] = ["PLAN.md", owned.digest(b"new\n"), "absent", "absent", "100644"]
        with self.assertRaisesRegex(owned.Refusal, "aliases"):
            self.run_commit()
        self.assertEqual(self.git("ls-tree", "HEAD", "plan.md").split()[2], self.blob)
        self.assert_clean()

    def test_canonical_root_allows_linked_ancestors_but_not_selected_links(self):
        link = self.root / "access"
        if os.name == "nt":
            result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                     f"New-Item -ItemType Junction -Path '{link}' -Target '{self.root}'"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.addCleanup(lambda: subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True))
        else:
            link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(owned.Refusal):
            owned.commit(self.root, "refs/heads/main", self.baseline,
                         [["access/plan.md", owned.digest(b"new\n"), "absent", "absent", "100644"]], MESSAGE)
        owned.commit(link, "refs/heads/main", self.baseline, self.files, MESSAGE)
        self.assert_clean()

    def test_index_permissions_survive_replacement_and_recovery(self):
        if os.name == "nt":
            # Check the portable mode handoff. Native POSIX permission behavior needs POSIX.
            with mock.patch.object(owned.os, "chmod", wraps=os.chmod) as chmod:
                self.run_commit()
            self.assertTrue(any(str(call.args[0]).endswith("index.lock") for call in chmod.call_args_list))
        else:
            os.chmod(self.root / ".git/index", 0o660)
            receipt, state = self.failed("published")
            owned.recover(receipt)
            self.assertEqual(stat.S_IMODE((self.root / ".git/index").stat().st_mode), 0o660)

    def install_hooks(self):
        references = Path(os.environ.get("PLAN_COMMIT_REFERENCE_ROOT", Path(__file__).parent.parent / "references"))
        hooks = self.root / ".githooks"
        hooks.mkdir()
        for name in ("pre-commit", "commit-msg"):
            target = hooks / name
            target.write_bytes((references / name).read_bytes())
            target.chmod(0o755)
        self.git("config", "core.hooksPath", ".githooks")
        return hooks

    def test_configured_reference_hooks_and_message_arguments_use_private_index(self):
        hooks = self.install_hooks()
        reference = hooks / "pre-commit-reference"
        reference.write_bytes((hooks / "pre-commit").read_bytes())
        (hooks / "pre-commit").write_bytes(b'#!/bin/sh\nsh .githooks/pre-commit-reference || exit 30\n[ "$(git show :plan.md)" = new ] || exit 31\nprintf private > hook-pre\n')
        prepare = hooks / "prepare-commit-msg"
        prepare.write_bytes(b'#!/bin/sh\n[ "$#" = 2 ] && [ "$2" = message ] && [ -f "$1" ] || exit 32\nprintf prepare > hook-prepare\n')
        prepare.chmod(0o755)
        with (hooks / "commit-msg").open("ab") as output:
            output.write(b'\n[ "$#" = 1 ] && [ -f "$1" ] || exit 33\nprintf message > hook-message\n')
        self.write("other.md", b"foreign staged\n")
        self.git("add", "other.md")
        self.run_commit()
        for name in ("pre", "prepare", "message"):
            self.assertTrue((self.root / ("hook-" + name)).exists())
        self.assertEqual(self.git("show", ":other.md"), "foreign staged\n")
        self.assert_clean()

    def test_hook_rejection_and_message_or_index_mutation_refuse_publication(self):
        hooks = self.install_hooks()
        for body in (b'exit 42\n', b'printf changed >> "$1"\n', b'git update-index --force-remove plan.md\n'):
            target = hooks / "prepare-commit-msg"
            target.write_bytes(b"#!/bin/sh\n" + body)
            target.chmod(0o755)
            with self.assertRaises(owned.Refusal):
                self.run_commit()
            self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.baseline)
        self.assertEqual(len(self.receipts()), 3)

    def test_provenance_matches_standalone_copied_hook(self):
        hooks = self.install_hooks()
        messages = [MESSAGE, MESSAGE.replace(b"fixture", b"unknown-model"),
                    MESSAGE.replace(b"fixture", b"fixture (custom)"),
                    MESSAGE.replace(b"11111111-2222-4333-8444-555555555555", b"11111111222243338444555555555555"),
                    MESSAGE.replace(b"11111111-2222-4333-8444-555555555555", b"{11111111-2222-4333-8444-555555555555}"),
                    MESSAGE.replace(b"fixture (effort high, test)", b"unknown"),
                    MESSAGE.replace(b"Agent-Session:", b"\nAgent-Session:")]
        for message in messages:
            self.write("message.txt", message)
            result = self.git("hook", "run", "commit-msg", "--", str(self.root / "message.txt"), check=False)
            try:
                owned.validate_message(self.root, message)
                admitted = True
            except owned.Refusal:
                admitted = False
            self.assertEqual(admitted, result.returncode == 0, message)


if __name__ == "__main__":
    unittest.main()
