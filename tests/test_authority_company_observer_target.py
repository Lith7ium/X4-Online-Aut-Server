"""Read-only target observation contract. The native adapter uses a simulated game API."""
from pathlib import Path
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class CompanyObserverTargetTests(unittest.TestCase):
    def test_md_looks_up_only_an_attended_target_in_both_registry_directions(self):
        script = ET.parse(ROOT / 'src/authority/md/company_observer_target.xml').getroot()
        self.assertEqual({node.tag for node in script.iter()}, {
            'mdscript', 'cues', 'cue', 'conditions', 'event_ui_triggered', 'actions',
            'set_value', 'do_if', 'raise_lua_event',
        })
        self.assertTrue(all(node.get('name').startswith('$') for node in script.iter('set_value')))
        self.assertIn('@player.target', {node.get('exact') for node in script.iter('set_value')})
        guards = ' '.join(node.get('value') for node in script.iter('do_if'))
        for field in ('State.$epoch == $args.$epoch', 'State.$last == $args.$count', 'not md.X4AuthorityShipRegistry.State.$faulted',
                      '$target.owner == faction.player', '$target.isoperational', '$target != player.occupiedship',
                      '$target != player.controlled', '$target.sector == player.sector',
                      '$target.attention ge attention.visible', '$target.isphysicsready',
                      'State.$ships.{$registered} == $target', '$registered != $args.$own'):
            self.assertIn(field, guards)
        reverse = script.find(".//set_value[@name='$registered']")
        self.assertEqual(reverse.get('exact'), '@md.X4AuthorityShipRegistry.State.$byship.{$target}')

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_native_adapter_needs_current_cockpit_visibility_and_matching_md_receipt(self):
        script = r'''
local ffi = require('ffi')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
X4LAN_RESOLVE_STRING = resolve
local own = resolve.parse_hex('0x20000000000030')
local remote = resolve.parse_hex('0x20000000000019')
local other = resolve.parse_hex('0x20000000000028')
local sector = resolve.parse_hex('0x20000000000040')
local zero = ffi.new('uint64_t', 0)
local actor_text = '0x20000000000019'
local sector_macro, code = 'cluster_06_sector002_macro', 'WRN-302'
local by_decimal = {}
for _, id in ipairs({own, remote, other, sector}) do
    by_decimal[(tostring(id):gsub('ULL$', ''))] = id
end

local now, requested, receipt, target = 0, nil, nil, remote
local ready, hud, on_screen, operational, known, radar_visible, stored = true, true, true, true, true, true, false
local owner, external, occupied, paused = 'player', false, own, false
local requests = 0
Menus = {{name='MapMenu'}}
GetCurRealTime = function() return now end
GetPlayerTarget = function() return target end
ConvertStringToLuaID = function(key) return by_decimal[key] end
ConvertIDTo64Bit = function(id) return id end
GetComponentData = function(id)
    if id == sector then return sector_macro end
    return owner, known, radar_visible, stored
end
RegisterEvent = function(event, handler)
    assert(event == 'X4AuthorityCompanyObserverTarget.Result')
    receipt = handler
end
AddUITriggeredEvent = function(screen, action, args)
    assert(screen == 'X4AuthorityCompanyObserverTarget' and action == 'Read')
    assert(args.count == 7354 and args.own == 1970)
    requests = requests + 1
    requested = args
end
package.loaded.ffi = {cdef=function() end, typeof=function() return true end, NULL={},
    string=function(value) return value end, C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return paused end,
        IsHUDActive=function() return hud end, IsExternalTargetMode=function() return false end,
        IsExternalViewActive=function() return external end, IsValidComponent=function(id) return id ~= zero end,
        IsComponentOperational=function() return operational end, IsComponentClass=function(id, kind)
            return (kind == 'sector') == (id == sector)
        end,
        GetPlayerControlledShipID=function() return own end, GetPlayerOccupiedShipID=function() return occupied end,
        GetPlayerContainerID=function() return own end, GetContextByClass=function() return sector end,
        GetObjectIDCode=function() return code end,
        GetComponentScreenPosition=function() return {onScreen=on_screen} end,
    },
}

local module = dofile('src/authority/lua/company_observer_target.lua')
local epoch = ('a'):rep(32)
local observer = module.new_x4({epoch=epoch, nonce=('b'):rep(32), issued_count=7354}, 1970,
                               function() return ready end)

local function result(req, index, actor)
    return table.concat({req.request_id, epoch, 'OBSERVED', index or '7354',
                         actor or actor_text, sector_macro, code}, '|')
end
local function inspect_and_accept()
    local before = requests
    observer:tick()
    assert(requests == before + 1)
    receipt(nil, result(requested))
    assert(observer:matches(epoch, sector_macro, 7354, actor_text, code))
end

observer:tick()
observer:tick()
assert(requests == 1 and observer:current() == nil)
receipt(nil, result(requested, '1970')) -- Never return the local selected company index.
assert(observer:current() == nil)
now = .16
inspect_and_accept()
local current = observer:current()
assert(current.epoch == epoch and current.sector_macro == sector_macro and current.index == 7354
       and current.code == code and current.actor == actor_text)
current.index = -1
assert(not observer:matches(epoch, sector_macro, 7354, '0x19', code))
assert(observer:current().index == 7354)

for _, block in ipairs({
    function(disable) hud = not disable end,
    function(disable) on_screen = not disable end,
    function(disable) known = not disable end,
    function(disable) radar_visible = not disable end,
    function(disable) operational = not disable end,
    function(disable) stored = disable end,
    function(disable) owner = disable and 'argon' or 'player' end,
    function(disable) Menus[1].shown = disable end,
    function(disable) external = disable end,
    function(disable) paused = disable end,
    function(disable) occupied = disable and other or own end,
    function(disable) target = disable and own or remote end,
    function(disable) ready = not disable end,
}) do
    block(true)
    local before = requests
    assert(observer:current() == nil and not observer:matches(epoch, sector_macro, 7354, actor_text, code))
    observer:tick()
    assert(requests == before)
    block(false)
    now = now + .16
    inspect_and_accept()
end

now = now + .16
observer:tick()
local obsolete = requested
target = other
receipt(nil, result(obsolete)) -- Target changed while MD was resolving.
assert(observer:current() == nil)
target = remote
now = now + .16
observer:tick()
assert(requested.request_id ~= obsolete.request_id)
receipt(nil, result(obsolete))
assert(observer:current() == nil)
receipt(nil, result(requested))
assert(observer:current() ~= nil)
now = now + .4
assert(observer:current() == nil)
observer:close()
observer:tick()
assert(observer:current() == nil)
'''
        subprocess.run(['luajit', '-'], input=script, text=True, cwd=ROOT, check=True)


if __name__ == '__main__':
    unittest.main()
