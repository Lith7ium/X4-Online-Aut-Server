-- Private native stock damage and health requests from the owned provider.
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

local function ship_health(observed)
    local maximum = 9007199254740991
    return type(observed) == 'table' and observed.ok and observed.owner == 'player'
        and type(observed.shieldmax) == 'number' and type(observed.hullmax) == 'number'
        and type(observed.shield) == 'number' and type(observed.hull) == 'number'
        and number(observed.shieldmax, 0, maximum) and number(observed.hullmax, 0, maximum)
        and number(observed.shield, 0, observed.shieldmax)
        and number(observed.hull, 0, observed.hullmax) and observed.hull > 0
end

function M.new(lease, net, monotonic, paused, read_health, game_time)
    local active, reading, next_hello
    local last_serial, last_health_serial = 0, 0

    local function finish_impact(reply, reason)
        local attempt = active
        active = nil
        if reason and DebugError then
            local hp, readback = attempt.health, attempt.readback or {}
            local native_numbers = {}
            for _, field in ipairs(health_fields) do
                native_numbers[#native_numbers + 1] = field .. '=' .. tostring(hp[field])
            end
            local result = attempt.result
            local md_age = result and tonumber(result[11]:match('^[^,]+,([^,]+)')) or nil
            local read_age = attempt.read_age
            DebugError(string.format('[X4LAN] AUTH_COMPANY_GUN_DAMAGE_UNKNOWN serial=%d reason=%s '
                .. 'md=%s typed=%s lua_shield=%s lua_hull=%s lua_max_shield=%s lua_max_hull=%s '
                .. 'lua_age=%s elapsed_game_age=%s',
                attempt.serial, reason, result and result[11] or '-', table.concat(native_numbers, ','),
                tostring(readback.shield), tostring(readback.hull), tostring(readback.shieldmax),
                tostring(readback.hullmax), tostring(read_age),
                tostring(read_age and md_age and (read_age - md_age) or nil)))
        end
        net:send('GUN_RESULT|' .. attempt.identity .. '|' .. reply)
    end

    local function settle_impact()
        local attempt = active
        if not attempt or not attempt.result then return end
        local fields, hp = attempt.result, attempt.health
        local status, source, target, killed, witness = fields[7], fields[8], fields[9], fields[10], fields[11]
        if attempt.invalid then
            finish_impact('unknown', attempt.invalid)
        elseif paused() or monotonic() >= attempt.deadline then
            finish_impact('unknown', 'native_observations_outside_deadline_or_running_world')
        elseif status == 'refused' and witness == '-' and next(hp) == nil then
            finish_impact('refused')
        elseif status ~= 'applied' or witness == '-' then
            finish_impact('unknown', 'md_did_not_confirm')
        elseif not attempt.readback then
            finish_impact('unknown', 'lua_readback_missing')
        else
            -- X4 may deliver a status event before the numeric parameters.
            -- Keep one independent readback taken at status delivery and wait
            -- for the typed values under the original, nonrenewable deadline.
            local numbers = {}
            for _, field in ipairs(health_fields) do
                if hp[field] == nil then return end
                numbers[#numbers + 1] = string.format('%.17g', hp[field])
            end
            local observed = attempt.readback
            if hp.Amount ~= attempt.amount then
                finish_impact('unknown', 'amount_mismatch')
                return
            end
            if not (near(hp.AfterShield, observed.shield) and near(hp.AfterHull, observed.hull)
                    and near(hp.MaxShield, observed.shieldmax) and near(hp.MaxHull, observed.hullmax)) then
                finish_impact('unknown', 'health_readback_mismatch')
                return
            end
            for _, field in ipairs({'shield', 'hull', 'shieldmax', 'hullmax'}) do
                numbers[#numbers + 1] = string.format('%.17g', observed[field])
            end
            finish_impact(table.concat({'applied', source, target, killed, table.concat(numbers, '|')}, '|'))
        end
    end

    for _, field in ipairs(health_fields) do
        assert(RegisterEvent(screen .. '.' .. field, function(_, value)
            if not active then return end
            local value_type = type(value)
            local hp = (value_type == 'number' or value_type == 'string')
                and number(value, 0, 9007199254740991)
            if active.health[field] ~= nil or not hp then
                active.invalid = 'invalid_typed_' .. field .. '_' .. value_type
            else
                active.health[field] = hp
            end
            settle_impact()
        end) ~= false)
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or type(row) ~= 'string' or #row > 512 then return end
        if row:sub(1, #active.identity + 1) ~= active.identity .. '|' then return end
        local fields = split(row)
        if active.result or #fields ~= 11 or paused() or monotonic() >= active.deadline then
            finish_impact('unknown', 'invalid_native_result_or_deadline')
            return
        end
        active.result = fields
        if fields[7] == 'applied' then
            active.read_age = game_time and game_time()
            local ok, observed = pcall(read_health, fields[9])
            if ok and type(observed) == 'table' and observed.ok then active.readback = observed end
        end
        settle_impact()
    end) ~= false)

    assert(RegisterEvent(screen .. '.HealthResult', function(_, row)
        if not reading or type(row) ~= 'string' or #row > 512
                or row:sub(1, #reading.identity + 1) ~= reading.identity .. '|' then return end
        local attempt = reading
        reading = nil
        local fields = split(row)
        local reply = 'refused'
        if #fields == 7 and fields[6] == 'registered' and fields[7]:match('^0[xX][0-9a-fA-F]+$')
                and not paused() and monotonic() < attempt.deadline then
            local ok, observed = pcall(read_health, fields[7])
            if ok and ship_health(observed) and monotonic() < attempt.deadline then
                reply = string.format('observed|%s|%.17g|%.17g|%.17g|%.17g', fields[7],
                                      observed.shield, observed.shieldmax, observed.hull, observed.hullmax)
            end
        end
        net:send('GUN_HEALTH_RESULT|' .. attempt.identity .. '|' .. reply)
    end) ~= false)

    local function dispatch(packet, now)
        if type(packet) ~= 'string' or #packet > 512 then return end
        local fields = split(packet)
        if #fields == 7 and fields[1] == 'GUN_HEALTH' and fields[2] == lease then
            local serial = index(fields[3], 2147483647)
            local source = index(fields[6], 200000)
            local deadline = number(fields[7], now, now + 2)
            if not serial or serial <= last_health_serial or not token(fields[4]) or not token(fields[5])
                    or not source or not deadline or deadline == now then return end
            last_health_serial = serial
            return {identity=table.concat(fields, '|', 2, 6), deadline=deadline,
                args={lease=lease, serial=serial, generation=fields[4], epoch=fields[5], source=source}}
        end
        if #fields ~= 9 or fields[1] ~= 'GUN_IMPACT' or fields[2] ~= lease then return end
        local serial = index(fields[3], 2147483647)
        local source, target = index(fields[6], 200000), index(fields[7], 200000)
        local amount = number(fields[8], 0, 9007199254740991)
        local deadline = number(fields[9], now, now + 30)
        if not serial or serial <= last_serial or not token(fields[4]) or not token(fields[5])
                or not source or not target or target == source
                or not amount or amount == 0 or not deadline or deadline == now then return end
        last_serial = serial
        reading = nil
        active = {identity=table.concat(fields, '|', 2, 7), deadline=deadline, amount=amount, serial=serial, health={}}
        -- Record the attempt before calling the engine. Never reissue on failure.
        pcall(AddUITriggeredEvent, screen, 'Impact', {lease=lease, serial=serial,
            generation=fields[4], epoch=fields[5], source=source, target=target, amount=amount})
        return true
    end

    return function()
        local now = monotonic()
        if not now or not (now >= 0 and now < math.huge) then return end
        if not next_hello or now >= next_hello then
            net:send('GUN_HELLO|' .. lease)
            next_hello = now + 1
        end
        if active and now >= active.deadline then finish_impact('unknown', 'native_observations_timed_out') end
        if reading and now >= reading.deadline then reading = nil end
        if active or paused() then return end
        local next_read
        for _ = 1, 8 do
            if not net:pending() then break end
            local command = dispatch(net:receive(), now)
            if command == true then return end  -- A queued impact takes precedence over a read in this tick.
            if command then next_read = command end
        end
        if next_read then
            reading = next_read
            pcall(AddUITriggeredEvent, screen, 'Health', reading.args)
        end
    end
end

X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE = M
