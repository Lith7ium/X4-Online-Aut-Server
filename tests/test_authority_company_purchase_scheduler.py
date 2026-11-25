from collections import deque
from types import SimpleNamespace as Ns
import unittest
from unittest.mock import Mock, patch

from authority import company_purchase_scheduler as scheduler
from authority import company_purchase_control as control
from authority.company_purchase_control import CompanyPurchasePeerLost
from authority.company_purchase_ledger import CompanyPurchaseLedger


def order(number):
    return dict(purchase_id=f'{number:032x}', sector='sector_1', station_code='BIM-169',
                ware='water', quantity=6, price_cents=5819)


def confirmed(purchase):
    return dict(purchase, outcome='confirmed', settled_total_cents=34900,
                company_balance_cents=965100, cargo_units=6, ship_code='WRN-302')


def world_fixture(test, *, pilot_count=2, **options):
    epoch = 'a' * 32
    handles = range(1, pilot_count + 1)
    peers = {h: dict(handle=h, stage='admitted', attempt_id=f'{h:032x}',
                     company_purchase_epoch=epoch, company_purchase_ship=7353+h,
                     company_purchase_statement_version=1)
             for h in handles}
    reservations = {h: Ns(ship_index=7353+h, code='WRN-302' if h == 1 else 'ABC-123')
                    for h in handles}
    session = Ns(_peer=lambda conn: conn, _admitted_ships=reservations, _live=Mock())
    host = Ns(_check=Mock(), _control=Ns(_session=session, _peers=peers, _reader_free=Mock()),
              _world=Ns(session=Ns(state='running', pending=None, world_epoch='world',
                                    ready_players=[(h, None) for h in handles])))
    participants = Ns(_host=host)
    now = [0.]
    outcomes = {h: deque() for h in handles}
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
                                                   verify_timeout=5, **options)

    def step(t):
        now[0] = t
        schedule.step()

    return Ns(step=step, schedule=schedule, outcomes=outcomes, received=received,
              native=native, now=now, peers=peers, reservations=reservations, host=host,
              ledger=ledger, request_statement=request_statement)


class SchedulerTests(unittest.TestCase):
    def test_fleet_dispatch_polls_once_then_preserves_target_on_wrong_native_receipt(self):
        from authority import company_fleet_dispatch
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                fleet = Mock()
                fleet.begin.return_value = fleet.poll.return_value = None
                world = world_fixture(self, pilot_count=1, fleet=fleet)
                world.peers[1]['fleet_order_version'] = 1
                sessions = world.host._control._session
                sessions.hold_fleet_target = Mock(return_value=None)
                sessions.release_fleet_target = Mock()
                order = dict(order_id='c' * 32, ship_index=20, ship_code='NPC-200',
                             sector='cluster_01_sector001_macro', x=1000, y=-105.5, z=0)
                response = dict(order, outcome='accepted')
                received = []

                def poll(owner, handle, attempt, index, receipt, *, guard):
                    received.append(receipt)
                    return order if len(received) == 1 else None

                with patch.object(company_fleet_dispatch, 'poll_company_fleet', poll):
                    world.step(0)
                    world.step(.5)
                    self.assertFalse(world.schedule.before_world_transition())
                    fleet.poll.return_value = dict(response, z=1) if corrupt else response
                    self.assertTrue(world.schedule.before_world_transition())
                    world.step(1)
                    world.step(1.5)
                fleet.begin.assert_called_once_with({'registry_epoch': 'a' * 32}, order)
                sessions.hold_fleet_target.assert_called_once_with(1, world.reservations[1], 'a' * 32, order)
                if corrupt:
                    sessions.release_fleet_target.assert_not_called()
                    self.assertEqual(received, [None, dict(order, outcome='unknown')])
                else:
                    sessions.release_fleet_target.assert_called_once_with(order)
                    self.assertEqual(received, [None, response])

    def test_four_pilot_paging_reserves_regular_purchase_and_undock_turns(self):
        market = Mock()
        offer = dict(side='buy', ware='water', price_cents=100, amount=10, min_amount=1,
                     cargo_units=0, free_units=10, tradeable=True)
        market.begin.return_value = dict(outcome='observed', company_balance_cents=500,
                                         offers=[offer] * 512)
        world = world_fixture(self, pilot_count=4, undock=Mock(), market=market)
        for peer in world.peers.values():
            peer.update(trade_view_version=1, company_undock_version=1)
        events = []
        seen = {handle: [] for handle in world.peers}
        completed = {}

        def purchase(_, handle, *args, page, **kwargs):
            events.append((world.now[0], handle, 'purchase'))
            if page is not None and page['offset'] not in seen[handle]:
                seen[handle].append(page['offset'])
            offset = len(seen[handle]) * 8
            if offset == 512:
                completed.setdefault(handle, world.now[0])
            view = (dict(view_id=f'{handle:032x}', sector='sector_1', station_code='BIM-169', offset=offset)
                    if offset < 512 else None)
            return dict(purchase=None, view=view)

        def undock(_, handle, *args, **kwargs):
            events.append((world.now[0], handle, 'undock'))

        with patch.object(scheduler, 'poll_company_purchase', side_effect=purchase), \
                patch.object(control, 'poll_company_undock', side_effect=undock):
            for tick in range(401):
                before = len(events)
                world.step(tick / 4)
                self.assertLessEqual(len(events) - before, 1)
            self.assertTrue(all(offsets == list(range(0, 512, 8)) for offsets in seen.values()))
            before = len(events)
            world.step(100.25)
            self.assertEqual(len(events), before)  # all four windows closed
        self.assertLessEqual(max(completed.values()), 90)

        regular = [(handle, kind) for time, handle, kind in events if not time % .5]
        self.assertEqual(regular, [(h, kind) for _ in range(25)
                                   for kind in ('purchase', 'undock') for h in range(1, 5)] + [(1, 'purchase')])
        self.assertEqual(market.begin.call_count, 4)
        market.poll.assert_not_called()

    def test_repeated_offsets_and_slow_reopen_do_not_claim_extra_slots(self):
        market = Mock()
        market.begin.return_value = dict(outcome='observed', company_balance_cents=500, offers=[])
        world = world_fixture(self, market=market)
        world.peers[1]['trade_view_version'] = 1
        request = dict(view_id='a' * 32, sector='sector_1', station_code='BIM-169', offset=0)
        events = []

        def purchase(_, handle, *args, page=None, **kwargs):
            now = world.now[0]
            events.append((now, handle, page is not None))
            if handle != 1:
                return None
            if now >= 1:
                world.now[0] += .2
                return dict(purchase=None, view=dict(request, view_id=f'{int(now) + 1:032x}'))
            return dict(purchase=None, view=request)

        with patch.object(scheduler, 'poll_company_purchase', side_effect=purchase):
            for tick in range(26):
                world.step(tick / 4)
        self.assertEqual([(time, handle) for time, handle, _ in events if time % .5], [(0.25, 1)])
        self.assertEqual([time for time, handle, _ in events if handle == 2], [.5, 1.5, 2.5, 3.5, 4.5, 5.5])
        self.assertEqual(market.begin.call_count, 7)

    def test_undock_is_bound_to_a_confirmed_purchase_and_held_for_physical_readback(self):
        undock = Mock()
        undock.begin.return_value = undock.poll.return_value = None
        world = world_fixture(self, undock=undock)
        world.peers.pop(2)
        world.peers[1]['company_undock_version'] = 1
        world.reservations[1].captain_seed = '2211373338772438693'
        purchase = order(1)
        world.outcomes[1].append(purchase)
        world.native.begin.return_value = confirmed(purchase)
        request = dict(request_id='b' * 32, sector=purchase['sector'], station_code=purchase['station_code'],
                       bay_seed='507326418098681806', bay_macro='external_bay', dockarea_macro='dock_area')
        with patch.object(control, 'poll_company_undock', side_effect=[request, None, dict(
                request, request_id='c' * 32), None], create=True) as exchange:
            world.step(0)
            world.step(.5)
            world.step(1)
            undock.begin.assert_called_once_with(
                dict(registry_epoch='a' * 32, ship_index=7354, ship_code='WRN-302',
                     captain_seed='2211373338772438693'), request)
            self.assertFalse(world.schedule.before_world_transition())
            self.assertEqual(exchange.call_count, 1)
            undock.poll.return_value = dict(request, outcome='confirmed')
            self.assertTrue(world.schedule.before_world_transition())
            world.step(1.5)
            self.assertEqual(exchange.call_args.args[1:],
                             (1, world.peers[1]['attempt_id'], 7354, dict(request, outcome='confirmed')))

            # This reservation's purchase cannot unlock another departure.
            world.step(2)
            world.step(2.5)
            world.step(3)
            refused = dict(request, request_id='c' * 32, outcome='refused', reason='purchase_required')
            self.assertEqual(exchange.call_args.args[4], refused)
            undock.begin.assert_called_once()

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

        def attest_after_commit(receipt):
            row = world.ledger._db.execute('SELECT status FROM purchases WHERE id=?',
                                           (receipt['purchase_id'],)).fetchone()
            self.assertEqual(row, ('confirmed',))
            self.assertEqual(world.native.begin.call_count, 1)

        world.native.confirm_persisted.side_effect = attest_after_commit
        world.native.begin.side_effect = [dict(second, outcome='refused', reason='unsupported_purchase')]
        world.step(1)
        assert world.native.begin.call_count == 2
        world.native.confirm_persisted.assert_called_once_with(confirmed(first))
        assert world.received[-1] == (1, confirmed(first))
        world.step(1.5)
        assert world.received[-1] == (2, dict(second, outcome='refused', reason='unsupported_purchase'))
        assert world.schedule.before_world_transition() is True


    def test_ledger_write_failure_does_not_release_native_provider(self):
        world = world_fixture(self)
        first, second = order(1), order(2)
        world.outcomes[1].append(first)
        world.outcomes[2].append(second)
        world.step(0)
        world.step(.5)
        world.native.poll.return_value = confirmed(first)
        with patch.object(world.ledger, 'finish', side_effect=OSError('unavailable ledger')):
            with self.assertRaisesRegex(OSError, 'unavailable ledger'):
                world.step(1)
        world.native.confirm_persisted.assert_not_called()
        self.assertEqual(world.native.begin.call_count, 1)


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
