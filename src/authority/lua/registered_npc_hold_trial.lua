-- One opt-in hold/release observation on the pilot copy, with optional single alignment.
local M = {}
local EPOCH, SECTOR = '26a1a365c66788aa06f0aeb9e18ff62e', 'cluster_06_sector001_macro'
local NPC_INDEX, NPC_CODE = 943, 'MFQ-419'
local SEAT_INDEX, SEAT_CODE = 1073, 'HVC-420'
local ALIGNMENT_WAIT, MAX_SAMPLE_AGE = 3, .75

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
    local api = {alignment_enabled = options.alignment == true}
    local control, observed, cockpit, binding, release_reason, native_fault
    local checked_at, deadline, candidate
    local alignment_state = {used=false, uncertain=false}
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
        binding = {epoch=EPOCH, index=NPC_INDEX, sector_macro=SECTOR,
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

    local function same_track(row, serial, generation)
        return observed and row.actor == observed.actor and serial == observed.serial and generation == observed.generation
    end

    local function local_now()
        local now = options.clock()
        if type(now) == 'number' and now >= 0 and now <= 1e12 then return now end
    end

    local function report_alignment(state, detail, evidence)
        local function point(value)
            if not value then return '-' end
            return ('%.17g,%.17g,%.17g'):format(value.x, value.y, value.z)
        end
        local function scalar(value) return value and ('%.17g'):format(value) or '-' end
        local snapshot = candidate.snapshot
        report(state, ('seq=%.0f|generation=%s|arrival=%.17g|source_time=%.17g|capture_time=%.17g'
            .. '|before=%s|target=%s|actual=%s|error_m=%s|rotation_error_rad=%s|reason=%s'):format(
            snapshot.sequence, observed.generation, candidate.arrival, snapshot.source_time, snapshot.capture_time,
            point(evidence and evidence.before), point(evidence and evidence.target), point(evidence and evidence.actual),
            scalar(evidence and evidence.error), scalar(evidence and evidence.rotation_error_rad), detail))
    end

    local function align()
        local proof, fault
        local function prepared()
            local now = local_now()
            return now and now >= checked_at and now < deadline and not release_reason
                and control:state() == 'prepared' and exact_cockpit() ~= nil and visible()
        end
        local ok, aligned = pcall(X4LAN_SAVED_MOTION_APPLY.align_once_x4, {
            enabled=true, fixture='recovery_mfq_419', binding=binding,
            expected_save_path=options.expected_save_path, resolve=resolve,
            clock=options.clock, guard=prepared, prepared=prepared, max_age=MAX_SAMPLE_AGE,
            initial_alignment_state=alignment_state, initial_sample=candidate,
            record=function(evidence) proof = evidence; return true end,
            fault=function(_, _, _, stage) fault = stage end,
        })
        if ok and aligned == true then
            report_alignment('ALIGNED', 'verified', proof)
            return 'alignment_complete'
        end
        report_alignment(alignment_state.uncertain and 'ALIGN_UNCERTAIN' or 'ALIGN_ABORTED',
            fault or 'guard', alignment_state.evidence)
        return 'alignment_failed'
    end

    function api:sample(row, serial, sample, arrival)
        if phase == 'finished' or row.mode ~= 'radar' or row.epoch ~= EPOCH or row.sector_macro ~= SECTOR
                or row.index ~= NPC_INDEX or row.code ~= NPC_CODE then return end
        -- The observer stream calls this only after the validated REMOTE
        -- was acknowledged by its local cockpit SEEN transport.
        if self.alignment_enabled then
            local now = local_now()
            if not arrival or not now or arrival > now or now - arrival >= MAX_SAMPLE_AGE then return end
            if observed then
                if not same_track(row, serial, sample.generation) then
                    release_reason = release_reason or 'track_changed'
                    return
                end
                if sample.sequence <= observed.sequence or sample.capture_time <= observed.capture_time then return end
                observed.sequence, observed.capture_time = sample.sequence, sample.capture_time
                if phase == 'awaiting_alignment' and not release_reason and now < deadline and arrival > checked_at then
                    local velocity = sample.real_velocity
                    candidate = {arrival=arrival, snapshot={version=3, frame=SECTOR, sequence=sample.sequence,
                        source_time=sample.source_time, capture_time=sample.capture_time, pose={sample.x, sample.y, sample.z},
                        real_rate=velocity and {velocity.x, velocity.y, velocity.z}}}
                    phase = 'checking_alignment'
                    operate('prepare') -- Recheck ownership of the exact Wait just before the setter.
                end
                return
            end
        elseif phase ~= 'waiting' then return end
        observed = {actor=row.actor, serial=serial, generation=sample.generation,
            sequence=sample.sequence, capture_time=sample.capture_time}
        if not visible() then fail('radar_not_verified'); return end
        local ok, created = pcall(bind)
        if not ok or not created then fail('binding_refused'); return end
        control = created
        if operate('prepare') then
            phase = 'preparing'
            report('PREPARE', ('seq=%.0f'):format(sample.sequence))
        end
    end

    function api:withdraw()
        if control and phase ~= 'finished' then release_reason = release_reason or 'radar_lost' end
    end

    function api:end_track(row, serial, generation)
        if same_track(row, serial, generation) then release_reason = release_reason or 'source_ended' end
    end

    function api:tick(running)
        if not control or phase == 'finished' then return end
        if not release_reason then
            if not running then release_reason = 'observation_stopped'
            elseif not visible() then self:withdraw() end
            if deadline then
                local now = local_now()
                if not now or now < checked_at or now >= deadline then
                    release_reason = release_reason or 'alignment_timeout'
                end
            end
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
                if self.alignment_enabled and not release_reason then
                    checked_at = local_now()
                    if checked_at then
                        deadline = checked_at + ALIGNMENT_WAIT
                        phase = 'awaiting_alignment'
                        report('ALIGN_WAIT', ('after_seq=%.0f|checked_at=%.17g'):format(observed.sequence, checked_at))
                        return
                    end
                    release_reason = 'alignment_clock'
                end
                release_reason = release_reason or 'check_complete'
            elseif phase == 'awaiting_alignment' and not release_reason then
                return
            elseif phase == 'checking_alignment' and not release_reason then
                local outcome = align()
                release_reason = release_reason or outcome
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
