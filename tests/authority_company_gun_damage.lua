local callbacks, issued = {}, {}
function RegisterEvent(name, callback)
    assert(not callbacks[name])
    callbacks[name] = callback
    return true
end
function AddUITriggeredEvent(screen, action, args)
    assert(screen == 'X4AuthorityCompanyGunDamage' and action == 'Impact')
    issued[#issued + 1] = args
    return true
end

dofile(arg[1])
local lease, generation, epoch = string.rep('a', 32), string.rep('b', 32), string.rep('c', 32)
local paused, now, input = true, 1, nil
local outputs = {}
local receipt = {ok=true, shield=4.000000165, hull=3600, shieldmax=2366, hullmax=3600}
local tick = X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE.new(lease, {
    pending=function() return input ~= nil end,
    receive=function() local value = input; input = nil; return value end,
    send=function(_, row) outputs[#outputs + 1] = row; return true end,
}, function() return now end, function() return paused end, function(actor)
    assert(actor == '0x103')
    return receipt
end)

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
    for name, value in pairs({Amount=26, BeforeShield=30, BeforeHull=3600,
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
observations()
received('Result', identity(1) .. '|applied|0x102|0x103|0')
local confirmed = 'GUN_RESULT|' .. identity(1) .. '|applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|'
assert(outputs[2]:sub(1, #confirmed) == confirmed)
input = input_for(1, 4)
tick()
assert(#issued == 1 and #outputs == 2)

input = input_for(2, 4)
tick()
observations()
receipt.hull = 3590
received('Result', identity(2) .. '|applied|0x102|0x103|0')
assert(#issued == 2 and outputs[3] == 'GUN_RESULT|' .. identity(2) .. '|unknown')

input = input_for(3, 1)
tick()
assert(#issued == 2 and #outputs == 3)
input = input_for(3, 4)
tick()
received('Result', identity(3) .. '|refused|null|null|0')
assert(#issued == 3 and outputs[4] == 'GUN_RESULT|' .. identity(3) .. '|refused')
