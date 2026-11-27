dofile(arg[1])
local driver = assert(X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE)
local lease, generation, epoch = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local now = 0
local inputs, outputs, actions, registered = {}, {}, {}, {}
local health_calls = {}
RegisterEvent = function(name, callback)
    registered[name] = callback
    return true
end
AddUITriggeredEvent = function(_, action, args)
    actions[#actions + 1] = {action, args}
    return true
end
local tick = driver.new(lease, {
    pending = function() return #inputs > 0 end,
    receive = function() return table.remove(inputs, 1) end,
    send = function(_, packet) outputs[#outputs + 1] = packet; return true end,
}, function() return now end, function() return false end, function(actor)
    health_calls[#health_calls + 1] = actor
    return {ok=true, owner='player', shield=100, shieldmax=2366, hull=3600, hullmax=3600}
end, function() return 1 end, true)

local function id(serial, ship, world)
    return table.concat({lease, serial, generation, world or epoch, ship}, '|')
end
local function read(serial, ship, slot)
    return 'GUN_HEALTH|' .. id(serial, ship) .. '|' .. (now + 2) .. '|' .. slot
end
local function health_reply(serial, ship, actor, world)
    registered['X4AuthorityCompanyGunDamage.HealthResult'](nil, id(serial, ship, world) .. '|registered|' .. actor)
end
local function expect(serial, ship, actor)
    assert(outputs[#outputs] == 'GUN_HEALTH_RESULT|' .. id(serial, ship) .. '|observed|' .. actor .. '|100|2366|3600|3600')
end

inputs = {read(4, 1076, 4), read(2, 1074, 2), read(3, 1075, 3), read(1, 1073, 1), read(5, 1077, 5)}
tick()
assert(#actions == 4)
for slot = 1, 4 do
    local row = actions[slot]
    assert(row[1] == 'Health' and row[2].serial == slot and row[2].source == 1072 + slot)
end
health_reply(3, 1074, '0x103')
health_reply(4, 1076, '0x104', ('d'):rep(32))
assert(#health_calls == 0)
for _, slot in ipairs({4, 2, 1}) do
    local actor = '0x10' .. slot
    health_reply(slot, 1072 + slot, actor)
    expect(slot, 1072 + slot, actor)
    local before = #outputs
    health_reply(slot, 1072 + slot, actor)
    assert(#outputs == before)
end

-- Slot reuse supersedes just the cancelled identity and preserves other reads.
inputs = {read(6, 1078, 3), read(7, 1079, 3), read(8, 1074, 2), read(3, 1075, 3)}
tick()
assert(#actions == 6 and actions[5][2].serial == 8 and actions[6][2].serial == 7)
health_reply(3, 1075, '0x103')
health_reply(6, 1078, '0x106')
assert(#health_calls == 3)
health_reply(7, 1079, '0x107')
expect(7, 1079, '0x107')

-- A real damage command in the same update suppresses new and pending reads.
inputs = {read(9, 1075, 1), 'GUN_IMPACT|' .. id(1, 1073) .. '|1074|26|4'}
tick()
assert(#actions == 7 and actions[7][1] == 'Impact')
health_reply(8, 1074, '0x108')
health_reply(9, 1075, '0x109')
assert(#health_calls == 4)
registered['X4AuthorityCompanyGunDamage.Result'](nil, id(1, 1073) .. '|1074|refused|null|null|0|-|-')
assert(outputs[#outputs] == 'GUN_RESULT|' .. id(1, 1073) .. '|1074|refused')
inputs = {read(10, 1075, 1)}
tick()
assert(#actions == 8 and actions[8][2].serial == 10)
health_reply(10, 1075, '0x110')
expect(10, 1075, '0x110')
