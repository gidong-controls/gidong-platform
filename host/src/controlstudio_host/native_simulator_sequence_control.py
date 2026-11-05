"""One Host-owned simulator invocation: cancel capability and terminal evidence only."""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import tempfile
import time
import uuid
from pathlib import Path

MAX_PROGRESS_BYTES = 2 * 1024 * 1024
CONTROL_SCHEMA = 'controlstudio-simulator-write-sequence/v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _kernel():
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = [wintypes.HANDLE]; api.SetEvent.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]; api.CloseHandle.restype = wintypes.BOOL
    return api


def _reparse(path):
    return bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400) or path.is_symlink()


class SimulatorSequenceControl:
    def __init__(self, binding, steps, *, api=None):
        import psutil
        self.api = _kernel() if api is None else api
        started = time.monotonic()
        self.native_deadline = started + 90.0
        self.return_deadline = started + 105.0
        self.scope = uuid.uuid4().hex
        self.host = {'pid': os.getpid(), 'process_create_time': psutil.Process().create_time()}
        self.binding_digest = digest(binding)
        self.plan_digest = digest(steps)
        self.key = secrets.token_hex(32)
        self.event_name = 'Local\\ControlStudioSimSequence.' + self.scope
        ctypes.set_last_error(0)
        self.handle = self.api.CreateEventW(None, True, False, self.event_name)
        if not self.handle or ctypes.get_last_error() == 183:
            if self.handle: self.api.CloseHandle(self.handle)
            raise RuntimeError('Simulator sequence cancellation owner could not be created')
        try:
            self.root = Path(tempfile.mkdtemp(prefix='controlstudio-sim-sequence-' + self.scope + '-')).resolve()
            self.root_identity = self.root.stat().st_ino
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise
        self.owner_path, self.progress_path = self.root / 'owner.json', self.root / 'progress.json'
        self.revoked = False
        self.signal_failed = False
        self.terminal_verified = False
        self.control = {'schema': CONTROL_SCHEMA, 'scope': self.scope, 'host': self.host,
            'binding_digest': self.binding_digest, 'plan_digest': self.plan_digest, 'key': self.key,
            'event_name': self.event_name, 'owner_path': str(self.owner_path), 'progress_path': str(self.progress_path),
            'native_deadline': self.native_deadline, 'return_deadline': self.return_deadline}

    def revoke(self):
        """Closing the sole persistent handle also denies the next fresh OpenEvent."""
        if self.handle is None:
            return self.revoked and not self.signal_failed
        signalled = bool(self.api.SetEvent(self.handle))
        self.signal_failed = self.signal_failed or not signalled
        closed = bool(self.api.CloseHandle(self.handle))
        if closed: self.handle = None
        self.revoked = signalled or closed
        return self.revoked and not self.signal_failed

    def _read(self, path, maximum):
        if (path.parent.resolve() != self.root or _reparse(self.root) or _reparse(path)
                or self.root.stat().st_ino != self.root_identity):
            raise ValueError('Sequence evidence path changed')
        before = path.stat()
        if not 1 <= before.st_size <= maximum:
            raise ValueError('Sequence evidence size is invalid')
        with path.open('rb') as stream:
            raw = stream.read(maximum + 1)
        after = path.stat()
        if (len(raw) != before.st_size or len(raw) > maximum
                or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError('Sequence evidence changed during terminal read')
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {'payload', 'mac'}:
            raise ValueError('Sequence evidence envelope is invalid')
        payload = envelope['payload']
        expected = hmac.new(bytes.fromhex(self.key), canonical(payload), hashlib.sha256).hexdigest()
        if not isinstance(envelope['mac'], str) or not hmac.compare_digest(envelope['mac'], expected):
            raise ValueError('Sequence evidence authentication failed')
        if (not isinstance(payload, dict) or payload.get('schema') != CONTROL_SCHEMA
                or payload.get('scope') != self.scope or payload.get('host') != self.host
                or payload.get('binding_digest') != self.binding_digest or payload.get('plan_digest') != self.plan_digest):
            raise ValueError('Sequence evidence owner or plan changed')
        return payload

    @staticmethod
    def _alive(identity):
        import psutil
        try:
            process = psutil.Process(identity['pid'])
            return abs(process.create_time() - identity['process_create_time']) < 0.001 and process.is_running()
        except psutil.NoSuchProcess:
            return False

    def terminal_owner(self, *, wait_seconds=15.0):
        """No normal running reader: join first, then prove exact CLI identities retired."""
        owner = self._read(self.owner_path, 65536)
        birth, chain = owner.get('spawn_birth'), owner.get('cli_chain')
        if (not isinstance(birth, dict) or birth.get('role') != 'cli'
                or not isinstance(chain, list) or not 1 <= len(chain) <= 8
                or chain[-1] != {key: birth[key] for key in ('pid', 'process_create_time')}
                or len({item.get('pid') for item in chain}) != len(chain)):
            raise ValueError('Sequence CLI birth ownership is invalid')
        for item in chain:
            if (set(item) != {'pid', 'process_create_time'} or type(item['pid']) is not int or item['pid'] <= 0
                    or type(item['process_create_time']) not in (int, float)
                    or not math.isfinite(item['process_create_time']) or item['process_create_time'] <= 0):
                raise ValueError('Sequence CLI lifetime is invalid')
        stop_by = min(self.return_deadline, time.monotonic() + max(0.0, min(15.0, wait_seconds)))
        while any(self._alive(item) for item in chain):
            if time.monotonic() >= stop_by:
                raise RuntimeError('Sequence CLI retirement is unconfirmed')
            time.sleep(min(0.02, max(0.0, stop_by - time.monotonic())))
        return owner

    def terminal_progress(self):
        owner = self.terminal_owner()
        progress = self._read(self.progress_path, MAX_PROGRESS_BYTES)
        if (progress.get('owner_digest') != digest(owner)
                or type(progress.get('sequence')) is not int or not 0 <= progress['sequence'] <= 182):
            raise ValueError('Sequence progress is not bound to its exact CLI birth')
        result = dict(progress['result'])
        result.pop('_sequence_proof', None)
        result['_sequence_terminal_proven'] = progress.get('terminal') is True
        self.terminal_verified = True
        return result

    def terminal_result(self, raw):
        owner = self.terminal_owner()
        if not isinstance(raw, dict): raise ValueError('Sequence terminal receipt is invalid')
        body = {key: value for key, value in raw.items() if key != '_sequence_proof'}
        proof = raw.get('_sequence_proof')
        expected = {'owner_digest': digest(owner), 'result_digest': digest(body), 'scope': self.scope,
                    'binding_digest': self.binding_digest, 'plan_digest': self.plan_digest}
        if not isinstance(proof, dict) or set(proof) != {'payload', 'mac'} or proof['payload'] != expected:
            raise ValueError('Sequence terminal proof changed')
        mac = hmac.new(bytes.fromhex(self.key), canonical(expected), hashlib.sha256).hexdigest()
        if not isinstance(proof['mac'], str) or not hmac.compare_digest(proof['mac'], mac):
            raise ValueError('Sequence terminal proof authentication failed')
        body['_sequence_terminal_proven'] = True
        self.terminal_verified = True
        return body

    def close(self):
        revoked = self.revoke()
        if self.terminal_verified and revoked:
            # Resolve and verify this exact generated directory before recursive cleanup.
            resolved = self.root.resolve()
            if (resolved != self.root or not resolved.name.startswith('controlstudio-sim-sequence-' + self.scope + '-')
                    or _reparse(resolved) or resolved.stat().st_ino != self.root_identity):
                raise RuntimeError('Sequence evidence cleanup identity changed')
            shutil.rmtree(resolved)
        # Unconfirmed child/evidence remains private and cannot grant new input.
        return self.terminal_verified and revoked
