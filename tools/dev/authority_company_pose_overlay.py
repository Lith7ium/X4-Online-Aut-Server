"""Install an opt-in company pose owner in an isolated recovered X4 overlay."""
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from authority_load_live import lua_literal

ROOT = Path(__file__).resolve().parents[2]
_CUE = 'company_ship_pose.xml'
_UI = 'company_ship_pose.lua'
_TOKEN = re.compile(r'[0-9a-f]{32}')
_DECLARATION = 'local registry_checkpoint, registry_checkpoint_pause_time, registry_checkpoint_started\n'
_GATE = ' if not activated and not registry_checkpoint:ready() then return end\n'
_UDP = "  net=checked_control_socket(assert(lib.open('127.0.0.1',cfg.authority_control_port)))\n"
_TICK = ' local ok,reason=bridge:update();assert(ok,reason)\n'
_MANIFEST_HOOK = '    <file name="ui/authority_world_bridge_probe.lua" />\n'


def _replace_once(text, old, replacement):
    if text.count(old) != 1:
        raise ValueError('missing or ambiguous company pose bridge hook')
    return text.replace(old, replacement)


def _read_only_cue(data):
    root = ET.fromstring(data)
    libraries = root.findall("./cues/library[@name='ApplyPose']")
    if len(libraries) != 1:
        raise ValueError('expected one company pose library')
    actions = libraries[0].findall('actions')
    if len(actions) != 1:
        raise ValueError('expected one company pose action body')
    actions[0].clear()
    ET.SubElement(actions[0], 'set_value', {'name': '$actor', 'exact': "'0'"})
    ET.SubElement(actions[0], 'set_value', {'name': '$status', 'exact': "'WRITE_DISABLED'"})
    if any(tag.tag.rsplit('}', 1)[-1] == 'warp' for tag in root.iter()):
        raise ValueError('read-only company pose cue retains a warp')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def install(case, profile, *, port, lease, epoch, writable):
    """Return the protected overlay paths for one recovered, verified registry."""
    case, profile = Path(case), Path(profile)
    save = profile / 'save'
    lab = case / 'extensions/x4_lan_lab'
    if (not case.is_absolute() or profile != case / 'home/.config/EgoSoft/X4/45841'
            or not save.is_dir() or save.resolve(strict=True) != save
            or not lab.is_dir() or lab.resolve(strict=True) != lab):
        raise ValueError('company pose requires the exact disposable profile and extension')
    if (type(port) is not int or not 1 <= port <= 65535 or type(writable) is not bool
            or not all(type(value) is str and _TOKEN.fullmatch(value) for value in (lease, epoch))
            or lease == epoch):
        raise ValueError('invalid company pose configuration')

    ui = lab / 'ui'
    probe_path = ui / 'authority_world_bridge_probe.lua'
    manifest_path = lab / 'ui.xml'
    config_path = ui / 'config.lua'
    if any(path.resolve(strict=True) != path for path in (ui, lab / 'md', probe_path,
                                                        manifest_path, config_path)):
        raise ValueError('company pose overlay paths must belong to the owned extension')
    save_literal = lua_literal(str(save))
    config = config_path.read_text(encoding='ascii')
    if config.count(f'authority_load_save_path={save_literal},') != 1:
        raise ValueError('company pose save path differs from the recovery bridge')
    probe = probe_path.read_text(encoding='ascii')
    manifest = manifest_path.read_text(encoding='ascii')
    if (probe.count(_GATE) != 1 or probe.count(f'enabled=true, epoch="{epoch}", issued_count=') != 1
            or 'company_pose' in probe):
        raise ValueError('company pose requires the installed full registry gate for this epoch')
    probe = _replace_once(probe, _DECLARATION, _DECLARATION + 'local company_pose, company_pose_udp\n')
    probe = _replace_once(probe, _UDP, _UDP + '  company_pose_udp=lib\n')
    hook = f''' if not company_pose and registry_checkpoint:ready() then
  company_pose=assert(X4LAN_AUTHORITY_COMPANY_SHIP_POSE.new_x4(
   {{port={port},lease="{lease}",epoch="{epoch}",allow_apply={str(writable).lower()}}},
   function() return registry_checkpoint:ready() end, {save_literal}, company_pose_udp))
 end
 if company_pose then company_pose:tick() end
'''
    probe = _replace_once(probe, _TICK, _TICK + hook)

    sources = {
        ui / 'authority_registry_resolve_string.lua': ROOT / 'tools/dev/authority_registry_resolve_string.lua',
        ui / 'replica_motion_binding.lua': ROOT / 'src/authority/lua/replica_motion_binding.lua',
        ui / _UI: ROOT / 'src/authority/lua' / _UI,
    }
    contents = {}
    manifest_entries = []
    for path, source in sources.items():
        data = source.read_bytes()
        entry = f'    <file name="ui/{path.name}" />\n'
        if path.exists() or path.is_symlink():
            if (path.name == _UI or path.is_symlink() or not path.is_file()
                    or path.read_bytes() != data or manifest.count(entry) != 1):
                raise ValueError('incompatible existing company pose dependency')
        else:
            if entry in manifest:
                raise ValueError('missing existing company pose dependency')
            contents[path] = data
            manifest_entries.append(entry)
    manifest = _replace_once(manifest, _MANIFEST_HOOK, ''.join(manifest_entries) + _MANIFEST_HOOK)

    cue = lab / 'md' / _CUE
    if cue.exists() or cue.is_symlink():
        raise ValueError('company pose cue already present')
    data = (ROOT / 'src/authority/md' / _CUE).read_bytes()
    contents[cue] = data if writable else _read_only_cue(data)

    for path, body in contents.items():
        with path.open('xb') as output:
            output.write(body)
    probe_path.write_text(probe, encoding='ascii')
    manifest_path.write_text(manifest, encoding='ascii')
    return [*sources, cue, probe_path, manifest_path]


def archive_cue(case, out):
    """After owned X4 stops, preserve the diagnostic cue outside the extension."""
    case, out = Path(case), Path(out)
    if case != out / 'case':
        raise ValueError('company pose archive must belong to the stopped run')
    source = case / 'extensions/x4_lan_lab/md' / _CUE
    if not source.is_file() or source.is_symlink() or source.resolve(strict=True) != source:
        raise ValueError('missing ordinary company pose cue')
    destination = out / _CUE
    os.link(source, destination, follow_symlinks=False)
    source.unlink()
    return destination
