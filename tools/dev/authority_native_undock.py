"""Bridge the existing one-shot stock undock cue to the owned provider."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import re
import secrets
import socket
import time

from authority.company_undock_messages import undock_request
from authority.marker_evidence import _parse
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
_QUIESCENT_STATUSES = _REFUSALS | {'idle', 'undocked'}


def verify_saved_undock_quiescent(checkpoint):
    """Recognize the saved shape of the old stock cue and refuse pending undock work."""
    script_path = ['savegame', 'md', 'script']
    state_path = script_path + ['cue']
    request_path = state_path + ['cue']
    instance_path = request_path + ['instances', 'cue']
    fence_path = state_path + ['vars', 'value']
    status_path = instance_path + ['vars', 'value']
    in_script = in_state = in_request = False
    scripts = states = requests = fences = 0
    instances = []
    components = []

    def inspect(path, attributes):
        nonlocal in_script, in_state, in_request, scripts, states, requests, fences
        depth = len(path)
        while components and components[-1][0] >= depth:
            components.pop()
        if depth > 2 and path[1] == 'universe' and path[-1] == 'component':
            components.append((depth, attributes.get('owner')))
        if (path[-2:] == ['orders', 'order'] and attributes.get('order') == 'Undock'
                and components and components[-1][1] == 'player'):
            raise ValueError('saved player Undock order is still queued')
        if path == script_path:
            in_script = attributes.get('name') == 'X4AuthorityCompanyUndockTrial'
            in_state = in_request = False
            scripts += in_script
        elif not in_script:
            return
        elif path == state_path:
            in_state = attributes.get('name') == 'State'
            in_request = False
            if in_state:
                states += 1
                if attributes.get('state') != 'complete':
                    raise ValueError('saved undock state is not initialized')
        elif in_state and path == fence_path and attributes.get('name') == '$AttemptedNonce':
            fences += 1
            if attributes.get('type') != 'string':
                raise ValueError('invalid saved undock fence')
        elif in_state and path == request_path:
            in_request = attributes.get('name') == 'Request'
            if in_request:
                requests += 1
                if attributes.get('state') != 'waiting':
                    raise ValueError('saved undock request listener is not waiting')
        elif in_request and path == instance_path:
            if attributes.get('state') != 'complete' or len(instances) >= _MAX_REQUESTS:
                raise ValueError('saved undock request is not complete')
            instances.append([])
        elif in_request and path == status_path and attributes.get('name') == '$Status':
            reference = attributes.get('value', '')
            if attributes.get('type') != 'string' or not re.fullmatch(r'[0-9]{1,32}', reference):
                raise ValueError('invalid saved undock request status')
            instances[-1].append(reference)

    _parse(checkpoint, inspect)
    if (scripts, states, requests, fences) != (1, 1, 1, 1) or any(len(status) != 1 for status in instances):
        raise ValueError('unrecognized saved undock cue state')
    references = {status[0] for status in instances}
    if not references:
        return
    confirmed = set()
    string_table = False

    def read_status(path, attributes):
        nonlocal string_table
        if path == ['savegame', 'script', 'refs']:
            string_table = attributes.get('type') == 'string'
        if string_table and path == ['savegame', 'script', 'refs', 'ref']:
            reference = attributes.get('id')
            if reference not in references:
                return
            if reference in confirmed or attributes.get('string') not in _QUIESCENT_STATUSES:
                raise ValueError('saved undock request status is not quiescent')
            confirmed.add(reference)

    _parse(checkpoint, read_status)
    if confirmed != references:
        raise ValueError('saved undock request status reference is missing')


# Kept next to its only installer. The unchanged MD cue owns all engine
# decisions. The connector waits only before dispatch and forwards at most two
# result rows per request after the read-only checks.
_LUA = r'''
local M = {}
local screen = 'X4AuthorityCompanyUndockTrial'
local PRECHECK_SECONDS, CHECK_INTERVAL = 8, .25

local function split(value)
    local fields = {}
    for item in (value .. '|'):gmatch('(.-)|') do fields[#fields + 1] = item end
    return fields
end

local function actor_tuple(fields)
    for i = 15, 18 do
        local ref = fields[i]
        if #ref > 18 or not ref:match('^0[xX][0-9a-fA-F]+$') or ref:match('^0[xX]0+$') then return end
    end
    return {fields[15], fields[16], fields[17], fields[18]}
end

function M.new(lease, net, monotonic)
    local active, attempted, next_hello

    local function forward(row)
        net:send('UNDOCK_RESULT|' .. lease .. '|' .. row)
    end

    local function issue(now)
        active.sequence = active.sequence + 1
        active.nonce = active.id .. '-' .. active.sequence
        local fields, actors = active.fields, active.actors
        local request = {
            nonce=active.nonce, epoch=fields[4], index=active.index, ship_code=fields[6],
            sector=fields[7], station_code=fields[8], bay_seed=fields[9],
            bay_macro=fields[10], dockarea_macro=fields[11], captain_seed=fields[12],
            check_only=actors == nil,
        }
        if actors then
            active.sent = true  -- No second dispatch, even if the callback never arrives.
            request.checked_ship, request.checked_captain, request.checked_station, request.checked_bay =
                actors[1], actors[2], actors[3], actors[4]
        else
            active.due = now + CHECK_INTERVAL
        end
        AddUITriggeredEvent(screen, 'Run', request)
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or not active.nonce or type(row) ~= 'string' or #row > 940
                or row:sub(1, #active.nonce + 1) ~= active.nonce .. '|' then return end
        local result = active.id .. row:sub(#active.nonce + 1)
        local fields = split(row)
        if active.sent then
            forward(result)
            if fields[2] ~= 'awaiting' then active = nil end
            return
        end
        active.nonce = nil
        local matches = #fields == 18 and fields[12] == '0'
        for i = 3, 11 do
            matches = matches and fields[i] == active.fields[i + 1]
        end
        if matches and fields[2] == 'busy' then
            active.busy = result
            return
        end
        if matches and fields[2] == 'idle' then
            active.actors = actor_tuple(fields)
            if active.actors then return end
        end
        forward(result)
        active = nil
    end) ~= false)

    local function receive(now)
        if not net:pending() then return end
        local value = net:receive()
        if type(value) ~= 'string' or #value > 1024 then return end
        local fields = split(value)
        if #fields ~= 13 or fields[1] ~= 'UNDOCK' or fields[2] ~= lease
                or #fields[3] ~= 32 or attempted == fields[3] then return end
        local index = tonumber(fields[5])
        local deadline = tonumber(fields[13])
        if not index or index < 1 or index > 2147483647 or index % 1 ~= 0
                or not deadline or not (deadline > now and deadline <= now + 120) then return end
        attempted = fields[3]
        active = {id=attempted, fields=fields, index=index, sequence=0, due=now,
                  deadline=math.min(deadline, now + PRECHECK_SECONDS)}
    end

    return function(paused)
        local now = monotonic()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge) then return end
        if not next_hello or now >= next_hello then
            net:send('UNDOCK_HELLO|' .. lease)
            next_hello = now + .5
        end
        if paused then return end
        if not active then receive(now) end
        if not active or active.sent then return end
        if now >= active.deadline then
            if active.busy then forward(active.busy) end
            active = nil
        elseif not active.nonce and (active.actors or now >= active.due) then
            issue(now)
        end
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
        self._fenced = False
        self._staged_cue = None

    def install(self, case):
        """Stage the original native cue in a privately owned provider view."""
        extension = Path(case).absolute() / 'extensions/x4_lan_lab'
        if (self._staged_cue is not None or extension.resolve(strict=True) != extension
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
        cue_bytes = Path(__file__).with_name(_CUE).read_bytes()
        with cue.open('xb') as output:
            output.write(cue_bytes)
        with lua.open('x', encoding='ascii') as output:
            output.write(_LUA)
        port = self._socket.getsockname()[1]
        hook = f""" if registry_checkpoint:ready() then
  if not native_undock_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   native_undock_tick = X4LAN_AUTHORITY_NATIVE_UNDOCK.new(
       "{self._lease}", assert(lib.open('127.0.0.1',{port})), lib.monotonic)
  end
  native_undock_tick(C.IsGamePaused())
 end
"""
        bridge.write_text('local native_undock_tick\n' + bridge_text.replace(tick, tick + hook), encoding='ascii')
        manifest.write_text(manifest_text.replace(entry, f'    <file name="ui/{_DRIVER}" />\n' + entry),
                            encoding='ascii')
        self._staged_cue = (cue, hashlib.sha256(cue_bytes).digest())
        return [cue, lua, bridge, manifest]

    def archive_cue(self, case, out):
        """Archive the exact installed cue after the caller confirms X4 stopped."""
        case, out = Path(case).absolute(), Path(out).absolute()
        if case != out / 'case' or self._staged_cue is None:
            raise ValueError('native undock archive must belong to its stopped run')
        source, expected = self._staged_cue
        if (source != case / 'extensions/x4_lan_lab/md' / _CUE or not source.is_file()
                or source.resolve(strict=True) != source or hashlib.sha256(source.read_bytes()).digest() != expected):
            raise ValueError('staged native undock cue changed')
        archive = out / _CUE
        os.link(source, archive, follow_symlinks=False)
        source.unlink()
        return archive

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
        deadline = self._clock() + self._timeout
        self._active = _Attempt(request, identity, deadline)
        packet = '|'.join(('UNDOCK', self._lease, request_id, *identity, format(deadline, '.17g'))).encode('ascii')
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
