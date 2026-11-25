"""Keep the optional movement stream scoped to one already-admitted player."""
from copy import deepcopy
import time

from .company_limits import MAX_COCKPIT_PLAYBACK_PILOTS
from .company_motion_client import CompanyMotionClient
from .company_motion_frames import BIND_TIMEOUT, normalize_views

_INITIAL_DELAY = .25
_MAX_DELAY = 5
_COUNTERS = ('samples_acknowledged', 'velocity_samples_acknowledged',
             'snapshots_received', 'visibility_ends')


class _LocalSource:
    """Keep local X4 socket failures distinct from retryable TCP failures."""
    def __init__(self, source):
        self._source = source

    @property
    def registry(self):
        return self._source.registry

    @property
    def local_generation(self):
        return self._source.local_generation

    @property
    def observed_npc(self):
        return getattr(self._source, 'observed_npc', None)

    @property
    def registered_npc_hold_trial(self):
        return getattr(self._source, 'registered_npc_hold_trial', False)

    def poll(self, validate_peer):
        try:
            return self._source.poll(validate_peer)
        except OSError:
            raise ValueError('local movement source unavailable') from None


class ReconnectingCompanyMotionClient:
    """Retry only the movement socket. The caller owns control and the local source.

    Construction is inert. Poll after pumping the admitted control connection;
    close before releasing that connection or its source. Never transfer this
    owner to a later admission. The optional observation callbacks run on this
    same thread. An explicit view survives a link reset without retaining poses.
    """
    def __init__(self, port, credential, source, validate_peer, *, on_snapshot=None, on_end=None,
                 on_transport_lost=None, on_world_reset=None, trial_index=None, resume_route=None,
                 cockpit_playback_limit=None, resume_routes=None, on_pilot_roster=None):
        if ((on_snapshot is not None or on_end is not None)
                and not (callable(on_snapshot) and callable(on_end))):
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        if (trial_index is not None or resume_route is not None) and (
                cockpit_playback_limit is not None or resume_routes is not None):
            raise ValueError('cannot combine single trial_index with multi-pilot cockpit playback')
        if on_pilot_roster is not None and (cockpit_playback_limit is None or not callable(on_pilot_roster)):
            raise ValueError('pilot roster requires automatic cockpit playback')
        if cockpit_playback_limit is not None:
            if (type(cockpit_playback_limit) is not int or not 1 <= cockpit_playback_limit <= MAX_COCKPIT_PLAYBACK_PILOTS
                    or not callable(resume_routes) or not callable(on_transport_lost)):
                raise ValueError('cockpit playback needs recovery callbacks')
        elif (trial_index is not None or on_transport_lost is not None) and (
                trial_index is None or not callable(resume_route) or not callable(on_transport_lost)):
            raise ValueError('a movement trial needs recovery callbacks')
        self._port = port
        self._credential = dict(credential) if type(credential) is dict else credential
        self._source = _LocalSource(source)
        self._npc_measurement_source = (source.take_npc_measurement
                                        if getattr(source, 'registered_npc_local_measurement', False) is True else None)
        self._validate_peer = validate_peer
        self._on_snapshot, self._on_end = on_snapshot, on_end
        self._on_transport_lost = on_transport_lost
        self._on_world_reset = on_world_reset
        self._trial_index = trial_index
        self._resume_route = resume_route
        self._cockpit_playback_limit = cockpit_playback_limit
        self._resume_routes = resume_routes
        self._on_pilot_roster = on_pilot_roster
        self._retry_without_roster = False
        self._initial_bind_until = (None if trial_index is None and cockpit_playback_limit is None
                                    else time.monotonic() + BIND_TIMEOUT)
        self._clock = None
        self._view = None
        self._latest_seen = {}
        self._client = None
        self._closed = False
        self._retry_at = 0.0
        self._retry_delay = _INITIAL_DELAY
        self._finished_stats = dict.fromkeys(_COUNTERS, 0)
        self.failure = None

    def set_view(self, sector, indices):
        """Set the effective observation scope for the current or next link."""
        self.set_views([{'sector_macro': sector, 'indices': indices}])

    def set_views(self, views):
        """Replace the observation scope for the current or next link."""
        view = normalize_views(views, own_index=self._source.registry[1])
        if self._on_snapshot is None:
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        if self._closed or self.failure is not None:
            raise RuntimeError('movement stream unavailable')
        if view == self._view:
            return
        self._view = view
        try:
            self._apply_view()
        except ValueError:
            self._fail()
            self._finish_link()

    def _apply_view(self):
        if self._client is not None and self._view is not None:
            self._client.set_views([{'sector_macro': sector, 'indices': sorted(indices)}
                                    for sector, indices in self._view.items()])

    @staticmethod
    def _notify(callback, *args):
        try:
            callback(*args)
        except Exception:
            # Local callback errors must never cause a transport retry.
            raise ValueError('local movement observer failed') from None

    def _bound_clock(self, window, running):
        self._initial_bind_until = None
        self._retry_without_roster = False
        clock = (window, running)
        if self._on_world_reset is not None and self._clock is not None and clock != self._clock:
            self._notify(self._on_world_reset)
        self._clock = clock

    def _fail(self, reason='motion_local_or_protocol_failure'):
        self.failure = reason
        self._credential = None
        self._view = None
        self._latest_seen.clear()
        if self._on_world_reset is not None:
            try:
                self._notify(self._on_world_reset)
            except ValueError:
                pass

    def _finish_link(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except OSError:
                pass  # The link is already discarded; control stays with the caller.
            except ValueError:
                self._fail()
            finally:
                self._merge_link_stats(self._finished_stats, client)

    @staticmethod
    def _merge_link_stats(totals, client):
        link = client.stats()
        for key in _COUNTERS:
            totals[key] += link[key]
        for key in ('last_acknowledged_sample', 'last_nonzero_velocity_acknowledged'):
            if key in link:
                totals[key] = link[key]

    def poll(self):
        if self._closed or self.failure is not None:
            return
        if self._initial_bind_until is not None and time.monotonic() >= self._initial_bind_until:
            self._finish_link()
            self._fail('motion_initial_bind_timeout')
            return
        try:
            if self._client is None:
                # Consume pre-bind observations without retaining them. The new
                # client's bind reply carries the service's accepted sequence.
                self._source.poll(self._validate_peer)
                if time.monotonic() < self._retry_at:
                    return
                observer = {}
                if self._on_snapshot is not None:
                    observer = dict(
                        on_snapshot=lambda sample: self._notify(self._on_snapshot, sample),
                        on_end=lambda index, generation: self._notify(self._on_end, index, generation),
                        _latest_seen=self._latest_seen,
                    )
                    if self._on_transport_lost is not None:
                        observer['on_transport_lost'] = lambda index, generation: self._notify(
                            self._on_transport_lost, index, generation)
                    if self._on_transport_lost is not None or self._on_world_reset is not None:
                        observer['on_clock'] = self._bound_clock
                    if self._trial_index is not None:
                        observer['trial_index'] = self._trial_index
                        observer['resume_route'] = self._resume_route()
                    elif self._cockpit_playback_limit is not None:
                        observer['cockpit_playback_limit'] = self._cockpit_playback_limit
                        if self._on_pilot_roster is not None and not self._retry_without_roster:
                            observer['on_pilot_roster'] = lambda indices: self._notify(self._on_pilot_roster, indices)
                        routes = self._resume_routes()
                        if routes:
                            observer['resume_routes'] = routes
                if self._npc_measurement_source is not None:
                    observer['npc_measurement_source'] = self._npc_measurement_source
                self._client = CompanyMotionClient(self._port, self._credential,
                                                   self._source, self._validate_peer, **observer)
                # The receiver binds this trial once. A later link carries ordinary pilot motion.
                self._npc_measurement_source = None
                self._apply_view()
            self._client.poll()
            if self._retry_delay > _INITIAL_DELAY and self._client.stats()['samples_acknowledged']:
                self._retry_delay = _INITIAL_DELAY
        except OSError:
            # Older providers close a bind that requests an unknown capability.
            # Keep the same credential and retry with their ordinary radar binding.
            if (self._on_pilot_roster is not None and self._client is not None
                    and self._client.disconnected_before_bind):
                self._retry_without_roster = True
            self._finish_link()
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(_MAX_DELAY, self._retry_delay * 2)
        except Exception:
            self._finish_link()
            self._fail()

    @classmethod
    def observing(cls, port, credential, source, validate_peer):
        observer = source.observe(validate_peer)
        trial = {}
        trial_index = getattr(source, 'playback_index', None)
        cockpit_radar = getattr(source, 'cockpit_radar_playback', False)
        if getattr(source, 'registered_npc_alignment_trial', False) is True:
            # A disconnected cockpit must retire this one-shot placement, including a reconnect on the same seat.
            trial = dict(on_transport_lost=observer.end, on_world_reset=observer.reset_world,
                         trial_index=source.observed_npc[0], resume_route=observer.resume_route)
        elif trial_index is not None:
            trial = dict(on_transport_lost=observer.transport_lost, on_world_reset=observer.reset_world,
                         trial_index=trial_index, resume_route=observer.resume_route)
        elif cockpit_radar:
            trial = dict(on_transport_lost=observer.transport_lost, on_world_reset=observer.reset_world,
                         cockpit_playback_limit=getattr(source, 'cockpit_max_pilots', MAX_COCKPIT_PLAYBACK_PILOTS),
                         resume_routes=observer.resume_routes, on_pilot_roster=observer.set_pilot_roster)
        client = cls(port, credential, source, validate_peer, on_snapshot=observer.snapshot, on_end=observer.end, **trial)
        observer.bind_views(client.set_views)
        return client

    def stats(self):
        result = deepcopy(self._finished_stats)
        if self._client is not None:
            self._merge_link_stats(result, self._client)
        return result

    def close(self):
        self._closed = True
        self._credential = None
        self._view = None
        self._latest_seen.clear()
        try:
            self._finish_link()
        finally:
            if self._on_world_reset is not None:
                self._notify(self._on_world_reset)
