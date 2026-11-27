-- Private native stock damage and health requests from the owned provider.
local M = {}
local screen = 'X4AuthorityCompanyGunDamage'
local max_group, group_chunk = 32, 8
local max_health_reads = 4
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

-- MD numeric event parameters truncate floats. A string of bounded integer
-- chunks preserves the sample without relying on MD's display rounding.
local function decode_health(encoded)
    if encoded:sub(-1) ~= ',' then return end
    local chunks = {}
    for value in encoded:gmatch('([^,]*),') do
        if #value > 10 or not value:match('^%-?%d+$') then return end
        chunks[#chunks + 1] = tonumber(value)
    end
    if #chunks ~= #health_fields * 3 then return end
    local health = {}
    for i, field in ipairs(health_fields) do
        local block, whole, fraction = chunks[3*i-2], chunks[3*i-1], chunks[3*i]
        if block < 0 or block > 134217728 or math.abs(whole) > 67108864
                or math.abs(fraction) > 1048576 then return end
        local value = block * 67108864 + whole + fraction / 1048576
        if value < 0 or value > 9007199254740991 then return end
        health[field] = value
    end
    return health
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

function M.new(lease, net, monotonic, paused, read_health, game_time, ordered_hits)
    local active, next_hello
    local reading, last_health_serial = {}, {}
    local last_serial = 0

    local function finish_impact(reply, reason)
        local attempt = active
        active = nil
        if attempt.group then
            net:send('GUN_GROUP_RESULT|' .. attempt.identity .. '|' .. reply)
            return
        end
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
                .. 'md=%s fixed_point=%s lua_shield=%s lua_hull=%s lua_max_shield=%s lua_max_hull=%s '
                .. 'lua_age=%s elapsed_game_age=%s',
                attempt.serial, reason, result and result[11] or '-', table.concat(native_numbers, ','),
                tostring(readback.shield), tostring(readback.hull), tostring(readback.shieldmax),
                tostring(readback.hullmax), tostring(read_age),
                tostring(read_age and md_age and (read_age - md_age) or nil)))
        end
        net:send('GUN_RESULT|' .. attempt.identity .. '|' .. reply)
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or active.group or type(row) ~= 'string' or #row > 512 then return end
        if row:sub(1, #active.identity + 1) ~= active.identity .. '|' then return end
        local fields = split(row)
        if #fields ~= 12 or paused() or monotonic() >= active.deadline then
            finish_impact('unknown', 'invalid_native_result_or_deadline')
            return
        end
        local attempt = active
        attempt.result = fields
        local status, source, target, killed, witness, encoded = fields[7], fields[8], fields[9],
            fields[10], fields[11], fields[12]
        if status == 'refused' and witness == '-' and encoded == '-' then
            finish_impact('refused')
            return
        end
        if status ~= 'applied' or witness == '-' then
            finish_impact('unknown', 'md_did_not_confirm')
            return
        end
        attempt.read_age = game_time and game_time()
        local ok, observed = pcall(read_health, target)
        if not ok or type(observed) ~= 'table' or not observed.ok then
            finish_impact('unknown', 'lua_readback_missing')
            return
        end
        attempt.readback = observed
        local hp = decode_health(encoded)
        if not hp then
            finish_impact('unknown', 'invalid_fixed_point_health')
            return
        end
        attempt.health = hp
        if not near(hp.Amount, attempt.amount) then
            finish_impact('unknown', 'amount_mismatch')
            return
        end
        local is_killed = killed == '1' or killed == 'true'
        local immediate_match = near(hp.AfterShield, observed.shield) and near(hp.AfterHull, observed.hull)
            and near(hp.MaxShield, observed.shieldmax) and near(hp.MaxHull, observed.hullmax)
        local wreck_match = is_killed and hp.BeforeHull > 0 and hp.MaxHull > 0
            and hp.AfterShield == 0 and hp.AfterHull == 0
            and observed.shield == 0 and observed.hull == 0
            and observed.shieldmax == 0 and observed.hullmax == 0
        if not (immediate_match or wreck_match) then
            finish_impact('unknown', 'health_readback_mismatch')
            return
        end
        -- The first number binds the wire receipt to the exact input. The
        -- in-game amount has been checked at the fixed-point resolution.
        local numbers = {string.format('%.17g', attempt.amount)}
        for field = 2, #health_fields do
            numbers[#numbers + 1] = string.format('%.17g', hp[health_fields[field]])
        end
        for _, field in ipairs({'shield', 'hull', 'shieldmax', 'hullmax'}) do
            numbers[#numbers + 1] = string.format('%.17g', observed[field])
        end
        finish_impact(table.concat({'applied', source, target, killed, table.concat(numbers, '|')}, '|'))
    end) ~= false)

    if ordered_hits == true then
        assert(RegisterEvent(screen .. '.GroupResult', function(_, row)
            local group = active
            if not group or not group.group or not group.dispatched or type(row) ~= 'string'
                    or #row > 512 or row:sub(1, #group.identity + 1) ~= group.identity .. '|' then return end
            if paused() or monotonic() >= group.deadline then
                finish_impact('unknown')
                return
            end
            local fields = split(row)
            local status = fields[6]
            if #fields == 12 and status == 'row' then
                local position = #group.rows + 1
                local expected = group.impacts[position]
                local hp = decode_health(fields[12])
                if not expected or index(fields[7], max_group) ~= position
                        or index(fields[8], 200000) ~= expected.target or not number(fields[9], 0, math.huge)
                        or not fields[10]:match('^0[xX][0-9a-fA-F]+$')
                        or not fields[11]:match('^0[xX][0-9a-fA-F]+$')
                        or not hp or not near(hp.Amount, expected.amount) or hp.AfterHull <= 0 then
                    finish_impact('unknown')
                    return
                end
                group.rows[position] = {fields=fields, hp=hp, target=fields[11], amount=expected.amount}
                return
            end
            if #fields == 6 and status == 'refused' and #group.rows == 0 then
                finish_impact('refused')
                return
            end
            if #fields ~= 6 or status ~= 'complete' or #group.rows ~= #group.impacts then
                finish_impact('unknown')
                return
            end

            -- A later hit may change a target again. Read its stock health
            -- once, after every MD write, and compare with its final MD row.
            local observed_by_target = {}
            for i = #group.rows, 1, -1 do
                local result = group.rows[i]
                if not observed_by_target[result.target] then
                    local ok, observed = pcall(read_health, result.target)
                    local hp = result.hp
                    if not ok or type(observed) ~= 'table' or not observed.ok
                            or not (near(hp.AfterShield, observed.shield) and near(hp.AfterHull, observed.hull)
                            and near(hp.MaxShield, observed.shieldmax) and near(hp.MaxHull, observed.hullmax)) then
                        finish_impact('unknown')
                        return
                    end
                    observed_by_target[result.target] = observed
                end
            end
            local prefix = 'GUN_GROUP_RESULT|' .. group.identity .. '|'
            for i, result in ipairs(group.rows) do
                if paused() or monotonic() >= group.deadline then
                    finish_impact('unknown')
                    return
                end
                local fields, observed = result.fields, observed_by_target[result.target]
                local wire = {prefix .. 'row', i, fields[8], fields[9], 'applied', fields[10], fields[11], '0',
                              string.format('%.17g', result.amount)}
                for j = 2, #health_fields do
                    wire[#wire + 1] = string.format('%.17g', result.hp[health_fields[j]])
                end
                for _, key in ipairs({'shield', 'hull', 'shieldmax', 'hullmax'}) do
                    wire[#wire + 1] = string.format('%.17g', observed[key])
                end
                if net:send(table.concat(wire, '|')) ~= true then
                    finish_impact('unknown')
                    return
                end
            end
            finish_impact('complete')
        end) ~= false)
    end

    assert(RegisterEvent(screen .. '.HealthResult', function(_, row)
        if active or type(row) ~= 'string' or #row > 512 then return end
        local fields = split(row)
        if #fields ~= 7 then return end
        local identity = table.concat(fields, '|', 1, 5)
        local slot, attempt
        for index, pending in pairs(reading) do
            if pending.identity == identity then
                slot, attempt = index, pending
                break
            end
        end
        if not attempt then return end
        reading[slot] = nil
        local reply = 'refused'
        if fields[6] == 'registered' and fields[7]:match('^0[xX][0-9a-fA-F]+$')
                and not paused() and monotonic() < attempt.deadline then
            local ok, observed = pcall(read_health, fields[7])
            if ok and ship_health(observed) and monotonic() < attempt.deadline then
                reply = string.format('observed|%s|%.17g|%.17g|%.17g|%.17g', fields[7],
                                      observed.shield, observed.shieldmax, observed.hull, observed.hullmax)
            end
        end
        net:send('GUN_HEALTH_RESULT|' .. attempt.identity .. '|' .. reply)
    end) ~= false)

    local function group_chunk_arrived(fields, now)
        if #fields < 10 or fields[2] ~= lease then return end
        local serial = index(fields[3], 2147483647)
        local source = index(fields[6], 200000)
        local offset, count = index(fields[7], max_group), index(fields[8], max_group)
        local deadline = number(fields[9], now, now + 30)
        if not (serial and source and token(fields[4]) and token(fields[5]) and offset and count
                and deadline and deadline > now and offset <= count
                and #fields == 9 + math.min(group_chunk, count - offset + 1)) then return end
        local identity = table.concat(fields, '|', 2, 6)
        if active then
            if not active.group or active.dispatched or identity ~= active.identity
                    or deadline ~= active.deadline or count ~= active.args.count
                    or offset ~= #active.impacts + 1 then return end
        elseif offset ~= 1 or serial <= last_serial then return end

        local chunk = {}
        for i = 10, #fields do
            local target_text, amount_text = fields[i]:match('^([^,]+),([^,]+)$')
            local target = target_text and index(target_text, 200000)
            local amount = amount_text and number(amount_text, 0, 9007199254740991)
            if not target or target == source or not amount or amount == 0 then return end
            chunk[#chunk + 1] = {target=target, amount=amount}
        end
        if not active then
            last_serial, reading = serial, {}
            active = {group=true, identity=identity, deadline=deadline, rows={}, impacts={},
                      args={lease=lease, serial=serial, generation=fields[4], epoch=fields[5],
                            source=source, count=count}}
        end
        for _, impact in ipairs(chunk) do
            local position = #active.impacts + 1
            active.impacts[position] = impact
            active.args['target' .. position] = impact.target
            active.args['amount' .. position] = impact.amount
        end
        if #active.impacts == count then
            active.dispatched = true
            pcall(AddUITriggeredEvent, screen, 'Group', active.args)
            return true
        end
    end

    local function dispatch(packet, now)
        if type(packet) ~= 'string' or #packet > 512 then return end
        local fields = split(packet)
        if ordered_hits == true and fields[1] == 'GUN_GROUP' then
            return group_chunk_arrived(fields, now)
        end
        if active then return end
        if (#fields == 7 or #fields == 8) and fields[1] == 'GUN_HEALTH' and fields[2] == lease then
            local serial = index(fields[3], 2147483647)
            local source = index(fields[6], 200000)
            local deadline = number(fields[7], now, now + 2)
            local slot = index(fields[8] or '1', max_health_reads)
            if not serial or not slot or serial <= (last_health_serial[slot] or 0)
                    or not token(fields[4]) or not token(fields[5])
                    or not source or not deadline or deadline == now then return end
            last_health_serial[slot] = serial
            return {identity=table.concat(fields, '|', 2, 6), deadline=deadline, slot=slot,
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
        reading = {}
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
        for slot, attempt in pairs(reading) do
            if now >= attempt.deadline then reading[slot] = nil end
        end
        if (active and (not active.group or active.dispatched)) or paused() then return end
        local next_reads = {}
        for _ = 1, 8 do
            if not net:pending() then break end
            local command = dispatch(net:receive(), now)
            if command == true then return end  -- A queued impact takes precedence over a read in this tick.
            if command then next_reads[command.slot] = command end
        end
        if active then return end
        for slot = 1, max_health_reads do
            local read = next_reads[slot]
            if read and not paused() and monotonic() < read.deadline then
                reading[slot] = read
                pcall(AddUITriggeredEvent, screen, 'Health', read.args)
            end
        end
    end
end

X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE = M
