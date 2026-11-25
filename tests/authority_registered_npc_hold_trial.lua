-- Exercise the production radar stream, fixture inspector and hold controller.
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
ffi.cdef('typedef struct { float x,y,z,yaw,pitch,roll; } UIPosRot;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
local own, npc, sector, zone = resolve.parse_hex('0x10'), resolve.parse_hex('0x20'),
    resolve.parse_hex('0x30'), resolve.parse_hex('0x40')
local zero = ffi.new('UniverseID', 0)
local epoch, frame = '26a1a365c66788aa06f0aeb9e18ff62e', 'cluster_06_sector001_macro'
local pinned = {epoch=epoch, nonce=('a'):rep(32), issued_count=1200}
local generation = ('c'):rep(32)
local now, own_code, distance, in_zone, events, pending_radar, requests, logs, inbox, seen, accept_seen
local allow_write, destination_in_zone, position, writes, extra_x, extra_yaw

function GetCurRealTime() return now end
local function monotonic() return 5000 + now end
function DebugError(row) logs[#logs + 1] = row end
function RegisterEvent(name, callback) assert(not events[name]); events[name] = callback end
function ConvertStringToLuaID(text) return ffi.new('UniverseID', tonumber(text)) end
function ConvertIDTo64Bit(id) return id end
function GetComponentData(id, field)
    if field == 'isdocked' then return false, false, false end
    if field == 'macro' and id == sector then return frame end
    if field == 'macro' and id == npc then return 'ship_arg_m_trans_container_02_b_macro', 'antigone' end
    if field == 'owner' and id == npc then return 'antigone', false, false, false, true end
    if field == 'isknown' and id == npc then return true, true end
    if field == 'maxradarrange' and id == own then return 40000 end
    error('unexpected query')
end
function AddUITriggeredEvent(screen, operation, args)
    if screen == 'X4AuthorityNpcCockpitObserver' then
        assert(operation == 'Read' and args.mode == 'radar' and args.target_index == 943)
        pending_radar = args.request_id
        return
    end
    assert(screen == 'X4AuthoritySavedReplicaControl' and args.fixture == 'recovery_mfq_419')
    assert(args.epoch == epoch and args.index == 943 and args.actor == npc and args.sector == sector and args.zone == zone)
    requests[#requests + 1] = {operation=operation, id=args.request_id}
end

package.loaded.ffi = {os='Linux', typeof=ffi.typeof, new=ffi.new, NULL=ffi.NULL, cdef=function() end,
    string=function(text) return text end, C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return false end,
        GetSaveFolderPath=function() return '/private/45841/save' end,
        IsValidComponent=function(id) return id == own or id == npc or id == sector or id == zone end,
        IsComponentOperational=function(id) return id == npc end,
        IsComponentClass=function(id, class)
            return (class == 'ship' and (id == own or id == npc)) or (class == 'sector' and id == sector)
                or (class == 'zone' and id == zone)
        end,
        GetPlayerControlledShipID=function() return own end, GetPlayerOccupiedShipID=function() return own end,
        GetPlayerContainerID=function() return own end,
        GetObjectIDCode=function(id) return id == npc and 'MFQ-419' or own_code end,
        GetDistanceBetween=function() return distance end,
        GetContextByClass=function(id, class)
            if class == 'sector' then assert(id == npc or id == own); return sector end
            if class == 'zone' then assert(id == npc); return in_zone and zone or zero end
            if class == 'ship' then assert(id == own); return own end
            return zero
        end,
        GetObjectPositionInSector=function(id) assert(id == npc); return position end,
        GetCurrentGameTime=function() return now end,
        GetZoneAt=function(id, offset)
            assert(id == sector and offset[0].x == 1 and offset[0].y == 2 and offset[0].z == 3)
            offset[0].x = 10000000
            return destination_in_zone and zone or zero
        end,
        SetObjectSectorPos=function(id, space, pose)
            assert(allow_write and id == npc and space == sector and pose.x == 1 and pose.y == 2 and pose.z == 3)
            writes = writes + 1
            position = {x=pose.x + extra_x, y=pose.y, z=pose.z,
                yaw=math.rad(pose.yaw) + extra_yaw, pitch=math.rad(pose.pitch), roll=math.rad(pose.roll)}
        end,
    }}
dofile('src/authority/lua/replica_motion_binding.lua')
dofile('src/authority/lua/saved_motion_apply.lua')
local radar_module = dofile('src/authority/lua/npc_cockpit_observer.lua')
local trial_module = dofile('src/authority/lua/registered_npc_hold_trial.lua')
local stream_module = dofile('src/authority/lua/company_observer_stream.lua')

local function remote(slot, serial, sequence, arrival, gen)
    sequence = sequence or 7
    local parts = {'REMOTE', slot, serial, epoch, '943', gen or generation, tostring(sequence), frame,
        '1', '2', '3', '-', '-', '-', '10', tostring(20 + sequence)}
    if arrival then parts[17] = ('%.17g'):format(arrival) end
    inbox[#inbox + 1] = table.concat(parts, '|')
end
local function ending(gen)
    inbox[#inbox + 1] = table.concat({'END', 'cockpit', '2', epoch, '943', gen, frame}, '|')
end
local function record(state, detail)
    local search = 'AUTH_REGISTERED_NPC_HOLD ' .. state .. '|943|MFQ-419|' .. detail
    for _, row in ipairs(logs) do if row:find(search, 1, true) then return true end end
    return false
end

local function boot(alignment)
    dofile('src/authority/lua/saved_replica_control.lua')
    now, own_code, distance, in_zone, accept_seen = 1, 'HVC-420', 13000, true, true
    events, requests, logs, inbox, seen, pending_radar = {}, {}, {}, {}, {}, nil
    allow_write, destination_in_zone, writes, extra_x, extra_yaw = alignment == true, true, 0, 0, 0
    position = {x=100, y=200, z=300, yaw=.2, pitch=.4, roll=-.5}
    local trial
    local radar = assert(radar_module.new_x4(pinned, 1073, function() return true end, {
        enabled=true, mode='radar', target_index=943, target_code='MFQ-419',
        on_withdrawal=function() trial:withdraw() end,
    }))
    local map = {tick=function() return {actor='0x20', code='MFQ-419', index=943, epoch=epoch,
        sector_macro=frame, mode='map'} end, matches=function() return true end, close=function() end}
    trial = assert(trial_module.new_x4({enabled=true, pinned=pinned, own_index=1073,
        target_index=943, target_code='MFQ-419', radar=radar, nonce=('b'):rep(32),
        expected_save_path='/private/45841/save', clock=monotonic, alignment=alignment,
        guard=function() return true end, own_actor=function() return own end,
    }))
    local stream = stream_module.new({cockpit=radar, map=map}, epoch, {
        send=function(_, row)
            if row:find('^SEEN|') then seen[#seen + 1] = row; return accept_seen end
            return true
        end,
        receive=function() return table.remove(inbox, 1) end,
    }, '', monotonic, nil, nil, trial)
    local function step(expected, receipt)
        now = now + .15
        if expected then
            local request = requests[#requests]
            assert(request.operation == expected)
            events['X4AuthoritySavedReplicaControl.Result'](nil, request.id .. '|' .. receipt .. '|0x20|0x30|0x40')
        end
        stream:tick(true)
        if pending_radar then
            local id = pending_radar
            pending_radar = nil
            local body = distance <= 40000 and ('OBSERVED|943|0x20|' .. frame .. '|MFQ-419|radar|13000|40000')
                or 'NONE|0|-|-|-|-|-|40000'
            events['X4AuthorityNpcCockpitObserver.Result'](nil, id .. '|' .. epoch .. '|' .. body)
        end
    end
    step()
    return step, stream
end

local function hold_next(step)
    step('Prepare', 'Created')
    step('Probe', 'Started')
    step()
    step('Probe', 'Started')
    step()
    assert(requests[#requests].operation == 'Hold')
end

local function restore(step)
    step('Release', 'Released')
    step('Sample', 'Restored')
    assert(not record('RESTORED', ''))
    step()
    step('Sample', 'Restored')
end

local step, stream = boot()
remote('map', '1'); step(); assert(#seen == 1 and #requests == 0)
accept_seen = false
remote('cockpit', '2'); step(); assert(#seen == 2 and #requests == 0)
accept_seen = true
remote('cockpit', '2'); step(); assert(#requests == 1 and record('PREPARE', 'seq=7'))
hold_next(step)
step('Hold', 'Held')
step('Check', 'Held')
assert(record('HELD', 'radar') and record('CHECKED', 'radar') and record('RELEASING', 'check_complete'))
restore(step)
assert(record('RESTORED', 'check_complete'))
local before = #requests
remote('cockpit', '2'); step(); stream:close(); assert(#requests == before and not record('UNCERTAIN', ''))

step = boot()
remote('cockpit', '2'); step(); hold_next(step)
distance = 50000 -- Use the actual radar reader's distance gate while Hold is in flight.
step('Hold', 'Held')
assert(record('RELEASING', 'radar_lost') and not record('CHECKED', ''))
restore(step)
assert(record('RESTORED', 'radar_lost'))

step = boot()
remote('cockpit', '2'); step()
distance = 50000
step('Prepare', 'Created'); step('Probe', 'Started'); step(); step('Probe', 'Started'); step()
assert(requests[#requests].operation == 'Probe' and record('UNCERTAIN', 'radar_lost_preparing'))
assert(not record('RESTORED', '') and not record('HELD', ''))

step = boot()
own_code = 'WRONG'
remote('cockpit', '2'); step()
assert(#requests == 0 and record('ABORTED', 'binding_refused'))

step = boot()
remote('cockpit', '2'); step(); hold_next(step)
distance = 50000
step('Hold', 'Held')
in_zone = false
step('Release', 'Released')
assert(record('UNCERTAIN', 'radar_lost_releasing') and not record('RESTORED', ''))

local function await_alignment()
    local advance = boot(true)
    remote('map', '1', 7, monotonic()); advance(); assert(#requests == 0)
    remote('cockpit', '2'); advance(); assert(#requests == 0) -- Local receive evidence is mandatory.
    remote('cockpit', '2', 7, monotonic() + 5); advance(); assert(#requests == 0)
    remote('cockpit', '2', 7, monotonic() - 2); advance(); assert(#requests == 0)
    remote('cockpit', '2', 7, monotonic()); advance(); hold_next(advance)
    advance('Hold', 'Held')
    remote('cockpit', '2', 8, monotonic())
    advance('Check', 'Held')
    assert(record('ALIGN_WAIT', 'after_seq=8') and writes == 0 and requests[#requests].operation == 'Check')
    return advance
end

local function fresh_candidate(step)
    step()
    remote('cockpit', '2', 10, monotonic()); step()
    assert(writes == 0 and requests[#requests].operation == 'Check')
end

step = await_alignment()
before = #requests
remote('cockpit', '2', 9, monotonic() - .01); step() -- Queued before CHECKED.
remote('cockpit', '2', 9, monotonic()); step() -- Repetition cannot make the old sequence fresh.
assert(writes == 0 and #requests == before)
fresh_candidate(step)
step('Check', 'Held')
assert(writes == 1 and record('ALIGNED', 'seq=10') and record('RELEASING', 'alignment_complete'), table.concat(logs, '\n'))
assert(table.concat(logs, '\n'):find('|before=100,200,300|target=1,2,3|actual=1,2,3|error_m=0|rotation_error_rad=', 1, true))
assert(math.abs(position.yaw - .2) < 1e-6 and math.abs(position.pitch - .4) < 1e-6 and math.abs(position.roll + .5) < 1e-6)
restore(step)
remote('cockpit', '2', 11, monotonic()); step()
assert(writes == 1 and record('RESTORED', 'alignment_complete'))

for _, failure in ipairs({'cross_zone', 'position', 'attitude', 'stale'}) do
    step = await_alignment()
    fresh_candidate(step)
    if failure == 'cross_zone' then destination_in_zone = false end
    if failure == 'position' then extra_x = 4 end
    if failure == 'attitude' then extra_yaw = .01 end
    if failure == 'stale' then for _ = 1, 5 do step() end end
    step('Check', 'Held')
    local dispatched = failure == 'position' or failure == 'attitude'
    assert(writes == (dispatched and 1 or 0) and not record('ALIGNED', ''))
    assert(record(dispatched and 'ALIGN_UNCERTAIN' or 'ALIGN_ABORTED', 'seq=10'))
    if failure == 'position' then
        assert(table.concat(logs, '\n'):find('|target=1,2,3|actual=5,2,3|error_m=4|', 1, true))
    end
    restore(step)
    assert(record('RESTORED', 'alignment_failed'))
end

for _, loss in ipairs({'source_ended', 'radar_lost', 'track_changed', 'alignment_timeout'}) do
    step = await_alignment()
    if loss == 'source_ended' then
        ending(('e'):rep(32)); step(); assert(requests[#requests].operation == 'Check')
        ending(generation); step()
    elseif loss == 'radar_lost' then
        fresh_candidate(step)
        distance = 50000
        step('Check', 'Held')
    elseif loss == 'track_changed' then
        step()
        remote('cockpit', '2', 10, monotonic(), ('e'):rep(32)); step()
    else
        for _ = 1, 21 do step() end
    end
    assert(writes == 0 and record('RELEASING', loss))
    restore(step)
    assert(record('RESTORED', loss))
end
