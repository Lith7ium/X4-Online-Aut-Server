"""Per-peer latest-motion I/O for the experimental control poll owner.

This is not a transport, lifecycle replication, or an X4 source. One owner
thread must poll the control channel and explicitly fence lifecycle operations.
Only an unsent sample may be replaced; a started frame and its receipt remain
in flight until completion or the peer is disconnected.
"""
import json
import time

from .load_messages import MAX_MESSAGE, _pairs, _reject_constant


class LatestMotionPoll:
    def __init__(self, control, *, limit, timeout):
        self.control = control
        self.limit = limit
        self.timeout = timeout
        self.blocked = False
        self.world_blocked = False
        self.generation = 0

    def busy(self):
        return any(p.get('motion_active') or p.get('motion_pending')
                   for p in self.control._peers.values())

    def enqueue(self, entries):
        if self.blocked or self.world_blocked:
            raise RuntimeError('motion is fenced for a lifecycle operation')
        # Validate the whole selection before mutating anything. A full queue
        # indicates one peer cannot keep up with distinct tracks. Closing only
        # that socket keeps other peers moving; this is a provisional fail-safe,
        # not an interest, resynchronization, or reconnection policy.
        overflow=[]
        for conn, peer, key, data, expected, accepted, discarded, guard in entries:
            if self.control._peers.get(conn) is not peer or peer['stage'] != 'admitted':
                raise RuntimeError('motion peer changed')
            pending = peer.setdefault('motion_pending', {})
            active = peer.get('motion_active')
            replaces_active = active is not None and active['key'] == key and active['sent'] == 0
            if not replaces_active and key not in pending and len(pending) >= self.limit:
                overflow.append(conn)
        for conn in overflow:self.control._drop(conn)
        survivors=[]
        try:
            for conn, peer, key, data, expected, accepted, discarded, guard in entries:
                if conn in overflow:continue
                active = peer.get('motion_active')
                if active is not None and active['key'] == key and active['sent'] == 0:
                    # Keep the original deadline so replacement cannot defer
                    # the timeout. Nothing has been sent on this frame yet.
                    active['discarded']()
                    obsolete_pending = peer['motion_pending'].pop(key, None)
                    if obsolete_pending is not None:
                        obsolete_pending[3]()
                    active.update(data=data, expected=expected, accepted=accepted,
                                  discarded=discarded, guard=guard)
                else:
                    obsolete_pending = peer['motion_pending'].get(key)
                    if obsolete_pending is not None:
                        obsolete_pending[3]()
                    # In-place replacement preserves FIFO order across tracks.
                    peer['motion_pending'][key] = (data, expected, accepted, discarded, guard)
                survivors.append(conn)
        except BaseException as error:
            # Track sequence is committed only after enqueue returns. A partial
            # enqueue must never be sent under the old sequence state. Fence the
            # whole control owner: callback effects may also be uncertain.
            self.control._fatal(error)
        return frozenset(survivors)

    def barrier(self, *, world=False):
        if world:
            if not self.world_blocked:self.generation += 1
            self.world_blocked = True
        else:
            if not self.blocked:self.generation += 1
            self.blocked = True
        try:
            for peer in self.control._peers.values():
                active = peer.get('motion_active')
                if active is not None and active['sent'] == 0:
                    peer['motion_active'] = None
                    active['discarded']()
                pending = peer.get('motion_pending', {})
                for _, _, _, discarded, _ in pending.values(): discarded()
                pending.clear()
        except BaseException as error:
            self.control._fatal(error)
        return not self.busy()

    def release(self, *, world=False):
        if self.busy(): raise RuntimeError('motion barrier has not drained')
        if world: self.world_blocked = False
        else: self.blocked = False

    def prepare(self, peer):
        if self.blocked or self.world_blocked or peer.get('motion_active') or peer['out']:
            return
        pending = peer.get('motion_pending', {})
        if pending:
            key = next(iter(pending))
            data, expected, accepted, discarded, guard = pending.pop(key)
            peer['motion_active'] = {'key': key, 'data': data, 'sent': 0, 'input': b'',
                                     'expected': expected, 'accepted': accepted, 'discarded': discarded, 'guard': guard,
                                     'deadline': time.monotonic() + self.timeout}

    def send(self, conn, peer):
        active = peer['motion_active']
        if active['guard'] is not None: active['guard']()
        try: count = conn.send(active['data'][active['sent']:])
        except BlockingIOError: return
        except OSError:
            self.control._drop(conn, transport_lost=True)
            return
        if count <= 0:
            self.control._drop(conn, transport_lost=True)
            return
        active['sent'] += count

    def receive(self, conn, peer, chunk):
        active = peer.get('motion_active')
        if active is None or active['sent'] != len(active['data']):
            self.control._drop(conn); return
        if active['guard'] is not None: active['guard']()
        active['input'] += chunk
        if b'\n' not in active['input']:
            if len(active['input']) > MAX_MESSAGE: self.control._drop(conn)
            return
        raw, tail = active['input'].split(b'\n', 1)
        if tail or len(raw) > MAX_MESSAGE:
            self.control._drop(conn); return
        try:
            reply = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                               parse_constant=_reject_constant)
            expected = active['expected']
            if (type(reply) is not dict or set(reply) != set(expected) or
                    any(type(reply.get(k)) is not type(v) or reply[k] != v for k, v in expected.items())):
                raise ValueError('invalid motion receipt')
            active['accepted']()
        except (ValueError, UnicodeError, RecursionError):
            self.control._drop(conn); return
        peer['motion_active'] = None
