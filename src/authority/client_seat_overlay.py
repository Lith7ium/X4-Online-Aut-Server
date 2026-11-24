"""Connect a saved company-ship choice to the paused client load owner."""
import json

from .client_registry_overlay import registry_receipt
from .company_motion_source import CompanyMotionSource

COMPANY_MOTION_UI = {
    'replica_motion_binding.lua': 'src/authority/lua/replica_motion_binding.lua',
    'authority_job_descriptor_probe.lua': 'tools/dev/authority_job_descriptor_probe.lua',
    'source_motion_sampler.lua': 'src/authority/lua/source_motion_sampler.lua',
    'company_motion_source.lua': 'src/authority/lua/company_motion_source.lua',
}
COMPANY_MOTION_MD = {'authority_job_descriptor_probe.xml': 'tools/dev/authority_job_descriptor_probe.xml'}

SEAT_TAG = b'[X4LAN] AUTH_CLIENT_SEAT '
SEAT_REFUSED_TAG = b'[X4LAN] AUTH_CLIENT_SEAT_REFUSED '
REFUSALS = frozenset(b'''
    REGISTRY_INVALID REGISTRY_CHANGED INDEX_INVALID BINDING_INVALID OWNER_INVALID
    SHIP_INOPERABLE TARGET_CHANGED CONTROL_MISSING PLAYER_CONTEXT
    SHIP_DOCKED_OR_STORED SHIP_SECTOR_INVALID PILOT_PRESENT ACTION_INVALID
    CLOCK_INVALID TIMEOUT PAUSE_OR_REGISTRY_LOST UNEXPECTED_RESULT RESULT_MISMATCH
    RESULT_INVALID SHIP_READ_FAILED DISPATCH_FAILED REGISTRATION_FAILED
    SHIP_ID_INVALID LOCAL_CONTROL_MISSING SHIP_DOCK_STATE_UNSAFE
'''.split())


def seat_prefix(index, nonce, status):
    """Format the verified selection context carried by seat receipts."""
    registry_receipt(nonce, status)
    if status[0] != 'ready' or type(index) is not int or not 1 <= index <= status[2]:
        raise ValueError('invalid saved company ship index')
    return f'{nonce}|{status[1]}|{index}|'.encode('ascii')


def company_motion_config(source, index, full_registry):
    """Hand an installed, verified registry selection to the local IPC owner."""
    if type(source) is not CompanyMotionSource or full_registry is None or index is None:
        raise ValueError('company motion requires a verified selected seat and local source owner')
    settings = source.configure(full_registry['epoch'], index)
    return ',authority_company_motion={' + ','.join(
        f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in settings.items()
    ) + '}'


def extend_seat_gate(text, index, full_registry, *, company_motion=False):
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
  if not client_seat:tick() or not client_seat:ready() then return end
'''.replace('INDEX', str(index))
    if company_motion:
        gate += '  X4LAN_COMPANY_MOTION_SOURCE.arm(client_seat)\n'
    return text.replace(declaration, declaration + 'local client_seat=nil\n').replace(hook, hook + gate)
