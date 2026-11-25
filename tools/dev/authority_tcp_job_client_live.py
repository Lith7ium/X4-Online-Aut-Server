#!/usr/bin/env python3
"""One X4 participant TCP-to-job-IPC attempt; never starts an X4 instance.

The caller supplies owned UDP sockets and a private control socket. This is an
internal Linux experiment: server engine, checkpoint and admission claim are
synthetic; only the participant-side X4 effect is observed through local IPC.
"""
import gzip
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/dev')]
from authority.checkpoints import CheckpointStore
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery, job_event
from authority.experimental_tcp_participants import ExperimentalTCPParticipants
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.experimental_participant_job_ipc import ExperimentalParticipantJobIPC
from authority.experimental_client_load import HonestClientLoadClaim
from authority.load_messages import Receipt
from authority.local_participant_control import LocalParticipantControl
from authority.save_transfer import SaveOffers
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from lockstep.launcher.joinfiles import FileService
from authority_tcp_native_worker import check_process, parse_json, read_line, send_frame

_TOKEN=re.compile(r'[0-9a-f]{32}\Z')
_MARKER='1234567890abcdef1234567890abcdef'
_XML=('<savegame><script><refs type="string"><ref id="42" string="'+_MARKER+
      '"/></refs></script><md><script name="X4AuthorityCheckpointMarker">'
      '<cue name="MarkerState"><vars><value name="$marker" type="string" value="42"/>'
      '<value name="$loaded" type="integer"/></vars></cue></script></md></savegame>')


def _proc_ipv4_endpoint(address):
    value = int.from_bytes(socket.inet_aton(address[0]), byteorder=sys.byteorder)
    return f'{value:08X}:{address[1]:04X}'


def _udp_table(path, address_length):
    lines = path.read_text().splitlines()
    if not lines or 'local_address' not in lines[0]:
        raise RuntimeError('UDP socket table unavailable')
    result = []
    for line in lines[1:]:
        row = line.split()
        if len(row) <= 9:
            raise RuntimeError('invalid UDP socket table row')
        local = row[1].split(':')
        if (len(local) != 2 or len(local[0]) != address_length or len(local[1]) != 4 or
                not all(char in '0123456789ABCDEF' for char in local[0] + local[1]) or
                not row[9].isdigit()):
            raise RuntimeError('invalid UDP socket table endpoint')
        result.append(row)
    return result


def socket_owner(peer, destination, process, *, diagnostic=None):
    """Sample the connected IPv4 peer and reject competing local sources.

    Reject any other IPv4 or IPv6 UDP socket with the same source port in the
    peer's network namespace, regardless of its bind address or remote. This is
    conservative: harmless sockets on other interfaces or IPv6-only sockets
    can also cause rejection. A socket with a different bind address can still
    select a loopback source using IP_PKTINFO. These are sampled checks; the
    caller must fence changes between samples and own the receiving socket.
    """
    if diagnostic is not None and not callable(diagnostic):
        raise ValueError('diagnostic callback must be callable')
    def reject(reason):
        if diagnostic is not None:
            diagnostic(reason)
        return False
    check_process(process)
    if peer[0] != '127.0.0.1' or destination[0] != '127.0.0.1':
        return reject('non-loopback endpoint')
    proc = Path('/proc') / str(process['pid'])
    rows = _udp_table(proc / 'net/udp', 8)
    rows6 = _udp_table(proc / 'net/udp6', 32)
    peer_endpoint = _proc_ipv4_endpoint(peer)
    destination_endpoint = _proc_ipv4_endpoint(destination)
    source_port = f'{peer[1]:04X}'

    def matching_rows(table):
        return [row for row in table if row[1:3] == [peer_endpoint, destination_endpoint]]

    def row_identity(row):
        # Include state, UID and the kernel pointer column when supplied.
        # Queue counters, reference counts and drops are live measurements.
        return row[1:4], row[7], row[9], row[11:12]

    def conflicts(ipv4, ipv6, expected):
        inode = expected[9]
        identity = row_identity(expected)
        for row in ipv4:
            if row[9] == inode and row_identity(row) != identity:
                return 'conflicting IPv4 rows for socket inode'
            if row[9] != inode and row[1].rsplit(':', 1)[1] == source_port:
                return 'competing IPv4 source port'
        if any(row[1].rsplit(':', 1)[1] == source_port for row in ipv6):
            return 'competing IPv6 source port'
        if any(row[9] == inode for row in ipv6):
            return 'socket inode appeared in both UDP tables'

    peer_rows = matching_rows(rows)
    if not peer_rows:
        return reject('connected IPv4 flow count differs from one (matches=0)')
    inodes = {row[9] for row in peer_rows}
    if len(inodes) != 1:
        return reject('connected IPv4 flow count differs from one '
                      f'(matches={len(peer_rows)}, distinct_inodes={len(inodes)})')
    expected = peer_rows[0]
    inode = expected[9]
    if inode == '0':
        return reject('connected IPv4 flow has no socket inode')
    reason = conflicts(rows, rows6, expected)
    if reason:
        return reject(reason)
    if len(peer_rows) > 1:
        # An inode is not a permanent socket ID. Re-read both namespace tables
        # once and require a single unambiguous entry for the same candidate.
        check_process(process)
        fresh_rows = _udp_table(proc / 'net/udp', 8)
        fresh_rows6 = _udp_table(proc / 'net/udp6', 32)
        fresh_matches = matching_rows(fresh_rows)
        if len(fresh_matches) != 1:
            return reject('connected IPv4 flow remained ambiguous on repeat sample '
                          f'(matches={len(fresh_matches)}, distinct_inodes={len({row[9] for row in fresh_matches})})')
        if row_identity(fresh_matches[0]) != row_identity(expected):
            return reject('connected IPv4 flow identity changed on repeat sample')
        reason = conflicts(fresh_rows, fresh_rows6, expected)
        if reason:
            return reject(reason)
    wanted = 'socket:[' + inode + ']'
    found = []
    for fd in (proc / 'fd').iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target == wanted:
            found.append(fd.name)
    check_process(process)
    if len(found) != 1:
        return reject('process socket descriptor count differs from one')
    return True


def config(raw):
    c=parse_json(raw)
    keys={'version','epoch','lease','attempt','generation','participant_lease','client_process','deadline','event'}
    if type(c) is not dict or set(c)!=keys or type(c['version']) is not int or c['version']!=1:
        raise ValueError('invalid supervisor request')
    if any(type(c[k]) is not str or not _TOKEN.fullmatch(c[k]) for k in ('epoch','lease','attempt','generation','participant_lease')):
        raise ValueError('invalid context tokens')
    if (type(c['deadline']) not in (int,float) or not math.isfinite(c['deadline']) or
        not 10 < c['deadline']-time.monotonic() <= 600): raise ValueError('invalid deadline')
    e=job_event(c['event'])
    if (e['generation']!=c['generation'] or e['sequence']!=1 or
        len({c['epoch'],c['participant_lease'],c['lease'],c['attempt'],e['epoch'],c['generation']})!=6):
        raise ValueError('invalid job lineage')
    check_process(c['client_process'])
    return c,e


def validate_udp(s):
    if (type(s) is not socket.socket or s.family!=socket.AF_INET or
        s.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_DGRAM or
        s.getsockname()[0]!='127.0.0.1' or not s.getsockname()[1]): raise ValueError('bound loopback UDP required')
    try: s.getpeername()
    except OSError: return
    raise ValueError('expected unconnected UDP')


class SyntheticEngine:
    pending_marker=_MARKER
    def __init__(self,root): self.path=root/'synthetic-save.gz'
    def pause(self): pass
    def resume(self): pass
    def save(self): self.path.write_bytes(gzip.compress(_XML.encode()));return self.path


def run(participant_udp,job_udp,control,c,event,*,delivery_action=None,extra_guard=None,expected_jobs=1,motion_udp=None,allow_motion_apply=False,authority_udp=None,authority_grant=None):
    if expected_jobs is not None and (type(expected_jobs) is not int or not 1<=expected_jobs<=8):
        raise ValueError('invalid bounded job count')
    if delivery_action is not None and not callable(delivery_action):raise ValueError('invalid delivery action')
    if extra_guard is not None and not callable(extra_guard):raise ValueError('invalid extra guard')
    if type(allow_motion_apply) is not bool or (allow_motion_apply and motion_udp is None):raise ValueError('invalid one-shot apply mode')
    if (authority_udp is None)!=(authority_grant is None) or (authority_udp is not None and (motion_udp is not None or expected_jobs is not None or delivery_action is None)):
        raise ValueError('invalid authority diagnostic mode')
    if authority_grant is not None:
        from authority.experimental_tcp_client_authority_motion import grant_value
        authority_grant=grant_value(authority_grant)
        if (authority_grant['session_epoch']!=c['epoch'] or authority_grant['epoch']!=c['job_epoch'] or
            authority_grant['generation']!=c['generation']):raise ValueError('authority context differs')
    job_epoch=event['epoch'] if event is not None else c['job_epoch']
    validate_udp(participant_udp);validate_udp(job_udp)
    sockets=[participant_udp,job_udp]
    if motion_udp is not None:validate_udp(motion_udp);sockets.append(motion_udp)
    if authority_udp is not None:validate_udp(authority_udp);sockets.append(authority_udp)
    if len({s.getsockname() for s in sockets})!=len(sockets): raise ValueError('distinct UDP sockets required')
    if control.family!=socket.AF_UNIX or control.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_STREAM:
        raise ValueError('private control stream required')
    deadline=c['deadline']; process=c['client_process'];failure=[];stop=threading.Event();thread=None
    def guard():
        if time.monotonic()>=deadline: raise TimeoutError('worker deadline')
        check_process(process)
        if extra_guard is not None and extra_guard() is not True:raise RuntimeError('additional process guard failed')
        readable,_,bad=select.select([control],[],[control],0)
        if readable or bad: raise RuntimeError('parent control changed')
        return True
    def peer_guard(peer,sock,*,diagnostic=None):
        guard()
        if diagnostic is None:
            result=socket_owner(peer,sock.getsockname(),process)
        else:
            result=socket_owner(peer,sock.getsockname(),process,diagnostic=diagnostic)
        guard()
        return result
    guard()
    participant_udp.settimeout(.1);participant_peer=None
    expected=('HELLO|'+c['participant_lease']+'|'+c['epoch']).encode()
    while participant_peer is None:
        guard()
        try:data,peer=participant_udp.recvfrom(769)
        except socket.timeout:continue
        if data!=expected:
            raise RuntimeError('unexpected participant hello')
        diagnostics=[]
        if not peer_guard(peer,participant_udp,diagnostic=diagnostics.append):
            reason=diagnostics[0] if len(diagnostics)==1 else 'reason unavailable'
            raise RuntimeError('participant peer not owned by X4: '+reason)
        participant_peer=peer
    with tempfile.TemporaryDirectory(prefix='x4-job-client-') as tmp:
        root=Path(tmp);(root/'store').mkdir()
        service=FileService(c['epoch'],bind='127.0.0.1',port=0,slots=(1,)).start()
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        server=client=control_host=None
        try:
            store=CheckpointStore(root/'store')
            world=WorldCoordinator(c['epoch'],SyntheticEngine(root),store,SaveOffers(service,store))
            session=TrustedMarkerLoadSession(world,enabled=True)
            control_host=ExperimentalTCPControl(session,enabled=True,idle_timeout=120)
            host=ExperimentalLocalHost(world,control_host,service,enabled=True)
            participants=ExperimentalTCPParticipants(host,enabled=True,timeout=min(30,deadline-time.monotonic()-2))
            world._participants=participants
            host.tick();listener.bind(('127.0.0.1',0));listener.listen(1)
            client=socket.create_connection(listener.getsockname(),timeout=2)
            server,_=listener.accept();handle=host.adopt(server)
            host.tick();client.sendall(offer_request());control_host.poll(.1);control_host.poll(.1)
            bound=decode_bound_offer(read_line(client,8192,deadline,guard))
            receipt=Receipt(**{key:getattr(bound.offer,key) for key in Receipt.__dataclass_fields__})
            # Explicitly synthetic admission: this test does not claim that X4
            # loaded the checkpoint offered by this synthetic coordinator.
            claim=HonestClientLoadClaim(receipt,bound.marker,True,True)
            client.sendall(encode_load_claim(claim)+b'\n');control_host.poll(.1);control_host.poll(.1)
            ack=parse_json(read_line(client,4096,deadline,guard))
            if ack!={'version':1,'kind':'accepted','attempt_id':bound.offer.attempt_id}:
                raise RuntimeError('synthetic admission not accepted')
            def participant():
                bridge=None;adapter=None;motion_bridge=None;authority_bridge=None;resumed=False
                try:
                    ctl=LocalParticipantControl.__new__(LocalParticipantControl)
                    ctl.sock=participant_udp;ctl.lease=c['participant_lease'];ctl.peer=participant_peer
                    ctl.epoch=c['epoch'];ctl.handle=handle;ctl.next_id=1;ctl.fault=False;ctl.timeout=min(20,deadline-time.monotonic()-2)
                    class GuardedControl:
                        def pause(self,handles): raise RuntimeError('unexpected pause in one-shot experiment')
                        def resume(self,handles):
                            nonlocal resumed
                            if not peer_guard(participant_peer,participant_udp): raise RuntimeError('control peer changed')
                            r=ctl.resume(handles)
                            if not peer_guard(participant_peer,participant_udp): raise RuntimeError('control peer changed')
                            if r is not True:raise RuntimeError('resume not confirmed')
                            resumed=True
                            return r
                    bridge=ExperimentalParticipantJobIPC(job_udp,enabled=True,lease=c['lease'],attempt=c['attempt'],
                        epoch=job_epoch,generation=c['generation'],guard=guard,
                        validate_peer=lambda p:peer_guard(p,job_udp),timeout=min(20,deadline-time.monotonic()-2))
                    if motion_udp is not None:
                        from authority.experimental_participant_motion_ipc import ExperimentalParticipantMotionIPC
                        motion_bridge=ExperimentalParticipantMotionIPC(motion_udp,enabled=True,
                            lease=c['client_motion_lease'],attempt=c['client_motion_attempt'],epoch=job_epoch,
                            generation=c['generation'],guard=guard,validate_peer=lambda p:peer_guard(p,motion_udp),
                            timeout=min(20,deadline-time.monotonic()-5),allow_apply=allow_motion_apply)
                    adapter=ExperimentalTCPParticipantClient(client,GuardedControl(),enabled=True,epoch=c['epoch'],handle=handle,
                                                              timeout=min(25,deadline-time.monotonic()-1),apply_job=bridge,
                                                              apply_motion=motion_bridge,
                                                              apply_motion_once=motion_bridge.apply_once if allow_motion_apply else None,
                                                              preflight_motion=motion_bridge.preflight if allow_motion_apply else None,
                                                              authority_grant=authority_grant,
                                                              authority_guard=(lambda v: guard() and v==authority_grant) if authority_grant is not None else None,
                                                              pull_authority_motion=(lambda: authority_bridge.pull()) if authority_grant is not None else None)
                    # Resume is the first command. READY arrives only after X4 resumed.
                    while not adapter.poll(.1): guard()
                    bridge.wait_ready()
                    if motion_bridge is not None:motion_bridge.wait_ready()
                    if authority_grant is not None:
                        from authority_client_authority_bind import bind_client_authority
                        def grant(e,g,i,t,inc,session):
                            return (guard() and (e,g,i,t,inc,session)==(authority_grant['epoch'],authority_grant['generation'],
                                    authority_grant['index'],authority_grant['token'],authority_grant['incarnation'],authority_grant['session_epoch']))
                        authority_bridge=bind_client_authority(authority_udp,process=process,lease=c['authority_lease'],
                            attempt=c['authority_attempt'],epoch=job_epoch,generation=c['generation'],
                            incarnation=authority_grant['incarnation'],token=authority_grant['token'],session_epoch=c['epoch'],
                            guard=guard,running=lambda: resumed and guard(),grant=grant,timeout=min(20,deadline-time.monotonic()-5),
                            include_orientation=c.get('include_orientation',False))
                        authority_bridge.register_saved()
                    if expected_jobs is None:
                        while not stop.is_set():adapter.poll(.1);guard()
                    else:
                        for _ in range(expected_jobs):
                            while not adapter.poll(.1): guard()
                        while not stop.wait(.05): guard()
                except BaseException as error:failure.append(error);stop.set()
                finally:
                    if adapter is not None:
                        try:adapter.close()
                        except BaseException as error:failure.append(error)
                    if bridge is not None:
                        try:bridge.close()
                        except BaseException as error:failure.append(error)
                    if motion_bridge is not None:
                        try:motion_bridge.close()
                        except BaseException as error:failure.append(error)
                    if authority_bridge is not None:
                        try:authority_bridge.close()
                        except BaseException as error:failure.append(error)
            thread=threading.Thread(target=participant,daemon=True);thread.start()
            guard();host.tick(.1);guard()
            if world.session.state!='running' or failure: raise RuntimeError('participant resume failed')
            delivery=ExperimentalTCPJobDelivery(host,enabled=True,timeout=min(28,deadline-time.monotonic()-1))
            if authority_grant is not None:delivery.bind_test_incarnation(handle,authority_grant['incarnation'],enabled=True)
            members=delivery.membership();seen=[]
            if len(members)!=1: raise RuntimeError('unexpected admitted set')
            def ack(peer,received):
                guard()
                if peer!=members[0] or received!=event or seen:return False
                seen.append(peer);return True
            if delivery_action is None:
                if delivery.deliver(event,members,ack) is not True or seen!=list(members):
                    raise RuntimeError('TCP job receipt missing')
                result={'event':'result','ok':True,'tcp_admitted':True,'tcp_job_receipt':True,
                        'x4_md_ffi_asserted_by_owner':True,'synthetic_server_and_checkpoint':True,
                        'checkpoint_offered_to_x4':False,'event_sequence':event['sequence'],'index':event['index']}
            else:
                def action_guard():
                    # An action may have irreversible side effects after its
                    # last TCP request. Fence a failed participant reader
                    # both before and after checking process/control state.
                    if failure or stop.is_set() or not thread.is_alive():
                        raise RuntimeError('participant reader unavailable')
                    guard()
                    if failure or stop.is_set() or not thread.is_alive():
                        raise RuntimeError('participant reader unavailable')
                    return True
                result=delivery_action(delivery,members,action_guard)
                if type(result) is not dict:raise RuntimeError('invalid source delivery result')
            guard()
            if failure:raise RuntimeError('participant thread failed')
            stop.set();thread.join(3)
            if thread.is_alive() or failure:raise RuntimeError('participant cleanup failed')
            return result
        finally:
            errors=[];stop.set()
            if client is not None:
                try:client.close()
                except OSError as error:errors.append(error)
            if thread is not None:
                thread.join(3)
                if thread.is_alive():errors.append(RuntimeError('participant thread still active'))
            if control_host is not None and not (control_host._closed or control_host._failed):
                try:control_host.close()
                except BaseException as error:errors.append(error)
            if server is not None:
                try:server.close()
                except OSError as error:errors.append(error)
            try:listener.close()
            except OSError as error:errors.append(error)
            try:
                service.close();service.thread.join(3)
                if service.thread.is_alive():errors.append(RuntimeError('file service still active'))
            except BaseException as error:errors.append(error)
            if errors:raise RuntimeError('worker cleanup unconfirmed') from errors[0]


def main():
    if len(sys.argv)!=4: return 2
    try:
        participant_udp,job_udp,control=(socket.socket(fileno=int(x)) for x in sys.argv[1:])
        c,event=config(read_line(control,16384,time.monotonic()+30))
        result=run(participant_udp,job_udp,control,c,event)
        send_frame(control,json.dumps(result,separators=(',',':')).encode(),c['deadline'],lambda:check_process(c['client_process']))
        return 0
    except BaseException:
        try:control.sendall(b'{"event":"result","ok":false}\n')
        except BaseException:pass
        return 1

if __name__=='__main__': sys.exit(main())
