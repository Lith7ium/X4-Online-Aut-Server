#!/usr/bin/env python3
"""Disposable two-Linux-X4 admission/resume diagnostic; no gameplay.

TEST ASSUMPTION: native paused-load receipt, immutable hashed staged bytes and
matching fixture references are treated as an exact-load confirmation. They do
not prove all bytes consumed. This assumption MUST NOT be used for real clients.
The participant confirmations are native, not simulated. Import starts nothing.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools/dev')]
import authority_two_x4_identity_live as identity
import authority_two_x4_live as base
from lockstep.launcher import extension
from authority.local_participant_control import LocalParticipantControl


def local_udp_loader_bytes():
    """Expose the existing loader to scripts sharing the extension's UI state."""
    source = (ROOT/'src/authority/lua/local_udp_module.lua').read_bytes()
    if source.count(b'return M\n') != 1:
        raise ValueError('unexpected local UDP loader')
    return source.replace(b'return M\n', b'X4LAN_LOCAL_UDP_MODULE=M\nreturn M\n')


class Participant(LocalParticipantControl):
    """Single serialized loopback diagnostic, fenced on any uncertain receipt."""
    def __init__(self):
        super().__init__(enabled=True)
        self.config = self.config_identity = None

    def install(self, case, *, include_identity=True):
        lab = case/'extensions/x4_lan_lab'; ui = lab/'ui'
        udp_loader = ui/'local_udp_module.lua'
        udp_loader_data = local_udp_loader_bytes()
        if udp_loader.exists() or udp_loader.is_symlink():
            raise ValueError('local UDP loader already exists')
        for source, name in ((ROOT/'src/authority/lua/control_state.lua', 'authority_control_state.lua'),
                             (ROOT/'src/authority/lua/x4_control_engine.lua', 'x4_control_engine.lua'),
                             (ROOT/'tools/dev/authority_two_x4_participant_probe.lua', 'authority_two_x4_participant_probe.lua')):
            shutil.copyfile(source, ui/name)
        with udp_loader.open('xb') as output:
            output.write(udp_loader_data)
        server_lib = getattr(self, 'server_library', case.parent.parent/'server/case/extensions/x4_lan_lab/native/x4lan_net.so')
        shutil.copyfile(server_lib, lab/'native/x4lan_net.so')
        config = ui/'config.lua'; text = config.read_text(encoding='ascii')
        if not text.endswith('}\n'): raise ValueError('unexpected client config')
        st = config.lstat()
        if not stat.S_ISREG(st.st_mode): raise ValueError('client config is not a regular file')
        self.config, self.config_identity = config, (st.st_dev, st.st_ino)
        config.write_text(text[:-2]+f',authority_participant_port={self.sock.getsockname()[1]},'
                          f'authority_participant_lease="{self.lease}"'+'}\n', encoding='ascii')
        files = [*extension.BASE_UI, 'ui/client_load_barrier.lua', 'ui/x4_client_load_engine.lua',
                 *(['ui/authority_identity_probe.lua'] if include_identity else []), 'ui/authority_download_load_probe.lua',
                 'ui/authority_control_state.lua', 'ui/x4_control_engine.lua',
                 'ui/local_udp_module.lua',
                 'ui/authority_two_x4_participant_probe.lua']
        (lab/'ui.xml').write_text(extension.ui_xml(files), encoding='utf-8')

    def redact(self):
        if self.config is None: return
        fd = os.open(self.config, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != self.config_identity or st.st_size > 512 * 1024:
                raise ValueError('owned client config changed before redaction')
            text = os.read(fd, 512 * 1024 + 1).decode('ascii')
            original = f'authority_participant_lease="{self.lease}"'
            if text.count(original) != 1: raise ValueError('client lease missing or repeated')
            text = text.replace(original, 'authority_participant_lease="redacted"')
            if ',authority_company_motion=' in text:
                # Match the serializer's ordered options. Continuous playback
                # is emitted only for the automatic cockpit roster.
                text, count = re.subn(
                    r',authority_company_motion=\{port=[0-9]+,lease="[0-9a-f]{32}",'
                    r'generation="[0-9a-f]{32}",nonce="[0-9a-f]{32}",index=[0-9]+'
                    r'(?:,cockpit_health=true)?'
                    r'(?:,observed_npc_index=[0-9]+,observed_npc_code="[A-Z0-9]{3}-[A-Z0-9]{3}"'
                    r'(?:,registered_npc_hold_trial=true(?:,registered_npc_alignment_trial=true)?'
                    r'|,registered_npc_local_measurement=true)?'
                    r'|,playback_index=[0-9]+(?:,cockpit_radar_playback=true,cockpit_max_pilots=1)?'
                    r'|(?:,cockpit_radar_playback=true,cockpit_max_pilots=[1-3]'
                    r'(?:,cockpit_continuous_playback=true)?)?)'
                    r'\}(?=[,}])', '', text)
                if count != 1 or ',authority_company_motion=' in text:
                    raise ValueError('unexpected Company motion lease configuration')
            if ',authority_company_purchase=' in text:
                text, count = re.subn(
                    r',authority_company_purchase=\{epoch="[0-9a-f]{32}",index=[0-9]+,'
                    r'port=[0-9]+,lease="[0-9a-f]{32}"(?:,trade_view=true(?:,sales=true)?)?'
                    r'(?:,undock_nonce="[0-9a-f]{32}",undock_request="[0-9a-f]{32}")?'
                    r'\}(?=[,}])', '', text)
                if count != 1 or ',authority_company_purchase=' in text:
                    raise ValueError('unexpected Company purchase lease configuration')
            if ',authority_company_fleet=' in text:
                text, count = re.subn(
                    r',authority_company_fleet=\{epoch="[0-9a-f]{32}",port=[0-9]+,lease="[0-9a-f]{32}"'
                    r'(?:,version=2)?\}(?=[,}])', '', text)
                if count != 1 or ',authority_company_fleet=' in text:
                    raise ValueError('unexpected Company fleet order lease configuration')
            data = text.encode('ascii')
            os.lseek(fd, 0, os.SEEK_SET)
            written = 0
            while written < len(data):
                n = os.write(fd, data[written:])
                if n <= 0: raise OSError('short config write')
                written += n
            os.ftruncate(fd, len(data))
        finally:
            os.close(fd)

    def protected(self, case):
        lab = case/'extensions/x4_lan_lab'
        return [lab/'ui'/name for name in ('authority_control_state.lua', 'x4_control_engine.lua', 'local_udp_module.lua',
                                           'authority_two_x4_participant_probe.lua')] + [lab/'native/x4lan_net.so']

    def on_ready(self, *, world, handle, ticket, target, server_identity, server_proc, server_started,
                 client_identity, client_proc, client_started, game, out, deadline, result):
        # This callback is reached only after the two ordered ticket-bound load
        # receipts, a transfer size/hash check and protected-file checks.
        profile = Path('case/home/.config/EgoSoft/X4/45841/engine-output.log')
        identity.compare_observations(out/'server'/profile, out/'client'/profile)
        if base.live.sha(target) != ticket['sha256']: raise ValueError('staged bytes changed')
        def check_processes():
            if (base.client.game_identity(server_proc, server_started, game) != server_identity or
                    base.client.game_identity(client_proc, client_started, game) != client_identity):
                raise RuntimeError('owned X4 process changed')
        check_processes()
        self.handle = handle
        self.enroll(ticket['world_epoch'], deadline)
        self.request('pause')
        check_processes()
        # Explicit diagnostic assumption, not a proof of all bytes read.
        world.mark_loaded(ticket['world_epoch'], handle, ticket['baseline_id'], ticket['load_generation'],
                          ticket['checkpoint_id'], ticket['sha256'], trusted_confirmation=True)
        if world.session.pending is None or world.session.pending.operation != 'resume':
            raise RuntimeError('resume not scheduled')
        world.step()  # Native server and client both confirm advancing game time.
        check_processes()
        if world.session.state != 'running': raise RuntimeError('world did not resume')
        world.join('isolated-pause-probe')
        if world.session.pending is None or world.session.pending.operation != 'pause':
            raise RuntimeError('pause not scheduled')
        world.step()  # Both native controllers confirm a stable paused game clock.
        check_processes()
        result.update(client_admitted=True, native_server_and_client_resume_confirmed=True,
                      native_server_and_client_pause_confirmed=True,
                      load_confirmation='TEST ASSUMPTION: full X4 file consumption not proven',
                      server_state='paused')


def run(args):
    # Reject pre-existing or out-of-scope paths before registering cleanup.
    out = base.snapshot.output_path(args.out)
    participant = Participant()
    old_inject, old_run = identity.inject, base.run
    def inject(case, phase):
        old_inject(case, phase)
        if phase == 'load': participant.install(case)
    def run_base(arguments): return old_run(arguments, experiment=participant)
    result = None
    original_error = None
    try:
        identity.inject, base.run = inject, run_base
        result = identity.run(args)
    except BaseException as error:
        original_error = error
        raise
    finally:
        identity.inject, base.run = old_inject, old_run
        cleanup_errors = []
        for name, action in (('participant close', participant.close),
                             ('client lease redaction', lambda: base.redact_client_after_owned_stop(participant, result))):
            try: action()
            except BaseException as error: cleanup_errors.append(name + ' failed: ' + str(error))
        if cleanup_errors:
            detail = '; '.join(cleanup_errors)
            if result is not None:
                result.update(success=False, cleanup_error='; '.join(filter(None, (result.get('cleanup_error'), detail))))
            elif original_error is not None:
                original_error.add_note(detail)
            else:
                raise RuntimeError(detail)
    result['scope'] = ('two isolated Linux X4 processes; assumed exact load; native admission, '
                       'resume and pause; no gameplay, Windows or internet')
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game', required=True, type=Path); p.add_argument('--out', required=True, type=Path)
    p.add_argument('--display', required=True); p.add_argument('--timeout', type=float, default=600)
    p.add_argument('--seed', type=int, default=276944566)
    try: result = run(p.parse_args())
    except (OSError, ValueError, RuntimeError) as error: p.exit(1, 'REFUSED: '+str(error)+'\n')
    print(json.dumps(result, indent=2)); return 0 if result['success'] else 1

if __name__ == '__main__': sys.exit(main())
