"""Schedule purchases on admitted control peers and a serial native provider."""

from collections import deque
from dataclasses import dataclass, field
import secrets
import time

from .company_purchase_control import (
    CompanyPurchasePeerLost, deliver_company_purchase_statement_result, poll_company_purchase,
    purchase_receipt, request_company_purchase_statement,
)


@dataclass(frozen=True)
class _Binding:
    handle: int
    attempt_id: str
    registry_epoch: str
    world_epoch: str
    ship_index: int
    ship_code: str
    conn: object
    reservation_identity: int
    reservation: object = field(compare=False, repr=False)

    def native_value(self):
        return dict(registry_epoch=self.registry_epoch, ship_index=self.ship_index,
                    ship_code=self.ship_code)


@dataclass
class _ActivePurchase:
    binding: _Binding
    purchase: dict
    deadline: float


@dataclass
class _ReportSearch:
    """Per-connection cursors. A later seat may be on the original buyer's device."""
    next_check: float = 0.
    report_cursor: str | None = None
    notice_cursor: str | None = None
    prefer_notice: bool = False
    recognized: dict | None = None
    regular_poll_due: bool = False


class CompanyPurchaseScheduler:
    """Poll admitted pilots while running and settle an in-flight booking before saving."""

    def __init__(self, participants, native, ledger, *, guard=None, clock=time.monotonic,
                 verify_timeout=50):
        if not 0 < verify_timeout <= 60:
            raise ValueError('company purchase verify timeout must be in (0, 60]')
        self._participants = participants
        self._native = native
        self._ledger = ledger
        self._guard = guard
        self._clock = clock
        self._verify_timeout = verify_timeout
        self._active = None
        self._queue = deque()
        self._receipts = {}
        self._report_search = {}
        self._last_handle = 0
        self._next_poll = float('-inf')


    def _check(self):
        self._participants._host._check()
        if self._guard is not None and self._guard() is not True:
            raise RuntimeError('company purchase world guard failed')

    def _bindings(self):
        host = self._participants._host
        world = host._world.session
        if world.state != 'running' or world.pending is not None:
            raise RuntimeError('company purchases require an idle running world')
        control = host._control
        control._reader_free()
        session = control._session
        session._live()
        ready = {handle for handle, _ in world.ready_players}
        bindings = {}
        for conn, peer in control._peers.items():
            handle = peer['handle']
            epoch = peer.get('company_purchase_epoch')
            index = peer.get('company_purchase_ship')
            if (handle not in ready or peer['stage'] != 'admitted'
                    or type(index) is not int or epoch != self._ledger.registry_epoch
                    or peer.get('company_purchase_statement_version') != 1):
                continue
            reservation = session._admitted_ships.get(session._peer(conn))
            if reservation is not None and reservation.ship_index == index:
                bindings[handle] = _Binding(handle, peer['attempt_id'], epoch, world.world_epoch,
                                            index, reservation.code, conn, id(reservation),
                                            reservation)
        return bindings

    def _finish(self, receipt):
        active = self._active
        durable_receipt = self._ledger.finish(active.purchase['purchase_id'], receipt)
        if durable_receipt['outcome'] == 'confirmed':
            self._native.confirm_persisted(durable_receipt)
        self._receipts[active.binding.handle] = (active.binding, durable_receipt)
        self._active = None

    def _advance_native(self, *, begin=False):
        active = self._active
        self._check()
        try:
            receipt = (self._native.begin(active.binding.native_value(), dict(active.purchase))
                       if begin else self._native.poll())
            if receipt is not None:
                receipt = purchase_receipt(receipt)
                if (receipt['outcome'] not in ('confirmed', 'refused', 'unknown')
                        or any(receipt[field] != value for field, value in active.purchase.items())
                        or (receipt['outcome'] == 'confirmed'
                            and receipt['ship_code'] != active.binding.ship_code)):
                    raise ValueError('native purchase receipt binding changed')
            if receipt is None and self._clock() >= active.deadline:
                receipt = dict(active.purchase, outcome='unknown')
        except Exception:
            receipt = dict(active.purchase, outcome='unknown')
        if receipt is not None:
            self._finish(receipt)
        self._check()

    def _begin_one(self, bindings):
        if self._active is not None:
            return
        while self._queue:
            binding, purchase = self._queue.popleft()
            if bindings.get(binding.handle) != binding:
                continue
            self._check()
            if not self._ledger.begin(binding.ship_index, binding.ship_code, purchase):
                self._receipts[binding.handle] = (
                    binding, dict(purchase, outcome='refused', reason='request_replayed'))
                continue
            self._active = _ActivePurchase(binding, purchase, self._clock() + self._verify_timeout)
            self._advance_native(begin=True)
            return

    def _recover_one(self, binding, now):
        search = self._report_search.setdefault(binding, _ReportSearch())
        if search.regular_poll_due:
            search.regular_poll_due = False
            return False
        if now < search.next_check:
            return False
        search.next_check = now + 2
        endpoint = (self._participants, binding.handle, binding.attempt_id, binding.ship_index)
        if search.recognized is not None:
            receipt = search.recognized
            # Another connection might already have acknowledged this receipt.
            # Client delivery is idempotent, and its callback checks original fields.
            deliver_company_purchase_statement_result(*endpoint, receipt, guard=self._guard)
            self._ledger.notified(receipt['purchase_id'])
            search.recognized = None
            search.regular_poll_due = True
            return True

        # Alternate unresolved statements and undelivered results. Only send the
        # opaque ID until this device demonstrates it has the original basket.
        for notice in (search.prefer_notice, not search.prefer_notice):
            cursor = search.notice_cursor if notice else search.report_cursor
            fetch = self._ledger.next_notification if notice else self._ledger.next_report
            original = fetch(after_id=cursor)
            if original is None and cursor is not None:
                original = fetch()
            if original is None:
                continue
            purchase_id = original['purchase_id']
            if notice:
                search.notice_cursor = purchase_id
            else:
                search.report_cursor = purchase_id
            search.prefer_notice = not notice
            challenge = secrets.token_hex(16)
            answer = request_company_purchase_statement(*endpoint, purchase_id, challenge, guard=self._guard)
            if answer is None:
                search.next_check = now + 5
            elif notice:
                if all(original[key] == value for key, value in answer.items()):
                    search.recognized = original
            elif self._ledger.statement(purchase_id, challenge, answer):
                search.recognized = dict(original, outcome='pilot_reported',
                                         settled_total_cents=answer['settled_total_cents'])
            search.regular_poll_due = True
            return True
        return False

    def step(self):
        """Advance native work, then poll at most one peer each half-second."""
        self._check()
        bindings = self._bindings()
        if self._active is not None:
            self._advance_native()
        self._queue = deque((binding, purchase) for binding, purchase in self._queue
                            if bindings.get(binding.handle) == binding)
        self._receipts = {handle: item for handle, item in self._receipts.items()
                          if bindings.get(handle) == item[0]}
        current = set(bindings.values())
        self._report_search = {binding: search for binding, search in self._report_search.items()
                               if binding in current}
        self._begin_one(bindings)

        now = self._clock()
        if now < self._next_poll:
            return
        busy = {binding for binding, _ in self._queue}
        if self._active is not None:
            busy.add(self._active.binding)
        eligible = [handle for handle, binding in bindings.items() if binding not in busy]
        if not eligible:
            return
        handle = min(eligible, key=lambda candidate: (candidate <= self._last_handle, candidate))
        self._last_handle = handle
        self._next_poll = now + 0.5
        binding = bindings[handle]
        _, receipt = self._receipts.get(handle, (None, None))
        try:
            if receipt is None and self._recover_one(binding, now):
                return
            purchase = poll_company_purchase(self._participants, handle, binding.attempt_id,
                                             binding.ship_index, receipt, guard=self._guard)
        except CompanyPurchasePeerLost:
            self._receipts.pop(handle, None)
            return
        self._receipts.pop(handle, None)
        if purchase is not None:
            self._queue.append((binding, purchase))

    def before_world_transition(self):
        """Allow a transition once the current native decision has been recorded.

        While deferred, call this again on a later running tick. It only verifies
        an already-dispatched purchase. It never reads or starts another request.
        """
        self._check()
        if self._active is not None:
            self._advance_native()
        return self._active is None
