"""Durable dispatch observations, delivery attempts, and parent consumption."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

VERSION = 1
DEADLINE = 30


def cli_command(args, bash=None):
    """Run configured shell scripts through the launcher's Bash executable."""
    bash = os.environ.get('DISPATCH_CLI_BASH') if bash is None else bash
    if bash and os.name == 'nt':
        try:
            with open(args[0], 'rb') as stream:
                header = stream.readline(256).decode('ascii', errors='replace').strip()
            if header.startswith('#!') and Path(header.split()[-1]).name in ('bash', 'sh'):
                return [bash, Path(args[0]).resolve().as_posix(), *args[1:]]
        except OSError:
            pass
    return args


def publish(path, value):
    """Replace one file atomically on its own filesystem."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def process_lock(path, timeout=5):
    """An OS lock releases when its process exits, including abrupt termination."""
    with open(path, 'a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('dispatch receipt is busy')
                time.sleep(0.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Receipt:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @classmethod
    def create(cls, origin, runner, name, requested_log, requested_result, codex_bin):
        parent = Path(requested_log).resolve().parent
        root = Path(tempfile.mkdtemp(prefix='dispatch-receipt-', dir=parent))
        receipt = cls(root)
        receipt.save({'version': VERSION, 'launch': str(uuid.uuid4()), 'origin': origin,
                      'route': 'queue', 'runner': runner, 'name': name, 'session': None,
                      'requested_log': str(requested_log), 'requested_result': str(requested_result),
                      'log': str(root / 'worker.log'), 'report': str(root / 'report.md'),
                      'codex_bin': codex_bin, 'cli_bash': os.environ.get('DISPATCH_CLI_BASH'), 'events': []})
        # Check coordination support before any worker starts.
        with receipt.lock():
            pass
        with receipt.monitor(timeout=0):
            pass
        return receipt

    def lock(self):
        return process_lock(self.root / 'state.lock')

    def monitor(self, timeout=0):
        return process_lock(self.root / 'monitor.lock', timeout)

    def read(self):
        data = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))
        if data.get('version') != VERSION:
            raise ValueError('unsupported dispatch receipt version')
        return data

    def save(self, data):
        publish(self.root / 'state.json', json.dumps(data, ensure_ascii=True, indent=2).encode())

    def update(self, **values):
        with self.lock():
            data = self.read()
            data.update(values)
            self.save(data)

    def observe(self, kind, status, *, report_format='text', detail=None):
        """Freeze owned bytes and reconcile an unchanged observation with its event."""
        with self.lock():
            return self._observe_locked(self.read(), kind, status, report_format, detail)

    def _observe_locked(self, data, kind, status, report_format, detail=None):
        report = Path(data['report'])
        content = b''
        availability = 'missing'
        error = None
        try:
            content = report.read_bytes()
            availability = 'available' if content.strip() else 'empty'
            if report_format == 'claude-json' and content.strip():
                value = json.loads(content.decode('utf-8'))
                if not isinstance(value, dict) or not isinstance(value.get('result'), str) or value.get('is_error'):
                    availability = 'malformed'
                elif not value['result'].strip():
                    availability = 'empty'
        except FileNotFoundError:
            pass
        except (OSError, ValueError, UnicodeError) as exc:
            availability = 'error' if isinstance(exc, OSError) else 'malformed'
            error = str(exc)
        digest = hashlib.sha256(content).hexdigest()
        identity = [kind, status, availability, digest]
        if data['events'] and data['events'][-1]['identity'] == identity:
            return data['events'][-1]
        event_id = str(uuid.uuid4())
        frozen = self.root / (event_id + '.result')
        publish(frozen, content)
        diagnostic = self.root / (event_id + '.log')
        try:
            publish(diagnostic, Path(data['log']).read_bytes())
        except OSError as exc:
            error = error or str(exc)
        event = {'id': event_id, 'identity': identity, 'kind': kind, 'status': status,
                 'availability': availability, 'digest': digest, 'result': str(frozen),
                 'diagnostic': str(diagnostic), 'error': error, 'delivery': 'pending',
                 'attempts': [], 'consumption': 'unread', 'claim': None, 'outcome': None,
                 'detail': detail}
        data['events'].append(event)
        self.save(data)
        return event

    @staticmethod
    def event(data, event_id):
        for event in data['events']:
            if event['id'] == event_id:
                return event
        raise ValueError('unknown dispatch event')

    def authority(self, data, origin, launch):
        if data['origin'] != origin or data['launch'] != launch:
            raise ValueError('dispatch event belongs to another origin or launch')

    def claim(self, origin, launch, event_id, owner):
        if not owner:
            raise ValueError('a processing owner is required')
        with self.lock():
            data = self.read()
            self.authority(data, origin, launch)
            event = self.event(data, event_id)
            if event['consumption'] == 'consumed':
                return {'consumption': 'consumed', 'outcome': event['outcome']}
            if event['claim']:
                if event['claim']['owner'] != owner:
                    raise ValueError('processing outcome unknown: check durable effects before taking over')
                return dict(event['claim'], consumption='processing_unknown')
            event['claim'] = {'owner': owner, 'token': str(uuid.uuid4())}
            event['consumption'] = 'claimed'
            self.save(data)
            return dict(event['claim'], consumption='claimed')

    def acknowledge(self, origin, launch, event_id, owner, token, outcome):
        with self.lock():
            data = self.read()
            self.authority(data, origin, launch)
            event = self.event(data, event_id)
            if event['claim'] != {'owner': owner, 'token': token}:
                raise ValueError('acknowledgment requires the exact processing claim')
            if event['consumption'] == 'consumed':
                if event['outcome'] != outcome:
                    raise ValueError('consumed outcome cannot be changed')
                return event['outcome']
            event['consumption'] = 'consumed'
            event['outcome'] = outcome
            self.save(data)
            return outcome

    def send(self, event_id, *, resend_unknown=False, run=None):
        with self.lock():
            data = self.read()
            event = self.event(data, event_id)
            if event['consumption'] == 'consumed':
                return 0
            if event['delivery'] == 'accepted':
                return 0
            if event['delivery'] == 'submitting' or (event['delivery'] == 'unknown' and not resend_unknown):
                return 1
            attempt = {'id': str(uuid.uuid4()), 'state': 'submitting'}
            event['attempts'].append(attempt)
            event['delivery'] = 'submitting'
            self.save(data)
            message = (f"Fresh-session handoff {data['name']} {event['kind']} (exit {event['status'] if event['status'] is not None else 'unknown'}). "
                       f"Result: {event['result']}. Receipt: {self.root}. Origin: {data['origin']}. "
                       f"Launch: {data['launch']}. Event: {event_id}. Report: {event['availability']}. "
                       f"Claim before processing with receipt.py. Acknowledge after durable incorporation. "
                       f"Observation: {event.get('detail') or event['kind']}.")
            args = cli_command([data['codex_bin'], 'queue', '--thread', data['origin'], '--message', message], data.get('cli_bash') or '')
        # Never hold the state lock across the queue process.
        state, detail = 'unknown', None
        spawned = False
        log = self.root / (attempt['id'] + '.queue.log')
        try:
            with log.open('wb') as stream:
                try:
                    if run is None:
                        # Only Popen failure establishes that no queue process existed.
                        try:
                            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT)
                            spawned = True
                        except OSError as exc:
                            state, detail = 'not_submitted', str(exc)
                        else:
                            try:
                                code = proc.wait(timeout=DEADLINE)
                                state = 'accepted' if code == 0 else 'unknown'
                                detail = f'codex queue exited {code}. Acceptance {state}.'
                            except (OSError, subprocess.TimeoutExpired) as exc:
                                detail = str(exc)
                                proc.kill()
                                try:
                                    proc.wait(timeout=DEADLINE)
                                except (OSError, subprocess.TimeoutExpired):
                                    pass
                    else:
                        proc = run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=DEADLINE, check=False)
                        state = 'accepted' if proc.returncode == 0 else 'unknown'
                        detail = f'codex queue exited {proc.returncode}. Acceptance {state}.'
                except OSError as exc:
                    state, detail = ('unknown' if spawned else 'not_submitted'), str(exc)
                except subprocess.TimeoutExpired as exc:
                    detail = str(exc)
        except OSError as exc:
            state, detail = ('unknown' if spawned else 'not_submitted'), str(exc)
        with self.lock():
            data = self.read()
            event = self.event(data, event_id)
            current = next(item for item in event['attempts'] if item['id'] == attempt['id'])
            current.update(state=state, detail=detail, log=str(log))
            event['delivery'] = state
            self.save(data)
        if state != 'accepted':
            print(detail, file=sys.stderr)
        return 0 if state == 'accepted' else 1

    def deliver(self, event_id):
        for _ in range(3):
            status = self.send(event_id)
            event = self.event(self.read(), event_id)
            if status == 0 or event['delivery'] != 'not_submitted':
                return status
        return 1

    def refresh(self):
        with self.lock():
            data = self.read()
            if not data['events']:
                raise ValueError('no observation exists: recover monitoring first')
            latest = data['events'][-1]
            return self._observe_locked(data, latest['kind'], latest['status'], data.get('report_format', 'text'))

    def recover_delivery(self, event_id=None, resend_unknown=False):
        with self.monitor():
            event = self.refresh()
            if event_id and event_id != event['id']:
                raise ValueError('recovery event differs from the current observation')
            with self.lock():
                data = self.read()
                current = self.event(data, event['id'])
                if current['delivery'] == 'submitting':
                    current['delivery'] = 'unknown'
                    current['attempts'][-1]['state'] = 'unknown'
                    current['attempts'][-1]['detail'] = 'submission interrupted: acceptance unknown'
                    self.save(data)
            return self.send(event['id'], resend_unknown=resend_unknown)

    def recover_monitor(self):
        # Acquire before reading recovery state, and retain for the replacement lifetime.
        with self.monitor():
            data = self.read()
            latest = data['events'][-1] if data['events'] else None
            if data['runner'] == 'claude-writable' and data['session'] and (latest is None or latest['kind'] in ('monitoring-error', 'attention')):
                import watch_claude
                watch_claude.run_watcher(data['claude_bin'], data['codex_bin'], data['session'],
                                        data['name'], data['origin'], data['report'],
                                        f"claude attach {data['session']}", receipt=self, monitor_owned=True)
                return self.read()['events'][-1]
            if latest:
                return self.refresh()
            event = self.observe('unverified', None, report_format=data.get('report_format', 'text'))
            return event


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect and recover a dispatch receipt.')
    parser.add_argument('receipt')
    parser.add_argument('action', choices=['status', 'read', 'refresh', 'claim', 'ack', 'recover', 'monitor'])
    parser.add_argument('--event')
    parser.add_argument('--origin')
    parser.add_argument('--launch')
    parser.add_argument('--owner')
    parser.add_argument('--token')
    parser.add_argument('--outcome')
    parser.add_argument('--resend-unknown', action='store_true', help='resend uncertain delivery, risking a duplicate callback')
    args = parser.parse_args(argv)
    receipt = Receipt(args.receipt)
    try:
        if args.action == 'status':
            if receipt.read()['events']:
                receipt.refresh()
            result = receipt.read()
        elif args.action == 'refresh':
            result = receipt.refresh()
        elif args.action == 'monitor':
            result = receipt.recover_monitor()
        elif args.action == 'read':
            event = receipt.event(receipt.read(), args.event)
            sys.stdout.buffer.write(Path(event['result']).read_bytes())
            return 0
        elif args.action == 'claim':
            result = receipt.claim(args.origin, args.launch, args.event, args.owner)
        elif args.action == 'ack':
            if args.outcome is None:
                raise ValueError('acknowledgment requires a durable processing outcome')
            result = receipt.acknowledge(args.origin, args.launch, args.event, args.owner, args.token, args.outcome)
        else:
            return receipt.recover_delivery(args.event, args.resend_unknown)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
