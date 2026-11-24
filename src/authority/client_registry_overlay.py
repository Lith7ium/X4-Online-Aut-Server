"""Shared read-only registry gate for the existing client load update hook.

The caller supplies status derived from its verified checkpoint. Epoch/count
receipts describe the live registry header; they do not prove the full mapping
or actor identity. This module performs no filesystem or native operations.
"""
import re


def _check_load_gate(load_gate):
    if load_gate not in ('identity', 'marker'):
        raise ValueError('invalid client registry load gate')


def registry_status_text(status):
    """Validate the saved status tuple and format the observed live header."""
    if status == ('uninitialized',):
        return 'uninitialized'
    if (type(status) is tuple and len(status) == 4 and status[0] == 'ready' and
            type(status[1]) is str and re.fullmatch(r'[0-9a-f]{32}', status[1]) and
            type(status[2]) is int and 1 <= status[2] <= 200000 and
            type(status[3]) is str and re.fullmatch(r'[0-9a-f]{64}', status[3])):
        return status[1] + '|' + str(status[2])
    raise ValueError('invalid client registry status')


def registry_receipt(nonce, status):
    """Return the expected correlated startup record, without its log tag."""
    if type(nonce) is not str or not re.fullmatch(r'[0-9a-f]{32}', nonce):
        raise ValueError('invalid client registry nonce')
    return (nonce + '|startup|' + registry_status_text(status)).encode('ascii')


def validate_full_snapshot(snapshot, status, nonce, marker):
    """Pin a source-checked format-3 snapshot to the offered checkpoint."""
    registry_status_text(status)
    registry_receipt(nonce, status)
    if status[0] != 'ready':
        raise ValueError('full registry inspection requires a ready header')
    if (type(snapshot) is not dict or type(snapshot.get('format')) is not int or
            snapshot['format'] != 3 or snapshot.get('source_registry_checked') is not True):
        raise ValueError('source-checked format-3 registry snapshot required')
    if (snapshot.get('evidence_scope') != 'saved checkpoint bytes only' or
            snapshot.get('run_id') != marker or
            snapshot.get('epoch') != status[1] or
            type(snapshot.get('issued_count')) is not int or
            snapshot['issued_count'] != status[2] or
            snapshot.get('mapping_sha256') != status[3]):
        raise ValueError('registry snapshot differs from offered checkpoint')
    if marker in (status[1], nonce) or nonce == status[1]:
        raise ValueError('registry correlation tokens overlap')
    binding = snapshot.get('availability_bits')
    operational = snapshot.get('operational_bits')
    count = status[2]
    if (type(binding) is not str or type(operational) is not str or
            len(binding) != count or len(operational) != count or
            re.fullmatch(r'[01]+', binding) is None or
            re.fullmatch(r'[01]+', operational) is None or
            any(op == '1' and bit != '1' for bit, op in zip(binding, operational))):
        raise ValueError('invalid registry snapshot bits')
    if (any(type(snapshot.get(name)) is not int for name in
            ('available_count', 'unavailable_count', 'operational_count')) or
            snapshot['available_count'] != binding.count('1') or
            snapshot['unavailable_count'] != binding.count('0') or
            snapshot['operational_count'] != operational.count('1')):
        raise ValueError('registry snapshot totals differ from bits')
    return {'epoch': status[1], 'run_id': marker, 'nonce': nonce,
            'issued_count': count, 'binding_bits': binding,
            'operational_bits': operational}


def extend_full_inspect(text, *, software_rendered=False):
    """Run the read-only full owner before the legacy header receipt."""
    declaration = 'local registry_status=nil;local registry_pause_requested=false;local registry_pause_confirmed=false\n'
    if text.count(declaration) != 1:
        raise ValueError('full inspect requires the registry load gate')
    text = text.replace(declaration, declaration + 'local full_registry_inspect=nil\n')
    hook = '  registry_pause_confirmed=true\n  if not registry_status then\n'
    if text.count(hook) != 1:
        raise ValueError('full inspect hook unavailable')
    code = '''  if not full_registry_inspect then
   full_registry_inspect=assert(X4LAN_AUTHORITY_CLIENT_REGISTRY_INSPECT.new_x4(
    cfg.authority_registry_snapshot,engine,function()
     failed=true;DebugError('[X4LAN] AUTH_DOWNLOAD_LOAD ERROR')
    end))
  end
  assert(full_registry_inspect:tick())
  if not full_registry_inspect:ready() then return end
'''
    if software_rendered:
        code = code.replace('    end))', '    end,300))')
    text = text.replace(hook, '  registry_pause_confirmed=true\n' + code + '  if not registry_status then\n')
    header_log = "log=function(marker,status)DebugError('[X4LAN] AUTH_RECOVERY_REGISTRY '.."
    if text.count(header_log) != 1:
        raise ValueError('legacy registry header log unavailable')
    text = text.replace(header_log,
                        "log=function(marker,status)assert(full_registry_inspect:guard());DebugError('[X4LAN] AUTH_RECOVERY_REGISTRY '..")
    ready_hook = '  if not registry_status:ready() then return end\n'
    if text.count(ready_hook) != 1:
        raise ValueError('legacy registry readiness hook unavailable')
    return text.replace(ready_hook, ready_hook +
                        "  assert(full_registry_inspect:guard(),'full registry guard lost before admission')\n")


def extend_load_probe(text, nonce, status, *, load_gate='identity'):
    """Add the existing paused registry check before the load pause receipt."""
    _check_load_gate(load_gate)
    registry_receipt(nonce, status)
    expected = registry_status_text(status)
    hook = "local state='initial';local failed=false\n"
    if text.count(hook) != 1: raise ValueError('unexpected client load owner')
    text = text.replace(hook, hook+'local registry_status=nil;local registry_pause_requested=false;local registry_pause_confirmed=false\n')
    if load_gate == 'marker' and 'X4LAN_IDENTITY_READY' in text:
        raise ValueError('marker registry gate requires an identity-free load probe')
    hook = ('  if not X4LAN_IDENTITY_READY() then return end\n' if load_gate == 'identity' else
            '  if not X4LAN_AUTHORITY_MARKER_READY() then return end\n')
    if text.count(hook) != 1: raise ValueError('client ' + load_gate + ' gate unavailable')
    code = '''  if not engine.is_paused() then
   if registry_pause_confirmed then error('client registry pause lost') end
   if not registry_pause_requested then registry_pause_requested=true;engine.pause() end
   return
  end
  registry_pause_confirmed=true
  if not registry_status then
   registry_status=assert(X4LAN_RECOVERY_REGISTRY_STATUS.new({nonce='NONCE',expected='EXPECTED',
    clock=GetCurRealTime,paused=engine.is_paused,
    register=function(f)return RegisterEvent('X4AuthorityShipRegistry.Correlated',f)~=false end,
    issue=function(screen,op,args)return AddUITriggeredEvent(screen,op,args)~=false end,
    log=function(marker,status)DebugError('[X4LAN] AUTH_RECOVERY_REGISTRY '..'NONCE'..'|'..marker..'|'..status);return true end,
    fault=function()DebugError('[X4LAN] AUTH_RECOVERY_REGISTRY_ERROR '..'NONCE') end,
    store=function()return false end}))
   assert(registry_status:begin())
  end
  assert(registry_status:tick())
  if not registry_status:ready() then return end
'''.replace('NONCE', nonce).replace('EXPECTED', expected)
    updated_probe = text.replace(hook, hook+code)
    return updated_probe
