"""Durable server purchase decisions and checkpoint-bound portable snapshots."""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .checkpoints import _read_regular
from .company_purchase_control import purchase_receipt, purchase_value, _statement_value

PURCHASE_LEDGER_NAME = 'company-purchases.sqlite3'
PURCHASE_LEDGER_LIMIT = 64 * 1024 * 1024
_TOKEN = re.compile(r'[0-9a-f]{32}')
_HASH = re.compile(r'[0-9a-f]{64}')
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_GENERATION = re.compile(r'checkpoint-[0-9a-f]{32}')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchases (
    id TEXT PRIMARY KEY,
    ship_index INTEGER NOT NULL,
    ship_code TEXT NOT NULL,
    purchase TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt TEXT,
    reported_cents INTEGER
);
CREATE INDEX IF NOT EXISTS purchases_due ON purchases (ship_index, ship_code, status, id);
CREATE INDEX IF NOT EXISTS purchases_by_status ON purchases (status, id);
CREATE TABLE IF NOT EXISTS challenges (
    purchase_id TEXT NOT NULL,
    challenge TEXT NOT NULL,
    PRIMARY KEY (purchase_id, challenge)
);
"""


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _binding(ship_index, ship_code):
    if type(ship_index) is not int or not 0 < ship_index <= 200000 or not _matches(ship_code, _CODE):
        raise ValueError('invalid company purchase ship binding')


def _id(value):
    if not _matches(value, _TOKEN):
        raise ValueError('invalid company purchase ID')
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _sidecar(checkpoint):
    if not _matches(checkpoint.checkpoint_id, _GENERATION) or not _matches(checkpoint.sha256, _HASH):
        raise ValueError('invalid purchase ledger checkpoint identity')
    return checkpoint.path.with_name(PURCHASE_LEDGER_NAME)


def _metadata(db):
    return dict(db.execute("SELECT key,value FROM metadata WHERE key IN "
                           "('registry_epoch','checkpoint_id','checkpoint_sha256')"))


def _snapshot_bytes(checkpoint, reference, registry_epoch):
    path = _sidecar(checkpoint)
    if reference is None:
        if path.exists() or path.is_symlink():
            raise ValueError('checkpoint purchase ledger exists without a reference')
        return None
    if (type(reference) is not dict
            or set(reference) != {'version', 'registry_epoch', 'checkpoint_id', 'checkpoint_sha256',
                                 'sha256', 'size'}
            or type(reference['version']) is not int or reference['version'] != 1
            or reference['checkpoint_id'] != checkpoint.checkpoint_id
            or reference['checkpoint_sha256'] != checkpoint.sha256
            or not _matches(reference['registry_epoch'], _TOKEN)
            or (registry_epoch is not None and reference['registry_epoch'] != registry_epoch)
            or not _matches(reference['sha256'], _HASH)
            or type(reference['size']) is not int or not 0 < reference['size'] <= PURCHASE_LEDGER_LIMIT):
        raise ValueError('invalid checkpoint purchase ledger reference')
    raw = _read_regular(path, reference['size'])
    if len(raw) != reference['size'] or hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('checkpoint purchase ledger content differs')
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(raw)
        expected = {key: reference[key] for key in ('registry_epoch', 'checkpoint_id', 'checkpoint_sha256')}
        if _metadata(db) != expected or db.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
            raise ValueError('checkpoint purchase ledger provenance differs')
    except sqlite3.DatabaseError as error:
        raise ValueError('invalid checkpoint purchase ledger database') from error
    finally:
        db.close()
    return raw


def verify_purchase_ledger(checkpoint, reference, *, registry_epoch=None):
    """Verify the optional sidecar and return its fixed path, or None for legacy saves."""
    if _snapshot_bytes(checkpoint, reference, registry_epoch) is not None:
        return _sidecar(checkpoint)
    return None


class CompanyPurchaseLedger:
    """One host-owned SQLite ledger for a verified registry epoch."""

    def __init__(self, registry_epoch, path=None):
        if not _matches(registry_epoch, _TOKEN):
            raise ValueError('invalid purchase ledger registry epoch')
        self.registry_epoch = registry_epoch
        self._db = sqlite3.connect(':memory:' if path is None else os.fspath(path))
        try:
            self._db.executescript(_SCHEMA)
            with self._db:
                self._db.execute("INSERT OR IGNORE INTO metadata VALUES ('registry_epoch',?)",
                                 (registry_epoch,))
                if _metadata(self._db).get('registry_epoch') != registry_epoch:
                    raise ValueError('purchase ledger belongs to another registry epoch')
                # A prior process may have lost its journal after dispatch. Its
                # durable reservation still forbids beginning this purchase again.
                self._db.execute("UPDATE purchases SET status='pending' WHERE status='begun'")
        except BaseException:
            self._db.close()
            raise

    def begin(self, ship_index, ship_code, purchase):
        _binding(ship_index, ship_code)
        purchase = purchase_value(purchase)
        with self._db:
            result = self._db.execute(
                "INSERT OR IGNORE INTO purchases (id,ship_index,ship_code,purchase,status) "
                "VALUES (?,?,?,?,'begun')",
                (purchase['purchase_id'], ship_index, ship_code, _json(purchase)))
        return result.rowcount == 1

    def finish(self, purchase_id, receipt):
        _id(purchase_id)
        receipt = purchase_receipt(receipt)
        if receipt['outcome'] == 'unknown':
            receipt = dict(receipt, outcome='accepted_pending_statement')
        status = receipt['outcome']
        if status not in ('accepted_pending_statement', 'confirmed', 'refused'):
            raise ValueError('invalid native purchase outcome')
        body = _json(receipt)
        with self._db:
            row = self._db.execute("SELECT ship_code,purchase,status,receipt FROM purchases WHERE id=?",
                                   (purchase_id,)).fetchone()
            if row is None:
                raise ValueError('native purchase was not reserved')
            ship_code, original, previous, prior = row
            if (any(receipt[key] != value for key, value in json.loads(original).items())
                    or (status == 'confirmed' and receipt['ship_code'] != ship_code)):
                raise ValueError('native purchase receipt differs from reserved purchase')
            if previous != 'begun':
                if prior != body:
                    raise ValueError('native purchase already finished')
                return receipt
            self._db.execute("UPDATE purchases SET status=?,receipt=? WHERE id=?",
                             ('pending' if status == 'accepted_pending_statement' else status,
                              body, purchase_id))
        return receipt

    def _next(self, ship_index, ship_code, status, after_id):
        after = '' if after_id is None else _id(after_id)
        if ship_index is None and ship_code is None:
            return self._db.execute(
                'SELECT purchase,reported_cents FROM purchases WHERE status=? AND id>? ORDER BY id LIMIT 1',
                (status, after)).fetchone()
        _binding(ship_index, ship_code)
        return self._db.execute(
            "SELECT purchase,reported_cents FROM purchases WHERE ship_index=? AND ship_code=? "
            "AND status=? AND id>? ORDER BY id LIMIT 1",
            (ship_index, ship_code, status, after)).fetchone()

    def next_report(self, ship_index=None, ship_code=None, after_id=None):
        row = self._next(ship_index, ship_code, 'pending', after_id)
        return None if row is None else json.loads(row[0])

    def statement(self, purchase_id, challenge, statement):
        _id(purchase_id)
        _id(challenge)
        statement = _statement_value(statement, purchase_id)
        if statement is None:
            return False
        with self._db:
            row = self._db.execute(
                "SELECT purchase,reported_cents FROM purchases WHERE id=? AND status='pending'",
                (purchase_id,)).fetchone()
            if row is None:
                return False
            original, first = row
            original = json.loads(original)
            observed = self._db.execute("INSERT OR IGNORE INTO challenges VALUES (?,?)",
                                        (purchase_id, challenge))
            if (observed.rowcount != 1
                    or any(statement[key] != original[key] for key in ('ware', 'quantity'))):
                return False
            total = statement['settled_total_cents']
            if first is None:
                self._db.execute("UPDATE purchases SET reported_cents=? WHERE id=?", (total, purchase_id))
            elif total == first:
                self._db.execute("UPDATE purchases SET status='reported' WHERE id=?", (purchase_id,))
                return True
        return False

    def next_notification(self, ship_index=None, ship_code=None, after_id=None):
        row = self._next(ship_index, ship_code, 'reported', after_id)
        if row is None:
            return None
        return dict(json.loads(row[0]), outcome='pilot_reported', settled_total_cents=row[1])

    def notified(self, purchase_id):
        with self._db:
            result = self._db.execute("UPDATE purchases SET status='notified' WHERE id=? AND status='reported'",
                                      (_id(purchase_id),))
        return result.rowcount == 1

    def checkpoint(self, checkpoint):
        target = _sidecar(checkpoint)
        snapshot = sqlite3.connect(':memory:')
        try:
            size = self._db.execute('PRAGMA page_size').fetchone()[0]
            pages = self._db.execute('PRAGMA page_count').fetchone()[0]
            if size * pages > PURCHASE_LEDGER_LIMIT:
                raise ValueError('company purchase ledger exceeds checkpoint size limit')
            self._db.backup(snapshot)
            binding = {'registry_epoch': self.registry_epoch, 'checkpoint_id': checkpoint.checkpoint_id,
                       'checkpoint_sha256': checkpoint.sha256}
            with snapshot:
                snapshot.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', binding.items())
            raw = snapshot.serialize()
        finally:
            snapshot.close()
        if len(raw) > PURCHASE_LEDGER_LIMIT:
            raise ValueError('company purchase ledger exceeds checkpoint size limit')
        reference = dict(binding, version=1, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix='.company-purchases-')
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(temp_name, target)
            except FileExistsError:
                verify_purchase_ledger(checkpoint, reference, registry_epoch=self.registry_epoch)
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temp_name).unlink()
        return reference

    def restore(self, checkpoint, reference):
        raw = _snapshot_bytes(checkpoint, reference, self.registry_epoch)
        if raw is None:
            return False
        if self._db.execute('SELECT 1 FROM purchases LIMIT 1').fetchone() is not None:
            raise ValueError('restore requires a fresh company purchase ledger')
        snapshot = sqlite3.connect(':memory:')
        try:
            snapshot.deserialize(raw)
            snapshot.backup(self._db)
        finally:
            snapshot.close()
        with self._db:
            self._db.execute("DELETE FROM metadata WHERE key IN ('checkpoint_id','checkpoint_sha256')")
            self._db.execute("UPDATE purchases SET status='pending' WHERE status='begun'")
        return True

    def close(self):
        self._db.close()
