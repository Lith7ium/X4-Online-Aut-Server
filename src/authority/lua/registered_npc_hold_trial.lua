-- One opt-in hold/release observation on the pilot copy. No pose writer.
local M = {}
local EPOCH, SECTOR = '26a1a365c66788aa06f0aeb9e18ff62e', 'cluster_06_sector001_macro'
local NPC_INDEX, NPC_CODE = 943, 'MFQ-419'
local SEAT_INDEX, SEAT_CODE = 1073, 'HVC-420'

function M.new_x4(options)
    if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
    assert(options.pinned.epoch == EPOCH and options.own_index == SEAT_INDEX
        and options.target_index == NPC_INDEX and options.target_code == NPC_CODE)
    local ffi = require('ffi')
    assert(ffi.os == 'Linux')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[UniverseID GetContextByClass(UniverseID, const char*, bool);
        UniverseID GetPlayerControlledShipID(void); UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetPlayerContainerID(void); const char* GetObjectIDCode(UniverseID);]]
    local C, resolve = ffi.C, assert(X4LAN_RESOLVE_STRING)
    local saved_control, radar = assert(X4LAN_SAVED_REPLICA_CONTROL), options.radar
    local api = {}
    local control, observed, cockpit, release_reason, native_fault
    local phase = 'waiting'

    local function report(state, detail)
        pcall(DebugError, '[X4LAN] AUTH_REGISTERED_NPC_HOLD ' .. state .. '|943|MFQ-419|' .. detail)
    end

    local function exact_cockpit()
        if options.guard() ~= true then return nil end
        local own = options.own_actor()
        if not own or own ~= C.GetPlayerControlledShipID() or own ~= C.GetPlayerOccupiedShipID()
                or own ~= C.GetPlayerContainerID() or (cockpit and own ~= cockpit) then return nil end
        local code = C.GetObjectIDCode(own)
        if code ~= nil and code ~= ffi.NULL and ffi.string(code) == SEAT_CODE then return own end
    end

    local function visible()
        local ok, matches = pcall(radar.matches, radar, EPOCH, SECTOR, NPC_INDEX, observed.actor, NPC_CODE)
        return ok and matches == true
    end

    local function actor_key(id)
        return tostring(assert(resolve.actor_u64(id))):gsub('ULL$', '')
    end

    local function fail(reason)
        if phase == 'finished' then return end
        local uncertain = control and (control:outcome_uncertain() or control:state() == 'prepared')
        local detail = release_reason and (release_reason .. '_' .. phase) or (native_fault or reason)
        phase = 'finished'
        report(uncertain and 'UNCERTAIN' or 'ABORTED', detail)
    end

    local function operate(method)
        local ok, accepted = pcall(control[method], control)
        if ok and accepted == true then return true end
        fail(method)
        return false
    end

    local function bind()
        cockpit = assert(exact_cockpit())
        local target = assert(resolve.parse_hex(observed.actor))
        assert(target ~= cockpit)
        local sector = C.GetContextByClass(target, 'sector', false)
        local zone = C.GetContextByClass(target, 'zone', false)
        local binding = {epoch=EPOCH, index=NPC_INDEX, sector_macro=SECTOR,
            actor_key=actor_key(target), sector_key=actor_key(sector), zone_key=actor_key(zone)}
        -- The native inspector round-trips these references and revalidates
        -- live code, ownership, undocked state, sector and zone on each operation.
        return saved_control.new_x4({enabled=true, fixture='recovery_mfq_419', binding=binding,
            expected_save_path=options.expected_save_path, resolve=resolve,
            nonce=options.nonce, timeout=8, clock=options.clock,
            guard=function() return exact_cockpit() ~= nil end,
            acquire_guard=function() return release_reason == nil and visible() end,
            fault=function(reason) native_fault = reason end,
        })
    end

    function api:sample(row, sequence)
        if phase ~= 'waiting' or row.mode ~= 'radar' or row.epoch ~= EPOCH or row.sector_macro ~= SECTOR
                or row.index ~= NPC_INDEX or row.code ~= NPC_CODE then return end
        -- The observer stream calls this only after the validated REMOTE
        -- was acknowledged by its local cockpit SEEN transport.
        observed = {actor=row.actor}
        if not visible() then fail('radar_not_verified'); return end
        local ok, created = pcall(bind)
        if not ok or not created then fail('binding_refused'); return end
        control = created
        if operate('prepare') then
            phase = 'preparing'
            report('PREPARE', ('seq=%.0f'):format(sequence))
        end
    end

    function api:withdraw()
        if control and phase ~= 'finished' then release_reason = release_reason or 'radar_lost' end
    end

    function api:tick(running)
        if not control or phase == 'finished' then return end
        if not release_reason then
            if not running then release_reason = 'observation_stopped'
            elseif not visible() then self:withdraw() end
        end
        -- After loss, an already-issued Hold may still acknowledge. Finish
        -- reading that result so the owned Wait can be released. The controller
        -- refuses any new Hold once the acquisition guard has closed.
        if not operate('tick') then return end
        if control:state() == 'released' then
            phase = 'finished'
            report('RESTORED', release_reason)
        elseif control:state() == 'prepared' then
            if phase == 'preparing' then
                report('HELD', release_reason or 'radar')
                if not release_reason then
                    phase = 'checking'
                    operate('prepare')
                    return
                end
            elseif phase == 'checking' then
                report('CHECKED', release_reason or 'radar')
                release_reason = release_reason or 'check_complete'
            end
            if operate('release') then
                phase = 'releasing'
                report('RELEASING', release_reason)
            end
        end
    end

    function api:close()
        fail(control and 'closed_before_restore' or 'no_confirmed_radar_sample')
    end

    return api
end

X4LAN_REGISTERED_NPC_HOLD_TRIAL = M
return M
