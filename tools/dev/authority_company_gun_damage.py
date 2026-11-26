"""Opt-in stock damage RPC to a running, owned provider X4."""

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import socket
import time

from authority.company_combat_service import CombatImpactRejected
from authority_tcp_job_client_live import socket_owner


_SCRIPT = 'authority_company_gun_damage'
_TOKEN = re.compile(r'[0-9a-f]{32}')
_ACTOR = re.compile(r'0[xX][0-9a-fA-F]{1,16}')
_MAX_PACKET = 512
_MAX_INDEX = 200000
_MAX_SERIAL = 2**31 - 1
_MAX_AMOUNT = 2**53 - 1


class NativeGunDamageUncertain(RuntimeError):
    """The provider cannot rule out a native write. Further writes are fenced."""


@dataclass
class _Impact:
    provider: 'NativeGunDamageProvider'
    binding: tuple
    amount: float
    deadline: float
    response_prefix: bytes
    health: tuple | None = None
    error: Exception | None = None

    def poll(self):
        if self.health is None and self.error is None:
            self.provider._poll_impact(self)
        if self.error is not None:
            raise self.error
        return self.health


def _bounded_index(value):
    return type(value) is int and 0 < value <= _MAX_INDEX


def _bounded_amount(value):
    return type(value) in (int, float) and 0 < value <= _MAX_AMOUNT and math.isfinite(value)


def _read_health(fields, amount):
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
    if (killed != (hull == 0) or killed != (observed_hull == 0)
            or (killed and (shield != 0 or observed_shield != 0))):
        raise NativeGunDamageUncertain('native kill does not have a matching root health readback')
    if not all(math.isclose(first, second, rel_tol=1e-11, abs_tol=1e-6)
               for first, second in zip(values[3:7], values[7:], strict=True)):
        raise NativeGunDamageUncertain('native impact health readbacks disagree')
    return observed_shield, observed_hull


class NativeGunDamageProvider:
    """Apply one authorized impact at a time with no post-dispatch writes retried.

    The combat service owns confirmation identity, ordering and deduplication.
    is_current checks the assignment and can_fire adds input freshness before
    dispatch. world_current optionally fences which world can settle the
    receipt, even if the firing pilot has departed since dispatch. The owner
    pumps the returned operation without blocking TLS or motion.
    """

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
        self._serial = 0
        self._peer = self._process = self._guard = self._staged_md = self._active = None
        self._fenced = False

    def install(self, case):
        """Stage in the newly owned registry-aware overlay before native launch."""
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
        if xml.count(b'__PROVIDER_LEASE__') != 1:
            raise ValueError('native damage cue must have one lease binding')
        xml = xml.replace(b'__PROVIDER_LEASE__', self._lease.encode('ascii'))
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
       C.IsGamePaused, X4LAN_COMBAT_OBSERVER.read_lua_health)
  end
  company_gun_damage_tick()
 end
"""
        bridge.write_text('local company_gun_damage_tick\n' + bridge_text.replace(tick, tick + hook),
                          encoding='ascii')
        modules = ''.join(f'    <file name="ui/{path.name}" />\n' for path in (reader, driver))
        manifest.write_text(manifest_text.replace(entry, modules + entry), encoding='ascii')
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
        if (type(binding) is not tuple or len(binding) != 3
                or not all(type(token) is str and _TOKEN.fullmatch(token) for token in binding[:2])
                or not _bounded_index(binding[2]) or not _bounded_index(target_index)
                or target_index == binding[2] or not _bounded_amount(amount)):
            raise CombatImpactRejected('invalid native impact binding or amount')
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
        self._serial += 1
        generation, epoch, source_index = binding
        identity = f'{self._lease}|{self._serial}|{generation}|{epoch}|{source_index}|{target_index}'
        deadline = self._clock() + self._timeout
        packet = f'GUN_IMPACT|{identity}|{amount:.17g}|{deadline:.17g}'.encode('ascii')
        attempt = _Impact(self, binding, amount, deadline, f'GUN_RESULT|{identity}|'.encode('ascii'))
        self._active = attempt
        try:
            if self._socket.sendto(packet, self._peer) != len(packet):
                raise NativeGunDamageUncertain('native damage send was incomplete')
        except BaseException as error:
            failure = NativeGunDamageUncertain('native damage send outcome is unknown')
            self._finish(error=failure)
            if isinstance(error, Exception):
                raise failure from error
            raise
        return attempt

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
                    self._finish(error=CombatImpactRejected('native registry or actors refused before damage'))
                    return
                health = _read_health(fields, attempt.amount)
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
