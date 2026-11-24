"""Pull one X4 source motion sample over owned loopback UDP into admitted TCP.

The caller binds/validates the source socket using its PID/inode guard before
construction, confirms registry lifetimes separately, and owns all processes,
profile locks and failure fencing. This bridge does not start X4 or a listener.
"""
from contextlib import contextmanager
import copy
import math
import re
import select
import socket
import threading
import time
from .experimental_source_job_bridge import _SOCKET_OWNERS, _SOCKET_LOCK
from .experimental_tcp_motion import (
    ExperimentalTCPMotion, check_motion_progress, motion_value, motion_digest,
)
from .experimental_tcp_participants import ExperimentalTCPParticipants

_TOKEN=re.compile(r'[0-9a-f]{32}\Z')
_NUMBER=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
_MAX_PACKET=768
_MAX=9007199254740991
_SAMPLE_FORMATS = {'SAMPLE': (1, 17), 'SAMPLE2': (2, 23), 'SAMPLE3': (3, 18)}

class ExperimentalSourceMotionIPC:
    """One owned source request at a time; optional replaceable TCP delivery."""
    def __init__(self,sock,motion,*,enabled=False,lease,attempt,epoch,generation,guard,validate_peer,timeout=30,validate_sample=None,select_recipients=None,saved_baseline=None,saved_guard=None,latest=False,saved_lifetime_guard=None,stop_enabled=False,candidate_indices=None,private_long_diagnostic=False,batch_latest_saved_checks=False):
        if (enabled is not True or type(sock) is not socket.socket or sock.family!=socket.AF_INET or
            sock.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_DGRAM or
            sock.getsockname()[0]!='127.0.0.1' or not sock.getsockname()[1] or
            type(motion) is not ExperimentalTCPMotion or
            any(type(x) is not str or not _TOKEN.fullmatch(x) for x in (lease,attempt,epoch,generation)) or
            len({lease,attempt,epoch,generation})!=4 or not callable(guard) or not callable(validate_peer) or
            (validate_sample is not None and not callable(validate_sample)) or
            (select_recipients is not None and not callable(select_recipients)) or
            (saved_lifetime_guard is not None and not callable(saved_lifetime_guard)) or
            (latest and saved_guard is not None and saved_lifetime_guard is None) or
            (saved_lifetime_guard is not None and (not latest or saved_guard is None)) or
            type(batch_latest_saved_checks) is not bool or
            (batch_latest_saved_checks and (not latest or saved_guard is None or saved_lifetime_guard is None)) or
            type(latest) is not bool or type(stop_enabled) is not bool or (latest and (select_recipients is None or
                motion._delivery._host._control._latest_motion is None or motion._delivery._host._control._latest_source is not None or
                motion._delivery._test_binding is not None)) or
            not ((saved_baseline is None and saved_guard is None) or
                 (type(saved_baseline) is int and 1<=saved_baseline<=_MAX and callable(saved_guard))) or
            (candidate_indices is not None and (type(candidate_indices) is not tuple or not 1<=len(candidate_indices)<=32 or
                saved_guard is None or stop_enabled is not True or latest or
                any(type(n) is not int or not 1<=n<=saved_baseline for n in candidate_indices) or
                len(set(candidate_indices))!=len(candidate_indices))) or
            type(private_long_diagnostic) is not bool or
            (private_long_diagnostic and (saved_guard is None or stop_enabled is not True or latest)) or
            type(timeout) not in (int,float) or not math.isfinite(timeout) or
            not 0<timeout<=(600 if private_long_diagnostic else 300) or
            timeout<=motion._delivery._timeout):
            raise ValueError('invalid source motion IPC')
        peer=sock.getpeername()
        if peer[0]!='127.0.0.1' or not peer[1]:raise ValueError('connected source loopback peer required')
        if getattr(motion,'_source_owner',None) is not None:raise ValueError('motion owner already has a source')
        with _SOCKET_LOCK:
            if sock in _SOCKET_OWNERS:raise ValueError('source UDP socket already owned')
            sock.setblocking(False);_SOCKET_OWNERS[sock]=True
        self._sock=sock;self._peer=peer;self._local=sock.getsockname();self._motion=motion
        self._lease=lease;self._attempt=attempt;self._epoch=epoch;self._generation=generation
        self._guard=guard;self._validate=validate_peer;self._validate_sample=validate_sample;self._select_recipients=select_recipients;self._timeout=float(timeout)
        self._state='ready';self._owner=threading.get_ident();self._next=0;self._indices=set();self._saved_indices=set();self._scheduler_owner=None;self._scheduler_lease=None
        self._last_pull=None;self._observed={};self._latest=latest;self._pending_request=None;self._pending_deadline=None;self._pending_barrier_generation=None
        self._saved_baseline=saved_baseline;self._saved_guard=saved_guard;self._saved_lifetime_guard=saved_lifetime_guard;self._lifetime_only=False
        self._stop_enabled=stop_enabled
        self._batch_latest_saved_checks = batch_latest_saved_checks
        self._candidate_indices=candidate_indices;self._candidate_selected=None;self._candidate_attempted=False
        motion._source_owner=self
        if latest:motion._delivery._host._control._latest_source=self
    @property
    def state(self):return self._state
    def _fail(self):
        self._state='failed';self._motion._failed=True
        self._motion._delivery._failed=True;self._motion._delivery._host._failed=True
        try:self._sock.close()
        except OSError:pass
    def _saved_fence(self):
        """Execute the external baseline assertion under the shared reader lease."""
        d=self._motion._delivery;host=d._host;control=host._control
        owned=control._reader_lease is None and host._busy is False and d._active is False
        if owned:
            control._reader_free(motion=self._latest);lease=object();control._reader_lease=lease;host._busy=True;d._active=True
        else:
            lease=control._reader_lease
            if lease is None or host._busy is not True or d._active is not True:
                raise RuntimeError('saved baseline reader fence unavailable')
        try:
            callback=self._saved_lifetime_guard if self._lifetime_only else self._saved_guard
            result=callback(self._epoch,self._saved_baseline)
            if control._reader_lease is not lease or host._busy is not True or d._active is not True:
                raise RuntimeError('saved baseline guard changed reader ownership')
            return result
        finally:
            changed=control._reader_lease is not lease or host._busy is not True or d._active is not True
            if owned:
                if control._reader_lease is lease:control._reader_lease=None
                host._busy=False;d._active=False
            if changed:raise RuntimeError('saved baseline reader fence changed')
    def _call_fenced(self,fn,*args):
        """Forbid callbacks from reentering either socket owner or the world."""
        d=self._motion._delivery;host=d._host;control=host._control
        owned=control._reader_lease is None and host._busy is False and d._active is False
        if owned:
            control._reader_free(motion=self._latest)
            lease=object();control._reader_lease=lease;host._busy=True;d._active=True
        else:
            lease=control._reader_lease
            if lease is None or host._busy is not True or d._active is not True:
                raise RuntimeError('callback reader fence unavailable')
        try:return fn(*args)
        finally:
            changed=control._reader_lease is not lease or host._busy is not True or d._active is not True
            if owned:
                if control._reader_lease is lease:control._reader_lease=None
                host._busy=False;d._active=False
            if changed:raise RuntimeError('callback reader fence changed')

    def _check(self, deadline, phase):
        if (self._state != phase or self._motion._failed or self._motion._delivery._failed or
                self._motion._delivery._host._failed or threading.get_ident() != self._owner or
                time.monotonic() >= deadline):
            raise RuntimeError('source motion state or deadline failed')

        guard_confirmed = self._call_fenced(self._guard)
        if (guard_confirmed is not True or self._state != phase or self._motion._failed or
                self._motion._delivery._failed or self._motion._delivery._host._failed or
                threading.get_ident() != self._owner or time.monotonic() >= deadline or
                self._sock.getsockname() != self._local or self._sock.getpeername() != self._peer):
            raise RuntimeError('source motion guard failed')

        peer_confirmed = self._call_fenced(self._validate, self._peer)
        if (peer_confirmed is not True or self._state != phase or self._motion._failed or
                self._motion._delivery._failed or self._motion._delivery._host._failed or
                threading.get_ident() != self._owner or time.monotonic() >= deadline):
            raise RuntimeError('source motion X4 peer ownership changed')

        if self._saved_guard is not None:
            baseline_confirmed = self._saved_fence()
            # Evaluate the existing conditions in the same short-circuit order.
            # The fixed suffix distinguishes the observed failure without
            # printing checkpoint data, socket addresses, or callback errors.
            reason = None
            if baseline_confirmed is not True:
                reason = 'guard_false'
            elif self._state != phase:
                reason = 'phase_changed'
            elif self._motion._failed:
                reason = 'motion_failed'
            elif self._motion._delivery._failed:
                reason = 'delivery_failed'
            elif self._motion._delivery._host._failed:
                reason = 'host_failed'
            elif threading.get_ident() != self._owner:
                reason = 'thread_changed'
            elif time.monotonic() >= deadline:
                reason = 'operation_deadline'
            elif self._sock.getsockname() != self._local:
                reason = 'local_socket_changed'
            elif self._sock.getpeername() != self._peer:
                reason = 'peer_socket_changed'
            if reason is not None:
                raise RuntimeError('saved baseline guard failed: ' + reason)

            guard_confirmed = self._call_fenced(self._guard)
            if (guard_confirmed is not True or self._state != phase or self._motion._failed or
                    self._motion._delivery._failed or self._motion._delivery._host._failed or
                    time.monotonic() >= deadline):
                raise RuntimeError('source guard changed during baseline check')

            peer_confirmed = self._call_fenced(self._validate, self._peer)
            if (peer_confirmed is not True or self._state != phase or self._motion._failed or
                    self._motion._delivery._failed or self._motion._delivery._host._failed or
                    threading.get_ident() != self._owner or time.monotonic() >= deadline or
                    self._sock.getsockname() != self._local or self._sock.getpeername() != self._peer):
                raise RuntimeError('source peer changed during baseline check')

    def _quiet(self, deadline, phase):
        self._check(deadline, phase)
        self._quiet_after_entry_check(deadline, phase)

    def _quiet_after_entry_check(self, deadline, phase):
        """Check the socket immediately after a caller's completed entry check.

        Keep the post-select check: readiness observation is an I/O boundary,
        and ownership must still be current before sending the next request.
        """
        readable, _, exceptional = select.select([self._sock], [], [self._sock], 0)
        if readable or exceptional:
            raise RuntimeError('unexpected queued source motion packet')
        self._check(deadline, phase)

    def _reserved(self,owner):
        if self._scheduler_owner is not None and (owner is None or owner is not self._scheduler_lease):
            self._fail();raise RuntimeError('source motion bridge is reserved by its scheduler')
    def _motion_call(self,fn,*args,**kwargs):
        lease=object();self._motion._source_lease=lease
        try:return fn(*args,_source=lease,**kwargs)
        finally:self._motion._source_lease=None
    def _select(self,value,deadline):
        """Run one caller selector under the existing TCP-reader fence."""
        d=self._motion._delivery;host=d._host;self._check(deadline,'pulling')
        members=d.membership();handles,peers=d._peers();incarnations=dict(d._incarnations)
        snapshot=tuple((m['id'],m['incarnation']) for m in members)
        if snapshot!=tuple(('peer_'+str(h),incarnations[h]) for h in handles):
            raise RuntimeError('motion membership changed before selection')
        host._control._reader_free(motion=self._latest);lease=object();host._control._reader_lease=lease;host._busy=True;d._active=True
        candidate=copy.deepcopy(value);offered=copy.deepcopy(members)
        try:
            if not self._latest:ExperimentalTCPParticipants._quiet(d,peers,deadline)
            self._check(deadline,'pulling')
            selected=self._select_recipients(candidate,offered)
            self._check(deadline,'pulling')
            if (host._control._reader_lease is not lease or host._busy is not True or d._active is not True or
                d._incarnations!=incarnations or motion_digest(candidate)!=motion_digest(value) or offered!=members):
                raise RuntimeError('motion selector changed protected state')
            if type(selected) not in (tuple,list):raise ValueError('motion selector must return a list or tuple')
            allowed=set(snapshot);seen=set();result=[]
            for peer in selected:
                if (type(peer) is not dict or set(peer)!={'id','incarnation'} or
                    type(peer['id']) is not str or type(peer['incarnation']) is not str):
                    raise ValueError('invalid selected motion recipient')
                key=(peer['id'],peer['incarnation'])
                if key not in allowed or key in seen:raise ValueError('duplicate or foreign motion recipient')
                seen.add(key);result.append({'id':key[0],'incarnation':key[1]})
            if not self._latest:ExperimentalTCPParticipants._quiet(d,peers,deadline)
            self._check(deadline,'pulling')
            if d._incarnations!=incarnations:raise RuntimeError('motion membership changed during selection')
        finally:
            changed=host._control._reader_lease is not lease or host._busy is not True or d._active is not True
            if host._control._reader_lease is lease:host._control._reader_lease=None
            host._busy=False;d._active=False
            if changed:raise RuntimeError('motion selector changed exclusive ownership')
        self._check(deadline,'pulling');current,current_peers=d._peers()
        if current!=handles or d._incarnations!=incarnations or any(current_peers[h]!=peers[h] for h in handles):
            raise RuntimeError('motion membership changed after selection')
        return result
    def bind_confirmed(self,index,*,_scheduler=None):
        """Caller asserts a source event was confirmed by the real job owner."""
        self._reserved(_scheduler)
        if self._state!='ready' or threading.get_ident()!=self._owner or self._candidate_indices is not None:
            self._fail();raise RuntimeError('source motion registration unavailable')
        self._state='registering';deadline=time.monotonic()+self._timeout
        try:
            self._check(deadline,'registering')
            self._motion_call(self._motion.track,self._epoch,self._generation,index)
            self._check(deadline,'registering');self._indices.add(index);self._state='ready';return True
        except BaseException:self._fail();raise
    def register_saved(self, index, *, _scheduler=None):
        """Register a locally resolved saved index, only after correlated X4 confirmation.

        The saved guard is a caller assertion; it does not prove checkpoint bytes
        or cross-process identity. Failure after dispatch has an unknown X4 effect.
        """
        self._reserved(_scheduler)
        if (self._state != 'ready' or threading.get_ident() != self._owner or self._saved_guard is None or
            type(index) is not int or not 1 <= index <= self._saved_baseline or index in self._indices or
            (self._candidate_indices is not None and index != self._candidate_selected)):
            self._fail()
            raise RuntimeError('saved registration unavailable')
        self._state = 'registering'
        deadline = time.monotonic() + self._timeout
        try:
            self._quiet(deadline, 'registering')
            if self._next >= _MAX:
                raise RuntimeError('source motion request capacity')
            self._next += 1
            request_id = str(self._next)
            packet = '|'.join(['P', self._lease, self._attempt, 'REGISTER_SAVED', self._epoch,
                               self._generation, request_id, str(index), str(self._saved_baseline)]).encode('ascii')
            if self._sock.send(packet) != len(packet):
                raise RuntimeError('saved registration dispatch failed')
            while True:
                self._check(deadline, 'registering')
                readable, _, exceptional = select.select(
                    [self._sock], [], [self._sock], min(.1, max(0, deadline - time.monotonic())))
                self._check(deadline, 'registering')
                if exceptional:
                    raise RuntimeError('saved registration socket error')
                if readable:
                    break
            response = self._sock.recv(_MAX_PACKET + 1)
            expected = '|'.join(['S', self._lease, self._attempt, 'SAVED_REGISTERED', self._epoch,
                                 self._generation, request_id, str(index), str(self._saved_baseline)]).encode('ascii')
            if response != expected:
                raise ValueError('foreign saved registration response')
            self._quiet(deadline, 'registering')
            self._motion_call(self._motion.track, self._epoch, self._generation, index)
            self._check(deadline, 'registering')
            self._indices.add(index)
            self._saved_indices.add(index)
            self._state = 'ready'
            return True
        except BaseException:
            self._fail()
            raise

    def pull(self, *, _scheduler=None):
        self._reserved(_scheduler)
        if (self._state != 'ready' or threading.get_ident() != self._owner or
            (self._candidate_indices is not None and self._candidate_selected is None)):
            self._fail()
            raise RuntimeError('source motion reentrant or unavailable')
        self._motion._delivery._host._control._reader_free()
        self._state = 'pulling'
        deadline = time.monotonic() + self._timeout
        try:
            # The sampler can return the same track twice, so this bridge spaces
            # all requests from the completion of its previous pull.
            while self._last_pull is not None:
                remaining = self._last_pull + self._motion._min_interval - time.monotonic()
                if remaining <= 0:
                    break
                self._check(deadline, 'pulling')
                time.sleep(min(.01, remaining))
            self._quiet(deadline, 'pulling')
            if self._next >= _MAX:
                raise RuntimeError('source motion request capacity')
            self._next += 1
            request_id = str(self._next)
            packet = '|'.join(['P', self._lease, self._attempt, 'PULL', self._epoch,
                               self._generation, request_id]).encode('ascii')
            if self._sock.send(packet) != len(packet):
                raise RuntimeError('source motion pull failed')
            while True:
                self._check(deadline, 'pulling')
                readable, _, exceptional = select.select(
                    [self._sock], [], [self._sock], min(.1, max(0, deadline - time.monotonic())))
                self._check(deadline, 'pulling')
                if exceptional:
                    raise RuntimeError('source motion socket error')
                if readable:
                    break
            return self._consume(self._sock.recv(_MAX_PACKET + 1), request_id, deadline, latest=False)
        except BaseException:
            self._fail()
            raise

    def request_candidate(self,*,_scheduler=None):
        """Request one bounded, revalidated index from the same UDP owner.

        Candidate is a transient local observation, not a reservation or a
        cross-X4 identity claim. Inconclusive or ambiguous replies fault all
        participants; registration must independently revalidate the index.
        """
        self._reserved(_scheduler)
        if self._candidate_indices is None:raise RuntimeError('candidate report is not enabled')
        if self._state!='ready' or threading.get_ident()!=self._owner or self._candidate_attempted:
            self._fail();raise RuntimeError('candidate request unavailable')
        self._candidate_attempted=True;self._state='candidate';deadline=time.monotonic()+self._timeout
        try:
            self._quiet(deadline,'candidate')
            if self._next>=_MAX:raise RuntimeError('source motion request capacity')
            self._next+=1;request=str(self._next)
            packet='|'.join(['P',self._lease,self._attempt,'CANDIDATE',self._epoch,self._generation,request]).encode('ascii')
            if self._sock.send(packet)!=len(packet):raise RuntimeError('candidate dispatch failed')
            while True:
                self._check(deadline,'candidate')
                readable,_,bad=select.select([self._sock],[],[self._sock],min(.1,max(0,deadline-time.monotonic())))
                self._check(deadline,'candidate')
                if bad:raise RuntimeError('candidate socket error')
                if readable:break
            raw=self._sock.recv(_MAX_PACKET+1)
            try:parts=raw.decode('ascii').split('|')
            except UnicodeError:raise ValueError('invalid candidate response') from None
            if (len(raw)>_MAX_PACKET or len(parts)!=9 or parts[:7]!=['S',self._lease,self._attempt,'CANDIDATE',self._epoch,self._generation,request]):
                raise ValueError('foreign candidate response')
            if parts[7]=='inconclusive' and parts[8] in ('no_candidate','registry_refused','getter_or_context_unavailable'):
                raise RuntimeError('source candidate inconclusive: '+parts[8])
            if parts[7]!='candidate' or not re.fullmatch(r'[1-9][0-9]*',parts[8]) or len(parts[8])>16:
                raise ValueError('invalid candidate result')
            index=int(parts[8])
            if index not in self._candidate_indices:raise ValueError('candidate not in requested set')
            self._quiet(deadline,'candidate');self._candidate_selected=index;self._state='ready'
            return index
        except BaseException:self._fail();raise
    def _consume(self, data, request, deadline, *, latest):
        if not data or len(data) > _MAX_PACKET:
            raise ValueError('invalid source motion response')
        try:
            parts = data.decode('ascii').split('|')
        except UnicodeError:
            raise ValueError('invalid source motion encoding') from None
        prefix = ['S', self._lease, self._attempt]
        if parts == prefix + ['NO_SAMPLE', self._epoch, self._generation, request]:
            self._quiet(deadline, 'pulling')
            self._last_pull = time.monotonic()
            self._state = 'ready'
            return None
        packet_kind = parts[3] if len(parts) > 3 else None
        packet_format = _SAMPLE_FORMATS.get(packet_kind)
        if packet_format is None:
            raise ValueError('foreign source motion response')
        version, field_count = packet_format
        expected_header = prefix + [packet_kind, self._epoch, self._generation, request]
        if len(parts) != field_count or parts[:7] != expected_header:
            raise ValueError('foreign source motion response')
        full_pose = version == 2
        real_time = version == 3
        index, sequence = parts[7:9]
        if not re.fullmatch(r'[1-9][0-9]*', index) or not re.fullmatch(r'[1-9][0-9]*', sequence):
            raise ValueError('invalid motion identity')
        if int(index) not in self._indices:
            raise ValueError('unconfirmed source motion index')
        position_start = 12 if real_time else 11
        time_and_position_fields = parts[10:11] + parts[position_start:position_start + 3]
        capture_fields = parts[11:12] if real_time else []
        orientation_fields = parts[14:17] if full_pose else []
        velocity_start = 17 if full_pose else position_start + 3
        velocity_fields = parts[velocity_start:velocity_start + 3]
        angular_fields = parts[20:23] if full_pose else []
        unknown_velocity = velocity_fields == ['-', '-', '-']
        if full_pose and (unknown_velocity != (angular_fields == ['-', '-', '-'])):
            raise ValueError('incomplete source motion rate')
        numeric_fields = time_and_position_fields + capture_fields + orientation_fields
        if not unknown_velocity:
            numeric_fields += velocity_fields + angular_fields
        if any(len(item) > 32 or not _NUMBER.fullmatch(item) for item in numeric_fields):
            raise ValueError('invalid source motion numeric syntax')
        try:
            source_time, x, y, z = map(float, time_and_position_fields)
            velocity = None if unknown_velocity else dict(
                zip(('x', 'y', 'z'), map(float, velocity_fields)))
        except ValueError:
            raise ValueError('invalid source motion number') from None
        raw_sample = {'version': version, 'epoch': self._epoch, 'generation': self._generation,
            'index': int(index), 'sequence': int(sequence), 'sector_macro': parts[9],
            'source_time': source_time, 'x': x, 'y': y, 'z': z}
        if real_time:
            raw_sample['capture_time'] = float(capture_fields[0])
            raw_sample['real_velocity'] = velocity
        else:
            raw_sample['velocity'] = velocity
        if full_pose:
            axes = ('yaw', 'pitch', 'roll')
            raw_sample['orientation'] = dict(zip(axes, map(float, orientation_fields)))
            raw_sample['angular_rate'] = None if velocity is None else dict(zip(axes, map(float, angular_fields)))
        sample = motion_value(raw_sample)
        previous = self._observed.get(sample['index'])
        if previous is not None and sample['sequence'] <= previous['sequence']:
            raise ValueError('stale or non-monotone source motion sample')
        check_motion_progress(sample, previous)
        if self._validate_sample is not None:
            candidate = copy.deepcopy(sample)
            self._check(deadline, 'pulling')
            accepted = self._call_fenced(self._validate_sample, candidate)
            self._check(deadline, 'pulling')
            if accepted is not True or motion_digest(candidate) != motion_digest(sample):
                raise ValueError('source motion pre-send validation failed')
        self._quiet(deadline, 'pulling')
        control = self._motion._delivery._host._control
        fenced = latest and (self._pending_barrier_generation != control._latest_motion.generation or
                             control._latest_motion.blocked or control._latest_motion.world_blocked or
                             self._motion._delivery._host._world.session.pending is not None)
        # None selects all recipients; an empty list selects nobody.
        if fenced:
            selected = []
        elif self._select_recipients is None:
            selected = None
        else:
            selected = self._select(sample, deadline)
        if selected == []:
            self._motion_call(self._motion.observe_unselected, sample,
                              _interest_observed=latest and not fenced and self._select_recipients is not None)
        elif latest:
            if self._motion_call(self._motion.enqueue_latest, sample, recipients=selected,
                                 _guard=self._latest_guard, _capture_request=int(request)) is not True:
                selected = []
        elif self._motion_call(self._motion.send, sample, guard=lambda: self._check(deadline, 'pulling') or True,
                               recipients=selected) is not True:
            raise RuntimeError('motion TCP not confirmed')
        # Keep a detached sample so later callbacks cannot change the clock or
        # frame baseline used to validate the next source observation.
        self._observed[sample['index']] = copy.deepcopy(sample)
        self._quiet(deadline, 'pulling')
        self._last_pull = time.monotonic()
        self._state = 'ready'
        return None if selected == [] else sample

    def _latest_guard(self):
        """Revalidate source ownership under the control owner's reader lease."""
        d=self._motion._delivery;host=d._host;control=host._control
        if (self._state not in ('ready','pulling') or control._reader_lease is not None or
            host._busy is not True or d._active is not False):
            self._fail();raise RuntimeError('latest source guard ownership unavailable')
        lease=object();control._reader_lease=lease;d._active=True
        try:
            deadline=(self._pending_deadline if self._state=='pulling' else time.monotonic()+self._timeout)
            previous=self._lifetime_only;self._lifetime_only=True
            try:self._check(deadline,self._state)
            finally:self._lifetime_only=previous
        except BaseException:self._fail();raise
        finally:
            changed=control._reader_lease is not lease or host._busy is not True or d._active is not True
            if control._reader_lease is lease:control._reader_lease=None
            d._active=False
            if changed:
                self._fail();raise RuntimeError('latest source guard changed ownership')

    @contextmanager
    def _latest_saved_checks(self, deadline, phase, *, obsolete=False):
        """Check entry once, adding a full exit check in explicit batch mode."""
        if not self._batch_latest_saved_checks:
            self._check(deadline, phase)
            yield
            return
        previous = self._lifetime_only
        try:
            # An obsolete reply must remain drainable after its old baseline
            # has changed. Its existing checks validate lifetime and discard it.
            self._lifetime_only = obsolete
            self._check(deadline, phase)
            self._lifetime_only = True
            yield
            self._lifetime_only = obsolete
            self._check(deadline, self._state)
        finally:
            self._lifetime_only = previous

    def begin_latest(self,*,_scheduler=None):
        """Start one bounded UDP sample without waiting for X4 or any recipient.

        The caller must continue pumping the control owner and call poll_latest.
        False means lifecycle/rate fencing delayed dispatch; nothing was sent.
        """
        self._reserved(_scheduler)
        if not self._latest or self._state!='ready' or threading.get_ident()!=self._owner:
            self._fail();raise RuntimeError('latest source pull unavailable')
        control=self._motion._delivery._host._control
        control._reader_free(motion=True)
        gate=control._latest_motion
        world=self._motion._delivery._host._world.session
        if (gate.blocked or gate.world_blocked or world.pending is not None or world.state!='running' or
            not world.ready_players or len(world.ready_players)!=len(world.players) or
            (self._last_pull is not None and time.monotonic()-self._last_pull<self._motion._min_interval)):
            return False
        self._state='pulling';deadline=time.monotonic()+self._timeout
        try:
            with self._latest_saved_checks(deadline, 'pulling'):
                self._quiet_after_entry_check(deadline, 'pulling')
                if self._next>=_MAX:raise RuntimeError('source motion request capacity')
                self._next+=1;request=str(self._next)
                packet='|'.join(['P',self._lease,self._attempt,'PULL',self._epoch,self._generation,request]).encode('ascii')
                if self._sock.send(packet)!=len(packet):raise RuntimeError('source motion pull failed')
                self._pending_request=request;self._pending_deadline=deadline;self._pending_barrier_generation=gate.generation
                return True
        except BaseException:self._fail();raise
    def poll_latest(self,*,_scheduler=None):
        """Poll source readiness once; caller guards can perform bounded I/O.

        A sample completed under a world/lifecycle barrier is validated then
        discarded. Completion means local queue acceptance, not client receipt.
        """
        self._reserved(_scheduler)
        if not self._latest or self._state!='pulling' or threading.get_ident()!=self._owner:
            self._fail();raise RuntimeError('no pending latest source pull')
        deadline=self._pending_deadline;request=self._pending_request
        previous = self._lifetime_only
        try:
            control=self._motion._delivery._host._control
            control._reader_free(motion=True)
            obsolete = self._pending_barrier_generation != control._latest_motion.generation
            self._lifetime_only = obsolete
            with self._latest_saved_checks(deadline, 'pulling', obsolete=obsolete):
                readable,_,bad=select.select([self._sock],[],[self._sock],0)
                self._check(deadline,'pulling')
                if bad:raise RuntimeError('source motion socket error')
                if not readable:return (False,None)
                value=self._consume(self._sock.recv(_MAX_PACKET+1),request,deadline,latest=True)
                self._pending_request=None;self._pending_deadline=None;self._pending_barrier_generation=None
                return (True,value)
        except BaseException:self._fail();raise
        finally:self._lifetime_only=previous
    def preflight(self,sample,*,_scheduler=None):
        """Check a confirmed saved sample without requesting a local write."""
        self._reserved(_scheduler)
        if self._state!='ready' or threading.get_ident()!=self._owner or self._saved_guard is None:
            self._fail();raise RuntimeError('saved motion preflight unavailable')
        self._state='preflighting';deadline=time.monotonic()+self._timeout
        try:
            value=motion_value(sample)
            if value['index'] not in self._saved_indices:raise ValueError('unregistered saved actor')
            self._quiet(deadline,'preflighting')
            status=self._motion_call(self._motion.preflight,value,guard=lambda:self._check(deadline,'preflighting') or True)
            if status not in ('near','ready'):raise RuntimeError('motion preflight unconfirmed')
            self._quiet(deadline,'preflighting');self._state='ready';return status
        except BaseException:self._fail();raise
    def apply_once(self,sample,*,_scheduler=None):
        """Opt-in local writer request for a previously confirmed saved sample."""
        self._reserved(_scheduler)
        if self._state!='ready' or threading.get_ident()!=self._owner or self._saved_guard is None:
            self._fail();raise RuntimeError('saved motion apply unavailable')
        self._state='applying';deadline=time.monotonic()+self._timeout
        try:
            value=motion_value(sample)
            if value['index'] not in self._saved_indices:raise ValueError('unregistered saved actor')
            self._quiet(deadline,'applying')
            if self._motion_call(self._motion.apply_once,value,guard=lambda:self._check(deadline,'applying') or True) is not True:
                raise RuntimeError('motion readback not confirmed')
            self._quiet(deadline,'applying');self._state='ready';return True
        except BaseException:self._fail();raise
    def stop_remote(self, *, _scheduler=None):
        """Confirm a cooperative X4-side stop before allowing world pause.

        STOP is opt-in and valid only with no pull or latest-motion work in
        flight. The X4 caller must close its sampler, bootstrap and event
        router before acknowledging. Lost or ambiguous receipts fault the host;
        this method does not prove client identity or game-world consistency.
        """
        self._reserved(_scheduler)
        if not self._stop_enabled:
            raise RuntimeError('remote source stop is not enabled')
        if self._state != 'ready' or threading.get_ident() != self._owner:
            self._fail()
            raise RuntimeError('source motion cannot stop')
        if self._latest and not self._motion._delivery._host._control._motion_idle():
            self._fail()
            raise RuntimeError('drain latest motion before stopping source')
        self._state = 'stopping'
        deadline = time.monotonic() + self._timeout
        try:
            self._quiet(deadline, 'stopping')
            if self._next >= _MAX:
                raise RuntimeError('source motion request capacity')
            self._next += 1
            request_id = str(self._next)
            packet = '|'.join(['P', self._lease, self._attempt, 'STOP', self._epoch,
                               self._generation, request_id]).encode('ascii')
            if self._sock.send(packet) != len(packet):
                raise RuntimeError('source motion stop dispatch failed')
            while True:
                self._check(deadline, 'stopping')
                readable, _, exceptional = select.select(
                    [self._sock], [], [self._sock], min(.1, max(0, deadline - time.monotonic())))
                self._check(deadline, 'stopping')
                if exceptional:
                    raise RuntimeError('source motion socket error')
                if readable:
                    break
            response = self._sock.recv(_MAX_PACKET + 1)
            expected = '|'.join(['S', self._lease, self._attempt, 'STOPPED', self._epoch,
                                 self._generation, request_id]).encode('ascii')
            if response != expected:
                raise ValueError('foreign source motion stop response')
            self._quiet(deadline, 'stopping')
            self._state = 'remote_stopped'
            return True
        except BaseException:
            self._fail()
            raise

    def close(self,*,_scheduler=None):
        self._reserved(_scheduler)
        if (self._state not in ('ready','remote_stopped') or threading.get_ident()!=self._owner or
                (self._stop_enabled and self._state!='remote_stopped')):
            self._fail();raise RuntimeError('source motion cannot close without confirmed remote stop')
        if self._latest and not self._motion._delivery._host._control._motion_idle():
            raise RuntimeError('drain latest motion before closing source')
        self._state='closed'
        if self._latest:self._motion._delivery._host._control._latest_source=None
        with _SOCKET_LOCK:_SOCKET_OWNERS.pop(self._sock,None)
        self._sock.close()
