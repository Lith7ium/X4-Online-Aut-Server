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
    local wanted = assert(resolve.parse_hex(cfg.actor))
    local save_path = cfg.save_path:gsub('/+$', '')
    local prefix = 'N|' .. cfg.lease .. '|' .. cfg.generation .. '|'
    local stopped, sampler = false, nil

    local function stop()
        if not stopped then
            stopped = true
            pcall(net.send, net, prefix .. 'END')
        end
    end

    local function guard()
        if stopped or registry_ready() ~= true or not C.IsPlayerValid() or C.IsGamePaused() then return false end
        local path = C.GetSaveFolderPath()
        return path ~= nil and path ~= ffi.NULL and ffi.string(path):gsub('/+$', '') == save_path
    end

    local function local_binding(row, actor)
        if row.index ~= cfg.index or actor ~= wanted or not C.IsValidComponent(actor)
                or not C.IsComponentOperational(actor) or not C.IsComponentClass(actor, 'ship')
                or actor == C.GetPlayerControlledShipID() or actor == C.GetPlayerOccupiedShipID() then return false end
        local code = C.GetObjectIDCode(actor)
        if code == nil or code == ffi.NULL or ffi.string(code) ~= cfg.code then return false end
        local ref = ConvertStringToLuaID((tostring(actor):gsub('ULL$', '')))
        if not ref or resolve.actor_u64(ConvertIDTo64Bit(ref)) ~= actor then return false end
        local owner, pilot = GetComponentData(ref, 'owner', 'pilot')
        if owner ~= cfg.owner or owner == 'player' or not pilot then return false end
        local pilot_id = resolve.actor_u64(ConvertIDTo64Bit(pilot))
        return pilot_id ~= nil and C.IsValidComponent(pilot_id) and C.IsComponentClass(pilot_id, 'npc')
    end

    local function deliver(sample)
        local velocity, format = sample.real_velocity, string.format
        local fields = {'SAMPLE3', sample.epoch, tostring(sample.index), format('%.0f', sample.sequence),
            cfg.code, cfg.owner, sample.sector_macro, format('%.17g', sample.source_time),
            format('%.17g', sample.capture_time), format('%.17g', sample.x), format('%.17g', sample.y),
            format('%.17g', sample.z), velocity and format('%.17g', velocity.x) or '-',
            velocity and format('%.17g', velocity.y) or '-', velocity and format('%.17g', velocity.z) or '-'}
        if net:send(prefix .. table.concat(fields, '|')) ~= true then stop() end
    end

    assert(RegisterEvent('X4AuthorityJobDescriptorProbe.Result', function(_, reply)
        local request = not stopped and sampler and sampler:expected_request()
        if not request or type(reply) ~= 'string' or reply:sub(1, #request + 1) ~= request .. '|' then return end
        local ok, accepted, sample = pcall(sampler.event, sampler, reply)
        if not ok or accepted ~= true then stop(); return end
        if sample and not pcall(deliver, sample) then stop() end
    end) ~= false)

    local function advance()
        if not guard() then
            if sampler then stop() end  -- A pause is the end of this single observation window.
            return
        end
        if not sampler then
            sampler = assert(X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4({
                enabled=true, epoch=cfg.epoch, generation=cfg.generation, nonce=cfg.nonce,
                max_tracks=1, scan_budget=1, interval=.10, timeout=1.5, min_dt=.02, max_dt=1,
                real_time_motion=true, capture_clock=clock, expected_save_path=save_path,
                guard=guard, binding_guard=function() return false end,
                saved_baseline_limit=cfg.count, saved_baseline_guard=local_binding,
                descriptor=X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE, resolve=resolve, fault=stop,
            }))
            assert(sampler:register({epoch=cfg.epoch, index=cfg.index, classification='saved_baseline'}, wanted))
        end
        if sampler:tick() ~= true then stop() end
    end

    return function()
        if not stopped and not pcall(advance) then stop() end
    end
end

X4LAN_REGISTERED_NPC_SOURCE = M
