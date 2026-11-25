"""Synthetic marked checkpoint and trusted client claims; no X4 or listener."""
from dataclasses import dataclass, replace
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from authority.checkpoints import CheckpointStore
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_client_load import ExperimentalClientLoad, HonestClientLoadClaim
from authority.load_messages import Receipt
from authority.save_transfer import SaveOffers
from authority.trusted_marker_load_session import (
    CompanyShipCatalogUnavailable, CompanyShipUnavailable, StaleShipChoice, TrustedMarkerLoadSession,
)
from authority.world import WorldCoordinator

MARKER = 'a' * 32
XML = ('<savegame><script><refs type="string"><ref id="42" string="' + MARKER +
       '"/></refs></script><md><script name="X4AuthorityCheckpointMarker">'
       '<cue name="MarkerState"><vars><value name="$marker" type="string" value="42"/>'
       '<value name="$loaded" type="integer"/></vars></cue></script></md></savegame>')


@dataclass(frozen=True)
class ShipRow:
    ship_index: int
    component_ref: str


class FakeEngine:
    pending_marker = MARKER
    def __init__(self, root): self.root = root
    def pause(self): pass
    def save(self):
        path = self.root / 'source.gz'; path.write_bytes(gzip.compress(XML.encode())); return path
    def resume(self): raise AssertionError('no native engine in test')


class FakeService:
    def __init__(self): self.offered = {}; self.lock = threading.Lock()
    def offer(self, name, path, expected):
        data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
        if digest != expected: raise ValueError('digest differs')
        with self.lock: self.offered[name] = (path, len(data), digest)
        return len(data), digest
    def withdraw(self, name):
        with self.lock: self.offered.pop(name, None)
    def withdraw_many(self, names):
        with self.lock:
            for name in names: self.offered.pop(name, None)


def claim_for(offer, **changes):
    receipt = Receipt(**{key: getattr(offer, key) for key in Receipt.__dataclass_fields__})
    return replace(HonestClientLoadClaim(receipt, MARKER, True, True), **changes)


class TrustedMarkerSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root / 'store').mkdir()
        self.store = CheckpointStore(self.root / 'store')
        self.service = FakeService()
        self.world = WorldCoordinator('epoch-1', FakeEngine(self.root), self.store,
                                      SaveOffers(self.service, self.store))
        self.catalogue = (ShipRow(1, '[0x11]'), ShipRow(2, '[0x22]'))
        self.catalogue_reads = []
        self.adapter = TrustedMarkerLoadSession(self.world, enabled=True,
                                                company_ship_reader=self.read_catalogue)
        self.a, self.b = object(), object()
        self.handle_a = self.adapter.attach(self.a); self.handle_b = self.adapter.attach(self.b)
        self.world.step(); self.world.step()

    def read_catalogue(self, checkpoint):
        self.assertEqual(checkpoint, self.store.latest_marked_confirmed())
        self.catalogue_reads.append(checkpoint)
        return self.catalogue

    def test_required_ship_rejects_unselected_claim_until_this_connection_selects(self):
        root = self.root / 'required'
        root.mkdir()
        (root / 'checkpoints').mkdir()
        store = CheckpointStore(root / 'checkpoints')
        world = WorldCoordinator('epoch-2', FakeEngine(root), store, SaveOffers(FakeService(), store))
        adapter = TrustedMarkerLoadSession(world, enabled=True, require_company_ship=True,
                                           company_ship_reader=lambda checkpoint: self.catalogue)
        client = object()
        adapter.attach(client)
        world.step()
        world.step()
        offer = adapter.begin(client).offer
        with self.assertRaisesRegex(CompanyShipUnavailable, 'choose a free company ship'):
            adapter.accept(client, claim_for(offer))
        adapter.choose_company_ship(client, offer.attempt_id, 1)
        self.assertEqual(adapter.accept(client, claim_for(offer)).attempt_id, offer.attempt_id)

    def test_running_reservations_follow_the_pilots_verified_checkpoint_after_offer_expires(self):
        self.adapter.disconnect(self.b)
        offer = self.adapter.begin(self.a).offer
        checkpoint = self.store.latest_marked_confirmed()
        seat = self.adapter.choose_company_ship(self.a, offer.attempt_id, 1)
        self.assertIsNone(self.adapter.running_ship_reservations())
        self.adapter.accept(self.a, claim_for(offer))
        self.assertEqual(self.world.session.state, 'resuming')
        self.assertIsNone(self.adapter.running_ship_reservations())
        with patch.object(self.world._engine, 'resume'), patch.object(self.world, '_participants') as peers:
            peers.resume.return_value = True
            self.world.step()
        self.assertEqual(self.world.session.state, 'running')
        self.assertIsNone(self.world.session.baseline_id)
        self.assertEqual(self.adapter.running_ship_reservations(), (checkpoint, (seat,)))
        with patch.object(self.adapter, '_verified_marker_checkpoint', replace(checkpoint, sha256='f' * 64)):
            self.assertIsNone(self.adapter.running_ship_reservations())
        self.adapter.disconnect(self.a)
        self.assertIsNone(self.adapter.running_ship_reservations())

    def test_fleet_order_excludes_admitted_and_joining_ships_and_holds_target_across_pilot_loss(self):
        epoch = 'c' * 32
        ships = tuple(CompanyShip(index, f'[0x{index}]', 'sector', None, code)
                      for index, code in enumerate(('AAA-001', 'BBB-002', 'CCC-003'), 1))
        self.adapter._company_ship_reader = lambda _: CompanyShipCatalogue(epoch, ships)
        pilot_offer = self.adapter.begin(self.a).offer
        joining_offer = self.adapter.begin(self.b).offer
        seat = self.adapter.choose_company_ship(self.a, pilot_offer.attempt_id, 1)
        self.adapter.accept(self.a, claim_for(pilot_offer))
        self.adapter.choose_company_ship(self.b, joining_offer.attempt_id, 2)
        order = dict(order_id='e' * 32, ship_index=3, ship_code='CCC-003')
        hold = self.adapter.hold_fleet_target

        for ship in ships[:2]:
            self.assertEqual(hold(self.a, seat, epoch, dict(order, ship_index=ship.ship_index,
                                                            ship_code=ship.code)), 'ship_reserved')
        self.assertEqual(hold(self.b, seat, epoch, order), 'pilot_unavailable')
        self.assertEqual(hold(self.a, seat, 'a' * 32, order), 'invalid_registry')
        self.assertEqual(hold(self.a, seat, epoch, dict(order, ship_code='AAA-001')), 'identity_mismatch')
        self.assertIsNone(hold(self.a, seat, epoch, order))
        with self.assertRaises(CompanyShipUnavailable):
            self.adapter.choose_company_ship(self.b, joining_offer.attempt_id, 3)
        self.adapter.disconnect(self.a)
        with self.assertRaises(ValueError):
            self.adapter.release_fleet_target(dict(order, order_id='f' * 32))
        with self.assertRaises(CompanyShipUnavailable):
            self.adapter.choose_company_ship(self.b, joining_offer.attempt_id, 3)
        self.adapter.release_fleet_target(order)
        self.assertEqual(self.adapter.choose_company_ship(self.b, joining_offer.attempt_id, 3), ships[2])

    def test_shared_ship_reservations_retry_switch_and_reoffer(self):
        attempt_a = self.adapter.begin(self.a).offer.attempt_id
        attempt_b = self.adapter.begin(self.b).offer.attempt_id
        first, second = self.catalogue
        self.assertEqual(self.adapter.list_company_ships(self.a, attempt_a), self.catalogue)
        self.assertEqual(self.adapter.choose_company_ship(self.a, attempt_a, 1), first)
        self.assertEqual(self.adapter.choose_company_ship(self.a, attempt_a, 1), first)
        self.assertEqual(self.adapter.list_company_ships(self.a, attempt_a), self.catalogue)
        self.assertEqual(self.adapter.list_company_ships(self.b, attempt_b), (second,))
        self.assertEqual(self.adapter.list_company_ships(self.b, attempt_b, limit=1), (second,))
        self.assertEqual(self.adapter.list_company_ships(self.a, attempt_a, after_index=1, limit=1),
                         (second,))
        self.assertEqual(self.adapter.list_company_ships(self.b, attempt_b, after_index=2, limit=1), ())
        self.assertEqual(len(self.catalogue_reads), 1)
        with self.assertRaises(StaleShipChoice):
            self.adapter.choose_company_ship(self.b, attempt_a, 2)
        self.assertEqual(self.adapter.choose_company_ship(self.b, attempt_b, 2), second)
        for bad_index in (2, 3, True):
            with self.assertRaises(CompanyShipUnavailable):
                self.adapter.choose_company_ship(self.a, attempt_a, bad_index)
        self.assertEqual(self.adapter.list_company_ships(self.b, attempt_b), (second,))

        new_attempt_b = self.adapter.begin(self.b).offer.attempt_id
        with self.assertRaises(StaleShipChoice):
            self.adapter.list_company_ships(self.b, attempt_b)
        self.assertEqual(self.adapter.choose_company_ship(self.a, attempt_a, 2), second)
        self.assertEqual(self.adapter.list_company_ships(self.b, new_attempt_b), (first,))
        self.adapter.disconnect(self.a)
        self.assertEqual(self.adapter.list_company_ships(self.b, new_attempt_b), self.catalogue)

    def test_admitted_reservation_survives_world_resume_and_new_checkpoint(self):
        class Participants:
            def pause(self, handles):
                return True

            def resume(self, handles):
                return True

        self.world._participants = Participants()
        self.world._engine.resume = lambda: None
        offer_a = self.adapter.begin(self.a).offer
        offer_b = self.adapter.begin(self.b).offer
        self.adapter.choose_company_ship(self.a, offer_a.attempt_id, 1)
        self.adapter.accept(self.a, claim_for(offer_a))
        with self.assertRaises(CompanyShipUnavailable):
            self.adapter.choose_company_ship(self.b, offer_b.attempt_id, 1)
        self.adapter.accept(self.b, claim_for(offer_b))  # Legacy peer.
        self.world.step()
        self.assertEqual(self.world.session.state, 'running')

        waiting = object()
        self.adapter.attach(waiting)
        self.world.step()
        self.world.step()
        offer_waiting = self.adapter.begin(waiting).offer
        self.assertNotEqual(offer_a.checkpoint_id, offer_waiting.checkpoint_id)
        # Bindings may differ in a later snapshot. Keep the reserved identity
        # excluded by component reference as well as by issued index.
        alias = ShipRow(3, '[0x11]')
        self.catalogue += (alias,)
        self.assertEqual(self.adapter.list_company_ships(waiting, offer_waiting.attempt_id),
                         (self.catalogue[1],))
        self.assertEqual(len(self.catalogue_reads), 2)
        self.adapter.disconnect(self.a)
        self.assertEqual(self.adapter.list_company_ships(waiting, offer_waiting.attempt_id), self.catalogue)

    def test_ship_catalogue_unavailable_or_confirmed_checkpoint_changed(self):
        offer = self.adapter.begin(self.a).offer
        with patch.object(self.adapter, '_company_ship_reader', side_effect=ValueError('bad save')):
            with self.assertRaises(CompanyShipCatalogUnavailable):
                self.adapter.list_company_ships(self.a, offer.attempt_id)
        self.adapter.choose_company_ship(self.a, offer.attempt_id, 1)
        replacement = self.store.publish(self.root / 'source.gz', marker=MARKER)
        self.store.confirm(replacement)
        with self.assertRaises(StaleShipChoice):
            self.adapter.choose_company_ship(self.a, offer.attempt_id, 2)

    def test_explicit_opt_in_and_untrusted_primitives(self):
        with self.assertRaises(ValueError): TrustedMarkerLoadSession(self.world)
        with self.assertRaises(ValueError): self.adapter.attach('remote-label')

    def test_real_client_claim_type_flows_without_asserting_byte_proof(self):
        bound = self.adapter.begin(self.a)
        self.assertEqual(bound.marker, MARKER)
        offer = bound.offer
        path = self.root / 'staged.gz'; path.write_bytes(self.world._baseline[1].path.read_bytes())
        client = ExperimentalClientLoad(enabled=True, offer=offer, expected_marker=bound.marker)
        client.verify_staged(path); client.observe_load_requested(offer.attempt_id)
        client.observe_loaded_marker(offer.attempt_id, bound.marker); client.observe_pause(offer.attempt_id, True)
        claim = client.make_claim(offer.attempt_id)
        result = self.adapter.accept(self.a, claim)
        self.assertTrue(result.ready_under_trusted_client_assumption)
        self.assertFalse(result.exact_loaded_bytes_proven)
        self.assertFalse(result.client_authenticated)
        self.assertEqual(self.world.session.ready_players, ((self.handle_a, 'trusted-marker-1'),))
        self.assertNotIn(offer.name, self.service.offered)
        with self.assertRaises(ValueError): self.adapter.accept(self.a, claim)

    def test_mismatched_peer_marker_fields_and_flags_refused(self):
        offer = self.adapter.begin(self.a).offer; self.adapter.begin(self.b)
        claim = claim_for(offer)
        with self.assertRaises(ValueError): self.adapter.accept(self.b, claim)
        for bad in (replace(claim, marker='b'*32), replace(claim, load_event_observed=False),
                    replace(claim, paused_observed=False),
                    replace(claim, receipt=replace(claim.receipt, sha256='c'*64)),
                    replace(claim, receipt=replace(claim.receipt, size=1)),
                    replace(claim, receipt=replace(claim.receipt, load_generation=999))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.adapter.accept(self.a, bad)
        self.assertEqual(self.world.session.ready_players, ())
        self.adapter.accept(self.a, claim)

    def test_reoffer_and_disconnect_fence_old_attempt_and_connection(self):
        old = self.adapter.begin(self.a).offer; new = self.adapter.begin(self.a).offer
        with self.assertRaises(ValueError): self.adapter.accept(self.a, claim_for(old))
        self.adapter.disconnect(self.a)
        new_connection = object(); self.adapter.attach(new_connection)
        with self.assertRaises(ValueError): self.adapter.accept(self.a, claim_for(new))
        with self.assertRaises(ValueError): self.adapter.accept(new_connection, claim_for(new))

    def test_external_withdrawal_and_new_confirmed_checkpoint_refused(self):
        offer = self.adapter.begin(self.a).offer
        self.world._offers.revoke(self.world._load_offers[self.handle_a].save)
        with self.assertRaises(ValueError): self.adapter.accept(self.a, claim_for(offer))
        offer = self.adapter.begin(self.a).offer
        other = self.store.publish(self.root / 'source.gz', marker=MARKER); self.store.confirm(other)
        with self.assertRaisesRegex(ValueError, 'confirmed checkpoint changed'):
            self.adapter.accept(self.a, claim_for(offer))
        self.assertEqual(self.world.session.ready_players, ())

    def test_marker_must_be_present_in_confirmed_saved_bytes(self):
        bad = self.root / 'bad.gz'; bad.write_bytes(gzip.compress(b'<savegame/>'))
        checkpoint = self.store.publish(bad, marker=MARKER); self.store.confirm(checkpoint)
        self.world._baseline = (self.world.session.baseline_id, checkpoint)
        with self.assertRaises(ValueError): self.adapter.begin(self.a)
        with self.assertRaises(RuntimeError): self.adapter.begin(self.b)

    def test_offers_share_marker_proof_only_for_revalidated_generation(self):
        from authority import trusted_marker_load_session as session
        with patch.object(session, 'verify_saved_marker', wraps=session.verify_saved_marker) as verify:
            self.adapter.begin(self.a)
            self.adapter.begin(self.b)
            verify.assert_called_once()
            checkpoint = self.world._baseline[1]
            checkpoint.path.write_bytes(b'x' * checkpoint.size)
            with self.assertRaises(ValueError):
                self.adapter.begin(self.a)
            verify.assert_called_once()

    def test_partial_admission_failure_faults_entire_adapter(self):
        offer = self.adapter.begin(self.a).offer
        original = self.world.mark_loaded_trusting_marker
        def partial(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('indeterminate completion')
        with patch.object(self.world, 'mark_loaded_trusting_marker', side_effect=partial):
            with self.assertRaisesRegex(RuntimeError, 'indeterminate'): self.adapter.accept(self.a, claim_for(offer))
        self.assertEqual(len(self.world.session.ready_players), 1)
        with self.assertRaisesRegex(RuntimeError, 'faulted'): self.adapter.begin(self.b)

    def test_pre_accept_guard_rejects_lost_process_or_exception_before_marking(self):
        offer = self.adapter.begin(self.a).offer
        claim = claim_for(offer)
        def lost_process(): raise RuntimeError('owned process disappeared')
        for guard in (lambda: False, lambda: None, lost_process):
            with self.subTest(guard=guard), self.assertRaises((ValueError, RuntimeError)):
                self.adapter.accept(self.a, claim, pre_accept=guard)
            self.assertEqual(self.world.session.ready_players, ())
            self.assertIn(offer.name, self.service.offered)
        calls = []
        outcome = self.adapter.accept(self.a, claim, pre_accept=lambda: calls.append('guard') or True)
        self.assertEqual(calls, ['guard'])
        self.assertTrue(outcome.ready_under_trusted_client_assumption)


if __name__ == '__main__':
    unittest.main()
