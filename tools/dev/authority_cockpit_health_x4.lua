-- Opt-in native Windows X4 9.00 driver for the cockpit health receiver.
-- Hosts the REAL X4LAN_COMPANY_COCKPIT_HEALTH receiver (new + motion_link) inside the REAL
-- X4LAN_COMPANY_MOTION_SOURCE.new host, exactly as company_motion_source.lua new_x4 wires it,
-- over the real loopback UDP module. Test-only boundaries:
--   * The occupied ship is registered as Company index 1 by the disposable probe MD.
--   * A fixture sampler replaces the saved-baseline descriptor sampler. It reports the ship's real
--     sector position at 10 Hz; the source host still owns SAMPLE3, GAP and receiver sample anchors.
--   * TEST rows on the same authenticated UDP lease let the runner request a gap or a readback.
local cfg = X4NEON_COCKPIT_HEALTH
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
for _, text in ipairs({
    'bool IsStartmenu(void);',
    'bool IsPlayerValid(void);',
    'bool IsGamePaused(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
}) do declare(text) end

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id) .. '/'
local INDEX = 1

local function log(msg)
    DebugError('[X4LAN] AUTH_COCKPIT_HEALTH_DRIVER ' .. msg)
end

local function split(text)
    local row = {}
    for field in (text .. '|'):gmatch('([^|]*)|') do row[#row + 1] = field end
    return row
end

local state = {stage = 'boot', boot_frames = 0, online = true, gap_until = nil, requests = 0}

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'), 'not disposable profile ' .. tostring(cfg.profile))
end

local function readback(tag)
    AddUITriggeredEvent('X4AuthorityCockpitHealthProbe', 'Readback', {tag = tag})
end

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.registry_reply = tostring(value)
end)
RegisterEvent('X4AuthorityCockpitHealthProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

local function occupied_id()
    local id = tonumber(C.GetPlayerOccupiedShipID())
    if id and id ~= 0 and tonumber(C.GetPlayerControlledShipID()) == id then return id end
end

local function init_stack(ready)
    local open_fn, err = package.loadlib(EXT_DIR .. 'native/x4lan_net_64.dll', 'luaopen_x4lan_net')
    assert(type(open_fn) == 'function', 'failed loading local UDP module: ' .. tostring(err))
    local udp = open_fn()
    assert(type(udp) == 'table' and type(udp.open) == 'function' and type(udp.monotonic) == 'function',
        'invalid local UDP module')
    local net = assert(udp.open('127.0.0.1', assert(cfg.udp_port)))
    local prefix = 'C|' .. cfg.lease .. '|' .. cfg.generation .. '|'
    local actor_text = ready.actor
    local actor_number = tonumber(actor_text)
    assert(actor_number and occupied_id() == actor_number, 'registered ship is not the occupied ship')

    local function controlled_actor()
        if occupied_id() == actor_number then return actor_text end
    end
    local function available()
        return state.online and C.IsPlayerValid() and not C.IsGamePaused() and controlled_actor() ~= nil
    end

    local health = assert(X4LAN_COMPANY_COCKPIT_HEALTH, 'receiver module missing')
    local observer = assert(X4LAN_COMBAT_OBSERVER, 'health reader module missing')
    local link = health.motion_link(health.new({enabled = true, lease = cfg.lease,
        epoch = cfg.epoch, index = INDEX, clock = udp.monotonic, available = available,
        controlled_actor = controlled_actor,
        read_health = observer.read_lua_health,
        on_receipt = function(row)
            net:send(prefix .. table.concat({'HEALTH_RESULT', row.generation, row.epoch, row.index,
                row.window, string.format('%.0f', row.sequence), row.status}, '|'))
            -- A second Lua read in the same callback helps diagnose a non-confirmed receipt.
            local now_read = observer.read_lua_health(actor_text)
            log(('RECEIPT window=%s sequence=%.0f status=%s shield=%s maxshield=%s hull=%s maxhull=%s lua_shield=%.17g lua_hull=%.17g mono=%.6f'):format(
                tostring(row.window), row.sequence, row.status, tostring(row.shield), tostring(row.maxshield),
                tostring(row.hull), tostring(row.maxhull), tonumber(now_read.shield) or -1,
                tonumber(now_read.hull) or -1, udp.monotonic()))
            readback('receipt-' .. string.format('%.0f', row.sequence) .. '-' .. row.status)
        end,
    }))

    local counter = 0
    local function fixture_sampler(selected, guard)
        local sampler = {sequence = 0, pending = nil, last = nil}
        function sampler:register(row, entity)
            return row.epoch == cfg.epoch and row.index == INDEX and entity == selected
        end
        function sampler:reset_baseline(index) self.pending = nil; return index == INDEX end
        function sampler:state() return self.pending and 'pending' or 'idle' end
        function sampler:discard_pending() self.pending = nil; return true end
        function sampler:expected_request() return self.pending end
        function sampler:tick()
            local now = udp.monotonic()
            if not self.pending and (not self.last or now - self.last >= 0.1) then
                self.last = now
                counter = counter + 1
                self.pending = string.format('%032x', counter)
            end
            return true
        end
        function sampler:event(row)
            if not self.pending or row:sub(1, 33) ~= self.pending .. '|' then return true end
            self.pending = nil
            if guard() ~= true then return true end
            local pose = C.GetObjectPositionInSector(selected)
            self.sequence = self.sequence + 1
            return true, {epoch = cfg.epoch, index = INDEX, sequence = self.sequence,
                sector_macro = ready.sector, source_time = tonumber(C.GetCurrentGameTime()),
                capture_time = udp.monotonic(), x = tonumber(pose.x), y = tonumber(pose.y), z = tonumber(pose.z)}
        end
        state.sampler = sampler
        return sampler
    end

    local source = X4LAN_COMPANY_MOTION_SOURCE.new({
        observer = nil, observer_fault = function() log('ERROR observer_fault') end,
        cockpit_health = link,
        epoch = cfg.epoch, index = INDEX, available = available,
        actor = function() return controlled_actor() and actor_number or nil end,
        send = function(payload)
            if payload == 'GAP' then log(('SENT_GAP mono=%.6f'):format(udp.monotonic())) end
            net:send(prefix .. payload)
        end,
        make_sampler = fixture_sampler,
    })
    source:set_running(true)
    state.udp, state.net, state.prefix, state.link, state.source = udp, net, prefix, link, source
end

local function handle_test(row)
    if row[2] == 'gap' then
        local seconds = tonumber(row[3]) or 0.8
        state.online = false
        state.gap_until = state.udp.monotonic() + math.min(math.max(seconds, 0.2), 3)
        log(('GAP_BEGIN mono=%.6f'):format(state.udp.monotonic()))
    elseif row[2] == 'readback' and row[3] and row[3]:match('^[%w_%-]+$') then
        readback(row[3])
    elseif row[2] == 'done' then
        state.source:set_running(false)
        state.stage = 'done'
        log('DONE')
    end
end

local function receive()
    local prefix = state.prefix
    for _ = 1, 32 do
        local packet = state.net:receive()
        if packet == nil then break end
        if #packet <= 576 and packet:sub(1, #prefix) == prefix then
            local row = split(packet:sub(#prefix + 1))
            if row[1] == 'HEALTH' or row[1] == 'HEALTH_CLEAR' then
                local now = state.udp.monotonic()
                if row[1] == 'HEALTH' and #row == 13 then
                    log(('RX HEALTH revision=%s window=%s sequence=%s anchor=%s deadline_in=%.4f shield=%s maxshield=%s hull=%s maxhull=%s'):format(
                        row[2], row[6], row[7], row[8], (tonumber(row[9]) or 0) - now, row[10], row[11], row[12], row[13]))
                else
                    log(('RX %s revision=%s fields=%d'):format(row[1], tostring(row[2]), #row))
                end
                state.link:receive(row)
            elseif row[1] == 'TEST' then
                handle_test(row)
            end
        end
    end
end

local function frame()
    if state.stage == 'boot' then
        if C.IsStartmenu() then
            -- The menus environment reloads with the new game, so this runs once per launch.
            check_profile()
            log('STARTMENU gamestart=' .. tostring(cfg.gamestart))
            state.stage = 'starting'
            Helper.addDelayedOneTimeCallbackOnUpdate(function() NewGame(cfg.gamestart) end, true, getElapsedTime() + 2)
            return
        end
        if not C.IsPlayerValid() or C.IsGamePaused() then return end
        state.boot_frames = state.boot_frames + 1
        if state.boot_frames < 120 or occupied_id() == nil then return end
        check_profile()
        AddUITriggeredEvent('X4AuthorityShipRegistry', 'Initialize', {epoch = cfg.epoch, request_id = 'reg-1'})
        state.stage = 'registry'
    elseif state.stage == 'registry' then
        local reply = state.registry_reply
        if not reply then return end
        assert(reply == 'reg-1|Initialized|' .. cfg.epoch, 'registry refused')
        AddUITriggeredEvent('X4AuthorityCockpitHealthProbe', 'Setup', {epoch = cfg.epoch, request_id = 'setup-1'})
        state.stage = 'setup'
    elseif state.stage == 'setup' then
        local reply = state.probe_reply
        if not reply then return end
        local row = split(reply)
        assert(row[1] == 'setup-1' and row[2] == 'Ready', 'setup refused: ' .. reply)
        local ready = {actor = row[3], idcode = row[4], macro = row[5], sector = row[6],
            shield = row[7], maxshield = row[8], hull = row[9], maxhull = row[10]}
        init_stack(ready)
        -- The receiver compares fixture maxima with its own Lua read, so report that read as well.
        local lua = X4LAN_COMBAT_OBSERVER.read_lua_health(ready.actor)
        assert(lua.ok == true, 'Lua health read failed')
        log(('READY actor=%s idcode=%s macro=%s sector=%s shield=%s maxshield=%s hull=%s maxhull=%s lua_shield=%.17g lua_maxshield=%.17g lua_hull=%.17g lua_maxhull=%.17g mono=%.6f'):format(
            ready.actor, ready.idcode, ready.macro, ready.sector, ready.shield, ready.maxshield, ready.hull,
            ready.maxhull, lua.shield, lua.shieldmax, lua.hull, lua.hullmax, state.udp.monotonic()))
        state.stage = 'run'
    elseif state.stage == 'run' then
        if state.gap_until and state.udp.monotonic() >= state.gap_until then
            state.gap_until = nil
            state.online = true
            log(('GAP_END mono=%.6f'):format(state.udp.monotonic()))
        end
        receive()
        if state.stage ~= 'run' then return end
        local source = state.source
        source:tick()
        -- The fixture sampler answers its own pending request in the same frame,
        -- through the host's expected-request check.
        local pending = state.sampler and state.sampler.pending
        if pending then source:event(pending .. '|fixture') end
    end
end

SetScript('onUpdate', function()
    local ok, err = pcall(frame)
    if not ok and state.stage ~= 'failed' then
        state.stage = 'failed'
        log('ERROR ' .. tostring(err):gsub('%s+', ' '))
    end
end)
