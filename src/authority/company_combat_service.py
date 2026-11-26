"""Reliable Company combat ingress, pumped by the authoritative world owner."""
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import socket
import threading
import time

from .company_combat_frames import (BINDING_FIELDS, MAX_COUNTER, decode_request, encode_frame,
                                    number, read_frame)
from .company_combat_frames import derive_combat_ticket as derive_combat_ticket
from .company_motion_frames import MAX_PEERS

RECEIPT_HISTORY = 128
IO_TIMEOUT = 15
IDLE_TIMEOUT = 60
_LOG = logging.getLogger(__name__)


class CombatImpactRejected(Exception):
    """The adapter refused the impact before issuing any native damage call."""


@dataclass
class _Peer:
    connection: socket.socket
    deadline: float
    identity: tuple | None = None
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: bytes = b''
    pending: bool = False


@dataclass
class _History:
    binding: tuple
    last_id: int = 0
    receipts: OrderedDict = field(default_factory=OrderedDict)

    def add(self, receipt):
        self.last_id = receipt['id']
        self.receipts[self.last_id] = receipt
        if len(self.receipts) > RECEIPT_HISTORY:
            self.receipts.popitem(last=False)

    def match_retries(self, impacts):
        if impacts[0]['id'] > self.last_id + 1:
            raise ValueError('gap in combat impact ids')
        receipts = []
        for impact in impacts:
            if impact['id'] > self.last_id:
                break
            receipt = self.receipts.get(impact['id'])
            if receipt is None or any(receipt[key] != impact[key] for key in impact):
                raise ValueError('expired or changed combat impact retry')
            receipts.append(receipt)
        return receipts


@dataclass
class _Batch:
    peer: _Peer
    ticket: str
    binding: tuple
    impacts: list
    receipts: list | None = None


@dataclass
class _NativePending:
    batch: _Batch
    receipt: dict
    poll: Callable[[], tuple | None]


class CompanyCombatService:
    """Run all methods on the owner thread, behind the experimental TLS gateway.

    lookup_current(ticket) returns (generation, registry_epoch, shooter_index),
    or None for permanently revoked or unknown tickets. The adapter begins
    one command and returns confirmed native (shield, hull) or an operation
    with a nonblocking poll() that returns None until confirmation. It raises
    CombatImpactRejected if it can prove that no damage was applied. A pending
    operation owns its native receipt timeout.

    A native outcome that cannot be confirmed closes the write path for this
    service. Orders increase across all shooters for one service lifetime.
    Restarting the service requires fresh admission tickets.
    """

    def __init__(self, lookup_current, apply_impact):
        if not callable(lookup_current) or (apply_impact is not None and not callable(apply_impact)):
            raise ValueError('invalid combat admission lookup or impact adapter')
        self._lookup_current = lookup_current
        self._apply_impact = apply_impact
        self._owner = threading.current_thread()
        self._peers = {}
        self._history = {}
        self._batches = deque()
        self._order = 0
        self._poisoned = False
        self._native = None
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._listener.bind(('127.0.0.1', 0))
            self._listener.listen(MAX_PEERS)
            self._listener.setblocking(False)
        except BaseException:
            self._listener.close()
            raise
        self.port = self._listener.getsockname()[1]

    @property
    def pending(self):
        return bool(self._batches)

    @property
    def poisoned(self):
        return self._poisoned

    @property
    def native_pending(self):
        return self._native is not None

    def _check_owner(self):
        if threading.current_thread() is not self._owner:
            raise RuntimeError('Company combat service must run on the world owner thread')

    def _accept(self, now):
        for _ in range(MAX_PEERS):
            try:
                connection, _ = self._listener.accept()
            except BlockingIOError:
                return
            if len(self._peers) >= MAX_PEERS:
                connection.close()
                continue
            try:
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                connection.setblocking(False)
            except OSError:
                connection.close()
                continue
            self._peers[connection] = _Peer(connection, now + IO_TIMEOUT)

    def _drop(self, peer):
        self._peers.pop(peer.connection, None)
        peer.connection.close()

    def _receive(self, peer):
        frame = read_frame(peer.connection, peer.incoming)
        if frame is None:
            return
        ticket, binding, impacts = decode_request(frame)
        identity = ticket, binding
        if (self._lookup_current(ticket) != binding
                or (peer.identity is not None and peer.identity != identity)):
            raise ValueError('inactive or mismatched combat admission')
        peer.identity = identity
        peer.pending = True
        self._batches.append(_Batch(peer, ticket, binding, impacts))

    def _get_history(self, ticket, binding):
        history = self._history.get(ticket)
        if history is None:
            if len(self._history) >= MAX_PEERS:
                for previous in tuple(self._history):
                    if self._lookup_current(previous) is None:
                        del self._history[previous]
                if len(self._history) >= MAX_PEERS:
                    raise ValueError('combat admission history is full')
            history = self._history[ticket] = _History(binding)
        if history.binding != binding:
            raise ValueError('combat admission binding changed')
        return history

    def _start(self, batch, history, impact):
        if self._order >= MAX_COUNTER:
            raise ValueError('combat order exhausted')
        self._order += 1
        receipt = dict(impact, order=self._order, status='unknown', shield=None, hull=None)
        # Reserve the id before issuing a write. A failed confirmation never reissues it.
        history.add(receipt)
        if self._poisoned or self._apply_impact is None:
            receipt['status'] = 'rejected'
            batch.receipts.append(receipt)
            return
        self._resolve(batch, receipt, lambda: self._apply_impact(
            history.binding, impact['target_index'], impact['amount']), starting=True)

    def _finish(self, batch, receipt):
        batch.receipts.append(receipt)
        self._native = None

    def _resolve(self, batch, receipt, readback, *, starting):
        try:
            result = readback()
            if starting:
                pending_poll = getattr(result, 'poll', None)
                if callable(pending_poll):
                    self._native = _NativePending(batch, receipt, pending_poll)
                    return
            elif result is None:
                return
            if type(result) is not tuple or len(result) != 2 or not all(map(number, result)):
                raise ValueError('invalid native combat health confirmation')
        except CombatImpactRejected:
            receipt['status'] = 'rejected'
        except BaseException as exc:
            self._poisoned = True
            self._finish(batch, receipt)
            if not isinstance(exc, Exception):
                raise
            _LOG.exception('Native combat outcome unknown for impact %d at order %d', receipt['id'], receipt['order'])
            return
        else:
            receipt.update(status='confirmed', shield=result[0], hull=result[1])
        self._finish(batch, receipt)

    def settle_pending(self):
        """Check only the dispatched command. Return True once no native work remains."""
        self._check_owner()
        native = self._native
        if native is not None:
            # A command already dispatched must settle even after the pilot is gone.
            self._resolve(native.batch, native.receipt, native.poll, starting=False)
        return not self.native_pending

    def _advance(self):
        if not self._batches:
            return
        batch = self._batches[0]
        try:
            if self._lookup_current(batch.ticket) != batch.binding:
                raise ValueError('combat admission changed before applying impact')
            history = self._get_history(batch.ticket, batch.binding)
            if batch.receipts is None:
                batch.receipts = history.match_retries(batch.impacts)
            if len(batch.receipts) < len(batch.impacts):
                self._start(batch, history, batch.impacts[len(batch.receipts)])
            if len(batch.receipts) < len(batch.impacts):
                return
            if self._lookup_current(batch.ticket) != batch.binding:
                raise ValueError('combat admission changed before issuing receipts')
            batch.peer.outgoing = encode_frame(dict(zip(BINDING_FIELDS, batch.binding),
                                                    kind='combat_receipts', receipts=batch.receipts))
            batch.peer.deadline = time.monotonic() + IO_TIMEOUT
        except ValueError:
            self._drop(batch.peer)
        self._batches.popleft()
        batch.peer.pending = False

    def _write(self, peer, now):
        try:
            sent = peer.connection.send(peer.outgoing)
        except BlockingIOError:
            return
        if not sent:
            raise ConnectionError('Company combat send closed')
        peer.outgoing = peer.outgoing[sent:]
        if not peer.outgoing:
            peer.deadline = now + IDLE_TIMEOUT

    def poll(self):
        """Queue complete frames and apply at most one new impact, in observed order."""
        self._check_owner()
        now = time.monotonic()
        self._accept(now)
        for peer in tuple(self._peers.values()):
            if peer.pending:
                continue
            try:
                if now >= peer.deadline:
                    raise TimeoutError('Company combat peer timed out')
                if not peer.outgoing:
                    self._receive(peer)
            except (OSError, ValueError, RecursionError):
                self._drop(peer)
        if self.native_pending:
            self.settle_pending()
        else:
            self._advance()
        now = time.monotonic()
        for peer in tuple(self._peers.values()):
            if peer.outgoing:
                try:
                    self._write(peer, now)
                except OSError:
                    self._drop(peer)

    def close(self):
        """Close ingress. The owner can still settle a dispatched native command."""
        self._check_owner()
        self._listener.close()
        for peer in tuple(self._peers.values()):
            self._drop(peer)
        self._batches.clear()
        self._history.clear()
