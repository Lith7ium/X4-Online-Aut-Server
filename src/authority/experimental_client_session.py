"""Synchronous client owner with an owned background TLS bridge only.

No game process is started here. The caller keeps native lifecycle and every
owner operation on its own thread and closes native resources before leaving.
"""
import asyncio
from contextlib import contextmanager
import math
import socket
import threading
import time

from .experimental_client_connection import _listener
from .experimental_client_owner import ExperimentalClientOwner
from .experimental_tls_tunnel import start_client


class ClientConnectionError(RuntimeError):
    """Fixed credential-free lifecycle failure."""


class _TLSBridgeThread:
    def __init__(self, options, *, motion_enabled=False):
        self.options = options
        self.motion_enabled = motion_enabled
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.loop = self.stop = None
        self.ports = None
        self.failed = False
        self.cleanup_failed = False
        self.thread = threading.Thread(target=self._run, name='authority-client-tls')

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            self.failed = True
        finally:
            self.ready.set()

    async def _serve(self):
        listeners, tunnel = [], None
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        try:
            listeners.append(_listener())
            listeners.append(_listener())
            if self.motion_enabled:
                listeners.append(_listener())
            extra = {'motion_listener': listeners[2]} if self.motion_enabled else {}
            tunnel = await start_client(control_listener=listeners[0], file_listener=listeners[1],
                max_connections=2, **self.options, **extra)
            self.ports = tuple(listener.getsockname()[1] for listener in listeners)
            self.ready.set()
            if not self.stop_requested.is_set():
                await self.stop.wait()
        finally:
            try:
                if tunnel is not None:
                    await asyncio.wait_for(tunnel.close(), 5)
            except BaseException:
                self.cleanup_failed = True
                raise
            finally:
                for listener in listeners:
                    listener.close()

    def close(self):
        self.stop_requested.set()  # Also covers stop before the event loop exists.
        if self.loop is not None and self.stop is not None:
            try:
                self.loop.call_soon_threadsafe(self.stop.set)
            except RuntimeError:
                pass  # The thread may already have completed cleanup.
        if self.thread.ident is not None:
            self.thread.join(8)
        if self.thread.is_alive() or self.cleanup_failed:
            raise ClientConnectionError('tls_cleanup_failed')


@contextmanager
def client_connection(*, server_host, server_port, server_fingerprint, shared_key,
                      file_token, file_slot, deadline, io_timeout=30, keepalive_interval=None,
                      motion_enabled=False):
    """Keep one offer socket alive through native load and participant polling.

    All ExperimentalClientOwner methods run on the entering thread. Native
    resources must be stopped inside the with-block, before bridge shutdown.
    Closing/joining the bridge is bounded; failure never returns success.
    When opted in, owner.motion_port exposes a loopback endpoint. The caller
    must separately bind and authorize that stream with the admitted session.
    """
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline-time.monotonic() <= 3600:
        raise ValueError('invalid_client_deadline')
    if type(motion_enabled) is not bool:
        raise ValueError('invalid_motion_option')
    bridge = _TLSBridgeThread(dict(server_host=server_host, server_port=server_port,
        server_fingerprint=server_fingerprint, shared_key=shared_key), motion_enabled=motion_enabled)
    connection = owner = None
    original_error = None
    try:
        bridge.thread.start()
        if not bridge.ready.wait(min(10, max(0, deadline-time.monotonic()))):
            raise ClientConnectionError('tls_startup_timeout')
        if bridge.failed or bridge.ports is None or not bridge.thread.is_alive():
            raise ClientConnectionError('tls_startup_failed')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('client_deadline')
        connection = socket.create_connection(('127.0.0.1', bridge.ports[0]), timeout=min(io_timeout, remaining))
        owner = ExperimentalClientOwner(connection, enabled=True, file_host='127.0.0.1',
            file_port=bridge.ports[1], file_token=file_token, file_slot=file_slot,
            deadline=deadline, io_timeout=io_timeout, keepalive_interval=keepalive_interval,
            motion_port=bridge.ports[2] if motion_enabled else None)
        yield owner
    except BaseException as error:
        original_error = error
        raise
    finally:
        failures = []
        try:
            if owner is not None:
                owner.close()
            elif connection is not None:
                connection.close()
        except BaseException:
            failures.append('owner_cleanup_failed')
        try:
            bridge.close()
        except BaseException:
            failures.append('tls_cleanup_failed')
        if failures:
            if original_error is not None:
                for failure in failures:
                    original_error.add_note(failure)
            else:
                raise ClientConnectionError('client_cleanup_failed')
        if original_error is None and bridge.failed:
            raise ClientConnectionError('tls_bridge_failed')
