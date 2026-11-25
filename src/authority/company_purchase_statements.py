"""Durable native cockpit basket totals scoped to one device and registry."""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import stat


_TOKEN = re.compile(r'[0-9a-f]{32}')
_FINGERPRINT = re.compile(r'[0-9a-f]{64}')
_WARE = re.compile(r'[a-z][a-z0-9_]{0,127}')
_MAX_EXACT = (1 << 53) - 1


def _state_path():
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA')
        if not base or not Path(base).is_absolute():
            raise ValueError('private Windows application data directory required')
        return Path(base) / 'X4Authority'
    base = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
    if not base.is_absolute():
        raise ValueError('absolute X4 Authority state directory required')
    return base / 'x4-authority'


def _ordinary_owned(path, *, directory):
    info = path.lstat()
    if (not (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & 0x400
            or (os.name != 'nt' and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
        raise ValueError('private ordinary Company purchase statement storage required')


class CompanyPurchaseStatements:
    """Keep one immutable account of what a pilot saw when confirming."""

    def __init__(self, fingerprint, epoch, index, *, state_dir=None):
        if (type(fingerprint) is not str or not _FINGERPRINT.fullmatch(fingerprint)
                or type(epoch) is not str or not _TOKEN.fullmatch(epoch)
                or type(index) is not int or not 1 <= index <= 200000):
            raise ValueError('invalid Company purchase statement scope')
        self._scope = (fingerprint, epoch)
        self._index = index
        directory = _state_path() if state_dir is None else Path(state_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ordinary_owned(directory, directory=True)
        self._database = directory / 'company-purchase-statements.sqlite3'
        try:
            service_fd = os.open(self._database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(service_fd)
        _ordinary_owned(self._database, directory=False)
        with closing(self._connect()) as connection, connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS cockpit_statement (
                    fingerprint TEXT NOT NULL,
                    registry_epoch TEXT NOT NULL,
                    ship_index INTEGER NOT NULL,
                    purchase_id TEXT NOT NULL,
                    sector TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    ware TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price_cents INTEGER NOT NULL,
                    displayed_total_cents INTEGER NOT NULL,
                    pilot_reported INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (fingerprint, registry_epoch, ship_index, purchase_id)
                )
            ''')
            connection.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS cockpit_statement_device_key
                ON cockpit_statement (fingerprint, registry_epoch, purchase_id)
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS cockpit_notice (
                    fingerprint TEXT NOT NULL,
                    registry_epoch TEXT NOT NULL,
                    purchase_id TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (fingerprint, registry_epoch, purchase_id)
                )
            ''')

    def _connect(self):
        _ordinary_owned(self._database, directory=False)
        connection = sqlite3.connect(self._database, timeout=0)
        try:
            connection.execute('PRAGMA synchronous=FULL')
        except BaseException:
            connection.close()
            raise
        return connection

    def _read(self, connection, purchase_id):
        return connection.execute('''
            SELECT ship_index, sector, station_code, ware, quantity, price_cents, displayed_total_cents
            FROM cockpit_statement
            WHERE fingerprint = ? AND registry_epoch = ? AND purchase_id = ?
        ''', (*self._scope, purchase_id)).fetchone()

    def retain(self, intent, displayed_total_cents):
        purchase_id, ware, quantity = intent['purchase_id'], intent['ware'], intent['quantity']
        if (type(purchase_id) is not str or not _TOKEN.fullmatch(purchase_id)
                or type(ware) is not str or not _WARE.fullmatch(ware)
                or type(quantity) is not int or not 1 <= quantity <= 2147483647
                or type(displayed_total_cents) is not int or not 1 <= displayed_total_cents <= _MAX_EXACT):
            raise ValueError('invalid native cockpit basket statement')
        observed = (self._index, intent['sector'], intent['station_code'], ware, quantity, intent['price_cents'],
                    displayed_total_cents)
        with closing(self._connect()) as connection, connection:
            connection.execute('''
                INSERT INTO cockpit_statement
                    (fingerprint, registry_epoch, purchase_id, ship_index, sector, station_code,
                     ware, quantity, price_cents, displayed_total_cents)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            ''', (*self._scope, purchase_id, *observed))
            if self._read(connection, purchase_id) != observed:
                raise ValueError('Company purchase statement changed')

    def retain_result(self, receipt, *, queue_notice=False):
        purchase_id = receipt.get('purchase_id')
        if (receipt.get('outcome') != 'pilot_reported' or type(purchase_id) is not str
                or not _TOKEN.fullmatch(purchase_id)):
            return False
        fields = ('sector', 'station_code', 'ware', 'quantity', 'price_cents', 'settled_total_cents')
        with closing(self._connect()) as connection, connection:
            observed = self._read(connection, purchase_id)
            if observed is None or any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                                       for key, value in zip(fields, observed[1:])):
                return False
            settled = connection.execute('''
                UPDATE cockpit_statement SET pilot_reported = 1
                WHERE fingerprint = ? AND registry_epoch = ? AND purchase_id = ? AND pilot_reported = 0
            ''', (*self._scope, purchase_id))
            if queue_notice and settled.rowcount == 1:
                connection.execute('''
                    INSERT INTO cockpit_notice (fingerprint, registry_epoch, purchase_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT DO NOTHING
                ''', (*self._scope, purchase_id))
        return True

    def next_notice(self):
        with closing(self._connect()) as connection:
            row = connection.execute('''
                SELECT basket.purchase_id, basket.ware, basket.quantity,
                       basket.displayed_total_cents, basket.station_code
                FROM cockpit_notice AS notice
                JOIN cockpit_statement AS basket USING (fingerprint, registry_epoch, purchase_id)
                WHERE notice.fingerprint = ? AND notice.registry_epoch = ?
                    AND notice.acknowledged = 0 AND basket.pilot_reported = 1
                ORDER BY notice.rowid
                LIMIT 1
            ''', self._scope).fetchone()
        if row is None:
            return None
        purchase_id, ware, quantity, total, station_code = row
        return dict(purchase_id=purchase_id, ware=ware, quantity=quantity,
                    settled_total_cents=total, station_code=station_code)

    def acknowledge_notice(self, purchase_id):
        if type(purchase_id) is not str or not _TOKEN.fullmatch(purchase_id):
            return
        with closing(self._connect()) as connection, connection:
            connection.execute('''
                UPDATE cockpit_notice SET acknowledged = 1
                WHERE fingerprint = ? AND registry_epoch = ? AND purchase_id = ? AND acknowledged = 0
            ''', (*self._scope, purchase_id))

    def statement(self, purchase_id):
        if type(purchase_id) is not str or not _TOKEN.fullmatch(purchase_id):
            return None
        with closing(self._connect()) as connection:
            observed = self._read(connection, purchase_id)
        if observed is None:
            return None
        _, _, _, ware, quantity, _, total = observed
        # The wire name is inherited. The value is the displayed basket
        # total attested by this pilot, independently of any native debit.
        return dict(purchase_id=purchase_id, ware=ware, quantity=quantity, settled_total_cents=total)
