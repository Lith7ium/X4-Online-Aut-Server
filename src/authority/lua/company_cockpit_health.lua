-- Apply fresh authoritative health to this client's admitted, living cockpit.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitHealth'
local MAX_COUNTER = 9007199254740991
local MAX_REQUEST = 2147483647
local MAX_WINDOW = 2147483647
local MAX_VALIDITY = 2
local APPLY_VALIDITY = 1
local MD_VALIDITY = 0.25

local function number(value, minimum, maximum)
    return type(value) == 'number' and value >= minimum and value <= maximum
end

local function integer(value, minimum, maximum)
    return number(value, minimum, maximum) and value % 1 == 0
end

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function actor_id(value)
    return type(value) == 'string' and #value <= 18 and value:match('^0x[0-9a-fA-F]+$') ~= nil
        and value:match('[1-9a-fA-F]') ~= nil
end

local function near(actual, expected)
    -- The MD setters take percentages and the engine may round the conversion.
    if expected == 0 then return actual == 0 end
    return type(actual) == 'number' and math.abs(actual - expected) <= math.max(0.0001, expected * 2e-7)
end

local function valid_health(health, row)
    return type(health) == 'table' and health.ok == true and health.shieldmax == row.maxshield
        and health.hullmax == row.maxhull and number(health.shield, 0, row.maxshield)
        and number(health.hull, 0, row.maxhull) and health.hull > 0
end

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit cockpit health opt-in required')
    assert(token(options.lease) and token(options.epoch) and integer(options.index, 1, 200000))
    for _, dependency in ipairs({'clock', 'controlled_actor', 'available', 'read_health', 'on_receipt'}) do
        assert(type(options[dependency]) == 'function', 'missing cockpit health ' .. dependency)
    end
    local issue = options.issue or AddUITriggeredEvent
    local register = options.register or RegisterEvent
    assert(type(issue) == 'function' and type(register) == 'function')

    local generation, window, enabled, active, actor, last_time
    local request, sequence = 0, 0
    local fenced = false
    local api = {}

    local function clock()
        local ok, now = pcall(options.clock)
        if not ok or not number(now, last_time or 0, MAX_COUNTER) then return nil end
        last_time = now
        return now
    end

    local function helm()
        local ok, available = pcall(options.available)
        if not ok or available ~= true then return nil end
        local controlled, candidate = pcall(options.controlled_actor)
        if controlled and actor_id(candidate) and (actor == nil or actor == candidate) then return candidate end
    end

    local function emit(attempt, status, health)
        active = nil
        if status == 'unknown' then fenced = true end
        local receipt = {generation=attempt.generation, window=attempt.window, epoch=options.epoch,
            index=options.index, sequence=attempt.sequence, status=status}
        if health then
            receipt.shield, receipt.maxshield = health.shield, health.shieldmax
            receipt.hull, receipt.maxhull = health.hull, health.hullmax
        end
        options.on_receipt(receipt)
    end

    local function withdraw()
        if not active then return end
        local attempt = active
        pcall(issue, SCREEN, 'Clear', {lease=options.lease, request=attempt.request})
        emit(attempt, attempt.writing and 'unknown' or 'rejected')
    end

    local function still_current(attempt, now)
        return enabled and not fenced and now and now < attempt.expires_at
            and generation == attempt.generation and window == attempt.window and helm() == actor
    end

    function api:set_window(value, revision)
        if value == nil then
            enabled = false
            withdraw()
            return not fenced
        end
        if fenced or not token(value) or not integer(revision, 1, MAX_WINDOW)
                or (generation and value ~= generation) or (window and revision < window) then return false end
        if revision ~= window then
            enabled = false
            withdraw()
            if fenced then return false end
            sequence = 0
        end
        generation, window, enabled = value, revision, true
        return true
    end

    function api:apply(row)
        local now = clock()
        if not enabled or active or fenced or not now or request >= MAX_REQUEST or type(row) ~= 'table'
                or row.generation ~= generation or row.window ~= window or row.epoch ~= options.epoch
                or row.index ~= options.index or not integer(row.sequence, sequence + 1, MAX_COUNTER)
                or not number(row.maxshield, 0, MAX_COUNTER) or not number(row.shield, 0, row.maxshield)
                or not number(row.maxhull, 0, MAX_COUNTER) or not number(row.hull, 0, row.maxhull)
                or row.hull == 0 or not number(row.expires_at, now, now + MAX_VALIDITY)
                or row.expires_at == now then return false end
        local candidate = helm()
        if not candidate then
            if actor then self:set_window(nil) end
            return false
        end
        local ok, health = pcall(options.read_health, candidate)
        now = clock()
        if not ok or not valid_health(health, row) or not now or now >= row.expires_at
                or helm() ~= candidate then return false end
        actor = candidate
        request, sequence = request + 1, row.sequence
        local deadline = math.min(row.expires_at, now + APPLY_VALIDITY)
        active = {request=request, generation=generation, window=window, sequence=sequence, expires_at=deadline,
            shield=row.shield, maxshield=row.maxshield, hull=row.hull, maxhull=row.maxhull, writing=false}
        local issued, result = pcall(issue, SCREEN, 'Prepare', {
            lease=options.lease, request=request, generation=generation, window=window, sequence=sequence,
            epoch=options.epoch, index=options.index, actor=actor,
            shield=row.shield, maxshield=row.maxshield, hull=row.hull, maxhull=row.maxhull,
            validity=math.min(MD_VALIDITY, deadline - now),
        })
        if not issued or result == false then withdraw() end
        return issued and result ~= false
    end

    function api:tick()
        if enabled and actor and helm() ~= actor then
            self:set_window(nil)
        elseif active and not still_current(active, clock()) then
            withdraw()
        end
    end

    function api:close()
        fenced = true
        self:set_window(nil)
    end

    assert(register(SCREEN .. '.Result', function(_, row)
        if not active or type(row) ~= 'string' or #row > 128 then return end
        local lease, serial, status = row:match('^([0-9a-f]+)|(%d+)|([a-z_]+)$')
        local attempt = active
        if lease ~= options.lease or serial ~= tostring(attempt.request) then return end
        if (status == 'prepare_rejected' and not attempt.writing)
                or (status == 'apply_rejected' and attempt.writing) then
            emit(attempt, 'rejected')
            return
        end
        local now = clock()
        if not still_current(attempt, now) then withdraw(); return end
        if status == 'prepared' and not attempt.writing then
            attempt.writing = true
            local issued, result = pcall(issue, SCREEN, 'Apply', {
                lease=options.lease, request=attempt.request, generation=attempt.generation,
                window=attempt.window, sequence=attempt.sequence,
                validity=math.min(MD_VALIDITY, attempt.expires_at - now),
            })
            if not issued or result == false then withdraw() end
        elseif status == 'applied' and attempt.writing then
            local ok, health = pcall(options.read_health, actor)
            if ok and valid_health(health, attempt) and near(health.shield, attempt.shield)
                    and near(health.hull, attempt.hull) and still_current(attempt, clock()) then
                emit(attempt, 'confirmed', health)
            else emit(attempt, 'unknown') end
        end
    end) ~= false)
    return api
end

local function decimal(value)
    if not value or #value > 16 or not value:match('^[1-9]%d*$') then return nil end
    local number = tonumber(value)
    if integer(number, 1, MAX_COUNTER) then return number end
end

function M.motion_link(receiver)
    local first, last
    local revision = 0
    local api = {}

    function api:gap()
        first, last = nil, nil
        receiver:set_window(nil)
    end

    function api:sample(sequence)
        first = first or sequence
        last = sequence
    end

    function api:receive(row)
        local received_revision = decimal(row[2])
        if not received_revision or received_revision < revision then return end
        if #row == 2 and row[1] == 'HEALTH_CLEAR' then
            revision = received_revision
            receiver:set_window(nil)
            return
        end
        if #row ~= 13 or row[1] ~= 'HEALTH' then return end
        local anchor = decimal(row[8])
        if not first or not anchor or anchor < first or anchor > last then return end
        local generation, window = row[3], decimal(row[6])
        if received_revision > revision then
            revision = received_revision
            receiver:set_window(nil)
        end
        if receiver:set_window(generation, window) then
            receiver:apply({generation=generation, epoch=row[4], index=decimal(row[5]), window=window,
                sequence=decimal(row[7]), expires_at=tonumber(row[9]), shield=tonumber(row[10]),
                maxshield=tonumber(row[11]), hull=tonumber(row[12]), maxhull=tonumber(row[13])})
        end
    end

    function api:tick()
        receiver:tick()
    end

    return api
end

X4LAN_COMPANY_COCKPIT_HEALTH = M
return M
