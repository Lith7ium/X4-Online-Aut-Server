"""Own the loopback receive side of the selected X4 player's read-only sampler."""
import re
import secrets
import select
import socket
import threading
import time

from .company_cockpit_health import CompanyCockpitHealthBridge
from .company_motion_frames import MAX_SAMPLE_AGE
from .company_motion_observer import (MAX_COCKPIT_PILOTS, MAX_OBSERVER_DATAGRAM,
                                      MAX_ORIENTED_OBSERVER_DATAGRAM, MAX_ROSTER_SUFFIX_BYTES,
                                      ROSTER_SAMPLE_SUFFIX, CompanyMotionObserver)
from .company_npc_measurement import (NPC_CODE, NPC_INDEX, PILOT_INDEX, RADAR_ENDED,
                                      REGISTRY_EPOCH, measurement_value, native_capture_now)
from .experimental_tcp_motion import ORIENTATION_AXES, check_motion_progress, motion_value


_MAX_SAMPLER_DATAGRAM = 512
_MAX_ORIENTED_SAMPLER_DATAGRAM = 576
_MAX_NPC_AGE = 1.0
_NATIVE_NUMBER = re.compile(r'-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')


def _decode_npc_point(fields):
    if len(fields) != 15:
        raise ValueError('malformed local NPC point')
    _, index, code, sector, actor, sequence, attention, *numbers = fields
    if (index != str(NPC_INDEX) or not re.fullmatch(r'[1-9][0-9]{0,2}', sequence)
            or any(len(number) > 30 or not _NATIVE_NUMBER.fullmatch(number) for number in numbers)):
        raise ValueError('invalid local NPC point fields')
    capture_time, game_time, x, y, z, vx, vy, vz = map(float, numbers)
    return measurement_value({
        'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': code, 'sector_macro': sector,
        'actor': actor, 'sequence': int(sequence), 'attention': attention,
        'capture_time': capture_time, 'game_time': game_time, 'position': [x, y, z],
        'velocity_m_per_game_second': [vx, vy, vz],
    })


def _npc_progress(sample):
    return sample['actor'], sample['sequence'], sample['capture_time'], sample['game_time']


def validate_playback_index(index, *, company_motion, own_index=None, cockpit_radar_playback=False):
    if type(cockpit_radar_playback) is not bool:
        raise ValueError('cockpit_radar_playback must be a boolean')
    if cockpit_radar_playback and company_motion is not True:
        raise ValueError('cockpit playback requires company motion')
    if index is not None and (
        company_motion is not True or type(index) is not int or not 1 <= index <= 200000 or index == own_index
    ):
        raise ValueError('hull playback requires company motion and a different valid registry index')


def validate_map_marker(enabled, *, company_motion, observed_npc):
    if type(enabled) is not bool or (enabled and (company_motion is not True or observed_npc is not None)):
        raise ValueError('--company-map-marker requires --company-motion and a Company pilot view')


def validate_npc_observation(target, *, company_motion, playback_index=None, cockpit_radar_playback=False,
                             own_index=None):
    """Read one registered NPC while the pilot is in a read-only observation mode."""
    if target is None:
        return None
    if company_motion is not True or playback_index is not None or cockpit_radar_playback:
        raise ValueError('registered NPC observation requires Company motion with ship playback disabled')
    if not isinstance(target, (list, tuple)) or len(target) != 2:
        raise ValueError('registered NPC observation requires an index and ship code')
    index, code = target
    if type(index) is str and re.fullmatch(r'[1-9][0-9]{0,5}', index):
        index = int(index)
    if (type(index) is not int or not 1 <= index <= 200000 or index == own_index
            or type(code) is not str or not re.fullmatch(r'[A-Z0-9]{3}-[A-Z0-9]{3}', code)):
        raise ValueError('invalid registered NPC index or ship code')
    return index, code


class CompanyMotionSource:
    """Poll the latest authenticated sample on the same thread as the game owner."""

    def __init__(self, *, playback_index=None, on_stage=None, cockpit_radar_playback=False, cockpit_max_pilots=None,
                 cockpit_continuous_playback=False, cockpit_native_velocity=False,
                 observed_npc=None, registered_npc_hold_trial=False, registered_npc_alignment_trial=False,
                 registered_npc_local_measurement=False, private_map_marker=False):
        validate_playback_index(playback_index, company_motion=True,
                                cockpit_radar_playback=cockpit_radar_playback)
        if type(cockpit_continuous_playback) is not bool or (cockpit_continuous_playback and
                (not cockpit_radar_playback or playback_index is not None)):
            raise ValueError('Company continuous playback requires automatic cockpit playback')
        if type(cockpit_native_velocity) is not bool or (cockpit_native_velocity and not cockpit_radar_playback):
            raise ValueError('Company native velocity requires cockpit playback')
        if cockpit_radar_playback:
            if cockpit_max_pilots is None:
                cockpit_max_pilots = 1 if playback_index is not None else MAX_COCKPIT_PILOTS
            if type(cockpit_max_pilots) is not int or not 1 <= cockpit_max_pilots <= MAX_COCKPIT_PILOTS:
                raise ValueError('cockpit_max_pilots must be between 1 and 3')
            if playback_index is not None and cockpit_max_pilots != 1:
                raise ValueError('single playback_index requires cockpit_max_pilots=1')
        else:
            if cockpit_max_pilots is not None:
                raise ValueError('cockpit_max_pilots requires cockpit_radar_playback')
            cockpit_max_pilots = 0
        if on_stage is not None and not callable(on_stage):
            raise ValueError('invalid company motion stage reporter')
        self._on_stage = on_stage
        self._reported_stages = set()
        self._observed_npc = validate_npc_observation(
            observed_npc, company_motion=True, playback_index=playback_index, cockpit_radar_playback=cockpit_radar_playback)
        if type(registered_npc_alignment_trial) is not bool or (registered_npc_alignment_trial and not registered_npc_hold_trial):
            raise ValueError('NPC alignment trial requires its reversible hold')
        self._registered_npc_alignment_trial = registered_npc_alignment_trial
        if type(registered_npc_hold_trial) is not bool or (registered_npc_hold_trial and
                self._observed_npc != (943, 'MFQ-419')):
            raise ValueError('NPC hold trial requires observation of MFQ-419 at index 943')
        self._registered_npc_hold_trial = registered_npc_hold_trial
        if type(registered_npc_local_measurement) is not bool or (registered_npc_local_measurement and (
                self._observed_npc != (NPC_INDEX, NPC_CODE) or registered_npc_hold_trial
                or registered_npc_alignment_trial)):
            raise ValueError('NPC local measurement requires read-only observation of MFQ-419 at index 943')
        self._registered_npc_local_measurement = registered_npc_local_measurement
        validate_map_marker(private_map_marker, company_motion=True, observed_npc=self._observed_npc)
        self._private_map_marker = private_map_marker
        self._playback_index = playback_index
        self._cockpit_radar_playback = cockpit_radar_playback
        self._cockpit_max_pilots = cockpit_max_pilots
        self._cockpit_continuous_playback = cockpit_continuous_playback
        self._cockpit_native_velocity = cockpit_native_velocity
        self._thread = threading.get_ident()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
            self._lease = secrets.token_hex(16)
            self._generation = secrets.token_hex(16)
            self._nonce = secrets.token_hex(16)
        except BaseException:
            self._socket.close()
            raise
        self._registry = None
        self._previous = self._returned = None
        self._deferred_motion_sample = None
        self._discard_captured_through = None
        self._peer = None
        self._observer = None
        self._cockpit_health_enabled = False
        self._cockpit_health = None
        self._cockpit_health_peer = None
        self._npc_latest = self._npc_accepted = None
        self._npc_ended = False
        self._npc_seat_gap = False

    @property
    def socket(self):
        """Pass the bound socket to the platform's owned-X4 peer validator."""
        return self._socket

    @property
    def local_generation(self):
        return self._generation

    @property
    def playback_index(self):
        return self._playback_index

    @property
    def private_map_marker(self):
        return self._private_map_marker

    @property
    def registered_npc_hold_trial(self):
        return self._registered_npc_hold_trial

    @property
    def registered_npc_alignment_trial(self):
        return self._registered_npc_alignment_trial

    @property
    def registered_npc_local_measurement(self):
        return self._registered_npc_local_measurement

    @property
    def observed_npc(self):
        return self._observed_npc

    @property
    def cockpit_radar_playback(self):
        return self._cockpit_radar_playback

    @property
    def cockpit_max_pilots(self):
        return self._cockpit_max_pilots

    @property
    def cockpit_native_velocity(self):
        return self._cockpit_native_velocity

    @property
    def registry(self):
        """Verified (registry epoch, selected index), populated by the installer."""
        return self._registry

    def _owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive company motion owner thread required')

    def enable_cockpit_health(self):
        self._owner()
        if self._registry is not None:
            raise ValueError('cockpit health must be staged before Company motion configuration')
        self._cockpit_health_enabled = True

    def cockpit_health_md(self, data):
        self._owner()
        marker = b'__COCKPIT_HEALTH_LEASE__'
        if not self._cockpit_health_enabled or self._registry is None or data.count(marker) != 1:
            raise ValueError('unexpected cockpit health MD source or configuration')
        return data.replace(marker, self._nonce.encode('ascii'))

    def bind_cockpit_health(self, credential):
        self._owner()
        if (not self._cockpit_health_enabled or self._registry is None or self._cockpit_health is not None
                or type(credential) is not dict or (credential.get('epoch'), credential.get('index')) != self._registry):
            raise ValueError('cockpit health requires the configured admitted seat')
        self._cockpit_health = CompanyCockpitHealthBridge(
            (credential.get('generation'), credential['epoch'], credential['index']))
        return self._cockpit_health

    def configure(self, epoch, index, *, operational_bits=None):
        """Bind this source once to the installed overlay's verified selection."""
        self._owner()
        if (self._registry is not None or self._socket.fileno() < 0 or
                type(epoch) is not str or not re.fullmatch(r'[0-9a-f]{32}', epoch) or
                type(index) is not int or not 1 <= index <= 200000):
            raise ValueError('invalid selected company motion configuration')
        if len({epoch, self._generation, self._lease, self._nonce}) != 4:
            raise RuntimeError('company motion tokens overlap')
        playback_index = self._playback_index
        validate_playback_index(playback_index, company_motion=True, own_index=index)
        if playback_index is not None and (
            type(operational_bits) is not str or playback_index > len(operational_bits)
            or operational_bits[playback_index - 1] != '1'
        ):
            raise ValueError('hull playback target is unavailable in the verified register')
        if self._observed_npc:
            target_index = self._observed_npc[0]
            if (target_index == index or type(operational_bits) is not str
                    or target_index > len(operational_bits) or operational_bits[target_index - 1] != '1'):
                raise ValueError('registered NPC must be a distinct operational entry in the offered checkpoint')
        if self._registered_npc_hold_trial and (epoch, index) != ('26a1a365c66788aa06f0aeb9e18ff62e', 1073):
            raise ValueError('NPC hold trial requires the verified HVC-420 seat in the MFQ-419 test world')
        if self._registered_npc_local_measurement and (epoch, index) != (REGISTRY_EPOCH, PILOT_INDEX):
            raise ValueError('NPC local measurement requires the verified HVC-420 seat in the MFQ-419 test world')
        self._registry = (epoch, index)
        config = {'port': self._socket.getsockname()[1], 'lease': self._lease,
                  'generation': self._generation, 'nonce': self._nonce, 'index': index}
        if self._cockpit_health_enabled:
            config['cockpit_health'] = True
        if self._private_map_marker:
            config['private_map_marker'] = True
        if self._observed_npc:
            config['observed_npc_index'], config['observed_npc_code'] = self._observed_npc
        if self._registered_npc_hold_trial:
            config['registered_npc_hold_trial'] = True
        if self.registered_npc_alignment_trial:
            config['registered_npc_alignment_trial'] = True
        if self._registered_npc_local_measurement:
            config['registered_npc_local_measurement'] = True
        if playback_index is not None:
            config['playback_index'] = playback_index
        if self._cockpit_radar_playback:
            config['cockpit_radar_playback'] = True
            config['cockpit_max_pilots'] = self._cockpit_max_pilots
        if self._cockpit_continuous_playback:
            config['cockpit_continuous_playback'] = True
        if self._cockpit_native_velocity:
            config['cockpit_native_velocity'] = True
        return config

    def _prefix(self):
        return f'C|{self._lease}|{self._generation}|'.encode('ascii')

    def _send_to_peer(self, validate_peer, message):
        self._owner()
        packet = self._prefix() + message.encode('ascii')
        limit = MAX_ORIENTED_OBSERVER_DATAGRAM if message.startswith('REMOTE4|') else MAX_OBSERVER_DATAGRAM
        base, marker, roster = packet.partition(ROSTER_SAMPLE_SUFFIX.encode('ascii'))
        if (self._peer is None or len(base) > limit or (marker and (
                not message.startswith(('REMOTE|', 'REMOTE4|'))
                or len(marker) + len(roster) > MAX_ROSTER_SUFFIX_BYTES))):
            return False
        try:
            return (validate_peer(self._peer) is True
                    and self._socket.sendto(packet, self._peer) == len(packet))
        except Exception:
            return False

    def observe(self, validate_peer):
        """Attach the optional observer to this source's authenticated X4 peer."""
        self._owner()
        if self._registry is None or self._observer is not None or not callable(validate_peer):
            raise ValueError('configured company motion source and validator required')
        if self._cockpit_health_enabled:
            self._cockpit_health_peer = validate_peer

        cockpit_slots = self._cockpit_max_pilots if self._cockpit_radar_playback and self._playback_index is None else 0
        timed_index = self._observed_npc[0] if self.registered_npc_alignment_trial else self.playback_index
        self._observer = CompanyMotionObserver(self._registry, lambda message: self._send_to_peer(validate_peer, message),
                                               trial_index=timed_index,
                                               cockpit_playback_slots=cockpit_slots,
                                               private_map_marker=self._private_map_marker,
                                               roster_with_samples=self._cockpit_continuous_playback)
        return self._observer

    def observer_stats(self):
        self._owner()
        return self._observer.stats()

    def _report_once(self, stage):
        if stage not in self._reported_stages:
            self._reported_stages.add(stage)
            self._on_stage(stage)

    @staticmethod
    def _npc_binding(sample):
        return sample['sector_macro'], sample['index'], sample['actor'], sample['code']

    def _receive_npc_measurement(self, body):
        if not self._registered_npc_local_measurement or self._npc_ended or self._observer is None:
            return
        try:
            fields = body.decode('ascii').split('|')
            if fields[0] == 'NPC_END':
                if fields == ['NPC_END', str(NPC_INDEX), NPC_CODE]:
                    self._npc_latest = RADAR_ENDED
                    self._npc_ended = True
                return
            sample = _decode_npc_point(fields)
        except (UnicodeError, ValueError):
            return  # Malformed diagnostic data cannot stop pilot motion.

        if self._npc_seat_gap or not 0 <= native_capture_now() - sample['capture_time'] <= _MAX_NPC_AGE:
            return
        binding = self._observer.cockpit_binding()
        if binding is not None:
            if binding != self._npc_binding(sample):
                return
            if self._npc_latest is not None and binding != self._npc_binding(self._npc_latest):
                self._npc_latest = None
        actor, sequence, captured, game_time = _npc_progress(sample)
        previous = self._npc_accepted
        if self._npc_latest is not None:
            previous = _npc_progress(self._npc_latest)
        if previous is not None and (actor != previous[0] or sequence <= previous[1]
                                     or captured <= previous[2] or game_time < previous[3]):
            return
        self._npc_latest = sample
        if binding is not None:
            self._npc_accepted = (actor, sequence, captured, game_time)

    def take_npc_measurement(self):
        """Take the latest fresh local radar measurement or its one-shot end."""
        self._owner()
        sample, self._npc_latest = self._npc_latest, None
        if self._observer is None:
            return None
        if sample is None or sample is RADAR_ENDED:
            return sample
        now = native_capture_now()
        if not 0 <= now - sample['capture_time'] <= _MAX_NPC_AGE:
            return None
        if self._observer.cockpit_binding() != self._npc_binding(sample):
            return None
        self._npc_accepted = _npc_progress(sample)
        return sample

    def discard_pending_motion(self):
        """Drop the held pose and ignore queued samples captured at or before this transition."""
        self._owner()
        self._deferred_motion_sample = self._returned = None
        self._discard_captured_through = native_capture_now()
        return True

    def poll(self, validate_peer):
        """Return the latest new real-time sample, or None. validate_peer takes (IPv4, port)."""
        self._owner()
        if self._registry is None or not callable(validate_peer):
            raise ValueError('configured company motion source and validator required')
        deferred, self._deferred_motion_sample = self._deferred_motion_sample, None
        newest = deferred
        checked_peer = None
        prefix = self._prefix()
        packet_limit = (MAX_ORIENTED_OBSERVER_DATAGRAM if self._observer is not None
                        else _MAX_ORIENTED_SAMPLER_DATAGRAM)
        if self._observer is not None:
            self._observer.expire()
        drain_limit = 64 if self._cockpit_max_pilots > 1 else 32
        for _ in range(drain_limit):
            try:
                raw, address = self._socket.recvfrom(packet_limit + 1)
            except BlockingIOError:
                break
            except OSError as error:
                if ((self._observer is not None or self._registered_npc_local_measurement)
                        and getattr(error, 'winerror', None) == 10040):
                    continue  # An oversized datagram is discarded by Winsock.
                raise
            if not raw.startswith(prefix) or address[0] != '127.0.0.1':
                continue
            if self._on_stage is not None:
                self._report_once('first_local_motion_datagram_matched')
            if checked_peer != address:
                if self._peer is not None and address != self._peer:
                    continue
                if validate_peer(address) is not True:
                    continue
                checked_peer = self._peer = address
                if self._on_stage is not None:
                    self._report_once('first_local_motion_peer_verified')
            body = raw[len(prefix):]
            kind = body.partition(b'|')[0]
            if self._cockpit_health is not None and kind == b'HEALTH_RESULT':
                if len(raw) <= MAX_OBSERVER_DATAGRAM and body.isascii():
                    self._cockpit_health.native_result(body.decode('ascii').split('|'))
                continue
            if kind in (b'NPC_POINT', b'NPC_END'):
                if len(raw) <= MAX_OBSERVER_DATAGRAM:
                    self._receive_npc_measurement(body)
                continue
            if self._observer is not None and kind not in (b'GAP', b'SAMPLE3', b'SAMPLE4'):
                limit = MAX_ORIENTED_OBSERVER_DATAGRAM if kind == b'SEEN4' else MAX_OBSERVER_DATAGRAM
                if len(raw) <= limit and kind in (b'VIEW', b'SEEN', b'SEEN4'):
                    try:
                        fields = body.decode('ascii').split('|')
                    except UnicodeDecodeError:
                        continue
                    self._observer.receive(fields)
                continue
            fields = body.decode('ascii').split('|')
            if fields == ['GAP']:
                if self._cockpit_health is not None:
                    self._cockpit_health.on_gap()
                newest = None
                self._returned = None
                self._npc_seat_gap = True
                if self._npc_latest is not RADAR_ENDED:
                    self._npc_latest = None
                continue
            oriented = kind == b'SAMPLE4'
            limit = _MAX_ORIENTED_SAMPLER_DATAGRAM if oriented else _MAX_SAMPLER_DATAGRAM
            if kind not in (b'SAMPLE3', b'SAMPLE4') or len(raw) > limit or len(fields) != (17 if oriented else 14):
                raise ValueError('invalid local company motion packet')
            _, epoch, index, sequence, sector, source_time, capture_time, x, y, z, vx, vy, vz = fields[:13]
            if (fields[-1] != '' or not re.fullmatch(r'[1-9][0-9]{0,15}', sequence)
                    or not re.fullmatch(r'[1-9][0-9]{0,5}', index)
                    or (epoch, int(index)) != self._registry):
                raise ValueError('foreign local company motion sample')
            rate = None if (vx, vy, vz) == ('-', '-', '-') else dict(zip('xyz', map(float, (vx, vy, vz))))
            sample = {
                'version': 4 if oriented else 3, 'epoch': epoch, 'generation': self._generation,
                'index': int(index), 'sequence': int(sequence), 'sector_macro': sector,
                'source_time': float(source_time), 'capture_time': float(capture_time),
                'x': float(x), 'y': float(y), 'z': float(z), 'real_velocity': rate,
            }
            if oriented:
                angles = fields[13:16]
                if any(not _NATIVE_NUMBER.fullmatch(angle) for angle in angles):
                    raise ValueError('invalid local company motion orientation')
                sample['orientation'] = dict(zip(ORIENTATION_AXES, map(float, angles)))
            sample = motion_value(sample, allow_v4=True)
            if (self._discard_captured_through is not None
                    and sample['capture_time'] <= self._discard_captured_through):
                continue
            if self._previous is not None:
                if sample['sequence'] <= self._previous['sequence']:
                    continue
                if sample['sector_macro'] != self._previous['sector_macro']:
                    sample['real_velocity'] = None
                check_motion_progress(sample, self._previous)
            self._previous = newest = sample
            self._npc_seat_gap = False
            if self._on_stage is not None:
                self._report_once('first_local_own_pose_validated')
        if newest is not None:
            if select.select([self._socket], [], [], 0)[0]:
                self._deferred_motion_sample = newest
                return None  # Later datagrams may supersede this pose or contain GAP.
            if newest is deferred and (
                    not 0 <= native_capture_now() - newest['capture_time'] < MAX_SAMPLE_AGE
                    or checked_peer is None and validate_peer(self._peer) is not True):
                newest = None
        if newest is not None:
            # A coalesced first sample or sector transition has no rate in the
            # receiver's frame until a preceding observation in that frame.
            if self._returned != newest['sector_macro']:
                newest = dict(newest, real_velocity=None)
            self._returned = newest['sector_macro']
        if self._cockpit_health is not None:
            if newest is not None:
                self._cockpit_health.on_sample(newest)
            self._cockpit_health.deliver(lambda message: self._send_to_peer(validate_peer, message))
        return newest

    def close(self):
        self._owner()
        self._deferred_motion_sample = None
        self._npc_latest = None
        if self._cockpit_health is not None:
            self._cockpit_health.close()
            if self._cockpit_health_peer is not None:
                self._cockpit_health.deliver(lambda message: self._send_to_peer(self._cockpit_health_peer, message))
        self._socket.close()
