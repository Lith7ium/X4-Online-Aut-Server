"""Build a Windows load/participant overlay for isolated fixture diagnostics.

Every saved checkpoint must contain the offered checkpoint marker. Legacy
fixture-only runs also check the original two identity-fixture ships. A
Company seat instead requires its operational entry in the full registry and
the native seat takeover. This does not start X4, establish process ownership,
authenticate log messages or admit a player.
The caller must establish that the supplied path names the intended private
physical profile before installation and X4 startup; a path ending in
``45841/save`` can also name an ordinary profile. The caller owns the view,
profile and their ancestors, and keeps them exclusive throughout use. The
participant probe can listen before load observations complete. The caller
must withhold all commands until correlated admission. The config
contains a plaintext ticket and lease; the caller must restrict access using
private ACLs and securely clean up its owned configuration after X4 stops.
Checks here cannot prevent concurrent replacement or establish these duties.
"""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat

from .trusted_load_messages import encode_bound_offer
from .client_registry_overlay import extend_full_inspect, extend_load_probe, validate_full_snapshot
from .client_seat_overlay import (
    COMPANY_MOTION_MD, COMPANY_PURCHASE_UI, company_motion_config, company_motion_ui,
    company_purchase_config, export_motion_buffer, extend_seat_gate,
)
from .client_motion_overlay import (
    SECRET_FIELDS, append_load_ready_hook, render_motion_config,
    select_motion_sources, transform_motion_control, validate_motion_settings,
)
from lockstep.launcher import extension

ROOT = Path(__file__).resolve().parents[2]
_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_MAX_DLL = 8 * 1024 * 1024
_FIELDS = ('world_epoch', 'connection_handle', 'baseline_id', 'load_generation',
           'checkpoint_id', 'sha256', 'attempt_id', 'slot')
_FACTION_ASSETS = (
    'libraries/factions.xml',
    'libraries/icons.xml',
    *(f'assets/textures/ui/x4lan/x4lan_player{index}.dds' for index in range(1, 17)),
)


def _ordinary(path, *, directory):
    info = path.lstat()
    if ((directory and not stat.S_ISDIR(info.st_mode)) or
            (not directory and not stat.S_ISREG(info.st_mode))):
        raise ValueError('expected ordinary file or directory')
    # Reparse metadata is exposed by Windows Python 3.11 even when
    # pathlib.Path.is_junction is unavailable.
    if hasattr(info, 'st_file_attributes'):
        reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', None)
        attributes = info.st_file_attributes
        if (type(reparse) is not int or reparse <= 0 or type(attributes) is not int or
                attributes < 0 or attributes & reparse):
            raise ValueError('reparse point not permitted')
    elif os.name == 'nt':
        raise ValueError('Windows reparse attributes unavailable')
    return info


def _source(path, maximum):
    info = _ordinary(path, directory=False)
    if not 0 < info.st_size <= maximum:
        raise ValueError('invalid source size')
    data = path.read_bytes()
    if len(data) != info.st_size:
        raise ValueError('source changed while reading')
    return data


def _windows_save_path(value):
    if (type(value) is not str or not value or
            any(ord(character) < 32 or ord(character) > 126 for character in value)):
        raise ValueError('ASCII Windows save path required')
    path = PureWindowsPath(value)
    if (not path.is_absolute() or '..' in path.parts or len(path.parts) < 3 or
            path.name.lower() != 'save' or path.parent.name != '45841'):
        raise ValueError('Windows profile-45841 save path syntax required')
    return value


def install_fixture_overlay(view, *, new_test_view, bound_offer, save_path,
                            participant_port, participant_lease, windows_dll, dll_sha256,
                            registry_status=None, registry_nonce=None, motion_settings=None,
                            registry_snapshot=None, seat_index=None, company_motion=None, company_purchase=None):
    """Create one absent x4_lan_lab in an exclusively owned new test view.

    A failure after creation leaves an owned partial extension for inspection;
    this function never deletes or overwrites it. Its returned SHA-256 map is
    only a snapshot: the caller must guard these files and the view until X4
    stops. The DLL digest must come from a separately verified trusted source.
    The save-path check is syntactic; the caller must independently establish
    that it resolves to the intended private physical profile. Before the
    ordered observations and correlated admission, the caller must not send
    participant commands even though the UDP probe may already be listening.
    The caller must protect the plaintext ticket and lease with private ACLs
    and clean up the owned configuration after X4 stops.
    Optional registry status must come from the verified offered checkpoint;
    the Lua gate checks its epoch/count before the ordinary pause receipt.
    The optional seat index selects one operational entry in the full saved
    registry and replaces the legacy named fixture probe with a seat gate.
    Optional motion settings reuse the Linux client's modules and update hooks.
    They require this registry gate and a distinct UDP port/context. The caller
    must stop motion before pause/exit; this builder starts no process or socket.
    """
    if new_test_view is not True:
        raise ValueError('exclusive new test view required')
    encode_bound_offer(bound_offer)  # validates the immutable offer and marker
    offer = bound_offer.offer
    full_registry = None
    if registry_snapshot is not None:
        full_registry = validate_full_snapshot(registry_snapshot, registry_status,
                                               registry_nonce, bound_offer.marker)
    if seat_index is not None and full_registry is None:
        raise ValueError('client seat requires full registry inspection')
    if (not _HEX32.fullmatch(offer.world_epoch) or
            type(participant_port) is not int or not 1 <= participant_port <= 65535 or
            type(participant_lease) is not str or not _HEX32.fullmatch(participant_lease) or
            type(dll_sha256) is not str or not _HEX64.fullmatch(dll_sha256)):
        raise ValueError('invalid participant or DLL configuration')
    save_path = _windows_save_path(save_path)
    view = Path(view)
    windows_dll = Path(windows_dll)
    if not view.is_absolute() or not windows_dll.is_absolute():
        raise ValueError('absolute view and DLL paths required')
    _ordinary(view, directory=True)
    _ordinary(view / '.x4ls-view', directory=False)
    extensions = view / 'extensions'
    _ordinary(extensions, directory=True)
    dll = _source(windows_dll, _MAX_DLL)
    if not dll.startswith(b'MZ') or hashlib.sha256(dll).hexdigest() != dll_sha256:
        raise ValueError('Windows DLL does not match trusted digest')
    sources = {
        'ui/sha256.lua': ROOT / 'src/lockstep/base/ui/sha256.lua',
        'ui/client_load_barrier.lua': ROOT / 'src/authority/lua/client_load_barrier.lua',
        'ui/x4_client_load_engine.lua': ROOT / 'src/authority/lua/x4_client_load_engine.lua',
        'ui/authority_identity_probe.lua': ROOT / 'tools/dev/authority_identity_probe.lua',
        'ui/authority_checkpoint_marker_probe.lua': ROOT / 'tools/dev/authority_checkpoint_marker_probe.lua',
        'ui/authority_download_load_probe.lua': ROOT / 'tools/dev/authority_download_load_probe.lua',
        'ui/authority_control_state.lua': ROOT / 'src/authority/lua/control_state.lua',
        'ui/x4_control_engine.lua': ROOT / 'src/authority/lua/x4_control_engine.lua',
        'ui/local_udp_module.lua': ROOT / 'src/authority/lua/local_udp_module.lua',
        'ui/authority_two_x4_participant_probe.lua': ROOT / 'tools/dev/authority_two_x4_participant_probe.lua',
        'md/authority_identity_fixture.xml': ROOT / 'tools/dev/authority_identity_fixture.xml',
        'md/authority_checkpoint_marker.xml': ROOT / 'src/authority/md/checkpoint_marker.xml',
    }
    legacy_identity = seat_index is None
    if not legacy_identity:
        del sources['ui/authority_identity_probe.lua']
        del sources['md/authority_identity_fixture.xml']
    # Fixture saves were made with the tracked faction pool. Preserve those
    # definitions and icon paths when loading the same world on a client.
    # The offered session still assigns players to the shared player faction.
    sources.update({name: ROOT / 'src/lockstep/base' / name for name in _FACTION_ASSETS})
    files = {name: _source(path, 1024 * 1024) for name, path in sources.items()}
    load_probe = files['ui/authority_download_load_probe.lua']
    hook = b" elseif state=='observing' then\n"
    if load_probe.count(hook) != 1:
        raise ValueError('unexpected load probe hook')
    marker_hook = b'  if not X4LAN_AUTHORITY_MARKER_READY() then return end\n'
    if legacy_identity:
        marker_hook += b'  if not X4LAN_IDENTITY_READY() then return end\n'
    files['ui/authority_download_load_probe.lua'] = load_probe.replace(hook, hook + marker_hook)
    if registry_status is not None or registry_nonce is not None:
        files['ui/authority_download_load_probe.lua'] = extend_load_probe(
            files['ui/authority_download_load_probe.lua'].decode('utf-8'),
            registry_nonce, registry_status,
            load_gate='identity' if legacy_identity else 'marker').encode('utf-8')
        for target, source in (
                ('ui/recovery_registry_status.lua', 'src/authority/lua/recovery_registry_status.lua'),
                ('md/authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml')):
            files[target] = _source(ROOT / source, 1024 * 1024)
    if full_registry is not None:
        for target, source in (
                ('ui/authority_registry_availability_snapshot.lua',
                 'tools/dev/authority_registry_availability_snapshot.lua'),
                ('ui/registry_checkpoint_gate.lua',
                 'src/authority/lua/registry_checkpoint_gate.lua'),
                ('ui/client_registry_inspect.lua',
                 'src/authority/lua/client_registry_inspect.lua'),
                ('md/authority_registry_availability_snapshot.xml',
                 'tools/dev/authority_registry_availability_snapshot.xml')):
            files[target] = _source(ROOT / source, 1024 * 1024)
        files['ui/authority_download_load_probe.lua'] = extend_full_inspect(
            files['ui/authority_download_load_probe.lua'].decode('utf-8')).encode('utf-8')
    if seat_index is not None:
        load_name = 'ui/authority_download_load_probe.lua'
        files[load_name] = extend_seat_gate(files[load_name].decode('utf-8'), seat_index,
                                            full_registry, company_motion=company_motion is not None,
                                            company_purchases=company_purchase is not None).encode('utf-8')
        for target, source in (
                ('ui/authority_registry_resolve_string.lua',
                 'tools/dev/authority_registry_resolve_string.lua'),
                ('ui/client_seat.lua', 'src/authority/lua/client_seat.lua'),
                ('md/client_seat.xml', 'src/authority/md/client_seat.xml')):
            files[target] = _source(ROOT / source, 1024 * 1024)
    udp = files['ui/local_udp_module.lua']
    if udp.count(b'return M\n') != 1:
        raise ValueError('unexpected UDP module export')
    files['ui/local_udp_module.lua'] = udp.replace(b'return M\n', b'X4LAN_LOCAL_UDP_MODULE=M\nreturn M\n')
    ticket = {field: getattr(offer, field) for field in _FIELDS if field != 'slot'}
    ticket['slot'] = 'save_009'
    def lua_string(value):
        return json.dumps(value, ensure_ascii=True)
    ticket_lua = ','.join(key + '=' + (str(ticket[key]) if type(ticket[key]) is int else lua_string(ticket[key]))
                          for key in _FIELDS)
    identity_config = 'authority_identity_phase="load",' if legacy_identity else ''
    config = ('X4LAN_CONFIG = {enabled=false,test_profile="45841",authority_download_load=true,'
              f'{identity_config}authority_marker_phase="load",'
              f'authority_load_save_path={lua_string(save_path)},authority_ticket={{{ticket_lua}}},'
              f'authority_participant_port={participant_port},authority_participant_lease={lua_string(participant_lease)}' + '}\n')
    ui_files = [name for name in sources if name.startswith('ui/')]
    if registry_status is not None:
        ui_files.insert(ui_files.index('ui/authority_download_load_probe.lua'),
                        'ui/recovery_registry_status.lua')
    if full_registry is not None:
        insertion = ui_files.index('ui/recovery_registry_status.lua')
        ui_files[insertion:insertion] = [
            'ui/authority_registry_availability_snapshot.lua',
            'ui/registry_checkpoint_gate.lua',
            'ui/client_registry_inspect.lua']
        config = config[:-2] + ',authority_registry_snapshot={' + ','.join(
            key + '=' + (str(value) if type(value) is int else lua_string(value))
            for key, value in full_registry.items()) + '}}\n'
    if seat_index is not None:
        insertion = ui_files.index('ui/authority_download_load_probe.lua')
        ui_files[insertion:insertion] = [
            'ui/authority_registry_resolve_string.lua', 'ui/client_seat.lua']
    if company_purchase is not None:
        config = config[:-2] + company_purchase_config(company_purchase, seat_index, full_registry) + '}\n'
        insertion = ui_files.index('ui/authority_download_load_probe.lua')
        ui_files[insertion:insertion] = ['ui/' + name for name in COMPANY_PURCHASE_UI]
        for name, source in COMPANY_PURCHASE_UI.items():
            files['ui/' + name] = _source(ROOT / source, 1024 * 1024)
    if company_motion is not None:
        config = config[:-2] + company_motion_config(company_motion, seat_index, full_registry) + '}\n'
        ui_sources = company_motion_ui(company_motion)
        if company_motion.playback_index is not None and motion_settings is not None:
            raise ValueError('company hull playback requires its exclusive motion overlay')
        insertion = ui_files.index('ui/authority_download_load_probe.lua')
        ui_files[insertion:insertion] = ['ui/' + name for name in ui_sources]
        for folder, sources in (('ui', ui_sources), ('md', COMPANY_MOTION_MD)):
            for name, source in sources.items():
                data = _source(ROOT / source, 1024 * 1024)
                files[folder + '/' + name] = export_motion_buffer(data) if name == 'motion_buffer.lua' else data
    if motion_settings is not None:
        selection = validate_motion_settings(registry_status, motion_settings)
        motion_settings = selection.settings
        tokens = [motion_settings[name] for name in SECRET_FIELDS]
        contexts = tokens + [registry_status[1], participant_lease, offer.world_epoch,
                             offer.attempt_id, registry_nonce,
                             offer.checkpoint_id.removeprefix('checkpoint-')]
        if (registry_nonce is None or motion_settings['port'] == participant_port or
                len(set(contexts)) != len(contexts)):
            raise ValueError('client motion context overlaps existing control')
        player_sector = motion_settings.get('player_sector')
        if player_sector is not None:
            sector_tokens = [player_sector['incarnation'], player_sector['nonce']]
            if len(set(contexts + sector_tokens)) != len(contexts) + 2:
                raise ValueError('client player sector context overlaps existing context')
        modules, md_names = select_motion_sources(motion_settings)
        for source, name in modules:
            data = _source(ROOT / source, 1024 * 1024)
            if name == 'motion_buffer.lua':
                data = export_motion_buffer(data)
            files['ui/' + name] = data
        for name in md_names:
            files['md/' + name] = _source(ROOT / 'tools/dev' / name, 1024 * 1024)
        control_name = 'ui/authority_two_x4_participant_probe.lua'
        load_name = 'ui/authority_download_load_probe.lua'
        files[control_name] = transform_motion_control(
            files[control_name].decode('utf-8'), motion_settings).encode('utf-8')
        files[load_name] = append_load_ready_hook(files[load_name].decode('utf-8')).encode('utf-8')
        config = config[:-2] + render_motion_config(registry_status, motion_settings) + '}\n'
        insertion = ui_files.index(control_name)
        ui_files[insertion:insertion] = ['ui/' + name for _, name in modules
                                           if 'ui/' + name not in ui_files]
    files.update({'ui/config.lua': config.encode('ascii'),
                  'ui.xml': extension.ui_xml(ui_files).encode('utf-8'),
                  'content.xml': extension.CONTENT.encode('utf-8'),
                  'native/x4lan_net_64.dll': dll})
    target = extensions / extension.EXTENSION_ID
    target.mkdir()  # exclusive: an existing extension is never changed
    for name in ('ui', 'md', 'native', 'libraries', 'assets/textures/ui/x4lan'):
        (target / name).mkdir(parents=True)
    for name, data in files.items():
        with (target / name).open('xb') as output:
            output.write(data)
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
