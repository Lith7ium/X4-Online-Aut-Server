-- Exchange observations between X4 and its own launcher. This reader has no game setters.
local M = {}
local SLOT_NAMES = {'cockpit', 'map'}
local HEARTBEAT_SECONDS = 0.5
local MAX_COUNTER = 9007199254740991

local function same_binding(left, right)
    if left == nil or right == nil then return left == right end
    return left.epoch == right.epoch and left.index == right.index and left.sector_macro == right.sector_macro
        and left.actor == right.actor and left.code == right.code
end

local function counter(text)
    if not text:match('^[1-9]%d*$') or #text > 16 then return nil end
    local value = tonumber(text)
    return value and value <= MAX_COUNTER and value or nil
end

local function coordinate(text, limit)
    if not text:match('^[%d%.eE+%-]+$') then return nil end
    local value = tonumber(text)
    return value and value >= -limit and value <= limit and value or nil
end

local function valid_sample(row)
    if #row[6] ~= 32 or not row[6]:match('^[0-9a-f]+$') or not counter(row[7]) then return false end
    for field = 9, 11 do
        if not coordinate(row[field], 10000000) then return false end
    end
    for field = 15, 16 do
        local timestamp = coordinate(row[field], 1e12)
        if not timestamp or timestamp < 0 then return false end
    end
    if row[12] == '-' and row[13] == '-' and row[14] == '-' then return true end
    for field = 12, 14 do
        if not coordinate(row[field], 5000) then return false end
    end
    return true
end

function M.new(readers, epoch, net, prefix, clock)
    local slots = {}
    for _, name in ipairs(SLOT_NAMES) do
        slots[name] = {reader = assert(readers[name]), serial = 0, next_heartbeat = 0}
    end
    local api = {}

    local function publish_view(slot, name, binding, now, running)
        if slot.serial == 0 and not running then return end
        local changed = slot.serial == 0 or not same_binding(binding, slot.binding)
        if changed then
            assert(slot.serial < MAX_COUNTER, 'company view serial exhausted')
            slot.serial = slot.serial + 1
            slot.binding = binding
        elseif not running or now < slot.next_heartbeat then
            return
        end
        slot.next_heartbeat = now + HEARTBEAT_SECONDS
        local fields = {'VIEW', name, ('%.0f'):format(slot.serial), epoch}
        if binding then
            fields[5], fields[6], fields[7], fields[8] =
                binding.sector_macro, tostring(binding.index), binding.actor, binding.code
        else
            fields[5], fields[6], fields[7], fields[8] = '-', '0', '-', '-'
        end
        net:send(prefix .. table.concat(fields, '|'))
    end

    local function observe(packet)
        if #packet > 576 or packet:sub(1, #prefix) ~= prefix then return end
        local row = {}
        for field in (packet:sub(#prefix + 1) .. '|'):gmatch('([^|]*)|') do
            row[#row + 1] = field
        end
        if #row ~= 16 or row[1] ~= 'REMOTE' then return end
        local slot = slots[row[2]]
        local binding = slot and slot.binding
        if not binding or counter(row[3]) ~= slot.serial or row[4] ~= epoch
                or counter(row[5]) ~= binding.index or row[8] ~= binding.sector_macro
                or not valid_sample(row) then return end
        if not slot.reader:matches(epoch, binding.sector_macro, binding.index, binding.actor, binding.code) then return end
        -- Echo only after reading the live local hull again. Source rates may be absent.
        local receipt = {'SEEN', row[2], row[3], epoch, row[5], row[6], row[7], row[8],
                         binding.actor, binding.code, unpack(row, 9, 16)}
        net:send(prefix .. table.concat(receipt, '|'))
    end

    function api:tick(running)
        local now = clock()
        for _, name in ipairs(SLOT_NAMES) do
            local slot = slots[name]
            local binding
            if running then
                binding = slot.reader:tick()
            end
            publish_view(slot, name, binding, now, running)
        end
        for _ = 1, 8 do
            local packet = net:receive()
            if packet == nil then break end
            if running then observe(packet) end
        end
    end

    function api:close()
        for _, name in ipairs(SLOT_NAMES) do slots[name].reader:close() end
    end
    return api
end

X4LAN_COMPANY_OBSERVER_STREAM = M
return M
