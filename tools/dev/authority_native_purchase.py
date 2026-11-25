"""Opt-in, Linux provider-side IPC for serial stock native Company bookings."""
from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import socket
import time

from authority.company_trade_messages import purchase_value, sale_value
from authority_trade_settlement import bound_dock, live_purchase_before, purchase_settlement, sale_settlement


_IDS = re.compile(r'[0-9a-f]{32}')
_COMPONENT = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_BASKET_FIELDS = ('sector', 'station_code', 'ware', 'quantity', 'price_cents')
_BINDING_FIELDS = ('registry_epoch', 'ship_index', 'ship_code')
_REFUSALS = {'captain_required', 'dock_mismatch', 'invalid_registry', 'no_unique_offer',
             'request_binding_invalid', 'provider_busy', 'provider_limit'}
_MAX_IDS = 1024
_MAX_RECEIPTS = 201
_MAX_PACKET = 768
_MAX_UNIT_PRICE = 2**31 - 1
_MAX_NATIVE_QUANTITY = 65535
_MAX_SAFE_NUMBER = 2**53 - 1


def _valid_binding(binding):
    epoch = binding['registry_epoch']
    index = binding['ship_index']
    code = binding['ship_code']
    return (type(epoch) is str and _IDS.fullmatch(epoch) is not None
            and type(index) is int and 0 < index <= 2**31 - 1
            and type(code) is str and _COMPONENT.fullmatch(code) is not None)


@dataclass
class _Attempt:
    binding: dict
    request: dict
    kind: str
    deadline: float
    rows: list = field(default_factory=list)
    sent: bool = False

    @property
    def id(self):
        return self.request[f'{self.kind}_id']


class NativePurchaseProvider:
    """Drive one leased, already-owned X4 process from its supervisor loop.

    Calls are serialized by the supervisor. Pending is None. Exact duplicate
    calls read pending or cached state. A loss after dispatch remains unknown
    and disables further dispatch. No command changes ship or station state
    outside the guarded stock native booking.
    """

    def __init__(self, *, timeout=45, clock=time.monotonic):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError('invalid purchase deadline')
        self._timeout = timeout
        self._clock = clock
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 512 << 10)
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._lease = secrets.token_hex(16)
        self._peer = None
        self._attempt = None
        self._history = {}
        self._exhausted = False
        self._awaiting_commit = None
        self._previous_confirmed = '-'
        self._staged_md = None

    def install(self, case):
        """Stage only in the guarded recovery overlay, before the X4 launch."""
        if self._staged_md is not None:
            raise ValueError('provider purchase is already staged')
        case = Path(case).absolute()
        extension = case / 'extensions/x4_lan_lab'
        if extension.resolve(strict=True) != extension or extension.stat().st_uid != os.geteuid():
            raise ValueError('provider purchase requires an owned overlay')
        bridge = extension / 'ui/authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        if any(path.is_symlink() or not path.is_file() for path in (bridge, manifest)):
            raise ValueError('provider purchase requires ordinary bridge overlay files')
        bridge_text = bridge.read_text(encoding='ascii')
        manifest_text = manifest.read_text(encoding='ascii')
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        entry = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        if (bridge_text.count(tick) != 1 or 'local registry_checkpoint' not in bridge_text
                or manifest_text.count(entry) != 1):
            raise ValueError('provider purchase requires the registry-aware recovered world bridge')

        probe_dir = Path(__file__).resolve().parent
        names = ('authority_trade_ledger.lua', 'authority_native_purchase.lua')
        md = extension / 'md/authority_company_stock_trade.xml'
        files = [*(extension / 'ui' / name for name in names), md]
        if any(path.exists() or path.is_symlink() for path in files):
            raise FileExistsError('provider purchase scripts already present')
        contents = {path: (probe_dir / path.name).read_bytes() for path in files}
        for path, data in contents.items():
            with path.open('xb') as output:
                output.write(data)
        self._staged_md = md, hashlib.sha256(contents[md]).digest()

        port = self._socket.getsockname()[1]
        hook = f""" if registry_checkpoint:ready() then
  if not native_purchase_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   native_purchase_tick = X4LAN_AUTHORITY_NATIVE_PURCHASE.new("{self._lease}", assert(lib.open('127.0.0.1',{port})))
  end
  native_purchase_tick(C.IsGamePaused())
 end
"""
        bridge.write_text('local native_purchase_tick\n' + bridge_text.replace(tick, tick + hook), encoding='ascii')
        modules = ''.join(f'    <file name="ui/{name}" />\n' for name in names)
        manifest.write_text(manifest_text.replace(entry, modules + entry), encoding='ascii')
        return [*files, bridge, manifest]

    def archive_cues(self, case, out):
        """Archive the verified MD only after the caller proves child cleanup."""
        case, out = Path(case).absolute(), Path(out).absolute()
        if case != out / 'case' or self._staged_md is None:
            raise ValueError('provider purchase archive must belong to its stopped run')
        source, expected = self._staged_md
        if (source != case / 'extensions/x4_lan_lab/md/authority_company_stock_trade.xml'
                or not source.is_file() or source.resolve(strict=True) != source
                or hashlib.sha256(source.read_bytes()).digest() != expected):
            raise ValueError('staged provider purchase cue changed')
        destination = out / source.name
        os.link(source, destination, follow_symlinks=False)
        source.unlink()
        return destination

    def begin(self, binding, purchase):
        return self._begin(binding, purchase_value(purchase), 'purchase')

    def begin_sale(self, binding, sale):
        """Submit an explicit Company sale through the same serialized native slot."""
        return self._begin(binding, sale_value(sale), 'sale')

    def _begin(self, binding, request, kind):
        try:
            binding = {name: binding[name] for name in _BINDING_FIELDS}
        except (TypeError, KeyError):
            raise ValueError('incomplete native purchase binding') from None
        if not _valid_binding(binding):
            raise ValueError('invalid native purchase binding')
        attempt = _Attempt(binding, request, kind, self._clock() + self._timeout)
        prior = self._history.get(attempt.id)
        if prior is not None:
            previous, receipt, _ = prior
            self._same_request(previous, attempt)
            return dict(receipt)
        if self._attempt is not None and self._attempt.id == attempt.id:
            self._same_request(self._attempt, attempt)
            return self.poll()

        if len(self._history) >= _MAX_IDS:
            return dict(request, outcome='refused', reason='provider_limit')
        if request['quantity'] > _MAX_NATIVE_QUANTITY or request['price_cents'] > _MAX_UNIT_PRICE:
            return self._record(attempt, 'refused', reason=f'unsupported_{kind}')
        if self._exhausted:
            return self._record(attempt, 'refused', reason='provider_limit')
        if self._attempt is not None or self._awaiting_commit is not None:
            return self._record(attempt, 'refused', reason='provider_busy')
        self._attempt = attempt
        return self.poll()

    @staticmethod
    def _same_request(previous, attempt):
        if previous.binding != attempt.binding or previous.request != attempt.request:
            raise ValueError('native trade ID already bound to different input')

    def evidence(self, purchase_id):
        """Return the private native rows and verification for audit."""
        saved = self._history.get(purchase_id)
        if saved is not None:
            return saved[2]
        if self._attempt is not None and self._attempt.id == purchase_id:
            return self._observations(self._attempt)

    @staticmethod
    def _observations(attempt, reason=None, audit=None):
        before = None
        if len(attempt.rows) >= 3:
            try:
                parts = [row.split('|') for row in attempt.rows[:3]]
                before = live_purchase_before(parts, attempt.id, attempt.binding['registry_epoch'],
                                              ware=attempt.request['ware'], quantity=attempt.request['quantity'],
                                              sell=attempt.kind == 'sale')
                if before['price_cents'] != attempt.request['price_cents']:
                    before = None
            except ValueError:
                pass
        return {'reason': reason, 'before': before, 'rows': tuple(attempt.rows), 'settlement': audit}

    def _record(self, attempt, outcome, *, reason=None, audit=None, **fields):
        receipt = dict(attempt.request, outcome=outcome, **fields)
        if outcome == 'refused':
            receipt['reason'] = reason
        self._history[attempt.id] = (attempt, receipt, self._observations(attempt, reason, audit))
        if self._attempt is attempt:
            self._attempt = None
            if outcome == 'confirmed':
                self._awaiting_commit = attempt.id
            elif outcome == 'unknown' and attempt.sent:
                self._exhausted = True
        return dict(receipt)

    def confirm_persisted(self, receipt):
        """Release the exact native confirmation after the host commits its ledger."""
        self._confirm(receipt, 'purchase')

    def confirm_sale_persisted(self, receipt):
        """Release the shared slot after the host durably records the native sale."""
        self._confirm(receipt, 'sale')

    def _confirm(self, receipt, kind):
        request_id = receipt.get(f'{kind}_id')
        if (self._exhausted or request_id is None or self._awaiting_commit != request_id
                or self._history[request_id][1] != receipt or receipt['outcome'] != 'confirmed'):
            raise ValueError(f'no matching native {kind} confirmation awaiting ledger commit')
        self._previous_confirmed = request_id
        self._awaiting_commit = None

    def _settle(self, attempt):
        parts = [row.split('|') for row in attempt.rows]
        try:
            steps = {part[0] for part in parts}
            if attempt.kind == 'sale' and 'SALE_COMPLETE' in steps:
                audit = sale_settlement(attempt.rows, attempt.id, attempt.binding['registry_epoch'],
                                        ware=attempt.request['ware'], quantity=attempt.request['quantity'])
                balances = (audit['credited_cents'], audit['company_balance_cents'], audit['cargo_units'])
                if (not audit['verified'] or audit['price_cents'] != attempt.request['price_cents']
                        or any(not 0 <= value <= _MAX_SAFE_NUMBER for value in balances)):
                    return self._record(attempt, 'unknown', reason='native_settlement_unverified', audit=audit)
                return self._record(
                    attempt, 'confirmed', audit=audit, ship_code=attempt.binding['ship_code'],
                    credited_cents=balances[0], company_balance_cents=balances[1], cargo_units=balances[2],
                )
            if attempt.kind == 'purchase' and 'DEAL' in steps:
                audit = purchase_settlement(attempt.rows, attempt.id, attempt.binding['registry_epoch'],
                                            'live_purchase', ware=attempt.request['ware'],
                                            quantity=attempt.request['quantity'])
                balances = (audit['settled_total_cents'], audit['deal']['faction_after_ct'],
                            audit['ship_ware_units_after'])
                if (not audit['verified'] or audit['price_cents'] != attempt.request['price_cents']
                        or any(not 0 <= value <= _MAX_SAFE_NUMBER for value in balances)):
                    return self._record(attempt, 'unknown', reason='native_settlement_unverified', audit=audit)
                return self._record(
                    attempt, 'confirmed', audit=audit, ship_code=attempt.binding['ship_code'],
                    settled_total_cents=audit['settled_total_cents'],
                    company_balance_cents=audit['deal']['faction_after_ct'],
                    cargo_units=audit['ship_ware_units_after'],
                )
            result = parts[-2]
            if len(result) != 4 or result[:2] != ['RESULT', attempt.id]:
                raise ValueError('unbound terminal receipt')
            _, _, reason, price = result
            opening = parts[:-2]
            if not opening and reason in _REFUSALS and price == '0':
                return self._record(attempt, 'refused', reason=reason)
            if opening:
                bound_dock(opening[0], attempt.id)
            quote = ['RESULT', attempt.id, 'quote', price]
            if reason in ('not_tradeable', 'provider_limit') and (opening[1:] == [quote] or
                                                                  (len(opening) == 1 and price == '0')):
                return self._record(attempt, 'refused', reason=reason)
            if reason == 'price_changed' and len(opening) == 3 and opening[1] == quote:
                refusal = opening[2]
                if len(refusal) != 9 or refusal[:3] != ['REFUSED', attempt.id, 'price_changed']:
                    raise ValueError('unbound price refusal')
                original, current, before_cargo, after_cargo, before_cash, after_cash = map(int, refusal[3:])
                if (original == attempt.request['price_cents'] and 0 < current <= _MAX_UNIT_PRICE
                        and current != original and price == str(current)
                        and before_cargo == after_cargo >= 0 and before_cash == after_cash >= 0):
                    return self._record(attempt, 'refused', reason=reason, current_price_cents=current)
        except (ValueError, IndexError):
            return self._record(attempt, 'unknown', reason='malformed_native_receipt')
        return self._record(attempt, 'unknown', reason='native_receipt_inconclusive')

    def poll(self):
        for _ in range(256):
            try:
                packet, source = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                break
            if len(packet) > _MAX_PACKET:
                continue
            if packet == f'PURCHASE_HELLO|{self._lease}'.encode('ascii') and self._peer is None:
                self._peer = source
                continue
            attempt = self._attempt
            if source != self._peer or attempt is None or not attempt.sent:
                continue
            marker = f'RECEIPT|{self._lease}|{attempt.id}|'.encode('ascii')
            if not packet.startswith(marker):
                continue
            if self._clock() >= attempt.deadline:
                return self._record(attempt, 'unknown', reason='native_receipt_timeout')
            sequence, _, row_bytes = packet[len(marker):].partition(b'|')
            if sequence != str(len(attempt.rows) + 1).encode('ascii'):
                return self._record(attempt, 'unknown', reason='native_receipt_sequence_gap')
            try:
                row = row_bytes.decode('ascii')
            except UnicodeDecodeError:
                return self._record(attempt, 'unknown', reason='malformed_native_receipt')
            if not row or not row.isprintable() or len(attempt.rows) >= _MAX_RECEIPTS:
                return self._record(attempt, 'unknown', reason='receipt_limit')
            attempt.rows.append(row)
            if row == 'DONE':
                return self._settle(attempt)

        attempt = self._attempt
        if attempt is None:
            return None
        if self._clock() >= attempt.deadline:
            if attempt.sent:
                return self._record(attempt, 'unknown', reason='native_receipt_timeout')
            return self._record(attempt, 'refused', reason='provider_unavailable')
        if self._peer and not attempt.sent:
            fields = [attempt.binding[key] for key in _BINDING_FIELDS]
            fields.extend(attempt.request[key] for key in _BASKET_FIELDS)
            packet = '|'.join(map(str, (attempt.kind.upper(), self._lease, attempt.id, *fields,
                                        self._previous_confirmed))).encode('ascii')
            attempt.sent = True
            try:
                if self._socket.sendto(packet, self._peer) != len(packet):
                    return self._record(attempt, 'unknown', reason='short_native_dispatch')
            except OSError:
                return self._record(attempt, 'unknown', reason='native_dispatch_error')
        return None

    def close(self):
        self._socket.close()
