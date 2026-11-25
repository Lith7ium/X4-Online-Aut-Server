"""Test the durable device inbox for reconciled Company purchases."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from authority.company_purchase_statements import CompanyPurchaseStatements


FINGERPRINT = 'f' * 64
EPOCH = 'e' * 32
PURCHASE = dict(purchase_id='1' * 32, sector='cluster_01_sector001_macro', station_code='ABC-123',
                ware='water', quantity=2, price_cents=230)
RECEIPT = dict(PURCHASE, outcome='pilot_reported', settled_total_cents=460)
NOTICE = dict(purchase_id=PURCHASE['purchase_id'], ware='water', quantity=2,
              settled_total_cents=460, station_code='ABC-123')


class CompanyPurchaseStatementsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_dir = Path(directory.name)

    def open_statements(self, index=1, fingerprint=FINGERPRINT, epoch=EPOCH):
        return CompanyPurchaseStatements(fingerprint, epoch, index, state_dir=self.state_dir)

    def test_rejoin_on_another_ship_preserves_notice_and_acknowledgment(self):
        original = self.open_statements()
        original.retain(PURCHASE, 460)
        self.assertTrue(original.retain_result(RECEIPT, queue_notice=True))
        self.assertTrue(original.retain_result(RECEIPT, queue_notice=True))

        rejoined = self.open_statements(2)
        self.assertEqual(rejoined.next_notice(), NOTICE)
        for unrelated in (self.open_statements(fingerprint='a' * 64), self.open_statements(epoch='a' * 32)):
            self.assertIsNone(unrelated.next_notice())
            unrelated.acknowledge_notice(PURCHASE['purchase_id'])
        self.assertEqual(rejoined.next_notice(), NOTICE)
        rejoined.acknowledge_notice(PURCHASE['purchase_id'])
        rejoined.acknowledge_notice(PURCHASE['purchase_id'])

        restarted = self.open_statements(2)
        self.assertTrue(restarted.retain_result(RECEIPT, queue_notice=True))
        self.assertIsNone(restarted.next_notice())

    def test_notice_requires_a_matching_local_basket_and_an_explicit_request(self):
        statements = self.open_statements()
        self.assertFalse(statements.retain_result(RECEIPT, queue_notice=True))
        statements.retain(PURCHASE, 460)
        self.assertFalse(statements.retain_result(dict(RECEIPT, settled_total_cents=461), queue_notice=True))
        self.assertTrue(statements.retain_result(RECEIPT))
        self.assertIsNone(statements.next_notice())
        rejoined = self.open_statements(2)
        self.assertTrue(rejoined.retain_result(RECEIPT, queue_notice=True))
        self.assertIsNone(rejoined.next_notice())

    def test_inbox_migration_keeps_reported_baskets_quiet(self):
        statements = self.open_statements()
        statements.retain(PURCHASE, 460)
        self.assertTrue(statements.retain_result(RECEIPT))
        pending_purchase = dict(PURCHASE, purchase_id='2' * 32)
        statements.retain(pending_purchase, 460)
        database = self.state_dir / 'company-purchase-statements.sqlite3'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute('DROP TABLE cockpit_notice')

        migrated = self.open_statements()
        self.assertIsNone(migrated.next_notice())
        self.assertTrue(migrated.retain_result(RECEIPT, queue_notice=True))
        self.assertIsNone(migrated.next_notice())
        pending_receipt = dict(RECEIPT, purchase_id=pending_purchase['purchase_id'])
        self.assertTrue(migrated.retain_result(pending_receipt, queue_notice=True))
        self.assertEqual(migrated.next_notice(), dict(NOTICE, purchase_id=pending_purchase['purchase_id']))
