"""Exercise durable purchase provenance and two-challenge adoption."""
from pathlib import Path
import tempfile
import unittest

from authority.checkpoints import Checkpoint
from authority.company_purchase_ledger import CompanyPurchaseLedger, verify_purchase_ledger

EPOCH = 'e'*32
SHIP = (7354, 'WRN-302')
PURCHASE = dict(purchase_id='a'*32, sector='sector_1', station_code='BIM-169',
                ware='water', quantity=6, price_cents=5819)
ID = PURCHASE['purchase_id']
REPORT = dict(purchase_id=ID, ware='water', quantity=6, settled_total_cents=34900)
UNKNOWN = dict(PURCHASE, outcome='unknown')


class CompanyPurchaseLedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.checkpoint = Checkpoint('checkpoint-' + 'b'*32, self.root/'save.xml.gz', 'd'*64, 30)

    def open(self, path=None, epoch=EPOCH):
        ledger = CompanyPurchaseLedger(epoch, path)
        self.addCleanup(ledger.close)
        return ledger

    def test_two_matching_fresh_statements_survive_snapshot_and_cursor(self):
        ledger = self.open()
        self.assertTrue(ledger.begin(*SHIP, PURCHASE))
        self.assertFalse(ledger.begin(7355, 'WRN-303', PURCHASE))
        self.assertEqual(ledger.finish(ID, UNKNOWN), dict(UNKNOWN, outcome='accepted_pending_statement'))
        self.assertEqual(ledger.next_report(*SHIP), PURCHASE)
        self.assertEqual(ledger.next_report(), PURCHASE)
        self.assertIsNone(ledger.next_report(after_id=ID))
        self.assertIsNone(ledger.next_report(7355, 'WRN-303'))
        self.assertFalse(ledger.statement(ID, '1'*32, REPORT))
        reference = ledger.checkpoint(self.checkpoint)

        restored = self.open(self.root/'restored.sqlite3')
        self.assertTrue(restored.restore(self.checkpoint, reference))
        self.assertFalse(restored.begin(*SHIP, PURCHASE))
        self.assertFalse(restored.statement(ID, '1'*32, REPORT))
        self.assertFalse(restored.statement(ID, '2'*32, dict(REPORT, quantity=5)))
        self.assertFalse(restored.statement(ID, '2'*32, REPORT))
        self.assertFalse(restored.statement(ID, '3'*32, dict(REPORT, settled_total_cents=34914)))
        self.assertFalse(restored.statement(ID, '4'*32, None))
        self.assertTrue(restored.statement(ID, '5'*32, REPORT))
        reported = dict(PURCHASE, outcome='pilot_reported', settled_total_cents=34900)
        self.assertEqual(restored.next_notification(*SHIP), reported)
        self.assertEqual(restored.next_notification(), reported)
        self.assertIsNone(restored.next_notification(after_id=ID))
        self.assertIsNone(restored.next_report())
        self.assertTrue(restored.notified(ID))
        self.assertFalse(restored.notified(ID))
        self.assertIsNone(restored.next_notification())
        self.assertFalse(restored.begin(*SHIP, PURCHASE))

    def test_crash_reservation_is_recovered_without_beginning_again(self):
        path = self.root/'runtime.sqlite3'
        original = CompanyPurchaseLedger(EPOCH, path)
        self.assertTrue(original.begin(*SHIP, PURCHASE))
        original.close()
        restored = self.open(path)
        self.assertEqual(restored.next_report(*SHIP), PURCHASE)
        self.assertFalse(restored.begin(*SHIP, PURCHASE))
        with self.assertRaises(ValueError):
            self.open(path, epoch='f'*32)

    def test_exact_native_receipt_and_sidecar_integrity(self):
        ledger = self.open()
        self.assertIsNone(verify_purchase_ledger(self.checkpoint, None))
        ledger.begin(*SHIP, PURCHASE)
        confirmed = dict(PURCHASE, outcome='confirmed', settled_total_cents=34900,
                         company_balance_cents=965100, cargo_units=6, ship_code=SHIP[1])
        with self.assertRaises(ValueError):
            ledger.finish(ID, dict(confirmed, ship_code='WRN-303'))
        self.assertEqual(ledger.finish(ID, confirmed), confirmed)
        self.assertEqual(ledger.finish(ID, confirmed), confirmed)
        reference = ledger.checkpoint(self.checkpoint)
        self.assertEqual(ledger.checkpoint(self.checkpoint), reference)
        sidecar = verify_purchase_ledger(self.checkpoint, reference, registry_epoch=EPOCH)
        for forged in ({'checkpoint_sha256': 'f'*64}, {'sha256': 'b'*64}, {'registry_epoch': '1'*32}):
            with self.assertRaises(ValueError):
                verify_purchase_ledger(self.checkpoint, {**reference, **forged}, registry_epoch=EPOCH)
        with self.assertRaises(ValueError):
            verify_purchase_ledger(self.checkpoint, None)
        sidecar.write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            verify_purchase_ledger(self.checkpoint, reference)
