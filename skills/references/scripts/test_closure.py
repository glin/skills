"""Lifecycle fixtures. Run with python3 -m unittest discover -s skills/references/scripts."""
import copy
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest import mock
import zipfile
from test_remove_worktree import make_link

SCRIPT = Path(os.environ.get('CLOSURE_TEST_SCRIPT', Path(__file__).with_name('closure.py')))
spec = importlib.util.spec_from_file_location('closure', SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class ClosureTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / '.claude/test-closure'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='closure-', dir=scratch, ignore_cleanup_errors=True)
        self.base = Path(temporary.name).resolve()

        def cleanup():
            temporary.cleanup()
            if self.base.exists():
                retained = []
                pending = [self.base]
                while pending:
                    path = pending.pop()
                    if c.linked(path.lstat()):
                        retained.append({'path': str(path), 'kind': 'link'})
                    elif path.is_dir():
                        retained.append({'path': str(path), 'kind': 'directory'})
                        pending.extend(path.iterdir())
                    else:
                        retained.append(c.identity(path))
                print(json.dumps({'test': self.id(), 'cleanup': 'retained', 'outputs': retained}), file=sys.stderr)

        self.addCleanup(cleanup)
        self.root = self.base / 'outputs'
        self.root.mkdir()
        self.m = {'version': 1, 'adopted': True, 'view_id': 'fixture',
                  'roots': [{'path': str(self.root), 'owner': 'child'}], 'scratch': [], 'resources': []}

    def write(self, name='report.md', data=b'canonical report'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def retained(self, path, ident='report'):
        resource = dict(c.identity(path), id=ident, owner='child', status='Retained',
                        consumer='Release acceptance reviewer', evidence='Canonical signed review report',
                        next_owner='Release coordinator', release_condition='Acceptance review finishes')
        self.m['resources'].append(resource)
        return resource

    def archived(self):
        source = self.root / 'logs'
        data = [('A.log', b'a' * 64), ('B.log', b'b' * 64)]
        members = [{'path': name, 'size': len(content), 'sha256': c.digest(content)} for name, content in data]
        archive = self.root / 'logs.zip'
        with zipfile.ZipFile(archive, 'w') as packed:
            for name, content in data:
                packed.writestr(name, content)
        self.retained(archive, 'archive')
        removed = {'id': 'logs', 'path': str(source), 'owner': 'child', 'status': 'Removed',
                   'members': members, 'archive': {'resource': 'archive'}}
        self.m['resources'].append(removed)
        return removed, archive

    def test_aggregate_subthreshold_ignored_outputs_require_dispositions(self):
        for i in range(24):
            self.write(f'ignored/log-{i}.txt', b'x' * 1024)
        result = c.check(self.m)
        self.assertFalse(result['ok'])
        self.assertEqual(result['files'], 24)
        self.assertEqual(result['bytes'], 24 * 1024)
        self.assertEqual(len(result['errors']), 24)
        paths = sorted((self.root / 'ignored').glob('*.txt'))
        archive = self.root / 'logs.zip'
        with zipfile.ZipFile(archive, 'w') as packed:
            for i, path in enumerate(paths):
                name = 'ignored/' + path.name
                packed.write(path, name)
                self.m['resources'].append(dict(c.identity(path), id=f'log-{i}', owner='child', status='Removed', archive={'resource': 'archive', 'prefix': name}))
        self.retained(archive, 'archive')
        for path in paths:
            path.unlink()
        self.assertTrue(c.check(self.m)['ok'])

    def test_archive_verified_sources_settle_terminal_review_with_open_findings(self):
        self.retained(self.write())
        removed, _ = self.archived()
        removed['review_verdict'] = 'REQUEST_CHANGES'
        result = c.check(self.m)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['bytes'], result['bytes_by_disposition']['Retained'])
        removed['pending_readers'] = ['Named supplemental reviewer']
        self.assertFalse(c.check(self.m)['ok'])

    def test_missing_archive_member_and_changed_archive_fail(self):
        _, archive = self.archived()
        with zipfile.ZipFile(archive, 'w') as packed:
            packed.writestr('A.log', b'a' * 64)
        self.assertFalse(c.check(self.m)['ok'])
        current = c.identity(archive)
        self.m['resources'][0].update(size=current['size'], sha256=current['sha256'])
        self.assertIn('B.log', ' '.join(c.check(self.m)['errors']))

    def test_tar_archives_and_archive_links(self):
        _, archive = self.archived()
        with tarfile.open(archive, 'w:gz') as packed:
            for name, data in [('A.log', b'a' * 64), ('B.log', b'b' * 64)]:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                packed.addfile(member, io.BytesIO(data))
        current = c.identity(archive)
        self.m['resources'][0].update(size=current['size'], sha256=current['sha256'])
        self.assertTrue(c.check(self.m)['ok'])
        with zipfile.ZipFile(archive, 'w') as packed:
            link = zipfile.ZipInfo('A.log')
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            packed.writestr(link, b'a' * 64)
            packed.writestr('B.log', b'b' * 64)
        current = c.identity(archive)
        self.m['resources'][0].update(size=current['size'], sha256=current['sha256'])
        with self.assertRaisesRegex(ValueError, 'link'):
            c.check(self.m)

    def test_retained_missing_changed_new_and_stale_removed_paths_fail(self):
        path = self.write()
        resource = self.retained(path)
        self.assertTrue(c.check(self.m)['ok'])
        path.write_bytes(b'changed')
        self.assertFalse(c.check(self.m)['ok'])
        path.unlink()
        self.assertFalse(c.check(self.m)['ok'])
        resource.update(status='Removed', removal='deleted', evidence='Disposable duplicate removed after publication')
        self.assertTrue(c.check(self.m)['ok'])
        path.write_bytes(b'canonical report')
        self.assertFalse(c.check(self.m)['ok'])
        self.write('new.txt')
        self.assertIn('new.txt', ' '.join(c.check(self.m)['errors']))

    def test_specific_retention_and_completion_states(self):
        resource = self.retained(self.write())
        for field in ('consumer', 'evidence', 'next_owner', 'release_condition'):
            saved = resource[field]
            resource[field] = 'old evidence'
            with self.subTest(field=field), self.assertRaises(ValueError):
                c.check(self.m)
            resource[field] = saved
        Path(resource['path']).unlink()
        for state in ('Planned', 'Active'):
            resource['status'] = state
            self.assertFalse(c.check(self.m)['ok'])

    def test_completed_child_is_independent_of_active_parent(self):
        self.retained(self.write())
        parent = self.base / 'parent'
        parent.mkdir()
        (parent / 'live.log').write_bytes(b'active parent')
        self.m['roots'].append({'path': str(parent), 'owner': 'parent'})
        self.m['resources'].append({'id': 'parent', 'owner': 'parent', 'path': str(parent / 'live.log'), 'status': 'Active'})
        self.assertTrue(c.check(self.m, owner='child')['ok'])
        self.assertFalse(c.check(self.m)['ok'])

    def test_git_core_foreign_transactions_and_nested_worktrees_are_excluded(self):
        git = self.root / '.git'
        owned = git / 'transactions/owned/index'
        for path in (owned, git / 'transactions/foreign/index', git / 'index', git / 'objects/blob', git / 'recovery/copy'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'index')
        self.m['scratch'].append({'path': str(git / 'transactions/owned'), 'owner': 'child'})
        self.retained(owned)
        self.assertEqual(c.check(self.m)['files'], 1)
        direct = git / 'plan-commit-owned/index'
        direct.parent.mkdir()
        direct.write_bytes(b'index')
        self.assertEqual(len(c.inventory([], [direct.parent])[0]), 1)
        for path in (git, git / 'index', git / 'objects/blob', git / 'recovery/copy', git / 'COMMIT_EDITMSG', git / 'HEAD.lock', git / 'config.worktree'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                c.inventory([], [path])
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / '.git').write_text('gitdir: other')
        (nested / 'foreign.txt').write_bytes(b'foreign')
        files, _ = c.inventory([self.root], [])
        self.assertEqual(files, {})

    def test_links_are_excluded_and_root_links_refuse(self):
        target = self.base / 'target'
        target.mkdir()
        (target / 'foreign.log').write_bytes(b'foreign')
        link = self.root / 'linked'
        make_link(str(link), str(target))
        try:
            self.assertEqual(c.inventory([self.root], [])[0], {})
            with self.assertRaises(ValueError):
                c.inventory([link], [])
        finally:
            if os.name == 'nt':
                os.rmdir(link)
            else:
                link.unlink()
        self.assertEqual((target / 'foreign.log').read_bytes(), b'foreign')

    def test_platform_path_comparison_is_independent(self):
        self.assertEqual(c.path_key('C:/Run/LOG.txt', 'win32'), c.path_key('c:/run/log.TXT', 'win32'))
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform):
                self.assertNotEqual(c.path_key('/Run/LOG.txt', platform), c.path_key('/run/log.TXT', platform))
        mixed = self.base / 'mixed'
        (mixed / '.GIT').mkdir(parents=True)
        (mixed / '.GIT/index').write_bytes(b'Git metadata on Windows')
        for platform, expected in [('win32', 0), ('darwin', 0), ('linux', 1)]:
            with self.subTest(platform=platform), mock.patch.object(c.sys, 'platform', platform):
                self.assertEqual(len(c.inventory([mixed], [])[0]), expected)

    def test_compressed_complete_member_inventory_is_digest_bound(self):
        resource = self.retained(self.write())
        detail = self.base / 'members.json.gz'
        data = [{'path': '', 'size': resource.pop('size'), 'sha256': resource.pop('sha256')}]
        with gzip.open(detail, 'wt', encoding='utf-8') as stream:
            json.dump(data, stream)
        resource['members'] = {'inventory': str(detail), 'sha256': c.identity(detail)['sha256'], 'count': 1}
        self.assertTrue(c.check(self.m)['ok'])
        resource['members']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            c.check(self.m)

    def test_manifest_bound_and_explicit_adoption(self):
        self.m['adopted'] = False
        with self.assertRaises(ValueError):
            c.check(self.m)
        path = self.base / 'oversized.json'
        path.write_bytes(b' ' * c.LIMIT)
        with self.assertRaises(ValueError):
            c.bounded_json(path)

    def test_owned_scope_and_duplicate_dispositions_refuse(self):
        resource = self.retained(self.write())
        self.m['resources'].append(dict(resource, id='duplicate'))
        with self.assertRaises(ValueError):
            c.check(self.m)
        self.m['resources'].pop()
        resource['owner'] = 'foreign'
        with self.assertRaises(ValueError):
            c.check(self.m)
        resource['owner'] = 'child'
        resource['path'] = str(self.base / 'foreign')
        with self.assertRaises(ValueError):
            c.check(self.m)

    def test_generated_view_migration_preserves_manual_rows_and_checks_drift(self):
        path = self.write()
        self.retained(path)
        manifest = self.base / 'dispositions.json'
        manifest.write_text(json.dumps(self.m), encoding='utf-8')
        sha = c.digest(manifest.read_bytes())
        legacy = '# Plan\n\n## Runtime resources\n\n| Type | Path or identifier | Purpose | Owner | Cleanup condition | Status |\n| --- | --- | --- | --- | --- | --- |\n| Job | `manual-job` | External job | Parent | Finish | Active |\n' + f'| File | `{path}` | Review | child | Finish | Active |\n'
        plan = self.base / 'plan.md'
        migrated = c.migrate(legacy, self.m, sha, [str(path)])
        self.assertIn('`manual-job`', migrated)
        plan.write_text(migrated, encoding='utf-8')
        command = ['check', '--manifest', str(manifest), '--plan', str(plan)]
        with mock.patch('sys.stdout'):
            self.assertEqual(c.main(command), 0)
            for changed in (migrated.replace('| Retained |', '| Planned |'), migrated.replace(sha, '0' * 64), migrated + f'| File | `{path}` | Review | child | Finish | Active |\n', migrated.replace('## Runtime resources', '## Wrong section')):
                plan.write_text(changed, encoding='utf-8')
                self.assertEqual(c.main(command), 1)
            plan.write_text(migrated, encoding='utf-8')
            self.m['resources'][0]['consumer'] = 'Another pending reviewer'
            manifest.write_text(json.dumps(self.m), encoding='utf-8')
            self.assertEqual(c.main(command), 1)
        with self.assertRaises(ValueError):
            c.migrate(legacy, self.m, sha, ['foreign-row'])

    def test_checker_does_not_mutate_inputs(self):
        self.retained(self.write())
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        c.check(self.m)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_mixed_legacy_row_preserves_foreign_authority(self):
        path = self.write()
        self.retained(path)
        legacy = f'## Runtime resources\n| File | `{path}` and `{self.base / "foreign.log"}` | Owner | Active |\n'
        with self.assertRaisesRegex(ValueError, 'mixed legacy row'):
            c.migrate(legacy, self.m, 'a' * 64, [str(path)])

    def test_equivalent_legacy_member_paths_cannot_compete(self):
        path = self.write('logs/live.log')
        resource = self.retained(path)
        resource['path'] = str(path.parent)
        resource['members'] = [{'path': path.name, 'size': resource.pop('size'), 'sha256': resource.pop('sha256')}]
        spelling = str(path).replace('\\', '/')
        if sys.platform == 'win32':
            spelling = spelling.upper()
        legacy = f'## Runtime resources\n| File | `{spelling}` | Child | Active |\n'
        migrated = c.migrate(legacy, self.m, 'a' * 64, [str(path)])
        self.assertNotIn(f'`{spelling}`', migrated)
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps(self.m), encoding='utf-8')
        plan = self.base / 'plan.md'
        plan.write_text('## Runtime resources\n' + c.view(self.m, c.digest(manifest.read_bytes())) + f'\n| File | `{spelling}` | Child | Active |\n', encoding='utf-8')
        with mock.patch('sys.stdout'):
            self.assertEqual(c.main(['check', '--manifest', str(manifest), '--plan', str(plan)]), 1)

    def test_split_index_metadata_is_never_scratch(self):
        subprocess.run(['git', '-C', str(self.root), 'init'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'update-index', '--split-index'], check=True, capture_output=True)
        git = self.root / '.git'
        backing = next(git.glob('sharedindex.*'))
        with self.assertRaisesRegex(ValueError, 'Git core metadata'):
            c.inventory([], [backing])
        for platform in ('win32', 'darwin', 'linux'):
            with self.subTest(platform=platform), mock.patch.object(c.sys, 'platform', platform):
                with self.assertRaises(ValueError):
                    c.scratch_safe(git / 'index')
                if platform != 'linux':
                    with self.assertRaises(ValueError):
                        c.scratch_safe(self.root / '.GIT/SHAREDINDEX.abc')

    def test_linked_worktree_transaction_excludes_metadata_siblings(self):
        repo = self.base / 'repo'
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()
        git('init')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        worktree = self.base / 'worktree'
        git('worktree', 'add', '-b', 'child', str(worktree))
        admin = Path(subprocess.check_output(['git', '-C', str(worktree), 'rev-parse', '--path-format=absolute', '--git-path', 'index']).decode().strip()).parent
        transaction = admin / 'plan-commit-fixture'
        transaction.mkdir()
        payload = transaction / 'index'
        payload.write_bytes(b'owned transaction')
        self.m['scratch'].append({'path': str(transaction), 'owner': 'child'})
        self.retained(payload)
        self.assertTrue(c.check(self.m)['ok'])
        self.assertEqual(c.check(self.m)['files'], 1)
        with self.assertRaises(ValueError):
            c.inventory([], [admin])

    def test_missing_run_root_and_evidenced_removed_scratch(self):
        self.root.rmdir()
        with self.assertRaisesRegex(ValueError, 'root is missing'):
            c.check(self.m)
        self.root.mkdir()
        missing = self.base / 'removed-transaction'
        self.m['scratch'].append({'path': str(missing), 'owner': 'child'})
        with self.assertRaises(ValueError):
            c.check(self.m)
        self.m['resources'].append({'id': 'scratch', 'path': str(missing), 'owner': 'child', 'status': 'Removed', 'size': 3, 'sha256': c.digest(b'old'), 'removal': 'deleted', 'evidence': 'Transaction removed after independent reconciliation'})
        self.assertTrue(c.check(self.m)['ok'])

    def test_disposition_totals_register_active_and_removed_members(self):
        removed, _ = self.archived()
        path = self.write('active.log', b'active')
        active = dict(c.identity(path), id='active', owner='child', status='Active')
        self.m['resources'].append(active)
        result = c.check(self.m)
        self.assertEqual(result['bytes_by_disposition']['Removed'], 128)
        self.assertEqual(result['bytes_by_disposition']['Active'], 6)
        self.assertEqual(len(result['errors']), 1, result)
        active['status'] = 'Planned'
        self.assertEqual(c.check(self.m)['bytes_by_disposition']['Planned'], 6)

    def test_archive_inventory_reused_and_changes_refused(self):
        removed, archive = self.archived()
        extra = dict(removed, id='extra', path=str(self.root / 'other-logs'))
        self.m['resources'].append(extra)
        with mock.patch.object(c, 'archive_members', wraps=c.archive_members) as read:
            self.assertTrue(c.check(self.m)['ok'])
            self.assertEqual(read.call_count, 1)
        original = c.members
        def change(resource):
            if resource['id'] == 'extra':
                archive.write_bytes(b'changed archive')
            return original(resource)
        with mock.patch.object(c, 'members', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'Archive changed'):
                c.check(self.m)

    def test_unknown_excluded_outputs_block_without_entering_them(self):
        nested = self.root / 'unknown'
        nested.mkdir()
        (nested / '.git').write_text('gitdir: outside')
        (nested / 'foreign.log').write_bytes(b'foreign')
        result = c.check(self.m)
        self.assertFalse(result['ok'])
        self.assertEqual(result['files'], 0)
        self.m['exclusions'] = [{'path': str(nested), 'owner': 'Foreign session', 'evidence': 'Registered foreign checkout retained by its owner'}]
        self.assertTrue(c.check(self.m)['ok'])
        target = self.base / 'foreign-target'
        target.mkdir()
        (target / 'data').write_bytes(b'foreign')
        link = self.root / 'linked'
        make_link(str(link), str(target))
        try:
            self.assertFalse(c.check(self.m)['ok'])
            self.m['exclusions'].append({'path': str(link), 'owner': 'Foreign session', 'evidence': 'Link retained for the active foreign session'})
            self.assertTrue(c.check(self.m)['ok'])
        finally:
            if os.name == 'nt':
                os.rmdir(link)
            else:
                link.unlink()
        self.assertEqual((target / 'data').read_bytes(), b'foreign')

    def test_nested_child_selection_never_inventories_parent(self):
        child = self.root / 'round-1'
        child.mkdir()
        path = self.write('round-1/report.md')
        self.retained(path)
        self.m['roots'][0]['owner'] = 'parent'
        self.m['roots'].append({'path': str(child), 'owner': 'child'})
        self.write('parent-live.log', b'active')
        original = c.identity
        def selected(path):
            self.assertTrue(path.is_relative_to(child), path)
            return original(path)
        with mock.patch.object(c, 'identity', side_effect=selected):
            self.assertTrue(c.check(self.m, owner='child')['ok'])
        self.assertFalse(c.check(self.m)['ok'])
        self.m['resources'][0]['owner'] = 'parent'
        with self.assertRaises(ValueError):
            c.check(self.m)

    def test_standalone_view_matches_manifest(self):
        self.retained(self.write())
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps(self.m), encoding='utf-8')
        view = self.base / 'resources.md'
        view.write_text(c.view(self.m, c.digest(manifest.read_bytes())), encoding='utf-8')
        command = ['check', '--manifest', str(manifest), '--resource-view', str(view)]
        with mock.patch('sys.stdout'):
            self.assertEqual(c.main(command), 0)

    def test_nonobject_manifests_produce_named_input_errors(self):
        manifest = self.base / 'manifest.json'
        command = ['check', '--manifest', str(manifest)]
        with mock.patch('sys.stdout'), mock.patch('sys.stderr') as error:
            for malformed in ([], None, 'manifest'):
                manifest.write_text(json.dumps(malformed))
                self.assertEqual(c.main(command), 2)
                self.assertIn('closure:', str(error.write.call_args_list))

    def test_truncated_compressed_archives_produce_named_input_errors(self):
        _, archive = self.archived()
        with tarfile.open(archive, 'w:gz') as packed:
            item = tarfile.TarInfo('A.log')
            item.size = 64
            packed.addfile(item, io.BytesIO(b'a' * 64))
        archive.write_bytes(archive.read_bytes()[:20])
        current = c.identity(archive)
        self.m['resources'][0].update(size=current['size'], sha256=current['sha256'])
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps(self.m))
        command = ['check', '--manifest', str(manifest)]
        with mock.patch('sys.stdout'), mock.patch('sys.stderr') as error:
            self.assertEqual(c.main(command), 2)
            self.assertIn('closure:', str(error.write.call_args_list))

    def test_canonical_and_installed_junction_cli(self):
        installed = self.base / 'installed'
        installed.mkdir()
        link = installed / 'references'
        target = Path(__file__).resolve().parent.parent
        make_link(str(link), str(target))
        try:
            for script in (SCRIPT, link / 'scripts/closure.py'):
                result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('migrate', result.stdout)
        finally:
            if os.name == 'nt':
                os.rmdir(link)
            else:
                link.unlink()


if __name__ == '__main__':
    unittest.main()
