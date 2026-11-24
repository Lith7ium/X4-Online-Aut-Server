"""Offline checks for the supervisor's purchase save and recovery boundaries."""

import json
import socket
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import authority_recovered_loopback_server as server
from authority.checkpoints import CheckpointStore
from authority.company_purchase_ledger import CompanyPurchaseLedger
from authority.registry_availability_evidence import verify_join_snapshot
from authority_recovered_registry_snapshot import receipt
import test_authority_portable_seed as fixtures


PURCHASE = dict(purchase_id='b'*32, sector='sector_1', station_code='BIM-169',
                ware='water', quantity=6, price_cents=5819)
PURCHASE_ID = PURCHASE['purchase_id']
SHIP = (7354, 'WRN-302')
REPORT = dict(purchase_id=PURCHASE_ID, ware='water', quantity=6, settled_total_cents=34900)


class PurchaseWorldTransitionTests(unittest.TestCase):
    def test_serve_waits_for_native_verification_before_pause_and_save(self):
        events = []
        session = SimpleNamespace(pending=None, state='running')
        barrier_results = iter((False, True, True))
        purchases = SimpleNamespace(
            before_world_transition=lambda: events.append('verify') or next(barrier_results),
            step=lambda: events.append('poll purchases'))

        def tick(_timeout, *, defer_world=False):
            if defer_world:
                events.append('read control')
                if session.state == 'running' and session.pending is None:
                    session.pending = 'pause'
            else:
                assert session.pending
                events.append(session.pending)
                if session.pending == 'pause':
                    session.state = 'paused'
                    session.pending = 'save'
                else:
                    session.pending = None

        host = SimpleNamespace(tick=tick, _world=SimpleNamespace(session=session),
                               _control=SimpleNamespace(_peers={}))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            server.serve(listener, host, deadline=time.monotonic()+2, tick_interval=.001,
                         check=lambda: None, should_stop=lambda: 'save' in events,
                         purchases=purchases)
        self.assertEqual(events, ['read control', 'verify', 'read control', 'verify',
                                  'pause', 'read control', 'verify', 'save'])

    def test_shutdown_drains_before_control_close_and_checkpoint(self):
        events = []
        session = SimpleNamespace(pending=None, state='running', players=[1], baseline_id=3)
        checkpoint = object()

        def control_close():
            events.append('control close')
            session.pending = 'save'
            session.players.clear()

        def save():
            self.assertEqual(events[:3], ['native pending', 'native recorded', 'control close'])
            events.append('save')
            session.pending = None
            session.state = 'paused'

        outcomes = iter((('native pending', False), ('native recorded', True)))

        def barrier():
            label, ready = next(outcomes)
            events.append(label)
            return ready

        control = SimpleNamespace(close=control_close)
        world = SimpleNamespace(session=session, step=save, _baseline=(3, checkpoint))
        store = SimpleNamespace(latest_marked_confirmed=Mock(return_value=checkpoint))
        with patch.object(server.time, 'sleep'):
            self.assertIs(server.settle_shutdown(control, world, store, lambda: None,
                                                 purchases=SimpleNamespace(before_world_transition=barrier)),
                          checkpoint)
        self.assertEqual(events, ['native pending', 'native recorded', 'control close', 'save'])

    def test_prior_contract_and_restart_carry_ledger_without_a_scheduler(self):
        prior = fixtures.PortableSeedTests()
        self.addCleanup(prior.doCleanups)
        prior.setUp()
        # Build a confirmed format-3 fixture with a real sidecar and first report.
        evidence = verify_join_snapshot(prior.checkpoint)
        ledger = CompanyPurchaseLedger(evidence['epoch'], prior.root/'initial.sqlite3')
        self.addCleanup(ledger.close)
        self.assertTrue(ledger.begin(*SHIP, PURCHASE))
        ledger.finish(PURCHASE_ID, dict(PURCHASE, outcome='unknown'))
        self.assertFalse(ledger.statement(PURCHASE_ID, '1'*32, REPORT))
        reference = ledger.checkpoint(prior.checkpoint)
        prior.result['company_purchase_ledger'] = reference
        result_path = prior.source/'result.json'
        result_path.write_text(json.dumps(prior.result))
        profile = (prior.source/fixtures.seed.CONTENT).parent
        options = dict(require_registry_snapshot=True)
        prior_pair = server.verify_previous_source(prior.store, prior.checkpoint, profile, **options)
        self.assertEqual(len(prior_pair), 2)
        self.assertEqual(server.verify_previous_source(prior.store, prior.checkpoint, profile,
                                                       return_supervisor_result=True, **options),
                         prior_pair)
        self.assertEqual(server.verify_previous_source(prior.store, prior.checkpoint, profile,
                                                       return_purchase_reference=True, **options),
                         (*prior_pair, reference))

        # Carry forward with no purchase provider or scheduler instantiated.
        carried = CompanyPurchaseLedger(evidence['epoch'], prior.root/'opt-out.sqlite3')
        self.addCleanup(carried.close)
        self.assertTrue(carried.restore(prior.checkpoint, reference))
        new_checkpoint = prior.store.publish(prior.root/'native-save.gz', marker=prior.checkpoint.marker)
        new_reference = carried.checkpoint(new_checkpoint)
        prior.store.confirm(new_checkpoint)
        prior.result.update(final_checkpoint_id=new_checkpoint.checkpoint_id,
                            final_checkpoint_sha256=new_checkpoint.sha256,
                            company_purchase_ledger=new_reference)
        prior.result['registry_snapshot']['final_receipt'] = receipt(new_checkpoint, evidence)
        result_path.write_text(json.dumps(prior.result))

        reopened = CheckpointStore(prior.store.directory)
        self.assertEqual(server.verify_previous_source(reopened, new_checkpoint, profile,
                                                       return_purchase_reference=True, **options)[2],
                         new_reference)
        resumed = CompanyPurchaseLedger(evidence['epoch'], prior.root/'resumed.sqlite3')
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.restore(new_checkpoint, new_reference))
        self.assertFalse(resumed.begin(*SHIP, PURCHASE))
        self.assertFalse(resumed.statement(PURCHASE_ID, '1'*32, REPORT))
        self.assertTrue(resumed.statement(PURCHASE_ID, '2'*32, REPORT))
        self.assertEqual(resumed.next_notification()['settled_total_cents'], 34900)
        with self.assertRaises(ValueError):
            server.verify_previous_source(reopened, new_checkpoint, profile, registry_status=True)
