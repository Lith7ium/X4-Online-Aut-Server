-- Private diagnostic: recover the original empty ship, pilot and dock it
-- normally, then capture a stock purchase intent without booking it.
local cfg = X4LAN_CONFIG
if not cfg or cfg.authority_player_dock_flight ~= true then return end

local ffi = require('ffi')
if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
if not pcall(ffi.typeof, 'SofttargetDetails2') then
    ffi.cdef[[typedef struct {
        UniverseID softtargetID;
        const char* softtargetConnectionName;
        uint32_t messageID;
    } SofttargetDetails2;]]
end
ffi.cdef[[
    bool IsGamePaused(void);
    bool IsComponentClass(UniverseID,const char*);
    bool IsComponentOperational(UniverseID);
    double GetCurrentGameTime(void);
    UniverseID GetPlayerControlledShipID(void);
    UniverseID GetPlayerOccupiedShipID(void);
    UniverseID GetPlayerContainerID(void);
    UniverseID GetContextByClass(UniverseID,const char*,bool);
    const char* GetObjectIDCode(UniverseID);
    bool RequestDockAt(UniverseID,bool);
    bool SetSofttarget(UniverseID,const char*const);
    SofttargetDetails2 GetSofttarget2(void);
    bool QuickDock(void);
    uint32_t GetNumDockedShips(UniverseID,const char*);
    uint32_t GetDockedShips(UniverseID*,uint32_t,UniverseID,const char*);
]]
local C = ffi.C
local screen = 'X4AuthorityPlayerDockObserver'
local pinned = {nonce = cfg.authority_player_dock_flight_nonce,
    epoch = '4ba7be438ebb04ec5bb85fdb95b5a488', issued_count = 10082}
local index = 7354
local phase, deadline = 'init'
local binding, observation, seat, capture, before, intent
local departed
local pending, next_poll, next_sample
local start_time, next_range
local autopilot = false

local function log(row) DebugError('[X4LAN] AUTH_PLAYER_DOCK_FLIGHT ' .. row) end

local function context()
    return C.GetPlayerControlledShipID(), C.GetPlayerOccupiedShipID(), C.GetPlayerContainerID()
end

local function control_matches()
    local controlled, occupied, container = context()
    return controlled == binding.ship and occupied == binding.ship and container == binding.ship
end

local function terminal(row)
    local controlled, occupied, container = context()
    log(('LAST|%s|%s|%s|%s|%s'):format(phase, tostring(controlled), tostring(occupied),
        tostring(container), observation and observation.raw or 'unobserved'))
    if autopilot then
        autopilot = false
        if binding and control_matches() then pcall(StopAutoPilot) end
    end
    phase = 'done'
    log(row)
end

local function guarded(fn, ...)
    if phase == 'done' then return end
    local ok, why = pcall(fn, ...)
    if not ok then terminal('ERROR|' .. phase .. '|' .. tostring(why):gsub('[\r\n|]', ' ')) end
end

local function object_code(id, expected, class)
    if not id or not C.IsComponentClass(id, class) or not C.IsComponentOperational(id) then return false end
    local raw = C.GetObjectIDCode(id)
    return raw ~= nil and ffi.string(raw) == expected
end

local function observe(now)
    assert(not pending, 'observer_busy')
    pending = true
    next_poll = now + 1
    assert(AddUITriggeredEvent(screen, 'Inspect', {}) ~= false, 'observer_dispatch')
end

local function matching_observation()
    return observation.controlled == binding.ship and observation.occupied == binding.ship
        and observation.container == binding.ship and control_matches()
end

local function listed_at_station()
    local bay, station = observation.bay, binding.station
    if not bay or C.GetContextByClass(bay, 'station', true) ~= station then return false end
    local owner, docked, stored = GetComponentData(binding.ship_lua, 'owner', 'isdocked', 'isinternallystored')
    if owner ~= 'player' or docked ~= true or stored ~= false then return false end
    local count = tonumber(C.GetNumDockedShips(station, nil))
    if not count or count < 1 or count > 4096 then return false end
    local ships = ffi.new('UniverseID[?]', count)
    if tonumber(C.GetDockedShips(ships, count, station, nil)) ~= count then return false end
    for i = 0, count - 1 do
        if ships[i] == binding.ship then
            log(('DOCK|%s|%s|%s|%s'):format(tostring(binding.ship), tostring(station), tostring(bay), count))
            return true
        end
    end
    return false
end

local function on_observation(_, row)
    assert(pending and type(row) == 'string' and #row < 512, 'observer_reply')
    pending = false
    local fields = {}
    for field in row:gmatch('[^|]+') do fields[#fields + 1] = field end
    assert(#fields == 12, 'observer_shape')
    local parse = X4LAN_RESOLVE_STRING.parse_hex
    local status, ship, station = fields[1], parse(fields[2]), parse(fields[3])
    local distance, water, account, age = tonumber(fields[9]), tonumber(fields[10]),
        tonumber(fields[11]), tonumber(fields[12])
    observation = {raw = row, status = status, bay = parse(fields[4]), controlled = parse(fields[5]),
        occupied = parse(fields[6]), container = parse(fields[7]), distance = distance, age = age,
        water = water, account = account}
    if phase == 'initial_observation' then
        assert(status == 'IN_SPACE' and fields[4] == '0' and fields[8] == '0'
            and distance and distance > 0 and water and water >= 0 and account and account >= 0
            and age and age >= 0, 'initial_' .. status)
        assert(object_code(ship, 'WRN-302', 'ship') and object_code(station, 'BIM-169', 'station'), 'initial_identity')
        binding = {ship = ship, station = station, start_distance = distance,
            ship_lua = assert(ConvertStringToLuaID(tostring(ship))),
            station_lua = assert(ConvertStringToLuaID(tostring(station)))}
        log('START|' .. row)
        phase = 'begin_seat'
        return
    end
    assert(ship == binding.ship and station == binding.station, 'observer_binding_changed')
    assert(fields[8] == '0' and matching_observation(), 'observer_control_changed')
    assert(status == 'IN_SPACE' or status == 'DOCKED' or (phase == 'dock_wait' and status == 'DOCK_INVALID'),
        'observer_' .. status)
    if phase == 'flight' and not departed and distance and distance >= 0
            and binding.start_distance - distance >= 1000 then
        departed = true
        log(('DEPARTURE|%s|%s'):format(binding.start_distance, distance))
    elseif phase == 'dock_wait' and status == 'DOCKED' and listed_at_station() then
        phase = 'begin_capture'
        log('DOCKED|' .. row)
    elseif phase == 'before_confirm_wait' or phase == 'after_confirm_wait' then
        assert(status == 'DOCKED' and observation.bay == binding.bay and listed_at_station()
            and water and water >= 0 and account and account >= 0, 'confirm_observation_unverified')
        if phase == 'before_confirm_wait' then
            before, phase = observation, 'confirm'
            log('BEFORE_CONFIRM|' .. row)
        else
            log('AFTER_CONFIRM|' .. row)
            assert(observation.water == before.water and observation.account == before.account,
                'stock_money_or_cargo_changed')
            log(('INTENT|%s|%s|water|%d|%d'):format(intent.sector, intent.station_code,
                intent.quantity, intent.price_cents))
            terminal('DONE|intent_captured')
        end
    end
end

local function frame(registry)
    local now = GetCurRealTime()
    if phase == 'init' then
        local snapshot = cfg.authority_registry_snapshot
        assert(type(snapshot) == 'table' and snapshot.epoch == pinned.epoch
            and snapshot.issued_count == pinned.issued_count and cfg.authority_player_dock_flight_epoch == pinned.epoch
            and cfg.authority_player_dock_flight_index == index and type(pinned.nonce) == 'string'
            and #pinned.nonce == 32 and pinned.nonce:match('^[0-9a-f]+$')
            and registry and registry:ready() and C.IsGamePaused(), 'initial_guard')
        assert(RegisterEvent(screen .. '.Result', function(...) guarded(on_observation, ...) end) ~= false,
            'observer_register')
        phase, deadline = 'initial_observation', now + 8
        observe(now)
    elseif phase == 'begin_seat' then
        assert(registry and registry:ready() and C.IsGamePaused(), 'seat_guard')
        seat = assert(X4LAN_AUTHORITY_CLIENT_SEAT.new_x4(pinned, index, registry, function() end), 'seat_owner')
        phase, deadline = 'seat', now + 12
    elseif phase == 'seat' then
        assert(now < deadline and seat:tick(), 'seat_refused_or_timeout')
        if seat:ready() then
            assert(X4LAN_RESOLVE_STRING.parse_hex(seat:controlled_actor()) == binding.ship
                and control_matches(), 'seat_identity')
            log('SEAT|controlled|' .. tostring(binding.ship))
            start_time = tonumber(C.GetCurrentGameTime())
            Unpause()
            phase, deadline = 'await_unpause', now + 8
        end
    elseif phase == 'await_unpause' then
        assert(now < deadline and control_matches(), 'unpause_or_control')
        if not C.IsGamePaused() and tonumber(C.GetCurrentGameTime()) > start_time then
            local accepted = StartAutoPilot(binding.station_lua)
            assert(accepted ~= false, 'autopilot_refused')
            autopilot = true
            phase, deadline, next_poll, next_sample, next_range = 'flight', now + 300, now, now, now
            log('AUTOPILOT|started|' .. tostring(accepted))
        end
    elseif phase == 'flight' or phase == 'dock_wait' then
        assert(now < deadline and control_matches(), 'timeout_or_control_lost')
        if not pending and now >= next_poll then observe(now) end
        if phase == 'begin_capture' then return end
        if now >= next_sample then
            log('PROGRESS|' .. phase .. '|' .. (observation and observation.raw or 'unobserved'))
            next_sample = now + 5
        end
        if phase == 'flight' and departed and now >= next_range then
            next_range = now + 0.5
            if C.RequestDockAt(binding.station, true) then
                StopAutoPilot()
                autopilot = false
                assert(C.SetSofttarget(binding.station, '') and C.GetSofttarget2().softtargetID == binding.station
                    and C.RequestDockAt(binding.station, true), 'dock_target_or_range_changed')
                assert(control_matches() and C.QuickDock(), 'quickdock_refused')
                phase, deadline = 'dock_wait', now + 30
                log('QUICKDOCK|dispatched')
            end
        end
    elseif phase == 'begin_capture' then
        assert(matching_observation() and listed_at_station(), 'dock_changed_before_capture')
        binding.bay = observation.bay
        capture = X4LAN_AUTHORITY_TRADE_CAPTURE_FROM_DOCK(binding.ship, binding.station, binding.bay)
        phase, deadline = 'capture', now + 15
    elseif phase == 'capture' then
        assert(now < deadline and control_matches(), 'capture_timeout_or_control_lost')
        local outcome, failure, ready = capture(false)
        assert(not failure and not outcome, 'stock_capture_prepare_failed')
        if ready then
            phase, deadline = 'before_confirm_wait', now + 8
            observe(now)
        end
    elseif phase == 'confirm' then
        assert(now < deadline and control_matches(), 'confirm_timeout_or_control_lost')
        local outcome, failure, _, captured = capture(true)
        assert(not failure and outcome == 'intent_captured' and captured, 'stock_capture_failed')
        intent = captured
        phase, deadline = 'after_confirm_wait', now + 8
        observe(now)
    else
        assert(now < deadline, 'observer_timeout')
    end
end

function X4LAN_RECOVERY_ON_PAUSED(registry)
    guarded(frame, registry)
end
