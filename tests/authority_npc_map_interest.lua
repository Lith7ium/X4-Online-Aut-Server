-- Exercise the real map sampler, NPC observer and stream with local native getters simulated.
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
X4LAN_RESOLVE_STRING = resolve
local own = resolve.parse_hex('0x20000000000011')
local npc = resolve.parse_hex('0x20000000000021')
local unrelated = resolve.parse_hex('0x20000000000031')
local sector = resolve.parse_hex('0x20000000000041')
local unrelated_sector = resolve.parse_hex('0x20000000000051')
local zero, map_id = ffi.new('UniverseID', 0), ffi.new('UniverseID', 9)
local function key(id)
    return (tostring(id):gsub('ULL$', ''))
end
local by_decimal = {}
for _, id in ipairs({own, npc, unrelated, sector, unrelated_sector}) do
    by_decimal[key(id)] = id
end
local actor_text = {[key(npc)]='0x20000000000021', [key(unrelated)]='0x20000000000031'}
local space = {[key(own)]=sector, [key(npc)]=sector, [key(unrelated)]=unrelated_sector}
local frame = {[key(sector)]='cluster_01_sector001_macro', [key(unrelated_sector)]='cluster_06_sector001_macro'}
local code = {[key(npc)]='NPC-021', [key(unrelated)]='NPC-031'}
local menu = {name='MapMenu', holomap=map_id, picking=true, shown=false}
Menus = {menu}
local selected, picked, viewed = {npc}, npc, sector
local rendered = {npc, unrelated}
local handlers, waiting, requests = {}, {}, {radar=0, map=0}
local now = 0
local radar_visible, known = true, true
local radar_distance, status = 1000, {}
DebugError = function(row) status[#status + 1] = row end

ConvertStringToLuaID = function(id) return by_decimal[id] end
ConvertIDTo64Bit = function(id) return id end
GetComponentData = function(id, field)
    if field == 'macro' then return frame[key(id)] end
    if field == 'maxradarrange' and id == own then return 10000 end
    if field == 'isknown' then return known, radar_visible end
    return 'argon', false, false, false, true
end
RegisterEvent = function(event, handler)
    assert(event == 'X4AuthorityNpcCockpitObserver.Result')
    handlers[#handlers + 1] = handler
end
AddUITriggeredEvent = function(screen, action, args)
    assert(screen == 'X4AuthorityNpcCockpitObserver' and action == 'Read')
    waiting[args.mode] = args
    requests[args.mode] = requests[args.mode] + 1
end
package.loaded.ffi = {cdef=function(def) if def:find('typedef', 1, true) then ffi.cdef(def) end end,
    typeof=ffi.typeof, new=ffi.new, cast=ffi.cast, NULL=ffi.NULL, string=function(value) return value end,
    C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return false end,
        IsValidComponent=function(id) return id ~= zero end, IsComponentOperational=function() return true end,
        IsComponentClass=function(id, class)
            if class == 'sector' then return frame[key(id)] ~= nil end
            return class == 'ship' and space[key(id)] ~= nil
        end,
        GetPlayerControlledShipID=function() return own end, GetPlayerOccupiedShipID=function() return own end,
        GetPlayerContainerID=function() return own end, GetContextByClass=function(id) return space[key(id)] end,
        GetObjectIDCode=function(id) return code[key(id)] end, GetDistanceBetween=function() return radar_distance end,
        GetNumMapSelectedComponents=function(id) assert(id == map_id); return #selected end,
        GetMapSelectedComponents=function(out, count, id)
            assert(id == map_id and count == 1); out[0] = selected[1]; return #selected
        end,
        GetNumMapRenderedComponents=function(id) assert(id == map_id); return #rendered end,
        GetMapRenderedComponents=function(out, count, id)
            assert(id == map_id and count == #rendered)
            for i, actor in ipairs(rendered) do out[i - 1] = actor end
            return count
        end,
        GetPickedMapComponent=function(id) assert(id == map_id); return picked end,
        GetMapPositionOnEcliptic2=function(id) assert(id == map_id); return viewed end,
    },
}

dofile('src/authority/lua/company_observer_target.lua')
dofile('src/authority/lua/map_render_observation.lua')
local observer = dofile('src/authority/lua/npc_cockpit_observer.lua')
local stream_module = dofile('src/authority/lua/company_observer_stream.lua')
local pinned = {epoch=('a'):rep(32), nonce=('b'):rep(32), issued_count=50}
local function make(mode)
    return assert(observer.new_x4(pinned, 1, function() return true end, {
        enabled=true, target_index=20, target_code='NPC-021', clock=function() return now end, mode=mode,
    }))
end
local radar, map = make('radar'), make('map')
local published = {}
local stream = stream_module.new({cockpit=radar, map=map}, pinned.epoch, {
    send=function(_, packet)
        local slot, index = packet:match('^VIEW|([^|]+)|[^|]+|[^|]+|[^|]+|(%d+)|')
        if slot then published[slot] = tonumber(index) end
        return true
    end,
    receive=function() return nil end,
}, '', function() return now end)

local function reply(mode, actor)
    local request = assert(waiting[mode])
    waiting[mode] = nil
    if mode == 'radar' then assert(not request.target and not request.view_space) end
    local value = table.concat({request.request_id, pinned.epoch, 'OBSERVED', '20',
        actor_text[key(actor)], frame[key(space[key(actor)])], code[key(actor)], mode, '1000', '10000'}, '|')
    for _, handler in ipairs(handlers) do handler(nil, value) end
end
local function step()
    now = now + .16
    stream:tick(true)
    if waiting.radar then reply('radar', npc) end
    stream:tick(true)
    assert(published.cockpit == 20)
end

step() -- A closed map cannot use stale selections.
assert(requests.map == 0 and published.map == 0)
assert(#status == 1 and status[1]:find('VISIBLE|radar|20|NPC-021|verified|cluster_01_sector001_macro|distance=1000|range=10000', 1, true))
menu.shown, selected = true, {}
step() -- An open map with no selection is not interest.
assert(requests.map == 0 and published.map == 0)
selected, picked, viewed = {npc}, npc, unrelated_sector
step()
assert(requests.map == 0 and published.map == 0)
viewed, picked = sector, unrelated
step()
assert(requests.map == 0 and published.map == 0)

selected, picked, viewed = {unrelated}, unrelated, unrelated_sector
step()
assert(waiting.map.target == unrelated)
reply('map', npc) -- An unrelated selected ship cannot expose the configured NPC.
stream:tick(true)
assert(published.map == 0 and published.cockpit == 20)

selected, picked, viewed = {npc}, npc, sector
step()
assert(waiting.map.target == npc and waiting.map.view_space == sector)
picked = unrelated
reply('map', npc) -- A delayed receipt loses its local target before publication.
stream:tick(true)
assert(published.map == 0 and published.cockpit == 20)

picked = npc
step()
reply('map', unrelated) -- A remapped registry index cannot substitute another actor.
stream:tick(true)
assert(published.map == 0 and published.cockpit == 20)

step()
reply('map', npc)
stream:tick(true)
assert(published.map == 20 and published.cockpit == 20)
assert(#status == 2 and status[2]:find('VISIBLE|map|20|NPC-021|verified', 1, true))
menu.shown = false
step()
assert(published.map == 0 and published.cockpit == 20)
assert(#status == 3 and status[3]:find('END|map|20|NPC-021|map_pick_unavailable', 1, true))

-- A pilot outside radar visibility cannot attest a reading for this ship.
radar_visible = false
stream:tick(true)
assert(published.cockpit == 0)
assert(#status == 4 and status[4]:find('END|radar|20|NPC-021|native_radar_hidden', 1, true))
stream:tick(true)
assert(#status == 4)
radar_visible = true
step()
known = false
stream:tick(true)
assert(published.cockpit == 0)
assert(#status == 6 and status[6]:find('END|radar|20|NPC-021|native_ship_unknown', 1, true))
known = true
step()
radar_distance = 12000
stream:tick(true)
assert(published.cockpit == 0 and #status == 8)
assert(status[8]:find('END|radar|20|NPC-021|radar_out_of_range|cluster_01_sector001_macro|distance=12000|range=10000', 1, true))
radar_distance = 1000
step()
code[key(npc)] = 'NPC-999'
now = now + .16
stream:tick(true)
reply('radar', npc)
stream:tick(true)
assert(published.cockpit == 0)
assert(#status == 10 and status[10]:find('END|radar|20|NPC-021|code_mismatch', 1, true))
stream:close()
assert(#status == 10)
for _, row in ipairs(status) do assert(not row:find(pinned.nonce, 1, true)) end
