-- Exercise the production radar stream, fixture inspector and hold controller.
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
local own, npc, sector, zone = resolve.parse_hex('0x10'), resolve.parse_hex('0x20'),
    resolve.parse_hex('0x30'), resolve.parse_hex('0x40')
local zero = ffi.new('UniverseID', 0)
local epoch, frame = '26a1a365c66788aa06f0aeb9e18ff62e', 'cluster_06_sector001_macro'
local pinned = {epoch=epoch, nonce=('a'):rep(32), issued_count=1200}
local now, own_code, distance, in_zone, events, pending_radar, requests, logs, inbox, seen, accept_seen

function GetCurRealTime() return now end
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
        GetObjectPositionInSector=function(id) assert(id == npc); return {x=0, y=0, z=0, yaw=0, pitch=0, roll=0} end,
        GetCurrentGameTime=function() return now end,
        SetObjectSectorPos=function() error('pose writer called') end,
    }}
dofile('src/authority/lua/replica_motion_binding.lua')
dofile('src/authority/lua/saved_motion_apply.lua')
local radar_module = dofile('src/authority/lua/npc_cockpit_observer.lua')
local trial_module = dofile('src/authority/lua/registered_npc_hold_trial.lua')
local stream_module = dofile('src/authority/lua/company_observer_stream.lua')

local function remote(slot, serial)
    inbox[#inbox + 1] = table.concat({'REMOTE', slot, serial, epoch, '943', ('c'):rep(32), '7', frame,
        '1', '2', '3', '-', '-', '-', '10', '20'}, '|')
end
local function record(state, detail)
    local search = 'AUTH_REGISTERED_NPC_HOLD ' .. state .. '|943|MFQ-419|' .. detail
    for _, row in ipairs(logs) do if row:find(search, 1, true) then return true end end
    return false
end

local function boot()
    dofile('src/authority/lua/saved_replica_control.lua')
    now, own_code, distance, in_zone, accept_seen = 1, 'HVC-420', 13000, true, true
    events, requests, logs, inbox, seen, pending_radar = {}, {}, {}, {}, {}, nil
    local trial
    local radar = assert(radar_module.new_x4(pinned, 1073, function() return true end, {
        enabled=true, mode='radar', target_index=943, target_code='MFQ-419',
        on_withdrawal=function() trial:withdraw() end,
    }))
    local map = {tick=function() return {actor='0x20', code='MFQ-419', index=943, epoch=epoch,
        sector_macro=frame, mode='map'} end, matches=function() return true end, close=function() end}
    trial = assert(trial_module.new_x4({enabled=true, pinned=pinned, own_index=1073,
        target_index=943, target_code='MFQ-419', radar=radar, nonce=('b'):rep(32),
        expected_save_path='/private/45841/save', clock=GetCurRealTime,
        guard=function() return true end, own_actor=function() return own end,
    }))
    local stream = stream_module.new({cockpit=radar, map=map}, epoch, {
        send=function(_, row)
            if row:find('^SEEN|') then seen[#seen + 1] = row; return accept_seen end
            return true
        end,
        receive=function() return table.remove(inbox, 1) end,
    }, '', GetCurRealTime, nil, nil, trial)
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
