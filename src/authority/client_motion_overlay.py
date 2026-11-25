"""Shared, pure rendering and validation for recovered client motion overlays."""
from dataclasses import dataclass
import math
import re

HEX = re.compile(r'[0-9a-f]{32}\Z')
MODULES = (
    ('tools/dev/authority_registry_resolve_string.lua', 'authority_registry_resolve_string.lua'),
    ('src/authority/lua/replica_motion_binding.lua', 'replica_motion_binding.lua'),
    ('src/authority/lua/saved_baseline_motion_binding.lua', 'saved_baseline_motion_binding.lua'),
    ('src/authority/lua/motion_buffer.lua', 'motion_buffer.lua'),
    ('src/authority/lua/client_motion_event_router.lua', 'client_motion_event_router.lua'),
    ('src/authority/lua/motion_client_owner.lua', 'motion_client_owner.lua'),
    ('tools/dev/authority_recovered_client_motion.lua', 'authority_recovered_client_motion.lua'),
)
MD = ('authority_saved_baseline_motion_bind.xml', 'authority_replica_motion_bind.xml')
SECRET_FIELDS = ('lease', 'attempt', 'generation', 'nonce', 'router_generation')
PLAYER_SECTOR_MODULE = ('src/authority/lua/player_sector_interest.lua', 'player_sector_interest.lua')
PLAYER_SECTOR_MD = 'authority_player_sector_interest.xml'
RECOVERY_APPLY_FIXTURE = 'recovery_enh_870'
RECOVERY_APPLY_EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
RECOVERY_APPLY_MODULE = ('src/authority/lua/saved_motion_apply.lua', 'saved_motion_apply.lua')
RECOVERY_APPLY_MD = 'authority_saved_motion_apply.xml'
RECOVERY_PLAYBACK_MODULE = ('src/authority/lua/motion_playback.lua', 'motion_playback.lua')
RECOVERY_REPLICA_CONTROL_MODULE = ('src/authority/lua/saved_replica_control.lua', 'saved_replica_control.lua')
RECOVERY_REPLICA_CONTROL_MD = 'authority_saved_replica_control.xml'


@dataclass(frozen=True)
class MotionSelection:
    """Validated selection with settings detached from caller-owned containers."""

    status: tuple
    settings: dict
    indices: tuple


def validate_motion_settings(status, settings):
    """Validate motion options; replica control requires the fixed playback actor."""
    if (type(status) is not tuple or len(status) != 4 or status[0] != 'ready' or
            type(status[1]) is not str or not HEX.fullmatch(status[1]) or
            type(status[2]) is not int or not 1 <= status[2] <= 200000 or
            type(status[3]) is not str or not re.fullmatch(r'[0-9a-f]{64}', status[3])):
        raise ValueError('observed saved registry required')

    if type(settings) is not dict:
        raise ValueError('invalid client motion settings')
    copied_settings = dict(settings)
    if type(copied_settings.get('player_sector')) is dict:
        copied_settings['player_sector'] = dict(copied_settings['player_sector'])
    if type(copied_settings.get('indices')) is list:
        copied_settings['indices'] = list(copied_settings['indices'])

    fields = {'port', 'lease', 'attempt', 'generation', 'nonce', 'router_generation', 'timeout'}
    allowed_optional = {
        'player_sector', 'saved_apply_fixture', 'saved_playback_fixture',
        'saved_replica_control_fixture', 'saved_initial_alignment_fixture',
    }
    required_fields = set(copied_settings) - allowed_optional
    if required_fields not in (fields | {'index'}, fields | {'indices'}):
        raise ValueError('invalid client motion settings')

    saved_apply_fixture = copied_settings.get('saved_apply_fixture')
    if 'saved_apply_fixture' in copied_settings and (
            saved_apply_fixture != RECOVERY_APPLY_FIXTURE or status[1] != RECOVERY_APPLY_EPOCH or
            copied_settings.get('index') != 1318 or 'indices' in copied_settings):
        raise ValueError('invalid recovery saved-apply fixture')
    saved_playback_fixture = copied_settings.get('saved_playback_fixture')
    if 'saved_playback_fixture' in copied_settings and (
            saved_playback_fixture != RECOVERY_APPLY_FIXTURE or status[1] != RECOVERY_APPLY_EPOCH or
            copied_settings.get('index') != 1318 or 'indices' in copied_settings or
            'saved_apply_fixture' in copied_settings):
        raise ValueError('invalid recovery saved-playback fixture')

    if 'saved_replica_control_fixture' in copied_settings and (
            copied_settings['saved_replica_control_fixture'] != RECOVERY_APPLY_FIXTURE or
            saved_playback_fixture != RECOVERY_APPLY_FIXTURE):
        raise ValueError('invalid recovery saved-replica-control fixture')

    # Matching playback above also enforces the fixed saved epoch and index.
    if 'saved_initial_alignment_fixture' in copied_settings and (
            copied_settings['saved_initial_alignment_fixture'] != RECOVERY_APPLY_FIXTURE or
            copied_settings.get('saved_replica_control_fixture') != RECOVERY_APPLY_FIXTURE or
            saved_playback_fixture != RECOVERY_APPLY_FIXTURE):
        raise ValueError('invalid recovery saved-initial-alignment fixture')

    player_sector = copied_settings.get('player_sector')
    if 'player_sector' in copied_settings:
        if (type(player_sector) is not dict or set(player_sector) not in
                ({'incarnation', 'nonce'}, {'incarnation', 'nonce', 'after_bound'}) or
                ('after_bound' in player_sector and player_sector['after_bound'] is not True) or
                any(type(player_sector[name]) is not str or not HEX.fullmatch(player_sector[name])
                    for name in ('incarnation', 'nonce'))):
            raise ValueError('invalid player sector settings')

    indices = ([copied_settings['index']] if 'index' in copied_settings
               else copied_settings['indices'])
    if (type(indices) is not list or not 1 <= len(indices) <= 32 or
            any(type(index) is not int or not 1 <= index <= status[2] for index in indices) or
            len(set(indices)) != len(indices)):
        raise ValueError('invalid client motion indices')
    if (type(copied_settings['port']) is not int or not 1 <= copied_settings['port'] <= 65535 or
            type(copied_settings['timeout']) not in (int, float) or
            not math.isfinite(copied_settings['timeout']) or
            not 0 < copied_settings['timeout'] <= 30):
        raise ValueError('invalid client motion bounds')
    tokens = [copied_settings[name] for name in SECRET_FIELDS]
    if (any(type(token) is not str or not HEX.fullmatch(token) for token in tokens) or
            len(set(tokens + [status[1]])) != 6):
        raise ValueError('invalid or colliding client motion tokens')

    return MotionSelection(status, copied_settings, tuple(indices))


def select_motion_sources(settings):
    """Return common Lua modules and MD files in their required load order."""
    modules = MODULES
    md_names = MD
    if settings.get('player_sector') is not None:
        modules = (*modules[:-1], PLAYER_SECTOR_MODULE, modules[-1])
        md_names = (*md_names, PLAYER_SECTOR_MD)
    if settings.get('saved_apply_fixture') is not None:
        modules = (*modules[:-1], RECOVERY_APPLY_MODULE, modules[-1])
        md_names = (*md_names, RECOVERY_APPLY_MD)
    if settings.get('saved_playback_fixture') is not None:
        playback_modules = (RECOVERY_APPLY_MODULE, RECOVERY_PLAYBACK_MODULE)
        modules = (*modules[:-1], *playback_modules, modules[-1])
    if settings.get('saved_replica_control_fixture') is not None:
        modules = (*modules[:-1], RECOVERY_REPLICA_CONTROL_MODULE, modules[-1])
        md_names = (*md_names, RECOVERY_REPLICA_CONTROL_MD)
    return modules, md_names


def render_motion_config(status, settings):
    """Render the exact Lua config suffix shared by Linux and Windows overlays."""
    selection = validate_motion_settings(status, settings)
    settings = selection.settings
    config = (',authority_client_motion_enabled=true,authority_client_motion_epoch="' + status[1] +
              '",authority_client_motion_baseline=' + str(status[2]))
    config += ',authority_client_motion_port=' + str(settings['port'])
    if 'index' in settings:
        config += ',authority_client_motion_index=' + str(settings['index'])
    else:
        config += ',authority_client_motion_indices={' + ','.join(map(str, selection.indices)) + '}'
    config += ',authority_client_motion_timeout=' + format(settings['timeout'], '.17g')
    for name in SECRET_FIELDS:
        config += ',authority_client_motion_' + name + '="' + settings[name] + '"'
    if settings.get('saved_apply_fixture') is not None:
        config += ',authority_client_motion_saved_apply_fixture="' + settings['saved_apply_fixture'] + '"'
    if settings.get('saved_playback_fixture') is not None:
        config += ',authority_client_motion_saved_playback_fixture="' + settings['saved_playback_fixture'] + '"'
    if settings.get('saved_replica_control_fixture') is not None:
        config += ',authority_client_motion_saved_replica_control_fixture="' + settings['saved_replica_control_fixture'] + '"'
    if settings.get('saved_initial_alignment_fixture') is not None:
        config += ',authority_client_motion_saved_initial_alignment_fixture="' + settings['saved_initial_alignment_fixture'] + '"'
    if settings.get('player_sector') is not None:
        player_sector = settings['player_sector']
        for name in ('incarnation', 'nonce'):
            config += ',authority_client_motion_player_sector_' + name + '="' + player_sector[name] + '"'
    return config


def transform_motion_control(control_text, settings):
    """Install the shared update hook and motion-specific STOP/pause gate."""
    control_hook = ' assert(state:on_update())\n'
    local_hook = 'local net,state,engine,next_hello,failed=nil,nil,nil,0,false\n'
    validity_hook = ' if not ffi.C.IsPlayerValid() then'
    submission_hook = "  assert(state:submit('isolated-client',epoch,tonumber(id),op))\n"
    if (control_text.count(control_hook) != 1 or control_text.count(local_hook) != 1 or
            control_text.count(validity_hook) != 1 or control_text.count("SetScript('onUpdate'") != 1):
        raise ValueError('client owner hooks missing or ambiguous')
    if settings.get('saved_playback_fixture') is not None and control_text.count(submission_hook) != 1:
        raise ValueError('client pause submission hook missing or ambiguous')

    code = ''' if cfg.authority_client_motion_enabled then
  if not client_motion and X4LAN_RECOVERED_CLIENT_LOAD_READY() and not ffi.C.IsGamePaused() then
   client_motion=assert(X4LAN_RECOVERED_CLIENT_MOTION.new_x4({enabled=true,port=cfg.authority_client_motion_port,
    epoch=cfg.authority_client_motion_epoch,baseline=cfg.authority_client_motion_baseline,index=cfg.authority_client_motion_index,
    indices=cfg.authority_client_motion_indices,
    lease=cfg.authority_client_motion_lease,attempt=cfg.authority_client_motion_attempt,generation=cfg.authority_client_motion_generation,
    nonce=cfg.authority_client_motion_nonce,router_generation=cfg.authority_client_motion_router_generation,
    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))
  end
  if client_motion then assert(client_motion:tick())end
 end
'''
    if settings.get('saved_apply_fixture') is not None:
        code = code.replace(
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))',
            '    saved_apply_fixture=cfg.authority_client_motion_saved_apply_fixture,\n'
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))')
    if settings.get('saved_playback_fixture') is not None:
        code = code.replace(
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))',
            '    saved_playback_fixture=cfg.authority_client_motion_saved_playback_fixture,\n'
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))')
        pause_guard = (
            "  assert(op~='pause' or not client_motion or client_motion:state()=='stopped',"
            "'client playback STOP required before pause')\n")
        control_text = control_text.replace(submission_hook, pause_guard + submission_hook)
    if settings.get('saved_replica_control_fixture') is not None:
        code = code.replace(
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))',
            '    saved_replica_control_fixture=cfg.authority_client_motion_saved_replica_control_fixture,\n'
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))')
    if settings.get('saved_initial_alignment_fixture') is not None:
        code = code.replace(
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))',
            '    saved_initial_alignment_fixture=cfg.authority_client_motion_saved_initial_alignment_fixture,\n'
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))')
    player_sector = settings.get('player_sector')
    if player_sector is not None:
        after_bound = ',after_bound=true' if player_sector.get('after_bound') is True else ''
        code = code.replace(
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))',
            '    player_sector={incarnation=cfg.authority_client_motion_player_sector_incarnation,'
            'nonce=cfg.authority_client_motion_player_sector_nonce' + after_bound + '},\n'
            '    timeout=cfg.authority_client_motion_timeout,expected_save_path=cfg.authority_load_save_path}))')
    control_text = control_text.replace(local_hook, local_hook + 'ffi.cdef[[bool IsGamePaused(void);]]\nlocal client_motion=nil\n')
    control_text = control_text.replace(
        validity_hook,
        validity_hook + "\n  assert(not client_motion, 'client motion player lost')\n")
    return control_text.replace(control_hook, control_hook + code)


def append_load_ready_hook(load_text):
    """Append the existing load-complete predicate used by the shared owner."""
    if 'X4LAN_RECOVERED_CLIENT_LOAD_READY' in load_text or load_text.count("SetScript('onUpdate'") != 1:
        raise ValueError('client load owner hook missing or ambiguous')
    return load_text + "\nfunction X4LAN_RECOVERED_CLIENT_LOAD_READY() return state=='done' and not failed end\n"
