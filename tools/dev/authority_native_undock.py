"""Bridge the existing one-shot stock undock cue to the owned provider."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import secrets
import socket
import time

from authority.company_undock_messages import undock_request
from authority_native_purchase import _valid_binding
from authority_tcp_job_client_live import socket_owner


_CUE = 'authority_company_undock_trial.xml'
_DRIVER = 'authority_native_undock.lua'
_ACTOR = re.compile(r'0[xX][0-9a-fA-F]{1,16}')
_SEED = re.compile(r'(?:0|-?[1-9][0-9]{0,18})')
_FIELDS = ('sector', 'station_code', 'bay_seed', 'bay_macro', 'dockarea_macro')
_REFUSALS = {'invalid_request', 'identity_mismatch', 'duplicate_or_attempted', 'berth_mismatch', 'busy'}
_MAX_PACKET = 1024
_MAX_REQUESTS = 1024


# Kept next to its only installer. The unchanged MD cue owns all engine
# decisions. This connector forwards at most two local result rows per request.
_LUA = r'''
local M = {}
local screen = 'X4AuthorityCompanyUndockTrial'

function M.new(lease, net)
    local active, attempted, next_hello
    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or type(row) ~= 'string' or #row > 940
                or row:sub(1, 33) ~= active .. '|' then return end
        net:send('UNDOCK_RESULT|' .. lease .. '|' .. row)
        if row:sub(34, 42) ~= 'awaiting|' then active = nil end
    end) ~= false)

    return function(paused)
        local now = GetCurRealTime()
        if not next_hello or now >= next_hello then
            net:send('UNDOCK_HELLO|' .. lease)
            next_hello = now + .5
        end
        if paused or active or not net:pending() then return end
        local value = net:receive()
        if type(value) ~= 'string' or #value > 1024 then return end
        local fields = {}
        for item in (value .. '|'):gmatch('(.-)|') do fields[#fields + 1] = item end
        if #fields ~= 12 or fields[1] ~= 'UNDOCK' or fields[2] ~= lease
                or #fields[3] ~= 32 or attempted == fields[3] then return end
        local index = tonumber(fields[5])
        if not index or index < 1 or index > 2147483647 or index % 1 ~= 0 then return end
        active, attempted = fields[3], fields[3]
        AddUITriggeredEvent(screen, 'Run', {
            nonce=active, epoch=fields[4], index=index, ship_code=fields[6],
            sector=fields[7], station_code=fields[8], bay_seed=fields[9],
            bay_macro=fields[10], dockarea_macro=fields[11], captain_seed=fields[12],
        })
    end
end
X4LAN_AUTHORITY_NATIVE_UNDOCK = M
'''


@dataclass
class _Attempt:
    request: dict
    identity: tuple[str, ...]
    deadline: float
    actors: tuple[str, ...] | None = None


class NativeUndockProvider:
    """One owned command at a time. An unknown dispatch fences this connector."""

    def __init__(self, *, clock=time.monotonic, timeout=60):
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError('invalid native undock timeout')
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
        self._history = {}
        self._fenced = self._staged = False

    def install(self, case):
        """Stage the original native cue in a privately owned provider view."""
        extension = Path(case).absolute() / 'extensions/x4_lan_lab'
        if (self._staged or extension.resolve(strict=True) != extension
                or extension.stat().st_uid != os.geteuid()):
            raise ValueError('new native undock overlay required')
        bridge = extension / 'ui/authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        if any(path.resolve(strict=True) != path or not path.is_file() for path in (bridge, manifest)):
            raise ValueError('native undock requires ordinary private overlay files')
        bridge_text, manifest_text = bridge.read_text(encoding='ascii'), manifest.read_text(encoding='ascii')
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        entry = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        if bridge_text.count(tick) != 1 or 'local registry_checkpoint' not in bridge_text or manifest_text.count(entry) != 1:
            raise ValueError('native undock requires a registry-aware provider')
        cue, lua = extension / 'md' / _CUE, extension / 'ui' / _DRIVER
        if any(path.exists() or path.is_symlink() for path in (cue, lua)):
            raise FileExistsError('native undock scripts already present')
        with cue.open('xb') as output:
            output.write(Path(__file__).with_name(_CUE).read_bytes())
        with lua.open('x', encoding='ascii') as output:
            output.write(_LUA)
        port = self._socket.getsockname()[1]
        hook = f""" if registry_checkpoint:ready() then
  if not native_undock_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   native_undock_tick = X4LAN_AUTHORITY_NATIVE_UNDOCK.new("{self._lease}", assert(lib.open('127.0.0.1',{port})))
  end
  native_undock_tick(C.IsGamePaused())
 end
"""
        bridge.write_text('local native_undock_tick\n' + bridge_text.replace(tick, tick + hook), encoding='ascii')
        manifest.write_text(manifest_text.replace(entry, f'    <file name="ui/{_DRIVER}" />\n' + entry),
                            encoding='ascii')
        self._staged = True
        return [cue, lua, bridge, manifest]

    def bind(self, process, guard):
        required = {'pid', 'start_ticks', 'exe_dev', 'exe_ino'}
        if self._process is not None or not callable(guard) or not required <= process.keys():
            raise ValueError('native undock requires one owned process')
        self._process, self._guard = dict(process), guard

    def begin(self, binding, request):
        request = undock_request(request)
        seed = binding.get('captain_seed')
        if (not _valid_binding(binding) or type(seed) is not str or not _SEED.fullmatch(seed)
                or not -(1 << 63) <= int(seed) < (1 << 63)):
            raise ValueError('native undock requires a valid saved captain binding')
        identity = (binding['registry_epoch'], str(binding['ship_index']), binding['ship_code'],
                    *(request[field] for field in _FIELDS), seed)
        request_id = request['request_id']
        if request_id in self._history:
            previous, receipt = self._history[request_id]
            if previous.identity != identity or previous.request != request:
                raise ValueError('native undock request ID reused')
            return dict(receipt)
        if self._active is not None:
            raise RuntimeError('native undock operation already active')
        if self._fenced or len(self._history) >= _MAX_REQUESTS:
            return dict(request, outcome='refused', reason='provider_limit')
        self.poll()
        if self._peer is None or not self._owns(self._peer):
            return dict(request, outcome='refused', reason='provider_unavailable')
        self._active = _Attempt(request, identity, self._clock() + self._timeout)
        packet = '|'.join(('UNDOCK', self._lease, request_id, *identity)).encode('ascii')
        try:
            if self._socket.sendto(packet, self._peer) == len(packet):
                return None
        except OSError:
            pass
        return self._finish('unknown')

    def _owns(self, peer):
        return (self._process is not None and self._guard() is not False
                and socket_owner(peer, self._socket.getsockname(), self._process))

    def _finish(self, outcome, reason=None):
        attempt, self._active = self._active, None
        receipt = dict(attempt.request, outcome=outcome)
        if reason is not None:
            receipt['reason'] = reason
        self._history[attempt.request['request_id']] = (attempt, receipt)
        if outcome == 'unknown':
            self._fenced = True
        return dict(receipt)

    def _native_result(self, row):
        active = self._active
        fields = row.split('|')
        if len(fields) != 18 or tuple(fields[2:11]) != active.identity:
            return self._finish('unknown')
        status, witness = fields[1], fields[11]
        actors = tuple(fields[14:])
        if active.actors is None:
            if status in _REFUSALS and witness == '0':
                return self._finish('refused', status)
            if status == 'awaiting' and witness == '0' and all(_ACTOR.fullmatch(ref) for ref in actors):
                active.actors = actors
                return None
        elif status == 'undocked' and witness == '1' and actors == active.actors:
            return self._finish('confirmed')
        return self._finish('unknown')

    def poll(self):
        if self._active is not None and self._clock() >= self._active.deadline:
            return self._finish('unknown')
        hello = f'UNDOCK_HELLO|{self._lease}'.encode('ascii')
        for _ in range(64):
            try:
                packet, source = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                break
            if self._peer is None and packet == hello and self._owns(source):
                self._peer = source
            active = self._active
            if source != self._peer or active is None:
                continue
            prefix = f"UNDOCK_RESULT|{self._lease}|{active.request['request_id']}|".encode('ascii')
            if not packet.startswith(prefix):
                continue
            if (len(packet) > _MAX_PACKET or not self._owns(source)
                    or self._clock() >= active.deadline):
                return self._finish('unknown')
            try:
                row = packet.split(b'|', 2)[2].decode('ascii')
            except UnicodeDecodeError:
                return self._finish('unknown')
            if (receipt := self._native_result(row)) is not None:
                return receipt
        return None

    def close(self):
        self._socket.close()
