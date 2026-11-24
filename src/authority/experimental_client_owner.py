"""Single-thread owner for one experimental load and participant connection.

The caller establishes transport security and X4 process identity, owns all
paths and supplies load, marker and pause observations. This adapter cannot
verify those assertions or prove which file X4 loaded. It starts no process,
opens no listener and makes no operating-system-specific ownership assumptions.
"""
from contextlib import contextmanager
import threading

from .experimental_remote_load_client import ExperimentalRemoteLoadClient
from .experimental_tcp_participant_client import (
    ExperimentalTCPParticipantClient, validate_participant_connection,
)


class CompanyShipChoiceDenied(ValueError):
    """The server rejected a catalogue query or choice without closing the offer."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ExperimentalClientOwner:
    """Own the load socket and transfer it once to the participant adapter.

    After successful construction the owner closes the connection on failure or
    close. If construction fails, the supplied socket remains the caller's. All
    methods, including close, must run on the constructing thread. The caller
    must pump the load connection during long waits when keepalive is enabled.
    """

    def __init__(self, conn, *, enabled=False, file_host, file_port, file_token,
                 file_slot, deadline, io_timeout=30, keepalive_interval=None, motion_port=None):
        if motion_port is not None and (type(motion_port) is not int or not 1 <= motion_port <= 65535):
            raise ValueError('invalid local motion port')
        load = ExperimentalRemoteLoadClient(
            conn, enabled=enabled, file_host=file_host, file_port=file_port,
            file_token=file_token, file_slot=file_slot, deadline=deadline,
            io_timeout=io_timeout, keepalive_interval=keepalive_interval)
        self._load = load
        # Retain the original object across the handoff. An interruption can
        # occur after take_connection transfers ownership but before it returns.
        self._socket = conn
        self._participant = None
        self._offer = None
        self._ship_motion_credential = None
        self._thread = threading.current_thread()
        self._phase = 'loading'
        self._motion_port = motion_port
        self._busy = False
        self._invalidated = False

    @property
    def motion_port(self):
        """Return the optional loopback transport port during this owner's lifetime."""
        if threading.current_thread() is not self._thread or self._phase in ('closed', 'failed'):
            raise RuntimeError('client owner unavailable for motion transport')
        return self._motion_port

    def _close_owned(self):
        self._ship_motion_credential = None
        try:
            if self._participant is not None:
                try:
                    self._participant.close()
                except RuntimeError:
                    # A faulted participant closes its socket itself. Do not
                    # mask an error while the socket remains open.
                    if self._socket.fileno() != -1:
                        raise
        finally:
            try:
                if self._load is not None:
                    self._load.close()
            finally:
                self._socket.close()

    @contextmanager
    def _operation(self, phase):
        if threading.current_thread() is not self._thread:
            raise RuntimeError('exclusive owner thread required')
        if self._busy:
            self._invalidated = True
            raise RuntimeError('reentrant client owner operation')
        if self._phase in ('failed', 'closed'):
            raise RuntimeError('client owner unavailable for this operation')
        if self._phase != phase:
            self._phase = 'failed'
            self._close_owned()
            raise RuntimeError('client owner unavailable for this operation')
        self._busy = True
        try:
            yield
            if self._invalidated:
                raise RuntimeError('client owner changed during operation')
        except BaseException:
            self._phase = 'failed'
            self._close_owned()
            raise
        finally:
            self._busy = False

    def request_offer(self):
        """Request a server offer; its marker is an expectation, not evidence."""
        with self._operation('loading'):
            self._offer = self._load.request_offer()
            return self._offer

    def list_company_ships(self) -> tuple[dict, ...]:
        """List offered public ships. A CompanyShipChoiceDenied leaves the offer usable."""
        with self._operation('loading'):
            result = self._load.list_company_ships()
        if type(result) is str:
            raise CompanyShipChoiceDenied(result)
        return result

    def choose_company_ship(self, ship_index: int) -> dict:
        """Choose an offered ship. A CompanyShipChoiceDenied leaves the offer usable."""
        with self._operation('loading'):
            result = self._load.choose_company_ship(ship_index)
        if type(result) is str:
            raise CompanyShipChoiceDenied(result)
        return result

    def request_ship_motion(self):
        """Request a separate motion credential for the chosen offered ship."""
        with self._operation('loading'):
            self._load.request_ship_motion()

    @property
    def ship_motion_credential(self):
        """Return a copy after acknowledged admission, or None before it or after close."""
        if threading.current_thread() is not self._thread:
            raise RuntimeError('exclusive owner thread required')
        if self._phase != 'active' or self._ship_motion_credential is None:
            return None
        return dict(self._ship_motion_credential)

    def download(self, new_directory):
        with self._operation('loading'):
            return self._load.download(new_directory)

    def verify_staged(self, path):
        with self._operation('loading'):
            return self._load.verify_staged(path)

    def copy_to_new_slot(self, source, target):
        """Copy to an absent slot; caller must fence X4's save-list scan."""
        with self._operation('loading'):
            return self._load.copy_to_new_slot(source, target)

    def load_requested(self, attempt):
        """Record a caller-observed load request, without establishing its source."""
        with self._operation('loading'):
            return self._load.load_requested(attempt)

    def loaded_marker(self, attempt, marker):
        with self._operation('loading'):
            return self._load.loaded_marker(attempt, marker)

    def paused(self, attempt, paused):
        with self._operation('loading'):
            return self._load.paused(attempt, paused)

    def activate(self, attempt, controller, *, participant_timeout=30,
                 authority_grant=None, authority_guard=None, pull_authority_motion=None,
                 apply_motion=None):
        """Claim the observed load and transfer its socket to one participant.

        The caller establishes that the observations and controller belong to
        the same still-live X4 process. A failed or uncertain claim is not retried.
        Motion source lifetime, pause/drain and cleanup remain the caller's duty.
        When enabled, all three authority arguments must be supplied together.
        An optional motion consumer runs on this owner's thread, using the
        existing participant protocol. Its receipt confirms consumption only;
        native movement needs separate evidence. It must bound its own work.
        """
        with self._operation('loading'):
            if self._offer is None:
                raise RuntimeError('participant requires an offered connection')
            if apply_motion is not None and not callable(apply_motion):
                raise ValueError('motion consumer must be callable')
            checked_grant = None
            if any(value is not None for value in
                   (authority_grant, authority_guard, pull_authority_motion)):
                from .experimental_tcp_client_authority_motion import grant_value
                if not callable(authority_guard) or not callable(pull_authority_motion):
                    raise ValueError('authority pull needs guard and callback')
                checked_grant = grant_value(authority_grant)
                if checked_grant['session_epoch'] != self._offer.offer.world_epoch:
                    raise ValueError('authority grant session differs')
            validate_participant_connection(self._socket, controller, enabled=True,
                epoch=self._offer.offer.world_epoch, handle=self._offer.offer.connection_handle,
                timeout=participant_timeout)
            if self._invalidated:
                raise RuntimeError('client owner changed during participant validation')
            credential = self._load.claim(attempt)
            conn = self._load.take_connection()
            try:
                self._participant = ExperimentalTCPParticipantClient(
                    conn, controller, enabled=True, epoch=self._offer.offer.world_epoch,
                    handle=self._offer.offer.connection_handle, timeout=participant_timeout,
                    authority_grant=checked_grant, authority_guard=authority_guard,
                    pull_authority_motion=pull_authority_motion, apply_motion=apply_motion)
            except BaseException:
                conn.close()
                raise
            self._load.close()
            self._load = None
            self._ship_motion_credential = credential
            self._phase = 'active'

    def poll(self, timeout=0):
        """Pump loading or participant I/O; loading accepts only timeout zero."""
        phase = self._phase
        with self._operation(phase):
            if phase == 'loading':
                if type(timeout) not in (int, float) or timeout != 0:
                    raise ValueError('load polling does not wait')
                return self._load.poll()
            if phase == 'active':
                return self._participant.poll(timeout)
            raise RuntimeError('client owner unavailable for polling')

    def close(self):
        if threading.current_thread() is not self._thread:
            raise RuntimeError('exclusive owner thread required')
        if self._busy:
            self._invalidated = True
            raise RuntimeError('reentrant client owner close')
        if self._phase == 'closed':
            return
        try:
            self._close_owned()
        finally:
            self._phase = 'closed'
