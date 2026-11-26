-- Exchange observations between X4 and its own launcher. This reader has no game setters.
local M = {}
local SLOT_NAMES = {'cockpit', 'map'}
local HEARTBEAT_SECONDS = 0.5
local ROSTER_TTL = 3
local MAX_PILOT_INDEX = 200000
local MAX_PILOTS = 3
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

local function remote_sample(row, binding, attitude)
    local velocity
    if row[12] ~= '-' then
        velocity = {x=tonumber(row[12]), y=tonumber(row[13]), z=tonumber(row[14])}
    end
    return {version=attitude and 4 or 3, attitude=attitude, epoch=binding.epoch, index=binding.index, sector_macro=binding.sector_macro,
        generation=row[6], sequence=tonumber(row[7]), x=tonumber(row[9]), y=tonumber(row[10]), z=tonumber(row[11]),
        real_velocity=velocity, source_time=tonumber(row[15]), capture_time=tonumber(row[16])}
end

function M.new(readers, epoch, net, prefix, clock, playback_trial, pilot_roster, hold_trial, cockpit_health)
    assert(not hold_trial or not playback_trial)
    local alignment = hold_trial and hold_trial.alignment_enabled
    local slot_names = {'cockpit'}
    local playback_slots = {cockpit = true}
    for _, extra in ipairs({'cockpit2', 'cockpit3'}) do
        if readers[extra] ~= nil then
            slot_names[#slot_names + 1] = extra
            playback_slots[extra] = true
        end
    end
    slot_names[#slot_names + 1] = 'map'
    local slots = {}
    for _, name in ipairs(slot_names) do
        slots[name] = {reader = assert(readers[name]), serial = 0, next_heartbeat = 0}
    end
    local receive_budget = 8 * #slot_names
    local api = {}
    local roster_revision, roster_packet, roster_deadline = 0, nil, nil

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

    local function observe_roster(row, packet, now)
        if not pilot_roster or #row < 3 or #row > 3 + MAX_PILOTS or row[2] ~= epoch then return end
        local revision = counter(row[3])
        if not revision or revision < roster_revision then return end
        if revision == roster_revision then
            if packet ~= roster_packet then return end
            if roster_deadline then
                roster_deadline = now + ROSTER_TTL
                return
            end
        end
        local indices, previous = {}, 0
        for field = 4, #row do
            local index = counter(row[field])
            if not index or index <= previous or index > MAX_PILOT_INDEX then return end
            indices[#indices + 1], previous = index, index
        end
        if pilot_roster:set_pilot_roster(indices) ~= true then return end
        roster_revision, roster_packet, roster_deadline = revision, packet, now + ROSTER_TTL
    end

    local function observe(packet, now)
        if #packet > 640 or packet:sub(1, #prefix) ~= prefix
                or (#packet > 576 and packet:sub(#prefix + 1, #prefix + 8) ~= 'REMOTE4|') then return end
        local row = {}
        for field in (packet:sub(#prefix + 1) .. '|'):gmatch('([^|]*)|') do
            row[#row + 1] = field
        end
        if cockpit_health and (row[1] == 'HEALTH' or row[1] == 'HEALTH_CLEAR') then
            cockpit_health:receive(row)
            return
        end
        if row[1] == 'ROSTER' then
            observe_roster(row, packet, now)
            return
        end
        local timed_trial = playback_trial and playback_slots[row[2]] and playback_trial
            or alignment and row[2] == 'cockpit' and hold_trial
        if timed_trial and #row == 7 and row[1] == 'END' then
            local slot = slots[row[2]]
            if slot and slot.binding and counter(row[3]) == slot.serial and row[4] == epoch
                    and counter(row[5]) == slot.binding.index and row[7] == slot.binding.sector_macro
                    and #row[6] == 32 and row[6]:match('^[0-9a-f]+$') then
                timed_trial:end_track(slot.binding, slot.serial, row[6], row[2])
            end
            return
        end
        local version4 = row[1] == 'REMOTE4'
        if not version4 and row[1] ~= 'REMOTE' then return end
        local angle_fields = version4 and 3 or 0
        local received_at
        if #row == 17 + angle_fields and timed_trial then
            -- Both clocks here belong to the receiving machine. Field 16 is
            -- the original capture clock on the remote pilot's machine.
            received_at = coordinate(row[17], 1e12)
            if not received_at or received_at < 0 or received_at > clock() then return end
        elseif #row ~= 16 + angle_fields then return end
        local attitude
        if version4 then
            local first = received_at and 18 or 17
            local yaw = coordinate(row[first], 2 * math.pi)
            local pitch = coordinate(row[first + 1], 2 * math.pi)
            local roll = coordinate(row[first + 2], 2 * math.pi)
            if not (yaw and pitch and roll) then return end
            attitude = {yaw = yaw, pitch = pitch, roll = roll}
        end
        local slot = slots[row[2]]
        local binding = slot and slot.binding
        if not binding or counter(row[3]) ~= slot.serial or row[4] ~= epoch
                or counter(row[5]) ~= binding.index or row[8] ~= binding.sector_macro
                or not valid_sample(row) then return end
        if not slot.reader:matches(epoch, binding.sector_macro, binding.index, binding.actor, binding.code) then return end
        -- Echo only after reading the live local hull again. Source rates may be absent.
        local receipt = {version4 and 'SEEN4' or 'SEEN', row[2], row[3], epoch, row[5], row[6], row[7], row[8],
                         binding.actor, binding.code, unpack(row, 9, 16)}
        if attitude then
            for index = #row - 2, #row do receipt[#receipt + 1] = row[index] end
        end
        local confirmed = net:send(prefix .. table.concat(receipt, '|'))
        if hold_trial and confirmed == true and row[2] == 'cockpit' and not version4 then
            hold_trial:sample(binding, slot.serial, remote_sample(row, binding), received_at)
        end
        if playback_trial and playback_slots[row[2]] and received_at and confirmed == true then
            playback_trial:sample(binding, slot.serial, remote_sample(row, binding, attitude), received_at, row[2])
        end
    end

    function api:tick(running)
        local now = clock()
        if roster_deadline and now >= roster_deadline then
            pilot_roster:set_pilot_roster({})
            roster_deadline = nil
        end
        for _, name in ipairs(slot_names) do
            local slot = slots[name]
            local binding
            if running then
                binding = slot.reader:tick()
            end
            publish_view(slot, name, binding, now, running)
        end
        if playback_trial then
            for _, name in ipairs(slot_names) do
                if playback_slots[name] then
                    playback_trial:view(slots[name].binding, slots[name].serial, name)
                end
            end
        end
        for _ = 1, receive_budget do
            local packet = net:receive()
            if packet == nil then break end
            if running then observe(packet, now) end
        end
        if hold_trial then hold_trial:tick(running) end
        if playback_trial and running then playback_trial:tick(now) end
    end

    function api:close()
        if hold_trial then hold_trial:close() end
        if playback_trial then playback_trial:close() end
        for _, name in ipairs(slot_names) do slots[name].reader:close() end
    end
    return api
end

X4LAN_COMPANY_OBSERVER_STREAM = M
return M
