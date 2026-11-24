"""Opt-in loopback-only owner for existing experimental host components.

The caller creates and fences the engine, listener, and started file service;
it owns cleanup, serializes all access, and supplies real participant controls
before using real clients. No X4 process or network listener is started here.
"""
import socket
import time

from lockstep.launcher.joinfiles import FileService
from .experimental_tcp_control import ExperimentalTCPControl
from .world import WorldCoordinator


class ExperimentalLocalHost:
    """Pump a connected loopback control channel and one world action per tick.

    This is an integration seam, not a runnable server. Bridge actions are
    synchronous and may block the owner's network pumping until they finish.
    Pause/save compensate peer idle deadlines for that blocked time, up to
    300 seconds in total per connection. This allowance is not a heartbeat or
    a guarantee of peer liveness and does not cover download or client loading.
    A failed action receives no compensation and fences the host.
    After an indeterminate exception all components must be fenced/discarded.
    """
    def __init__(self, world, control, service, *, enabled=False):
        if (enabled is not True or type(world) is not WorldCoordinator or
                type(control) is not ExperimentalTCPControl or type(service) is not FileService):
            raise ValueError('explicitly enabled local host components required')
        control._owner()
        if (control._session._world is not world or world._offers.service is not service or
                service.server.server_address[0] != '127.0.0.1' or
                service.thread is None or not service.thread.is_alive() or control._peers or
                world.session.players):
            raise ValueError('fresh serialized world and live loopback service required')
        self._world, self._control, self._service = world, control, service
        self._failed = False
        self._busy = False

    def _check(self):
        try:
            self._control._owner()
        except RuntimeError as error:
            if self._control._failed or self._control._closed:
                self._failed = True
                raise RuntimeError('control unavailable; fence all components') from error
            raise
        if self._failed:
            raise RuntimeError('host faulted; fence all components')
        if self._busy:
            raise RuntimeError('reentrant host access is forbidden')
        if not self._service.thread.is_alive():
            self._failed = True
            raise RuntimeError('file service stopped; fence all components')

    def adopt(self, conn, *, defer_attach=False):
        """Adopt a loopback socket; with deferred attach, wait for its offer request."""
        self._check()
        if (type(conn) is not socket.socket or conn.family != socket.AF_INET or
                conn.getpeername()[0] != '127.0.0.1'):
            raise ValueError('connected loopback socket required')
        self._busy = True
        try:
            return self._control.adopt(conn, defer_attach=defer_attach)
        except BaseException:
            if self._control._failed:
                self._failed = True
            raise
        finally:
            self._busy = False

    def tick(self, timeout=0, *, defer_world=False):
        """Pump sockets and dispatch at most one pending synchronous action."""
        if type(defer_world) is not bool:raise ValueError('invalid world dispatch option')
        self._check()
        self._busy = True
        try:
            motion = self._control._latest_motion
            if motion is not None and self._world.session.pending is not None:
                motion.barrier(world=True)
            self._control.poll(timeout)
            pending = self._world.session.pending
            if pending is None:
                if motion is not None and motion.world_blocked:
                    motion.release(world=True)
                return None
            if motion is not None and not motion.barrier(world=True):
                return None
            source = self._control._latest_source
            if defer_world or (source is not None and source.state=='pulling'):
                return None
            if pending.operation == 'resume' and not self._control.ready_for_resume():
                return None
            if pending.operation in ('pause', 'save'):
                started = time.monotonic()
                result = self._world.step()
                self._control._account_host_blocked(started, time.monotonic())
                return result
            return self._world.step()
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False
