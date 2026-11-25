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
        target = script.find(".//set_value[@name='$target']")
        self.assertEqual(target.get('exact'), "if $mode == 'map' then @$args.$target else @player.target")
        radar = script.find(".//set_value[@name='$withinradar']")
        self.assertIn('$target.distanceto.{player.controlled} le player.controlled.currentradarrange', radar.get('exact'))
        guards = ' '.join(node.get('value') for node in script.iter('do_if'))
        for field in ('State.$epoch == $args.$epoch', 'State.$last ge $args.$count', '$registered le $args.$count', 'not md.X4AuthorityShipRegistry.State.$faulted',
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
ffi.cdef('typedef uint64_t UniverseID;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
X4LAN_RESOLVE_STRING = resolve
local own = resolve.parse_hex('0x20000000000030')
local remote = resolve.parse_hex('0x20000000000019')
local other = resolve.parse_hex('0x20000000000028')
local sector = resolve.parse_hex('0x20000000000040')
local sector_b = resolve.parse_hex('0x20000000000050')
local map_id = ffi.new('UniverseID', 9)
local zero = ffi.new('uint64_t', 0)
local actor_text = '0x20000000000019'
local sector_macro, code = 'cluster_06_sector002_macro', 'WRN-302'
local map_sector, map_code = 'cluster_01_sector001_macro', 'ZRV-341'
local rendered, selected = {remote, other}, {other}
local change_selection_on_fill = false
local by_decimal = {}
for _, id in ipairs({own, remote, other, sector, sector_b}) do
    by_decimal[(tostring(id):gsub('ULL$', ''))] = id
end

local now, requested, target = 0, nil, remote
local handlers = {}
local ready, hud, on_screen, operational, known, radar_visible, stored = true, true, true, true, true, true, false
local owner, external, occupied, paused = 'player', false, own, false
local distance, radar_range = 1000, 5000
local requests = 0
Menus = {{name='MapMenu', holomap=map_id}}
GetCurRealTime = function() return now end
GetPlayerTarget = function() return target end
ConvertStringToLuaID = function(key) return by_decimal[key] end
ConvertIDTo64Bit = function(id) return id end
GetComponentData = function(id, field)
    if id == sector then return sector_macro end
    if id == sector_b then return map_sector end
    if id == own and field == 'maxradarrange' then return radar_range end
    return owner, known, radar_visible, stored
end
RegisterEvent = function(event, handler)
    assert(event == 'X4AuthorityCompanyObserverTarget.Result')
    handlers[#handlers + 1] = handler
end
local function receipt(_, row)
    for _, handler in ipairs(handlers) do handler(nil, row) end
end
AddUITriggeredEvent = function(screen, action, args)
    assert(screen == 'X4AuthorityCompanyObserverTarget' and action == 'Read')
    assert(args.count == 7354 and args.own == 1970)
    requests = requests + 1
    requested = args
end
package.loaded.ffi = {cdef=function() end, typeof=function() return true end, NULL={},
    new=ffi.new, cast=ffi.cast,
    string=function(value) return value end, C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return paused end,
        IsHUDActive=function() return hud end, IsExternalTargetMode=function() return false end,
        IsExternalViewActive=function() return external end, IsValidComponent=function(id) return id ~= zero end,
        IsComponentOperational=function() return operational end, IsComponentClass=function(id, kind)
            return (kind == 'sector') == (id == sector or id == sector_b)
        end,
        GetPlayerControlledShipID=function() return own end, GetPlayerOccupiedShipID=function() return occupied end,
        GetPlayerContainerID=function() return own end,
        GetContextByClass=function(id) return id == other and sector_b or sector end,
        GetObjectIDCode=function(id) return id == other and map_code or code end,
        GetComponentScreenPosition=function() return {onScreen=on_screen} end,
        GetDistanceBetween=function(cockpit, actor)
            assert(cockpit == own and actor == remote)
            return distance
        end,
        GetNumMapSelectedComponents=function(id) assert(id == map_id); return #selected end,
        GetMapSelectedComponents=function(out, size, id)
            assert(size == 1 and id == map_id)
            out[0] = selected[1]
            return #selected
        end,
        GetNumMapRenderedComponents=function(id) assert(id == map_id); return #rendered end,
        GetMapRenderedComponents=function(out, size, id)
            assert(size == #rendered and id == map_id)
            for i, key in ipairs(rendered) do out[i-1] = key end
            if change_selection_on_fill then selected = {remote} end
            return size
        end,
    },
}

local module = dofile('src/authority/lua/company_observer_target.lua')
local epoch = ('a'):rep(32)
local pinned = {epoch=epoch, nonce=('b'):rep(32), issued_count=7354}
local guard = function() return ready end
local observer = module.new_x4(pinned, 1970, guard)

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

-- Opening the map can retain this HUD target only with its own renewed
-- cockpit radar and MD evidence. It must not use the map selection.
Menus[1].shown = true
on_screen, hud = false, false
observer:tick()
assert(requested.mode == 'hudmap' and requested.target == nil)
assert(observer:current() == nil)
receipt(nil, result(requested))
assert(observer:current().index == 7354)
distance = 6000
assert(observer:current() == nil)
distance = 1000
now = now + .16
local prior = requests
observer:tick()
assert(requests == prior) -- The invalidated HUD target cannot be reacquired from the map.
Menus[1].shown = false
on_screen, hud = true, true
inspect_and_accept()

dofile('src/authority/lua/map_render_observation.lua')
local map = dofile('src/authority/lua/company_observer_map.lua').new_x4(pinned, 1970, guard)
map:tick()
assert(map:current() == nil)
Menus[1].shown = true
now = now + .16
observer:tick()
local hud_request = requested
map:tick()
local map_request = requested
assert(hud_request.request_id ~= map_request.request_id)
assert(hud_request.mode == 'hudmap' and map_request.mode == 'map' and map_request.target == other)
receipt(nil, result(hud_request))
assert(observer:current().index == 7354 and map:current() == nil)
receipt(nil, table.concat({map_request.request_id, epoch, 'OBSERVED', 7200,
                           '0x20000000000028', map_sector, map_code}, '|'))
assert(observer:current().index == 7354 and map:current().index == 7200)
assert(map:matches(epoch, map_sector, 7200, '0x20000000000028', map_code))
assert(not map:matches(epoch, sector_macro, 7200, '0x20000000000028', map_code))
rendered = {remote}  -- Still selected, now absent from the raw map.
assert(map:current() == nil and observer:current().index == 7354)

now = now + .16
rendered, selected = {remote, other}, {remote, other}
prior = requests
map:tick()
assert(requests == prior)
selected = {other}
change_selection_on_fill = true
map:tick()
assert(requests == prior) -- Discard a selection changed within the map read.
change_selection_on_fill = false
selected = {own}
rendered = {own, remote, other}
map:tick()
assert(requests == prior)
Menus[1].shown = false
assert(map:current() == nil and observer:current() == nil)
observer:close()
map:close()
observer:tick()
map:tick()
assert(observer:current() == nil and map:current() == nil)
'''
        subprocess.run(['luajit', '-'], input=script, text=True, cwd=ROOT, check=True)


if __name__ == '__main__':
    unittest.main()
