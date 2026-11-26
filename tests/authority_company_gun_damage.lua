local callbacks, issued, diagnostics = {}, {}, {}
function DebugError(value)
    diagnostics[#diagnostics + 1] = value
end
function RegisterEvent(name, callback)
    assert(not callbacks[name])
    callbacks[name] = callback
    return true
end
function AddUITriggeredEvent(screen, action, args)
    assert(screen == 'X4AuthorityCompanyGunDamage' and (action == 'Impact' or action == 'Health'))
    args.action = action
    issued[#issued + 1] = args
    return true
end

dofile(arg[1])
local lease, generation, epoch = string.rep('a', 32), string.rep('b', 32), string.rep('c', 32)
local paused, now, input = true, 1, nil
local outputs = {}
local receipt = {ok=true, shield=4.000000165, hull=3600, shieldmax=2366, hullmax=3600}
local own_health = {ok=true, owner='player', shield=100.25, hull=3500, shieldmax=2366, hullmax=3600}
local read_calls = 0
local tick = X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE.new(lease, {
    pending=function() return input ~= nil end,
    receive=function()
        if type(input) == 'table' then
            local value = table.remove(input, 1)
            if #input == 0 then input = nil end
            return value
        end
        local value = input
        input = nil
        return value
    end,
    send=function(_, row) outputs[#outputs + 1] = row; return true end,
}, function() return now end, function() return paused end, function(actor)
    if actor == '0x102' then
        read_calls = read_calls + 1
        return own_health
    end
    assert(actor == '0x103')
    return receipt
end, function() return 3.53 end)

local function identity(serial)
    return table.concat({lease, serial, generation, epoch, 1073, 1074}, '|')
end

local function input_for(serial, deadline)
    return 'GUN_IMPACT|' .. identity(serial) .. '|26|' .. deadline
end

local function received(name, value)
    callbacks['X4AuthorityCompanyGunDamage.' .. name](nil, value)
end

local function observations()
    for name, value in pairs({Amount='26', BeforeShield=30, BeforeHull=3600,
                              AfterShield=4, AfterHull=3600, MaxShield=2366, MaxHull=3600}) do
        received(name, value)
    end
end

tick()
assert(#issued == 0 and #outputs == 1 and outputs[1] == 'GUN_HELLO|' .. lease)
input = input_for(1, 4)
tick()
assert(#issued == 0 and input ~= nil and #outputs == 1)
paused = false
tick()
assert(#issued == 1 and issued[1].serial == 1 and issued[1].amount == 26 and input == nil)
local witness = '|3.51,3.52,30,3600,4,3600,2366,3600'
received('Result', identity(1) .. '|applied|0x102|0x103|0' .. witness)
assert(#outputs == 1)
observations()
local confirmed = 'GUN_RESULT|' .. identity(1) .. '|applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|'
assert(outputs[2]:sub(1, #confirmed) == confirmed)
input = input_for(1, 4)
tick()
assert(#issued == 1 and #outputs == 2)

input = input_for(2, 4)
tick()
observations()
receipt.hull = 3590
received('Result', identity(2) .. '|applied|0x102|0x103|0' .. witness)
assert(#issued == 2 and outputs[3] == 'GUN_RESULT|' .. identity(2) .. '|unknown')
assert(#diagnostics == 1 and diagnostics[1]:find('reason=health_readback_mismatch', 1, true)
    and diagnostics[1]:find('lua_hull=3590', 1, true) and diagnostics[1]:find('elapsed_game_age=', 1, true))

input = input_for(3, 1)
tick()
assert(#issued == 2 and #outputs == 3)
input = input_for(3, 4)
tick()
received('Result', identity(3) .. '|refused|null|null|0|-')
assert(#issued == 3 and outputs[4] == 'GUN_RESULT|' .. identity(3) .. '|refused')

local function health_identity(serial)
    return table.concat({lease, serial, generation, epoch, 1073}, '|')
end

local function read_for(serial)
    return 'GUN_HEALTH|' .. health_identity(serial) .. '|2'
end

input = read_for(1)
tick()
assert(#issued == 4 and issued[4].action == 'Health' and issued[4].source == 1073 and issued[4].target == nil)
received('HealthResult', health_identity(2) .. '|registered|0x102')
assert(read_calls == 0 and #outputs == 4)
received('HealthResult', health_identity(1) .. '|registered|0x102')
assert(read_calls == 1 and outputs[5] == 'GUN_HEALTH_RESULT|' .. health_identity(1) ..
    '|observed|0x102|100.25|2366|3500|3600')

input = read_for(2)
tick()
own_health.owner = 'argon'
received('HealthResult', health_identity(2) .. '|registered|0x102')
assert(read_calls == 2 and outputs[6] == 'GUN_HEALTH_RESULT|' .. health_identity(2) .. '|refused')
own_health.owner = 'player'

input = read_for(3)
tick()
input = input_for(4, 4)
tick()
assert(#issued == 7 and issued[6].action == 'Health' and issued[7].action == 'Impact')
received('HealthResult', health_identity(3) .. '|registered|0x102')
assert(read_calls == 2 and #outputs == 6)
received('Result', identity(4) .. '|refused|null|null|0|-')
assert(outputs[7] == 'GUN_RESULT|' .. identity(4) .. '|refused')

input = {read_for(4), input_for(5, 4)}
tick()
assert(#issued == 8 and issued[8].action == 'Impact' and issued[8].serial == 5)
received('HealthResult', health_identity(4) .. '|registered|0x102')
assert(read_calls == 2 and #outputs == 7)
