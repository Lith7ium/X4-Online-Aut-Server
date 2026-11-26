-- Private, one-shot native stock damage requests from the owned provider.
local M = {}
local screen = 'X4AuthorityCompanyGunDamage'
local health_fields = {'Amount', 'BeforeShield', 'BeforeHull', 'AfterShield', 'AfterHull', 'MaxShield', 'MaxHull'}

local function split(row)
    local fields = {}
    for value in (row .. '|'):gmatch('(.-)|') do fields[#fields + 1] = value end
    return fields
end

local function token(value)
    return #value == 32 and value:match('^[0-9a-f]+$')
end

local function number(value, minimum, maximum)
    local parsed = tonumber(value)
    if parsed and parsed >= minimum and parsed <= maximum then return parsed end
end

local function index(value, maximum)
    local parsed = number(value, 1, maximum)
    if parsed and parsed % 1 == 0 and value == string.format('%.0f', parsed) then return parsed end
end

local function near(first, second)
    return type(first) == 'number' and type(second) == 'number'
        and math.abs(first - second) <= math.max(.000001, math.abs(first) * 1e-11)
end

function M.new(lease, net, monotonic, paused, read_health)
    local active, next_hello
    local last_serial = 0

    for _, field in ipairs(health_fields) do
        assert(RegisterEvent(screen .. '.' .. field, function(_, value)
            if not active then return end
            if active.health[field] ~= nil or type(value) ~= 'number'
                    or not number(value, 0, 9007199254740991) then
                active.invalid = true
            else
                active.health[field] = value
            end
        end) ~= false)
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or type(row) ~= 'string' or #row > 512 then return end
        local attempt = active
        if row:sub(1, #attempt.identity + 1) ~= attempt.identity .. '|' then return end
        active = nil
        local fields = split(row)
        local status, source, target, killed = fields[7], fields[8], fields[9], fields[10]
        local reply = 'unknown'
        if #fields == 10 and status == 'refused' and next(attempt.health) == nil and not attempt.invalid then
            reply = 'refused'
        elseif #fields == 10 and status == 'applied' and not attempt.invalid
                and not paused() and monotonic() < attempt.deadline then
            local hp = attempt.health
            local ok, observed = pcall(read_health, target)
            if ok and observed and observed.ok and hp.Amount == attempt.amount and hp.BeforeShield and hp.BeforeHull
                    and near(hp.AfterShield, observed.shield) and near(hp.AfterHull, observed.hull)
                    and near(hp.MaxShield, observed.shieldmax) and near(hp.MaxHull, observed.hullmax) then
                local numbers = {}
                for _, field in ipairs(health_fields) do
                    numbers[#numbers + 1] = string.format('%.17g', hp[field])
                end
                for _, field in ipairs({'shield', 'hull', 'shieldmax', 'hullmax'}) do
                    numbers[#numbers + 1] = string.format('%.17g', observed[field])
                end
                reply = table.concat({'applied', source, target, killed, table.concat(numbers, '|')}, '|')
            end
        end
        net:send('GUN_RESULT|' .. attempt.identity .. '|' .. reply)
    end) ~= false)

    return function()
        local now = monotonic()
        if not now or not (now >= 0 and now < math.huge) then return end
        if not next_hello or now >= next_hello then
            net:send('GUN_HELLO|' .. lease)
            next_hello = now + 1
        end
        if active and now >= active.deadline then active = nil end
        if active or paused() or not net:pending() then return end
        local packet = net:receive()
        if type(packet) ~= 'string' or #packet > 512 then return end
        local fields = split(packet)
        if #fields ~= 9 or fields[1] ~= 'GUN_IMPACT' or fields[2] ~= lease then return end
        local serial = index(fields[3], 2147483647)
        local source, target = index(fields[6], 200000), index(fields[7], 200000)
        local amount = number(fields[8], 0, 9007199254740991)
        local deadline = number(fields[9], now, now + 30)
        if not serial or serial <= last_serial or not token(fields[4]) or not token(fields[5])
                or not source or not target or target == source
                or not amount or amount == 0 or not deadline or deadline == now then return end
        last_serial = serial
        active = {identity=table.concat(fields, '|', 2, 7), deadline=deadline, amount=amount, health={}}
        -- Record the attempt before calling the engine. Never reissue on failure.
        pcall(AddUITriggeredEvent, screen, 'Impact', {lease=lease, serial=serial,
            generation=fields[4], epoch=fields[5], source=source, target=target, amount=amount})
    end
end

X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE = M
