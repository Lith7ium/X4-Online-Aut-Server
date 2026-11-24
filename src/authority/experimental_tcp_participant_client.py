"""Pumpable, loopback-only experiment for cooperative participant control.

The caller owns this connected socket after consuming the load-accepted frame,
the X4 process and the injected synchronous controller. A controller result is
a local assertion; this module does not establish process or peer identity.
"""
import copy
import json
import math
import re
import select
import socket
import threading
import time

from .load_messages import MAX_INTEGER, MAX_MESSAGE, _pairs, _reject_constant
from .experimental_tcp_job_delivery import job_event, job_digest


_REENTRY_REPLIES = {
    'motion_reentry_prepare': ('motion_reentry_prepared_receipt', 'prepared'),
    'motion_reentry_align': ('motion_reentry_aligned_receipt', 'aligned'),
    'motion_reentry_cancel': ('motion_reentry_canceled_receipt', 'canceled'),
}


def validate_motion_reentry_controller(controller, apply_motion):
    if controller is not None and (not callable(apply_motion) or
            not callable(getattr(controller, 'prepare', None)) or
            not callable(getattr(controller, 'align', None)) or
            not callable(getattr(controller, 'cancel', None))):
        raise ValueError('motion reentry requires a motion consumer and prepare, align and cancel callbacks')


def validate_participant_connection(conn, controller, *, enabled, epoch, handle, timeout):
    """Validate known participant prerequisites without changing the socket."""
    if (enabled is not True or type(conn) is not socket.socket or conn.family != socket.AF_INET or
            conn.proto not in (0, socket.IPPROTO_TCP) or
            conn.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
            type(epoch) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', epoch) or
            type(handle) is not int or not 1 <= handle <= MAX_INTEGER or
            type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300 or
            not callable(getattr(controller, 'pause', None)) or
            not callable(getattr(controller, 'resume', None))):
        raise ValueError('enabled client, controller and bounded configuration required')
    if conn.getpeername()[0] != '127.0.0.1':
        raise ValueError('experimental client requires loopback peer')


class ExperimentalTCPParticipantClient:
    """Accept one command at a time, and reply only after local confirmation.

    The controller must bound its own synchronous pause/resume calls. This
    adapter checks its deadline after a call but cannot interrupt that call.
    The optional motion reentry controller also runs synchronously. Its
    prepare(previous_sector, sample) must check the local identity and target
    binding without writing. Its align(previous_sector, prepared_sample, sample)
    must confirm the actual alignment on that same binding from the fresh pose.
    Its cancel(previous_sector, prepared_sample) must discard a preparation
    without writing. Each callback returns exactly True on confirmation. Closing
    or faulting discards protocol state; the caller owns controller cleanup and
    X4 process fencing.
    """
    def __init__(self, conn, controller, *, enabled=False, epoch, handle, timeout=30, apply_job=None, apply_motion=None, apply_motion_once=None, preflight_motion=None, authority_grant=None, authority_guard=None, pull_authority_motion=None,
                 motion_reentry_controller=None):
        validate_motion_reentry_controller(motion_reentry_controller, apply_motion)
        validate_participant_connection(conn, controller, enabled=enabled,
                                        epoch=epoch, handle=handle, timeout=timeout)
        conn.setblocking(False)
        self._conn, self._controller, self._epoch, self._handle = conn, controller, epoch, handle
        self._timeout = float(timeout)
        self._thread = threading.get_ident()
        self._in = self._out = b''
        self._deadline = None
        self._last = 0
        if apply_job is not None and not callable(apply_job):
            raise ValueError('job application must be callable')
        if apply_motion is not None and not callable(apply_motion):
            raise ValueError('motion consumer must be callable')
        if apply_motion_once is not None and not callable(apply_motion_once):
            raise ValueError('one-shot motion writer must be callable')
        if preflight_motion is not None and not callable(preflight_motion):raise ValueError('motion preflight must be callable')
        if any(v is not None for v in (authority_grant,authority_guard,pull_authority_motion)):
            from .experimental_tcp_client_authority_motion import grant_value
            if not callable(authority_guard) or not callable(pull_authority_motion):raise ValueError('authority pull needs guard and callback')
            authority_grant=grant_value(authority_grant)
            if authority_grant['session_epoch']!=epoch:raise ValueError('authority grant session differs')
        self._apply_job=apply_job;self._job_incarnation=None;self._last_job=None
        self._apply_motion=apply_motion;self._motion_incarnation=None;self._motion_tracks={};self._motion_current={}
        self._apply_motion_once=apply_motion_once;self._preflight_motion=preflight_motion;self._motion_preflight=None;self._motion_write_attempted=False
        self._authority_grant=authority_grant;self._authority_guard=authority_guard;self._pull_authority_motion=pull_authority_motion
        self._authority_grant_wire=None if authority_grant is None else json.dumps(authority_grant,sort_keys=True,separators=(',',':'))
        self._authority_request=0;self._authority_sequence=0;self._authority_time=None
        self._motion_reentry_controller = motion_reentry_controller
        self._prepared_reentry = None
        self._failed = self._closed = self._busy = False

    def _fault(self):
        self._failed = True
        self._prepared_reentry = None
        try: self._conn.close()
        except BaseException: pass

    def _owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive owner thread required')
        if self._failed or self._closed:
            raise RuntimeError('participant client unavailable; fence local controller')
        if self._busy:
            self._fault()
            raise RuntimeError('reentrant participant client access')

    def _within(self):
        if self._failed or self._closed:
            raise RuntimeError('participant client changed during operation')
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError('participant operation deadline exceeded')

    def _quiet(self):
        readable, _, exceptional = select.select([self._conn], [], [self._conn], 0)
        if readable or exceptional:
            raise RuntimeError('unexpected input or disconnect during participant operation')
        self._within()

    def _command(self, raw):
        try:
            command = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('invalid participant command') from error
        if type(command) is dict and command.get('kind')=='job_delivery':
            return self._job(command)
        if type(command) is dict and command.get('kind') in ('motion_snapshot','motion_despawn'):
            return self._motion(command)
        if type(command) is dict and type(command.get('kind')) is str and command['kind'] in _REENTRY_REPLIES:
            return self._motion_reentry(command)
        if type(command) is dict and command.get('kind')=='motion_apply':
            return self._motion_apply(command)
        if type(command) is dict and command.get('kind')=='motion_preflight':
            return self._motion_preflight_command(command)
        if type(command) is dict and command.get('kind')=='authority_motion_pull':
            return self._authority_motion_pull(command)
        fields = {'version', 'kind', 'epoch', 'connection_handle', 'request_id', 'operation'}
        if (type(command) is not dict or set(command) != fields or
                type(command['version']) is not int or command['version'] != 1 or
                command['kind'] != 'participant_command' or command['epoch'] != self._epoch or
                type(command['connection_handle']) is not int or command['connection_handle'] != self._handle or
                type(command['request_id']) is not int or not self._last < command['request_id'] <= MAX_INTEGER or
                command['operation'] not in ('pause', 'resume')):
            raise ValueError('unexpected or stale participant command')
        if self._prepared_reentry is not None:
            raise RuntimeError('cancel pending motion reentry before participant control')
        self._quiet()
        self._last = command['request_id']  # never dispatch it twice
        confirmed = getattr(self._controller, command['operation'])((self._handle,))
        self._within()
        self._quiet()
        if confirmed is not True:
            raise RuntimeError('local participant state not confirmed')
        reply = dict(command, kind='participant_receipt', status='confirmed')
        self._out = json.dumps(reply, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'

    def _job(self, command):
        fields={'version','kind','connection_handle','request_id','session_epoch','incarnation','event'}
        if (self._apply_job is None or set(command)!=fields or type(command.get('version')) is not int or
                command['version']!=1 or type(command.get('connection_handle')) is not int or
                command['connection_handle']!=self._handle or command.get('session_epoch')!=self._epoch or
                type(command.get('request_id')) is not int or type(command.get('incarnation')) is not str or
                not re.fullmatch(r'[0-9a-f]{32}',command['incarnation'])):
            raise ValueError('invalid job command')
        event=job_event(command['event'])
        if (command['request_id']!=event['sequence'] or
                self._authority_grant is not None and self._authority_grant['incarnation']!=command['incarnation'] or
                self._motion_incarnation is not None and self._motion_incarnation!=command['incarnation'] or
                self._job_incarnation is not None and self._job_incarnation!=command['incarnation'] or
                self._last_job is not None and (event['epoch'],event['generation'])!=self._last_job[:2] or
                self._last_job is not None and event['sequence']!=self._last_job[2]+1):
            raise ValueError('stale job command')
        self._quiet()
        self._job_incarnation=command['incarnation']
        self._last_job=(event['epoch'],event['generation'],event['sequence'])
        digest=job_digest(event)
        applied=copy.deepcopy(event)
        confirmed=self._apply_job(applied)
        self._within();self._quiet()
        if job_digest(applied)!=digest:raise ValueError('job application mutated its event')
        if confirmed is not True:raise RuntimeError('local job application not confirmed')
        reply={k:command[k] for k in ('version','connection_handle','request_id','session_epoch','incarnation')}
        reply.update(kind='job_receipt',status='applied',event_sha256=digest)
        self._out=json.dumps(reply,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()+b'\n'

    def _motion(self, command):
        # The callback must bind the portable identity to the local X4 actor;
        # this adapter itself cannot establish a game binding.
        from .experimental_tcp_motion import check_motion_progress, motion_value, motion_digest
        required_fields = {'version', 'kind', 'connection_handle', 'session_epoch',
                           'incarnation', 'request_id', 'sample'}
        if (self._apply_motion is None or set(command) != required_fields or
                type(command.get('version')) is not int or command['version'] != 1 or
                type(command.get('connection_handle')) is not int or
                command['connection_handle'] != self._handle or command.get('session_epoch') != self._epoch or
                type(command.get('incarnation')) is not str or
                not re.fullmatch(r'[0-9a-f]{32}', command['incarnation']) or
                self._motion_incarnation is not None and self._motion_incarnation != command['incarnation'] or
                self._job_incarnation is not None and self._job_incarnation != command['incarnation'] or
                self._authority_grant is not None and self._authority_grant['incarnation'] != command['incarnation']):
            raise ValueError('invalid motion command')

        is_despawn = command['kind'] == 'motion_despawn'
        sample = motion_value(command['sample'], despawn=is_despawn)
        if type(command['request_id']) is not int or command['request_id'] != sample['sequence']:
            raise ValueError('motion request identity mismatch')
        track_key = (sample['epoch'], sample['generation'], sample['index'])
        prior_track = self._motion_tracks.get(track_key)
        if is_despawn and self._prepared_reentry is not None and self._prepared_reentry['track'] == track_key:
            raise RuntimeError('cancel pending motion reentry before despawn')
        if prior_track is None:
            if is_despawn or len(self._motion_tracks) >= 100000:
                raise ValueError('unknown motion lifetime')
            current_track = self._motion_current.get((sample['epoch'], sample['index']))
            if current_track is not None and not self._motion_tracks[current_track]['closed']:
                raise ValueError('previous motion lifetime active')
        elif prior_track['closed'] or sample['sequence'] <= prior_track['sequence']:
            raise ValueError('stale motion lifetime')
        if not is_despawn and prior_track:
            if self._motion_reentry_controller is not None and sample['sector_macro'] != prior_track['frame']:
                raise ValueError('motion frame changed without alignment')
            check_motion_progress(sample, self._previous_snapshot(prior_track))

        self._quiet()
        self._motion_incarnation = command['incarnation']
        sample_digest = motion_digest(sample, despawn=is_despawn)
        callback_sample = copy.deepcopy(sample)
        callback_confirmed = self._apply_motion(callback_sample, is_despawn)
        self._within()
        self._quiet()
        if motion_digest(callback_sample, despawn=is_despawn) != sample_digest:
            raise ValueError('motion callback mutated sample')
        if callback_confirmed is not True:
            raise RuntimeError('motion sample not accepted')
        self._record_motion(track_key, sample, sample_digest, is_despawn)
        receipt = {k: command[k] for k in ('version', 'connection_handle', 'session_epoch', 'incarnation', 'request_id')}
        receipt.update(kind='motion_receipt', status='accepted', sample_sha256=sample_digest)
        self._out = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode() + b'\n'

    @staticmethod
    def _previous_snapshot(track):
        return {'version': track['version'], 'source_time': track['time'],
                'capture_time': track['capture_time'], 'sector_macro': track['frame']}

    def _record_motion(self, key, sample, digest, despawn=False):
        self._motion_preflight = None
        track = self._motion_tracks.setdefault(key, {})
        track.update(sequence=sample['sequence'], closed=despawn, version=None if despawn else sample['version'],
                     capture_time=sample.get('capture_time'), time=sample.get('source_time'),
                     frame=sample.get('sector_macro'), digest=None if despawn else digest)
        self._motion_current[(sample['epoch'], sample['index'])] = key

    def _motion_reentry(self, command):
        from .experimental_tcp_motion import check_motion_progress, motion_value, motion_digest

        kind = command['kind']
        prepare = kind == 'motion_reentry_prepare'
        cancel = kind == 'motion_reentry_cancel'
        fields = {'version', 'kind', 'connection_handle', 'session_epoch',
                  'incarnation', 'request_id', 'previous_sector'}
        extra_fields = {'sample_sha256'} if cancel else {'sample'}
        if not prepare:
            extra_fields.add('prepared_sequence')
        if (self._motion_reentry_controller is None or set(command) != fields | extra_fields or
                type(command['version']) is not int or command['version'] != 1 or
                type(command['connection_handle']) is not int or command['connection_handle'] != self._handle or
                command['session_epoch'] != self._epoch or self._motion_incarnation is None or
                command['incarnation'] != self._motion_incarnation or type(command['request_id']) is not int or
                (not prepare and type(command['prepared_sequence']) is not int)):
            raise ValueError('invalid motion reentry command')

        previous_sector = command['previous_sector']
        pending = self._prepared_reentry
        if cancel:
            if (pending is None or previous_sector != pending['previous_sector'] or
                    command['request_id'] != command['prepared_sequence'] or
                    command['prepared_sequence'] != pending['sample']['sequence'] or
                    command['sample_sha256'] != pending['digest']):
                raise ValueError('motion reentry cancellation differs from prepare')
            self._quiet()
            prepared_candidate = copy.deepcopy(pending['sample'])
            confirmed = self._motion_reentry_controller.cancel(previous_sector, prepared_candidate)
            self._within()
            self._quiet()
            if confirmed is not True or motion_digest(prepared_candidate) != pending['digest']:
                raise RuntimeError('motion reentry cancellation not confirmed')
            self._prepared_reentry = None
            receipt = dict(command)
        else:
            sample = motion_value(command['sample'])
            key = (sample['epoch'], sample['generation'], sample['index'])
            prior = self._motion_tracks.get(key)
            if (prior is None or prior['closed'] or sample['sequence'] != command['request_id'] or
                    sample['sequence'] <= prior['sequence'] or previous_sector != prior['frame'] or
                    sample['sector_macro'] == previous_sector):
                raise ValueError('motion reentry differs from the confirmed track')
            previous = self._previous_snapshot(prior)
            if prepare:
                last_prepared = prior.get('last_prepared')
                if pending is not None or (last_prepared is not None and
                        sample['sequence'] <= last_prepared['sequence']):
                    raise ValueError('stale or overlapping motion reentry prepare')
                check_motion_progress(sample, previous)
                check_motion_progress(sample, last_prepared)
            else:
                if (pending is None or key != pending['track'] or
                        command['prepared_sequence'] != pending['sample']['sequence'] or
                        sample['sequence'] <= command['prepared_sequence'] or
                        sample['sector_macro'] != pending['sample']['sector_macro']):
                    raise ValueError('motion reentry alignment differs from prepare')
                check_motion_progress(sample, pending['sample'])
                # Check clocks against any confirmed old-sector sample too. Rates in this
                # sample are measured between observations in the destination sector.
                check_motion_progress(sample, dict(previous, sector_macro=sample['sector_macro']))

            self._quiet()
            digest = motion_digest(sample)
            candidate = copy.deepcopy(sample)
            controller = self._motion_reentry_controller
            if prepare:
                confirmed = controller.prepare(previous_sector, candidate)
            else:
                prepared_candidate = copy.deepcopy(pending['sample'])
                confirmed = controller.align(previous_sector, prepared_candidate, candidate)
            self._within()
            self._quiet()
            if (confirmed is not True or motion_digest(candidate) != digest or
                    not prepare and motion_digest(prepared_candidate) != pending['digest']):
                raise RuntimeError('motion reentry not confirmed')
            if prepare:
                self._motion_preflight = None
                self._prepared_reentry = dict(track=key, sample=sample, digest=digest,
                                              previous_sector=previous_sector)
                prior['last_prepared'] = {name: sample[name] for name in
                                          ('sequence', 'version', 'source_time', 'sector_macro')}
                prior['last_prepared']['capture_time'] = sample.get('capture_time')
            else:
                self._record_motion(key, sample, digest)
                self._prepared_reentry = None
            receipt = {name: value for name, value in command.items() if name != 'sample'}
            receipt['sample_sha256'] = digest

        reply_kind, status = _REENTRY_REPLIES[kind]
        receipt.update(kind=reply_kind, status=status)
        self._out = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

    def _motion_apply(self, command):
        from .experimental_tcp_motion import motion_value, motion_digest
        fields = {'version', 'kind', 'connection_handle', 'session_epoch',
                  'incarnation', 'request_id', 'sample'}
        if (self._apply_motion_once is None or self._motion_write_attempted or set(command) != fields or
                type(command.get('version')) is not int or command['version'] != 1 or
                type(command.get('connection_handle')) is not int or command['connection_handle'] != self._handle or
                command.get('session_epoch') != self._epoch or type(command.get('incarnation')) is not str or
                command['incarnation'] != self._motion_incarnation):
            raise ValueError('one-shot motion apply unavailable')
        sample = motion_value(command['sample'])
        if sample['version'] != 1:
            raise ValueError('full-pose motion apply is not implemented')
        track_key = (sample['epoch'], sample['generation'], sample['index'])
        previous_track = self._motion_tracks.get(track_key)
        digest = motion_digest(sample)
        if (type(command.get('request_id')) is not int or command['request_id'] != sample['sequence'] or
                self._prepared_reentry is not None and self._prepared_reentry['track'] == track_key or
                previous_track is None or previous_track['closed'] or previous_track['sequence'] != sample['sequence'] or
                previous_track['digest'] != digest or self._motion_preflight != (track_key, sample['sequence'], digest)):
            raise ValueError('motion apply differs from last confirmed sample')
        self._quiet()
        self._motion_write_attempted = True
        candidate = copy.deepcopy(sample)
        accepted = self._apply_motion_once(candidate)
        self._within()
        self._quiet()
        if motion_digest(candidate) != digest or accepted is not True:
            raise RuntimeError('motion readback not confirmed')
        reply = {
            key: command[key] for key in
            ('version', 'connection_handle', 'session_epoch', 'incarnation', 'request_id')
        }
        reply.update(kind='motion_applied_receipt', status='applied', sample_sha256=digest)
        self._out = json.dumps(reply, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

    def _motion_preflight_command(self, command):
        from .experimental_tcp_motion import motion_value, motion_digest
        fields = {'version', 'kind', 'connection_handle', 'session_epoch',
                  'incarnation', 'request_id', 'sample'}
        self._motion_preflight = None
        if (self._preflight_motion is None or self._motion_write_attempted or set(command) != fields or
                type(command.get('version')) is not int or command['version'] != 1 or
                type(command.get('connection_handle')) is not int or command['connection_handle'] != self._handle or
                command.get('session_epoch') != self._epoch or type(command.get('incarnation')) is not str or
                command['incarnation'] != self._motion_incarnation):
            raise ValueError('motion preflight unavailable')
        sample = motion_value(command['sample'])
        if sample['version'] != 1:
            raise ValueError('full-pose motion preflight is not implemented')
        track_key = (sample['epoch'], sample['generation'], sample['index'])
        previous_track = self._motion_tracks.get(track_key)
        digest = motion_digest(sample)
        if (type(command.get('request_id')) is not int or command['request_id'] != sample['sequence'] or
                self._prepared_reentry is not None and self._prepared_reentry['track'] == track_key or
                previous_track is None or previous_track['closed'] or previous_track['sequence'] != sample['sequence'] or
                previous_track['digest'] != digest):
            raise ValueError('motion preflight differs from last confirmed sample')
        self._quiet()
        candidate = copy.deepcopy(sample)
        status = self._preflight_motion(candidate)
        self._within()
        self._quiet()
        if motion_digest(candidate) != digest or status not in ('NEAR', 'READY'):
            raise RuntimeError('motion preflight not confirmed')
        if status == 'READY':
            self._motion_preflight = (track_key, sample['sequence'], digest)
        reply = {
            key: command[key] for key in
            ('version', 'connection_handle', 'session_epoch', 'incarnation', 'request_id')
        }
        reply.update(kind='motion_preflight_receipt', status=status.lower(), sample_sha256=digest)
        self._out = json.dumps(reply, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

    def _authority_motion_pull(self, command):
        from .experimental_tcp_client_authority_motion import grant_value
        from .experimental_tcp_motion import motion_value, motion_digest
        fields = {'version', 'kind', 'connection_handle', 'session_epoch',
                  'incarnation', 'request_id', 'grant'}
        if (self._authority_grant is None or set(command) != fields or
                type(command.get('version')) is not int or command['version'] != 1 or
                json.dumps(self._authority_grant, sort_keys=True, separators=(',', ':')) != self._authority_grant_wire or
                type(command.get('connection_handle')) is not int or command['connection_handle'] != self._handle or
                command.get('session_epoch') != self._epoch or
                command.get('incarnation') != self._authority_grant['incarnation'] or
                self._job_incarnation is not None and self._job_incarnation != command['incarnation'] or
                self._motion_incarnation is not None and self._motion_incarnation != command['incarnation'] or
                type(command.get('request_id')) is not int or command['request_id'] != self._authority_request + 1 or
                command['request_id'] > MAX_INTEGER or grant_value(command.get('grant')) != self._authority_grant):
            raise ValueError('invalid authority pull command')

        def guard():
            grant_copy = copy.deepcopy(self._authority_grant)
            previous_state = (self._authority_request, self._authority_sequence, self._authority_time)
            if (self._authority_guard(grant_copy) is not True or
                    json.dumps(grant_copy, sort_keys=True, separators=(',', ':')) != self._authority_grant_wire or
                    json.dumps(self._authority_grant, sort_keys=True, separators=(',', ':')) != self._authority_grant_wire or
                    previous_state != (self._authority_request, self._authority_sequence, self._authority_time)):
                raise RuntimeError('authority grant guard changed')
            self._within()

        self._quiet()
        guard()
        self._authority_request = command['request_id']
        raw_sample = self._pull_authority_motion()
        sample = None if raw_sample is None else motion_value(raw_sample)
        digest = None if sample is None else motion_digest(sample)
        guard()
        self._quiet()
        if sample is not None:
            if motion_digest(raw_sample) != digest:
                raise ValueError('authority sample changed during guard')
            if (sample['version'] not in (1, 2) or sample['epoch'] != self._authority_grant['epoch'] or
                    sample['generation'] != self._authority_grant['generation'] or
                    sample['index'] != self._authority_grant['index'] or
                    sample['sector_macro'] != self._authority_grant['sector_macro'] or
                    sample['sequence'] != self._authority_sequence + 1 or
                    (self._authority_time is None and sample['velocity'] is not None) or
                    (self._authority_time is not None and sample['source_time'] <= self._authority_time)):
                raise ValueError('foreign or stale authority sample')
            self._authority_sequence = sample['sequence']
            self._authority_time = sample['source_time']
        reply = {
            key: command[key] for key in
            ('version', 'connection_handle', 'session_epoch', 'incarnation', 'request_id', 'grant')
        }
        reply.update(kind='authority_motion_result', sample=sample, sample_sha256=digest)
        self._out = json.dumps(reply, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

    def poll(self, timeout=0):
        """Pump bounded I/O once; True means a receipt was sent to the socket."""
        self._owner()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 1:
            raise ValueError('invalid poll timeout')
        self._busy = True
        try:
            self._within()
            wait = timeout if self._deadline is None else min(timeout, max(0, self._deadline-time.monotonic()))
            readable, writable, exceptional = select.select([self._conn], [self._conn] if self._out else [], [self._conn], wait)
            self._within()
            if exceptional: raise RuntimeError('participant socket exception')
            if readable:
                if self._out: raise RuntimeError('pipelined participant command')
                try: chunk = self._conn.recv(MAX_MESSAGE+1-len(self._in))
                except BlockingIOError: chunk = None
                if chunk == b'': raise RuntimeError('participant peer disconnected')
                if chunk:
                    if self._deadline is None: self._deadline = time.monotonic()+self._timeout
                    self._in += chunk
                    if b'\n' in self._in:
                        frame, tail = self._in.split(b'\n', 1)
                        if tail or len(frame) > MAX_MESSAGE: raise ValueError('invalid participant framing')
                        self._in = b''
                        self._command(frame)
                    elif len(self._in) > MAX_MESSAGE:
                        raise ValueError('participant command too large')
            if writable and self._out:
                try: count = self._conn.send(self._out)
                except BlockingIOError: return False
                if count <= 0: raise RuntimeError('participant receipt send failed')
                self._out = self._out[count:]
                self._within()
                if not self._out:
                    self._deadline = None
                    return True
            return False
        except BaseException:
            self._fault()
            raise
        finally:
            self._busy = False

    def close(self):
        self._owner()
        self._closed = True
        self._prepared_reentry = None
        self._conn.close()
