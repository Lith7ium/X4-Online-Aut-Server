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

-- Private measurement starts only from a verified radar response and never
-- starts again after loss, even when the normal observer reacquires.
code[key(npc)] = 'NPC-021'
local measurement_args, measurement_reply, measured = nil, nil, 0
local visible, replies = true, {}
local bounded = observer.new(pinned, 1, {
    enabled=true, target_index=20, target_code='NPC-021',
    clock=function() return now end,
    probe=function() return {mode='radar'} end,
    parse_actor=resolve.parse_hex,
    verify_actor=function() return visible and {actor=npc} or nil end,
    validate_actor=function() return visible end,
    register=function(handler) measurement_reply=handler; return true end,
    issue=function(args) measurement_args=args; return true end,
    measure=function(id, epoch, index, shipcode, actor, hull)
        assert(id == measurement_args.local_measurement and epoch == pinned.epoch and index == 20
            and shipcode == 'NPC-021' and resolve.parse_hex(actor) == npc and hull.actor == npc)
        measured = measured + 1
    end,
})
local function measurement_step()
    now = now + .17
    bounded:tick()
    local args = assert(measurement_args)
    replies[#replies + 1] = args.local_measurement
    measurement_reply(nil, table.concat({args.request_id, pinned.epoch, 'OBSERVED', '20',
        actor_text[key(npc)], frame[key(sector)], code[key(npc)], 'radar', '1000', '10000'}, '|'))
end
measurement_step()
assert(measured == 1 and replies[1] == 1)
visible = false
measurement_step()
assert(measured == 1 and measurement_args.local_measurement == nil)
visible = true
measurement_step()
assert(measured == 1 and measurement_args.local_measurement == nil)

-- Exercise the native reader's one-frame wire output and terminal radar loss.
local dispatched = {}
local native_api = package.loaded.ffi.C
native_api.GetObjectPositionInSector = function(id)
    assert(id == npc)
    return {x=10, y=20, z=30}
end
native_api.GetCurrentGameTime = function() return 568.125 end
frame[key(sector)], code[key(npc)] = 'cluster_06_sector001_macro', 'MFQ-419'
local measurement_fixture = {epoch=pinned.epoch, nonce=('d'):rep(32), issued_count=5048}
local measured_radar = observer.new_x4(measurement_fixture, 1073, function() return true end, {
    enabled=true, mode='radar', target_index=943, target_code='MFQ-419', local_measurement=true,
    clock=function() return now end,
    send_measurement=function(message) dispatched[#dispatched + 1] = message; return true end,
})
local function measured_step(suffix)
    now = now + .17
    measured_radar:tick()
    local request = assert(waiting.radar)
    local fields = table.concat({request.request_id, measurement_fixture.epoch, 'OBSERVED', '943',
        actor_text[key(npc)], frame[key(sector)], code[key(npc)], 'radar', '1000', '10000'}, '|')
    handlers[#handlers](nil, fields .. (suffix or ''))
    return request
end

local axes = '|0|0|100|0|0|1|0|1|0|1|0|0'
assert(measured_step('|MEASURED|2|inzone|568.125' .. axes).local_measurement == 1)
assert(#dispatched == 0) -- Mismatched request sequence.
assert(measured_step('|MEASURED|2|inzone|565' .. axes).local_measurement == 2)
assert(#dispatched == 0) -- Stale MD game clock.
assert(measured_step('|MEASURED|3|inzone|568.125' .. axes:gsub('100', '1e309')).local_measurement == 3)
assert(#dispatched == 0) -- Non-finite native rate.
assert(measured_step('|MEASURED|4|inzone|568.125' .. axes).local_measurement == 4)
assert(#dispatched == 1)
local point = {}
for field in dispatched[1]:gmatch('[^|]+') do point[#point + 1] = field end
assert(#point == 15 and point[1] == 'NPC_POINT' and point[2] == '943' and point[3] == 'MFQ-419'
    and point[4] == 'cluster_06_sector001_macro' and point[5] == actor_text[key(npc)]
    and point[6] == '4' and point[7] == 'inzone' and tonumber(point[8]) == now
    and point[9] == '568.125' and point[10] == '10' and point[11] == '20' and point[12] == '30'
    and point[13] == '100' and point[14] == '0' and point[15] == '0')
radar_visible = false
assert(measured_step().local_measurement == nil)
assert(dispatched[2] == 'NPC_END|943|MFQ-419')
radar_visible = true
assert(measured_step().local_measurement == nil)
measured_radar:close()
assert(#dispatched == 2) -- No reacquisition or repeated terminal frame.
