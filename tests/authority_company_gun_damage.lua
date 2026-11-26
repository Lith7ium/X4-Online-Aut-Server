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
    assert(screen == 'X4AuthorityCompanyGunDamage' and (action == 'Impact' or action == 'Health' or action == 'Group'))
    args.action = action
    issued[#issued + 1] = args
    return true
end

dofile(arg[1])
local lease, generation, epoch = string.rep('a', 32), string.rep('b', 32), string.rep('c', 32)
local paused, now, input = true, 1, nil
local outputs = {}
local receipt = {ok=true, shield=3.9999997019768, hull=6400, shieldmax=5750, hullmax=6400}
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
end, function() return 3.53 end, true)

local function identity(serial)
    return table.concat({lease, serial, generation, epoch, 1073, 1074}, '|')
end

local function input_for(serial, deadline)
    return 'GUN_IMPACT|' .. identity(serial) .. '|26|' .. deadline
end

local function received(name, value)
    callbacks['X4AuthorityCompanyGunDamage.' .. name](nil, value)
end

local function fixed_point(values)
    local chunks = {}
    for _, value in ipairs(values) do
        local block = math.floor(value / 67108864)
        local remainder = value - block * 67108864
        local whole = math.floor(remainder)
        local fraction = math.floor((remainder - whole) * 1048576)
        chunks[#chunks + 1] = string.format('%.0f,%.0f,%.0f,', block, whole, fraction)
    end
    return table.concat(chunks)
end

local function applied(serial, values, witness)
    received('Result', identity(serial) .. '|applied|0x102|0x103|0|' .. witness .. '|' .. fixed_point(values))
end

local function impact_numbers(output, serial)
    local prefix = 'GUN_RESULT|' .. identity(serial) .. '|applied|0x102|0x103|0|'
    assert(output:sub(1, #prefix) == prefix)
    local values = {}
    for value in (output:sub(#prefix + 1) .. '|'):gmatch('(.-)|') do
        values[#values + 1] = assert(tonumber(value))
    end
    return values
end

tick()
assert(#issued == 0 and #outputs == 1 and outputs[1] == 'GUN_HELLO|' .. lease)
input = input_for(1, 4)
tick()
assert(#issued == 0 and input ~= nil and #outputs == 1)
paused = false
tick()
assert(#issued == 1 and issued[1].serial == 1 and issued[1].amount == 26 and input == nil)
local witness = '3.51,3.52,30,6400,4,6400,5750,6400'
local native = {26, 29.9999997019768, 6400, receipt.shield, 6400, 5750, 6400}
applied(1, native, witness)
local confirmed = impact_numbers(outputs[2], 1)
assert(math.abs(confirmed[2] - native[2]) < 1e-6 and math.abs(confirmed[4] - native[4]) < 1e-6)
assert(confirmed[8] == receipt.shield and confirmed[9] == receipt.hull)
input = input_for(1, 4)
tick()
assert(#issued == 1 and #outputs == 2)

input = input_for(2, 4)
tick()
receipt.hull = 3590
applied(2, native, witness)
assert(#issued == 2 and outputs[3] == 'GUN_RESULT|' .. identity(2) .. '|unknown')
assert(#diagnostics == 1 and diagnostics[1]:find('reason=health_readback_mismatch', 1, true)
    and diagnostics[1]:find('lua_hull=3590', 1, true) and diagnostics[1]:find('elapsed_game_age=', 1, true))

input = input_for(3, 1)
tick()
assert(#issued == 2 and #outputs == 3)
input = input_for(3, 4)
tick()
received('Result', identity(3) .. '|refused|null|null|0|-|-')
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
received('Result', identity(4) .. '|refused|null|null|0|-|-')
assert(outputs[7] == 'GUN_RESULT|' .. identity(4) .. '|refused')

input = {read_for(4), input_for(5, 4)}
tick()
assert(#issued == 8 and issued[8].action == 'Impact' and issued[8].serial == 5)
received('HealthResult', health_identity(4) .. '|registered|0x102')
assert(read_calls == 2 and #outputs == 7)

local limit = 9007199254740991
receipt.shield, receipt.shieldmax, receipt.hull, receipt.hullmax = 0, limit, limit - 22, limit
applied(5, {26, 4, limit, 0, limit - 22, limit, limit}, witness)
local high = impact_numbers(outputs[8], 5)
assert(high[4] == 0 and high[5] == limit - 22 and high[7] == limit and high[9] == limit - 22)

input = input_for(6, 4)
tick()
received('Result', identity(6) .. '|applied|0x102|0x103|0|' .. witness .. '|134217729,0,0,')
assert(outputs[9] == 'GUN_RESULT|' .. identity(6) .. '|unknown')


local function group_identity(serial)
    return table.concat({lease, serial, generation, epoch, 1073}, '|')
end

local function group_packet(serial, offset, count, deadline, rows)
    return table.concat({'GUN_GROUP', group_identity(serial), offset, count, deadline, rows}, '|')
end

local function group_result(serial, row)
    received('GroupResult', group_identity(serial) .. '|' .. row)
end

local ordered = group_packet(7, 1, 3, 4, '1074,26|943,26|1074,26')
input = ordered
tick()
assert(#issued == 10 and issued[10].action == 'Group' and issued[10].count == 3
    and issued[10].target1 == 1074 and issued[10].target2 == 943 and issued[10].target3 == 1074
    and issued[10].amount1 == 26 and issued[10].amount2 == 26 and issued[10].amount3 == 26)
local function group_row(serial, ordinal, target, actor, hp)
    group_result(serial, table.concat({'row', ordinal, target, '3.51', '0x104', actor, fixed_point(hp)}, '|'))
end
receipt.shield, receipt.hull, receipt.shieldmax, receipt.hullmax = 0, 3578, 2366, 3600
own_health.shield, own_health.hull = 2340, 3600
group_row(7, 1, 1074, '0x103', {26,30,3600,4,3600,2366,3600})
group_row(7, 2, 943, '0x102', {26,2366,3600,2340,3600,2366,3600})
group_row(7, 3, 1074, '0x103', {26,4,3600,0,3578,2366,3600})
assert(#outputs == 9 and read_calls == 2)
group_result(7, 'complete')
local prefix = 'GUN_GROUP_RESULT|' .. group_identity(7) .. '|'
assert(outputs[10] == prefix .. 'row|1|1074|3.51|applied|0x104|0x103|0|26|30|3600|4|3600|2366|3600|0|3578|2366|3600')
assert(outputs[11]:sub(1, #(prefix .. 'row|2|943')) == prefix .. 'row|2|943')
assert(outputs[12]:sub(1, #(prefix .. 'row|3|1074')) == prefix .. 'row|3|1074')
assert(outputs[13] == prefix .. 'complete' and read_calls == 3)
input = {ordered, group_packet(7, 1, 3, 4, '943,26|1074,26|1074,26')}
tick()
assert(#issued == 10 and #outputs == 13)

local eight = ('1074,26|'):rep(7) .. '1074,26'
input = group_packet(8, 1, 9, 4, eight)
tick()
assert(#issued == 10)
input = group_packet(8, 9, 9, 4, '943,26')
tick()
assert(#issued == 11 and issued[11].target9 == 943)
group_row(8, 1, 1074, '0x103', {26,30,3600,4,3600,2366,3600})
group_result(8, 'complete')
assert(outputs[14] == 'GUN_GROUP_RESULT|' .. group_identity(8) .. '|unknown' and #outputs == 14)

input = group_packet(9, 1, 3, 4, '1074,26|943,26|1074,26')
tick()
group_result(9, 'refused')
assert(outputs[15] == 'GUN_GROUP_RESULT|' .. group_identity(9) .. '|refused' and #issued == 12)
