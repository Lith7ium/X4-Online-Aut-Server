local capture = dofile('src/authority/lua/company_fleet_capture.lua')
local source = dofile('src/authority/lua/company_fleet_source.lua')
local epoch, lease = string.rep('0', 32), string.rep('b', 32)
local prefix = 'F|' .. lease .. '|'
local version = tonumber(arg[1]) or 1
local ship, sector, other_sector, occupied, npc = 905123, 223476, 223477, 906150, 975123
local actual_sector = sector
local controlled = true
local num_orders = 0
local control_pressed, shift_pressed = false, false
local stock, closed, time = 0, 0, 1
local events, messages, sent, incoming = {}, {}, {}, {}

local function original()
    stock = stock + 1
end

local map = {
    name = 'MapMenu', shown = true, orderMoveWait = original,
    selectedcomponents = {[tostring(ship)] = true},
}
local interact = {
    name = 'InteractMenu', buttonMoveWait = original, orderMoveWait = original,
    selectedplayerships = {ship}, numorderloops = 0, offsetcomponent = sector,
    offset = {x = -1200.25, y = 44.5, z = 1e-5}, componentSlot = {component = sector},
    priorityOrderMode = false, clearOtherOrders = true, clearOrderQueueByDefault = true,
    onCloseElement = function(reason)
        assert(reason == 'close')
        closed = closed + 1
    end,
}
assert(not capture.install({enabled = false, map = map, interact = interact}))
assert(map.orderMoveWait == original and interact.buttonMoveWait == original and interact.orderMoveWait == original)

local properties = {
    [ship] = {isplayerowned = true, assignedpilot = npc, isdocked = false, isdocking = false,
        isinternallystored = false, idcode = 'WRN-302'},
    [sector] = {macro = 'cluster_01_sector001_macro'},
    [other_sector] = {macro = 'cluster_01_sector002_macro'},
}
function GetComponentData(id, ...)
    local values = {}
    for i = 1, select('#', ...) do values[i] = properties[id][select(i, ...)] end
    return unpack(values, 1, select('#', ...))
end
ConvertStringToLuaID = tonumber
ConvertStringTo64Bit = tonumber
function ConvertIDTo64Bit(value) return value end
function DebugError(line) messages[#messages + 1] = line end

package.loaded.ffi = {cdef = function() end, typeof = function() end, C = {
    IsPlayerValid = function() return true end,
    IsGamePaused = function() return false end,
    IsComponentClass = function(id, class)
        return (id == ship and class == 'ship') or ((id == sector or id == other_sector) and class == 'sector')
            or (id == npc and class == 'npc')
    end,
    IsComponentOperational = function(id) return id == ship end,
    GetPlayerControlledShipID = function() return controlled and occupied or 0 end,
    GetPlayerOccupiedShipID = function() return occupied end,
    GetContextByClass = function() return actual_sector end,
    GetNumOrders = function() return num_orders end,
    IsOrderSelectableFor = function(kind, id) return kind == 'MoveWait' and id == ship end,
    IsControlPressed = function() return control_pressed end,
    IsShiftPressed = function() return shift_pressed end,
    ShowInfoLine = function(text) assert(type(text) == 'string') end,
}}
local event_handler
function RegisterEvent(name, handler)
    assert(name == 'X4AuthorityCompanyFleetLookup.Result')
    event_handler = handler
end
function AddUITriggeredEvent(screen, action, payload)
    assert(screen == 'X4AuthorityCompanyFleetLookup' and action == 'Lookup')
    assert(payload.epoch == epoch and payload.ship == ship)
    events[#events + 1] = payload
end
Menus = {map, interact}
X4LAN_RESOLVE_STRING = {parse_hex = function(value) return tonumber(value) end}
local seat = {controlled_actor = function() return tostring(occupied) end}
X4LAN_AUTHORITY_SELECTED_SEAT = seat
local ipc = source.new_x4({epoch = epoch, lease = lease, port = 12345, version = version == 2 and 2 or nil}, {epoch = epoch, operational_bits = '01'}, {
    monotonic = function() return time end,
    open = function(host, port)
        assert(host == '127.0.0.1' and port == 12345)
        return {
            send = function(_, packet)
                sent[#sent + 1] = packet
                return true
            end,
            receive = function() return table.remove(incoming, 1) end,
        }
    end,
})

local function status(expected)
    assert(messages[#messages]:find('AUTH_COMPANY_FLEET ' .. expected, 1, true), messages[#messages])
end
local function binding(id, index, actor)
    event_handler(nil, table.concat({id, epoch, index, actor}, '|'))
end
assert(ipc:prepare())
interact.buttonMoveWait(false)
status('unavailable')
assert(ipc:prepare(seat))
ipc:set_running(true)

if version == 2 then
    map.orderMoveWait(ship, sector)
    status('unsupported')
    interact.priorityOrderMode = true
    interact.buttonMoveWait(false)
    status('unsupported')
    interact.priorityOrderMode = false
    interact.numorderloops = 1
    interact.buttonMoveWait(false)
    status('unsupported')
    interact.numorderloops = 0
    interact.buttonMoveWait(nil)
    status('unsupported')

    -- Exercise a busy target, stock default flags, each possible queue toggle,
    -- and the explicit clear argument without forwarding any local order calls.
    num_orders = 7
    local cases = {
        {clear = false, queue_clear = false, default_clear = false, mode = 'append'},
        {clear = false, queue_clear = true, default_clear = true, mode = 'replace'},
        {clear = false, queue_clear = false, default_clear = true, shift = true, mode = 'append'},
        {clear = false, queue_clear = true, default_clear = false, control = true, mode = 'replace'},
        {clear = true, queue_clear = false, default_clear = false, mode = 'replace'},
    }
    for index, case in ipairs(cases) do
        interact.clearOtherOrders = case.queue_clear
        interact.clearOrderQueueByDefault = case.default_clear
        shift_pressed, control_pressed = case.shift == true, case.control == true
        interact.buttonMoveWait(case.clear)
        assert(#events == index)
        local id = events[index].order_id
        interact.clearOtherOrders = not case.queue_clear
        binding(id, 2, ship)
        ipc:tick()
        status('pending')
        assert(#sent == index and sent[index]:find('|ORDER|' .. id .. '|2|WRN-302|', 1, true)
            and sent[index]:match('|([a-z]+)$') == case.mode)
        incoming[1] = prefix .. 'RESULT|' .. id .. '|accepted'
        ipc:tick()
        status('accepted')
    end
    assert(stock == 0)
    return
end

map.orderMoveWait(ship, sector)
interact.orderMoveWait(ship, sector)
status('unsupported')
assert(stock == 0)
interact.selectedplayerships[2] = 8
interact.buttonMoveWait(false)
status('unsupported')
interact.selectedplayerships[2] = nil
control_pressed = true
interact.buttonMoveWait(false)
status('legacy_queue_override')
control_pressed = false
interact.buttonMoveWait(true)
status('legacy_queue_override')
interact.componentSlot.component = 88
interact.buttonMoveWait(false)
status('unsupported')
interact.componentSlot.component = sector
interact.offset.x = math.huge
interact.buttonMoveWait(false)
status('unsupported')
interact.offset.x = -1200.25
actual_sector = other_sector
interact.buttonMoveWait(false)
status('ship_unavailable')
actual_sector = sector
num_orders = 1
interact.buttonMoveWait(false)
status('legacy_ship_busy')
num_orders = 0
assert(#events == 0 and #sent == 0 and stock == 0)

interact.buttonMoveWait(false)
assert(#events == 1)
interact.buttonMoveWait(false)
status('busy')
local first = events[1].order_id
binding(first, 2, occupied)
status('binding_unavailable')
ipc:tick()
assert(#sent == 0)

interact.buttonMoveWait(false)
local second = events[2].order_id
assert(second ~= first)
binding(first, 2, ship)
binding(second, 2, ship)
ipc:tick()
status('pending')
assert(#sent == 1 and sent[1]:find('|ORDER|' .. second .. '|2|WRN-302|cluster_01_sector001_macro|', 1, true))
local x, y, z = sent[1]:match('|([^|]+)|([^|]+)|([^|]+)$')
assert(tonumber(x) == interact.offset.x and tonumber(y) == interact.offset.y and tonumber(z) == interact.offset.z)
time = time + 0.5
ipc:tick()
assert(#sent == 2 and sent[1] == sent[2] and stock == 0 and closed == 11)

incoming[1] = prefix .. 'RESULT|' .. first .. '|accepted'
incoming[2] = prefix .. 'RESULT|' .. second .. '|accepted|invalid_reason'
incoming[3] = prefix .. 'RESULT|' .. second .. '|accepted'
ipc:tick()
status('accepted')

interact.buttonMoveWait(false)
local third = events[3].order_id
binding(third, 2, ship)
ipc:tick()
ipc:set_running(false)
status('unknown')
ipc:set_running(true)
time = time + 31
ipc:tick()
assert(#sent == 3)
interact.buttonMoveWait(false)
status('busy')
incoming[1] = prefix .. 'RESULT|' .. third .. '|refused|ship_reserved'
ipc:tick()
status('refused')
assert(messages[#messages]:find('ship_reserved', 1, true))

map.shown = false
interact.buttonMoveWait(false)
interact.orderMoveWait(ship, sector)
status('unsupported')
assert(stock == 0)
