"""Synthetic byte snapshots; no process, file provenance or admission."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority import load_observations as observations
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer


class ClientLoadObservations(unittest.TestCase):
    def setUp(self):
        self.offer = Offer(world_epoch='world', connection_handle=2, baseline_id=3,
                           load_generation=4, checkpoint_id='checkpoint-' + 'a' * 32,
                           sha256='b' * 64, attempt_id='c' * 32, size=123, name='j1.xml.gz')
        self.bound = MarkerBoundOffer(self.offer, 'd' * 32)
        self.ticket = {name: getattr(self.offer, name) for name in observations.TICKET_FIELDS if name != 'slot'}
        self.ticket['slot'] = 'save_009'
        fields = '|'.join(str(self.ticket[name]) for name in observations.TICKET_FIELDS).encode('ascii')
        self.request = observations.LOAD_TAG + b'load_requested|' + fields + b'\n'
        self.marker = b'[X4LAN] AUTH_MARKER load INSPECTED ' + self.bound.marker.encode('ascii') + b'\n'
        self.pause = observations.LOAD_TAG + b'pause_observed|' + fields + b'\n'
        self.snapshot = self.request + b'unrelated\n' + self.marker + self.pause

    def validate(self, data, bound=None, slot='save_009'):
        return observations.validate_bound_offer_receipts(data, self.bound if bound is None else bound, slot=slot)

    def test_exact_order_with_unrelated_lines(self):
        self.assertIsNone(self.validate(self.snapshot))

    def test_wrong_offer_marker_or_slot_refused(self):
        variants = [replace(self.offer, **{name: value}) for name, value in (
            ('world_epoch', 'other'), ('connection_handle', 9), ('baseline_id', 9),
            ('load_generation', 9), ('checkpoint_id', 'checkpoint-' + 'e' * 32),
            ('sha256', 'e' * 64), ('attempt_id', 'e' * 32))]
        for offer in variants:
            with self.subTest(field=offer), self.assertRaises(ValueError):
                self.validate(self.snapshot, MarkerBoundOffer(offer, self.bound.marker))
        with self.assertRaises(ValueError):
            self.validate(self.snapshot, MarkerBoundOffer(self.offer, 'e' * 32))
        with self.assertRaises(ValueError):
            self.validate(self.snapshot, slot='save_008')
        for slot in ('save_9', 'save_00９', '../save_009', 'save_009.xml.gz'):
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                self.validate(self.snapshot, slot=slot)

    def test_incomplete_duplicate_out_of_order_and_error_records(self):
        cases = (b'', self.snapshot[:-1], self.request, self.request + self.pause + self.marker,
                 self.snapshot + self.request, self.request + self.marker + self.marker + self.pause,
                 self.request + b'[X4LAN] AUTH_MARKER load ERROR\n' + self.pause,
                 self.request + self.marker + observations.LOAD_TAG + b'ERROR\n' + self.pause)
        for value in cases:
            with self.subTest(data=value[:40]), self.assertRaises(ValueError):
                self.validate(value)

    def test_oversize_and_line_bound(self):
        with patch.object(observations, 'MAX_LOG', len(self.snapshot) - 1):
            with self.assertRaisesRegex(ValueError, 'oversized'):
                self.validate(self.snapshot)
        with patch.object(observations, 'MAX_LINE', 8):
            with self.assertRaisesRegex(ValueError, 'line too long'):
                self.validate(self.snapshot)

    def test_invalid_bound_offer_and_mutated_frozen_offer(self):
        with self.assertRaises(ValueError):
            self.validate(self.snapshot, object())
        with self.assertRaises(ValueError):
            self.validate(self.snapshot, MarkerBoundOffer(self.offer, 'invalid'))
        object.__setattr__(self.offer, 'connection_handle', True)
        with self.assertRaises(ValueError):
            self.validate(self.snapshot)

    def test_stream_accepts_fragmented_records_then_rejects_late_duplicate(self):
        stream = observations.BoundReceiptStream(self.bound, slot='save_009')
        self.assertFalse(stream.feed(self.request[:10]))
        self.assertFalse(stream.feed(self.request[10:] + self.marker + self.pause[:-1]))
        self.assertTrue(stream.feed(b'\n'))
        self.assertTrue(stream.feed(b'unrelated\r\n'))
        with self.assertRaisesRegex(ValueError, 'out of order'):
            stream.feed(self.request)
        with self.assertRaisesRegex(RuntimeError, 'stream failed'):
            stream.feed(b'')

    def test_stream_rejects_errors_and_limits_without_treating_them_as_pending(self):
        for data in (self.pause, b'[X4LAN] AUTH_MARKER load ERROR\n',
                     b'[X4LAN] AUTH_PARTICIPANT ERROR bad\n', b'[X4LAN] AUTH_IDENTITY load ERROR\n'):
            with self.subTest(data=data[:30]):
                stream = observations.BoundReceiptStream(self.bound, slot='save_009')
                with self.assertRaises(ValueError):
                    stream.feed(data)
        stream = observations.BoundReceiptStream(self.bound, slot='save_009')
        with patch.object(observations, 'MAX_LINE', 3):
            with self.assertRaisesRegex(ValueError, 'line too long'):
                stream.feed(b'xxxx')
        stream = observations.BoundReceiptStream(self.bound, slot='save_009')
        with patch.object(observations, 'MAX_LOG', 3):
            with self.assertRaisesRegex(ValueError, 'oversized'):
                stream.feed(b'xxxx')

    def test_registry_receipt_is_ordered_required_and_checked_after_load(self):
        nonce = '1' * 32
        status = ('ready', '2' * 32, 3, '3' * 64)
        registry = observations.REGISTRY_TAG + (nonce + '|startup|' + status[1] + '|3\n').encode()
        def stream():
            return observations.BoundReceiptStream(self.bound, slot='save_009',
                registry_status=status, registry_nonce=nonce)
        checked = stream()
        self.assertFalse(checked.feed(self.request + self.marker + registry[:-1]))
        self.assertTrue(checked.feed(b'\n' + self.pause))
        with self.assertRaises(ValueError):
            checked.feed(registry)
        for snapshot in (self.snapshot, self.request + registry + self.marker + self.pause,
                         self.request + self.marker + registry.replace(b'|3\n', b'|4\n') + self.pause):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                stream().feed(snapshot)
        for error in (b'[X4LAN] AUTH_RECOVERY_REGISTRY_ERROR ' + nonce.encode(),
                      b'Error while executing onEvent script for event: X4AuthorityShipRegistry.Correlated.'):
            checked = stream()
            self.assertTrue(checked.feed(self.request + self.marker + registry + self.pause))
            self.assertFalse(checked.feed(error[:10]))
            with self.assertRaisesRegex(ValueError, 'registry reported'):
                checked.feed(error[10:] + b'\n')

    def test_full_inspection_matches_checkpoint_and_count_before_header(self):
        nonce = '1' * 32
        status = ('ready', '2' * 32, 3, '3' * 64)
        header = observations.REGISTRY_TAG + (nonce + '|startup|' + status[1] + '|3\n').encode()
        inspection = observations.FULL_INSPECT_TAG + (self.bound.marker + ' 3\n').encode()
        def stream():
            return observations.BoundReceiptStream(self.bound, slot='save_009', registry_status=status,
                registry_nonce=nonce, require_full_inspection=True)
        for rows in (header, header + inspection, inspection * 2 + header,
                     inspection.replace(b' 3\n', b' 4\n') + header,
                     inspection.replace(self.bound.marker.encode(), nonce.encode()) + header):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                stream().feed(self.request + self.marker + rows + self.pause)
        checked = stream()
        self.assertFalse(checked.feed(self.request + self.marker + inspection[:-1]))
        self.assertTrue(checked.feed(b'\n' + header + self.pause))
        with self.assertRaisesRegex(ValueError, 'inspection error'):
            checked.feed(b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT_ERROR unexpected response\n')

    def test_saved_ship_seat_receipt_gates_pause_and_refuses_exactly(self):
        nonce = '1' * 32
        status = ('ready', '2' * 32, 20, '3' * 64)
        inspected = observations.FULL_INSPECT_TAG + (self.bound.marker + ' 20\n').encode()
        header = observations.REGISTRY_TAG + (nonce + '|startup|' + status[1] + '|20\n').encode()
        prefix = (nonce + '|' + status[1] + '|18|').encode()
        ready = observations.SEAT_TAG + prefix + b'controlled\n'
        refused = observations.SEAT_REFUSED_TAG + prefix + b'PILOT_PRESENT\n'
        before_seat = self.request + self.marker + inspected + header

        def stream(on_stage=None):
            return observations.BoundReceiptStream(self.bound, slot='save_009',
                registry_status=status, registry_nonce=nonce, require_full_inspection=True,
                seat_index=18, on_stage=on_stage)

        stages = []
        checked = stream(lambda *event: stages.append(event))
        self.assertFalse(checked.feed(before_seat + ready[:-1]))
        self.assertEqual(stages, [('world_marker_confirmed',)])
        self.assertFalse(checked.feed(ready[-1:]))
        self.assertEqual(stages, [('world_marker_confirmed',), ('cockpit_control_confirmed', 18)])
        self.assertTrue(checked.feed(self.pause))
        with self.assertRaisesRegex(ValueError, 'client seat refused: PILOT_PRESENT'):
            checked.feed(refused)
        for data in (before_seat + self.pause, before_seat + ready + ready,
                     before_seat + ready.replace(b'|18|', b'|17|') + self.pause,
                     self.request + self.marker + inspected + ready + header + self.pause):
            with self.subTest(data=data), self.assertRaises(ValueError):
                stream().feed(data)

    def test_partial_registry_configuration_is_refused(self):
        for options in ({'registry_nonce': '1' * 32},
                        {'registry_status': ('uninitialized',)},
                        {'require_full_inspection': True},
                        {'registry_status': ('uninitialized',), 'registry_nonce': '1' * 32,
                         'require_full_inspection': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                observations.BoundReceiptStream(self.bound, slot='save_009', **options)


if __name__ == '__main__':
    unittest.main()
