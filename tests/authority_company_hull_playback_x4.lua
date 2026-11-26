local module = dofile('src/authority/lua/company_hull_playback_x4.lua')
local epoch = ('1'):rep(32)
local binding = {slot='cockpit', epoch=epoch, index=73, actor='0x02', sector_macro='sector_a', code='ABC-123'}
local cockpit, target, sector, zone, cockpit_zone = 1, 2, 3, 4, 5
local owner, docked, docking, stored, pilot, assigned, ai
local controlled, occupied, contained, cockpit_sector, target_sector, target_zone, destination_zone
local player, paused, observed, guarded, writes, actual, requested, fault_write

local c = {
    IsPlayerValid = function() return player end,
    IsGamePaused = function() return paused end,
    IsValidComponent = function(id) return id ~= 0 end,
    IsComponentOperational = function(id) return id == target end,
    IsComponentClass = function(id, kind)
        return ({[1]='ship', [2]='ship', [3]='sector', [4]='zone', [5]='zone', [6]='sector'})[id] == kind
    end,
    GetPlayerControlledShipID = function() return controlled end,
    GetPlayerOccupiedShipID = function() return occupied end,
    GetPlayerContainerID = function() return contained end,
    GetContextByClass = function(id, kind)
        if kind == 'sector' then
            return ({[1]=cockpit_sector, [2]=target_sector, [4]=target_sector, [5]=cockpit_sector})[id]
        end
        return ({[1]=cockpit_zone, [2]=target_zone})[id]
    end,
    GetObjectIDCode = function() return 'ABC-123' end,
    GetObjectPositionInSector = function(id) assert(id == target); return actual end,
    GetZoneAt = function(id, offset)
        assert(id == sector and offset[0].x == 23)
        offset[0].x = -100 -- As a zone conversion must not change the setter's sector coordinates.
        return destination_zone
    end,
    SetObjectSectorPos = function(id, where, pose)
        assert(id == target and where == sector)
        writes, requested = writes + 1, pose
        if fault_write then error('callback failed after dispatch') end
    end,
}

package.loaded.ffi = {
    C = c, NULL = {},
    typeof = function() return true end,
    cdef = function() end,
    string = function(value) return value end,
    new = function(class, value)
        if class == 'UIPosRot[1]' then return {[0]={}} end
        assert(class == 'UIPosRot')
        return value
    end,
}
X4LAN_RESOLVE_STRING = {
    parse_hex = function(text) return text == '0x02' and target end,
    actor_u64 = tonumber,
}
ConvertStringToLuaID = tostring
ConvertIDTo64Bit = tonumber
function GetComponentData(id, field)
    if id == tostring(sector) and field == 'macro' then return 'sector_a' end
    assert(id == tostring(target) and field == 'owner')
    return owner, docked, docking, stored, pilot, assigned, ai
end

local observer = {}
function observer:matches(seen_epoch, seen_sector, index, actor, code)
    return observed and seen_epoch == epoch and seen_sector == 'sector_a'
        and index == 73 and actor == '0x02' and code == 'ABC-123'
end
local cfg = {enabled=true, pinned={epoch=epoch, issued_count=10082}, own_index=17, observer=observer,
             guard=function() return guarded end}
assert(not pcall(module.new_x4, {enabled=false}))
local adapter = module.new_x4(cfg)

local function reset()
    owner, docked, docking, stored, pilot, assigned, ai = 'player', false, false, false, nil, nil, false
    controlled, occupied, contained = cockpit, cockpit, cockpit
    cockpit_sector, target_sector, target_zone, destination_zone = sector, sector, zone, zone
    player, paused, observed, guarded, fault_write = true, false, true, true, false
    writes, actual, requested = 0, {x=20, y=5, z=-7, yaw=.5, pitch=.1, roll=-.2}, nil
end
local destination = {x=23, y=5, z=-7, yaw=99, pitch=99, roll=99}

local native_readback = dofile('tests/authority_native_pose_readback.lua')

local function check_attitude(pose, original)
    for _, axis in ipairs({'yaw', 'pitch', 'roll'}) do
        assert(math.abs(pose[axis] - original[axis]) < 1e-6, axis .. ' changed during position correction')
    end
end

reset()
local session = assert(adapter:bind(binding))
local before = assert(session:read())
assert(before.x == 20 and session:write(destination, before))
assert(writes == 1 and requested.x == 23)
assert(session:read().x == 20, 'native readback must never use the requested destination')
actual = native_readback(requested)
assert(session:read().x == 23)
check_attitude(session:read(), before)
session:close()
assert(not session:guard() and session:read() == nil and not session:write(destination, before) and writes == 1)

reset()
actual.x = -100
session = assert(adapter:bind(binding))
local alignment_origin = assert(session:read())
assert(session:align(destination) and writes == 1)
actual = native_readback(requested)
assert(session:read().x == 23)
check_attitude(session:read(), alignment_origin)
session:close()

for _, change in ipairs({
    function() controlled = target end, function() occupied = target end, function() contained = target end,
    function() cockpit_sector = 6 end, function() target_sector = 6 end, function() target_zone = cockpit_zone end,
    function() owner = 'argon' end, function() docked = true end, function() docking = true end,
    function() stored = true end, function() docking = nil end, function() assigned = 'captain' end,
    function() pilot = 'captain' end, function() ai = true end, function() paused = true end,
    function() player = false end, function() observed = false end, function() guarded = false end,
}) do
    reset()
    session = assert(adapter:bind(binding))
    change()
    assert(not session:write(destination, before) and writes == 0 and not session:guard())
end

reset()
session = assert(adapter:bind(binding))
destination_zone = cockpit_zone
assert(not session:write(destination, before) and writes == 0)
destination_zone = zone
assert(not session:write({x=1e5, y=0, z=0}, before) and writes == 0)
assert(not session:write({x=0/0, y=0, z=0}, before) and writes == 0)
fault_write = true
assert(not session:write(destination, before) and writes == 1 and not session:guard())
assert(not session:write(destination, before) and writes == 1)

binding.slot = 'map'
assert(adapter:bind(binding) == nil)
binding.slot, binding.index = 'cockpit', cfg.own_index
assert(adapter:bind(binding) == nil)

-- Exercise the controller's velocity allowance through the native adapter.
local base = 'src/authority/lua/'
X4LAN_MOTION_BUFFER = dofile(base .. 'motion_buffer.lua')
dofile(base .. 'motion_playback.lua')
dofile(base .. 'company_hull_playback.lua')
local cockpit_playback = dofile(base .. 'company_cockpit_playback.lua')
local generation = ('2'):rep(32)
binding.slot, binding.index = 'cockpit', 73

local function flight(speed, update, test)
    local now, x, y, pending = 0, 0, 0, nil
    local native_offset, writes, faults = 0, {}, {}
    local engine = {
        cockpit = function() return {actor=cockpit, sector=sector, zone=cockpit_zone} end,
        target = function()
            return {actor=target, sector=sector, zone=zone, sector_macro=binding.sector_macro, code=binding.code}
        end,
        read = function()
            return {x=x, y=y + native_offset, z=0, yaw=0, pitch=0, roll=0}
        end,
        same_zone = function() return true end,
        write = function(actor, _, pose)
            assert(actor == target and not pending)
            pending = pose
            writes[#writes + 1] = math.sqrt((pose.x - x)^2 + (pose.y - y)^2)
            return true
        end,
    }
    reset()
    local controller = cockpit_playback.new({
        enabled=true, own_index=cfg.own_index, target_index=binding.index, adapter=module.new(cfg, engine),
        clock=function() return now end, game=function() return now end, paused=function() return false end,
        record=function(event)
            if test == 'native_offset' and event.kind == 'observation' and event.target.x - event.actual.x > 50 then
                -- The independent pre-write read sees an unexpected lateral displacement.
                native_offset = 120
            end
            return true
        end,
        fault=function(reason) faults[#faults + 1] = reason end,
    })
    local function sample(seq, age, velocity)
        return controller:sample(binding, 1, {
            version=3, epoch=epoch, generation=generation, index=binding.index, sector_macro=binding.sector_macro,
            sequence=seq, source_time=age, capture_time=age, x=speed*age, y=0, z=0, real_velocity=velocity,
        }, age)
    end
    local velocity = {x=speed, y=0, z=0}
    assert(sample(1, 0, velocity))
    for frame = 1, 16 do
        now = frame * update
        if pending then
            x, y, pending = pending.x, pending.y, nil
        end
        if test ~= 'one_sample' and (test ~= 'missing_velocity' or frame <= 4) then
            local rate = (test ~= 'missing_velocity' or frame ~= 4) and velocity or nil
            assert(sample(frame + 1, now, rate))
        end
        if frame == 6 and test == 'long_update' then now = now + .12 end
        controller:tick(now)
        if #faults > 0 then break end
    end
    return writes, faults
end

local accepted, rejected = flight(1500, .05)
assert(#accepted > 0 and math.max(unpack(accepted)) > 50 and #rejected == 0)
for _, test in ipairs({'one_sample', 'missing_velocity', 'long_update', 'native_offset'}) do
    accepted, rejected = flight(1500, .05, test)
    assert(#rejected == 1, test)
end
accepted, rejected = flight(4000, .08)
assert(#rejected == 1 and (#accepted == 0 or math.max(unpack(accepted)) <= 250))
