"""Opt-in, single-owner loopback bridge for the experimental X4 controller.

Blocking methods implement WorldCoordinator's synchronous engine contract over
asynchronous Lua receipts. Use only with a disposable, isolated profile and a
fenced X4 process. A timeout permanently poisons this adapter: it does not fence
an already dispatched X4 operation. The caller serializes every access. Checks
around I/O are observations, not atomic dispatch fences.
"""
import re
import socket
import time
import secrets
from pathlib import Path

from .marker_evidence import verify_saved_marker

_HEX = re.compile(r'[0-9a-f]{32}\Z')
_MAX_QUEUED_PACKETS = 4096


class LocalControlBridge:
    def __init__(self, *, enabled, sock, peer, lease, world_epoch, profile, store,
                 verify_checkpoint, timeout=130, marker_required=False,
                 marker_script='X4AuthorityCheckpointMarker', save_timeout=None):
        if enabled is not True:
            raise ValueError('local control bridge requires explicit opt-in')
        if (not _HEX.fullmatch(lease) or not _HEX.fullmatch(world_epoch)
                or sock.family != socket.AF_INET or sock.type != socket.SOCK_DGRAM
                or sock.getsockname()[0] != '127.0.0.1'
                or not isinstance(peer, tuple) or len(peer) != 2 or peer[0] != '127.0.0.1'
                or not 1 <= peer[1] <= 65535 or not 0 < timeout <= 600
                or not callable(verify_checkpoint)):
            raise ValueError('invalid local bridge configuration')
        if save_timeout is not None and (type(save_timeout) not in (int, float)
                                         or not 0 < save_timeout <= 600):
            raise ValueError('invalid native SAVE timeout')
        self._save_timeout = save_timeout
        if type(marker_required) is not bool:
            raise ValueError('invalid marker setting')
        if marker_script not in ('X4AuthorityCheckpointMarker', 'X4AuthorityPlayerFixtureMarker'):
            raise ValueError('unsupported marker script')
        self._marker_required = marker_required
        self._marker_script = marker_script
        self._pending_marker = None
        profile = Path(profile)
        if profile.is_symlink() or profile.name != '45841' or not profile.is_dir():
            raise ValueError('isolated 45841 profile required')
        save_dir = profile / 'save'
        if save_dir.is_symlink() or not save_dir.is_dir():
            raise ValueError('isolated save directory required')
        self._source = save_dir / 'save_009.xml.gz'
        if self._source.exists() or self._source.is_symlink():
            raise ValueError('owned save slot must initially be empty')
        self._sock, self._peer, self._lease, self._epoch = sock, peer, lease, world_epoch
        self._store, self._verify, self._timeout = store, verify_checkpoint, timeout
        self._next, self._pending_save, self._poisoned = 1, None, False

    def _exchange(self, command, expected, *, timeout=None):
        if self._poisoned:
            raise RuntimeError('bridge is fenced after an indeterminate operation')
        self._poisoned = True
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError('native control receipt timed out; engine state indeterminate')
            return seconds

        old_timeout = self._sock.gettimeout()
        try:
            # Save verification can leave periodic HELLOs filling the receive
            # queue. Drain only known pre-command traffic on this owner thread;
            # a stale response from the peer makes the state indeterminate.
            self._sock.setblocking(False)
            hello = f'HELLO|{self._lease}|{self._epoch}'.encode('ascii')
            for _ in range(_MAX_QUEUED_PACKETS):
                remaining()
                try: data, peer = self._sock.recvfrom(769)
                except BlockingIOError: break
                if peer == self._peer and data != hello:
                    raise RuntimeError('unexpected native control receipt before command')
            else:
                raise RuntimeError('native control receive queue did not drain')
            self._sock.settimeout(remaining())
            self._sock.sendto(command.encode('ascii'), self._peer)
            while True:
                self._sock.settimeout(remaining())
                data, peer = self._sock.recvfrom(769)
                remaining()
                if peer != self._peer:
                    continue
                if data == f'HELLO|{self._lease}|{self._epoch}'.encode('ascii'):
                    continue
                if data != expected.encode('ascii'):
                    raise RuntimeError('unexpected native control receipt')
                self._poisoned = False
                return
        finally:
            self._sock.settimeout(old_timeout)

    def _request(self, operation, status, *, marker=None):
        if self._pending_save is not None:
            raise RuntimeError('previous save has not been verified')
        number = self._next
        if number > 9007199254740991:
            raise ValueError('request IDs exhausted')
        self._next += 1
        prefix = f'{self._lease}|{self._epoch}|{number}'
        suffix = '' if marker is None else f'|{marker}'
        self._exchange(f'CMD|{prefix}|{operation}{suffix}', f'RECEIPT|{prefix}|{operation}|{status}',
                       timeout=self._save_timeout if operation == 'save' else None)
        return number

    def pause(self):
        self._request('pause', 'confirmed')

    def resume(self):
        self._request('resume', 'confirmed')

    def save(self):
        marker = secrets.token_hex(16) if self._marker_required else None
        number = self._request('save', 'native_event_only', marker=marker)
        self._pending_save = number
        self._pending_marker = marker
        return self._source

    @property
    def pending_marker(self):
        return self._pending_marker

    def checkpoint_published(self, checkpoint):
        number = self._pending_save
        if number is None:
            raise ValueError('no native save awaits verification')
        # Poison before validation: never authorize reuse after an uncertain copy.
        self._poisoned = True
        expected = f'X4 Authority {self._epoch} {number}'
        evidence = self._verify(self._store, checkpoint.checkpoint_id, expected)
        if (getattr(checkpoint, 'marker', None) != self._pending_marker
                or self._store.get(checkpoint.checkpoint_id) != checkpoint
                or evidence.checkpoint_id != checkpoint.checkpoint_id
                or evidence.sha256 != checkpoint.sha256 or evidence.size != checkpoint.size
                or evidence.save_name != expected):
            raise ValueError('published checkpoint identity mismatch')
        if self._pending_marker is not None:
            verify_saved_marker(checkpoint, self._pending_marker,
                                script_name=self._marker_script, cue_name='MarkerState')
        self._poisoned = False
        prefix = f'{self._lease}|{self._epoch}|{number}'
        self._exchange(f'VERIFY|{prefix}', f'VERIFIED|{prefix}')
        self._pending_save = None
        self._pending_marker = None
        return True
