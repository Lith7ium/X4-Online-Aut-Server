"""Synchronous, opt-in participant-control experiment on owned TCP sockets.

The caller serializes the host and all sockets. During authoritative pause or resume,
a confirmed transport departure may revoke one pilot through the normal disconnect path.
Receipts are trusted client assertions, not proof or authentication.
"""
import json
import math
import select
import socket
import time

from .experimental_local_host import ExperimentalLocalHost
from .load_messages import MAX_MESSAGE, _pairs, _reject_constant

_MAX_ID = 9007199254740991


def _check_idle_sockets(readable, exceptional, retire):
    """Only a definitive end of input is a departure. Queued bytes remain a protocol fault."""
    if exceptional or (readable and retire is None):
        raise RuntimeError('unexpected input or disconnect during participant operation')
    for idle in readable:
        try:
            data = idle.recv(1, socket.MSG_PEEK)
        except ConnectionResetError:
            data = b''
        if data != b'':
            raise RuntimeError('unexpected participant input during operation')
        retire(idle)


class ParticipantGuardFailure(RuntimeError):
    """The owner or world changed during an exchange, independently of a peer's input."""


class ExperimentalTCPParticipants:
    """Participant adapter for an exclusively owned experimental local host.

    Failure permanently faults this adapter. Caller must fence all clients,
    outstanding transfers and the coordinator before attempting recovery.
    """
    def __init__(self, host, *, enabled=False, timeout=30, retire_disconnected=False):
        if (enabled is not True or type(host) is not ExperimentalLocalHost or
                type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise ValueError('explicitly enabled local host and bounded timeout required')
        if type(retire_disconnected) is not bool:
            raise ValueError('participant departure policy must be boolean')
        self._retire_disconnected = retire_disconnected
        host._check()
        if host._world.session.players:
            raise ValueError('install participant adapter before clients join')
        self._host = host
        self._timeout = float(timeout)
        self._next = 1
        self._failed = False
        self._busy = False

    def _control(self, operation, handles):
        host = self._host
        host._control._owner()
        host._control._reader_free()
        if self._failed or self._busy or host._failed:
            raise RuntimeError('participant control unavailable; fence all components')
        world = host._world
        request = world.session.pending
        if (not host._busy or request is None or request.operation != operation or
                type(handles) is not tuple or not handles or
                any(type(h) is not int or h < 1 for h in handles) or
                len(set(handles)) != len(handles) or
                handles != tuple(h for h, _ in world.session.ready_players)):
            raise ValueError('participants must match an active serialized world operation')
        self._busy = True
        try:
            # Validate the complete set before sending any command. Mapping is
            # based on the actual tracked connection, not client-supplied IDs.
            peers = {}
            for conn, peer in host._control._peers.items():
                handle = peer['handle']
                if handle in handles:
                    if handle in peers or peer['stage'] != 'admitted' or peer['in'] or peer['out']:
                        raise RuntimeError('participant socket unavailable')
                    peers[handle] = (conn, peer)
            if set(peers) != set(handles):
                raise RuntimeError('participant socket missing')
            def retire_disconnected(conn):
                peer = host._control._peers.get(conn)
                if peer is None:
                    raise RuntimeError('participant connection changed')
                handle = peer['handle']
                tracked = peers.get(handle)
                if (tracked is None or tracked[0] is not conn or tracked[1] is not peer or
                        peer['stage'] != 'admitted' or peer['in'] or peer['out']):
                    raise RuntimeError('participant connection changed')
                host._control._drop(conn, transport_lost=True)
                del peers[handle]

            # WorldCoordinator confirms the native server pause before calling us.
            retire = retire_disconnected if self._retire_disconnected else None
            deadline = time.monotonic() + self._timeout
            for handle in handles:
                if handle not in peers:
                    continue
                if self._next > _MAX_ID: raise RuntimeError('participant request IDs exhausted')
                number = self._next; self._next += 1
                command = {'version': 1, 'kind': 'participant_command',
                           'epoch': world.session.world_epoch, 'connection_handle': handle,
                           'request_id': number, 'operation': operation}
                expected = dict(command, kind='participant_receipt', status='confirmed')
                raw = json.dumps(command, separators=(',', ':'), sort_keys=True).encode('utf-8') + b'\n'
                if len(raw) > MAX_MESSAGE + 1: raise ValueError('participant command too large')
                conn, peer = peers[handle]
                others = [item[0] for key, item in peers.items() if key != handle]
                self._exchange(conn, peer, others, raw, expected, deadline, retire=retire,
                               retire_on_broken_pipe=operation == 'resume')
                self._quiet(peers, deadline, retire=retire)
            self._quiet(peers, deadline, retire=retire)
            return True
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False

    def _exchange(self, conn, peer, others, raw, expected, deadline, check=None, validate_answer=None,
                  *, retire=None, retire_on_broken_pipe=False):
        control = self._host._control
        waiting = [conn, *others]
        sent = 0
        data = b''
        while True:
            if check is not None:check()
            if control._peers.get(conn) is not peer:
                raise RuntimeError('participant connection changed')
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('participant operation indeterminate')
            readable, writable, exceptional = select.select(waiting, [conn] if sent < len(raw) else [], waiting,
                                                              min(remaining,.1) if check is not None else remaining)
            if check is not None:check()
            if time.monotonic() >= deadline:
                raise TimeoutError('participant operation indeterminate')
            idle_readable = [sock for sock in readable if sock is not conn]
            _check_idle_sockets(idle_readable, exceptional, retire)
            for departed in idle_readable:
                waiting.remove(departed)
            if conn in readable:
                try:
                    chunk = conn.recv(MAX_MESSAGE + 1 - len(data))
                except BlockingIOError:
                    chunk = None
                except ConnectionResetError:
                    if retire is None:
                        raise
                    chunk = b''
                if chunk == b'':
                    if retire is None:
                        raise RuntimeError('participant disconnected during operation')
                    # The departing pilot no longer has authority. Discard any
                    # unfinished participant receipt without acknowledging or replaying it.
                    retire(conn)
                    return None
                if chunk and sent != len(raw):
                    raise RuntimeError('participant response before command completed')
                if chunk:
                    data += chunk
                    if b'\n' in data:
                        frame, tail = data.split(b'\n', 1)
                        if tail or len(frame) > MAX_MESSAGE: raise ValueError('invalid participant frame')
                        try:
                            answer = json.loads(frame.decode('utf-8'), object_pairs_hook=_pairs,
                                                parse_constant=_reject_constant)
                        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
                            raise ValueError('invalid participant receipt') from error
                        choices=expected if type(expected) is tuple else (expected,)
                        if (type(answer) is not dict or
                                type(answer.get('version')) is not int or
                                type(answer.get('request_id')) is not int or
                                type(answer.get('connection_handle')) is not int):
                            raise ValueError('unexpected participant receipt')
                        if validate_answer is None:
                            if not choices or any(type(x) is not dict for x in choices) or set(answer)!=set(choices[0]) or not any(answer==choice for choice in choices):
                                raise ValueError('unexpected participant receipt')
                        else:
                            if expected is not None or not callable(validate_answer):raise ValueError('invalid dynamic receipt validator')
                            frozen=json.dumps(answer,sort_keys=True,separators=(',',':'),allow_nan=False)
                            if validate_answer(answer) is not True or json.dumps(answer,sort_keys=True,separators=(',',':'),allow_nan=False)!=frozen:
                                raise ValueError('unexpected participant receipt')
                            if check is not None:check()
                        if time.monotonic() >= deadline:
                            raise TimeoutError('participant receipt arrived after deadline')
                        readable, _, exceptional = select.select(waiting, [], waiting, 0)
                        _check_idle_sockets(readable, exceptional, retire)
                        return answer
                    if len(data) > MAX_MESSAGE: raise ValueError('participant frame too large')
            if conn in writable:
                try:
                    count = conn.send(raw[sent:])
                except BlockingIOError:
                    continue
                except (BrokenPipeError, ConnectionResetError) as error:
                    if retire is None or (isinstance(error, BrokenPipeError) and not retire_on_broken_pipe):
                        raise
                    retire(conn)
                    return None
                if count <= 0: raise RuntimeError('participant send failed')
                sent += count

    def _quiet(self, peers, deadline, *, retire=None):
        if time.monotonic() >= deadline:
            raise TimeoutError('participant operation indeterminate')
        sockets = []
        for handle, (conn, peer) in peers.items():
            if (self._host._control._peers.get(conn) is not peer or peer['handle'] != handle or
                    peer['stage'] != 'admitted' or peer['in'] or peer['out']):
                raise RuntimeError('participant connection changed')
            sockets.append(conn)
        if sockets:
            readable, _, exceptional = select.select(sockets, [], sockets, 0)
            _check_idle_sockets(readable, exceptional, retire)
        if time.monotonic() >= deadline:
            raise TimeoutError('participant operation indeterminate')

    def pause(self, handles): return self._control('pause', handles)
    def resume(self, handles): return self._control('resume', handles)
