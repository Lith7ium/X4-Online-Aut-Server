"""Dispatch one pilot's fleet command to a free Company ship on the server."""

from .company_fleet_orders import fleet_order, fleet_receipt
from .company_purchase_control import CompanyPurchasePeerLost, poll_company_fleet


class CompanyFleetDispatch:
    def __init__(self, participants, native, guard, clock, timeout):
        self._participants = participants
        self._session = participants._host._control._session
        self._native = native
        self._guard = guard
        self._clock = clock
        self._timeout = timeout
        self._active = None
        self._uncertain = False
        self._receipts = {}
        self._next_turn = set()

    @property
    def active_pilot(self):
        return self._active[0] if self._active is not None else None

    def retain(self, current):
        self._receipts = {pilot: receipt for pilot, receipt in self._receipts.items() if pilot in current}
        self._next_turn.intersection_update(current)

    def take_turn(self, pilot, peer):
        version = peer.get('fleet_order_version')
        if type(version) is not int or version not in (1, 2):
            return False
        if pilot in self._next_turn:
            self._next_turn.discard(pilot)
            return True
        self._next_turn.add(pilot)
        return False

    def poll_pilot(self, pilot, *, provider_busy):
        receipt = self._receipts.pop(pilot, None)
        try:
            request = poll_company_fleet(self._participants, pilot.handle, pilot.attempt_id, pilot.ship_index,
                                         receipt, guard=self._guard)
        except CompanyPurchasePeerLost:
            return
        if request is None:
            return
        order = fleet_order(request)
        if 'queue_mode' in order:
            # The caller queues these in the same receive order as purchases.
            return order
        if provider_busy or self._active is not None:
            self._receipts[pilot] = dict(order, outcome='refused', reason='provider_busy')
        else:
            self.start(pilot, order)

    def start(self, pilot, order):
        if self._active is not None:
            raise RuntimeError('another Company fleet order is still being verified')
        if self._uncertain:
            reason = 'provider_unavailable'
        else:
            reason = self._session.hold_fleet_target(pilot.conn, pilot.reservation, pilot.registry_epoch, order)
        if reason is not None:
            self._receipts[pilot] = dict(order, outcome='refused', reason=reason)
            return
        self._active = (pilot, order, self._clock() + self._timeout)
        self.advance(dispatch=True)

    def advance(self, *, dispatch=False):
        if self._active is None:
            return
        pilot, order, deadline = self._active
        try:
            if dispatch:
                result = self._native.begin({'registry_epoch': pilot.registry_epoch}, dict(order))
            else:
                result = self._native.poll()
            if result is None:
                if self._clock() < deadline:
                    return
                result = dict(order, outcome='unknown')
            receipt = fleet_receipt(result, version=2 if 'queue_mode' in order else 1)
            if any(receipt[key] != expected for key, expected in order.items()):
                raise ValueError('native receipt refers to another fleet order')
        except Exception:
            receipt = dict(order, outcome='unknown')
        if receipt['outcome'] == 'unknown':
            # A late native order may still affect this ship. Keep that seat
            # held for this provider process. Other seats and saves stay usable.
            self._uncertain = True
        else:
            self._session.release_fleet_target(order)
        self._receipts[pilot] = receipt
        self._active = None
