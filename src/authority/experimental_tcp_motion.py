"""Opt-in serial motion messages on the existing admitted TCP delivery owner.

The owner must bind each epoch/generation/index to a real source lifetime before
calling track(). A receipt confirms the participant callback only: no X4 write,
frame equivalence, native velocity, lifecycle detection or recovery is implied.
"""
import copy
import hashlib
import json
import math
import re
import time

from .experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
from .experimental_tcp_participants import ExperimentalTCPParticipants
from .load_messages import MAX_MESSAGE

_TOKEN = re.compile(r'[0-9a-f]{32}\Z')
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}\Z')
_MAX = 9007199254740991
_COMMON = {'version', 'epoch', 'generation', 'index', 'sequence'}
_SNAPSHOT = _COMMON | {'sector_macro', 'source_time', 'x', 'y', 'z', 'velocity'}
_SNAPSHOT_V2 = _SNAPSHOT | {'orientation', 'angular_rate'}
_SNAPSHOT_V3 = _COMMON | {'sector_macro', 'source_time', 'capture_time',
                          'x', 'y', 'z', 'real_velocity'}


def motion_value(value, *, despawn=False):
    """Validate exact portable fields and return a detached copy."""
    if type(despawn) is not bool or type(value) is not dict:
        raise ValueError('invalid motion fields')
    version = value.get('version')
    if type(version) is not int or version not in (1, 2, 3) or (despawn and version != 1):
        raise ValueError('invalid motion version')
    snapshots = {1: _SNAPSHOT, 2: _SNAPSHOT_V2, 3: _SNAPSHOT_V3}
    expected = _COMMON if despawn else snapshots[version]
    if set(value) != expected:
        raise ValueError('invalid motion fields')
    if any(type(value[k]) is not str or not _TOKEN.fullmatch(value[k]) for k in ('epoch', 'generation')):
        raise ValueError('invalid motion lineage')
    if any(type(value[k]) is not int or not 1 <= value[k] <= _MAX for k in ('index', 'sequence')):
        raise ValueError('invalid motion identity')
    def number(n, lo, hi):
        if type(n) not in (int, float): return False
        try: return math.isfinite(n) and lo <= n <= hi
        except OverflowError: return False
    if not despawn:
        if type(value['sector_macro']) is not str or not _SECTOR.fullmatch(value['sector_macro']):
            raise ValueError('invalid motion frame')
        if not number(value['source_time'], 0, 1e12) or any(not number(value[k], -1e7, 1e7) for k in ('x','y','z')):
            raise ValueError('invalid motion sample')
        if version == 3 and not number(value['capture_time'], 0, 1e12):
            raise ValueError('invalid motion capture time')
        vel = value['real_velocity'] if version == 3 else value['velocity']
        if vel is not None and (type(vel) is not dict or set(vel)!={'x','y','z'} or
                any(not number(vel[k], -5000, 5000) for k in ('x','y','z'))):
            raise ValueError('invalid motion rate')
        if version == 2:
            orientation = value['orientation']
            angular_rate = value['angular_rate']
            axes = ('yaw', 'pitch', 'roll')
            if (type(orientation) is not dict or set(orientation) != set(axes) or
                    any(not number(orientation[k], -2 * math.pi, 2 * math.pi) for k in axes)):
                raise ValueError('invalid motion orientation')
            if (vel is None) != (angular_rate is None):
                raise ValueError('incomplete motion rate')
            if angular_rate is not None and (type(angular_rate) is not dict or set(angular_rate) != set(axes) or
                    any(not number(angular_rate[k], -2 * math.pi, 2 * math.pi) for k in axes)):
                raise ValueError('invalid angular rate')
    return copy.deepcopy(value)


def check_motion_progress(value, previous):
    """Check validated snapshots' clock, version and frame progression.

    ``previous`` is the prior validated snapshot, or None for a new lifetime.
    Sequence ordering remains the caller's responsibility.
    """
    if previous is None:
        return
    if value['version'] != previous['version']:
        raise ValueError('motion snapshot version changed within lifetime')
    previous_time = previous['source_time']
    if value['version'] == 3:
        if value['source_time'] < previous_time:
            raise ValueError('source game time regressed')
        if value['capture_time'] <= previous['capture_time']:
            raise ValueError('non-monotone capture time')
        velocity = value['real_velocity']
    else:
        if value['source_time'] <= previous_time:
            raise ValueError('non-monotone source time')
        velocity = value['velocity']
    if value['sector_macro'] != previous['sector_macro'] and velocity is not None:
        raise ValueError('frame transition requires unknown velocity')


def _check_snapshot_progress(value, state):
    previous = None
    if state['version'] is not None:
        previous = {'version': state['version'], 'source_time': state['time'],
                    'capture_time': state['capture_time'], 'sector_macro': state['frame']}
    check_motion_progress(value, previous)


def motion_digest(value, *, despawn=False):
    raw=json.dumps(motion_value(value, despawn=despawn),sort_keys=True,separators=(',', ':'),allow_nan=False).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


class ExperimentalTCPMotion:
    """Share the job owner's socket lease; callbacks own real game binding."""
    def __init__(self, delivery, *, enabled=False, min_interval=1/60, max_tracks=100000,
                 allow_apply=False, allow_sector_reentry=False):
        if (enabled is not True or type(delivery) is not ExperimentalTCPJobDelivery or
            type(allow_apply) is not bool or type(allow_sector_reentry) is not bool or type(min_interval) not in (int,float) or not 1/240 <= min_interval <= 10 or not math.isfinite(min_interval) or
            type(max_tracks) is not int or not 1 <= max_tracks <= 100000):
            raise ValueError('invalid enabled motion owner')
        delivery._host._check()
        if getattr(delivery, '_motion_owner', None) is not None:raise ValueError('motion owner already bound')
        delivery._motion_owner=self;self._delivery=delivery;self._tracks={};self._current={};self._source_owner=None;self._source_lease=None
        self._min_interval=float(min_interval);self._max_tracks=max_tracks;self._failed=False
        self._allow_apply=allow_apply;self._apply_attempted=False;self._preflight=None
        self._allow_sector_reentry = allow_sector_reentry

    def _peer_disconnected(self, identity):
        """Discard per-track state for the exact disconnected incarnation."""
        for state in self._tracks.values():
            for name in ('peer_frames','peer_samples','latest_frames','reentry_absent','reentries'):
                state.get(name,{}).pop(identity,None)
        if self._preflight is not None and self._preflight[1]==identity:
            self._preflight=None

    def _reserved(self,owner):
        if self._source_owner is not None and (owner is None or owner is not self._source_lease):
            self._failed=True;self._delivery._failed=True;self._delivery._host._failed=True
            raise RuntimeError('motion delivery is reserved by its source bridge')

    def track(self, epoch, generation, index, *, _source=None):
        self._reserved(_source)
        self._delivery._host._check()
        if self._failed or self._delivery._failed:raise RuntimeError('motion owner unavailable')
        try:probe=motion_value({'version':1,'epoch':epoch,'generation':generation,'index':index,'sequence':1},despawn=True)
        except (TypeError,ValueError):
            self._failed=True;self._delivery._failed=True;self._delivery._host._failed=True
            raise
        key=(probe['epoch'],probe['generation'],probe['index'])
        if key in self._tracks or len(self._tracks)>=self._max_tracks:
            self._failed=True;self._delivery._failed=True;self._delivery._host._failed=True
            raise ValueError('duplicate or full motion registry')
        current=self._current.get((epoch,index))
        if current is not None and not self._tracks[current]['closed']:
            self._failed=True;self._delivery._failed=True;self._delivery._host._failed=True
            raise ValueError('previous lifetime is still active')
        self._tracks[key]={'sequence':0,'time':None,'sent':None,'frame':None,'closed':False,
                           'version':None,'capture_time':None,'peer_frames':{},'peer_samples':{}}
        self._current[(epoch,index)]=key

    def observe_unselected(self, raw, *, _source=None, _interest_observed=False):
        """Advance a validated source track when interest selects no peer.

        This sends no motion and leaves every peer's frame and receipt history
        untouched. An explicitly observed interest result can establish
        absence for the opt-in latest reentry path.
        """
        self._reserved(_source)
        if self._source_owner is None or _source is None or _source is not self._source_lease:
            raise RuntimeError('unselected observation requires the source owner')
        delivery = self._delivery
        host = delivery._host
        host._check()
        if self._failed or delivery._failed:
            raise RuntimeError('motion owner unavailable')
        try:
            sample = motion_value(raw)
            key = (sample['epoch'], sample['generation'], sample['index'])
            track = self._tracks.get(key)
            if track is None or track['closed'] or sample['sequence'] <= track['sequence']:
                raise ValueError('unknown, closed or stale motion track')
            _check_snapshot_progress(sample, track)
            host._control._reader_free(motion=True)
            if self._allow_sector_reentry and _interest_observed:
                self._reentry_interest(track, sample['sector_macro'], set())
            track['sequence'] = sample['sequence']
            track['time'] = sample['source_time']
            track['capture_time'] = sample.get('capture_time')
            track['version'] = sample['version']
            track['frame'] = sample['sector_macro']
        except BaseException:
            self._failed = True
            delivery._failed = True
            host._failed = True
            raise

    def send(self, raw, *, despawn=False, guard=None, recipients=None, _source=None):
        self._preflight=None
        self._reserved(_source)
        d=self._delivery;host=d._host;host._check()
        if not host._control._motion_idle():
            raise RuntimeError('drain latest motion before synchronous exchange')
        if despawn and host._control._latest_motion is not None and not host._control._latest_motion.blocked:
            raise RuntimeError('explicit motion barrier required before despawn')
        if self._failed or d._failed:raise RuntimeError('motion owner unavailable')
        if guard is not None and not callable(guard):raise ValueError('invalid motion operation guard')
        try:value=motion_value(raw,despawn=despawn)
        except (TypeError,ValueError):
            self._failed=True;d._failed=True;host._failed=True
            raise
        key=(value['epoch'],value['generation'],value['index'])
        state=self._tracks.get(key);now=time.monotonic()
        if not state or state['closed'] or value['sequence']<=state['sequence'] or (despawn and state['sequence']==0):
            self._failed=True;d._failed=True;host._failed=True
            raise ValueError('unknown, stale or despawned motion')
        if not despawn:
            try:
                _check_snapshot_progress(value, state)
            except ValueError:
                self._failed=True;d._failed=True;host._failed=True
                raise
            if state['sent'] is not None and now-state['sent']<self._min_interval:
                raise ValueError('motion rate limited')
        try:
            members=d.membership();handles,peers=d._peers()
            if not members:raise RuntimeError('no admitted motion recipients')
            # Snapshot full membership and exact socket incarnations even when
            # the caller selects only some participants. All other sockets
            # remain fenced and must remain quiet throughout the exchange.
            incarnations=dict(d._incarnations)
            full={(m['id'],m['incarnation']) for m in members}
            if len(full)!=len(handles) or full!={('peer_'+str(h),incarnations[h]) for h in handles}:
                raise RuntimeError('motion membership changed')
            # Only peers that confirmed a snapshot know this lifetime. A
            # despawn must reach every such connected incarnation; sending it
            # to an uninformed peer is invalid on the participant as well.
            required=full.intersection(state['peer_samples']) if despawn else None
            if recipients is None:
                selected=(tuple(h for h in handles if ('peer_'+str(h),incarnations[h]) in required)
                          if despawn else handles)
            else:
                if type(recipients) not in (tuple,list) or (not recipients and not despawn):
                    raise ValueError('nonempty explicit motion recipients required')
                wanted=set()
                for peer in recipients:
                    if (type(peer) is not dict or set(peer)!={'id','incarnation'} or
                        type(peer['id']) is not str or type(peer['incarnation']) is not str):
                        raise ValueError('invalid motion recipient')
                    identity=(peer['id'],peer['incarnation'])
                    if identity in wanted or identity not in full:raise ValueError('duplicate or foreign motion recipient')
                    wanted.add(identity)
                if despawn and wanted != required:
                    raise ValueError('despawn requires every informed connected recipient')
                selected=tuple(h for h in handles if ('peer_'+str(h),incarnations[h]) in wanted)
            if not despawn:
                for h in selected:
                    peer=('peer_'+str(h),incarnations[h])
                    if peer in state['peer_frames'] and state['peer_frames'][peer]!=value['sector_macro']:
                        raise ValueError('recipient motion frame changed without resync')
            host._control._reader_free()
        except BaseException:
            self._failed=True;d._failed=True;host._failed=True
            raise
        lease=object();host._control._reader_lease=lease
        host._busy=True;d._active=True
        try:
            deadline=time.monotonic()+d._timeout
            def check_guard():
                if guard is not None and guard() is not True:raise RuntimeError('motion operation guard failed')
                if (time.monotonic()>=deadline or self._failed or d._failed or host._failed or
                    host._busy is not True or d._active is not True or host._control._reader_lease is not lease or
                    d._incarnations!=incarnations):
                    raise RuntimeError('motion operation state changed')
            check_guard()
            ExperimentalTCPParticipants._quiet(d,peers,deadline)
            for h in selected:
                check_guard()
                incarnation=incarnations[h]
                req={'version':1,'kind':'motion_despawn' if despawn else 'motion_snapshot',
                     'connection_handle':h,'session_epoch':host._world.session.world_epoch,
                     'incarnation':incarnation,'request_id':value['sequence'],'sample':value}
                expected={k:req[k] for k in ('version','connection_handle','session_epoch','incarnation','request_id')}
                expected.update(kind='motion_receipt',status='accepted',sample_sha256=motion_digest(value,despawn=despawn))
                data=json.dumps(req,sort_keys=True,separators=(',', ':'),allow_nan=False).encode('ascii')+b'\n'
                if len(data)>MAX_MESSAGE+1:raise ValueError('motion frame too large')
                conn,peer=peers[h];others=[p[0] for n,p in peers.items() if n!=h]
                ExperimentalTCPParticipants._exchange(d,conn,peer,others,data,expected,deadline,check=check_guard if guard else None)
                check_guard()
                current,current_peers=d._peers()
                if (current!=handles or any(current_peers[k]!=peers[k] for k in handles) or
                    d._incarnations!=incarnations):raise RuntimeError('motion membership changed')
                ExperimentalTCPParticipants._quiet(d,peers,deadline)
            check_guard()
            state['sequence']=value['sequence'];state['sent']=time.monotonic();state['closed']=despawn
            if not despawn:
                state['time']=value['source_time'];state['frame']=value['sector_macro']
                state['version'] = value['version']
                state['capture_time'] = value.get('capture_time')
                for h in selected:
                    peer=('peer_'+str(h),incarnations[h]);state['peer_frames'][peer]=value['sector_macro']
                    state['peer_samples'][peer]=(value['sequence'],motion_digest(value))
            return True
        except BaseException:
            self._failed=True;d._failed=True;host._failed=True
            raise
        finally:
            changed=host._control._reader_lease is not lease
            if not changed:host._control._reader_lease=None
            else:self._failed=True;d._failed=True;host._failed=True
            d._active=False;host._busy=False
            if changed:raise RuntimeError('motion socket lease changed')

    def _reentry_interest(self, track, sector, selected):
        """Record confirmed exclusion separately from delivery and lifecycle fences."""
        absent = track.setdefault('reentry_absent', {})
        frames = track['peer_frames']
        for identity, reentry in tuple(track.get('reentries', {}).items()):
            if identity not in selected or reentry['sector'] != sector:
                reentry['cancel']()
        for identity in tuple(absent):
            if absent[identity][1] != sector:
                del absent[identity]
        for identity, frame in frames.items():
            if identity not in selected and frame != sector:
                absent[identity] = (frame, sector)

    def _begin_reentry(self, state, track_key, identity, conn, peer, value, epoch, guard):
        """One non-coalesced per-peer transaction on the existing latest socket."""
        delivery = self._delivery
        control = delivery._host._control
        handle = peer['handle']
        key = ('reentry', *track_key)
        reentries = state['reentries']
        cancellations = peer.setdefault('motion_reentry_cancel', {})
        previous = state['peer_frames'][identity]
        sector = value['sector_macro']
        transaction = {'sector': sector, 'phase': 'prepare', 'canceled': False}
        prepare_sample = copy.deepcopy(value)
        prepare_sample['real_velocity' if value['version'] == 3 else 'velocity'] = None
        if value['version'] == 2:
            prepare_sample['angular_rate'] = None
        prepared_sequence = value['sequence']
        prepared_digest = motion_digest(prepare_sample)

        def wire(operation, status, sample=None):
            request = {'version': 1, 'kind': 'motion_reentry_' + operation,
                       'connection_handle': handle, 'session_epoch': epoch,
                       'incarnation': identity[1], 'previous_sector': previous,
                       'request_id': prepared_sequence if sample is None else sample['sequence']}
            if operation != 'prepare':
                request['prepared_sequence'] = prepared_sequence
            digest = prepared_digest if sample is None else motion_digest(sample)
            expected = dict(request, kind='motion_reentry_' + status + '_receipt',
                            status=status, sample_sha256=digest)
            if sample is None:
                request['sample_sha256'] = digest
            else:
                request['sample'] = sample
            data = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
            if len(data) > MAX_MESSAGE + 1:
                raise ValueError('motion reentry frame too large')
            return data, expected

        def check_context():
            if (reentries.get(identity) is not transaction or control._peers.get(conn) is not peer or
                    delivery._incarnations.get(handle) != identity[1] or
                    delivery._host._world.session.world_epoch != epoch):
                raise ValueError('motion reentry receipt context changed')

        def finish():
            check_context()
            del reentries[identity]
            del cancellations[key]

        def stage(operation, status, accepted, sample=None):
            active = peer['motion_active']
            if active['key'] != key:
                raise RuntimeError('motion reentry lost its poll lane')
            data, expected = wire(operation, status, sample)
            active.update(data=data, input=b'', sent=0, expected=expected, accepted=accepted)
            transaction['phase'] = operation

        def cancel():
            transaction['canceled'] = True
            active = peer.get('motion_active')
            if active is None or active['key'] != key:
                pending = peer.get('motion_pending', {}).pop(key, None)
                if pending is not None:
                    pending[3]()
                return
            phase = transaction['phase']
            if phase == 'prepare' and active['sent'] == 0:
                peer['motion_active'] = None
                finish()
            elif phase == 'prepared' or (phase == 'align' and active['sent'] == 0):
                stage('cancel', 'canceled', finish)

        def prepared():
            check_context()
            active = peer['motion_active']
            active['reentry_started'] = True
            # No frame can be written until we have a source response from a
            # genuinely later PULL, even if an earlier PULL responds after ACK.
            transaction['prepared_after_pull'] = self._source_owner._next
            transaction['phase'] = 'prepared'
            active.update(data=b'', input=b'', sent=0)
            if transaction['canceled']:
                cancel()
            return True

        def advance(sample, capture_request):
            if (transaction['phase'] != 'prepared' or transaction['canceled'] or
                    capture_request <= transaction['prepared_after_pull']):
                return False
            if sample['sequence'] <= prepared_sequence or sample['sector_macro'] != sector:
                raise ValueError('invalid post-prepare alignment sample')
            digest = motion_digest(sample)

            def aligned():
                finish()
                state['peer_frames'][identity] = sector
                state['latest_frames'][identity] = sector
                state['peer_samples'][identity] = (sample['sequence'], digest)
                state['reentry_absent'].pop(identity, None)

            stage('align', 'aligned', aligned, sample)
            return True

        transaction.update(cancel=cancel, advance=advance)
        data, expected = wire('prepare', 'prepared', prepare_sample)
        reentries[identity] = transaction
        cancellations[key] = cancel
        return conn, peer, key, data, expected, prepared, finish, guard

    def enqueue_latest(self, raw, *, recipients=None, _source=None, _guard=None, _capture_request=None):
        """Queue a replaceable position for the existing control poll owner.

        Queueing is not receipt, delivery, X4 application, or interest discovery.
        Opt-in reentry requires a source-owned capture request and explicit
        interest selection. Its write uses a PULL issued after the prepare ACK.
        A lifecycle caller must establish and drain control.motion_barrier().
        """
        self._reserved(_source)
        d=self._delivery;host=d._host;host._check();control=host._control
        if _guard is not None and not callable(_guard):raise ValueError('invalid latest motion guard')
        latest=control._latest_motion
        if latest is None or self._failed or d._failed or d._active or d._test_binding is not None:
            raise RuntimeError('latest motion unavailable')
        if latest.blocked or latest.world_blocked:
            raise RuntimeError('motion fenced by lifecycle barrier')
        value=motion_value(raw);key=(value['epoch'],value['generation'],value['index'])
        state=self._tracks.get(key)
        if not state or state['closed'] or value['sequence']<=state['sequence']:
            raise ValueError('unknown, closed or stale motion track')
        _check_snapshot_progress(value, state)
        handles,peers=d._peers();incarnations=dict(d._incarnations)
        if not handles: raise RuntimeError('no admitted motion recipients')
        full={('peer_'+str(h),incarnations[h]):h for h in handles}
        if recipients is None: selected=handles
        else:
            if type(recipients) not in (tuple,list) or not recipients:
                raise ValueError('nonempty recipients required')
            wanted=set()
            for peer in recipients:
                if type(peer) is not dict or set(peer)!={'id','incarnation'}:
                    raise ValueError('invalid motion recipient')
                identity=(peer['id'],peer['incarnation'])
                if identity not in full or identity in wanted: raise ValueError('foreign or duplicate motion recipient')
                wanted.add(identity)
            selected=tuple(h for h in handles if ('peer_'+str(h),incarnations[h]) in wanted)
        reserved=state.setdefault('latest_frames',{})
        track_reentries = state.setdefault('reentries', {})
        reentry_enabled = (self._allow_sector_reentry and self._source_owner is not None and
                           _source is self._source_lease and recipients is not None and
                           type(_capture_request) is int and _capture_request > 0)
        if reentry_enabled:
            self._reentry_interest(state, value['sector_macro'], wanted)
        entries=[];digest=motion_digest(value);ordinary=[];reentry_work=[]
        request_epoch = host._world.session.world_epoch
        for h in selected:
            identity=('peer_'+str(h),incarnations[h]);conn,peer=peers[h]
            frame=reserved.get(identity,state['peer_frames'].get(identity))
            active_reentry = track_reentries.get(identity)
            if active_reentry is not None:
                if not reentry_enabled:
                    raise ValueError('recipient reentry requires confirmed interest')
                reentry_work.append((identity, conn, peer, active_reentry))
                continue
            if frame is not None and frame!=value['sector_macro']:
                if (not reentry_enabled or state['peer_frames'].get(identity) != frame or
                        state.get('reentry_absent', {}).get(identity) != (frame, value['sector_macro'])):
                    raise ValueError('recipient motion frame changed without resync')
                reentry_work.append((identity, conn, peer, None))
                continue
            ordinary.append(h)
            request={'version':1,'kind':'motion_snapshot','connection_handle':h,
                     'session_epoch':host._world.session.world_epoch,'incarnation':incarnations[h],
                     'request_id':value['sequence'],'sample':value}
            expected={k:request[k] for k in ('version','connection_handle','session_epoch','incarnation','request_id')}
            expected.update(kind='motion_receipt',status='accepted',sample_sha256=digest)
            data=json.dumps(request,sort_keys=True,separators=(',', ':'),allow_nan=False).encode('ascii')+b'\n'
            if len(data)>MAX_MESSAGE+1: raise ValueError('motion frame too large')
            def accepted(identity=identity, h=h, conn=conn, peer=peer):
                if (control._peers.get(conn) is not peer or d._incarnations.get(h)!=identity[1] or
                        host._world.session.world_epoch!=request_epoch):
                    raise RuntimeError('motion receipt context changed')
                state['peer_frames'][identity]=value['sector_macro']
                state['peer_samples'][identity]=(value['sequence'],digest)
            def discarded(identity=identity, h=h, conn=conn, peer=peer):
                if control._peers.get(conn) is not peer or d._incarnations.get(h)!=identity[1]:
                    return
                active=peer.get('motion_active')
                if identity not in state['peer_frames'] and not (
                        active is not None and active['key']==key and active['sent']>0):
                    reserved.pop(identity,None)
            entries.append((conn,peer,key,data,expected,accepted,discarded,_guard))
        advanced = False
        for identity, conn, peer, active_reentry in reentry_work:
            if active_reentry is None:
                entries.append(self._begin_reentry(state, key, identity, conn, peer, value, request_epoch, _guard))
            else:
                advanced = active_reentry['advance'](value, _capture_request) or advanced
        survivors=latest.enqueue(entries)
        state['sequence']=value['sequence'];state['time']=value['source_time'];state['frame']=value['sector_macro']
        state['version'] = value['version']
        state['capture_time'] = value.get('capture_time')
        for h in ordinary:
            if peers[h][0] in survivors:reserved[('peer_'+str(h),incarnations[h])]=value['sector_macro']
        self._preflight=None
        return bool(survivors or advanced)

    def apply_once(self, raw, *, guard=None, _source=None):
        """Opt-in one-peer apply/readback after an exactly confirmed motion sample.

        This is a caller assertion; a failure after dispatch cannot undo a write.
        """
        self._reserved(_source)
        delivery = self._delivery
        host = delivery._host
        host._check()
        if not host._control._motion_idle():
            raise RuntimeError('drain latest motion before apply')
        try:
            if not self._allow_apply or self._apply_attempted or self._failed or delivery._failed:
                raise RuntimeError('one-shot motion apply unavailable')
            if guard is not None and not callable(guard):
                raise ValueError('invalid operation guard')
            sample = motion_value(raw)
            if sample['version'] != 1:
                raise ValueError('full-pose motion apply is not implemented')
            track_key = (sample['epoch'], sample['generation'], sample['index'])
            track = self._tracks.get(track_key)
            members = delivery.membership()
            handles, peers = delivery._peers()
            incarnations = dict(delivery._incarnations)
            if len(members) != 1 or len(handles) != 1:
                raise RuntimeError('one admitted apply participant required')
            handle = handles[0]
            peer_identity = ('peer_' + str(handle), incarnations[handle])
            digest = motion_digest(sample)
            if (not track or track['closed'] or
                track['peer_samples'].get(peer_identity) != (sample['sequence'], digest) or
                self._preflight != (track_key, peer_identity, sample['sequence'], digest) or
                members != ({'id': peer_identity[0], 'incarnation': peer_identity[1]},)):
                raise ValueError('apply sample is not the confirmed participant sample')
            host._control._reader_free()
        except BaseException:
            self._failed = True
            delivery._failed = True
            host._failed = True
            raise
        reader_lease = object()
        host._control._reader_lease = reader_lease
        host._busy = True
        delivery._active = True
        self._apply_attempted = True
        try:
            deadline = time.monotonic() + delivery._timeout

            def check_context():
                if guard is not None and guard() is not True:
                    raise RuntimeError('motion apply guard failed')
                if (time.monotonic() >= deadline or self._failed or delivery._failed or host._failed or
                    host._busy is not True or delivery._active is not True or
                    host._control._reader_lease is not reader_lease or delivery._incarnations != incarnations):
                    raise RuntimeError('motion apply state changed')

            check_context()
            ExperimentalTCPParticipants._quiet(delivery, peers, deadline)
            request = {'version': 1, 'kind': 'motion_apply', 'connection_handle': handle,
                       'session_epoch': host._world.session.world_epoch, 'incarnation': incarnations[handle],
                       'request_id': sample['sequence'], 'sample': sample}
            expected = {k: request[k] for k in ('version', 'connection_handle', 'session_epoch',
                                                 'incarnation', 'request_id')}
            expected.update(kind='motion_applied_receipt', status='applied', sample_sha256=digest)
            payload = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
            if len(payload) > MAX_MESSAGE + 1:
                raise ValueError('motion apply frame too large')
            connection, connection_peer = peers[handle]
            ExperimentalTCPParticipants._exchange(delivery, connection, connection_peer, [], payload,
                                                  expected, deadline, check=check_context if guard else None)
            check_context()
            current, current_peers = delivery._peers()
            if current != handles or current_peers[handle] != peers[handle] or delivery._incarnations != incarnations:
                raise RuntimeError('motion apply membership changed')
            ExperimentalTCPParticipants._quiet(delivery, peers, deadline)
            check_context()
            return True
        except BaseException:
            self._failed = True
            delivery._failed = True
            host._failed = True
            raise
        finally:
            lease_changed = host._control._reader_lease is not reader_lease
            if not lease_changed:
                host._control._reader_lease = None
            else:
                self._failed = True
                delivery._failed = True
                host._failed = True
            delivery._active = False
            host._busy = False
            if lease_changed:
                raise RuntimeError('motion apply reader lease changed')

    def preflight(self, raw, *, guard=None, _source=None):
        """Read-only, correlated preflight for the last confirmed peer sample."""
        self._reserved(_source)
        delivery = self._delivery
        host = delivery._host
        host._check()
        self._preflight = None
        if not host._control._motion_idle():
            raise RuntimeError('drain latest motion before preflight')
        try:
            if not self._allow_apply or self._apply_attempted or self._failed or delivery._failed:
                raise RuntimeError('motion preflight unavailable')
            if guard is not None and not callable(guard):
                raise ValueError('invalid operation guard')
            sample = motion_value(raw)
            if sample['version'] != 1:
                raise ValueError('full-pose motion preflight is not implemented')
            track_key = (sample['epoch'], sample['generation'], sample['index'])
            track = self._tracks.get(track_key)
            members = delivery.membership()
            handles, peers = delivery._peers()
            incarnations = dict(delivery._incarnations)
            if len(members) != 1 or len(handles) != 1:
                raise RuntimeError('one admitted preflight participant required')
            handle = handles[0]
            peer_identity = ('peer_' + str(handle), incarnations[handle])
            digest = motion_digest(sample)
            if (not track or track['closed'] or
                track['peer_samples'].get(peer_identity) != (sample['sequence'], digest) or
                members != ({'id': peer_identity[0], 'incarnation': peer_identity[1]},)):
                raise ValueError('preflight sample is not the confirmed participant sample')
            host._control._reader_free()
        except BaseException:
            self._failed = True
            delivery._failed = True
            host._failed = True
            raise
        reader_lease = object()
        host._control._reader_lease = reader_lease
        host._busy = True
        delivery._active = True
        try:
            deadline = time.monotonic() + delivery._timeout

            def check_context():
                if guard is not None and guard() is not True:
                    raise RuntimeError('motion preflight guard failed')
                if (time.monotonic() >= deadline or self._failed or delivery._failed or host._failed or
                    host._busy is not True or delivery._active is not True or
                    host._control._reader_lease is not reader_lease or delivery._incarnations != incarnations):
                    raise RuntimeError('motion preflight state changed')

            check_context()
            ExperimentalTCPParticipants._quiet(delivery, peers, deadline)
            request = {'version': 1, 'kind': 'motion_preflight', 'connection_handle': handle,
                       'session_epoch': host._world.session.world_epoch, 'incarnation': incarnations[handle],
                       'request_id': sample['sequence'], 'sample': sample}
            expected = {k: request[k] for k in ('version', 'connection_handle', 'session_epoch',
                                                 'incarnation', 'request_id')}
            expected.update(kind='motion_preflight_receipt', sample_sha256=digest)
            choices = tuple(dict(expected, status=status) for status in ('near', 'ready'))
            payload = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
            if len(payload) > MAX_MESSAGE + 1:
                raise ValueError('motion preflight frame too large')
            connection, connection_peer = peers[handle]
            response = ExperimentalTCPParticipants._exchange(delivery, connection, connection_peer, [], payload,
                                                             choices, deadline,
                                                             check=check_context if guard else None)
            check_context()
            current, current_peers = delivery._peers()
            if current != handles or current_peers[handle] != peers[handle] or delivery._incarnations != incarnations:
                raise RuntimeError('motion preflight membership changed')
            ExperimentalTCPParticipants._quiet(delivery, peers, deadline)
            check_context()
            if response['status'] == 'ready':
                self._preflight = (track_key, peer_identity, sample['sequence'], digest)
            return response['status']
        except BaseException:
            self._failed = True
            delivery._failed = True
            host._failed = True
            raise
        finally:
            lease_changed = host._control._reader_lease is not reader_lease
            if not lease_changed:
                host._control._reader_lease = None
            else:
                self._failed = True
                delivery._failed = True
                host._failed = True
            delivery._active = False
            host._busy = False
            if lease_changed:
                raise RuntimeError('motion preflight reader lease changed')
