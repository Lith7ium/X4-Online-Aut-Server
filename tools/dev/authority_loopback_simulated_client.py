#!/usr/bin/env python3
"""Exercise loopback save transfer with explicitly simulated X4 client events.

This is a test probe, not a player and not an X4 client. It does not launch X4.
It asserts load, marker, pause and participant controls without observing a
client game. It reads only a private endpoint from the development supervisor.
"""
import argparse
import json
import math
import os
from pathlib import Path
import socket
import stat
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/dev')]
import linux_snapshot_capture as snapshot
from authority.checkpoints import Checkpoint
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.load_messages import _pairs, _reject_constant
from authority.marker_evidence import verify_saved_marker
from authority_recovered_loopback_server import read_evidence


def endpoint(path):
    """Read an owner-only supervisor endpoint under .local, without logging token."""
    path=Path(path)
    local=(ROOT/'.local').resolve(strict=True)
    if (path.name != 'endpoint.json' or path.is_symlink() or not path.is_file() or
            not path.resolve(strict=True).is_relative_to(local)):
        raise ValueError('private local supervisor endpoint required')
    info=path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('endpoint must be owner-only')
    try:
        value=json.loads(read_evidence(path,4096).decode('ascii'),object_pairs_hook=_pairs,
                         parse_constant=_reject_constant)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as error:
        raise ValueError('invalid endpoint') from error
    required={'version','control_host','control_port','file_host','file_port','file_token','file_slot'}
    if (type(value) is not dict or set(value)!=required or type(value['version']) is not int or value['version']!=1 or
            value['control_host']!='127.0.0.1' or value['file_host']!='127.0.0.1' or
            type(value['control_port']) is not int or not 1<=value['control_port']<=65535 or
            type(value['file_port']) is not int or not 1<=value['file_port']<=65535 or
            type(value['file_token']) is not str or len(value['file_token'])!=32 or
            any(c not in '0123456789abcdef' for c in value['file_token']) or value['file_slot']!=1 or
            type(value['file_slot']) is not int):
        raise ValueError('invalid local endpoint')
    return value


class SimulatedController:
    """Every receipt here is synthetic; no X4 state is read or changed."""
    def __init__(self): self.calls=[]
    def pause(self, handles): self.calls.append(('pause',handles)); return True
    def resume(self, handles): self.calls.append(('resume',handles)); return True


def attempt(config, directory, deadline, *, hold_seconds=.5):
    """Download real save bytes, then send labeled synthetic X4 observations."""
    conn=socket.create_connection((config['control_host'],config['control_port']),
                                  timeout=min(5,max(.01,deadline-time.monotonic())))
    client=participant=None
    try:
        client=ExperimentalRemoteLoadClient(conn,enabled=True,file_host=config['file_host'],
            file_port=config['file_port'],file_token=config['file_token'],file_slot=1,
            deadline=deadline,io_timeout=120,keepalive_interval=30)
        bound=client.request_offer(); offer=bound.offer
        downloaded=client.download(directory)
        verify_saved_marker(Checkpoint(offer.checkpoint_id,downloaded,offer.sha256,offer.size,bound.marker),bound.marker,
                            script_name='X4AuthorityCheckpointMarker',cue_name='MarkerState')
        client.verify_staged(downloaded)
        # These are deliberately fabricated, never X4 observations.
        attempt_id=offer.attempt_id
        client.load_requested(attempt_id); client.loaded_marker(attempt_id,bound.marker)
        client.paused(attempt_id,True); client.claim(attempt_id)
        conn=client.take_connection(); controller=SimulatedController()
        participant=ExperimentalTCPParticipantClient(conn,controller,enabled=True,
            epoch=offer.world_epoch,handle=offer.connection_handle,timeout=120)
        while time.monotonic()<deadline:
            sent=participant.poll(min(.1,max(0,deadline-time.monotonic())))
            if sent and controller.calls and controller.calls[-1][0]=='resume': break
        else: raise TimeoutError('simulated participant resume timed out')
        # Give the host time to validate its receipt before deliberately
        # disconnecting; EOF concurrent with its validation must fail closed.
        hold_started = time.monotonic()
        until = hold_started + hold_seconds
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('participant hold timed out')
            if now >= until:
                break
            participant.poll(min(.1, until - now, deadline - now))
        return {'checkpoint_id':offer.checkpoint_id,'sha256':offer.sha256,'size':offer.size,
                'marker':bound.marker,'session_epoch':offer.world_epoch,
                'transfer_verified':True,'client_x4_load_simulated':True,
                'participant_controls_simulated':True, 'hold_seconds': now - hold_started}
    finally:
        if participant is not None:
            try: participant.close()
            except RuntimeError: pass
        if client is not None:
            try: client.close()
            except RuntimeError: pass
        else: conn.close()


def run(config, out, *, count, timeout, hold_seconds=.5):
    if type(count) is not int or count not in (1,2) or not math.isfinite(timeout) or not 0<timeout<=1800:
        raise ValueError('invalid probe count or timeout')
    if type(hold_seconds) not in (float, int) or not math.isfinite(hold_seconds) or not .5 <= hold_seconds <= 600:
        raise ValueError('hold seconds must be between 0.5 and 600')
    deadline=time.monotonic()+timeout
    rows=[]
    for number in range(1,count+1):
        row=attempt(config,Path(out)/('download-'+str(number)),deadline,hold_seconds=hold_seconds)
        if rows and (row['checkpoint_id']==rows[-1]['checkpoint_id'] or row['marker']==rows[-1]['marker']):
            raise ValueError('later offer did not use a newly marked checkpoint')
        rows.append(row)
    return {'success':True,'scope':'real loopback save transfer; all client X4 events simulated',
            'client_x4_load_simulated':True,'gameplay_tested':False,'windows_tested':False,
            'internet_tested':False,'attempts':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint',type=Path,required=True); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--count',type=int,choices=(1,2),default=2); parser.add_argument('--timeout',type=float,default=600)
    parser.add_argument('--hold-seconds', type=float, default=.5,
                        help='remain connected after server resume so its simulation can be measured')
    args=parser.parse_args()
    try:
        config=endpoint(args.endpoint); out=snapshot.output_path(args.out); out.mkdir(mode=0o700,parents=True)
    except (OSError,ValueError) as error: parser.exit(1,'REFUSED: '+str(error)+'\n')
    try: result=run(config,out,count=args.count,timeout=args.timeout,hold_seconds=args.hold_seconds)
    except (Exception,KeyboardInterrupt) as error:
        result={'success':False,'client_x4_load_simulated':True,'error':str(error) or type(error).__name__}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2)); return 0 if result['success'] else 1

if __name__=='__main__': sys.exit(main())
