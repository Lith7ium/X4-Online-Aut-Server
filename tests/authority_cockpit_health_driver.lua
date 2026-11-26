-- Offline wiring check for tools/dev/authority_cockpit_health_x4.lua with the real receiver,
-- the real motion source host and the real Lua health reader. X4, MD and UDP are emulated.
local root = assert(arg and arg[1], 'repository root required')
local lease, generation, epoch = ('1'):rep(32), ('2'):rep(32), '5e1c0d7a3b9f42e68a0c4d2f7b1e9a36'
local admission = ('3'):rep(32)
local prefix = 'C|' .. lease .. '|' .. generation .. '|'
local now, game_time, paused = 1000.0, 50.0, false
local logs, sent, inbox, handlers, queued, commands = {}, {}, {}, {}, {}, {}
local update
local ship = {id = 0x179705, text = '0x179705', shield = 1000, maxshield = 1000, hull = 3000, maxhull = 3000}
local proposal, last_request = nil, 0

package.loaded.ffi = {cdef = function() end, string = function(value) return value end, NULL = false, C = {
    IsStartmenu = function() return false end,
    IsPlayerValid = function() return true end,
    IsGamePaused = function() return paused end,
    GetSaveFolderPath = function() return 'C:\\Users\\t\\Documents\\Egosoft\\X4\\45856\\save' end,
    GetCurrentGameTime = function() return game_time end,
    GetPlayerControlledShipID = function() return ship.id end,
    GetPlayerOccupiedShipID = function() return ship.id end,
    GetObjectPositionInSector = function(id) assert(id == ship.id); return {x = 1, y = 2, z = 3} end,
}}

function DebugError(message) logs[#logs + 1] = message end
function RegisterEvent(name, callback) handlers[name] = callback; return true end
function SetScript(name, callback) assert(name == 'onUpdate'); update = callback end
function ConvertIDTo64Bit(value) return tonumber(value) end
function ConvertStringToLuaID(value) return tonumber(value) end
function GetComponentData(ref, ...)
    assert(ref == ship.id)
    local values = {hullpercent = 100 * ship.hull / ship.maxhull, shieldpercent = 100 * ship.shield / ship.maxshield,
        hull = ship.hull, hullmax = ship.maxhull, shield = ship.shield, shieldmax = ship.maxshield,
        owner = 'player', macro = 'ship_test_macro'}
    local out = {}
    for i, key in ipairs({...}) do out[i] = values[key] end
    return unpack(out)
end
function AddUITriggeredEvent(screen, control, args)
    commands[#commands + 1] = {screen = screen, control = control, args = args}
    if screen == 'X4AuthorityShipRegistry' then
        queued[#queued + 1] = {'X4AuthorityShipRegistry.Correlated', args.request_id .. '|Initialized|' .. args.epoch}
    elseif screen == 'X4AuthorityCockpitHealthProbe' and control == 'Setup' then
        queued[#queued + 1] = {'X4AuthorityCockpitHealthProbe.Result', table.concat({args.request_id, 'Ready',
            ship.text, 'ABC-123', 'ship_test_macro', 'cluster_14_sector001_macro', ship.shield, ship.maxshield,
            ship.hull, ship.maxhull}, '|')}
    elseif screen == 'X4AuthorityCockpitHealthProbe' then
        logs[#logs + 1] = ('[X4LAN] AUTH_COCKPIT_HEALTH_READBACK tag=%s shield=%s hull=%s'):format(
            args.tag, ship.shield, ship.hull)
    elseif screen == 'X4AuthorityCompanyCockpitHealth' then
        assert(args.lease == lease)
        local status
        if control == 'Clear' then
            last_request, proposal = math.max(last_request, args.request), nil
        elseif control == 'Prepare' and args.request > last_request then
            last_request, proposal = args.request, nil
            status = 'prepare_rejected'
            if args.actor == ship.text and args.maxshield == ship.maxshield and args.maxhull == ship.maxhull
                    and args.validity > 0 and args.validity <= 0.25 then
                proposal, status = args, 'prepared'
            end
        elseif control == 'Apply' and proposal and args.request == proposal.request then
            ship.shield, ship.hull = proposal.shield, proposal.hull
            proposal, status = nil, 'applied'
        end
        if status then
            queued[#queued + 1] = {'X4AuthorityCompanyCockpitHealth.Result', lease .. '|' .. args.request .. '|' .. status}
        end
    end
end
package.loadlib = function(path, symbol)
    assert(path:match('native/x4lan_net_64%.dll$') and symbol == 'luaopen_x4lan_net')
    return function()
        return {monotonic = function() return now end, open = function(host, port)
            assert(host == '127.0.0.1' and port == 47000)
            return {send = function(_, packet) sent[#sent + 1] = packet; return true end,
                receive = function() return table.remove(inbox, 1) end}
        end}
    end
end

X4NEON_COCKPIT_HEALTH = {enabled = true, extension_id = 'x4_neon_cockpit_health', profile = '45856',
    epoch = epoch, udp_port = 47000, lease = lease, generation = generation}
for _, path in ipairs({'src/authority/lua/combat_observer.lua', 'src/authority/lua/company_cockpit_health.lua',
                       'src/authority/lua/company_motion_source.lua', 'tools/dev/authority_cockpit_health_x4.lua'}) do
    dofile(root .. '/' .. path)
end
assert(update, 'driver did not install its update hook')

local function frames(count)
    for _ = 1, count do
        now, game_time = now + 1 / 60, game_time + 1 / 60
        local pending = queued
        queued = {}
        for _, event in ipairs(pending) do handlers[event[1]](nil, event[2]) end
        update()
        for _, line in ipairs(logs) do assert(not line:match('DRIVER ERROR'), line) end
    end
end

local function find(list, pattern)
    for _, line in ipairs(list) do if line:match(pattern) then return line end end
end

local function count(list, pattern)
    local total = 0
    for _, line in ipairs(list) do if line:match(pattern) then total = total + 1 end end
    return total
end

local function last_sample()
    for i = #sent, 1, -1 do
        local sequence = sent[i]:match('^' .. prefix:gsub('%p', '%%%0') .. 'SAMPLE3|[^|]+|1|(%d+)|')
        if sequence then return tonumber(sequence) end
    end
end

local function health(revision, sequence, anchor, expires, shield, hull)
    return prefix .. table.concat({'HEALTH', revision, admission, epoch, 1, 1, sequence, anchor,
        string.format('%.17g', expires), shield, ship.maxshield, hull, ship.maxhull}, '|')
end

frames(130)
assert(find(logs, 'DRIVER READY actor=0x179705 .* lua_maxshield=1000 lua_hull=3000 lua_maxhull=3000'), 'no READY')
frames(30)
local before_gap = assert(last_sample(), 'no SAMPLE3')
assert(before_gap >= 4)

inbox[#inbox + 1] = prefix .. 'HEALTH_CLEAR|1'
inbox[#inbox + 1] = health(1, 1, before_gap, now + 2, 400, 1650)
frames(6)
assert(ship.shield == 400 and ship.hull == 1650, 'downward correction not applied')
assert(find(sent, '^' .. prefix:gsub('%p', '%%%0') .. 'HEALTH_RESULT|' .. admission .. '|' .. epoch .. '|1|1|1|confirmed$'))
assert(find(logs, 'RECEIPT window=1 sequence=1 status=confirmed shield=400'))
assert(find(logs, 'READBACK tag=receipt%-1%-confirmed shield=400 hull=1650'))

inbox[#inbox + 1] = health(1, 2, last_sample(), now + 2, 900, 2550)
frames(6)
assert(ship.shield == 900 and ship.hull == 2550, 'upward correction not applied')

local held = health(1, 3, last_sample(), now + 2, 250, 900)
inbox[#inbox + 1] = prefix .. 'TEST|gap|0.8'
frames(3)
assert(find(logs, 'DRIVER GAP_BEGIN') and find(sent, 'GAP$'), 'no gap')
frames(60)
assert(find(logs, 'DRIVER GAP_END'))
frames(30)
local after_gap = last_sample()
assert(after_gap > before_gap + 5)
inbox[#inbox + 1] = prefix .. 'HEALTH_CLEAR|2'
frames(2)
now = now + 2.5
local issued = #commands
inbox[#inbox + 1] = held
frames(10)
for i = issued + 1, #commands do
    assert(commands[i].screen ~= 'X4AuthorityCompanyCockpitHealth', 'held update reached the MD')
end
assert(ship.shield == 900 and ship.hull == 2550, 'held update changed the ship')
assert(count(sent, 'HEALTH_RESULT|.*|1|1|3|') == 0, 'held update produced a receipt')
assert(find(logs, 'RX HEALTH revision=1 window=1 sequence=3 anchor=%d+ deadline_in=%-'))

inbox[#inbox + 1] = health(2, 4, last_sample(), now + 2, 650, 2100)
frames(6)
assert(ship.shield == 650 and ship.hull == 2100, 'fresh baseline not applied')
assert(find(sent, 'HEALTH_RESULT|' .. admission .. '|' .. epoch .. '|1|1|4|confirmed$'))

inbox[#inbox + 1] = prefix .. 'TEST|done'
frames(2)
assert(find(logs, 'DRIVER DONE'))
print('cockpit health driver wiring ok: ' .. #sent .. ' datagrams, ' .. #logs .. ' log lines')
