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
                  + rb'([0-9a-f]{32})\|([1-9][0-9]{0,5})\|([1-9][0-9]{0,5})\|([1-9][0-9]{0,15})'
                  + rb'(?:\|([a-z0-9_]{1,96}))?')
# The original contact format had no weapon field and was emitted only by the stock Bolt Repeater Mk2.
_BOLT_REPEATER_MK2 = b'weapon_gen_s_gatling_01_mk2_macro'
_STOCK_IMPACT_DAMAGE = {_BOLT_REPEATER_MK2: 26, b'weapon_gen_s_laser_01_mk1_macro': 36}
_STATUS_ROW = re.compile(_HEADER + re.escape(_STATUS) + rb'(.*)')
_BLOCK = 16 * 1024
# The provider serializes up to one full batch per pilot. Allow four seconds
# per native impact even when the other pilots' batches are already queued.
_ACK_TIMEOUT = COMPANY_PILOT_LIMIT * MAX_BATCH * 4 + 30
_INITIAL_RECONNECT_DELAY = .25
_MAX_RECONNECT_DELAY = 5


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


class _GunLogError(Exception):
    """A source failure must propagate past the transport's recoverable read errors."""


class _GunLog:
    """Single reader shared by the normal tail and the health wait callback."""

    def __init__(self, path, stream, identity):
        self.path = path
        self.stream = stream
        self.identity = identity
        self.last_size = 0
        self.partial_line = b''
        self.pending_rows = []

    def _read_block(self):
        try:
            info = self.path.lstat()
            if (not _regular(info) or not os.path.samestat(info, self.identity)
                    or not self.last_size <= info.st_size <= MAX_LOG):
                raise _GunLogError('Company combat source log changed')
            self.last_size = info.st_size
            chunk = self.stream.read(_BLOCK)
        except OSError as error:
            raise _GunLogError('Company combat source log unavailable') from error
        if not chunk:
            return False
        *lines, self.partial_line = (self.partial_line + chunk).split(b'\n')
        if len(self.partial_line) > _BLOCK * 4:
            raise _GunLogError('Company combat source log line exceeds limit')
        self.pending_rows.extend(line.rstrip(b'\r') for line in lines if _STATUS in line or _CONTACT in line)
        return True

    def take_rows(self):
        if not self.pending_rows and not self._read_block():
            return None
        rows, self.pending_rows = self.pending_rows, []
        return rows

    def has_events(self):
        if not self.pending_rows:
            self._read_block()
        return bool(self.pending_rows)


class CompanyCombatSender:
    """Tail one private native log in a dedicated worker bound to one admission."""

    def __init__(self, path, port, credential, *, epoch, index, log_identity, health_bridge=None):
        if (type(epoch) is not str or not _EPOCH.fullmatch(epoch) or type(index) is not int
                or not 1 <= index <= 200000 or type(credential) is not dict
                or credential.get('epoch') != epoch or credential.get('index') != index):
            raise ValueError('Company combat admission does not match the local seat')
        if (type(log_identity) is not tuple or len(log_identity) != 2
                or any(type(part) is not int or part < 0 for part in log_identity) or not log_identity[1]):
            raise ValueError('Company combat requires the verified native log identity')
        self._client = CompanyCombatClient(port, credential)
        self._health_bridge = health_bridge
        self._finish_next_health_read = False
        self._path = path
        self._identity = log_identity
        self._epoch = epoch.encode('ascii')
        self._index = index
        self._stop = threading.Event()
        self._counts = dict(armed=False, observed=0, confirmed=0, rejected=0, unknown=0, retries=0,
                            coverage='throttled_direct_primary_gun_notifications')
        self._last_confirmed_receipt = None
        self._failure = None
        self._thread = threading.Thread(target=self._run, name='authority-company-combat', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        if self._thread.is_alive():
            raise RuntimeError('company_combat_sender_cleanup_failed')
        result = dict(self._counts, failure=self._failure)
        if self._last_confirmed_receipt is not None:
            result['last_confirmed_receipt'] = self._last_confirmed_receipt.copy()
        return result

    def _run(self):
        try:
            before = self._path.lstat()
            if not _regular(before) or (before.st_dev, before.st_ino) != self._identity:
                raise ValueError('invalid Company combat source log')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(self._path, flags), 'rb', buffering=0) as stream:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    raise ValueError('Company combat source log changed')
                self._tail(_GunLog(self._path, stream, before))
        except (OSError, ValueError, _GunLogError):
            self._failure = 'source_unavailable'
        finally:
            self._client.close()

    def _tail(self, log):
        armed = b'armed|' + self._epoch + b'|' + str(self._index).encode('ascii')
        while not self._stop.is_set():
            lines = log.take_rows()
            if lines is None:
                if not self._read_requested_health(log):
                    self._stop.wait(.05)
                continue
            impacts = []
            for line in lines:
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
                epoch, shooter, target, counter, weapon_macro = row.groups()
                amount = _STOCK_IMPACT_DAMAGE.get(weapon_macro or _BOLT_REPEATER_MK2)
                if amount is None:
                    raise ValueError('unrecognized Company gun contact weapon')
                sequence = int(counter)
                target_index = int(target)
                if (epoch != self._epoch or int(shooter) != self._index or not 1 <= target_index <= 200000
                        or target_index == self._index or sequence != self._counts['observed'] + 1
                        or sequence > MAX_COUNTER):
                    raise ValueError('Company gun contact does not match the active sequence')
                self._counts['observed'] = sequence
                impacts.append({'id': sequence, 'target_index': target_index, 'amount': amount})
                if len(impacts) == MAX_BATCH:
                    if not self._send(impacts):
                        return
                    impacts = []
            if impacts and not self._send(impacts):
                return
            self._read_requested_health(log)

    def _send(self, impacts):
        deadline = time.monotonic() + _ACK_TIMEOUT
        reconnect_delay = _INITIAL_RECONNECT_DELAY
        while not self._stop.is_set():
            try:
                receipts = self._client.exchange(impacts, deadline=deadline, cancelled=self._stop.is_set)
            except CombatExchangeCancelled:
                return False
            except OSError as error:
                # A dropped reply can follow a successful X4 write. Reuse its
                # impact IDs and the original deadline across reconnects.
                remaining = deadline - time.monotonic()
                if self._stop.wait(min(reconnect_delay, max(0, remaining))):
                    return False
                if time.monotonic() >= deadline:
                    self._failure = 'receipt_timeout' if isinstance(error, TimeoutError) else 'transport_unavailable'
                    return False
                self._counts['retries'] += 1
                reconnect_delay = min(_MAX_RECONNECT_DELAY, reconnect_delay * 2)
                continue
            except ValueError:
                self._failure = 'invalid_receipt'
                return False
            for receipt in receipts:
                self._counts[receipt['status']] += 1
                if receipt['status'] == 'confirmed':
                    # The receiver reports its sampled authority order as its
                    # health revision. Keep one validated receipt for comparison.
                    self._last_confirmed_receipt = receipt
            return True
        return False

    def _read_requested_health(self, log):
        """Yield a disposable read to new contacts; give the next read a full turn."""
        if self._health_bridge is None or self._stop.is_set():
            return False
        request = self._health_bridge.take_request()
        if request is None:
            return False

        def cancelled():
            return self._stop.is_set() or (not self._finish_next_health_read and log.has_events())

        preempted = False
        try:
            health = self._client.read_health(*request, cancelled=cancelled)
        except CombatExchangeCancelled:
            if self._stop.is_set():
                return False
            preempted = True
            health = None
        except (OSError, ValueError):
            # Reads have no side effects. The game can ask again with a fresh sequence.
            health = None
        window, sequence, _ = request
        self._health_bridge.accept_reply(window, sequence, health)
        self._finish_next_health_read = preempted
        return preempted
