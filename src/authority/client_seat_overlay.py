"""Connect a saved company-ship choice to the paused client load owner."""
import json

from .client_registry_overlay import registry_receipt
from .company_motion_source import CompanyMotionSource
from .company_purchase_source import CompanyPurchaseSource

COMPANY_PURCHASE_UI = {
    f'{name}.lua': f'src/authority/lua/{name}.lua'
    for name in ('company_trade_capture', 'company_purchase_source')
}

COMPANY_UNDOCK_UI = {'company_undock_hold.lua': 'src/authority/lua/company_undock_hold.lua'}
COMPANY_UNDOCK_MD = {'company_client_undock.xml': 'src/authority/md/company_client_undock.xml'}


def company_purchase_ui(source):
    return COMPANY_PURCHASE_UI | COMPANY_UNDOCK_UI if source.undock else COMPANY_PURCHASE_UI


def company_purchase_md(source):
    return COMPANY_UNDOCK_MD if source.undock else {}


COMPANY_MOTION_UI = {
    'replica_motion_binding.lua': 'src/authority/lua/replica_motion_binding.lua',
    'authority_job_descriptor_probe.lua': 'tools/dev/authority_job_descriptor_probe.lua',
    'source_motion_sampler.lua': 'src/authority/lua/source_motion_sampler.lua',
    'map_render_observation.lua': 'src/authority/lua/map_render_observation.lua',
    'company_observer_target.lua': 'src/authority/lua/company_observer_target.lua',
    'company_observer_map.lua': 'src/authority/lua/company_observer_map.lua',
    'company_observer_stream.lua': 'src/authority/lua/company_observer_stream.lua',
    'company_motion_source.lua': 'src/authority/lua/company_motion_source.lua',
}
COMPANY_PLAYBACK_UI = {
    f'{name}.lua': f'src/authority/lua/{name}.lua'
    for name in ('motion_buffer', 'motion_playback', 'company_hull_playback',
                 'company_hull_playback_x4', 'company_hull_trial')
}
COMPANY_COCKPIT_RADAR_UI = {
    'company_cockpit_observer.lua': 'src/authority/lua/company_cockpit_observer.lua',
    'company_cockpit_playback.lua': 'src/authority/lua/company_cockpit_playback.lua',
}
COMPANY_MOTION_MD = {
    'authority_job_descriptor_probe.xml': 'tools/dev/authority_job_descriptor_probe.xml',
    'company_observer_target.xml': 'src/authority/md/company_observer_target.xml',
}
COMPANY_COCKPIT_RADAR_MD = {
    'company_cockpit_observer.xml': 'src/authority/md/company_cockpit_observer.xml',
}

SEAT_TAG = b'[X4LAN] AUTH_CLIENT_SEAT '
SEAT_REFUSED_TAG = b'[X4LAN] AUTH_CLIENT_SEAT_REFUSED '
REFUSALS = frozenset(b'''
    REGISTRY_INVALID REGISTRY_CHANGED INDEX_INVALID BINDING_INVALID OWNER_INVALID
    SHIP_INOPERABLE TARGET_CHANGED CONTROL_MISSING PLAYER_CONTEXT
    SHIP_DOCKED_OR_STORED SHIP_SECTOR_INVALID PILOT_PRESENT ACTION_INVALID
    CLOCK_INVALID TIMEOUT PAUSE_OR_REGISTRY_LOST UNEXPECTED_RESULT RESULT_MISMATCH
    RESULT_INVALID SHIP_READ_FAILED DISPATCH_FAILED REGISTRATION_FAILED
    SHIP_ID_INVALID LOCAL_CONTROL_MISSING SHIP_DOCK_STATE_UNSAFE
    CAPTAIN_CHANGED CAPTAIN_CONTEXT
'''.split())


def seat_prefix(index, nonce, status):
    """Format the verified selection context carried by seat receipts."""
    registry_receipt(nonce, status)
    if status[0] != 'ready' or type(index) is not int or not 1 <= index <= status[2]:
        raise ValueError('invalid saved company ship index')
    return f'{nonce}|{status[1]}|{index}|'.encode('ascii')


def company_motion_ui(source):
    if getattr(source, 'cockpit_radar_playback', False):
        return COMPANY_PLAYBACK_UI | COMPANY_COCKPIT_RADAR_UI | COMPANY_MOTION_UI
    if source.playback_index is not None:
        return COMPANY_PLAYBACK_UI | COMPANY_MOTION_UI
    return COMPANY_MOTION_UI


def company_motion_md(source):
    if getattr(source, 'cockpit_radar_playback', False):
        return COMPANY_MOTION_MD | COMPANY_COCKPIT_RADAR_MD
    return COMPANY_MOTION_MD


def export_motion_buffer(data):
    if data.count(b'return M\n') != 1:
        raise ValueError('unexpected motion buffer module')
    return data.replace(b'return M\n', b'X4LAN_MOTION_BUFFER=M\nreturn M\n')


def company_motion_config(source, index, full_registry):
    """Hand an installed, verified registry selection to the local IPC owner."""
    if type(source) is not CompanyMotionSource or full_registry is None or index is None:
        raise ValueError('company motion requires a verified selected seat and local source owner')
    settings = source.configure(full_registry['epoch'], index, operational_bits=full_registry['operational_bits'])
    return ',authority_company_motion={' + ','.join(
        f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in settings.items()
    ) + '}'


def company_purchase_config(source, index, full_registry):
    if not isinstance(source, CompanyPurchaseSource) or full_registry is None or index is None:
        raise ValueError('Company purchases require a verified selected seat')
    settings = source.configure(full_registry['epoch'], index)
    return ',authority_company_purchase={' + ','.join(
        f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in settings.items()
    ) + '}'


def extend_seat_gate(text, index, full_registry, *, company_motion=False, company_purchases=False):
    """Run seat takeover after full inspection and before pause observation."""
    if full_registry is None:
        raise ValueError('client seat requires full registry inspection')
    if type(index) is not int or not 1 <= index <= full_registry['issued_count']:
        raise ValueError('invalid saved company ship index')
    if full_registry['operational_bits'][index - 1] != '1':
        raise ValueError('saved company ship is unavailable or inoperable')
    declaration = 'local full_registry_inspect=nil\n'
    hook = "  assert(full_registry_inspect:guard(),'full registry guard lost before admission')\n"
    if text.count(declaration) != 1 or text.count(hook) != 1:
        raise ValueError('client seat requires the full inspection gate')
    gate = '''  if not client_seat then
   client_seat=assert(X4LAN_AUTHORITY_CLIENT_SEAT.new_x4(
    cfg.authority_registry_snapshot,INDEX,full_registry_inspect,function() failed=true end))
  end
  if not client_seat:tick() or not client_seat:admission_ready() then return end
  X4LAN_AUTHORITY_SELECTED_SEAT=client_seat
'''.replace('INDEX', str(index))
    if company_motion:
        gate += '  X4LAN_COMPANY_MOTION_SOURCE.arm(client_seat)\n'
    if company_purchases:
        gate += '  X4LAN_COMPANY_PURCHASE_SOURCE.arm(client_seat)\n'
    return text.replace(declaration, declaration + 'local client_seat=nil\n').replace(hook, hook + gate)
