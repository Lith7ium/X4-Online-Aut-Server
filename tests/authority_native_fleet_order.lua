dofile('tools/dev/authority_native_fleet_order.lua')

local lease, epoch, order = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local next_order = ('d'):rep(32)
local callback, packet, now
local sent, issued = {}, {}
function RegisterEvent(name, handler)
    assert(name == 'X4AuthorityNativeFleetOrder.Result')
    callback = handler
end
function AddUITriggeredEvent(screen, action, args)
    assert(screen == 'X4AuthorityNativeFleetOrder' and action == 'MoveWait')
    issued[#issued + 1] = args
end
local tick = X4LAN_AUTHORITY_NATIVE_FLEET_ORDER.new(lease, {
    send=function(_, value) sent[#sent + 1] = value end,
    pending=function() return packet ~= nil end,
    receive=function() local value = packet; packet = nil; return value end,
}, function() return now end)
local function queue(id, deadline, x, token)
    packet = table.concat({'FLEET_ORDER', token or lease, id, epoch, '7354', 'WRN-302',
        'cluster_01_sector001_macro', x or '-123456.25', '0', '82.125', deadline}, '|')
end
local function step(at, paused)
    now = at
    tick(paused)
end

step(0)
assert(sent[1] == 'FLEET_HELLO|' .. lease)
queue(order, 10, nil, epoch); step(1)
queue(order, 10, '1e999'); step(2)
queue(order, 3); step(3, true); step(4)
assert(#issued == 0)

queue(order, 10); step(5, true); step(6)
assert(#issued == 1)
local first = issued[1]
assert(first.lease == lease and first.order_id == order and first.epoch == epoch)
assert(first.ship_index == 7354 and first.ship_code == 'WRN-302' and first.sector == 'cluster_01_sector001_macro')
assert(first.x == -123456.25 and first.y == 0 and first.z == 82.125)
local function receipt(id, token)
    return table.concat({token or lease, id, epoch, '7354', 'WRN-302', 'cluster_01_sector001_macro',
        'accepted', 'MoveWait', '0x101', '0x102'}, '|')
end
callback(nil, receipt(order, epoch))
callback(nil, receipt(next_order))
assert(not sent[#sent]:match('^FLEET_RESULT|'))

-- The first event's result is lost, and its duplicate datagram outlives the deadline.
queue(order, 20); step(9); step(10)
assert(#issued == 1)
callback(nil, receipt(order))
assert(not sent[#sent]:match('^FLEET_RESULT|'))

queue(next_order, 20); step(11)
assert(#issued == 2)
callback(nil, receipt(next_order))
assert(sent[#sent] == 'FLEET_RESULT|' .. receipt(next_order))
queue(next_order, 20); step(12)
assert(#issued == 2)
