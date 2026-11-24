local folder = 'src/authority/lua/'
X4LAN_MOTION_BUFFER = dofile(folder .. 'motion_buffer.lua')
dofile(folder .. 'motion_playback.lua')
dofile(folder .. 'company_hull_playback.lua')
local stream_module = dofile(folder .. 'company_observer_stream.lua')
local trial_module = dofile(folder .. 'company_hull_trial.lua')
local adapter_module = dofile(folder .. 'company_hull_playback_x4.lua')
local observer_module = dofile(folder .. 'company_observer_target.lua')

local epoch, generation = ('a'):rep(32), ('b'):rep(32)
local prefix = 'C|private|stream|'
local own_actor, remote_actor, other_actor, sector, zone = 1, 2, 3, 4, 5
local own_index, remote_index, other_index = 1970, 7354, 8010
local indices = {[own_actor]=own_index, [remote_actor]=remote_index, [other_actor]=other_index}
local codes = {[own_actor]='WOO-831', [remote_actor]='WRN-302', [other_actor]='ALT-123'}
local pinned = {epoch=epoch, issued_count=10082, nonce=('d'):rep(32)}
local state

local api = {
    IsPlayerValid = function() return true end,
    IsGamePaused = function() return state.paused end,
    IsValidComponent = function(id) return id == sector or id == zone or indices[id] ~= nil end,
    IsComponentOperational = function(id) return indices[id] ~= nil end,
    IsComponentClass = function(id, class)
        return (id == sector and class == 'sector') or (id == zone and class == 'zone')
            or (indices[id] ~= nil and class == 'ship')
    end,
    GetPlayerControlledShipID = function() return own_actor end,
    GetPlayerOccupiedShipID = function() return own_actor end,
    GetPlayerContainerID = function() return own_actor end,
    GetContextByClass = function(_, class) return class == 'sector' and sector or zone end,
    GetObjectIDCode = function(id) return codes[id] end,
    GetObjectPositionInSector = function(id) return assert(state.pose[id]) end,
    GetZoneAt = function(id, offset)
        assert(id == sector and type(offset[0].x) == 'number')
        return zone
    end,
    SetObjectSectorPos = function(id, space, pose)
        assert(id == remote_actor and space == sector, 'must never write the local or other hull')
        assert(pose.yaw == .1 and pose.pitch == .2 and pose.roll == .3)
        assert(not state.pending and not state.paused)
        local write = {actor=id, pose=pose, when=state.local_time}
        state.writes[#state.writes+1] = write
        state.pending = write
    end,
}
package.loaded.ffi = {
    C=api, NULL={}, typeof=function() return true end, cdef=function() end,
    string=function(v) return v end,
    new=function(kind, value)
        if kind == 'UIPosRot[1]' then return {[0]={}} end
        assert(kind == 'UIPosRot')
        return value
    end,
}
X4LAN_RESOLVE_STRING = {parse_hex=tonumber, actor_u64=tonumber}
ConvertStringToLuaID, ConvertIDTo64Bit = tostring, tonumber
function GetComponentData(id, field)
    if id == tostring(sector) and field == 'macro' then return 'sector_a' end
    assert(indices[tonumber(id)] and field == 'owner')
    return 'player', false, false, false, nil, nil, nil
end

local function pose(x)
    return {x=x, y=2, z=3, yaw=.1, pitch=.2, roll=.3}
end

local function wait_for_hull_then_lose_it(loss)
    state = {
        paused=false, visible=remote_actor, local_time=12, game_time=230,
        pose={[own_actor]=pose(90), [remote_actor]=pose(100), [other_actor]=pose(120)},
        writes={}, events={}, faults={}, incoming={}, sent={},
    }
    local function clock() return state.local_time end
    local function paused() return state.paused end
    local function target()
        local id = state.visible
        return {actor=id, space=sector, sector_macro='sector_a', code=codes[id], mode='hud'}
    end
    local handler
    local cockpit = observer_module.new(pinned, own_index, {
        scope='cockpit', clock=clock, target=target, confirm=function() end, parse_actor=tonumber,
        register=function(callback) handler=callback; return true end,
        issue=function(request)
            -- An MD receipt is emitted on a later frame, as in the loaded game.
            local id = state.visible
            state.md = table.concat({request.request_id, epoch, 'OBSERVED', indices[id],
                                      ('0x%x'):format(id), 'sector_a', codes[id]}, '|')
            return true
        end,
    })
    local adapter = adapter_module.new_x4({
        enabled=true, pinned=pinned, own_index=own_index, observer=cockpit,
        guard=function() return not paused() end,
    })
    local trial = trial_module.new({
        enabled=true, own_index=own_index, target_index=remote_index, adapter=adapter,
        clock=clock, game=function() return state.game_time end, paused=paused,
        record=function(event) state.events[#state.events+1] = event; return true end,
        fault=function(...) state.faults[#state.faults+1] = {...} end,
    })
    local stream = stream_module.new({
        cockpit=cockpit, map={tick=function() end, close=function() end},
    }, epoch, {
        receive=function() return table.remove(state.incoming, 1) end,
        send=function(_, wire) state.sent[#state.sent+1] = wire; return true end,
    }, prefix, clock, trial)

    local function frame(elapsed)
        state.local_time = state.local_time + elapsed
        if not paused() then state.game_time = state.game_time + elapsed end
        if state.pending then
            -- Movement becomes visible through the stock getter on a later UI update.
            state.pose[state.pending.actor] = state.pending.pose
            state.pending = nil
        end
        if state.md then
            local reply = state.md
            state.md = nil
            handler(nil, reply)
        end
        stream:tick(not paused())
    end

    local trace = {}
    local function deliver(sequence, x, source, capture, serial)
        trace[sequence] = {source=source, capture=capture, x=x}
        state.incoming[#state.incoming+1] = prefix .. table.concat({
            'REMOTE', 'cockpit', serial or 2, epoch, remote_index, generation, sequence, 'sector_a',
            x, 2, 3, 18, 0, 0, source, capture,
        }, '|')
    end

    frame(.04)
    frame(.04)
    assert(state.sent[#state.sent] == prefix .. 'VIEW|cockpit|2|' .. epoch .. '|sector_a|7354|0x2|WRN-302')
    deliver(41, 101, 375.12, 86000.12)
    frame(.04)
    frame(.11)
    deliver(42, 103.16, 375.16, 86000.24)
    frame(.04)
    frame(.10)
    deliver(43, 105.32, 375.19, 86000.36)
    frame(.04)
    frame(.06)
    assert(#state.faults == 0 and #state.writes >= 2, 'real sample flow must reach the stock setter')

    local receipts, readback_sequences = {}, {}
    local readbacks, moved, reconstructed = 0, false, false
    for _, wire in ipairs(state.sent) do
        local sequence = wire:match('|SEEN|cockpit|2|[^|]+|7354|[^|]+|(%d+)|')
        if sequence then receipts[tonumber(sequence)] = true end
    end
    for _, event in ipairs(state.events) do
        if event.kind == 'readback' then
            local write = state.writes[readbacks+1]
            readbacks = readbacks+1
            assert(write and event.local_time > write.when and event.elapsed > 0)
            assert(event.index == remote_index and event.generation == generation and event.serial == 2)
            local source = assert(trace[event.source_sequence])
            assert(receipts[event.source_sequence])
            assert(event.source_time == source.source and event.capture_time == source.capture)
            readback_sequences[event.source_sequence] = true
            assert(event.actual.x == write.pose.x and event.error <= 3)
            moved = moved or event.actual.x - 100 > 1
            reconstructed = reconstructed or math.abs(event.actual.x - source.x) > .01
        end
    end
    local distinct = 0
    for _ in pairs(readback_sequences) do distinct = distinct + 1 end
    assert(distinct >= 2 and moved and reconstructed)
    assert(receipts[41] and receipts[42] and receipts[43])

    local function wait_view(index, after_serial)
        for _ = 1, 8 do
            frame(.07)
            for _, wire in ipairs(state.sent) do
                local serial, selected = wire:match('|VIEW|cockpit|(%d+)|[^|]+|sector_a|(%d+)|')
                if tonumber(selected) == index and tonumber(serial) > after_serial then
                    return tonumber(serial)
                end
            end
        end
        error('new stock view did not resolve through the local registry')
    end

    local writes = #state.writes
    if loss == 'pause' then
        state.paused = true
        deliver(44, 107.48, 375.24, 86000.48)
        frame(.04)
        state.paused = false
    else
        state.visible = other_actor
        wait_view(other_index, 2)
    end
    state.visible = remote_actor
    -- Even if the exact target is observed again, the one-trial adapter is closed.
    local latest_serial = wait_view(remote_index, 2)
    deliver(45, 109.64, 375.30, 86000.60, latest_serial)
    frame(.04)
    frame(.06)
    assert(#state.writes == writes and #state.faults == 0)
    assert(state.pose[own_actor].x == 90 and state.pose[other_actor].x == 120)
    stream:close()
end

wait_for_hull_then_lose_it('pause')
wait_for_hull_then_lose_it('target')
