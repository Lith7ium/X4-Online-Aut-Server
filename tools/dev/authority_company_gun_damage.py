"""Opt-in stock damage and health observation RPC to a running provider X4."""

from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import socket
import time
from typing import Callable

from authority.company_combat_frames import MAX_BATCH
from authority.company_combat_service import CombatImpactRejected
from authority_tcp_job_client_live import socket_owner


_SCRIPT = 'authority_company_gun_damage'
_TOKEN = re.compile(r'[0-9a-f]{32}')
_ACTOR = re.compile(r'0[xX][0-9a-fA-F]{1,16}')
_MAX_PACKET = 512
_GROUP_CHUNK = 8
_MAX_INDEX = 200000
_MAX_SERIAL = 2**31 - 1
_MAX_AMOUNT = 2**53 - 1
_HEALTH_TIMEOUT = 2
_HEALTH_FIELDS = ('shield', 'maxshield', 'hull', 'maxhull')


class NativeGunDamageUncertain(RuntimeError):
    """The provider cannot rule out a native write. Further writes are fenced."""


@dataclass
class _Impact:
    provider: 'NativeGunDamageProvider'
    binding: tuple
    deadline: float
    response_prefix: bytes
    read_receipt: Callable
    health: tuple | None = None
    error: Exception | None = None

    def poll(self):
        if self.health is None and self.error is None:
            self.provider._poll_impact(self)
        if self.error is not None:
            raise self.error
        return self.health


@dataclass
class _HealthRead:
    provider: 'NativeGunDamageProvider'
    binding: tuple
    deadline: float
    response_prefix: bytes
    slot: int
    receipt: bytes | None = None
    health: dict | None = None
    error: ValueError | None = None

    def poll(self):
        if self.provider._health_reads.get(self.slot) is self:
            self.provider._poll_health(self)
        if self.error is not None:
            raise self.error
        return None if self.health is None else dict(self.health)

    def cancel(self):
        if self.provider._health_reads.get(self.slot) is self:
            self.provider._fail_health_read(self, 'native health read cancelled')


def _bounded_index(value):
    return type(value) is int and 0 < value <= _MAX_INDEX


def _bounded_amount(value):
    return type(value) in (int, float) and 0 < value <= _MAX_AMOUNT and math.isfinite(value)


def _valid_binding(binding):
    return (type(binding) is tuple and len(binding) == 3
            and all(type(token) is str and _TOKEN.fullmatch(token) for token in binding[:2])
            and _bounded_index(binding[2]))


def _health_sample(fields):
    if (len(fields) != 6 or fields[0] != 'observed' or not _ACTOR.fullmatch(fields[1])
            or int(fields[1], 16) == 0):
        raise ValueError('invalid native health receipt')
    try:
        shield, maxshield, hull, maxhull = values = tuple(map(float, fields[2:]))
    except ValueError:
        raise ValueError('invalid native health receipt numbers') from None
    if not (0 <= shield <= maxshield <= _MAX_AMOUNT and 0 < hull <= maxhull <= _MAX_AMOUNT):
        raise ValueError('invalid native living ship health')
    return dict(zip(_HEALTH_FIELDS, values, strict=True))


def _same_health(first, second):
    return all(math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-6)
               for a, b in zip(first, second, strict=True))


def _stock_impact(fields, amount):
    if (len(fields) != 15 or fields[0] != 'applied'
            or not all(_ACTOR.fullmatch(actor) for actor in fields[1:3])):
        raise NativeGunDamageUncertain('invalid native impact receipt')
    source, target = (int(actor, 16) for actor in fields[1:3])
    if not source or not target or source == target or fields[3] not in {'0', '1', 'false', 'true'}:
        raise NativeGunDamageUncertain('invalid native impact actor or result')
    killed = fields[3] in {'1', 'true'}
    values = tuple(map(float, fields[4:]))
    if any(not math.isfinite(value) or not 0 <= value <= _MAX_AMOUNT for value in values):
        raise NativeGunDamageUncertain('invalid native impact health')
    reported_amount, before_shield, before_hull, shield, hull, max_shield, max_hull = values[:7]
    observed_shield, observed_hull, observed_max_shield, observed_max_hull = values[7:]
    if (reported_amount != amount or not 0 < before_hull or not 0 < max_hull
            or shield > min(before_shield, max_shield) or hull > min(before_hull, max_hull)
            or observed_shield > observed_max_shield or observed_hull > observed_max_hull):
        raise NativeGunDamageUncertain('native stock impact reported impossible root health')
    if killed != (hull == 0) or (killed and shield != 0):
        raise NativeGunDamageUncertain('native kill does not have matching root health')
    return source, target, values[1:3], values[3:7], values[7:]


def _read_health(fields, amount):
    _, _, _, after, observed = _stock_impact(fields, amount)
    # Once X4 transitions a killed ship into a wreck (~7.5-7.8 ms after impact),
    # GetComponentData reports zero maxima alongside zero shield and zero hull.
    wreck_readback = after[:2] == (0, 0) and observed == (0, 0, 0, 0)
    if ((after[1] == 0) != (observed[1] == 0) or (after[1] == 0 and observed[0] != 0)
            or (not wreck_readback and not _same_health(after, observed))):
        raise NativeGunDamageUncertain('native impact health readbacks disagree')
    return observed[:2]


@dataclass
class _GroupReceipt:
    impacts: tuple
    rows: list = field(default_factory=list)
    last: dict = field(default_factory=dict)
    source: int | None = None
    age: float | None = None

    def __call__(self, fields):
        if fields == ['complete']:
            if len(self.rows) != len(self.impacts) or any(
                    not _same_health(after, observed) for _, after, observed in self.last.values()):
                raise NativeGunDamageUncertain('incomplete or inconsistent native group')
            # Establish each target's final read independently. Intermediate
            # receipts retain the native health immediately after that call.
            return tuple(after[:2] for after in self.rows)
        position = len(self.rows)
        if (len(fields) != 19 or fields[:2] != ['row', str(position + 1)]
                or position >= len(self.impacts) or fields[2] != str(self.impacts[position][0])):
            raise NativeGunDamageUncertain('native group receipt order differs')
        target_index, amount = self.impacts[position]
        age = float(fields[3])
        source, target, before, after, observed = _stock_impact(fields[4:], amount)
        if (not math.isfinite(age) or age < 0 or after[1] <= 0 or observed[1] <= 0
                or (self.age is not None and (age != self.age or source != self.source))):
            raise NativeGunDamageUncertain('native group game age, source or living target differs')
        previous = self.last.get(target_index)
        if previous is not None:
            old_target, old_after, old_observed = previous
            if (target != old_target or not _same_health(before, old_after[:2])
                    or not _same_health(after[2:], old_after[2:])
                    or not _same_health(observed, old_observed)):
                raise NativeGunDamageUncertain('native group target history differs')
        elif any(actor == target for actor, _, _ in self.last.values()):
            raise NativeGunDamageUncertain('native group alias between target indices')
        self.source, self.age = source, age
        self.last[target_index] = target, after, observed
        self.rows.append(after)
        return None


class NativeGunDamageProvider:
    """Serialize stock writes and allow up to four independent read-only health observations.

    The combat service owns confirmation identity, ordering and deduplication.
    is_current checks the assignment and can_fire adds input freshness before
    dispatch. world_current optionally fences which world can settle the
    receipt, even if the firing pilot has departed since dispatch. The owner
    pumps the returned operation without blocking TLS or motion. Damage takes
    precedence over cancellable, read-only health observations.
    """

    max_health_reads = 4

    def __init__(self, is_current, *, can_fire=None, world_current=None, timeout=4, clock=time.monotonic):
        if (not callable(is_current) or type(timeout) not in (int, float)
                or not 0 < timeout <= 30 or not callable(clock)
                or (can_fire is not None and not callable(can_fire))
                or (world_current is not None and not callable(world_current))):
            raise ValueError('native impact requires an active binding check and bounded deadline')
        self._is_current = is_current
        self._can_fire = can_fire
        self._world_current = world_current
        self._timeout, self._clock = timeout, clock
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._lease = secrets.token_hex(16)
        self._serial = self._health_serial = 0
        self._peer = self._process = self._guard = self._staged_md = self._active = None
        self._health_reads = {}
        self._fenced = False
        self._ordered_hits = False

    def install(self, case, *, ordered_hits=False):
        """Stage in the newly owned registry-aware overlay before native launch."""
        if type(ordered_hits) is not bool:
            raise ValueError('ordered native damage requires explicit opt-in')
        extension = Path(case).absolute() / 'extensions/x4_lan_lab'
        ui, md = extension / 'ui', extension / 'md'
        if (self._staged_md is not None or extension.resolve(strict=True) != extension
                or extension.stat().st_uid != os.geteuid()
                or any(path.resolve(strict=True) != path or not path.is_dir() for path in (ui, md))):
            raise ValueError('new owned native damage overlay required')
        bridge = ui / 'authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        if any(path.resolve(strict=True) != path or not path.is_file() for path in (bridge, manifest)):
            raise ValueError('native damage requires ordinary recovered overlay files')
        bridge_text = bridge.read_text(encoding='ascii')
        manifest_text = manifest.read_text(encoding='ascii')
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        entry = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        if (bridge_text.count(tick) != 1 or 'local registry_checkpoint' not in bridge_text
                or manifest_text.count(entry) != 1):
            raise ValueError('native damage requires the registry-aware provider')

        root = Path(__file__).resolve().parents[2]
        scripts = Path(__file__).resolve().parent
        cue, driver, reader = md / f'{_SCRIPT}.xml', ui / f'{_SCRIPT}.lua', ui / 'combat_observer.lua'
        if any(path.exists() or path.is_symlink() for path in (cue, driver, reader)):
            raise FileExistsError('native damage overlay scripts already present')
        xml = (scripts / cue.name).read_bytes()
        for marker in (b'__PROVIDER_LEASE__', b'__PROVIDER_GROUP_LEASE__', b'__PROVIDER_HEALTH_LEASE__'):
            if xml.count(marker) != 1:
                raise ValueError('native combat cue lease placeholders differ')
            value = b'-' if marker == b'__PROVIDER_GROUP_LEASE__' and not ordered_hits else self._lease.encode('ascii')
            xml = xml.replace(marker, value)
        files = {
            cue: xml,
            driver: (scripts / driver.name).read_bytes(),
            reader: (root / 'src/authority/lua/combat_observer.lua').read_bytes(),
        }
        for path, content in files.items():
            with path.open('xb') as output:
                output.write(content)
        self._staged_md = cue, hashlib.sha256(xml).digest()
        port = self._socket.getsockname()[1]
        hook = f""" if registry_checkpoint:ready() then
  if not company_gun_damage_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   company_gun_damage_tick = X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE.new(
       "{self._lease}", assert(lib.open('127.0.0.1',{port})), lib.monotonic,
       C.IsGamePaused, X4LAN_COMBAT_OBSERVER.read_lua_health, engine.game_time, {str(ordered_hits).lower()})
  end
  company_gun_damage_tick()
 end
"""
        bridge.write_text('local company_gun_damage_tick\n' + bridge_text.replace(tick, tick + hook),
                          encoding='ascii')
        modules = ''.join(f'    <file name="ui/{path.name}" />\n' for path in (reader, driver))
        manifest.write_text(manifest_text.replace(entry, modules + entry), encoding='ascii')
        self._ordered_hits = ordered_hits
        return [*files, bridge, manifest]

    def bind(self, process, guard):
        fields = {'pid', 'start_ticks', 'exe_dev', 'exe_ino'}
        if (self._process is not None or not callable(guard)
                or type(process) is not dict or process.keys() != fields):
            raise ValueError('native damage requires one owned process')
        self._process, self._guard = dict(process), guard

    def archive_cues(self, case, out):
        """Keep the verified cue after the owner stops X4."""
        case, out = Path(case).absolute(), Path(out).absolute()
        if case != out / 'case' or self._staged_md is None:
            raise ValueError('native damage cue must belong to the stopped run')
        source, expected = self._staged_md
        if (source != case / 'extensions/x4_lan_lab/md' / f'{_SCRIPT}.xml' or not source.is_file()
                or source.resolve(strict=True) != source or hashlib.sha256(source.read_bytes()).digest() != expected):
            raise ValueError('staged native damage cue changed')
        destination = out / source.name
        os.link(source, destination, follow_symlinks=False)
        source.unlink()
        return destination

    def close(self):
        self._socket.close()
        self._cancel_health_reads('native health adapter closed during read')
        if self._active is not None:
            self._finish(error=NativeGunDamageUncertain('native damage adapter closed during impact'))

    def _owns_peer(self, peer):
        try:
            return (self._process is not None and self._guard() is not False
                    and socket_owner(peer, self._socket.getsockname(), self._process))
        except (OSError, RuntimeError):
            return False

    def _discover_peer(self):
        if self._peer is not None:
            return
        hello = f'GUN_HELLO|{self._lease}'.encode('ascii')
        for _ in range(32):
            try:
                packet, peer = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                return
            if packet == hello and self._owns_peer(peer):
                self._peer = peer
                return

    def begin_impact(self, binding, target_index, amount):
        if (not _valid_binding(binding) or not _bounded_index(target_index)
                or target_index == binding[2] or not _bounded_amount(amount)):
            raise CombatImpactRejected('invalid native impact binding or amount')
        identity, deadline = self._reserve_impact(binding)
        identity += f'|{target_index}'
        packet = f'GUN_IMPACT|{identity}|{amount:.17g}|{deadline:.17g}'.encode('ascii')
        attempt = _Impact(self, binding, deadline, f'GUN_RESULT|{identity}|'.encode('ascii'),
                          lambda fields: _read_health(fields, amount))
        return self._dispatch_impact(attempt, (packet,))

    def begin_impacts(self, binding, impacts):
        """Dispatch one ordered group. A refusal certifies zero stock calls."""
        if (not self._ordered_hits or not _valid_binding(binding) or type(impacts) is not tuple
                or not 1 <= len(impacts) <= MAX_BATCH
                or any(type(row) is not tuple or len(row) != 2 or not _bounded_index(row[0])
                       or row[0] == binding[2] or not _bounded_amount(row[1]) for row in impacts)):
            raise CombatImpactRejected('invalid native impact group')
        identity, deadline = self._reserve_impact(binding)
        # The stock UDP module caps each packet. Stage all chunks before the
        # single MD dispatch. A lost/reordered chunk never triggers a retry.
        packets = []
        for offset in range(0, len(impacts), _GROUP_CHUNK):
            rows = '|'.join(f'{target},{amount:.17g}' for target, amount in impacts[offset:offset + _GROUP_CHUNK])
            packet = f'GUN_GROUP|{identity}|{offset + 1}|{len(impacts)}|{deadline:.17g}|{rows}'.encode('ascii')
            assert len(packet) <= _MAX_PACKET
            packets.append(packet)
        attempt = _Impact(self, binding, deadline, f'GUN_GROUP_RESULT|{identity}|'.encode('ascii'),
                          _GroupReceipt(impacts))
        return self._dispatch_impact(attempt, packets)

    def _reserve_impact(self, binding):
        if self._fenced:
            raise NativeGunDamageUncertain('native damage adapter is fenced')
        if (self._active is not None or self._socket.fileno() < 0 or self._process is None
                or self._serial >= _MAX_SERIAL or self._is_current(binding) is not True):
            raise CombatImpactRejected('native impact requires a current running assignment')
        self._discover_peer()
        if self._peer is None or not self._owns_peer(self._peer):
            raise CombatImpactRejected('owned native damage endpoint is unavailable')
        if (self._is_current(binding) is not True
                or (self._can_fire is not None and self._can_fire(binding) is not True)):
            raise CombatImpactRejected('native impact requires a current firing assignment')
        self._cancel_health_reads('native health read superseded by an impact')
        self._serial += 1
        generation, epoch, source_index = binding
        identity = f'{self._lease}|{self._serial}|{generation}|{epoch}|{source_index}'
        return identity, self._clock() + self._timeout

    def _dispatch_impact(self, attempt, packets):
        self._active = attempt
        try:
            for packet in packets:
                if self._socket.sendto(packet, self._peer) != len(packet):
                    raise NativeGunDamageUncertain('native damage send was incomplete')
        except BaseException as error:
            failure = NativeGunDamageUncertain('native damage send outcome is unknown')
            self._finish(error=failure)
            if isinstance(error, Exception):
                raise failure from error
            raise
        return attempt

    def _health_current(self, binding):
        return (self._can_fire is not None and self._is_current(binding) is True
                and self._can_fire(binding) is True
                and (self._world_current is None or self._world_current(binding[1]) is True))

    def begin_health_read(self, binding):
        """Sample only the caller's current registered Company ship."""
        if (not _valid_binding(binding) or self._active is not None or self._socket.fileno() < 0
                or self._process is None or self._health_serial >= _MAX_SERIAL):
            raise ValueError('native health read unavailable')
        now = self._clock()
        for attempt in tuple(self._health_reads.values()):
            if now >= attempt.deadline:
                self._fail_health_read(attempt, 'native health read expired')
        if any(attempt.binding == binding for attempt in self._health_reads.values()):
            raise ValueError('native health read already pending for this binding')
        slot = next((slot for slot in range(1, self.max_health_reads + 1) if slot not in self._health_reads), None)
        if slot is None:
            raise ValueError('native health read capacity exhausted')
        try:
            self._discover_peer()
            if self._peer is None or not self._owns_peer(self._peer) or not self._health_current(binding):
                raise ValueError('native health read requires a current running Company helm')
        except Exception as error:
            raise ValueError('native health read endpoint or assignment unavailable') from error

        self._health_serial += 1
        generation, epoch, index = binding
        identity = f'{self._lease}|{self._health_serial}|{generation}|{epoch}|{index}'
        deadline = self._clock() + _HEALTH_TIMEOUT
        # A reused private slot supersedes a previous cue that may still be
        # pending inside X4. Its exact old receipt identity cannot satisfy this read.
        packet = f'GUN_HEALTH|{identity}|{deadline:.17g}|{slot}'.encode('ascii')
        attempt = _HealthRead(self, binding, deadline, f'GUN_HEALTH_RESULT|{identity}|'.encode('ascii'), slot)
        self._health_reads[slot] = attempt
        try:
            if self._socket.sendto(packet, self._peer) != len(packet):
                raise ValueError('native health read send incomplete')
        except BaseException as error:
            self._fail_health_read(attempt, 'native health read could not be sent')
            if isinstance(error, Exception):
                raise attempt.error from error
            raise
        return attempt

    def _fail_health_read(self, attempt, reason):
        del self._health_reads[attempt.slot]
        attempt.receipt = None
        attempt.error = reason if isinstance(reason, ValueError) else ValueError(reason)

    def _cancel_health_reads(self, reason):
        for attempt in tuple(self._health_reads.values()):
            self._fail_health_read(attempt, reason)

    def _check_health_read(self, attempt):
        if self._clock() >= attempt.deadline:
            raise ValueError('native health read expired')
        if not self._health_current(attempt.binding):
            raise ValueError('native health read lost its Company helm')

    def _poll_health(self, attempt):
        try:
            self._check_health_read(attempt)
            if attempt.receipt is None:
                self._receive_health()
            packet = attempt.receipt
            if packet is None:
                return
            # The socket may have been drained by a different operation. Fence
            # ownership and freshness here, immediately before this delivery.
            if len(packet) > _MAX_PACKET or not self._owns_peer(self._peer):
                raise ValueError('native health receipt lost its process fence')
            self._check_health_read(attempt)
            try:
                fields = packet[len(attempt.response_prefix):].decode('ascii').split('|')
            except UnicodeError:
                raise ValueError('invalid native health receipt encoding') from None
            if fields == ['refused']:
                raise ValueError('native registry or actor refused health read')
            attempt.health = _health_sample(fields)
            attempt.receipt = None
            del self._health_reads[attempt.slot]
        except Exception as error:
            self._fail_health_read(attempt, error if isinstance(error, ValueError)
                                   else 'native health read unavailable')

    def _receive_health(self):
        # One owned socket serves all health operations. Keep replies for other
        # live slots when the first caller drains the socket.
        for _ in range(32):
            try:
                packet, peer = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                return
            if peer != self._peer:
                continue
            for attempt in self._health_reads.values():
                if attempt.receipt is None and packet.startswith(attempt.response_prefix):
                    attempt.receipt = packet
                    break

    def _finish(self, *, health=None, error=None):
        attempt, self._active = self._active, None
        attempt.health, attempt.error = health, error
        if isinstance(error, NativeGunDamageUncertain):
            self._fenced = True

    def _poll_impact(self, attempt):
        if self._active is not attempt:
            raise RuntimeError('native impact operation is no longer active')
        try:
            if self._clock() >= attempt.deadline:
                raise NativeGunDamageUncertain('native impact did not return an authoritative receipt')
            for _ in range(32):
                try:
                    packet, peer = self._socket.recvfrom(_MAX_PACKET + 1)
                except BlockingIOError:
                    return
                if peer != self._peer or not packet.startswith(attempt.response_prefix):
                    continue
                if (len(packet) > _MAX_PACKET or not self._owns_peer(peer)
                        or self._clock() >= attempt.deadline):
                    raise NativeGunDamageUncertain('native impact receipt lost its process or deadline fence')
                fields = packet[len(attempt.response_prefix):].decode('ascii').split('|')
                if fields == ['refused']:
                    if isinstance(attempt.read_receipt, _GroupReceipt) and attempt.read_receipt.rows:
                        raise NativeGunDamageUncertain('native group refused after reporting writes')
                    self._finish(error=CombatImpactRejected('native registry or actors refused before damage'))
                    return
                health = attempt.read_receipt(fields)
                if health is None:
                    continue
                if self._world_current is not None and self._world_current(attempt.binding[1]) is not True:
                    raise NativeGunDamageUncertain('native impact lost its world after dispatch')
                self._finish(health=health)
                return
        except NativeGunDamageUncertain as error:
            self._finish(error=error)
        except Exception as error:
            failure = NativeGunDamageUncertain('native impact receipt could not be verified')
            failure.__cause__ = error
            self._finish(error=failure)
