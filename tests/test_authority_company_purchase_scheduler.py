from collections import deque
from types import SimpleNamespace as Ns
import unittest
from unittest.mock import Mock, patch

from authority import company_purchase_scheduler as scheduler
from authority.company_purchase_control import CompanyPurchasePeerLost
from authority.company_purchase_ledger import CompanyPurchaseLedger


def order(number):
    return dict(purchase_id=f'{number:032x}', sector='sector_1', station_code='BIM-169',
                ware='water', quantity=6, price_cents=5819)


def confirmed(purchase):
    return dict(purchase, outcome='confirmed', settled_total_cents=34900,
                company_balance_cents=965100, cargo_units=6, ship_code='WRN-302')


def world_fixture(test):
    epoch = 'a' * 32
    peers = {h: dict(handle=h, stage='admitted', attempt_id=f'{h:032x}',
                     company_purchase_epoch=epoch, company_purchase_ship=7353+h,
                     company_purchase_statement_version=1)
             for h in (1, 2)}
    reservations = {h: Ns(ship_index=7353+h, code=code)
                    for h, code in ((1, 'WRN-302'), (2, 'ABC-123'))}
    session = Ns(_peer=lambda conn: conn, _admitted_ships=reservations, _live=Mock())
    host = Ns(_check=Mock(), _control=Ns(_session=session, _peers=peers, _reader_free=Mock()),
              _world=Ns(session=Ns(state='running', pending=None, world_epoch='world',
                                    ready_players=[(1, None), (2, None)])))
    participants = Ns(_host=host)
    now = [0.]
    outcomes = {1: deque(), 2: deque()}
    received = []

    def poll(owner, handle, attempt, index, receipt, *, guard):
        assert owner is participants
        assert (attempt, index) == (peers[handle]['attempt_id'], reservations[handle].ship_index)
        received.append((handle, receipt))
        value = outcomes[handle].popleft() if outcomes[handle] else None
        if isinstance(value, CompanyPurchasePeerLost):
            del peers[handle]
            raise value
        return value

    patcher = patch.object(scheduler, 'poll_company_purchase', poll)
    patcher.start()
    test.addCleanup(patcher.stop)
    statement = patch.object(scheduler, 'request_company_purchase_statement', return_value=None)
    request_statement = statement.start()
    test.addCleanup(statement.stop)
    ledger = CompanyPurchaseLedger(epoch)
    test.addCleanup(ledger.close)
    native = Mock()
    native.begin.return_value = native.poll.return_value = None
    schedule = scheduler.CompanyPurchaseScheduler(participants, native, ledger, clock=lambda: now[0],
                                                   verify_timeout=5)

    def step(t):
        now[0] = t
        schedule.step()

    return Ns(step=step, schedule=schedule, outcomes=outcomes, received=received,
              native=native, now=now, peers=peers, reservations=reservations, host=host,
              ledger=ledger, request_statement=request_statement)


class SchedulerTests(unittest.TestCase):
    def test_round_robin_serial_and_scoped_terminal_receipts(self):
        world = world_fixture(self)
        first, second = order(1), order(2)
        world.outcomes[1].extend([first, None])
        world.outcomes[2].extend([second, None])
        for instant in (0, .1, .49, .5):
            world.step(instant)
        assert world.received == [(1, None), (2, None)]
        world.native.begin.assert_called_once_with(
            dict(registry_epoch='a' * 32, ship_index=7354, ship_code='WRN-302'), first)
        assert world.schedule.before_world_transition() is False
        world.native.poll.return_value = confirmed(first)
        world.native.begin.side_effect = [dict(second, outcome='refused', reason='unsupported_purchase')]
        world.step(1)
        assert world.native.begin.call_count == 2
        assert world.received[-1] == (1, confirmed(first))
        world.step(1.5)
        assert world.received[-1] == (2, dict(second, outcome='refused', reason='unsupported_purchase'))
        assert world.schedule.before_world_transition() is True


    def test_disconnected_unrelated_peer_cannot_drop_active_and_replay_cannot_apply(self):
        world = world_fixture(self)
        purchase = order(1)
        world.outcomes[1].append(purchase)
        world.outcomes[2].append(CompanyPurchasePeerLost(2))
        world.step(0)
        second = world.peers[2]
        world.step(.5)
        assert world.schedule.before_world_transition() is False
        world.native.poll.return_value = confirmed(purchase)
        world.step(1)
        world.peers[2] = dict(second, attempt_id='b'*32)
        world.outcomes[2].append(purchase)
        world.step(1.5)
        world.step(2)
        world.step(2.5)
        assert world.received[-1] == (2, dict(purchase, outcome='refused', reason='request_replayed'))
        assert world.native.begin.call_count == 1


    def test_timeout_records_pending_without_preventing_save_or_the_next_purchase(self):
        world = world_fixture(self)
        first, second = order(1), order(2)
        world.outcomes[1].append(first)
        world.outcomes[2].append(second)
        world.step(0)
        world.step(.5)
        self.assertFalse(world.schedule.before_world_transition())
        world.now[0] = 5.6
        self.assertTrue(world.schedule.before_world_transition())
        self.assertEqual(world.ledger.next_report(), first)
        world.native.begin.return_value = dict(second, outcome='refused', reason='unsupported_purchase')
        world.step(5.6)
        world.step(6.1)
        self.assertIn((1, dict(first, outcome='accepted_pending_statement')), world.received)
        self.assertIn((2, world.native.begin.return_value), world.received)
        self.assertEqual(world.native.begin.call_count, 2)
        self.assertFalse(world.ledger.begin(7354, 'WRN-302', first))

    def test_replaced_reservation_discards_queued_purchase(self):
        world = world_fixture(self)
        world.outcomes[1].append(order(1))
        world.step(0)
        old = world.reservations[1]
        world.reservations[1] = Ns(**vars(old))
        world.step(.5)
        world.native.begin.assert_not_called()

    def test_departed_buyer_is_still_verified_and_no_receipt_crosses_attempt(self):
        first, second = order(1), order(2)
        world = world_fixture(self)
        world.outcomes[1].append(first)
        world.outcomes[2].append(second)
        world.step(0)
        world.step(.5)
        replacement = dict(world.peers[1], attempt_id='b'*32)
        del world.peers[1]
        assert world.schedule.before_world_transition() is False
        world.peers[1] = replacement
        world.native.poll.return_value = confirmed(first)
        world.step(1)
        assert world.native.begin.call_count == 2
        assert world.received[-1] == (1, None)

    def test_foreign_native_receipt_is_indeterminate(self):
        first = order(1)
        world = world_fixture(self)
        world.outcomes[1].append(first)
        world.native.begin.return_value = dict(confirmed(first), ship_code='ABC-123')
        world.step(0)
        world.step(.5)
        world.step(1)
        self.assertEqual(world.received[-1], (1, dict(first, outcome='accepted_pending_statement')))
        self.assertTrue(world.schedule.before_world_transition())
        self.assertEqual(world.ledger.next_report(), first)
        world.native.begin.assert_called_once()

    def test_host_ownership_failure_prevents_queued_native_dispatch(self):
        world = world_fixture(self)
        world.outcomes[1].append(order(1))
        world.step(0)
        world.host._check.side_effect = RuntimeError('host faulted')
        with self.assertRaisesRegex(RuntimeError, 'host faulted'):
            world.step(.5)
        world.native.begin.assert_not_called()
