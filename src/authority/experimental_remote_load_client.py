"""One cooperative load attempt over caller-supplied connected TCP.

No listener, X4 integration, endpoint discovery, authentication or encryption
is supplied. The caller exclusively owns the socket, destination and local
game process; supplied load/marker/pause observations are trusted assertions.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import select
import socket
import threading
import time

from .experimental_client_load import ExperimentalClientLoad
from .client_save_staging import copy_offered_save
from .experimental_tcp_control import offer_request
from .load_messages import MAX_MESSAGE, _pairs, _reject_constant
from .save_transfer import SaveOffer, receive
from .trusted_load_messages import decode_bound_offer, encode_load_claim, validate_ship_motion_credential


_COMPANY_SHIP_MAX_INDEX = 200_000
_COMPANY_SHIP_DENIALS = ('company_ship_unavailable', 'stale_ship_choice', 'company_ship_catalog_unavailable')
_COMPANY_SHIP_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
_COMPANY_SHIP_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_COMPANY_REPLY_FIELDS = {'version', 'kind', 'attempt_id'}


def _company_ship_index(value):
    return type(value) is int and 1 <= value <= _COMPANY_SHIP_MAX_INDEX


def _company_ship(value):
    if type(value) is not dict or set(value) != {'ship_index', 'sector_macro', 'name', 'code'}:
        raise ValueError('invalid company ship fields')
    sector, name, code = value['sector_macro'], value['name'], value['code']
    if (not _company_ship_index(value['ship_index'])
            or type(sector) is not str or not _COMPANY_SHIP_SECTOR.fullmatch(sector)
            or (name is not None and (type(name) is not str or not 1 <= len(name) <= 128
                 or name.strip() != name or not name.isprintable() or '{' in name or '}' in name))
            or (code is not None and (type(code) is not str or not _COMPANY_SHIP_CODE.fullmatch(code)))):
        raise ValueError('invalid company ship values')
    return value


class SessionFull(RuntimeError):
    """The authenticated server has no free pilot place."""

    code = 'session_full'
    message = 'Session Full'


class ExperimentalRemoteLoadClient:
    """Single-owner, single-attempt state machine.

    Once construction succeeds, failures in owner operations close TCP. A
    rejected constructor leaves its input socket with the caller; a call from
    another thread is rejected without changing the owner's socket or state.

    A caller supplies an already-connected IPv4 TCP stream, a file endpoint,
    and a monotonic absolute deadline. `download` uses the existing file fetcher:
    its socket operations are capped by the same absolute deadline. DNS,
    filesystem calls and callbacks cannot be interrupted; the deadline is
    checked after each returns. The caller must keep the download and staged paths
    exclusive, and establish that reported observations belong to the still-live
    X4 process. No native file consumption or peer authenticity is proven.
    Optional keepalive requires a matching enabled server. Its interval must be
    chosen below the server idle timeout with room for network and scheduling
    delays. While awaiting X4 events the owner must call poll() often enough;
    there is no background thread. DNS and filesystem calls may still block.
    """
    def __init__(self, conn, *, enabled=False, file_host, file_port, file_token,
                 file_slot, deadline, io_timeout=30, keepalive_interval=None):
        if (enabled is not True or type(conn) is not socket.socket or conn.family != socket.AF_INET or
                conn.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
                conn.proto not in (0, socket.IPPROTO_TCP) or type(file_host) is not str or
                not file_host or len(file_host) > 253 or type(file_port) is not int or not 1 <= file_port <= 65535 or
                type(file_token) is not str or not re.fullmatch(r'[0-9a-f]{32}', file_token) or
                type(file_slot) is not int or not 1 <= file_slot <= 8 or
                type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline <= time.monotonic() or
                type(io_timeout) not in (int,float) or not math.isfinite(io_timeout) or not 0 < io_timeout <= 300 or
                (keepalive_interval is not None and (type(keepalive_interval) not in (int,float) or
                 not math.isfinite(keepalive_interval) or not 0 < keepalive_interval <= 300))):
            raise ValueError('invalid enabled client, endpoint or deadline')
        conn.getpeername()
        conn.setblocking(False)
        self._conn = conn; self._endpoint = (file_host,file_port,file_token,file_slot)
        self._deadline = float(deadline); self._io_timeout = float(io_timeout)
        self._thread = threading.get_ident(); self._state = 'new'; self._busy = False
        self._bound = self._load = None
        self._chosen_ship_index = None
        self._ship_motion_requested = False
        self._keepalive_interval = keepalive_interval
        self._next_keepalive = None
        self._keepalive_sequence = 1

    def _remaining(self):
        value = self._deadline-time.monotonic()
        if value <= 0: raise TimeoutError('load attempt deadline expired')
        return value

    def _fault(self):
        self._state = 'failed'
        if self._load is not None: self._load.close()
        if self._conn is not None:
            try: self._conn.close()
            except OSError: pass
            self._conn = None

    @contextmanager
    def _operation(self, expected):
        if threading.get_ident() != self._thread: raise RuntimeError('exclusive owner thread required')
        if self._busy or self._state != expected:
            self._fault(); raise RuntimeError('reentrant, stale or out-of-order load operation')
        self._busy = True
        try:
            self._remaining(); yield; self._remaining()
        except BaseException:
            self._fault(); raise
        finally: self._busy = False

    def _wait(self, *, write=False, use_overall_deadline=False):
        duration = self._remaining() if use_overall_deadline else min(self._remaining(), self._io_timeout)
        read, writable, exceptional = select.select([] if write else [self._conn],
            [self._conn] if write else [], [self._conn], duration)
        self._remaining()
        if exceptional or not (writable if write else read): raise TimeoutError('control I/O failed or timed out')

    def _send(self, data):
        view = memoryview(data)
        while view:
            self._wait(write=True)
            try: sent = self._conn.send(view)
            except BlockingIOError: continue
            if sent <= 0: raise ConnectionError('control connection closed')
            view = view[sent:]

    def _read_frame(self, *, use_overall_deadline=False):
        # Never consume a subsequent participant command from the same stream.
        data = bytearray()
        while len(data) <= MAX_MESSAGE:
            self._wait(use_overall_deadline=use_overall_deadline)
            try: chunk = self._conn.recv(1)
            except BlockingIOError: continue
            if not chunk: raise ConnectionError('control connection closed')
            if chunk == b'\n': return bytes(data)
            data.extend(chunk)
        raise ValueError('control frame exceeds limit')

    def _pump(self):
        """Pump only on the owner thread, with no other frame in flight."""
        self._remaining()
        readable, _, exceptional = select.select([self._conn], [], [self._conn], 0)
        if readable or exceptional:
            raise ConnectionError('unexpected input during load')
        if time.monotonic() < self._next_keepalive:
            return
        started = time.monotonic()
        seq = self._keepalive_sequence
        request = {'version': 1, 'kind': 'keepalive',
                   'attempt_id': self._bound.offer.attempt_id, 'sequence': seq}
        self._send(json.dumps(request, separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n')
        try:
            response = json.loads(self._read_frame().decode('utf-8'), object_pairs_hook=_pairs,
                                  parse_constant=_reject_constant)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('invalid keepalive response') from error
        if (type(response) is not dict or set(response) != {'version', 'kind', 'attempt_id', 'sequence'} or
                type(response['version']) is not int or response['version'] != 1 or response['kind'] != 'alive' or
                response['attempt_id'] != self._bound.offer.attempt_id or
                type(response['sequence']) is not int or response['sequence'] != seq):
            raise ValueError('keepalive response differs from current attempt')
        self._keepalive_sequence += 1
        self._next_keepalive = started + self._keepalive_interval

    def poll(self):
        """Maintain an enabled offer while the owner waits for local X4 events.

        This synchronously waits for a keepalive response. Each socket wait is
        capped by io_timeout; a fragmented response can take longer in total,
        but cannot extend the absolute load deadline.
        """
        with self._operation(self._state):
            if self._keepalive_interval is None or self._state not in (
                    'offered', 'downloaded', 'staged', 'requested', 'loaded', 'paused'):
                raise RuntimeError('no enabled active load to pump')
            self._pump()

    def request_offer(self):
        """Wait for a baseline within the existing overall load deadline.

        A not_ready response can occur while the server pauses and saves for
        this join. Retry only that exact protocol response, without overlapping
        requests or extending the caller's deadline. A session_full response
        ends this attempt. Other responses still fail closed. The offer
        response may take until the overall deadline because server pause/save
        is synchronous. EOF and socket errors remain visible.
        Other socket waits retain io_timeout.
        """
        with self._operation('new'):
            while True:
                self._send(offer_request())
                raw = self._read_frame(use_overall_deadline=True)
                try:
                    reply = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                                       parse_constant=_reject_constant)
                except (UnicodeError, json.JSONDecodeError, RecursionError):
                    reply = None
                if (type(reply) is dict and set(reply) == {'version', 'kind'} and
                        type(reply['version']) is int and reply['version'] == 1 and
                        reply['kind'] in ('not_ready', SessionFull.code)):
                    if reply['kind'] == SessionFull.code:
                        raise SessionFull(SessionFull.message)
                    # No pipelining: unexpected data or EOF before the next
                    # request is a protocol failure, not another response.
                    delay = min(0.1, self._remaining())
                    readable, _, exceptional = select.select([self._conn], [], [self._conn], delay)
                    self._remaining()
                    if readable or exceptional:
                        raise ConnectionError('unexpected input between offer requests')
                    continue
                self._bound = decode_bound_offer(raw)
                self._load = ExperimentalClientLoad(enabled=True,offer=self._bound.offer,expected_marker=self._bound.marker)
                if self._keepalive_interval is not None:
                    self._next_keepalive = time.monotonic() + self._keepalive_interval
                self._state = 'offered'
                return self._bound

    def _company_ship_exchange(self, kind, **fields):
        attempt = self._bound.offer.attempt_id
        readable, _, exceptional = select.select([self._conn], [], [self._conn], 0)
        if readable or exceptional:
            raise ConnectionError('unexpected input before company ship request')
        request = {'version': 1, 'kind': kind, 'attempt_id': attempt, **fields}
        self._send(json.dumps(request, separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n')
        try:
            # A first lookup may verify every ship in a large checkpoint before replying.
            first_lookup = kind == 'choose_company_ship' or fields.get('after_index') == 0
            frame = self._read_frame(use_overall_deadline=first_lookup)
            reply = json.loads(frame.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('invalid company ship response') from error
        if (type(reply) is not dict or type(reply.get('version')) is not int or reply['version'] != 1
                or type(reply.get('kind')) is not str or reply.get('attempt_id') != attempt):
            raise ValueError('company ship response differs from current attempt')
        if reply['kind'] in _COMPANY_SHIP_DENIALS:
            if set(reply) != _COMPANY_REPLY_FIELDS:
                raise ValueError('invalid company ship denial fields')
            return reply['kind']
        return reply

    def list_company_ships(self):
        """Read the ordered public ship catalogue or return a recoverable denial kind."""
        with self._operation('offered'):
            ships, cursor = [], 0
            while True:
                reply = self._company_ship_exchange('list_company_ships', after_index=cursor)
                if type(reply) is str:
                    return reply
                if (set(reply) != _COMPANY_REPLY_FIELDS | {'ships', 'next_index'}
                        or reply['kind'] != 'company_ships' or type(reply['ships']) is not list):
                    raise ValueError('invalid company ship page')
                last_index = cursor
                for item in reply['ships']:
                    ship = _company_ship(item)
                    if ship['ship_index'] <= last_index:
                        raise ValueError('company ship page does not advance in index order')
                    last_index = ship['ship_index']
                    ships.append(ship)
                next_index = reply['next_index']
                if next_index is None:
                    return tuple(ships)
                if (not _company_ship_index(next_index) or next_index != last_index
                        or next_index <= cursor or next_index == _COMPANY_SHIP_MAX_INDEX):
                    raise ValueError('invalid company ship page cursor')
                cursor = next_index

    def choose_company_ship(self, ship_index):
        """Return the chosen public ship or a recoverable denial kind."""
        with self._operation('offered'):
            if not _company_ship_index(ship_index):
                raise ValueError('invalid company ship index')
            if self._ship_motion_requested and ship_index != self._chosen_ship_index:
                raise ValueError('ship selection is fixed for the motion claim')
            reply = self._company_ship_exchange('choose_company_ship', ship_index=ship_index)
            if type(reply) is str:
                return reply
            if set(reply) != _COMPANY_REPLY_FIELDS | {'ship'} or reply['kind'] != 'company_ship_chosen':
                raise ValueError('invalid company ship choice response')
            ship = _company_ship(reply['ship'])
            if ship['ship_index'] != ship_index:
                raise ValueError('company ship choice differs from requested index')
            self._chosen_ship_index = ship_index
            return ship

    def enable_company_purchases(self, *, undock=False, trade_view=False):
        """Negotiate the server-poll extension on this pilot's existing control socket."""
        with self._operation('offered'):
            if self._chosen_ship_index is None:
                raise ValueError('purchase control requires a chosen company ship')
            if type(undock) is not bool or type(trade_view) is not bool:
                raise ValueError('invalid Company trading capabilities')
            versions = {'statement_version': 1}
            if trade_view:
                versions['trade_view_version'] = 1
            if undock:
                versions['undock_version'] = 1
            reply = self._company_ship_exchange('enable_company_purchases', ship_index=self._chosen_ship_index,
                                                **versions)
            expected_fields = _COMPANY_REPLY_FIELDS | {'ship_index', 'registry_epoch'} | versions.keys()
            if (type(reply) is not dict or set(reply) != expected_fields
                    or any(type(reply[key]) is not int or reply[key] != version for key, version in versions.items())
                    or reply['kind'] != 'company_purchases_enabled' or not _company_ship_index(reply['ship_index'])
                    or reply['ship_index'] != self._chosen_ship_index or type(reply['registry_epoch']) is not str
                    or not re.fullmatch(r'[0-9a-f]{32}', reply['registry_epoch'])):
                raise ValueError('server did not enable purchases for the selected ship')
            return reply['registry_epoch']

    def request_ship_motion(self):
        """Opt in locally for the chosen ship; the claim will carry the request."""
        with self._operation(self._state):
            if self._state not in ('offered', 'downloaded', 'staged', 'requested', 'loaded', 'paused'):
                raise ValueError('ship motion requires an outstanding offer')
            if self._chosen_ship_index is None:
                raise ValueError('ship motion requires a chosen company ship')
            if not re.fullmatch(r'[0-9a-f]{32}', self._bound.offer.world_epoch):
                raise ValueError('ship motion requires a hex session epoch')
            self._ship_motion_requested = True

    def download(self, new_directory):
        """Download to a new exclusively owned directory; no retry or load."""
        with self._operation('offered'):
            offer = self._bound.offer
            save = SaveOffer(offer.name, offer.sha256, offer.size, offer.checkpoint_id)
            host, port, token, slot = self._endpoint
            options = {}
            if self._keepalive_interval is not None:
                options = {'pump': self._pump, 'pump_interval': min(.25, self._keepalive_interval / 2)}
            path = receive(host,port,token,slot,save,Path(new_directory),retries=0,
                           timeout=min(self._io_timeout,self._remaining()),deadline=self._deadline, **options)
            self._state = 'downloaded'
            return path

    def verify_staged(self, path):
        """Check a caller-staged copy before its X4 load is requested."""
        with self._operation('downloaded'):
            self._load.verify_staged(path, pump=self._pump if self._keepalive_interval is not None else None)
            self._state = 'staged'

    def copy_to_new_slot(self, source, target):
        """Copy to an absent X4 slot and verify it before local load.

        The caller exclusively controls both directories and fences X4's save
        scan until the copy completes. A partial target can remain after a
        failed fenced fallback copy on Unix filesystems without hard links.
        Keep the scan fenced until any incomplete target has been handled.
        After publication, the complete slot can remain if the owner's
        verification fails. A separate native load observation is required.
        """
        with self._operation('downloaded'):
            pump = self._pump if self._keepalive_interval is not None else self._remaining
            staged = copy_offered_save(source, target, self._bound.offer, pump=pump)
            self._load.verify_staged(staged, pump=pump)
            self._state = 'staged'
            return staged

    def load_requested(self, attempt):
        with self._operation('staged'):
            self._load.observe_load_requested(attempt); self._state = 'requested'

    def loaded_marker(self, attempt, marker):
        with self._operation('requested'):
            self._load.observe_loaded_marker(attempt,marker); self._state = 'loaded'

    def paused(self, attempt, paused):
        with self._operation('loaded'):
            self._load.observe_pause(attempt,paused); self._state = 'paused'

    def claim(self, attempt):
        """Submit one claim and wait within the overall deadline.

        The server may synchronously verify the checkpoint before responding.
        If the response is lost or late, host admission remains indeterminate.
        """
        with self._operation('paused'):
            # Refuse an already queued response, EOF, or exceptional socket.
            readable, _, exceptional = select.select([self._conn], [], [self._conn], 0)
            if readable or exceptional: raise ConnectionError('unexpected input before claim')
            claim = self._load.make_claim(attempt, pump=self._pump if self._keepalive_interval is not None else None)
            payload = encode_load_claim(claim, ship_motion=self._ship_motion_requested)
            self._send(payload+b'\n')
            try: ack = json.loads(self._read_frame(use_overall_deadline=True).decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)
            except (UnicodeError,json.JSONDecodeError,RecursionError) as error: raise ValueError('invalid accepted frame') from error
            fields = {'version', 'kind', 'attempt_id'}
            if self._ship_motion_requested:
                fields.add('ship_motion')
            offer = self._bound.offer
            if (type(ack) is not dict or set(ack) != fields or
                    type(ack['version']) is not int or ack['version'] != 1 or ack['kind'] != 'accepted' or
                    ack['attempt_id'] != offer.attempt_id):
                raise ValueError('accepted frame differs from current attempt')
            credential = None
            if self._ship_motion_requested:
                credential = validate_ship_motion_credential(ack['ship_motion'],
                    index=self._chosen_ship_index, session_epoch=offer.world_epoch)
            self._state = 'accepted'
            return credential

    def take_connection(self):
        """Transfer exclusive socket ownership once after successful admission."""
        with self._operation('accepted'):
            conn = self._conn
        self._conn = None; self._state = 'transferred'
        return conn

    def close(self):
        if threading.get_ident() != self._thread or self._busy: raise RuntimeError('exclusive owner thread required')
        if self._conn is not None: self._conn.close(); self._conn = None
        if self._load is not None: self._load.close()
        self._state = 'closed'
