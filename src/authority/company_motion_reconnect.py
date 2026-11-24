"""Keep the optional movement stream scoped to one already-admitted player."""
import time

from .company_motion_client import CompanyMotionClient
from .company_motion_frames import validate_view

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
    def __init__(self, port, credential, source, validate_peer, *, on_snapshot=None, on_end=None):
        if ((on_snapshot is not None or on_end is not None)
                and not (callable(on_snapshot) and callable(on_end))):
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        self._port = port
        self._credential = dict(credential) if type(credential) is dict else credential
        self._source = _LocalSource(source)
        self._validate_peer = validate_peer
        self._on_snapshot, self._on_end = on_snapshot, on_end
        self._view = None
        self._visible = {}
        self._latest_seen = {}
        self._client = None
        self._closed = False
        self._retry_at = 0.0
        self._retry_delay = _INITIAL_DELAY
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self.failure = None

    def set_view(self, sector, indices):
        """Set the effective observation scope for the current or next link."""
        validate_view(sector, indices)
        if self._on_snapshot is None:
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        if self._closed or self.failure is not None:
            raise RuntimeError('movement stream unavailable')
        view = (sector, frozenset(indices) - {self._source.registry[1]})
        if view == self._view:
            return
        self._view = view
        self._latest_seen = {index: seen for index, seen in self._latest_seen.items() if index in view[1]}
        if self._client is not None:
            self._client.set_view(sector, sorted(view[1]))
        try:
            self._end_view()
        except ValueError:
            self._fail()
            self._finish_link()

    @staticmethod
    def _notify(callback, *args):
        try:
            callback(*args)
        except Exception:
            # Local callback errors must never cause a transport retry.
            raise ValueError('local movement observer failed') from None

    def _snapshot(self, sample):
        index, generation, sequence = sample['index'], sample['generation'], sample['sequence']
        previous = self._latest_seen.get(index)
        # A new view/connection can get a still-fresh cached sample from the
        # service. Do not redeliver a position the caller has already seen.
        if previous is not None and generation == previous[0] and sequence <= previous[1]:
            return
        visible_generation = self._visible.get(index)
        if visible_generation is not None and visible_generation != generation:
            self._end(index, visible_generation)
        self._latest_seen[index] = generation, sequence
        self._visible[index] = generation
        self._notify(self._on_snapshot, sample)

    def _end(self, index, generation):
        if self._visible.get(index) == generation and index in self._visible:
            del self._visible[index]
            self._notify(self._on_end, index, generation)

    def _end_view(self):
        visible, self._visible = self._visible, {}
        failed = False
        for index, generation in visible.items():
            try:
                self._notify(self._on_end, index, generation)
            except ValueError:
                failed = True
        if failed:
            raise ValueError('local movement observer failed')

    def _fail(self):
        self.failure = 'motion_local_or_protocol_failure'
        self._credential = None
        self._view = None
        self._latest_seen.clear()

    def _finish_link(self):
        client, self._client = self._client, None
        if client is not None:
            self._add_counts(self._counts, client)
            try:
                client.close()
            except OSError:
                pass  # The link is already discarded; control stays with the caller.
        try:
            self._end_view()
        except ValueError:
            self._fail()

    @staticmethod
    def _add_counts(totals, client):
        counts = client.stats()
        for key in _COUNTERS:
            totals[key] += counts[key]

    def poll(self):
        if self._closed or self.failure is not None:
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
                    observer = dict(on_snapshot=self._snapshot, on_end=self._end, on_view_reset=self._end_view)
                self._client = CompanyMotionClient(self._port, self._credential,
                                                   self._source, self._validate_peer, **observer)
                if self._view is not None:
                    sector, indices = self._view
                    self._client.set_view(sector, sorted(indices))
            self._client.poll()
            if self._retry_delay > _INITIAL_DELAY and self._client.stats()['samples_acknowledged']:
                self._retry_delay = _INITIAL_DELAY
        except OSError:
            self._finish_link()
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(_MAX_DELAY, self._retry_delay * 2)
        except Exception:
            self._finish_link()
            self._fail()

    @classmethod
    def observing(cls, port, credential, source, validate_peer):
        observer = source.observe(validate_peer)
        client = cls(port, credential, source, validate_peer, on_snapshot=observer.snapshot, on_end=observer.end)
        observer.bind_views(client.set_views)
        return client

    def stats(self):
        counts = self._counts.copy()
        if self._client is not None:
            self._add_counts(counts, self._client)
        return counts

    def close(self):
        self._closed = True
        self._credential = None
        self._view = None
        self._latest_seen.clear()
        self._finish_link()
