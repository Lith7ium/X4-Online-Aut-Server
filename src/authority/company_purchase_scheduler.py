"""Schedule purchases on admitted control peers and a serial native provider."""

from collections import deque
from dataclasses import dataclass, field
import time

from .company_purchase_control import (
    CompanyPurchasePeerLost, poll_company_purchase, purchase_receipt,
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


class CompanyPurchaseScheduler:
    """Call step on idle running ticks. Defer transitions while the barrier is False."""

    def __init__(self, participants, native, *, guard=None, clock=time.monotonic,
                 verify_timeout=50):
        if not 0 < verify_timeout <= 60:
            raise ValueError('company purchase verify timeout must be in (0, 60]')
        self._participants = participants
        self._native = native
        self._guard = guard
        self._clock = clock
        self._verify_timeout = verify_timeout
        self._active = None
        self._queue = deque()
        self._receipts = {}
        self._seen = set()
        self._last_handle = 0
        self._next_poll = float('-inf')
        self._fenced = False

    def _check(self):
        if self._guard is not None and self._guard() is not True:
            raise RuntimeError('company purchase world guard failed')

    def _bindings(self):
        host = self._participants._host
        world = host._world.session
        if world.state != 'running' or world.pending is not None:
            raise RuntimeError('company purchases require an idle running world')
        control = host._control
        session = control._session
        ready = {handle for handle, _ in world.ready_players}
        bindings = {}
        for conn, peer in control._peers.items():
            handle = peer['handle']
            epoch = peer.get('company_purchase_epoch')
            index = peer.get('company_purchase_ship')
            if (handle not in ready or peer['stage'] != 'admitted'
                    or type(index) is not int or not epoch):
                continue
            reservation = session._admitted_ships.get(session._peer(conn))
            if reservation is not None and reservation.ship_index == index:
                bindings[handle] = _Binding(handle, peer['attempt_id'], epoch, world.world_epoch,
                                            index, reservation.code, conn, id(reservation),
                                            reservation)
        return bindings

    def _finish(self, receipt):
        active = self._active
        self._receipts[active.binding.handle] = (active.binding, receipt)
        self._active = None
        if receipt['outcome'] == 'unknown':
            self._fenced = True

    def _advance_native(self, *, begin=False):
        active = self._active
        self._check()
        try:
            receipt = (self._native.begin(active.binding.native_value(), dict(active.purchase))
                       if begin else self._native.poll())
            if receipt is not None:
                receipt = purchase_receipt(receipt)
                if (any(receipt[field] != value for field, value in active.purchase.items())
                        or (receipt['outcome'] == 'confirmed'
                            and receipt['ship_code'] != active.binding.ship_code)):
                    raise ValueError('native purchase receipt binding changed')
            if self._clock() >= active.deadline:
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
            if self._fenced or len(self._seen) >= 4096:
                self._receipts[binding.handle] = (
                    binding, dict(purchase, outcome='refused', reason='provider_limit'))
                continue
            if purchase['purchase_id'] in self._seen:
                self._receipts[binding.handle] = (
                    binding, dict(purchase, outcome='refused', reason='request_replayed'))
                continue
            self._check()
            self._seen.add(purchase['purchase_id'])
            self._active = _ActivePurchase(binding, purchase, self._clock() + self._verify_timeout)
            self._advance_native(begin=True)
            return

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
        self._begin_one(bindings)

        now = self._clock()
        if now < self._next_poll:
            return
        busy = {binding for binding, _ in self._queue}
        if self._active is not None:
            busy.add(self._active.binding)
        eligible = [handle for handle, binding in bindings.items()
                    if binding not in busy and (not self._fenced or handle in self._receipts)]
        if not eligible:
            return
        handle = min(eligible, key=lambda candidate: (candidate <= self._last_handle, candidate))
        self._last_handle = handle
        self._next_poll = now + 0.5
        binding = bindings[handle]
        _, receipt = self._receipts.get(handle, (None, None))
        try:
            purchase = poll_company_purchase(self._participants, handle, binding.attempt_id,
                                             binding.ship_index, receipt, guard=self._guard)
        except CompanyPurchasePeerLost:
            self._receipts.pop(handle, None)
            return
        self._receipts.pop(handle, None)
        if purchase is not None:
            self._queue.append((binding, purchase))

    def before_world_transition(self):
        """Return True to permit a transition, False to defer it, or raise to fence it.

        While deferred, call this again on a later running tick. It only verifies
        an already-dispatched purchase. It never reads or starts another request.
        """
        self._check()
        if self._active is not None:
            self._advance_native()
        if self._fenced:
            raise RuntimeError('native company purchase effect is indeterminate')
        return self._active is None
