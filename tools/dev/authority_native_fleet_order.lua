-- Private provider-side transport for a single guarded MD MoveWait request.
local M = {}
local screen = 'X4AuthorityNativeFleetOrder'

local function split(value)
    local fields = {}
    for item in (value .. '|'):gmatch('(.-)|') do fields[#fields + 1] = item end
    return fields
end

local function token(value)
    return #value == 32 and value:match('^[0-9a-f]+$')
end

local function coordinate(value)
    local number = tonumber(value)
    if number and number >= -10000000 and number <= 10000000 then return number end
end

function M.new(lease, net, monotonic)
    local active, next_hello
    local seen = {}

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or type(row) ~= 'string' or #row > 490 then return end
        local prefix = lease .. '|' .. active.id .. '|'
        if row:sub(1, #prefix) ~= prefix then return end
        active = nil
        net:send('FLEET_RESULT|' .. row)
    end) ~= false)

    return function(paused)
        local now = monotonic()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge) then return end
        if not next_hello or now >= next_hello then
            net:send('FLEET_HELLO|' .. lease)
            next_hello = now + .5
        end
        if active and now >= active.deadline then active = nil end
        if paused or active or not net:pending() then return end
        local packet = net:receive()
        if type(packet) ~= 'string' or #packet > 512 then return end
        local fields = split(packet)
        if #fields ~= 11 or fields[1] ~= 'FLEET_ORDER' or fields[2] ~= lease then return end
        local id, epoch, code, sector = fields[3], fields[4], fields[6], fields[7]
        if not token(id) or not token(epoch) or seen[id]
                or not code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$')
                or #sector > 128 or not sector:match('^[A-Za-z0-9_]+$') then return end
        local index, deadline = tonumber(fields[5]), tonumber(fields[11])
        local x, y, z = coordinate(fields[8]), coordinate(fields[9]), coordinate(fields[10])
        if not index or index < 1 or index > 200000 or index % 1 ~= 0
                or not deadline or not (deadline > now and deadline <= now + 120)
                or not (x and y and z) then return end
        seen[id] = true
        active = {id=id, deadline=deadline}
        -- Lua and Python never resubmit a native event, even after a missing callback.
        -- A failed API call remains pending until the supervisor reports an unknown outcome.
        pcall(AddUITriggeredEvent, screen, 'MoveWait', {lease=lease, order_id=id, epoch=epoch,
            ship_index=index, ship_code=code, sector=sector, x=x, y=y, z=z})
    end
end

X4LAN_AUTHORITY_NATIVE_FLEET_ORDER = M
