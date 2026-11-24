"""Keep the optional movement stream scoped to one already-admitted player."""
import time

from .company_motion_client import CompanyMotionClient

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
    owner to a later admission.
    """
    def __init__(self, port, credential, source, validate_peer):
        self._port = port
        self._credential = dict(credential) if type(credential) is dict else credential
        self._source = _LocalSource(source)
        self._validate_peer = validate_peer
        self._client = None
        self._closed = False
        self._retry_at = 0.0
        self._retry_delay = _INITIAL_DELAY
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self.failure = None

    def _finish_link(self):
        client, self._client = self._client, None
        if client is None:
            return
        self._add_counts(self._counts, client)
        try:
            client.close()
        except OSError:
            pass  # The link is already discarded; control stays with the caller.

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
                self._client = CompanyMotionClient(self._port, self._credential,
                                                   self._source, self._validate_peer)
            self._client.poll()
            if self._retry_delay > _INITIAL_DELAY and self._client.stats()['samples_acknowledged']:
                self._retry_delay = _INITIAL_DELAY
        except OSError:
            self._finish_link()
            self._retry_at = time.monotonic() + self._retry_delay
            self._retry_delay = min(_MAX_DELAY, self._retry_delay * 2)
        except Exception:
            self._finish_link()
            self.failure = 'motion_local_or_protocol_failure'
            self._credential = None

    def stats(self):
        counts = self._counts.copy()
        if self._client is not None:
            self._add_counts(counts, self._client)
        return counts

    def close(self):
        self._closed = True
        self._credential = None
        self._finish_link()
