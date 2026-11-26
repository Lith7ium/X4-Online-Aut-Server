"""Forward the owned cockpit's directly observed vanilla gun contacts."""
import json
import os
import re
import stat
import threading
import time

from .company_combat_client import CombatExchangeCancelled, CompanyCombatClient
from .company_combat_frames import MAX_BATCH, MAX_COUNTER
from .company_limits import COMPANY_PILOT_LIMIT
from .load_observations import MAX_LOG


COMPANY_GUN_UI = 'src/authority/lua/company_gun_source.lua'
COMPANY_GUN_MD = 'src/authority/md/company_gun_source.xml'
_CONTACT = b'[X4LAN] AUTH_COMPANY_GUN '
_STATUS = b'[X4LAN] AUTH_COMPANY_GUN_STATUS '
_HEADER = rb'\[=ERROR=\]\s+[0-9.]+\s+'
_EPOCH = re.compile(r'[0-9a-f]{32}')
_ROW = re.compile(_HEADER + re.escape(_CONTACT)
                  + rb'([0-9a-f]{32})\|([1-9][0-9]{0,5})\|([1-9][0-9]{0,5})\|([1-9][0-9]{0,15})')
_STATUS_ROW = re.compile(_HEADER + re.escape(_STATUS) + rb'(.*)')
_BLOCK = 16 * 1024
# The provider serializes up to one full batch per pilot. Allow four seconds
# per native impact even when the other pilots' batches are already queued.
_ACK_TIMEOUT = COMPANY_PILOT_LIMIT * MAX_BATCH * 4 + 30
_ATTEMPTS = 3


def company_gun_config(full_registry, index):
    epoch = full_registry['epoch']
    bits = full_registry['operational_bits']
    if (type(epoch) is not str or not _EPOCH.fullmatch(epoch) or type(index) is not int
            or not 1 <= index <= len(bits) or bits[index - 1] != '1'):
        raise ValueError('Company combat needs an operational Company seat')
    return f',authority_company_gun={{epoch={json.dumps(epoch)},index={index}}}'


def _regular(info):
    return (stat.S_ISREG(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class CompanyCombatSender:
    """Tail one private native log in a dedicated worker bound to one admission."""

    def __init__(self, path, port, credential, *, epoch, index, log_identity):
        if (type(epoch) is not str or not _EPOCH.fullmatch(epoch) or type(index) is not int
                or not 1 <= index <= 200000 or type(credential) is not dict
                or credential.get('epoch') != epoch or credential.get('index') != index):
            raise ValueError('Company combat admission does not match the local seat')
        if (type(log_identity) is not tuple or len(log_identity) != 2
                or any(type(part) is not int or part < 0 for part in log_identity) or not log_identity[1]):
            raise ValueError('Company combat requires the verified native log identity')
        self._client = CompanyCombatClient(port, credential)
        self._path = path
        self._identity = log_identity
        self._epoch = epoch.encode('ascii')
        self._index = index
        self._stop = threading.Event()
        self._counts = dict(armed=False, observed=0, confirmed=0, rejected=0, unknown=0, retries=0,
                            coverage='throttled_direct_primary_gun_notifications')
        self._failure = None
        self._thread = threading.Thread(target=self._run, name='authority-company-combat', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        if self._thread.is_alive():
            raise RuntimeError('company_combat_sender_cleanup_failed')
        return dict(self._counts, failure=self._failure)

    def _run(self):
        try:
            before = self._path.lstat()
            if not _regular(before) or (before.st_dev, before.st_ino) != self._identity:
                raise ValueError('invalid Company combat source log')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(self._path, flags), 'rb', buffering=0) as stream:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    raise ValueError('Company combat source log changed')
                self._tail(stream, before)
        except (OSError, ValueError):
            self._failure = 'source_unavailable'
        finally:
            self._client.close()

    def _tail(self, stream, identity):
        carry = b''
        max_seen = 0
        armed = b'armed|' + self._epoch + b'|' + str(self._index).encode('ascii')
        while not self._stop.is_set():
            info = self._path.lstat()
            if (not _regular(info) or not os.path.samestat(info, identity)
                    or not max_seen <= info.st_size <= MAX_LOG):
                raise ValueError('Company combat source log changed')
            max_seen = info.st_size
            chunk = stream.read(_BLOCK)
            if not chunk:
                self._stop.wait(.05)
                continue
            *lines, carry = (carry + chunk).split(b'\n')
            if len(carry) > _BLOCK * 4:
                raise ValueError('Company combat source log line exceeds limit')
            impacts = []
            for line in lines:
                line = line.rstrip(b'\r')
                if _STATUS in line:
                    status = _STATUS_ROW.fullmatch(line)
                    if not status or status[1] != armed:
                        raise ValueError('Company gun observer unavailable')
                    self._counts['armed'] = True
                if _CONTACT not in line:
                    continue
                row = _ROW.fullmatch(line)
                if not row or not self._counts['armed']:
                    raise ValueError('invalid Company gun contact record')
                epoch, shooter, target, counter = row.groups()
                sequence = int(counter)
                target_index = int(target)
                if (epoch != self._epoch or int(shooter) != self._index or not 1 <= target_index <= 200000
                        or target_index == self._index or sequence != self._counts['observed'] + 1
                        or sequence > MAX_COUNTER):
                    raise ValueError('Company gun contact does not match the active sequence')
                self._counts['observed'] = sequence
                impacts.append({'id': sequence, 'target_index': target_index, 'amount': 26})
                if len(impacts) == MAX_BATCH:
                    if not self._send(impacts):
                        return
                    impacts = []
            if impacts and not self._send(impacts):
                return

    def _send(self, impacts):
        deadline = time.monotonic() + _ACK_TIMEOUT
        for attempt in range(_ATTEMPTS):
            if self._stop.is_set():
                return False
            if time.monotonic() >= deadline:
                break
            if attempt:
                self._counts['retries'] += 1
            try:
                receipts = self._client.exchange(impacts, deadline=deadline, cancelled=self._stop.is_set)
            except CombatExchangeCancelled:
                return False
            except OSError:
                if attempt + 1 < _ATTEMPTS and self._stop.wait(min(.1, max(0, deadline - time.monotonic()))):
                    return False
                continue
            except ValueError:
                self._failure = 'invalid_receipt'
                return False
            for receipt in receipts:
                self._counts[receipt['status']] += 1
            return True
        if not self._stop.is_set():
            self._failure = 'receipt_timeout' if time.monotonic() >= deadline else 'transport_unavailable'
        return False
