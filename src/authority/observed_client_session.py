"""One caller-fenced load and participant session, without owning X4.

The caller owns the process, view, profile and log and supplies their guard.
The log reader returns bounded immutable snapshots; it must not hide changes.
The caller stops X4 and confirms its cleanup independently. Receipt text is
cooperative evidence, not proof of process origin or loaded bytes.
"""
from contextlib import contextmanager
import math
from pathlib import Path
import re
import threading
import time

from .experimental_client_owner import ExperimentalClientOwner
from .load_observations import BoundReceiptStream
from .local_participant_control import LocalParticipantControl
from .trusted_load_messages import encode_bound_offer

MAX_SNAPSHOT = 8 << 20


class _CheckedParticipant:
    """Do not acknowledge a command before its final log/guard check."""

    def __init__(self, session):
        self._session = session
        self._reported_resume = False

    def _call(self, operation, handles):
        session = self._session
        if session._motion_control is not None:
            if session._motion_control.before_control(operation) is not True:
                raise RuntimeError('motion preparation did not confirm')
            if not session._check():
                raise RuntimeError('client log became incomplete before command')
        if operation == 'resume':
            session._records.allow_seat_handoff()
        if getattr(session.controller, operation)(handles) is not True:
            raise RuntimeError('local participant did not confirm')
        if session._motion_control is not None:
            if session._motion_control.after_control(operation) is not True:
                raise RuntimeError('motion transition did not confirm')
        if not session._check():
            raise RuntimeError('client log became incomplete during command')
        if operation == 'resume' and session._on_stage is not None and not self._reported_resume:
            self._reported_resume = True
            session._on_stage('first_local_resume_confirmed')
        return True

    def pause(self, handles):
        return self._call('pause', handles)

    def resume(self, handles):
        return self._call('resume', handles)


class ObservedClientSession:
    """Own one staged client connection and local controller until close.

    Each public operation uses the creating thread. Reader/guard callbacks and
    filesystem or socket calls are synchronous and cannot be interrupted.
    A dispatched claim or command may have taken effect even if a later check
    fails; this owner never retries it. A motion callback must be bounded, and
    its IPC/STOP and cleanup remain caller-owned. Closing does not stop or fence X4.
    """

    def __init__(self, owner, bound, controller, *, slot, read_log, guard, deadline,
                 apply_motion=None, registry_status=None, registry_nonce=None,
                 motion_control=None, require_full_inspection=False, seat_index=None, on_stage=None,
                 company_purchase_exchange=None, company_purchase_statement=None,
                 company_purchase_statement_result=None, company_undock_exchange=None, company_trade_view_exchange=None):
        if not isinstance(owner, ExperimentalClientOwner) or not isinstance(controller, LocalParticipantControl):
            raise ValueError('client owner and local controller required')
        if owner._thread is not threading.current_thread():
            raise RuntimeError('client owner must belong to this thread')
        self.owner, self.controller = owner, controller
        self._thread = threading.get_ident()
        self._state, self._busy = 'new', False
        try:
            if apply_motion is not None and not callable(apply_motion):
                raise ValueError('motion consumer must be callable')
            motion_timeout = 0
            if motion_control is not None:
                motion_timeout = getattr(motion_control, 'timeout', None)
                if (apply_motion is not motion_control or
                        not callable(getattr(motion_control, 'before_control', None)) or
                        not callable(getattr(motion_control, 'after_control', None)) or
                        type(motion_timeout) not in (int, float) or not math.isfinite(motion_timeout) or
                        not 0 < motion_timeout < controller.timeout or
                        controller.timeout + 2 * motion_timeout + 1 > 300):
                    raise ValueError('bounded motion lifecycle matching the consumer required')
            if (not callable(read_log) or not callable(guard) or type(deadline) not in (int, float) or
                    not math.isfinite(deadline) or deadline <= time.monotonic()):
                raise ValueError('bounded session callbacks required')
            encoded = encode_bound_offer(bound)
            if (owner._phase != 'loading' or owner._offer is None or
                    encode_bound_offer(owner._offer) != encoded or owner._load is None or
                    owner._load._bound is not owner._offer or owner._load._state != 'staged' or owner._load._load is None or
                    owner._load._load._path is None or
                    Path(owner._load._load._path).name != slot + '.xml.gz' or
                    owner._load._keepalive_interval is None or owner._load._deadline > deadline):
                raise ValueError('matching staged offer, keepalive and bounded load deadline required')
            if (not re.fullmatch(r'[0-9a-f]{32}', bound.offer.world_epoch) or
                    controller.fault or controller.peer is not None or controller.handle is not None or
                    controller.epoch is not None or controller.wait_hook is not None or
                    controller.sock.fileno() < 0 or controller.timeout > 299):
                raise ValueError('fresh local participant controller required')
            self._bound, self._encoded = owner._offer, encoded
            self._load = owner._load
            self._slot, self._read_log, self._guard, self._deadline = slot, read_log, guard, float(deadline)
            if company_purchase_exchange is not None and not callable(company_purchase_exchange):
                raise ValueError('Company purchase callback must be callable')
            self._purchase_callbacks = {}
            if company_purchase_exchange is not None:
                self._purchase_callbacks['company_purchase_exchange'] = company_purchase_exchange
            if company_purchase_statement is not None or company_purchase_statement_result is not None:
                if (company_purchase_exchange is None or not callable(company_purchase_statement)
                        or not callable(company_purchase_statement_result)):
                    raise ValueError('Company purchase statement callbacks require a purchase exchange')
                self._purchase_callbacks.update(company_purchase_statement=company_purchase_statement,
                    company_purchase_statement_result=company_purchase_statement_result)
            if company_undock_exchange is not None:
                if company_purchase_exchange is None or not callable(company_undock_exchange):
                    raise ValueError('Company undock requires an admitted purchase exchange')
                self._purchase_callbacks['company_undock_exchange'] = company_undock_exchange
            if company_trade_view_exchange is not None:
                if company_purchase_exchange is None or not callable(company_trade_view_exchange):
                    raise ValueError('Company trade view requires an admitted purchase exchange')
                self._purchase_callbacks['company_trade_view_exchange'] = company_trade_view_exchange
            self._apply_motion = apply_motion
            self._on_stage = on_stage
            self._motion_control = motion_control
            self._participant_timeout = controller.timeout + 2 * motion_timeout + 1
            self._socket, self._lease = controller.sock, controller.lease
            self._controller_timeout = controller.timeout
            self._hook = self._wait_hook
            self._peer = None
            self._snapshot = b''
            self._records = BoundReceiptStream(bound, slot=slot,
                registry_status=registry_status, registry_nonce=registry_nonce,
                require_full_inspection=require_full_inspection, seat_index=seat_index, on_stage=on_stage)
        except BaseException as error:
            self._fail(error)
            raise

    def _fail(self, original):
        self._state = 'failed'
        for resource in (self.controller, self.owner):
            try:
                resource.close()
            except BaseException as cleanup_error:
                try:
                    original.add_note('session cleanup failed: ' + type(cleanup_error).__name__)
                except BaseException:
                    pass

    @contextmanager
    def _operation(self, states):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive session owner thread required')
        if self._busy or self._state not in states:
            error = RuntimeError('stale or reentrant observed session')
            self._fail(error)
            raise error
        self._busy = True
        try:
            yield
        except BaseException as error:
            self._fail(error)
            raise
        finally:
            self._busy = False

    def _invariants(self, state, *, pending_command):
        if (self._state != state or state not in ('waiting', 'activating', 'active')
                or (self._deadline is not None and time.monotonic() >= self._deadline)):
            raise TimeoutError('observed session unavailable or expired')
        if (self.owner._offer is not self._bound or encode_bound_offer(self._bound) != self._encoded or
                self.controller.sock is not self._socket or self._socket.fileno() < 0 or
                self.controller.lease != self._lease or self.controller.wait_hook is not self._hook or
                self.controller.timeout != self._controller_timeout or
                (self.controller.fault and not pending_command) or
                self.owner._phase != ('active' if state == 'active' else 'loading')):
            raise RuntimeError('observed session ownership changed')
        if self._peer is not None and (self.controller.peer != self._peer or
                self.controller.handle != self._bound.offer.connection_handle or
                self.controller.epoch != self._bound.offer.world_epoch):
            raise RuntimeError('local participant enrollment changed')
        if state != 'active' and (self.owner._load is not self._load or
                self._load._bound is not self._bound or self._load._deadline > self._deadline):
            raise RuntimeError('staged load owner changed')

    def _check(self, *, pending_command=False):
        state = self._state
        self._invariants(state, pending_command=pending_command)
        if self._guard() is not True:
            raise RuntimeError('observed session guard failed')
        self._invariants(state, pending_command=pending_command)
        snapshot = self._read_log()
        self._invariants(state, pending_command=pending_command)
        self._feed_log(snapshot)
        if self._guard() is not True:
            raise RuntimeError('observed session guard failed')
        self._invariants(state, pending_command=pending_command)
        return self._complete

    def _feed_log(self, snapshot):
        if type(snapshot) is not bytes or len(snapshot) > MAX_SNAPSHOT:
            raise RuntimeError('observed client log unavailable')
        if snapshot is not self._snapshot:
            if not snapshot.startswith(self._snapshot):
                raise ValueError('observed client log changed or shrank')
            complete = self._records.feed(snapshot[len(self._snapshot):])
            self._snapshot = snapshot
            self._complete = complete

    def _wait_hook(self):
        complete = self._check(pending_command=self._state == 'active')
        if self._state == 'active' and not complete:
            raise RuntimeError('client log became incomplete during command')
        # The active TCP participant calls this hook while already reading a
        # command. Reentering owner.poll there would violate its reader lease.
        if self._state == 'waiting':
            self.owner.poll()
            self._check()

    def _checked_motion(self, sample, despawn):
        if (threading.get_ident() != self._thread or self._state != 'active' or
                not self._busy or self._apply_motion is None):
            raise RuntimeError('observed motion callback unavailable')
        if not self._check():
            raise RuntimeError('client log became incomplete during motion')
        result = self._apply_motion(sample, despawn)
        if not self._check():
            raise RuntimeError('client log became incomplete during motion')
        return result

    def start(self):
        """Wait for observations, enroll once, then submit one admission claim."""
        with self._operation(('new',)):
            self._state, self._complete = 'waiting', False
            self.controller.wait_hook = self._hook
            while not self._check():
                self.owner.poll()
                self._check()
                time.sleep(min(.05, max(0, self._deadline - time.monotonic())))
            self.controller.enroll(self._bound.offer.world_epoch, self._deadline)
            self.controller.handle = self._bound.offer.connection_handle
            self._peer = self.controller.peer
            if self._peer is None or not self._check():
                raise RuntimeError('local participant enrollment unconfirmed')
            attempt = self._bound.offer.attempt_id
            self.owner.load_requested(attempt)
            if not self._check(): raise RuntimeError('client log became incomplete')
            self.owner.loaded_marker(attempt, self._bound.marker)
            if not self._check(): raise RuntimeError('client log became incomplete')
            self.owner.paused(attempt, True)
            if not self._check(): raise RuntimeError('client log became incomplete')
            self._state = 'activating'
            # A first pause may need both lazy READY and STOPPED before the
            # native pause. Each IPC has its own timeout, plus framing time.
            activate_options = {'participant_timeout': self._participant_timeout}
            if self._on_stage is not None:
                activate_options['on_stage'] = self._on_stage
            if self._apply_motion is not None:
                activate_options['apply_motion'] = self._checked_motion
            activate_options.update(self._purchase_callbacks)
            self.owner.activate(attempt, _CheckedParticipant(self), **activate_options)
            self._state = 'active'
            if not self._check(): raise RuntimeError('client log became incomplete after claim')
            return True

    def continue_until_exit(self):
        """End the admission time limit after a confirmed, guarded handoff."""
        with self._operation(('active',)):
            if not self._check():
                raise RuntimeError('admission evidence incomplete')
            self._deadline = None

    @property
    def company_seat_at_admission(self):
        return self._records.seat_at_admission

    @property
    def company_seat_status(self):
        return self._records.seat_status

    def verify_final_log(self, snapshot):
        """Finish the same receipt stream after the caller verifies X4 stopped."""
        if threading.get_ident() != self._thread or self._busy or self._state != 'active':
            raise RuntimeError('observed client log unavailable after cleanup')
        self._feed_log(snapshot)
        return self._complete

    def poll(self, timeout=0):
        """Poll the existing participant after checking the evolving log."""
        with self._operation(('active',)):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= .25:
                raise ValueError('bounded participant poll required')
            if not self._check():
                return False
            if self._deadline is not None:
                timeout = min(timeout, max(0, self._deadline - time.monotonic()))
            result = self.owner.poll(timeout)
            if not self._check():
                raise RuntimeError('client log became incomplete during poll')
            return result

    def close(self):
        """Release the network owners; X4 and later log monitoring remain external."""
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive session owner thread required')
        if self._busy:
            error = RuntimeError('reentrant observed session close')
            self._fail(error)
            raise error
        if self._state == 'closed':
            return
        self._state = 'closed'
        failures = []
        for resource in (self.controller, self.owner):
            try:
                resource.close()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise RuntimeError('observed session cleanup failed') from failures[0]
