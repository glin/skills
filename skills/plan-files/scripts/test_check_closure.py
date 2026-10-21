"""Adapter checks reuse the shared lifecycle contract and preserve legacy invocation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from test_large_files import make_link

SCRIPT = Path(__file__).with_name('check_closure.py')
spec = importlib.util.spec_from_file_location('check_closure', SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdapterTests(unittest.TestCase):
    def test_missing_shared_installation_is_a_named_prerequisite(self):
        with self.assertRaisesRegex(ValueError, 'Unmet prerequisite'):
            adapter.shared(Path(__file__).parent / 'absent-installation')

    def test_canonical_and_installed_junction_invocation_and_legacy_refusal(self):
        shared = Path(os.environ.get('CLOSURE_SHARED_ROOT', Path.home() / '.agents/skills'))
        self.assertTrue((shared / 'references/scripts/closure.py').is_file())
        scratch = Path.cwd() / '.tmp/phase3'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, ignore_cleanup_errors=True) as temporary:
            base = Path(temporary).resolve()
            installed = base / 'installed'
            installed.mkdir()
            link = installed / 'references'
            target = shared / 'references'
            make_link(str(link), str(target))
            try:
                for root in (shared, installed):
                    env = dict(os.environ, CLOSURE_SHARED_ROOT=str(root))
                    result = subprocess.run([sys.executable, str(SCRIPT), '--help'], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    folder = base / 'outputs'
                    folder.mkdir(exist_ok=True)
                    manifest = base / 'legacy.json'
                    manifest.write_text(json.dumps({'version': 1, 'adopted': False, 'roots': [{'path': str(folder), 'owner': 'child'}], 'resources': []}))
                    plan = base / 'plan.md'
                    plan.write_text('## Runtime resources\n\nNone yet.\n')
                    result = subprocess.run([sys.executable, str(SCRIPT.with_name('large-files.py')), '--manifest', str(manifest), '--plan', str(plan)], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('Legacy resource rows remain authoritative', result.stderr)
                    manifest.write_text(json.dumps({'version': 1, 'adopted': True, 'view_id': 'standalone', 'roots': [{'path': str(folder), 'owner': 'child'}], 'scratch': [], 'resources': []}))
                    generated = subprocess.run([sys.executable, str(SCRIPT), 'view', '--manifest', str(manifest)], env=env, capture_output=True, text=True)
                    self.assertEqual(generated.returncode, 0, generated.stderr)
                    view = base / 'resources.md'
                    view.write_text(generated.stdout, encoding='utf-8')
                    for caller, prefix in ((SCRIPT, ['check']), (SCRIPT.with_name('large-files.py'), [])):
                        result = subprocess.run([sys.executable, str(caller), *prefix, '--manifest', str(manifest), '--resource-view', str(view)], env=env, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                if os.name == 'nt':
                    os.rmdir(link)
                else:
                    link.unlink()


if __name__ == '__main__':
    unittest.main()
