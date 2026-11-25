-- One guarded, read-only source in the recovered provider's existing update.
local M = {}

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
    local wanted, runtime_ref

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

    local function deliver(sample)
        local velocity, format = sample.real_velocity, string.format
        local fields = {'SAMPLE3', sample.epoch, tostring(sample.index), format('%.0f', sample.sequence),
            cfg.code, cfg.owner, runtime_ref, sample.sector_macro, format('%.17g', sample.source_time),
            format('%.17g', sample.capture_time), format('%.17g', sample.x), format('%.17g', sample.y),
            format('%.17g', sample.z), velocity and format('%.17g', velocity.x) or '-',
            velocity and format('%.17g', velocity.y) or '-', velocity and format('%.17g', velocity.z) or '-'}
        if net:send(prefix .. table.concat(fields, '|')) ~= true then stop('source_send_failed') end
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
        local ok, accepted, sample = pcall(sampler.event, sampler, reply)
        if not ok or accepted ~= true then stop('sample_refused'); return end
        if sample and not pcall(deliver, sample) then stop('sample_send_failed') end
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
                real_time_motion=true, capture_clock=clock, expected_save_path=save_path,
                guard=guard, binding_guard=function() return false end,
                saved_baseline_limit=cfg.count, saved_baseline_guard=local_binding,
                descriptor=X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE, resolve=resolve,
                diagnostics=true, fault=function(reason) stop('sampler_' .. reason) end,
            }))
            assert(sampler:register({epoch=cfg.epoch, index=cfg.index, classification='saved_baseline'}, wanted))
        end
        if sampler:tick() ~= true then stop('sampler_tick_failed') end
    end

    return function()
        if not stopped and not pcall(advance) then stop('lua_error') end
    end
end

X4LAN_REGISTERED_NPC_SOURCE = M
return M
