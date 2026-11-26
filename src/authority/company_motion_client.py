"""Publish the selected cockpit and receive explicitly observed ships over the TLS bridge."""
from copy import deepcopy
import re
import socket
import time

from .company_limits import MAX_COCKPIT_PLAYBACK_PILOTS
from .company_motion_frames import (BIND_TIMEOUT, MAX_COUNTER, encode_frame,
                                    normalize_views, read_frames, validate_pilot_roster, view_matches)
from .company_npc_measurement import (MEASUREMENT_KIND, NPC_CODE, NPC_INDEX, PILOT_INDEX,
                                      RADAR_ENDED, REGISTRY_EPOCH, SECTOR, measurement_value, native_capture_now)
from .experimental_tcp_motion import REAL_TIME_MOTION_VERSIONS, motion_value
from .trusted_load_messages import validate_ship_motion_credential

_HEX32 = re.compile(r'[0-9a-f]{32}')


class CompanyMotionClient:
    """Poll on the player owner thread. Only one sample awaits a receipt in each direction."""

    def __init__(self, port, credential, source, validate_peer, *, on_snapshot=None, on_end=None,
                 on_view_reset=None, on_transport_lost=None, on_clock=None, trial_index=None,
                 resume_route=None, cockpit_playback_limit=None, resume_routes=None,
                 on_pilot_roster=None, _latest_seen=None, npc_measurement_source=None):
        epoch, index = source.registry
        if any(callback is not None and not callable(callback)
               for callback in (on_view_reset, on_transport_lost, on_clock, on_pilot_roster)):
            raise ValueError('invalid movement observation callback')
        if npc_measurement_source is not None and (
                not callable(npc_measurement_source) or (epoch, index) != (REGISTRY_EPOCH, PILOT_INDEX)
                or getattr(source, 'observed_npc', None) != (NPC_INDEX, NPC_CODE)
                or getattr(source, 'registered_npc_hold_trial', False)
                or trial_index is not None or cockpit_playback_limit is not None):
            raise ValueError('private NPC measurement requires the designated radar-only pilot')
        if on_pilot_roster is not None and cockpit_playback_limit is None:
            raise ValueError('pilot roster requires cockpit playback')
        if (trial_index is not None or resume_route is not None) and (
                cockpit_playback_limit is not None or resume_routes is not None):
            raise ValueError('cannot combine single trial_index with multi-pilot cockpit playback')
        if cockpit_playback_limit is not None:
            if (on_transport_lost is None or type(cockpit_playback_limit) is not int
                    or not 1 <= cockpit_playback_limit <= MAX_COCKPIT_PLAYBACK_PILOTS):
                raise ValueError('invalid cockpit playback limit')
        elif (trial_index is not None or on_transport_lost is not None) and (
                on_transport_lost is None or type(trial_index) is not int
                or not 1 <= trial_index <= 200000 or trial_index == index):
            raise ValueError('invalid movement trial index')
        if resume_route is not None and (
                trial_index is None or type(resume_route) is not tuple or len(resume_route) != 3
                or resume_route[0] != trial_index):
            raise ValueError('movement resume route does not match trial index')
        normalized_resume_routes = None
        if resume_routes is not None:
            if cockpit_playback_limit is None or type(resume_routes) not in (list, tuple) or len(resume_routes) > cockpit_playback_limit:
                raise ValueError('invalid cockpit resume routes')
            seen_r = set()
            normalized_resume_routes = []
            for item in resume_routes:
                if (type(item) not in (list, tuple) or len(item) != 3
                        or type(item[0]) is not int or not 1 <= item[0] <= 200000
                        or item[0] == index or item[0] in seen_r):
                    raise ValueError('invalid cockpit resume route entry')
                seen_r.add(item[0])
                normalized_resume_routes.append(tuple(item))
        if type(credential) is not dict or not callable(validate_peer):
            raise ValueError('movement admission and local source validator required')
        credential = validate_ship_motion_credential(credential, index=index, registry_epoch=epoch,
                                                     session_epoch=credential.get('session_epoch'))
        if not (type(port) is int and 1 <= port <= 65535):
            raise ValueError('local movement bridge required')
        self._connection = socket.create_connection(('127.0.0.1', port), timeout=2)
        try:
            self._connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._connection.setblocking(False)
            bind = {'kind': 'bind_motion', 'ticket': credential['ticket']}
            if trial_index is not None:
                bind.update(loss_notifications=True, trial_index=trial_index)
            if resume_route is not None:
                bind['resume_route'] = resume_route
            if cockpit_playback_limit is not None:
                bind.update(loss_notifications=True, cockpit_playback_limit=cockpit_playback_limit)
            if normalized_resume_routes:
                bind['resume_routes'] = [list(item) for item in normalized_resume_routes]
            if on_pilot_roster is not None:
                bind['pilot_roster'] = True
            if npc_measurement_source is not None:
                bind[MEASUREMENT_KIND] = True
            self._outgoing = encode_frame(bind)
        except BaseException:
            self._connection.close()
            raise
        self._source = source
        self._validate_peer = validate_peer
        self._on_snapshot = on_snapshot
        self._on_end = on_end
        self._on_view_reset = on_view_reset
        self._on_transport_lost = on_transport_lost
        self._on_clock = on_clock
        self._on_pilot_roster = on_pilot_roster
        self._pilot_roster = None
        self._trial_index = trial_index
        self._resume_route = resume_route
        self._cockpit_playback_limit = cockpit_playback_limit
        self._resume_routes = normalized_resume_routes if normalized_resume_routes else None
        self._generation = credential['generation']
        self._npc_measurement_source = npc_measurement_source
        self._npc_measurement_sequence = 0
        self._npc_measurement_seen_view = False
        self._incoming = bytearray()
        self._bind_deadline = time.monotonic() + BIND_TIMEOUT
        self.disconnected_before_bind = False
        self._window = None
        self._running = False
        self._last_sequence = 0
        self._last_sector = None
        self._awaiting_sample = self._latest = self._receipt = None
        self._desired_view = None
        self._sent_view = None
        self._visible = {}
        self._hidden = set()
        # Retained pairs can receive a frame already in flight on an older
        # revision. Removed pairs cannot resume on that revision if reselected.
        self._route_revisions = {}
        # Keep watermarks across clear, clock changes, and optional link replacement.
        self._latest_seen = {} if _latest_seen is None else _latest_seen
        self._view_id = 0
        self._stats = dict(samples_acknowledged=0, velocity_samples_acknowledged=0,
                           snapshots_received=0, visibility_ends=0)

    def stats(self):
        return deepcopy(self._stats)

    def set_view(self, sector, indices):
        """Replace the local observation set. Callers own game visibility and rendering."""
        self.set_views([{'sector_macro': sector, 'indices': indices}])

    def set_views(self, views):
        """Replace all observed sectors at once. An empty list clears the view."""
        desired = normalize_views(views, own_index=self._source.registry[1])
        if not callable(self._on_snapshot) or not callable(self._on_end):
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        if desired == self._desired_view:
            return
        self._desired_view = desired
        self._route_revisions = {
            (index, sector): revision for (index, sector), revision in self._route_revisions.items()
            if view_matches(desired, index, sector)
        }
        self._retire_outside(desired)

    def _retire_outside(self, views, *, transport_lost=False):
        failure = None
        for index, (sector, generation) in tuple(self._visible.items()):
            if view_matches(views, index, sector):
                continue
            try:
                self._end(index, generation, transport_lost=transport_lost)
            except Exception as error:
                failure = error
        if failure is not None:
            raise failure

    def _end(self, index, generation, *, transport_lost=False):
        visible = self._visible.get(index)
        if visible is not None and visible[1] == generation:
            del self._visible[index]
            self._hidden.discard(index)
            self._stats['visibility_ends'] += 1
            if transport_lost and (self._trial_index == index or getattr(self, '_cockpit_playback_limit', None) is not None):
                self._on_transport_lost(index, generation)
            else:
                self._on_end(index, generation)

    def _snapshot(self, sample):
        index, sector = sample['index'], sample['sector_macro']
        generation, sequence = sample['generation'], sample['sequence']
        previous = self._latest_seen.get(index)
        if previous is not None and generation == previous[0] and sequence <= previous[1]:
            return
        visible = self._visible.get(index)
        if visible is not None and visible != (sector, generation):
            self._end(index, visible[1])
        self._latest_seen[index] = generation, sequence
        self._hidden.discard(index)
        self._visible[index] = sector, generation
        self._on_snapshot(dict(sample))

    def _clock(self, message, binding):
        fields = {'kind', 'window', 'running'}
        if binding:
            fields.add('last_sequence')
            if getattr(self, '_npc_measurement_source', None) is not None:
                fields.add(MEASUREMENT_KIND)
                if message.get(MEASUREMENT_KIND) is not True:
                    raise ValueError('private NPC measurement not negotiated')
            if self._trial_index is not None:
                fields.update(('loss_notifications', 'trial_index'))
                if (message.get('loss_notifications') is not True or type(message.get('trial_index')) is not int
                        or message['trial_index'] != self._trial_index):
                    raise ValueError('movement bind lacks negotiated trial capability')
            if self._resume_route is not None:
                fields.add('resume_valid')
                if type(message.get('resume_valid')) is not bool:
                    raise ValueError('movement bind lacks a resume verdict')
            if self._cockpit_playback_limit is not None:
                fields.update(('loss_notifications', 'cockpit_playback_limit'))
                if (message.get('loss_notifications') is not True
                        or type(message.get('cockpit_playback_limit')) is not int
                        or message['cockpit_playback_limit'] != self._cockpit_playback_limit):
                    raise ValueError('movement bind lacks negotiated cockpit playback capability')
            if self._resume_routes is not None:
                fields.add('resume_valid_indices')
                valid_indices = message.get('resume_valid_indices')
                if type(valid_indices) is not list or any(type(item) is not int for item in valid_indices):
                    raise ValueError('movement bind lacks cockpit resume verdicts')
            if self._on_pilot_roster is not None:
                fields.add('pilot_roster')
                if message.get('pilot_roster') is not True:
                    raise ValueError('movement bind lacks negotiated pilot roster')
        window = message.get('window')
        if (set(message) != fields or type(window) is not int or not 0 <= window <= MAX_COUNTER or
                type(message['running']) is not bool or binding != (self._window is None)):
            raise ValueError('invalid movement clock receipt')
        if binding:
            sequence = message['last_sequence']
            if type(sequence) is not int or not 0 <= sequence <= MAX_COUNTER:
                raise ValueError('invalid movement bind sequence')
            self._last_sequence = sequence
        elif window != self._window + 1 or message['running'] == self._running:
            raise ValueError('movement clock transition out of order')
        self._window, self._running = window, message['running']
        if not binding and not self._running:
            self._npc_measurement_source = None
        self._latest = self._awaiting_sample = self._receipt = self._last_sector = None
        self._sent_view = None
        self._view_id = 0
        self._route_revisions.clear()
        self._clear_pilot_roster()
        self._retire_outside({})
        if self._on_clock is not None:
            self._on_clock(window, self._running)
        if binding and self._resume_route is not None:
            index, generation, sector = self._resume_route
            self._resume_route = None
            if message['resume_valid'] and self._running:
                if view_matches(self._desired_view or {}, index, sector):
                    self._visible[index] = sector, generation
            else:
                self._on_end(index, generation)
        if binding and self._resume_routes is not None:
            routes, self._resume_routes = self._resume_routes, None
            valid_set = set(message['resume_valid_indices'])
            for index, generation, sector in routes:
                if index in valid_set and self._running:
                    if view_matches(self._desired_view or {}, index, sector):
                        self._visible[index] = sector, generation
                else:
                    self._on_end(index, generation)
        if not binding and self._on_view_reset is not None:
            self._on_view_reset()

    def _receive(self, message):
        kind = message.get('kind')
        if kind in ('motion_bound', 'motion_state'):
            self._clock(message, kind == 'motion_bound')
            return
        window = message.get('window')
        if self._window is None or type(window) is not int or window != self._window or not self._running:
            raise ValueError('received movement outside the running window')
        if kind == 'pilot_roster' and self._on_pilot_roster is not None:
            epoch, own_index = self._source.registry
            roster = validate_pilot_roster(message, epoch=epoch, own_index=own_index)
            if roster != self._pilot_roster:
                self._pilot_roster = roster
                self._on_pilot_roster(roster)
            return
        if kind == 'motion_sampled':
            sequence = message.get('sequence')
            if (set(message) != {'kind', 'sequence', 'window'} or type(sequence) is not int or
                    self._awaiting_sample is None or sequence != self._awaiting_sample['sequence']):
                raise ValueError('unexpected source receipt')
            sample = self._awaiting_sample
            velocity = sample['real_velocity']
            self._stats['samples_acknowledged'] += 1
            self._stats['velocity_samples_acknowledged'] += int(velocity is not None)
            # Keep exactly what went on the wire, including stripped baseline rates.
            receipt = dict(sample, window=window)
            self._stats['last_acknowledged_sample'] = receipt
            if velocity is not None and any(component != 0 for component in velocity.values()):
                self._stats['last_nonzero_velocity_acknowledged'] = receipt
            self._awaiting_sample = None
            return
        view_id = message.get('view_id')
        if type(view_id) is not int or not 1 <= view_id <= self._view_id:
            raise ValueError('unexpected movement view')
        current_view = view_id == self._view_id
        if kind == 'motion_snapshot' and set(message) == {'kind', 'window', 'view_id', 'sample'}:
            sample = motion_value(message['sample'], allow_v4=True)
            if (sample['version'] not in REAL_TIME_MOTION_VERSIONS or sample['epoch'] != self._source.registry[0]
                    or self._receipt is not None):
                raise ValueError('invalid remote movement snapshot')
            index, sector = sample['index'], sample['sector_macro']
            if index == self._source.registry[1] or (
                current_view and not view_matches(self._sent_view, index, sector)
            ):
                raise ValueError('remote ship outside selected view')
            if view_id >= self._route_revisions.get((index, sector), MAX_COUNTER + 1):
                self._stats['snapshots_received'] += 1
                self._snapshot(sample)
            self._receipt = {'kind': 'motion_received', 'view_id': view_id, 'index': sample['index'],
                             'sequence': sample['sequence'], 'generation': sample['generation']}
        elif (kind == 'motion_end' or (kind == 'motion_hidden' and (
                self._trial_index is not None or self._cockpit_playback_limit is not None))) and (
                set(message) == {'kind', 'window', 'view_id', 'index', 'generation'}
        ):
            index, generation = message['index'], message['generation']
            if (type(index) is not int or not 1 <= index <= 200000 or
                    type(generation) is not str or not _HEX32.fullmatch(generation)):
                raise ValueError('invalid movement visibility end')
            if kind == 'motion_hidden' and self._trial_index is not None and index != self._trial_index:
                raise ValueError('remote disappearance outside trial index')
            visible = self._visible.get(index)
            if (visible is not None and visible[1] == generation
                    and view_id >= self._route_revisions.get((index, visible[0]), MAX_COUNTER + 1)):
                if kind == 'motion_end':
                    self._end(index, generation)
                elif index not in self._hidden:
                    self._hidden.add(index)
                    self._on_transport_lost(index, generation)
        else:
            raise ValueError('unknown remote movement frame')

    def _next_frame(self):
        if not self._running:
            return None
        if self._receipt is not None:
            frame, self._receipt = self._receipt, None
            return frame
        if self._desired_view is not None and (
            self._sent_view != self._desired_view
            or len(self._route_revisions) != sum(map(len, self._desired_view.values()))
        ):
            if self._view_id == MAX_COUNTER:
                raise ValueError('movement view revision exhausted')
            self._sent_view = self._desired_view
            self._view_id += 1
            for sector, indices in self._sent_view.items():
                for index in indices:
                    self._route_revisions.setdefault((index, sector), self._view_id)
            rows = [{'sector_macro': sector, 'indices': sorted(indices)}
                    for sector, indices in self._sent_view.items()]
            if len(rows) <= 1:
                row = rows[0] if rows else {'sector_macro': 'none', 'indices': []}
                return {'kind': 'motion_view', 'view_id': self._view_id, **row}
            return {'kind': 'motion_views', 'view_id': self._view_id, 'views': rows}
        if self._awaiting_sample is not None or self._latest is None:
            return self._next_npc_measurement()
        sample, self._latest = self._latest, None
        if sample['sector_macro'] != self._last_sector:
            sample['real_velocity'] = None
        self._last_sector, self._last_sequence = sample['sector_macro'], sample['sequence']
        self._awaiting_sample = sample
        return {'kind': 'motion_sample', 'sample': sample}


    def _next_npc_measurement(self):
        source = getattr(self, '_npc_measurement_source', None)
        if source is None:
            return None
        if not view_matches(self._desired_view or {}, NPC_INDEX, SECTOR):
            if self._npc_measurement_seen_view:
                self._npc_measurement_source = None
            return None
        self._npc_measurement_seen_view = True
        raw = source()
        if raw is None:
            return None
        if raw is RADAR_ENDED:
            self._npc_measurement_source = None
            return {'kind': MEASUREMENT_KIND, 'generation': self._generation,
                    'view_id': self._view_id, 'sample': None}
        sample = measurement_value(raw)
        if sample['sequence'] <= self._npc_measurement_sequence:
            return None
        if not 0 <= native_capture_now() - sample['capture_time'] <= 1:
            return None
        self._npc_measurement_sequence = sample['sequence']
        return {'kind': MEASUREMENT_KIND, 'generation': self._generation,
                'view_id': self._view_id, 'sample': sample}

    def poll(self):
        try:
            if self._window is None and time.monotonic() > self._bind_deadline:
                raise TimeoutError('movement stream did not bind to admitted player')
            old_window = self._window
            try:
                for message in read_frames(self._connection, self._incoming):
                    self._receive(message)
            except ConnectionError:
                self.disconnected_before_bind = self._window is None and not self._incoming
                raise
            sample = self._source.poll(self._validate_peer)
            if sample is not None and self._running and old_window == self._window:
                sample = motion_value(sample, allow_v4=True)
                if (sample['version'] not in REAL_TIME_MOTION_VERSIONS or
                        (sample['epoch'], sample['index'], sample['generation']) !=
                        (*self._source.registry, self._source.local_generation)):
                    raise ValueError('source does not match the selected local cockpit')
                if sample['sequence'] > self._last_sequence:
                    self._latest = dict(sample, generation=self._generation)
            for _ in range(4):
                if not self._outgoing:
                    frame = self._next_frame()
                    if frame is None:
                        break
                    self._outgoing = encode_frame(dict(frame, window=self._window))
                try:
                    sent = self._connection.send(self._outgoing)
                except BlockingIOError:
                    break
                except ConnectionError:
                    self.disconnected_before_bind = self._window is None and not self._incoming
                    raise
                if not sent:
                    self.disconnected_before_bind = self._window is None and not self._incoming
                    raise ConnectionError('movement stream closed on write')
                self._outgoing = self._outgoing[sent:]
        except OSError:
            self.close(transport_lost=True)
            raise
        except BaseException:
            self.close()
            raise

    def close(self, *, transport_lost=False):
        self._outgoing = b''
        self._connection.close()
        try:
            self._clear_pilot_roster()
        finally:
            self._retire_outside({}, transport_lost=transport_lost)

    def _clear_pilot_roster(self):
        previous, self._pilot_roster = self._pilot_roster, None
        if previous:
            self._on_pilot_roster(())
