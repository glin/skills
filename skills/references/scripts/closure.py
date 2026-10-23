#!/usr/bin/env python3
"""Read-only inventory and disposition checks for explicitly adopted owned outputs."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import lzma
import ntpath
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import tarfile
import zipfile

LIMIT = 1024 * 1024
STATES = {'Planned', 'Active', 'Removed', 'Retained'}
CORE = {'index', 'index.lock', 'HEAD', 'config', 'description', 'objects', 'refs', 'logs',
        'hooks', 'info', 'worktrees', 'modules', 'packed-refs', 'commondir', 'gitdir',
        'shallow', 'FETCH_HEAD', 'ORIG_HEAD', 'MERGE_HEAD', 'rebase-apply', 'rebase-merge'}
CORE.update({'config.worktree', 'COMMIT_EDITMSG', 'MERGE_MSG', 'SQUASH_MSG', 'AUTO_MERGE',
             'REBASE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG', 'BISECT_START',
             'BISECT_NAMES', 'BISECT_TERMS', 'BISECT_EXPECTED_REV', 'sequencer', 'rr-cache',
             'gc.log', 'gc.pid', 'maintenance'})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def path_key(value, platform=None):
    """Compare Windows paths without changing POSIX case semantics."""
    platform = platform or sys.platform
    return ntpath.normcase(ntpath.normpath(value)) if platform == 'win32' else posixpath.normpath(value)


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_reparse_tag', 0) & 0x20000000)


def plain_path(path, missing=False):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f'An absolute owned path is required: {path}')
    for part in [*reversed(path.parents), path]:
        try:
            if linked(part.lstat()):
                raise ValueError(f'Owned paths cannot cross a directory link: {part}')
        except FileNotFoundError:
            if not missing:
                raise
    return Path(os.path.normpath(path))


def identity(path):
    info = path.lstat()
    if linked(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f'An ordinary file is required: {path}')
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    after = path.lstat()
    if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f'File changed during inventory: {path}')
    return {'path': str(path), 'size': info.st_size, 'sha256': sha.hexdigest()}


def scratch_safe(path):
    parts = path.parts
    indices = [i for i, part in enumerate(parts) if git_name(part)]
    if indices:
        tail = parts[indices[-1] + 1:]
        if len(tail) >= 3 and tail[0] == 'worktrees' and tail[2].startswith('plan-commit-'):
            tail = tail[2:]
        if not tail or tail[0].lower().removesuffix('.lock') in {p.lower() for p in CORE} or tail[0].lower().startswith(('recover', 'sharedindex.')) or (len(tail) == 1 and tail[0].lower() in {'transactions', 'tmp', 'temp'}):
            raise ValueError(f'Git core metadata is outside scratch ownership: {path}')


def git_name(name):
    return (name.lower() if sys.platform in {'win32', 'darwin'} else name) == '.git'


def inventory(roots, scratch, boundaries=(), missing_scratch=()):
    """Count ignored regular files. Do not enter links or nested Git checkouts."""
    files = {}
    excluded = []
    for base, registered in [(p, False) for p in roots] + [(p, True) for p in scratch]:
        base = plain_path(base, missing=True)
        if registered:
            scratch_safe(base)
        elif any(git_name(p) for p in base.parts):
            raise ValueError(f'Use an exact scratch registration inside .git: {base}')
        if not base.exists():
            if registered and path_key(str(base)) in missing_scratch:
                continue
            raise ValueError(f'Owned inventory root is missing: {base}')
        pending = [base]
        while pending:
            path = pending.pop()
            if path != base and path_key(str(path)) in boundaries:
                continue
            info = path.lstat()
            if linked(info):
                excluded.append(str(path))
                continue
            if stat.S_ISDIR(info.st_mode):
                if path != base and any(git_name(child.name) for child in path.iterdir()):
                    excluded.append(str(path))
                    continue
                for child in sorted(path.iterdir()):
                    if git_name(child.name):
                        excluded.append(str(child))
                    else:
                        pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                key = path_key(str(path))
                if key in files:
                    raise ValueError(f'Owned inventory scopes overlap: {path}')
                files[key] = identity(path)
            else:
                raise ValueError(f'Unsupported owned output: {path}')
    return files, excluded


def bounded_json(path):
    with plain_path(path).open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) >= LIMIT:
        raise ValueError(f'Control JSON must be below 1 MiB: {path}')
    return json.loads(raw), digest(raw)


def members(resource):
    value = resource.get('members')
    if isinstance(value, dict):
        path = plain_path(value['inventory'])
        if identity(path)['sha256'] != value['sha256']:
            raise ValueError(f'Member inventory digest differs: {path}')
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            value = json.load(stream)
        if len(value) != resource['members']['count']:
            raise ValueError(f'Member inventory count differs: {path}')
    if value is None:
        value = [{'path': '', 'size': resource['size'], 'sha256': resource['sha256']}]
    if not isinstance(value, list) or not value:
        raise ValueError(f'A complete nonempty member inventory is required: {resource["path"]}')
    result = {}
    for member in value:
        name = member['path']
        if name and (name.startswith('/') or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')) or ':' in name):
            raise ValueError(f'Unsafe member path: {name}')
        if not isinstance(member['size'], int) or member['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', member['sha256']):
            raise ValueError(f'Invalid member identity: {name}')
        target = Path(resource['path']) / name if name else Path(resource['path'])
        key = path_key(str(target))
        if key in result:
            raise ValueError(f'Duplicate member: {target}')
        result[key] = {'path': str(target), 'size': member['size'], 'sha256': member['sha256'], 'member': name}
    return result


def archive_members(path):
    result = {}
    def add(name, stream):
        if name in result:
            raise ValueError(f'Duplicate archive member: {name}')
        sha, size = hashlib.sha256(), 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
            size += len(chunk)
        result[name] = (size, sha.hexdigest())
    with ExitStack() as contexts:
        source = contexts.enter_context(open(path, 'rb'))
        if source.read(6) == b'\xfd7zXZ\x00':
            source.seek(0)
            source = contexts.enter_context(lzma.LZMAFile(source))
            is_zip = source.read(4) in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')
        else:
            is_zip = zipfile.is_zipfile(source)
        source.seek(0)
        if is_zip:
            with zipfile.ZipFile(source) as archive:
                for item in archive.infolist():
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise ValueError(f'Archive contains a link: {item.filename}')
                    if not item.is_dir():
                        with archive.open(item) as stream:
                            add(item.filename, stream)
        else:
            with tarfile.open(fileobj=source, mode='r:*') as archive:
                for item in archive:
                    if item.isfile():
                        with archive.extractfile(item) as stream:
                            add(item.name, stream)
                    elif not item.isdir():
                        raise ValueError(f'Archive contains a link or special member: {item.name}')
    return result


def specific(resource, field):
    text = resource.get(field)
    if not isinstance(text, str) or len(text.strip()) < 5 or text.strip().lower() in {'old evidence', 'evidence', 'later', 'unknown', 'pending', 'retain', 'none yet'}:
        raise ValueError(f'{resource["path"]}: a specific {field} is required')


def scopes(manifest, owner=None):
    roots, scratch = [], []
    all_paths = []
    for field, target in [('roots', roots), ('scratch', scratch)]:
        for item in manifest.get(field, []):
            path = plain_path(item['path'], missing=True)
            for previous, previous_owner in all_paths:
                if field == 'scratch' and path.is_relative_to(previous) and any(git_name(p) for p in path.relative_to(previous).parts):
                    continue
                if path == previous or (item['owner'] == previous_owner and (path.is_relative_to(previous) or previous.is_relative_to(path))):
                    raise ValueError(f'Ownership scopes overlap: {path}')
            all_paths.append((path, item['owner']))
            if owner is None or item['owner'] == owner:
                target.append(path)
    if not all_paths or (owner is not None and not roots and not scratch):
        raise ValueError('An explicit owned inventory scope is required')
    return roots, scratch


def check(manifest, owner=None, complete=True):
    if not isinstance(manifest, dict) or manifest.get('version') != 1 or manifest.get('adopted') is not True:
        raise ValueError('Explicit disposition-manifest adoption is required. Legacy resource rows remain authoritative.')
    roots, scratch = scopes(manifest, owner)
    resources = manifest['resources']
    boundaries = {path_key(item['path']) for item in [*manifest.get('roots', []), *manifest.get('scratch', [])]}
    missing_scratch = set()
    for item in manifest.get('scratch', []):
        path = plain_path(item['path'], missing=True)
        if path in scratch and any(r['status'] == 'Removed' and r['owner'] == item['owner'] and (Path(r['path']) == path or Path(r['path']).is_relative_to(path)) for r in resources):
            missing_scratch.add(path_key(str(path)))
    actual, excluded = inventory(roots, scratch, boundaries, missing_scratch)
    by_id = {r['id']: r for r in resources}
    if len(by_id) != len(resources):
        raise ValueError('Resource IDs must be unique')
    expected = {}
    totals = {state: 0 for state in STATES}
    errors = []
    exemptions = {path_key(item['path']) for item in manifest.get('exclusions', []) if isinstance(item.get('owner'), str) and item['owner'].strip() and isinstance(item.get('evidence'), str) and len(item['evidence'].strip()) >= 5}
    errors.extend(f'{path}: excluded output requires explicit settlement' for path in excluded if not git_name(Path(path).name) and path_key(path) not in exemptions)
    archives = {}
    for resource in resources:
        if owner is not None and resource['owner'] != owner:
            continue
        path = plain_path(resource['path'], missing=True)
        containing = [item for item in [*manifest.get('roots', []), *manifest.get('scratch', [])] if path == plain_path(item['path'], missing=True) or path.is_relative_to(plain_path(item['path'], missing=True))]
        if not containing or max(containing, key=lambda item: len(Path(item['path']).parts))['owner'] != resource['owner']:
            raise ValueError(f'Resource owner differs from its inventory scope: {path}')
        state = resource['status']
        if state not in STATES:
            raise ValueError(f'Unknown disposition: {path}')
        if state in {'Active', 'Planned'}:
            if complete:
                errors.append(f'{path}: {state} blocks completion')
            declared = members(resource) if 'members' in resource or 'size' in resource else {key: value for key, value in actual.items() if Path(value['path']) == path or Path(value['path']).is_relative_to(path)}
        else:
            declared = members(resource)
        if state == 'Retained':
            for field in ('consumer', 'evidence', 'next_owner', 'release_condition'):
                specific(resource, field)
        elif state == 'Removed':
            if resource.get('pending_readers'):
                errors.append(f'{path}: pending readers block removal')
            if path.exists():
                errors.append(f'{path}: Removed output still exists')
            if 'archive' in resource:
                archive = by_id.get(resource['archive']['resource'])
                if not archive or archive['status'] != 'Retained':
                    raise ValueError(f'{path}: archive requires a separate Retained resource')
                if archive['owner'] != resource['owner']:
                    raise ValueError(f'{path}: archive belongs to another owner')
                archive_path = plain_path(archive['path'])
                key = path_key(str(archive_path))
                stamp = archive_path.stat()
                stamp = (stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
                if key not in archives:
                    observed = identity(archive_path)
                    packed = archive_members(archive_path)
                    after = archive_path.stat()
                    if stamp != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError(f'Archive changed during verification: {archive_path}')
                    archives[key] = (stamp, observed, packed)
                cached_stamp, observed, packed = archives[key]
                if stamp != cached_stamp:
                    raise ValueError(f'Archive changed during reuse: {archive_path}')
                if (observed['size'], observed['sha256']) != (archive['size'], archive['sha256']):
                    errors.append(f'{archive["path"]}: archive identity differs')
                prefix = resource['archive'].get('prefix', '')
                for member in declared.values():
                    name = prefix + member['member']
                    if packed.get(name) != (member['size'], member['sha256']):
                        errors.append(f'{member["path"]}: archive member is missing or changed at {archive["path"]}:{name}')
            else:
                specific(resource, 'evidence')
                if resource.get('removal') != 'deleted':
                    raise ValueError(f'{path}: removal requires deletion evidence or a verified archive')
        for key, member in declared.items():
            if key in expected:
                raise ValueError(f'Dispositions overlap: {member["path"]}')
            expected[key] = state
            totals[state] += member['size']
            if state == 'Retained':
                observed = actual.get(key)
                if observed is None:
                    errors.append(f'{member["path"]}: retained output is missing or excluded')
                elif (observed['size'], observed['sha256']) != (member['size'], member['sha256']):
                    errors.append(f'{member["path"]}: retained identity differs')
    for key, observed in actual.items():
        if key not in expected:
            errors.append(f'{observed["path"]}: owned output has no disposition')
    for key, (before, observed, _) in archives.items():
        after = Path(observed['path']).stat()
        if before != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f'Archive changed after verification: {observed["path"]}')
    return {'ok': not errors, 'files': len(actual), 'bytes': sum(f['size'] for f in actual.values()),
            'bytes_by_disposition': totals, 'excluded': excluded, 'errors': errors}


def view(manifest, manifest_digest):
    name = manifest['view_id']
    if not re.fullmatch('[A-Za-z0-9_-]+', name):
        raise ValueError('view_id must use letters, digits, underscores or hyphens')
    lines = [f'<!-- dispositions:{name} sha256:{manifest_digest} -->',
             '| Type | Path or identifier | Purpose | Owner | Cleanup condition | Status |',
             '| --- | --- | --- | --- | --- | --- |']
    for r in manifest['resources']:
        cells = ['Artifact', f'`{r["path"]}`', r.get('consumer', r.get('evidence', r['id'])), r['owner'], r.get('release_condition', 'Settled'), r['status']]
        if any('|' in str(cell) or '\n' in str(cell) or '\r' in str(cell) for cell in cells):
            raise ValueError('Runtime resource cells cannot contain pipes or newlines')
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines.append(f'<!-- /dispositions:{name} -->')
    return '\n'.join(lines)


def migrate(plan, manifest, manifest_digest, owned_rows):
    generated = view(manifest, manifest_digest)
    marker = f'<!-- dispositions:{manifest["view_id"]} '
    if marker in plan:
        raise ValueError('The plan already has this adopted view')
    resources = represented_paths(manifest)
    selected = {path_key(p) for p in owned_rows}
    if not selected.issubset(resources):
        raise ValueError('Migration rows require explicit manifest ownership')
    lines = plan.splitlines()
    if '## Runtime resources' not in lines:
        raise ValueError('A Runtime resources section is required')
    start = lines.index('## Runtime resources') + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('## ')), len(lines))
    found = set()
    kept = []
    for line in lines[start:end]:
        row = row_paths(line)
        matches = row & selected
        if matches:
            if row - selected:
                raise ValueError('A mixed legacy row requires explicit adoption of every represented resource')
            found.update(matches)
        elif line.strip() != 'None yet.':
            kept.append(line)
    if found != selected:
        raise ValueError('Every selected legacy row must exist exactly in Runtime resources')
    return '\n'.join(lines[:start] + kept + ['', generated, ''] + lines[end:]) + '\n'


def row_paths(line):
    return {path_key(p) for p in re.findall(r'`([^`]+)`', line)} if line.startswith('|') else set()


def represented_paths(manifest):
    result = {path_key(r['path']) for r in manifest['resources']}
    for resource in manifest['resources']:
        if 'members' in resource or 'size' in resource:
            result.update(members(resource))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['inventory', 'check', 'view', 'migrate'])
    parser.add_argument('--manifest', required=True)
    location = parser.add_mutually_exclusive_group()
    location.add_argument('--plan')
    location.add_argument('--resource-view', help='Standalone authoritative generated view for a run without a plan')
    parser.add_argument('--owner')
    parser.add_argument('--owned-row', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        manifest, sha = bounded_json(Path(args.manifest))
        if not isinstance(manifest, dict):
            raise ValueError('The disposition manifest must be an object')
        if args.operation == 'inventory':
            roots, scratch = scopes(manifest, args.owner)
            files, excluded = inventory(roots, scratch)
            output = {'files': list(files.values()), 'bytes': sum(f['size'] for f in files.values()), 'excluded': excluded}
        elif args.operation == 'view':
            print(view(manifest, sha))
            return 0
        elif args.operation == 'migrate':
            if manifest.get('adopted') is not True:
                raise ValueError('Migration requires explicit adopted: true')
            print(migrate(Path(args.plan).read_text(encoding='utf-8'), manifest, sha, args.owned_row), end='')
            return 0
        else:
            output = check(manifest, args.owner)
            source = args.plan or args.resource_view
            if not source:
                raise ValueError('The authoritative Runtime resources view requires --plan or --resource-view')
            plan = Path(source).read_text(encoding='utf-8')
            generated = view(manifest, sha)
            section = plan if args.resource_view else (plan.split('## Runtime resources\n', 1)[1].split('\n## ', 1)[0] if '## Runtime resources\n' in plan else '')
            manual = section.replace(generated, '')
            represented = represented_paths(manifest)
            competing = any(row_paths(line) & represented for line in manual.splitlines())
            if section.count(generated) != 1 or plan.count(f'<!-- dispositions:{manifest["view_id"]} ') != 1 or competing:
                output['errors'].append(f'{source}: Runtime resources view or manifest digest differs')
                output['ok'] = False
        print(json.dumps(output, indent=2))
        return 0 if output.get('ok', True) else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError, lzma.LZMAError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f'closure: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
