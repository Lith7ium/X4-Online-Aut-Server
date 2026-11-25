"""Add an opt-in client motion probe to an owned private recovery overlay.

The saved_playback_fixture option enables bounded-evidence sustained playback;
the default and separately enabled saved_apply fixture behavior are unchanged.
The saved_replica_control_fixture option adds the dedicated local control owner
only alongside matching saved playback. The saved_initial_alignment_fixture
option prepares that controlled fixture on the first binding, then aligns once
to a later fresh sample with velocity before starting playback. Waiting for that
sample uses the configured timeout; running corrections retain their own limit.

No game, helper, listener or socket is started. The caller owns the fourth UDP
FD handoff, all process/checkpoint/STOP fences, final log checks and redaction.
The recovered-server supervisor uses this only in its opt-in private diagnostic;
it is not a gameplay or cross-platform client.
"""
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
import authority_load_live as load
import authority_two_x4_resume_live as participant
from authority.client_motion_overlay import (
    HEX, MD, MODULES, PLAYER_SECTOR_MD, PLAYER_SECTOR_MODULE,
    RECOVERY_APPLY_EPOCH, RECOVERY_APPLY_FIXTURE, RECOVERY_APPLY_MD,
    RECOVERY_APPLY_MODULE, RECOVERY_PLAYBACK_MODULE, RECOVERY_REPLICA_CONTROL_MODULE,
    RECOVERY_REPLICA_CONTROL_MD, SECRET_FIELDS,
    append_load_ready_hook, render_motion_config, select_motion_sources,
    transform_motion_control, validate_motion_settings,
)

ROOT=Path(__file__).resolve().parents[2]


def read_ticket_token(config, field, *, prefix=''):
    """Read one canonical plain or decimal-escaped Lua token with optional prefix."""
    assignment_pattern = r'(?<![A-Za-z0-9_])' + re.escape(field) + r'\s*='
    value_pattern = assignment_pattern + r'\s*"([^"\n]*)"(?=\s*[,}])'
    values = re.findall(value_pattern, config)
    if len(re.findall(assignment_pattern, config)) != 1 or len(values) != 1:
        raise ValueError('client ticket token missing or ambiguous')
    encoded_token = values[0]
    if re.fullmatch(r'(?:\\[0-9]{3}){' + str(len(prefix)+32) + r'}', encoded_token):
        byte_values = [int(encoded_token[index + 1:index + 4])
                       for index in range(0, len(encoded_token), 4)]
        if any(value > 255 for value in byte_values):
            raise ValueError('invalid client ticket token encoding')
        try:
            decoded_token = bytes(byte_values).decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('invalid client ticket token encoding') from None
    else:
        decoded_token = encoded_token
    if not decoded_token.startswith(prefix) or not HEX.fullmatch(decoded_token[len(prefix):]):
        raise ValueError('invalid client ticket token encoding')
    return decoded_token[len(prefix):]


class Installation:
    """Protected file set and post-cleanup config redaction; caller fences X4."""
    def __init__(self,paths,config,settings):
        self.paths=tuple(paths);self.config=config;self.secrets={k:settings[k] for k in SECRET_FIELDS}
        if 'player_sector' in settings:
            for name in ('incarnation','nonce'):
                self.secrets['player_sector_'+name]=settings['player_sector'][name]
        st=config.lstat();self.identity=(st.st_dev,st.st_ino)
    def redact(self):
        # Call only after owned X4 has stopped and final input evidence passed.
        fd=os.open(self.config,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (st.st_dev,st.st_ino)!=self.identity or st.st_size>65536:
                raise ValueError('client motion config changed before redaction')
            chunks=[];remaining=st.st_size
            while remaining:
                chunk=os.read(fd,remaining)
                if not chunk:raise ValueError('incomplete client motion config read')
                chunks.append(chunk);remaining-=len(chunk)
            after=os.fstat(fd);path_st=self.config.lstat()
            if ((after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=
                (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns) or
                (path_st.st_dev,path_st.st_ino)!=(st.st_dev,st.st_ino) or not stat.S_ISREG(path_st.st_mode)):
                raise ValueError('client motion config changed during redaction')
            text=b''.join(chunks).decode('ascii')
            for k,v in self.secrets.items():
                original='authority_client_motion_'+k+'="'+v+'"'
                if text.count(original)!=1:raise ValueError('client motion secret missing or repeated')
                text=text.replace(original,'authority_client_motion_'+k+'="redacted"')
            data=text.encode('ascii');os.lseek(fd,0,os.SEEK_SET);written=0
            while written<len(data):
                n=os.write(fd,data[written:])
                if n<=0:raise OSError('short config redaction')
                written+=n
            os.ftruncate(fd,len(data))
        finally:os.close(fd)


def install(case,profile,status,settings):
    """Extend existing disposable client after load/control/registry installation."""
    selection=validate_motion_settings(status,settings)
    status=selection.status
    settings=selection.settings
    saved_apply_fixture=settings.get('saved_apply_fixture')
    saved_playback_fixture=settings.get('saved_playback_fixture')
    player_sector=settings.get('player_sector')
    tokens=[settings[k] for k in SECRET_FIELDS]
    case,profile=Path(case),Path(profile)
    if (case.is_symlink() or profile.is_symlink() or not profile.resolve().is_relative_to(case.resolve()) or
        profile.name!='45841' or (profile/'save').is_symlink() or not (profile/'save').is_dir()):
        raise ValueError('private 45841 profile required')
    lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
    if any(p.is_symlink() or not p.is_dir() for p in (lab,ui,md)):
        raise ValueError('invalid client extension')
    config,manifest=ui/'config.lua',lab/'ui.xml'
    control,loader=ui/'authority_two_x4_participant_probe.lua',ui/'authority_download_load_probe.lua'
    udp_loader=ui/'local_udp_module.lua'
    required=(config,manifest,control,loader,udp_loader,md/'authority_ship_registry.xml',ui/'recovery_registry_status.lua')
    if any(p.is_symlink() or not p.is_file() for p in required):raise ValueError('client load/control/registry owner unavailable')
    cfg=config.read_text(encoding='ascii');mf=manifest.read_text(encoding='utf-8')
    ctl=control.read_text(encoding='utf-8');load_text=loader.read_text(encoding='utf-8')
    try:
        manifest_files=[element.get('name') for element in ET.fromstring(mf).iter('file')]
    except ET.ParseError as error:
        raise ValueError('invalid client UI manifest') from error
    if (manifest_files.count('ui/local_udp_module.lua')!=1 or
        manifest_files.count('ui/authority_two_x4_participant_probe.lua')!=1 or
        manifest_files.index('ui/local_udp_module.lua')>manifest_files.index('ui/authority_two_x4_participant_probe.lua') or
        any(name=='ui/authority_recovered_client_motion.lua' for name in manifest_files)):
        raise ValueError('client UDP loader manifest is ambiguous')
    if not cfg.endswith('}\n') or re.search(r'\bauthority_client_motion_[A-Za-z0-9_]+\s*=',cfg):
        raise ValueError('invalid or already extended client config')
    path='authority_load_save_path='+load.lua_literal(str(profile/'save'))
    if cfg.count('authority_load_save_path=')!=1 or not re.search(re.escape(path)+r'(?=,|})',cfg):
        raise ValueError('client save path differs from private profile')
    port=re.findall(r'authority_participant_port=([0-9]+)',cfg)
    old_tokens=re.findall(r'authority_participant_lease="([0-9a-f]{32})"',cfg)
    world_epoch=read_ticket_token(cfg,'world_epoch')
    claim_attempt=read_ticket_token(cfg,'attempt_id')
    if (len(port)!=1 or int(port[0])==settings['port'] or len(old_tokens)!=1 or
        len(set(tokens+[status[1],old_tokens[0],world_epoch,claim_attempt]))!=9):
        raise ValueError('client motion context overlaps existing control')
    if player_sector is not None:
        registry_nonces=re.findall(r"registry_status=assert\(X4LAN_RECOVERY_REGISTRY_STATUS\.new\(\{nonce='([0-9a-f]{32})',expected='",load_text)
        if len(registry_nonces)!=1:
            raise ValueError('client registry context is missing or ambiguous')
        checkpoint_id=read_ticket_token(cfg,'checkpoint_id',prefix='checkpoint-')
        contexts=set(tokens+[status[1],old_tokens[0],world_epoch,claim_attempt,registry_nonces[0],checkpoint_id])
        incarnation,nonce=player_sector['incarnation'],player_sector['nonce']
        if incarnation==nonce or incarnation in contexts or nonce in contexts:
            raise ValueError('client player sector context overlaps existing context')
    marker="expected='"+status[1]+'|'+str(status[2])+"'"
    if load_text.count(marker)!=1 or load_text.count('if not registry_status:ready() then return end')!=1:
        raise ValueError('client registry status differs')
    manifest_hook='    <file name="ui/authority_two_x4_participant_probe.lua" />\n'
    udp_loader_hook='    <file name="ui/local_udp_module.lua" />\n'
    if (udp_loader.read_bytes()!=participant.local_udp_loader_bytes() or
        mf.count(udp_loader_hook)!=1 or mf.count(manifest_hook)!=1 or mf.index(udp_loader_hook)>mf.index(manifest_hook)):
        raise ValueError('client owner hooks missing or ambiguous')
    motion_control=transform_motion_control(ctl,settings)
    load_text=append_load_ready_hook(load_text)
    modules,md_names=select_motion_sources(settings)
    if settings.get('saved_replica_control_fixture') is not None:
        control_sources=(ROOT/RECOVERY_REPLICA_CONTROL_MODULE[0],ROOT/'tools/dev'/RECOVERY_REPLICA_CONTROL_MD)
        if (any(path.is_symlink() or not path.is_file() for path in control_sources) or
            'ui/'+RECOVERY_REPLICA_CONTROL_MODULE[1] in manifest_files):
            raise ValueError('saved replica control overlay source or manifest is invalid')
    if saved_apply_fixture is not None:
        apply_sources=(ROOT/RECOVERY_APPLY_MODULE[0],ROOT/'tools/dev'/RECOVERY_APPLY_MD)
        if (any(path.is_symlink() or not path.is_file() for path in apply_sources) or
            'ui/'+RECOVERY_APPLY_MODULE[1] in manifest_files):
            raise ValueError('saved apply overlay source or manifest is invalid')
    if saved_playback_fixture is not None:
        playback_modules=(RECOVERY_APPLY_MODULE,RECOVERY_PLAYBACK_MODULE)
        for source,name in playback_modules:
            if ((ROOT/source).is_symlink() or not (ROOT/source).is_file() or 'ui/'+name in manifest_files):
                raise ValueError('saved playback overlay source or manifest is invalid')
    if player_sector is not None:
        sector_sources=(ROOT/PLAYER_SECTOR_MODULE[0],ROOT/'tools/dev'/PLAYER_SECTOR_MD)
        if (any(path.is_symlink() or not path.is_file() for path in sector_sources) or
            'ui/'+PLAYER_SECTOR_MODULE[1] in manifest_files):
            raise ValueError('player sector overlay source or manifest is invalid')
    files=[ui/name for _,name in modules]+[md/name for name in md_names]
    if any(p.exists() or p.is_symlink() for p in files):raise ValueError('client motion overlay would overwrite files')
    for (source,_),target in zip(modules,files):
        data=(ROOT/source).read_bytes()
        if target.name=='motion_buffer.lua':
            if data.count(b'return M\n')!=1:raise ValueError('unexpected motion buffer module')
            data=data.replace(b'return M\n',b'X4LAN_MOTION_BUFFER=M\nreturn M\n')
        with target.open('xb') as out:out.write(data)
    for name,target in zip(md_names,files[len(modules):]):
        with target.open('xb') as out:out.write((ROOT/'tools/dev'/name).read_bytes())
    manifest.write_text(mf.replace(manifest_hook,''.join('    <file name="ui/'+name+'" />\n' for _,name in modules)+manifest_hook),encoding='utf-8')
    config_add=render_motion_config(status,settings)
    config.write_text(cfg[:-2]+config_add+'}\n',encoding='ascii');config.chmod(0o600)
    loader.write_text(load_text,encoding='utf-8')
    control.write_text(motion_control,encoding='utf-8')
    return Installation([config,manifest,control,loader,*files],config,settings)
