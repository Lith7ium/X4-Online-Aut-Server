"""Issue one guarded native Company MoveWait through the owned X4 process."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import socket
import time

from authority.company_fleet_orders import fleet_order, fleet_receipt
from authority_tcp_job_client_live import socket_owner


_SCRIPT = 'authority_native_fleet_order'
_TOKEN = re.compile(r'[0-9a-f]{32}')
_ACTOR = re.compile(r'0[xX]0*[1-9a-fA-F][0-9a-fA-F]*')
_REFUSALS = {'invalid_registry', 'identity_mismatch', 'unavailable_ship', 'captain_required',
             'sector_mismatch', 'ship_busy', 'provider_limit'}
_MAX_PACKET = 512
_MAX_ATTEMPTS = 4096


@dataclass
class _Attempt:
    order: dict
    epoch: str
    deadline: float

    @property
    def identity(self):
        return self.epoch, str(self.order['ship_index']), self.order['ship_code'], self.order['sector']


class NativeFleetOrderProvider:
    """Serve one serialized command at a time from the supervisor thread.

    A terminal result is retained for duplicate calls during this process.
    After an ambiguous dispatch this provider is fenced for its lifetime.
    The saved MD journal covers only attempts present in the restored save.
    The supervisor owns receipt recovery when restoring an earlier checkpoint.
    """

    def __init__(self, *, clock=time.monotonic, timeout=12):
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError('invalid native fleet order timeout')
        self._clock, self._timeout = clock, timeout
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._lease = secrets.token_hex(16)
        self._process = self._guard = self._peer = self._active = None
        self._staged_md = None
        self._history = {}
        self._fenced = False

    def install(self, case):
        """Add the scripts to the private, registry-aware provider overlay."""
        extension = Path(case).absolute() / 'extensions/x4_lan_lab'
        ui, md = extension / 'ui', extension / 'md'
        if (self._staged_md is not None or extension.resolve(strict=True) != extension
                or extension.stat().st_uid != os.geteuid()
                or any(path.resolve(strict=True) != path or not path.is_dir() for path in (ui, md))):
            raise ValueError('new owned native fleet order overlay required')
        bridge = ui / 'authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        if any(path.resolve(strict=True) != path or not path.is_file() for path in (bridge, manifest)):
            raise ValueError('native fleet orders require ordinary provider overlay files')
        bridge_text, manifest_text = bridge.read_text(encoding='ascii'), manifest.read_text(encoding='ascii')
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        entry = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        if (bridge_text.count(tick) != 1 or 'local registry_checkpoint' not in bridge_text
                or manifest_text.count(entry) != 1):
            raise ValueError('native fleet orders require a registry-aware provider')
        cue, lua = md / f'{_SCRIPT}.xml', ui / f'{_SCRIPT}.lua'
        if any(path.exists() or path.is_symlink() for path in (cue, lua)):
            raise FileExistsError('native fleet order scripts already present')
        source = Path(__file__).resolve().parent
        xml = (source / cue.name).read_bytes()
        if xml.count(b'__PROVIDER_LEASE__') != 1:
            raise ValueError('native fleet order cue requires one provider lease binding')
        xml = xml.replace(b'__PROVIDER_LEASE__', self._lease.encode('ascii'))
        with cue.open('xb') as output:
            output.write(xml)
        with lua.open('xb') as output:
            output.write((source / lua.name).read_bytes())
        self._staged_md = cue, hashlib.sha256(xml).digest()
        port = self._socket.getsockname()[1]
        hook = f""" if registry_checkpoint:ready() then
  if not native_fleet_order_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   native_fleet_order_tick = X4LAN_AUTHORITY_NATIVE_FLEET_ORDER.new(
       "{self._lease}", assert(lib.open('127.0.0.1',{port})), lib.monotonic)
  end
  native_fleet_order_tick(C.IsGamePaused())
 end
"""
        bridge.write_text('local native_fleet_order_tick\n' + bridge_text.replace(tick, tick + hook), encoding='ascii')
        manifest.write_text(manifest_text.replace(entry, f'    <file name="ui/{lua.name}" />\n' + entry),
                            encoding='ascii')
        return [cue, lua, bridge, manifest]

    def archive_cue(self, case, out):
        """Retain the exact installed cue after the caller proves process cleanup."""
        case, out = Path(case).absolute(), Path(out).absolute()
        if case != out / 'case' or self._staged_md is None:
            raise ValueError('native fleet order archive must belong to its stopped run')
        source, expected = self._staged_md
        if (source != case / 'extensions/x4_lan_lab/md' / f'{_SCRIPT}.xml' or not source.is_file()
                or source.resolve(strict=True) != source or hashlib.sha256(source.read_bytes()).digest() != expected):
            raise ValueError('staged native fleet order cue changed')
        destination = out / source.name
        os.link(source, destination, follow_symlinks=False)
        source.unlink()
        return destination

    def bind(self, process, guard):
        fields = {'pid', 'start_ticks', 'exe_dev', 'exe_ino'}
        if self._process is not None or not callable(guard) or type(process) is not dict or process.keys() != fields:
            raise ValueError('native fleet orders require one owned process')
        self._process, self._guard = dict(process), guard

    def _owns(self, peer):
        try:
            return (self._process is not None and self._guard() is not False
                    and socket_owner(peer, self._socket.getsockname(), self._process))
        except (OSError, RuntimeError):
            return False  # The supervisor independently checks the owned game's lifetime.

    def begin(self, binding, order):
        order = fleet_order(order)
        epoch = binding.get('registry_epoch') if type(binding) is dict else None
        if type(epoch) is not str or not _TOKEN.fullmatch(epoch):
            raise ValueError('native fleet order requires a valid registry epoch')
        if self._socket.fileno() < 0:
            raise RuntimeError('native fleet order provider is closed')
        order_id = order['order_id']
        if order_id in self._history:
            previous, receipt = self._history[order_id]
            if previous.epoch != epoch or previous.order != order:
                raise ValueError('native fleet order ID reused')
            return dict(receipt)
        if self._active is not None:
            if self._active.order['order_id'] != order_id:
                raise RuntimeError('native fleet order already active')
            if self._active.epoch != epoch or self._active.order != order:
                raise ValueError('native fleet order ID reused')
            return None
        if self._fenced or len(self._history) >= _MAX_ATTEMPTS:
            return fleet_receipt(dict(order, outcome='refused', reason='provider_limit'))

        self.poll()  # Receive the owned peer's greeting before attempting dispatch.
        attempt = _Attempt(order, epoch, self._clock() + self._timeout)
        self._active = attempt
        if self._peer is None or not self._owns(self._peer):
            return self._finish('refused', 'provider_unavailable')
        coordinates = (format(order[axis], '.17g') for axis in ('x', 'y', 'z'))
        packet = '|'.join(('FLEET_ORDER', self._lease, order_id, *attempt.identity,
                           *coordinates, format(attempt.deadline, '.17g'))).encode('ascii')
        try:
            if self._socket.sendto(packet, self._peer) == len(packet):
                return None
        except OSError:
            pass
        return self._finish('unknown')

    def _finish(self, outcome, reason=None):
        attempt, self._active = self._active, None
        receipt = dict(attempt.order, outcome=outcome)
        if reason is not None:
            receipt['reason'] = reason
        receipt = fleet_receipt(receipt)
        self._history[attempt.order['order_id']] = attempt, receipt
        if outcome == 'unknown':
            self._fenced = True
        return dict(receipt)

    def _native_result(self, row):
        fields = row.split('|')
        if len(fields) != 8 or tuple(fields[:4]) != self._active.identity:
            return self._finish('unknown')
        status, order_name, ship, captain = fields[4:]
        if (status == 'accepted' and order_name == 'MoveWait'
                and all(len(actor) <= 18 and _ACTOR.fullmatch(actor) for actor in (ship, captain))
                and int(ship, 16) != int(captain, 16)):
            return self._finish('accepted')
        if status in _REFUSALS and (order_name, ship, captain) == ('-', '0', '0'):
            return self._finish('refused', status)
        return self._finish('unknown')

    def poll(self):
        if self._socket.fileno() < 0:
            return None
        if self._active is not None and self._clock() >= self._active.deadline:
            return self._finish('unknown')
        hello = f'FLEET_HELLO|{self._lease}'.encode('ascii')
        for _ in range(32):
            try:
                packet, source = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                break
            if self._peer is None and packet == hello and self._owns(source):
                self._peer = source
            if self._active is None or source != self._peer:
                continue
            prefix = f"FLEET_RESULT|{self._lease}|{self._active.order['order_id']}|".encode('ascii')
            if not packet.startswith(prefix):
                continue
            if len(packet) > _MAX_PACKET or not self._owns(source) or self._clock() >= self._active.deadline:
                return self._finish('unknown')
            try:
                row = packet[len(prefix):].decode('ascii')
            except UnicodeDecodeError:
                return self._finish('unknown')
            return self._native_result(row)
        return None

    def close(self):
        self._socket.close()
        if self._active is not None:
            return self._finish('unknown')
