local module = dofile('src/authority/lua/company_hull_playback_x4.lua')
local epoch = ('1'):rep(32)
local binding = {slot='cockpit', epoch=epoch, index=73, actor='0x02', sector_macro='sector_a', code='ABC-123'}
local cockpit, target, sector, zone, cockpit_zone = 1, 2, 3, 4, 5
local own_zone
local owner, docked, docking, stored, pilot, assigned, ai
local controlled, occupied, contained, cockpit_sector, target_sector, target_zone, destination_zone
local player, paused, observed, guarded, writes, actual, requested, fault_write, active_pilot
local cockpit_pose, radar_range

local c = {
    IsPlayerValid = function() return player end,
    IsGamePaused = function() return paused end,
    IsValidComponent = function(id) return id ~= 0 end,
    IsComponentOperational = function(id) return id == target end,
    IsComponentClass = function(id, kind)
        return ({[1]='ship', [2]='ship', [3]='sector', [4]='zone', [5]='zone', [6]='sector', [7]='zone'})[id] == kind
    end,
    GetPlayerControlledShipID = function() return controlled end,
    GetPlayerOccupiedShipID = function() return occupied end,
    GetPlayerContainerID = function() return contained end,
    GetContextByClass = function(id, kind)
        if kind == 'sector' then
            return ({[1]=cockpit_sector, [2]=target_sector, [4]=target_sector, [5]=cockpit_sector, [7]=6})[id]
        end
        return ({[1]=own_zone, [2]=target_zone})[id]
    end,
    GetObjectIDCode = function() return 'ABC-123' end,
    GetObjectPositionInSector = function(id)
        if id == cockpit then return cockpit_pose end
        assert(id == target)
        return actual
    end,
    GetZoneAt = function(id, offset)
        assert(id == sector and type(offset[0].x) == 'number')
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
function observer:current_radar_range(index)
    if index == binding.index then return radar_range end
end
function observer:is_active_pilot(index)
    return active_pilot and index == binding.index
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
    active_pilot, own_zone = true, cockpit_zone
    cockpit_pose, radar_range = {x=0, y=5, z=-7, yaw=0, pitch=0, roll=0}, 5000
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
    function() own_zone = zone end, function() cockpit_sector = 6 end, function() target_sector = 6 end, function() target_zone = cockpit_zone end,
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
assert(not session:write(destination, before) and not session:align(destination) and writes == 0)
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

-- The engine keeps reporting the old zone until the following UI update.
-- Feed actual V3 samples through the regular receiving controller and FFI adapter.
local function cross_boundary(outcome)
    reset()
    local now, readbacks, faults = 0, 0, {}
    local controller = cockpit_playback.new({
        enabled=true, own_index=cfg.own_index, target_index=binding.index, adapter=adapter,
        clock=function() return now end, game=function() return now end, paused=function() return false end,
        record=function(event)
            if event.kind == 'readback' then readbacks = readbacks + 1 end
            return true
        end,
        fault=function(reason) faults[#faults + 1] = reason end,
    })

    local crossing_write, crossing_readback
    for frame = 1, 16 do
        now = frame * .05
        if requested then
            actual, requested = native_readback(requested), nil
        end
        if crossing_write and not crossing_readback then
            if outcome ~= 'wrong_actual_zone' then target_zone = cockpit_zone end
            if outcome == 'observer_changed_zone' then own_zone = zone end
            if outcome == 'wrong_actual_position' then actual.x = actual.x + 10 end
            if outcome == 'lost_radar' then observed = false end
            if outcome == 'pilot_disconnected' then active_pilot = false end
        end
        local before_writes, before_readbacks = writes, readbacks
        if readbacks >= 1 then
            if outcome == 'foreign_sector' then destination_zone = 7
            elseif outcome == 'unknown_zone' then destination_zone = 0
            else destination_zone = cockpit_zone end
            if outcome == 'unpiloted' then active_pilot = false end
        elseif outcome == 'first_write' then
            destination_zone = cockpit_zone
        end
        local sample = {
            version=3, epoch=epoch, index=binding.index, generation=generation, sector_macro=binding.sector_macro,
            sequence=frame, source_time=now, capture_time=now,
            x=20 + 100 * now, y=5, z=-7, real_velocity={x=100,y=0,z=0},
        }
        controller:sample(binding, 1, sample, now)
        controller:tick(now)
        if crossing_write and readbacks > before_readbacks then crossing_readback = true end
        if destination_zone ~= zone and writes > before_writes and not crossing_write then crossing_write = writes end
        if #faults > 0 then break end
    end
    if outcome == 'success' or outcome == 'observer_changed_zone' then
        assert(crossing_write and crossing_readback and writes > crossing_write and #faults == 0)
    elseif outcome == 'foreign_sector' or outcome == 'unknown_zone'
            or outcome == 'unpiloted' or outcome == 'first_write' then
        assert(not crossing_write and #faults > 0, outcome)
    else
        assert(crossing_write and not crossing_readback and writes == crossing_write and #faults > 0, outcome)
    end
end
for _, outcome in ipairs({'success', 'observer_changed_zone', 'first_write', 'unpiloted', 'unknown_zone', 'foreign_sector',
        'wrong_actual_zone', 'wrong_actual_position', 'lost_radar', 'pilot_disconnected'}) do
    cross_boundary(outcome)
end

local function flight(speed, update, test, trace, measured_attitude)
    local now, actual, pending = 0, {x=0, y=0, z=0, yaw=0, pitch=0, roll=0}, nil
    local accepted_samples, confirmed_sequence, confirmed_long_write = 0, 0, false
    local native_offset, writes, faults = 0, {}, {}
    local engine = {
        cockpit = function() return {actor=cockpit, sector=sector, zone=cockpit_zone} end,
        target = function()
            return {actor=target, sector=sector, zone=zone, sector_macro=binding.sector_macro, code=binding.code}
        end,
        read = function()
            return {x=actual.x, y=actual.y + native_offset, z=actual.z,
                yaw=actual.yaw, pitch=actual.pitch, roll=actual.roll}
        end,
        within_radar = function() return true end,
        destination_zone = function() return zone end,
        write = function(actor, _, pose)
            assert(actor == target and not pending)
            pending = pose
            writes[#writes + 1] = math.sqrt(
                (pose.x - actual.x)^2 + (pose.y - actual.y)^2 + (pose.z - actual.z)^2)
            return true
        end,
    }
    reset()
    local controller = cockpit_playback.new({
        enabled=true, own_index=cfg.own_index, target_index=binding.index, adapter=module.new(cfg, engine),
        trial_first_contact=test == 'first_contact_trace',
        clock=function() return now end, game=function() return now end, paused=function() return false end,
        record=function(event)
            if event.kind == 'readback' then
                assert(event.elapsed > 0 and event.error == 0)
                confirmed_sequence = event.source_sequence
                confirmed_long_write = confirmed_long_write or writes[#writes] > 50
            end
            if test == 'native_offset' and event.kind == 'observation' and event.target.x - event.actual.x > 50 then
                -- The independent pre-write read sees an unexpected lateral displacement.
                native_offset = 120
            end
            return true
        end,
        fault=function(reason) faults[#faults + 1] = reason end,
    })
    local function submit(seq, capture_time, game_time, pose, velocity)
        local accepted, reason = controller:sample(binding, 1, {
            version=measured_attitude and 4 or 3, epoch=epoch, generation=generation, index=binding.index,
            sector_macro=binding.sector_macro,
            sequence=seq, source_time=game_time, capture_time=capture_time,
            x=pose.x, y=pose.y, z=pose.z, real_velocity=velocity,
            attitude=measured_attitude and {yaw=capture_time, pitch=0, roll=0} or nil,
        }, capture_time)
        assert(accepted, reason)
        accepted_samples = accepted_samples + 1
    end
    local function synthetic_sample(seq, age, velocity)
        submit(seq, age, age, {x=speed*age, y=0, z=0}, velocity)
    end
    local velocity = {x=speed, y=0, z=0}
    local next_row, frames = 1, 16
    if trace then
        frames = math.ceil((trace[#trace][2] + .2) / update)
    else
        synthetic_sample(1, 0, velocity)
    end
    for frame = 1, frames do
        now = frame * update
        if pending then
            -- Expose the dispatched pose only on the subsequent receiver update.
            actual, pending = pending, nil
        end
        if trace then
            while trace[next_row] and trace[next_row][2] <= now do
                local seq, capture, game, x, y, z, vx, vy, vz = unpack(trace[next_row])
                local missing = test == 'first_contact_trace' and next_row == 1
                submit(seq, capture, game, {x=x, y=y, z=z}, not missing and {x=vx, y=vy, z=vz} or nil)
                next_row = next_row + 1
            end
        elseif test ~= 'one_sample' and (test ~= 'missing_velocity' or frame <= 4) then
            local rate = (test ~= 'missing_velocity' or frame ~= 4) and velocity or nil
            synthetic_sample(frame + 1, now, rate)
        end
        if frame == 6 and test == 'long_update' then now = now + .12 end
        controller:tick(now)
        if #faults > 0 then break end
    end
    return writes, faults, accepted_samples, confirmed_sequence, confirmed_long_write, actual.yaw
end

local accepted, rejected = flight(1500, .05)
assert(#accepted > 0 and math.max(unpack(accepted)) > 50 and #rejected == 0)
for _, test in ipairs({'one_sample', 'missing_velocity', 'long_update', 'native_offset'}) do
    accepted, rejected = flight(1500, .05, test)
    assert(#rejected == 1, test)
end
accepted, rejected = flight(4000, .08)
assert(#rejected == 1 and (#accepted == 0 or math.max(unpack(accepted)) <= 250))

do
    local writes, faults, _, _, confirmed_long_write, heading = flight(1500, .05, nil, nil, true)
    assert(#faults == 0 and confirmed_long_write and math.max(unpack(writes)) > 50)
    assert(heading > 0.4 and heading < 0.8)
    writes, faults = flight(1500, .05, 'native_offset', nil, true)
    assert(#faults == 1)
end


local trace = {}
local input = assert(io.open('tests/fixtures/company_gate_flight.tsv', 'r'))
assert(input:read('*l'):match('^sequence%s+relative_capture_s%s+relative_game_s'))
for line in input:lines() do
    local row = {}
    for column in line:gmatch('%S+') do row[#row + 1] = assert(tonumber(column)) end
    assert(#row == 9)
    trace[#trace + 1] = row
end
input:close()
assert(#trace == 44 and trace[#trace][1] == 192)
for _, hz in ipairs({20, 30}) do
    for _, mode in ipairs({'complete_rates', 'first_contact_trace'}) do
        local _, faults, samples, last_sequence, confirmed_long_write = flight(0, 1/hz, mode, trace)
        assert(#faults == 0 and samples == 44 and last_sequence == 192 and confirmed_long_write,
               mode .. ' at ' .. hz .. ' Hz')
    end
end

-- A verified local baseline followed by a coherent first velocity sample may
-- align once. This uses the regular receiving controller and the native FFI
-- adapter rather than calling an alignment setter directly.
local function first_contact(outcome)
    reset()
    own_zone = zone
    local enabled = outcome ~= 'disabled'
    local clock, records, faults, steps = 10, {}, {}, {}
    local controller = cockpit_playback.new({
        enabled=true, trial_first_contact=enabled, allow_reacquire_align=outcome == 'non_roster_baselines',
        own_index=cfg.own_index, target_index=binding.index, adapter=module.new_x4(cfg),
        clock=function() return clock end, game=function() return clock end, paused=function() return paused end,
        record=function(event) records[#records + 1] = event; return true end,
        fault=function(reason) faults[#faults + 1] = reason end,
    })
    local function sample(sequence, x, capture, rate, token)
        return {version=3, epoch=epoch, index=binding.index, generation=token or generation,
                sector_macro=binding.sector_macro, sequence=sequence, source_time=capture, capture_time=capture,
                x=x, y=5, z=-7, real_velocity=rate}
    end
    local function tick(at, apply)
        if requested and apply then
            actual, requested = native_readback(requested), nil
        end
        clock = at
        controller:tick(at)
    end
    local function found_readback(position)
        for _, event in ipairs(records) do
            if event.kind:find('readback', 1, true) and event.actual and event.actual.x == position
                    and event.elapsed and event.elapsed > 0 then
                return event
            end
        end
    end

    if outcome == 'non_roster_baselines' then
        active_pilot = false
        for sequence = 1, 2 do
            clock = 10 + sequence * .03
            assert(controller:sample(binding, 1, sample(sequence, 20, 1 + sequence * .03), clock))
            controller:tick(clock)
        end
        assert(writes == 0)
        controller:view(nil, 2)
        clock = 10.18
        assert(not controller:sample(binding, 3, sample(3, 191, 1.18, {x=1425,y=0,z=0}), clock))
        controller:tick(clock)
        assert(writes == 0, 'velocity-less non-roster baselines granted a large reacquisition write')
        return
    end

    local baseline_x = outcome == 'distant_baseline' and -31 or 20
    if outcome == 'pilot_absent' then active_pilot = false end
    controller:sample(binding, 1, sample(1, baseline_x, 1), clock)
    tick(10.001)
    assert(writes == 0, 'a velocity-less baseline cannot move a ship')
    local advance = outcome == 'step_limit' and 251 or 171
    local delta = outcome == 'capture_gap' and .251 or .114
    local arrival = outcome == 'arrival_gap' and 10.251 or 10.114
    local rate = outcome == 'lateral' and {x=0,y=1500,z=0} or {x=advance/delta,y=0,z=0}
    if outcome == 'pilot_expired' then active_pilot = false end
    if outcome == 'view_withdrawn' then observed = false end
    if outcome == 'destination_out_of_radar' then radar_range = 150 end
    if outcome == 'baseline_out_of_radar' then radar_range, cockpit_pose.x = 50, 190 end
    if outcome == 'wrong_zone' then destination_zone = cockpit_zone end
    if outcome == 'wrong_sector' then destination_zone = 7 end
    local next_sample = sample(2, 20 + advance, 1 + delta, rate,
                               outcome == 'generation_changed' and ('3'):rep(32) or nil)
    clock = arrival
    controller:sample(binding, 1, next_sample, arrival)
    tick(arrival)

    if outcome ~= 'confirmed' and outcome ~= 'wrong_readback' and outcome ~= 'expired_readback' then
        for i = 1, 4 do tick(arrival + i * .03) end
        assert(writes == 0, outcome .. ' wrote an unapproved native alignment')
        return
    end

    assert(writes == 1 and actual.x == 20 and requested.x == 191, outcome .. ' missed the bounded first contact')
    assert(not found_readback(191), 'the requested pose was treated as an actual native readback')
    check_attitude(native_readback(requested), actual)

    if outcome == 'expired_readback' then
        tick(arrival + 5.01, true)
        assert(actual.x == 191 and not found_readback(191) and faults[1] == 'first_contact_readback_expired',
               'a correct pose read after expiry confirmed an uncertain first contact')
        clock = arrival + 5.02
        assert(not controller:sample(binding, 1, sample(3, 210, 1 + delta + 5.02, {x=200,y=0,z=0}), clock))
        tick(clock + .03)
        assert(writes == 1, 'the controller wrote again after the first-contact readback expired')
        return
    end

    if outcome == 'wrong_readback' then
        actual = native_readback(requested)
        requested, actual.x = nil, 201
        tick(arrival + .05)
        assert(#faults > 0 and not found_readback(191), 'wrong actual pose confirmed an alignment')
        clock = arrival + .06
        controller:sample(binding, 1, next_sample, clock)
        tick(arrival + .10)
        assert(writes == 1, 'uncertain native write was repeated')
        return
    end

    tick(arrival + .05, true)
    local confirmed = found_readback(191)
    assert(confirmed and confirmed.source_sequence == 2 and writes == 1 and #faults == 0,
           'native first contact requires independent readback on a later update')
    clock = arrival + .06
    controller:sample(binding, 1, next_sample, clock)
    tick(arrival + .07)
    assert(writes == 1, 'duplicate velocity sample reissued first-contact alignment')

    for frame = 1, 9 do
        if requested then
            steps[#steps + 1] = math.abs(requested.x - actual.x)
            actual, requested = native_readback(requested), nil
        end
        clock = arrival + .07 + frame * .05
        controller:sample(binding, 1, sample(2 + frame, 191 + frame * 10, 1 + delta + frame * .05,
                          {x=200,y=0,z=0}), clock)
        controller:tick(clock)
    end
    local regular_readback = false
    for _, event in ipairs(records) do
        if event.kind == 'readback' and event.source_sequence > 2 and event.elapsed > 0 then
            regular_readback = true
            break
        end
    end
    assert(writes > 1 and #faults == 0 and #steps > 0 and math.max(unpack(steps)) <= 50 and regular_readback,
           'verified first contact did not continue through ordinary playback')
end

for _, outcome in ipairs({'confirmed', 'disabled', 'distant_baseline', 'step_limit', 'lateral', 'generation_changed',
        'capture_gap', 'arrival_gap', 'pilot_absent', 'pilot_expired', 'view_withdrawn', 'destination_out_of_radar',
        'baseline_out_of_radar', 'wrong_zone', 'wrong_sector', 'wrong_readback', 'expired_readback',
        'non_roster_baselines'}) do
    first_contact(outcome)
end
