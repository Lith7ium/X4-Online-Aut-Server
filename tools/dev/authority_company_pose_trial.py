"""Apply and independently observe admitted cockpit motion in a verified Company ship."""
import math
import re
import secrets
import select
import socket
import time

from authority_tcp_job_client_live import socket_owner

from authority.company_ships import read_company_ship_catalogue

_SHIP_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_INSPECT_UNAVAILABLE = frozenset({
    'SHIP_NOT_COMPANY_OPERATIONAL', 'PLAYER_CONTEXT', 'PILOT_PRESENT',
    'DOCKING_OR_STORED', 'FRAME_INVALID',
})


class ShipUnavailable(ValueError):
    """The native bridge confirmed the ship unavailable before writing."""


class TransferNotVisible(ValueError):
    """The verifier still sees the ship outside the expected destination sector."""


_SUCCESS = {'inspect': 'INSPECTED', 'locate': 'LOCATED', 'apply': 'DISPATCHED',
            'transfer': 'DISPATCHED', 'verify': 'OBSERVED'}
_READ_ACTIONS = {'inspect', 'locate', 'verify'}
_WRITE_ACTIONS = {'apply', 'transfer'}
_MOVEMENT_PROOF_VERSION = 2
# Compare sector-space metres. Native UI coordinates use single precision.
READBACK_METRES = 1.0
_ACTOR = re.compile(r'0[xX][0-9a-fA-F]{1,16}')
_TOKEN = re.compile(r'[0-9a-f]{32}')
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')


def _xyz(values):
    xyz = tuple(float(v) for v in values)
    if len(xyz) != 3 or not all(math.isfinite(n) and abs(n) <= 1e7 for n in xyz):
        raise ValueError('invalid company ship coordinates')
    return xyz


def _report_xyz(values):
    if type(values) not in (list, tuple) or any(type(value) not in (int, float) for value in values):
        raise ValueError('invalid reported company ship coordinates')
    return _xyz(values)


def _readback_error(target, md, ui):
    return max(math.dist(target, md), math.dist(target, ui))


def _observed_move(before, target, md, ui, *, changed_sector=False):
    # Positions in different sectors use different origins. Arrival in the
    # verified destination frame establishes movement even at identical xyz.
    return ((changed_sector or min(math.dist(before, point) for point in (target, md, ui)) > READBACK_METRES)
            and _readback_error(target, md, ui) <= READBACK_METRES
            and math.dist(md, ui) <= READBACK_METRES)


class GamePoseChannel:
    """A single in-flight command to the owned game. Never replay an uncertain write."""

    def __init__(self, epoch):
        if not _TOKEN.fullmatch(epoch):
            raise ValueError('invalid source registry epoch')
        self.ship_codes = {}
        self.ship_sectors = {}
        self.epoch, self.lease = epoch, secrets.token_hex(16)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind(('127.0.0.1', 0))
            self.socket.setblocking(False)
        except BaseException:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        self.peer = self.process = self.guard = self.pending = None

    def read_company_catalogue(self, checkpoint):
        """Refresh eligible indices from the same verified snapshot used for seat selection."""
        self.ship_codes.clear()
        self.ship_sectors.clear()
        catalogue = read_company_ship_catalogue(checkpoint)
        if catalogue.registry_epoch != self.epoch:
            raise ValueError('company ship catalogue belongs to another registry')
        self.ship_codes = {ship.ship_index: ship.code for ship in catalogue.ships}
        self.ship_sectors = {ship.ship_index: ship.sector_macro for ship in catalogue.ships}
        return catalogue

    def bind(self, process, guard):
        self.process, self.guard = process, guard
        deadline = time.monotonic() + 15
        hello = f'P|{self.lease}|HELLO'.encode('ascii')
        while time.monotonic() < deadline:
            guard()
            if select.select([self.socket], [], [], .1)[0]:
                data, peer = self.socket.recvfrom(1025)
                if data == hello and socket_owner(peer, self.socket.getsockname(), process):
                    self.peer = peer
                    return
        raise TimeoutError('registered X4 pose channel did not bind')

    def begin(self, action, sample, actor='0'):
        if self.pending is not None:
            raise RuntimeError('previous company pose command is still pending')
        if action not in _SUCCESS:
            raise ValueError('invalid company pose action')
        needs_actor = action not in ('inspect', 'locate')
        if (needs_actor and not _ACTOR.fullmatch(actor)) or (not needs_actor and actor != '0'):
            raise ValueError('invalid company pose action or actor')
        self.guard()
        if not socket_owner(self.peer, self.socket.getsockname(), self.process):
            raise RuntimeError('owned X4 pose socket changed')
        sequence = secrets.token_hex(16)
        sector = sample['sector_macro']
        if (sample['epoch'] != self.epoch or sample['index'] not in self.ship_codes or
                not _TOKEN.fullmatch(sample['generation']) or not _SECTOR.fullmatch(sector)):
            raise ValueError('foreign pose identity or frame')
        identity = (sequence, self.epoch, str(sample['index']), sample['generation'])
        coordinates = _xyz(sample[key] for key in 'xyz')
        fields = ('P', self.lease, action, *identity, sector, actor, *(format(n, '.17g') for n in coordinates))
        packet = '|'.join(fields).encode('ascii')
        if self.socket.sendto(packet, self.peer) != len(packet):
            raise ConnectionError('could not send X4 pose command')
        self.pending = (identity, action, sector, actor, time.monotonic() + 7)

    def poll(self):
        if self.pending is None:
            return None
        identity, action, sector, actor, deadline = self.pending
        if time.monotonic() >= deadline:
            raise TimeoutError(f'X4 company pose {action} receipt expired')
        for _ in range(64):
            try:
                packet, peer = self.socket.recvfrom(1025)
            except BlockingIOError:
                break
            if peer != self.peer or not packet.startswith(f'P|{self.lease}|RESULT|'.encode('ascii')):
                continue
            fields = packet.decode('ascii').split('|')[3:]
            if len(fields) != 15 or len(packet) > 1024:
                raise ValueError('invalid pose response size')
            if tuple(fields[:4]) != identity or fields[4] != action:
                raise ValueError('pose receipt does not match the pending command')
            status, actual_actor, frame = fields[5:8]
            if status != _SUCCESS[action]:
                if action == 'verify' and status == 'FRAME_INVALID':
                    self.pending = None
                    raise TransferNotVisible('X4 has not verified the destination sector')
                if action in ('inspect', 'locate') and status in _INSPECT_UNAVAILABLE:
                    self.pending = None
                    raise ShipUnavailable(status)
                if action in _WRITE_ACTIONS and status == 'DOCKING_OR_STORED':
                    if actual_actor != actor or frame != sector or fields[8:] != ['0'] * 6 + ['-']:
                        raise ValueError('invalid pre-write docking receipt')
                    self.pending = None
                    raise ShipUnavailable(status)
                detail = status if re.fullmatch(r'[A-Z_]{1,64}', status) else 'INVALID_STATUS'
                raise ValueError(f'X4 refused {action} for ship {identity[2]}: {detail}')
            if (not _SECTOR.fullmatch(frame) or (action != 'locate' and frame != sector) or not _ACTOR.fullmatch(actual_actor)
                    or (actor != '0' and actual_actor != actor)):
                raise ValueError('X4 pose receipt changed actor or sector')
            md, ui = _xyz(fields[8:11]), _xyz(fields[11:14])
            if action in _READ_ACTIONS:
                index = int(identity[2])
                code = fields[14]
                if (not _SHIP_CODE.fullmatch(code) or index not in self.ship_codes
                        or self.ship_codes[index] not in (None, code)
                        or math.dist(md, ui) > READBACK_METRES):
                    raise ValueError(f'independent pose readers disagree for ship {index}: {md}, {ui}')
                self.ship_codes[index] = code
            self.pending = None
            return {'actor': actual_actor, 'md': md, 'ui': ui, 'code': fields[14], 'sector_macro': frame}
        return None

    def wait(self):
        while True:
            reply = self.poll()
            if reply is not None:
                return reply
            self.guard()
            select.select([self.socket], [], [], .04)

    def close(self):
        self.socket.close()


class CompanyPoseTrial:
    """Inspect, apply, then read on a later game update. Control keeps its own channel."""

    def __init__(self, channel, report):
        self.channel = channel
        self.report = report
        self.active = None
        self.last = {}
        self.window_moves = {}
        self.baselines = {}
        report.update(unavailable_before_write=0, sector_transfers_verified=0)
        report.update(movement_proof_version=_MOVEMENT_PROOF_VERSION, writes_verified=0, within_tolerance=0, expired_before_write=0,
                      max_verified_step_metres=0.0, max_source_travel_metres=0.0,
                      max_source_speed_mps=None, last_verified={}, paused={})

    def _start(self, record, service):
        if not service.is_application_current(record, after_inspect=False):
            self.report['expired_before_write'] += 1
            return
        sample = record.sample
        index = sample['index']
        if index not in self.channel.ship_codes:
            self._skip_unavailable(index)
            return
        target = _xyz(sample[key] for key in 'xyz')
        sector = sample['sector_macro']
        changed_sector = sector != self.channel.ship_sectors[index]
        if changed_sector and record.sector_revision == 0:
            # A first sample in a different frame cannot prove a gate crossing.
            self._skip_unavailable(index)
            return
        baseline = self.baselines.get(index)
        if baseline is None or baseline[0] != sector:
            self.baselines[index] = (sector, target)
        base = self.baselines[index][1]
        self.report['max_source_travel_metres'] = max(self.report['max_source_travel_metres'],
                                                    math.dist(target, base))
        if sample['real_velocity'] is not None:
            speed = math.hypot(*(sample['real_velocity'][key] for key in 'xyz'))
            peak = self.report['max_source_speed_mps']
            if peak is None or speed > peak:
                self.report['max_source_speed_mps'] = speed
        self.report['last_attempt'] = {'index': index, 'sequence': sample['sequence'],
                                       'sector_macro': sample['sector_macro'], 'target': target}
        action = 'locate' if changed_sector else 'inspect'
        self.channel.begin(action, sample)
        self.active = dict(record=record, sample=sample, target=target, stage=action)

    def _advance(self, service, reply):
        active = self.active
        stage, sample, target = active['stage'], active['sample'], active['target']
        if stage in ('inspect', 'locate'):
            active['before'], active['actor'] = reply['ui'], reply['actor']
            if not service.is_application_current(active['record'], after_inspect=True):
                self.report['expired_before_write'] += 1
                self.active = None
                return
            if stage == 'locate' and reply['sector_macro'] != sample['sector_macro']:
                if reply['sector_macro'] != self.channel.ship_sectors[sample['index']]:
                    self._skip_unavailable(sample['index'])
                    return
                active['source_sector_macro'] = reply['sector_macro']
                active['stage'] = 'transfer'
                self.channel.begin('transfer', sample, active['actor'])
                return
            self.channel.ship_sectors[sample['index']] = sample['sector_macro']
            if _readback_error(target, reply['md'], reply['ui']) > READBACK_METRES:
                active['stage'] = 'apply'
                self.channel.begin('apply', sample, active['actor'])
                return
            self.report['within_tolerance'] += 1
        elif stage in ('apply', 'transfer'):
            active['stage'] = 'verify'
            active['verify_deadline'] = time.monotonic() + 4
            self.channel.begin('verify', sample, active['actor'])
            return
        elif stage == 'verify':
            if not _observed_move(active['before'], target, reply['md'], reply['ui'],
                                  changed_sector='source_sector_macro' in active):
                if time.monotonic() < active['verify_deadline']:
                    # The stock relocation can become visible several frames after dispatch.
                    self.channel.begin('verify', sample, active['actor'])
                    return
                self.report['failed_readback'] = {'index': sample['index'], 'target': target, **reply}
                raise ValueError('server pose did not show the required movement on readback')
            self.report['writes_verified'] += 1
            move = {'sequence': sample['sequence'], 'code': reply['code'], 'before': active['before'],
                    'target': target, 'md': reply['md'], 'ui': reply['ui'],
                    'generation': sample['generation'], 'sector_macro': sample['sector_macro']}
            self.window_moves[sample['index']] = move
            if 'source_sector_macro' in active:
                move['source_sector_macro'] = active['source_sector_macro']
                self.channel.ship_sectors[sample['index']] = sample['sector_macro']
                self.report['sector_transfers_verified'] += 1
                self.report['last_sector_transfer'] = dict(index=sample['index'], **move)
            else:
                step = math.dist(reply['ui'], active['before'])
                if step > self.report['max_verified_step_metres']:
                    self.report['max_verified_step_metres'] = step
                    self.report['largest_verified_move'] = move
        self.last[sample['index']] = sample
        self.report['last_verified'][str(sample['index'])] = {
            'sequence': sample['sequence'], 'sector_macro': sample['sector_macro'], 'target': target,
            'md': reply['md'], 'ui': reply['ui'], 'code': reply['code'], 'stage': stage,
            'pre_write': active['before'],
        }
        self.active = None

    def _skip_unavailable(self, index):
        self.report['unavailable_before_write'] += 1
        self.last.pop(index, None)
        self.window_moves.pop(index, None)
        self.active = None

    def _receive(self, service, read_reply):
        try:
            reply = read_reply()
        except TransferNotVisible:
            if 'source_sector_macro' not in self.active or time.monotonic() >= self.active['verify_deadline']:
                raise
            self.channel.begin('verify', self.active['sample'], self.active['actor'])
            return
        except ShipUnavailable:
            self._skip_unavailable(self.active['sample']['index'])
            return
        if reply is not None:
            self._advance(service, reply)

    def poll(self, service):
        if self.active is not None:
            self._receive(service, self.channel.poll)
        if self.active is None:
            for record in service.take_application_batch(1):
                self._start(record, service)

    def _settle(self, service):
        while self.active is not None:
            self._receive(service, self.channel.wait)

    def before_pause(self, service):
        self._settle(service)
        for record in service.freeze_application():
            self._start(record, service)
            self._settle(service)

    def after_pause(self):
        paused = {}
        for index, sample in self.last.items():
            self.channel.begin('inspect', sample)
            try:
                reply = self.channel.wait()
            except ShipUnavailable:
                continue
            target = self.report['last_verified'][str(index)]['target']
            if _readback_error(target, reply['md'], reply['ui']) > READBACK_METRES:
                self.report['failed_pause'] = {'index': index, 'target': target, **reply}
                raise ValueError('paused server pose diverged before saving')
            item = {'code': reply['code'], 'position': reply['ui'], 'md_position': reply['md'],
                    'target': target, 'sector_macro': sample['sector_macro'],
                    'generation': sample['generation'], 'sequence': sample['sequence']}
            move = self.window_moves.get(index)
            if (move is not None and move['generation'] == item['generation']
                    and move['sector_macro'] == item['sector_macro']):
                item['movement'] = move
            paused[str(index)] = item
        self.report['paused'] = paused

    def start_window(self):
        self.last.clear()
        self.window_moves.clear()
        self.report['paused'] = {}
        self.report.pop('saved_checkpoint_id', None)
        self.report.pop('saved_checkpoint_sha256', None)

    def checkpoint_published(self, checkpoint):
        if self.report['paused']:
            self.report.update(saved_checkpoint_id=checkpoint.checkpoint_id,
                               saved_checkpoint_sha256=checkpoint.sha256)


def _check_movement(move, item, version):
    if (type(move) is not dict or move.get('code') != item['code']
            or type(move.get('sequence')) is not int or not 0 < move['sequence'] <= item['sequence']):
        raise ValueError('invalid checkpoint movement proof')
    transfer = 'source_sector_macro' in move
    if transfer:
        origin = move['source_sector_macro']
        if version < 2 or type(origin) is not str or not _SECTOR.fullmatch(origin) or origin == item['sector_macro']:
            raise ValueError('invalid checkpoint sector transfer')
    positions = (_report_xyz(move.get(name)) for name in ('before', 'target', 'md', 'ui'))
    if not _observed_move(*positions, changed_sector=transfer):
        raise ValueError('invalid checkpoint movement proof')


def _paused_poses(report, ship_codes):
    version = report.get('movement_proof_version')
    if type(version) is not int or version not in (1, _MOVEMENT_PROOF_VERSION):
        raise ValueError('unsupported checkpoint movement proof')

    poses = {}
    indices = {str(index): index for index in ship_codes}
    for key, item in report['paused'].items():
        index = indices.get(key)
        if index is None:
            raise ValueError('unexpected saved company ship')
        if (type(item) is not dict or type(item.get('code')) is not str
                or not _SHIP_CODE.fullmatch(item['code']) or ship_codes[index] not in (None, item['code'])
                or type(item.get('sequence')) is not int or item['sequence'] <= 0
                or type(item.get('sector_macro')) is not str or not _SECTOR.fullmatch(item['sector_macro'])):
            raise ValueError('invalid saved company ship pose')
        position = _report_xyz(item.get('position'))
        movement = False
        generation = item.get('generation')
        md_position = _report_xyz(item.get('md_position'))
        target = _report_xyz(item.get('target'))
        if (type(generation) is not str or not _TOKEN.fullmatch(generation)
                or _readback_error(target, md_position, position) > READBACK_METRES
                or math.dist(md_position, position) > READBACK_METRES):
            raise ValueError('invalid paused checkpoint readback')
        if 'movement' in item:
            move = item['movement']
            _check_movement(move, item, version)
            if move.get('generation') != generation or move.get('sector_macro') != item['sector_macro']:
                raise ValueError('checkpoint movement proof belongs to another grant or sector')
            movement = True
        poses[index] = (item, position, movement)
    if not any(movement for _, _, movement in poses.values()):
        raise ValueError('cold pose check requires a checkpoint movement proof')
    return poses


def verify_reloaded(channel, prior, checkpoint):
    """Resolve saved indices afresh: runtime actor handles never cross a cold reload."""
    report = prior.get('company_pose_trial', {})
    if (prior.get('success') is not True or prior.get('final_checkpoint_sha256') != checkpoint.sha256
            or prior.get('final_checkpoint_id') != checkpoint.checkpoint_id
            or report.get('saved_checkpoint_id') != checkpoint.checkpoint_id
            or report.get('saved_checkpoint_sha256') != checkpoint.sha256
            or type(prior.get('source_sha256')) is not str or not re.fullmatch(r'[0-9a-f]{64}', prior['source_sha256'])
            or report.get('source_sha256') != prior['source_sha256'] or type(report.get('paused')) is not dict
            or not report['paused'] or type(report.get('writes_verified')) is not int
            or report['writes_verified'] <= 0):
        raise ValueError('cold pose check requires a confirmed checkpoint with a verified movement write')
    verified = {}
    for index, (item, target, movement) in _paused_poses(report, channel.ship_codes).items():
        sample = dict(index=index, epoch=channel.epoch, generation=secrets.token_hex(16),
                      sector_macro=item['sector_macro'], **dict(zip('xyz', target)))
        channel.begin('inspect', sample)
        reply = channel.wait()
        if reply['code'] != item['code']:
            raise ValueError('reloaded ship code differs from the paused checkpoint')
        error = _readback_error(target, reply['md'], reply['ui'])
        if error > READBACK_METRES:
            raise ValueError(f'cold pose differs for {item["code"]}: {error:.3f} m')
        verified[str(index)] = dict(code=reply['code'], sector_macro=sample['sector_macro'],
                                    expected=target, ui=reply['ui'], md=reply['md'], error_metres=error,
                                    movement_verified=movement)
    return verified
