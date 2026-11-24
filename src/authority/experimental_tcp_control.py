"""Explicitly pumped, experimental cooperative TCP load-control channel.

No listener, engine or thread is started here. The caller exclusively owns the
coordinator, service, sockets and this object on one thread, and drives any
pause/save/resume operations separately. Accepted sockets are the connection
contexts. There is no peer authentication, encryption or native X4 integration.
"""
import json
import math
import select
import socket
import threading
import time

from .company_ship_protocol import company_ship_reply
from .load_messages import MAX_MESSAGE, _pairs, _reject_constant
from .trusted_load_messages import (
    MOTION_CLAIM_KIND, decode_load_claim, encode_bound_offer, validate_ship_motion_credential,
)
from .trusted_marker_load_session import TrustedMarkerLoadSession

_REQUEST = b'{"version":1,"kind":"offer_request"}\n'
# A peer may recover at most this much time over its entire connection when
# the host itself cannot pump sockets during synchronous world actions.
_MAX_HOST_BLOCKED_GRACE = 300.0


def offer_request():
    """Return the sole initial/re-offer request frame."""
    return _REQUEST


def _control_message(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid request') from error
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('invalid control message')
    return value


class ExperimentalTCPControl:
    """One owner thread; every method and coordinator operation is serialized.

    `adopt` binds the actual connected socket to a new handle. `poll` is driven
    by the caller; it does not call WorldCoordinator.step. Before requesting an
    offer the caller must complete the world's required pause/save. A successful
    claim is only a trusted-client assertion, not proof that X4 consumed bytes.
    This prototype has only been exercised on loopback; it is not a complete
    Internet protocol or a decision about the eventual transport policy.
    A motion service is for an authenticated transport. Its admit operation
    returns a credential for one admitted handle. Revoke must be idempotent
    and accept an attached handle even if admission never allocated a grant.
    """
    def __init__(self, session, *, enabled=False, idle_timeout=30, max_peers=8, keepalive_enabled=False,
                 motion_service=None, company_purchases=False):
        if enabled is not True or type(session) is not TrustedMarkerLoadSession:
            raise ValueError('explicitly enabled trusted session required')
        if type(idle_timeout) not in (int, float) or not math.isfinite(idle_timeout) or not 0 < idle_timeout <= 300:
            raise ValueError('invalid idle timeout')
        if type(max_peers) is not int or not 1 <= max_peers <= 64:
            raise ValueError('invalid peer limit')
        if type(keepalive_enabled) is not bool:
            raise ValueError('invalid keepalive option')
        if motion_service is not None and any(
                not callable(getattr(motion_service, name, None)) for name in ('admit', 'revoke')):
            raise ValueError('invalid ship motion service')
        # The caller exposes this option only on authenticated control sockets.
        if type(company_purchases) is not bool or (company_purchases and not session._require_company_ship):
            raise ValueError('purchase control requires company ship admission')
        self._company_purchases = company_purchases
        self._motion_service = motion_service
        self._keepalive_enabled = keepalive_enabled
        self._session = session
        self._timeout = float(idle_timeout)
        self._limit = max_peers
        self._peers = {}
        self._thread = threading.get_ident()
        self._failed = False
        self._closed = False
        self._reader_lease = None
        self._job_delivery_owner = None
        self._latest_motion = None
        self._latest_source = None

    def enable_latest_motion(self, *, enabled=False, per_peer_limit=1024, timeout=30):
        """Opt in to pumpable motion on this owner's admitted sockets."""
        self._owner(); self._reader_free()
        if (enabled is not True or self._latest_motion is not None or
                type(per_peer_limit) is not int or not 1 <= per_peer_limit <= 100000 or
                type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise ValueError('invalid latest motion configuration')
        from .experimental_tcp_latest_motion import LatestMotionPoll
        self._latest_motion = LatestMotionPoll(self, limit=per_peer_limit, timeout=float(timeout))

    def motion_barrier(self):
        """Fence new samples and discard unsent samples before lifecycle I/O."""
        self._owner(); self._reader_free(motion=True)
        if self._latest_motion is None: raise RuntimeError('latest motion not enabled')
        return self._latest_motion.barrier()

    def release_motion_barrier(self):
        self._owner(); self._reader_free(motion=True)
        if self._latest_motion is None: raise RuntimeError('latest motion not enabled')
        self._latest_motion.release()

    def _motion_idle(self):
        return self._latest_motion is None or not self._latest_motion.busy()

    def _reader_free(self, *, motion=False):
        if self._reader_lease is not None:
            raise RuntimeError('control sockets are leased to an exclusive operation')
        if not motion and not self._motion_idle():
            raise RuntimeError('latest motion owns pending socket exchanges')

    def _owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive owner thread required')
        if self._failed or self._closed:
            raise RuntimeError('control channel unavailable')

    def _fatal(self, error):
        self._failed = True
        for conn, peer in tuple(self._peers.items()):
            try:
                self._revoke_ship_motion(peer['handle'])
            except BaseException:
                pass
            finally:
                try: conn.close()
                except BaseException: pass
        self._peers.clear()
        raise RuntimeError('control channel faulted; fence coordinator and service') from error

    def adopt(self, conn, *, defer_attach=False):
        """Adopt a socket and return its handle, or None while awaiting an offer request."""
        self._owner()
        self._reader_free(motion=True)
        if type(defer_attach) is not bool:
            raise ValueError('invalid deferred attach option')
        if (type(conn) is not socket.socket or conn.family != socket.AF_INET or conn.proto not in (0, socket.IPPROTO_TCP) or
                conn.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM):
            raise ValueError('connected IPv4 TCP stream socket required')
        if conn in self._peers or len(self._peers) >= self._limit:
            raise ValueError('duplicate connection or peer limit')
        conn.getpeername()  # reject unconnected/closed sockets before mutating session
        conn.setblocking(False)
        if not self._live_socket(conn):
            raise ValueError('connection already closed')
        try:
            handle = None if defer_attach else self._session.attach(conn)
            self._peers[conn] = {'handle': handle, 'in': b'', 'out': b'', 'stage': 'attached',
                                 'deadline': time.monotonic() + self._timeout,
                                 'host_blocked_grace': 0.0}
        except BaseException as error:
            # attach may have succeeded even if control was interrupted
            # before the new connection could be recorded here.
            try: conn.close()
            except BaseException: pass
            self._fatal(error)
        return handle

    def _account_host_blocked(self, started, ended):
        """Compensate bounded host-owned I/O starvation, never peer activity.

        Called only after a successfully completed synchronous pause or save,
        immediately following a poll which expired old peers. A peer expiring
        after that poll but before the operation starts receives no credit.
        The cumulative allowance is per connection and is not renewed by frames.
        EOF, unread data, and the usual admission checks still apply.
        """
        self._owner()
        self._reader_free()
        elapsed = ended - started
        if not math.isfinite(elapsed) or elapsed < 0:
            raise RuntimeError('invalid host blocked interval')
        for peer in self._peers.values():
            if peer['deadline'] is None or peer['deadline'] <= started:
                continue
            credit = min(elapsed, max(0.0, _MAX_HOST_BLOCKED_GRACE - peer['host_blocked_grace']))
            peer['deadline'] += credit
            peer['host_blocked_grace'] += credit

    def _revoke_ship_motion(self, handle, *, transport_lost=False):
        if self._motion_service is not None and handle is not None:
            try:
                revoke = self._motion_service.revoke
                if transport_lost:
                    on_loss = getattr(self._motion_service, 'transport_lost', None)
                    if callable(on_loss):
                        revoke = on_loss
                revoke(handle)
            except Exception:
                raise RuntimeError('ship motion revocation failed') from None

    def _drop(self, conn, *, transport_lost=False):
        try:
            peer = self._peers[conn]
            handle = peer['handle']
            # Only an admitted peer could have published a pose. The motion
            # service withdraws authorization immediately in either case.
            self._revoke_ship_motion(handle, transport_lost=transport_lost and peer['stage'] == 'admitted')
            self._peers.pop(conn)
            if handle is not None:
                self._session.disconnect(conn)
                delivery = self._job_delivery_owner
                if delivery is not None:
                    delivery._peer_disconnected(handle)
            conn.close()
        except BaseException as error:
            try:
                conn.close()
            except BaseException:
                pass
            finally: self._fatal(error)

    @staticmethod
    def _live_socket(conn):
        # A peer which has already closed its write side cannot submit an
        # admission claim. This is point-in-time, not transport authentication.
        try:
            if select.select([conn], [], [], 0)[0]:
                try: return conn.recv(1, socket.MSG_PEEK) != b''
                except BlockingIOError: return True
            return True
        except (OSError, ValueError):
            return False

    def _current_socket(self, conn, peer):
        return (self._peers.get(conn) is peer and time.monotonic() < peer['deadline']
                and self._live_socket(conn))

    def _offer_ready(self):
        world = self._session._world
        state = world.session
        return (state.state == 'paused' and state.pending is None and not state.save_required
                and state.baseline_id is not None and world._baseline is not None
                and world._baseline[0] == state.baseline_id)

    def _frame(self, conn, peer, raw):
        try:
            message = _control_message(raw)
            if peer['stage'] in ('attached', 'offered') and message.get('kind') == 'offer_request':
                if set(message) != {'version', 'kind'}:
                    raise ValueError('invalid offer request')
                if peer['handle'] is None:
                    if not self._current_socket(conn, peer):
                        self._drop(conn)
                        return
                    peer['handle'] = self._session.attach(conn)
                if not self._offer_ready():
                    peer['out'] += b'{"version":1,"kind":"not_ready"}\n'
                    return
                started = time.monotonic()
                bound = self._session.begin(conn)
                self._account_host_blocked(started, time.monotonic())
                if not self._current_socket(conn, peer):
                    self._drop(conn)
                    return
                peer['out'] += encode_bound_offer(bound) + b'\n'
                peer.pop('company_purchase_ship', None)
                peer.pop('company_purchase_epoch', None)
                peer['stage'] = 'offered'
                peer['attempt_id'] = bound.offer.attempt_id
                peer['keepalive_sequence'] = 0
                return
            if peer['stage'] != 'offered':
                raise ValueError('unexpected control message')
            if message.get('kind') == 'enable_company_purchases':
                if (not self._company_purchases or set(message) != {'version', 'kind', 'attempt_id', 'ship_index'}
                        or message['attempt_id'] != peer['attempt_id'] or 'company_purchase_ship' in peer):
                    raise ValueError('invalid purchase control request')
                started = time.monotonic()
                registry_epoch = self._session.purchase_reservation(conn, message['attempt_id'], message['ship_index'])
                self._account_host_blocked(started, time.monotonic())
                if not self._current_socket(conn, peer):
                    self._drop(conn)
                    return
                peer.update(company_purchase_ship=message['ship_index'], company_purchase_epoch=registry_epoch)
                reply = dict(message, kind='company_purchases_enabled', registry_epoch=registry_epoch)
                peer['out'] += json.dumps(reply, separators=(',', ':')).encode('ascii') + b'\n'
                return
            if message.get('kind') in ('list_company_ships', 'choose_company_ship'):
                if message['kind'] == 'choose_company_ship' and 'company_purchase_ship' in peer:
                    raise ValueError('negotiated purchase control fixes the ship choice')
                started = time.monotonic()
                response = company_ship_reply(message, self._session, conn)
                self._account_host_blocked(started, time.monotonic())
                if self._current_socket(conn, peer):
                    peer['out'] += response
                else:
                    self._drop(conn)
                return
            if self._keepalive_enabled and message.get('kind') == 'keepalive':
                if (set(message) != {'version', 'kind', 'attempt_id', 'sequence'} or
                        type(message['version']) is not int or message['version'] != 1 or
                        message['attempt_id'] != peer['attempt_id'] or
                        type(message['sequence']) is not int or
                        message['sequence'] != peer['keepalive_sequence'] + 1 or
                        message['sequence'] > 2**53 - 1):
                    raise ValueError('invalid keepalive')
                peer['keepalive_sequence'] = message['sequence']
                reply = {'version': 1, 'kind': 'alive', 'attempt_id': peer['attempt_id'],
                         'sequence': message['sequence']}
                peer['out'] += json.dumps(reply, separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n'
                return
            ship_motion = message.get('kind') == MOTION_CLAIM_KIND
            if ship_motion and self._motion_service is None:
                raise ValueError('ship motion service unavailable')
            claim = decode_load_claim(raw, ship_motion=ship_motion)
            started = time.monotonic()
            def pre_accept():
                self._account_host_blocked(started, time.monotonic())
                return self._current_socket(conn, peer)
            options = {'ship_motion': True} if ship_motion else {}
            outcome = self._session.accept(conn, claim, pre_accept=pre_accept, **options)
            if not self._current_socket(conn, peer):
                self._drop(conn)
                return
            reply = {'version': 1, 'kind': 'accepted', 'attempt_id': outcome.attempt_id}
            if ship_motion:
                ship = outcome.motion_ship
                epoch = claim.receipt.world_epoch
                try:
                    value = self._motion_service.admit(peer['handle'], outcome.attempt_id, ship.ship_index,
                        ship.sector_macro, epoch, outcome.registry_epoch)
                except ValueError:
                    raise ValueError('ship motion admission refused') from None
                except Exception:
                    raise RuntimeError('ship motion admission failed') from None
                reply['ship_motion'] = validate_ship_motion_credential(value, index=ship.ship_index,
                    session_epoch=epoch, registry_epoch=outcome.registry_epoch)
                if not self._current_socket(conn, peer):
                    self._drop(conn)
                    return
            peer['out'] += json.dumps(reply, separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n'
            peer['stage'] = 'admitted'
        except ValueError as error:
            if self._session._failed or self._session._inner._failed:
                self._fatal(error)
            self._drop(conn)
        except BaseException as error:
            self._fatal(error)

    def poll(self, timeout=0):
        """Handle bounded socket I/O and idle expiry once on the owner thread."""
        self._owner()
        self._reader_free(motion=True)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 1:
            raise ValueError('invalid poll timeout')
        now = time.monotonic()
        for conn, peer in tuple(self._peers.items()):
            active = peer.get('motion_active')
            if ((peer['deadline'] is not None and now >= peer['deadline']) or
                    (active is not None and now >= active['deadline'])):
                self._drop(conn, transport_lost=True)
        if not self._peers:
            return
        sockets = tuple(self._peers)
        if self._latest_motion is not None:
            for peer in self._peers.values(): self._latest_motion.prepare(peer)
        deadlines = [p['deadline'] for p in self._peers.values() if p['deadline'] is not None]
        deadlines += [p['motion_active']['deadline'] for p in self._peers.values() if p.get('motion_active')]
        wait = min(timeout, max(0, min(deadlines) - time.monotonic())) if deadlines else timeout
        try:
            readable, writable, exceptional = select.select(sockets, [c for c in sockets if self._peers[c]['out'] or (self._peers[c].get('motion_active') and self._peers[c]['motion_active']['sent'] < len(self._peers[c]['motion_active']['data']))], sockets, wait)
        except (OSError, ValueError) as error:
            self._fatal(error)
        now = time.monotonic()
        for conn, peer in tuple(self._peers.items()):
            active = peer.get('motion_active')
            if ((peer['deadline'] is not None and now >= peer['deadline']) or
                    (active is not None and now >= active['deadline'])):
                self._drop(conn, transport_lost=True)
        for conn in exceptional:
            if conn in self._peers: self._drop(conn, transport_lost=True)
        for conn in readable:
            if conn not in self._peers: continue
            try: chunk = conn.recv(MAX_MESSAGE + 1)
            except BlockingIOError: continue
            except OSError:
                self._drop(conn, transport_lost=True); continue
            if not chunk:
                self._drop(conn, transport_lost=True); continue
            peer = self._peers[conn]
            if peer.get('motion_active'):
                self._latest_motion.receive(conn, peer, chunk)
                continue
            # Admitted peers only reply to a pending host exchange. The motion
            # reader above or the synchronous exchange owner consumes that reply.
            if peer['out'] or peer['stage'] == 'admitted':
                self._drop(conn); continue
            peer['in'] += chunk
            # Process at most one message per peer per poll; avoid unbounded
            # buffering and reject pipelined input rather than mixing attempts.
            if b'\n' in peer['in']:
                raw, tail = peer['in'].split(b'\n', 1)
                if len(raw) > MAX_MESSAGE or tail:
                    self._drop(conn); continue
                peer['in'] = b''
                peer['deadline'] = time.monotonic() + self._timeout
                self._frame(conn, peer, raw)
            elif len(peer['in']) > MAX_MESSAGE:
                self._drop(conn)
        for conn in writable:
            if conn not in self._peers: continue
            peer = self._peers[conn]
            if peer.get('motion_active'):
                try: self._latest_motion.send(conn, peer)
                except OSError: self._drop(conn)
                continue
            try: sent = conn.send(peer['out'])
            except BlockingIOError: continue
            except OSError:
                self._drop(conn, transport_lost=True); continue
            if sent <= 0: self._drop(conn, transport_lost=True)
            else:
                peer['out'] = peer['out'][sent:]
                if not peer['out'] and peer['stage'] == 'admitted':
                    peer['deadline'] = None

    def ready_for_resume(self):
        """Point-in-time output-drain and peer check; caller must honor it.

        True means no tracked peer has pending output or unread input and no
        EOF was observed. It does not establish remote receipt or future liveness.
        The coordinator still decides whether a resume is valid.
        """
        self._owner()
        self._reader_free()
        ready = True
        for conn, peer in tuple(self._peers.items()):
            # An incomplete first request does not participate in world operations.
            if peer['handle'] is None:
                continue
            if peer['deadline'] is not None and time.monotonic() >= peer['deadline']:
                self._drop(conn, transport_lost=True)
                ready = False
                continue
            try:
                readable, _, exceptional = select.select([conn], [], [conn], 0)
                if exceptional:
                    self._drop(conn, transport_lost=True)
                    ready = False
                    continue
                if readable:
                    try: data = conn.recv(1, socket.MSG_PEEK)
                    except BlockingIOError: data = None
                    if data == b'':
                        self._drop(conn, transport_lost=True)
                    ready = False
                if peer['in'] or peer['out']:
                    ready = False
            except OSError:
                self._drop(conn, transport_lost=True)
                ready = False
            except ValueError:
                self._drop(conn)
                ready = False
        return ready

    def close(self):
        """Disconnect all adopted sockets, retaining no listener or thread."""
        self._owner()
        self._reader_free(motion=True)
        try:
            for conn in tuple(self._peers): self._drop(conn)
        finally:
            self._closed = True
