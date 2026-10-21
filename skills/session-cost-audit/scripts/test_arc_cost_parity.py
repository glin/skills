#!/usr/bin/env python3
"""Check Arc command selection and the session cost contract."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("arc_cost.py")
SPEC = importlib.util.spec_from_file_location("arc_cost", SCRIPT)
assert SPEC and SPEC.loader
ARC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARC)

class ArcResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "configured repo"
        self.bundle = self.repo / "cli" / "dist" / "arc.cjs"
        self.default = self.root / "default repo"
        self.default_bundle = self.default / "cli" / "dist" / "arc.cjs"
        self.env = {"ARC_STATUS": "", "ARC_REPO": str(self.repo), "PATH": str(self.root / "path")}

    def test_full_command_override_passes_arguments_and_requests_json(self) -> None:
        self.env["ARC_STATUS"] = f'node "{self.root / "custom bundle.cjs"}"'
        completed = subprocess.CompletedProcess([], 0, json.dumps({"schemaVersion": 2, "combined": {}}), "")
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.subprocess, "run", return_value=completed) as run:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(ARC.main(["session", "--since", "2026-09-01T00:00:00Z"]), 0)
        self.assertEqual(json.loads(output.getvalue())["schemaVersion"], 2)
        self.assertEqual(run.call_args.args[0], ["node", str(self.root / "custom bundle.cjs"), "cost", "session", "--since", "2026-09-01T00:00:00Z", "--json"])
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    @unittest.skipUnless(os.name == "nt", "Windows command-line quoting")
    def test_single_quoted_windows_override(self) -> None:
        self.assertEqual(ARC.split_command(r"node 'C:\Program Files\Arc\arc.cjs'"),
                         ["node", r"C:\Program Files\Arc\arc.cjs"])

    def test_missing_override_bundle_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node "{self.root / "missing.cjs"}"'
        self.assert_missing_script_has_build_help()

    def test_missing_override_bundle_after_node_flag_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node --no-warnings "{self.root / "missing.cjs"}"'
        self.assert_missing_script_has_build_help()

    def assert_missing_script_has_build_help(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node is unavailable")
        env = {**os.environ, "ARC_STATUS": self.env["ARC_STATUS"], "ARC_REPO": str(self.repo)}
        result = subprocess.run([sys.executable, str(SCRIPT), "session"], capture_output=True,
                                text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("npm --prefix", result.stderr)
        self.assertNotIn("&&", result.stderr)

    def test_missing_entry_after_value_taking_node_flag_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node -C development "{self.root / "missing.cjs"}"'
        self.assert_missing_script_has_build_help()

    def test_missing_entry_after_node_import_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node --import "data:text/javascript," "{self.root / "missing.cjs"}"'
        self.assert_missing_script_has_build_help()

    def test_missing_entry_without_node_stack_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node --stack-trace-limit=0 "{self.root / "missing.cjs"}"'
        self.assert_missing_script_has_build_help()

    def test_missing_esm_entry_after_node_import_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node --import "data:text/javascript," "{self.root / "missing.mjs"}"'
        self.assert_missing_script_has_build_help()

    def test_missing_entry_with_trailing_script_argument_gives_build_help(self) -> None:
        self.env["ARC_STATUS"] = f'node "{self.root / "missing.cjs"}" --trace'
        self.assert_missing_script_has_build_help()

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_existing_entry_after_node_title_keeps_arc_error(self) -> None:
        bundle = self.root / "existing-bundle.cjs"
        bundle.write_text('process.stderr.write("arc: Unknown cost option: --definitely-invalid\\n"); process.exitCode = 1;\n', encoding="utf-8")
        env = {**os.environ, "ARC_STATUS": f'node --title arc.cjs "{bundle}"', "ARC_REPO": str(self.repo)}
        result = subprocess.run([sys.executable, str(SCRIPT), "--definitely-invalid"], capture_output=True,
                                text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "arc: Unknown cost option: --definitely-invalid\n")

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_missing_import_in_existing_entry_keeps_node_error(self) -> None:
        bundle = self.root / "existing-bundle.cjs"
        bundle.write_text('require("./missing-import.cjs");\n', encoding="utf-8")
        env = {**os.environ, "ARC_STATUS": f'node "{bundle}"', "ARC_REPO": str(self.repo)}
        result = subprocess.run([sys.executable, str(SCRIPT), "session"], capture_output=True,
                                text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing-import.cjs", result.stderr)
        self.assertNotIn("npm --prefix", result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_missing_node_import_with_existing_entry_keeps_node_error(self) -> None:
        bundle = self.root / "existing-bundle.cjs"
        bundle.write_text('process.stdout.write("unused");\n', encoding="utf-8")
        missing_import = self.root / "missing-import.mjs"
        env = {**os.environ, "ARC_STATUS": f'node --import "{missing_import.as_uri()}" "{bundle}"',
               "ARC_REPO": str(self.repo)}
        result = subprocess.run([sys.executable, str(SCRIPT), "session"], capture_output=True,
                                text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing-import.mjs", result.stderr)
        self.assertNotIn("npm --prefix", result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_missing_import_named_by_script_argument_keeps_node_error(self) -> None:
        bundle = self.root / "existing-bundle.cjs"
        missing = self.root / "missing.cjs"
        bundle.write_text(f'require("{missing.as_posix()}");\n', encoding="utf-8")
        env = {**os.environ, "ARC_STATUS": f'node "{bundle}" "{missing}"', "ARC_REPO": str(self.repo)}
        result = subprocess.run([sys.executable, str(SCRIPT), "session"], capture_output=True,
                                text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing.cjs", result.stderr)
        self.assertNotIn("npm --prefix", result.stderr)

    def test_unicode_json_is_forwarded_as_utf8(self) -> None:
        self.env["ARC_STATUS"] = "arc"
        report = json.dumps({"schemaVersion": 2, "source": {"file": "Łódź.jsonl"}}, ensure_ascii=False)
        completed = subprocess.CompletedProcess([], 0, report, "")
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.subprocess, "run", return_value=completed):
            with contextlib.redirect_stdout(output):
                self.assertEqual(ARC.main(["session"]), 0)
        self.assertEqual(json.loads(output.getvalue())["source"]["file"], "Łódź.jsonl")

    def test_unicode_json_survives_a_real_pipe(self) -> None:
        child = self.root / "unicode cost.py"
        child.write_text('import json, sys\nsys.stdout.buffer.write(json.dumps({"schemaVersion": 2, "source": {"file": "Łódź.jsonl"}}, ensure_ascii=False).encode("utf-8"))\n',
                         encoding="utf-8")
        env = {**os.environ, "ARC_STATUS": f'"{sys.executable}" "{child}"', "ARC_REPO": str(self.repo)}
        output = subprocess.run([sys.executable, str(SCRIPT), "session"], capture_output=True, env=env)
        self.assertEqual(output.returncode, 0, output.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(output.stdout.decode("utf-8"))["source"]["file"], "Łódź.jsonl")

    def test_configured_checkout_outranks_stale_path_command(self) -> None:
        self.bundle.parent.mkdir(parents=True)
        self.bundle.touch()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.shutil, "which", return_value="stale-arc"):
            command, _, _ = ARC.command_for()
        self.assertEqual(command, ["node", str(self.bundle)])

    def test_missing_configured_bundle_fails_even_with_stale_path_command(self) -> None:
        completed = subprocess.CompletedProcess([], 0, '{"schemaVersion": 2}', "")
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.shutil, "which", return_value="stale-arc"), mock.patch.object(ARC.subprocess, "run", return_value=completed) as run:
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(ARC.main(["session"]), 2)
        run.assert_not_called()
        self.assertIn(str(self.bundle), error.getvalue())
        self.assertIn("npm --prefix", error.getvalue())

    def test_default_checkout_outranks_path(self) -> None:
        self.env["ARC_REPO"] = ""
        self.default_bundle.parent.mkdir(parents=True)
        self.default_bundle.touch()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC.shutil, "which", return_value="stale-arc"):
            command, _, _ = ARC.command_for()
        self.assertEqual(command, ["node", str(self.default_bundle)])

    def link_checkout(self) -> tuple[Path, Path]:
        checkout = self.root / "linked arc"
        (checkout / ".git").mkdir(parents=True)
        bundle = checkout / "cli" / "dist" / "arc.cjs"
        bundle.parent.mkdir(parents=True)
        bundle.touch()
        prefix = self.root / "npm prefix"
        scope = prefix / "node_modules" / "@glin"
        scope.mkdir(parents=True)
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(checkout / "cli"), str(scope / "arc"))
            shim = prefix / "arc.CMD"
            shim.touch()
        else:
            (scope / "arc").symlink_to(checkout / "cli", target_is_directory=True)
            shim = prefix / "arc"
            shim.symlink_to(scope / "arc" / "dist" / "arc.cjs")
        self.addCleanup(lambda: os.rmdir(scope / "arc") if os.name == "nt" else None)
        return checkout, shim

    def test_linked_path_command_outranks_default_and_names_its_checkout(self) -> None:
        self.env["ARC_REPO"] = ""
        self.default_bundle.parent.mkdir(parents=True)
        self.default_bundle.touch()
        checkout, shim = self.link_checkout()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC.shutil, "which", return_value=str(shim)):
            command, _, repo = ARC.command_for()
        self.assertEqual(repo.resolve(), checkout.resolve())
        self.assertEqual(command, ["node", str(repo / "cli" / "dist" / "arc.cjs")])

    def test_linked_checkout_behind_editor_command_runs_its_current_bundle(self) -> None:
        self.env["ARC_REPO"] = ""
        checkout, shim = self.link_checkout()
        current = checkout / "cli" / "dist" / "arc.cjs"
        leftover = current.with_name("arc-status.cjs")
        leftover.touch()
        old_shim = shim.with_name("arc-status.CMD" if os.name == "nt" else "arc-status")
        editor = self.root / "editor bin"
        editor.mkdir()
        editor_shim = editor / ("arc.CMD" if os.name == "nt" else "arc")
        editor_shim.touch()
        if os.name == "nt":
            old_shim.touch()
        else:
            old_shim.symlink_to(leftover)
            for file in (current, leftover, editor_shim):
                file.chmod(0o755)
        self.env["PATH"] = os.pathsep.join([str(editor), str(shim.parent)])
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC, "refresh_build"):
            command, _, repo = ARC.command_for()
        self.assertEqual(repo.resolve(), checkout.resolve())
        self.assertEqual(command, ["node", str(repo / "cli" / "dist" / "arc.cjs")])

    def stale_checkout(self, status: str) -> tuple[Path, list[list[str]]]:
        checkout = self.root / "stale arc"
        bundle = checkout / "cli" / "dist" / "arc.cjs"
        bundle.parent.mkdir(parents=True)
        bundle.write_text('buildCommit: true ? "' + "a" * 40 + '" : "unknown"', encoding="utf-8")
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            if args[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, "b" * 40 + "\n", "")
            if "status" in args:
                return subprocess.CompletedProcess(args, 0, status, "")
            return subprocess.CompletedProcess(args, 0, "", "")

        self.run = run
        return checkout, calls

    def test_stale_build_with_committed_sources_is_rebuilt(self) -> None:
        checkout, calls = self.stale_checkout("")
        with mock.patch.object(ARC.subprocess, "run", side_effect=self.run), mock.patch.object(ARC.shutil, "which", return_value="npm"):
            ARC.refresh_build(checkout)
        self.assertIn(["npm", "--prefix", str(checkout / "cli"), "run", "build"], calls)

    def test_stale_build_with_uncommitted_sources_is_left_alone(self) -> None:
        checkout, calls = self.stale_checkout(" M extension/src/pricing.ts\n")
        error = io.StringIO()
        with mock.patch.object(ARC.subprocess, "run", side_effect=self.run), mock.patch.object(ARC.shutil, "which", return_value="npm"), contextlib.redirect_stderr(error):
            ARC.refresh_build(checkout)
        self.assertFalse(any("build" in call for call in calls))
        self.assertIn("uncommitted", error.getvalue())

    def test_current_build_is_not_rebuilt(self) -> None:
        checkout, calls = self.stale_checkout("")
        (checkout / "cli" / "dist" / "arc.cjs").write_text('"' + "b" * 40 + '"', encoding="utf-8")
        with mock.patch.object(ARC.subprocess, "run", side_effect=self.run), mock.patch.object(ARC.shutil, "which", return_value="npm"):
            ARC.refresh_build(checkout)
        self.assertEqual(len(calls), 1)

    def test_editor_command_runs_its_bundle_last(self) -> None:
        self.env["ARC_REPO"] = ""
        bundle = self.root / "glin.arc-0.1.0" / "dist" / "cli.cjs"
        bundle.parent.mkdir(parents=True)
        bundle.touch()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC.shutil, "which", return_value=str(bundle.parent / "bin" / "arc")):
            command, _, _ = ARC.command_for()
        self.assertEqual(command, ["node", str(bundle)])

    def release_launcher(self, name: str) -> Path:
        launcher = self.root / "selected home" / ".arc" / "tools" / "releases" / "release-id" / "launchers" / name
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.touch()
        launcher.chmod(0o755)
        return launcher

    def test_release_launcher_executes_inherited_editor_and_forwards_arguments(self) -> None:
        launcher = self.release_launcher("arc.cmd" if os.name == "nt" else "arc")
        if os.name == "nt":
            launcher.write_text('@echo off\nsetlocal DisableDelayedExpansion\nset "ELECTRON_RUN_AS_NODE=1"\n"%ARC_EDITOR_EXECUTABLE%" "%~dp0..\\cli\\arc.cjs" %*\nexit /b %errorlevel%\n', encoding="utf-8")
        else:
            launcher.write_text('#!/bin/sh\nELECTRON_RUN_AS_NODE=1 exec "$ARC_EDITOR_EXECUTABLE" "${0%/*}/../cli/arc.cjs" "$@"\n', encoding="utf-8")
        bundle = launcher.parent.parent / "cli" / "arc.cjs"
        bundle.parent.mkdir()
        bundle.write_text('import json, os, sys\nprint(json.dumps({"schemaVersion": 2, "arguments": sys.argv[1:], "nodeMode": os.environ.get("ELECTRON_RUN_AS_NODE")}))\n', encoding="utf-8")
        home = launcher.parents[5]
        env = {**os.environ, "ARC_STATUS": "", "ARC_REPO": "", "PATH": str(launcher.parent),
               "HOME": str(home), "USERPROFILE": str(home), "ARC_EDITOR_EXECUTABLE": sys.executable,
               "ARC_TEST_PERCENT": "controlled expansion"}
        argument_cases = [
            [str(self.root / "session transcript.jsonl"), "--since", "2026-09-01T00:00:00Z", "--by-tool-kind"],
            ["session", "--until-text", "alpha|echo"],
            ["session&echo.jsonl"],
            ["%ARC_TEST_PERCENT%.jsonl", "--until-text", "%ARC_TEST_PERCENT%"],
            ["session", "--until-text", 'alpha"|echo "beta'],
            ["session", "--until-text", r'back\slash\\"quote', "path with spaces\\", "!literal!^caret<>"],
            ["session", "--until-text", '""%ARC_TEST_PERCENT%"&echo"', "", "tab\ttext"],
            ["session", "--until-text", "alpha\nbeta"],
            ["session", "--until-text", "alpha\rbeta"],
        ]
        for arguments in argument_cases:
            for json_options in ([], ["--json"]):
                with self.subTest(arguments=arguments, json_options=json_options), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default):
                    output = io.StringIO()
                    error = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                        if os.name == "nt" and any("\r" in argument or "\n" in argument for argument in arguments):
                            with mock.patch.object(ARC.subprocess, "run") as run:
                                self.assertEqual(ARC.main([*arguments, *json_options]), 2)
                            run.assert_not_called()
                            self.assertIn("line breaks", error.getvalue())
                            self.assertEqual(output.getvalue(), "")
                            continue
                        self.assertEqual(ARC.main([*arguments, *json_options]), 0, error.getvalue())
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["arguments"], ["cost", *arguments, "--json"])
                    self.assertEqual(report["nodeMode"], "1")

    def test_release_launcher_selection_rejects_other_homes_and_layouts(self) -> None:
        for platform, name, home_key in (("posix", "arc", "HOME"), ("nt", "ARC.CMD", "USERPROFILE")):
            launcher = self.release_launcher(name)
            home = launcher.parents[5]
            other_home = self.root / "other home"
            env = {**self.env, "ARC_REPO": "", "HOME": str(other_home), "USERPROFILE": str(other_home), home_key: str(home)}
            candidates = [
                (launcher, [str(launcher)]),
                (other_home / launcher.relative_to(home), None),
                (launcher.with_name("arc.cmd" if platform == "posix" else "arc.exe"), None),
                (launcher.with_name("ARC" if platform == "posix" else "arc-status.cmd"), None),
                (launcher.parent.parent / "bin" / name, None),
                (launcher.parent.parent.parent / "launchers" / name, None),
                (launcher.parent.parent.parent / ".." / "launchers" / name, None),
                (launcher.parent.parent / "nested" / "launchers" / name, None),
                (home / ".arc-other" / "tools" / "releases" / "release-id" / "launchers" / name, None),
            ]
            for candidate, expected in candidates:
                with self.subTest(platform=platform, candidate=candidate), mock.patch.object(ARC, "Path", type(self.root)), mock.patch.object(ARC.os, "name", platform):
                    with mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC, "path_commands", return_value=[str(candidate)]):
                        command, _, _ = ARC.command_for(env)
                        self.assertEqual(command, expected)

    def test_release_launcher_preserves_command_and_checkout_precedence(self) -> None:
        launcher = self.release_launcher("arc.cmd" if os.name == "nt" else "arc")
        self.bundle.parent.mkdir(parents=True)
        self.bundle.touch()
        self.default_bundle.parent.mkdir(parents=True)
        self.default_bundle.touch()
        checkout, shim = self.link_checkout()
        env = {**self.env, "HOME": str(launcher.parents[5]), "USERPROFILE": str(launcher.parents[5])}
        cases = [
            ({"ARC_STATUS": "custom arc"}, [str(launcher)], ["custom", "arc"]),
            ({}, [str(launcher)], ["node", str(self.bundle)]),
            ({"ARC_REPO": str(self.root / "absent repo")}, [str(launcher)], None),
            ({"ARC_REPO": ""}, [str(launcher), str(shim)], ["node", str(checkout / "cli" / "dist" / "arc.cjs")]),
            ({"ARC_REPO": ""}, [str(launcher)], ["node", str(self.default_bundle)]),
        ]
        for overrides, paths, expected in cases:
            with self.subTest(overrides=overrides, paths=paths), mock.patch.object(ARC, "DEFAULT_REPO", self.default):
                with mock.patch.object(ARC, "path_commands", return_value=paths), mock.patch.object(ARC, "refresh_build"):
                    command, _, _ = ARC.command_for({**env, **overrides})
                    self.assertEqual(command, expected)

    def test_release_and_legacy_editor_commands_keep_path_order(self) -> None:
        launcher = self.release_launcher("arc.cmd" if os.name == "nt" else "arc")
        later = launcher.parent.parent.parent / "later-release" / "launchers" / launcher.name
        later.parent.mkdir(parents=True)
        later.touch()
        bundle = self.root / "extension" / "dist" / "cli.cjs"
        bundle.parent.mkdir(parents=True)
        bundle.touch()
        legacy = str(bundle.parent / "bin" / launcher.name)
        env = {**self.env, "ARC_REPO": "", "HOME": str(launcher.parents[5]), "USERPROFILE": str(launcher.parents[5])}
        cases = [
            ([str(launcher), legacy], [str(launcher)]),
            ([legacy, str(launcher)], ["node", str(bundle)]),
            ([str(later), str(launcher)], [str(later)]),
            ([str(self.root / "arbitrary" / launcher.name), str(launcher)], [str(launcher)]),
        ]
        for paths, expected in cases:
            with self.subTest(paths=paths), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC, "path_commands", return_value=paths):
                command, _, _ = ARC.command_for(env)
                self.assertEqual(command, expected)

    def test_stale_path_command_is_refused(self) -> None:
        self.env["ARC_REPO"] = ""
        checkout = self.root / "old arc"
        (checkout / ".git").mkdir(parents=True)
        leftover = checkout / "cli" / "dist" / "arc-status.cjs"
        leftover.parent.mkdir(parents=True)
        leftover.touch()
        prefix = self.root / "npm prefix"
        scope = prefix / "node_modules" / "@glin"
        scope.mkdir(parents=True)
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(checkout / "cli"), str(scope / "arc"))
            self.addCleanup(os.rmdir, scope / "arc")
            shim = prefix / "arc.cmd"
            shim.write_text('@"node" "%~dp0\\node_modules\\@glin\\arc\\dist\\arc-status.cjs" %*\n', encoding="utf-8")
        else:
            (scope / "arc").symlink_to(checkout / "cli", target_is_directory=True)
            shim = prefix / "arc"
            shim.write_text('#!/bin/sh\nexec node "${0%/*}/node_modules/@glin/arc/dist/arc-status.cjs" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
        self.env["PATH"] = str(prefix)
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC.subprocess, "run") as run:
            self.assertEqual(ARC.path_commands(None), [shutil.which("arc", path=str(prefix))])
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(ARC.main(["session"]), 2)
        run.assert_not_called()
        self.assertIn("Arc cost command is unavailable.", error.getvalue())

    def test_empty_path_entry_is_current_directory_on_posix(self) -> None:
        folders: list[str] = []
        def which(_: str, path: str) -> str | None:
            folders.append(path)
            return os.path.join(path, "arc") if path == os.curdir else None
        listed = os.pathsep.join([str(self.root / "bin"), ""])
        with mock.patch.object(ARC.shutil, "which", side_effect=which):
            with mock.patch.object(ARC.os, "name", "posix"):
                self.assertEqual(ARC.path_commands(listed), [os.path.join(os.curdir, "arc")])
            self.assertEqual(folders, [str(self.root / "bin"), os.curdir])
            with mock.patch.object(ARC.os, "name", "nt"):
                self.assertEqual(ARC.path_commands(listed), [])

    def test_absent_command_exits_two_with_build_instruction(self) -> None:
        self.env["ARC_REPO"] = ""
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC, "DEFAULT_REPO", self.default), mock.patch.object(ARC.shutil, "which", return_value=None):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(ARC.main(["session"]), 2)
        self.assertIn(str(self.default_bundle), error.getvalue())
        self.assertIn(f'npm --prefix "{self.default}/cli" install', error.getvalue())

    def test_unknown_schema_exits_two(self) -> None:
        self.env["ARC_STATUS"] = "node fixture.cjs"
        completed = subprocess.CompletedProcess([], 0, '{"schemaVersion": 3}', "")
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.subprocess, "run", return_value=completed):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(ARC.main(["session"]), 2)
        self.assertIn("schemaVersion 2", error.getvalue())

    def test_arc_error_passes_through(self) -> None:
        bundle = self.root / "fixture.cjs"
        bundle.touch()
        self.env["ARC_STATUS"] = f'node "{bundle}"'
        completed = subprocess.CompletedProcess([], 1, "", "No transcript found.\n")
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ARC.subprocess, "run", return_value=completed):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(ARC.main(["absent"]), 1)
        self.assertEqual(error.getvalue(), "No transcript found.\n")


@unittest.skipUnless(os.environ.get("ARC_REPO"), "Arc integration requires a configured checkout")
class ArcFixtureTest(unittest.TestCase):
    def test_real_claude_fixture_uses_arc_parent_children_and_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory) / "projects"
            file = projects / "sample" / "session.jsonl"
            file.parent.mkdir(parents=True)
            record = {"type": "assistant", "timestamp": "2026-10-02T00:00:01Z", "sessionId": "session",
                      "message": {"id": "parent", "model": "claude-opus-5", "content": [],
                                  "usage": {"input_tokens": 1000, "output_tokens": 100}}}
            file.write_text(json.dumps(record) + "\n", encoding="utf-8")
            child = file.parent / "session" / "subagents" / "agent-child.jsonl"
            child.parent.mkdir(parents=True)
            child_record = {**record, "isSidechain": True, "agentId": "child",
                            "message": {**record["message"], "id": "child"}}
            child.write_text(json.dumps(child_record) + "\n", encoding="utf-8")
            for script in ["arc_cost.py", "summarize_usage.py", "transcript_usage.py"]:
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(SCRIPT.with_name(script)), str(file),
                                             "--projects", str(projects), "--by-model", "--until", "2026-10-02T00:00:02Z"],
                                            capture_output=True, text=True, encoding="utf-8", env={**os.environ, "ARC_STATUS": ""})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(report["schemaVersion"], 2)
                    self.assertEqual(report["parent"]["requests"], 1)
                    self.assertEqual(report["combined"]["requests"], 2)
                    self.assertEqual(report["parent"]["costUsd"], 0.0075)
                    self.assertEqual(report["combined"]["costUsd"], 0.015)
                    self.assertEqual(report["parent"]["modelCosts"][0]["model"], "claude-opus-5")
                    self.assertEqual(report["window"]["until"], "2026-10-02T00:00:02.000Z")


class DefaultRepoTest(unittest.TestCase):
    def test_worktree_copy_finds_checkout_beside_main_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp) / "skills"
            worktree = main / ".claude" / "worktrees" / "topic"
            main.mkdir()
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            for arguments in (["init", "-q"], ["-c", "user.name=Test", "-c", "user.email=test@example.com",
                                               "commit", "-q", "--allow-empty", "-m", "base"],
                              ["worktree", "add", "-q", str(worktree)]):
                subprocess.run(["git", "-C", str(main), *arguments], check=True, env=env)
            copy = worktree / "skills" / "session-cost-audit" / "scripts" / "arc_cost.py"
            copy.parent.mkdir(parents=True)
            shutil.copy(SCRIPT, copy)
            spec = importlib.util.spec_from_file_location("arc_cost_worktree", copy)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.DEFAULT_REPO, main.resolve().parent / "agent-diff-review")


if __name__ == "__main__":
    unittest.main()
