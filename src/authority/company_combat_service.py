"""Reliable Company combat ingress, pumped by the authoritative world owner."""
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import socket
import threading
import time

from .company_combat_frames import (BINDING_FIELDS, MAX_COUNTER, decode_health_request, decode_request,
                                    encode_frame, number, read_frame, validate_live_health)
from .company_combat_frames import derive_combat_ticket as derive_combat_ticket
from .company_motion_frames import MAX_PEERS

RECEIPT_HISTORY = 128
IO_TIMEOUT = 15
IDLE_TIMEOUT = 60
HEALTH_READ_TIMEOUT = 2
_LOG = logging.getLogger(__name__)


class CombatImpactRejected(Exception):
    """The adapter refused the impact before issuing any native damage call."""


@dataclass
class _Peer:
    connection: socket.socket
    deadline: float
    ticket: str | None = None
    binding: tuple | None = None
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: bytes = b''
    request_pending: bool = False
    last_health_window: int | None = None
    last_hit_on_ship: int = 0


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
    impacts: list
    receipts: list | None = None


@dataclass
class _PendingImpact:
    batch: _Batch
    receipt: dict
    poll_native: Callable[[], tuple | None] | None = None


@dataclass
class _HealthRead:
    peer: _Peer
    window: int
    sequence: int
    after_revision: int | None
    deadline: float
    operation: object | None = None
    sampled_revision: int = 0


class CompanyCombatService:
    """Run all methods on the owner thread, behind the experimental TLS gateway.

    lookup_current(ticket) returns (generation, registry_epoch, shooter_index),
    or None for permanently revoked or unknown tickets. The adapter begins
    one command and returns confirmed native (shield, hull) or an operation
    with a nonblocking poll() that returns None until confirmation. It raises
    CombatImpactRejected if it can prove that no damage was applied. A pending
    operation owns its native receipt timeout.

    Optional cockpit reads sample the current pilot ship in X4. Pending damage
    writes take priority over those reads.

    A native outcome that cannot be confirmed closes the write path for this
    service. Orders increase across all shooters for one service lifetime.
    Restarting the service requires fresh admission tickets.
    """

    def __init__(self, lookup_current, apply_impact, *, begin_health_read=None, health_window=None):
        if not callable(lookup_current) or (apply_impact is not None and not callable(apply_impact)):
            raise ValueError('invalid combat admission lookup or impact adapter')
        if ((begin_health_read is None) != (health_window is None)
                or (begin_health_read is not None and not all(map(callable, (begin_health_read, health_window))))):
            raise ValueError('cockpit health requires an in-game reader and a current window')
        self._lookup_current = lookup_current
        self._apply_impact = apply_impact
        self._begin_health_read = begin_health_read
        self._health_window = health_window
        self._health_reads = deque()
        self._owner = threading.current_thread()
        self._peers = {}
        self._history = {}
        self._batches = deque()
        self._order = 0
        self._poisoned = False
        self._pending_impact = None
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
        return self._pending_impact is not None

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

    def _admitted(self, peer):
        return peer.ticket is not None and self._lookup_current(peer.ticket) == peer.binding

    def _receive(self, peer):
        frame = read_frame(peer.connection, peer.incoming)
        if frame is None:
            return
        if frame.get('kind') == 'combat_health_read':
            ticket, binding, window, sequence, after_revision = decode_health_request(frame)
            request = _HealthRead(peer, window, sequence, after_revision, time.monotonic() + HEALTH_READ_TIMEOUT)
            queue = self._health_reads
        else:
            ticket, binding, impacts = decode_request(frame)
            request = _Batch(peer, impacts)
            queue = self._batches
        if (self._lookup_current(ticket) != binding
                or (peer.ticket is not None and (peer.ticket, peer.binding) != (ticket, binding))):
            raise ValueError('inactive or mismatched combat admission')
        peer.ticket, peer.binding = ticket, binding
        peer.request_pending = True
        queue.append(request)

    def _queue_reply(self, peer, kind, **fields):
        peer.request_pending = False
        if not self._admitted(peer):
            self._drop(peer)
            return
        peer.outgoing = encode_frame(dict(zip(BINDING_FIELDS, peer.binding), kind=kind, **fields))
        peer.deadline = time.monotonic() + IO_TIMEOUT

    def _health_read_current(self, query):
        return (self._begin_health_read is not None and time.monotonic() < query.deadline
                and self._admitted(query.peer) and self._health_window(query.peer.binding) == query.window)

    def _complete_health_read(self, query, health=None):
        self._health_reads.popleft()
        if query.operation is not None:
            query.operation.cancel()
        if health is not None:
            query.peer.last_health_window = query.window
        self._queue_reply(query.peer, 'combat_health', window=query.window, sequence=query.sequence,
                          health=health, revision=query.sampled_revision if health is not None else None)

    def _advance_health_read(self):
        """Read the pilot ship in X4 when its last observed damage revision is stale."""
        if not self._health_reads:
            return
        query = self._health_reads[0]
        peer = query.peer
        if (peer.last_health_window == query.window and query.after_revision is not None
                and peer.last_hit_on_ship <= query.after_revision <= self._order):
            self._complete_health_read(query)
            return
        if self.native_pending and query.operation is not None:
            # A pending damage write takes priority over a disposable snapshot.
            self._complete_health_read(query)
            return

        health = None
        try:
            if self._health_read_current(query):
                if self.native_pending:
                    return
                if query.operation is None:
                    query.operation = self._begin_health_read(peer.binding)
                    query.sampled_revision = self._order
                result = query.operation.poll()
                if result is None:
                    return
                if self._health_read_current(query) and peer.last_hit_on_ship <= query.sampled_revision:
                    health = validate_live_health(result)
        except (OSError, ValueError):
            pass  # A missed read cannot change the game or invalidate impact receipts.
        self._complete_health_read(query, health)

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

    def _begin_impact(self, batch, history, impact):
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
        self._advance_impact(_PendingImpact(batch, receipt))

    def _record_impact_receipt(self, impact):
        impact.batch.receipts.append(impact.receipt)
        self._pending_impact = None

    def _advance_impact(self, impact):
        receipt = impact.receipt
        binding = impact.batch.peer.binding
        try:
            if impact.poll_native is None:
                result = self._apply_impact(binding, receipt['target_index'], receipt['amount'])
                poll_native = getattr(result, 'poll', None)
                if callable(poll_native):
                    impact.poll_native = poll_native
                    self._pending_impact = impact
                    return
            else:
                result = impact.poll_native()
                if result is None:
                    return
            if type(result) is not tuple or len(result) != 2 or not all(map(number, result)):
                raise ValueError('invalid native combat health confirmation')
        except CombatImpactRejected:
            receipt['status'] = 'rejected'
        except BaseException as exc:
            self._poisoned = True
            self._record_impact_receipt(impact)
            if not isinstance(exc, Exception):
                raise
            _LOG.exception('Native combat outcome unknown for impact %d at order %d', receipt['id'], receipt['order'])
            return
        else:
            receipt.update(status='confirmed', shield=result[0], hull=result[1])
            _, epoch, _ = binding
            target = epoch, receipt['target_index']
            for peer in self._peers.values():
                if peer.binding is not None and peer.binding[1:] == target:
                    peer.last_hit_on_ship = receipt['order']
        self._record_impact_receipt(impact)

    def settle_pending(self):
        """Check only the dispatched command. Return True once no native work remains."""
        self._check_owner()
        impact = self._pending_impact
        if impact is not None:
            # A command already dispatched must settle even after the pilot is gone.
            self._advance_impact(impact)
        return not self.native_pending

    def _advance_batch(self):
        if not self._batches:
            return
        batch = self._batches[0]
        peer = batch.peer
        try:
            if not self._admitted(peer):
                raise ValueError('combat admission changed before applying impact')
            history = self._get_history(peer.ticket, peer.binding)
            if batch.receipts is None:
                batch.receipts = history.match_retries(batch.impacts)
            if len(batch.receipts) < len(batch.impacts):
                self._begin_impact(batch, history, batch.impacts[len(batch.receipts)])
            if len(batch.receipts) < len(batch.impacts):
                return
            self._queue_reply(peer, 'combat_receipts', receipts=batch.receipts)
        except ValueError:
            self._drop(peer)
        self._batches.popleft()

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
            if peer.request_pending:
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
            self._advance_batch()
        self._advance_health_read()
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
        for query in self._health_reads:
            if query.operation is not None:
                query.operation.cancel()
        self._health_reads.clear()
