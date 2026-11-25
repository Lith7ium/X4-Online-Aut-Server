-- One guarded, read-only source in the recovered provider's existing update.
local M = {}
local VELOCITY_TRACE_LIMIT = 48

function M.new_x4(cfg, net, clock, registry_ready)
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        bool IsPlayerValid(void); bool IsGamePaused(void); const char* GetSaveFolderPath(void);
        bool IsValidComponent(UniverseID); bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID, const char*); const char* GetObjectIDCode(UniverseID);
        UniverseID GetPlayerControlledShipID(void); UniverseID GetPlayerOccupiedShipID(void);
    ]]
    local C, resolve = ffi.C, assert(X4LAN_RESOLVE_STRING)
    local save_path = cfg.save_path:gsub('/+$', '')
    local prefix = 'N|' .. cfg.lease .. '|' .. cfg.generation .. '|'
    local stopped, lookup, sampler = false, nil, nil
    local ticking = false
    local wanted, runtime_ref
    local reported_rate = false
    local cadence = {updates=0}
    local velocity_requests = 0
    local descriptor_probe = X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE

    -- Only this already opted-in source annotates its sample descriptors.
    -- The separate saved-baseline lookup continues to use the ordinary cue.
    local descriptor = {new_x4=function(options)
        if velocity_requests < VELOCITY_TRACE_LIMIT then
            assert(runtime_ref and options.actor == wanted and options.row.index == cfg.index)
            velocity_requests = velocity_requests + 1
            options.registered_npc_velocity_sequence = velocity_requests
        end
        return descriptor_probe.new_x4(options)
    end}

    local function trace_time()
        local ok, now = pcall(clock)
        if ok and type(now) == 'number' and now >= 0 and now < math.huge then return now end
    end

    local function elapsed_ms(before, after)
        return before and after and after >= before and ('%.3f'):format((after - before) * 1000) or '-'
    end

    local function trace_capture(sample, reply_at)
        if not cadence then return '' end
        local start, dispatched = cadence.tick_started, cadence.tick_completed
        local suffix = ('|updates=%d|max_update_gap_ms=%s|capture_to_dispatch_ms=%s|dispatch_tick_ms=%s|md_reply_wait_ms=%s|reply_to_capture_ms=%s|dispatch_from=%s'):format(
            cadence.updates, elapsed_ms(0, cadence.max_gap), elapsed_ms(cadence.last_capture, start),
            elapsed_ms(start, dispatched), elapsed_ms(dispatched, reply_at), elapsed_ms(reply_at, sample.capture_time),
            cadence.dispatch_from or '-')
        if sample.sequence == 2 then
            cadence = nil
        else
            cadence.updates, cadence.max_gap, cadence.last_capture = 0, nil, sample.capture_time
            cadence.tick_started, cadence.tick_completed, cadence.dispatch_from = nil, nil, nil
        end
        return suffix
    end

    local function stop(reason)
        if not stopped then
            stopped = true
            DebugError('[X4LAN] AUTH_REGISTERED_NPC_SOURCE END|' .. reason)
            pcall(net.send, net, prefix .. 'END|' .. reason)
        end
    end

    local function guard()
        if stopped or registry_ready() ~= true then return false, 'registry_unavailable' end
        if not C.IsPlayerValid() then return false, 'player_context_lost' end
        if C.IsGamePaused() then return false, 'paused' end
        local path = C.GetSaveFolderPath()
        if path == nil or path == ffi.NULL or ffi.string(path):gsub('/+$', '') ~= save_path then
            return false, 'save_path_mismatch'
        end
        return true
    end

    local function inspect_npc(actor)
        if not C.IsValidComponent(actor)
                or not C.IsComponentOperational(actor) or not C.IsComponentClass(actor, 'ship')
                or actor == C.GetPlayerControlledShipID() or actor == C.GetPlayerOccupiedShipID() then
            return false, 'ship_unavailable'
        end
        local code = C.GetObjectIDCode(actor)
        if code == nil or code == ffi.NULL or ffi.string(code) ~= cfg.code then return false, 'code_mismatch' end
        local ref = ConvertStringToLuaID((tostring(actor):gsub('ULL$', '')))
        if not ref or resolve.actor_u64(ConvertIDTo64Bit(ref)) ~= actor then return false, 'actor_reference_mismatch' end
        local owner, pilot = GetComponentData(ref, 'owner', 'pilot')
        if owner ~= cfg.owner or owner == 'player' then return false, 'owner_mismatch' end
        if not pilot then return false, 'npc_pilot_missing' end
        local pilot_id = resolve.actor_u64(ConvertIDTo64Bit(pilot))
        if not pilot_id or not C.IsValidComponent(pilot_id) or not C.IsComponentClass(pilot_id, 'npc') then
            return false, 'npc_pilot_missing'
        end
        return true
    end

    local function local_binding(row, actor)
        if row.index ~= cfg.index or actor ~= wanted then stop('runtime_binding_changed'); return false end
        local accepted, reason = inspect_npc(actor)
        if not accepted then stop(reason) end
        return accepted
    end

    local function deliver(sample, reply_at)
        local velocity, format = sample.real_velocity, string.format
        local fields = {'SAMPLE3', sample.epoch, tostring(sample.index), format('%.0f', sample.sequence),
            cfg.code, cfg.owner, runtime_ref, sample.sector_macro, format('%.17g', sample.source_time),
            format('%.17g', sample.capture_time), format('%.17g', sample.x), format('%.17g', sample.y),
            format('%.17g', sample.z), velocity and format('%.17g', velocity.x) or '-',
            velocity and format('%.17g', velocity.y) or '-', velocity and format('%.17g', velocity.z) or '-'}
        if net:send(prefix .. table.concat(fields, '|')) ~= true then
            stop('source_send_failed')
            return
        end
        if sample.sequence <= 2 or (velocity and not reported_rate) then
            reported_rate = reported_rate or velocity ~= nil
            pcall(function()
                DebugError(('[X4LAN] AUTH_REGISTERED_NPC_SOURCE SENT|seq=%s|capture=%s|velocity=%s%s'):format(
                    fields[4], fields[10], velocity and 'present' or 'absent', trace_capture(sample, reply_at)))
            end)
        end
    end

    local function tick_sampler(origin)
        if stopped or ticking then return end
        local started_at = cadence and trace_time()
        local was_pending = started_at and sampler:expected_request()
        if started_at and origin == 'update' then
            cadence.updates = cadence.updates + 1
            if cadence.last_update and started_at >= cadence.last_update then
                cadence.max_gap = math.max(cadence.max_gap or 0, started_at - cadence.last_update)
            end
            cadence.last_update = started_at
        end
        ticking = true
        local ok, accepted = pcall(sampler.tick, sampler)
        ticking = false
        if not ok or accepted ~= true then
            stop('sampler_tick_failed')
        elseif started_at and not was_pending and sampler:expected_request() then
            cadence.tick_started, cadence.tick_completed, cadence.dispatch_from = started_at, trace_time(), origin
        end
    end

    assert(RegisterEvent('X4AuthorityJobDescriptorProbe.Result', function(_, reply)
        if stopped or type(reply) ~= 'string' then return end
        local request = lookup and lookup:expected_request()
        if request and reply:sub(1, #request + 1) == request .. '|' then
            local actor, owner = reply:match('^[^|]+|[^|]+|[^|]+|ok|(0[xX][0-9a-fA-F]+)|[^|]+|([^|]+)|')
            if actor and owner ~= cfg.owner then stop('owner_mismatch'); return end
            local ok, accepted = pcall(lookup.event, lookup, reply)
            if not ok or accepted ~= true or not actor or resolve.parse_hex(actor) ~= wanted then
                stop('lookup_' .. (lookup:diagnostic() or 'refused'))
                return
            end
            runtime_ref = actor
            DebugError(('[X4LAN] AUTH_REGISTERED_NPC_SOURCE BOUND|%s|%d|%s|%s|%s'):format(
                cfg.epoch, cfg.index, cfg.code, cfg.owner, runtime_ref))
            return
        end
        request = sampler and sampler:expected_request()
        if not request or reply:sub(1, #request + 1) ~= request .. '|' then return end
        -- An inline reply during a dispatch cannot reenter the sampler.
        if ticking then stop('sampler_event'); return end
        local reply_at = cadence and trace_time()
        local ok, accepted, sample = pcall(sampler.event, sampler, reply)
        if not ok or accepted ~= true then stop('sample_refused'); return end
        if not sample then return end
        if not pcall(deliver, sample, reply_at) then stop('sample_send_failed'); return end
        -- The accepted request has returned to ready. Try at most one new
        -- descriptor now, with the ordinary interval and identity checks.
        tick_sampler('reply')
    end) ~= false)

    local function advance()
        local running, reason = guard()
        if not running then
            -- The confirmed join save is paused until the pilot is admitted.
            if reason ~= 'paused' or lookup or sampler then stop(reason) end
            return
        end
        if not runtime_ref then
            if not lookup then
                lookup = assert(X4LAN_SAVED_BASELINE_SOURCE_BOOTSTRAP.new_x4({
                    enabled=true, epoch=cfg.epoch, nonce=cfg.lookup_nonce, baseline=cfg.count, timeout=1.5,
                    guard=guard, expected_save_path=save_path, resolve=resolve,
                    baseline_guard=function(epoch, count) return epoch == cfg.epoch and count == cfg.count end,
                    complete=function(index, actor)
                        if index ~= cfg.index then return false end
                        local accepted, why = inspect_npc(actor)
                        if not accepted then stop(why); return false end
                        wanted = actor
                        return true
                    end,
                }))
                if lookup:begin(cfg.index) ~= true then stop('lookup_dispatch_failed') end
            elseif lookup:tick() ~= true then
                stop('lookup_timeout_or_guard_lost')
            end
            return
        end
        if not sampler then
            sampler = assert(X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4({
                enabled=true, epoch=cfg.epoch, generation=cfg.generation, nonce=cfg.nonce,
                max_tracks=1, scan_budget=1, interval=.10, timeout=1.5, min_dt=.02, max_dt=1,
                interval_from_start=true, real_time_motion=true, capture_clock=clock, expected_save_path=save_path,
                guard=guard, binding_guard=function() return false end,
                saved_baseline_limit=cfg.count, saved_baseline_guard=local_binding,
                descriptor=descriptor, resolve=resolve,
                diagnostics=true, fault=function(reason) stop('sampler_' .. reason) end,
            }))
            assert(sampler:register({epoch=cfg.epoch, index=cfg.index, classification='saved_baseline'}, wanted))
        end
        tick_sampler('update')
    end

    return function()
        if not stopped and not pcall(advance) then stop('lua_error') end
    end
end

X4LAN_REGISTERED_NPC_SOURCE = M
return M
