-- Exercises the single update owner using cooperative synthetic MD replies.
local seat = dofile('src/authority/lua/client_seat.lua')
local pinned = {epoch = ('a'):rep(32), nonce = ('b'):rep(32), issued_count = 121}
local index = 118
local prefix = pinned.nonce .. '|' .. pinned.epoch .. '|' .. index .. '|'
local actor = '0x20000000000001'

local function owner()
    local c = {requests = {}, logs = {}, faults = {}, now = 1, paused = true}
    local probe = assert(seat.new({
        pinned = pinned, index = index,
        clock = function() return c.now end,
        guard = function() return c.paused end,
        register = function(callback) c.callback = callback; return true end,
        register_berth = function(callback)
            assert(not c.berth_callback)
            c.berth_callback = callback
            return true
        end,
        issue_berth = function(args)
            assert(args.nonce == pinned.nonce and args.epoch == pinned.epoch
                and args.count == pinned.issued_count and args.index == index and args.actor == actor)
            c.berth_args = args
            return true
        end,
        issue = function(args)
            assert(args.epoch == pinned.epoch and args.count == pinned.issued_count and args.index == index)
            c.requests[#c.requests + 1] = args
            return true
        end,
        inspect = function(id, owner_id, controlled)
            assert(id == actor and owner_id == 'player')
            if c.controlled ~= controlled then return 'LOCAL_CONTROL_MISSING' end
        end,
        log = function(row) c.logs[#c.logs + 1] = row end,
        fault = function(row) c.faults[#c.faults + 1] = row end,
    }))
    function c.answer(action, status, id)
        c.callback(nil, prefix .. action .. '|' .. status .. '|' .. (id or actor) .. '|player')
    end
    return probe, c
end

do
    local probe, c = owner()
    local received, calls
    calls = 0
    local function read(berth)
        calls = calls + 1
        received = berth
    end
    assert(not probe:read_occupied_berth(read) and not c.berth_callback)
    c.controlled = true
    assert(probe:tick())
    c.answer('inspect', 'CONTROLLED')
    assert(probe:tick() and probe:ready())
    c.paused = false

    local function reply(request, seed, reply_actor)
        c.berth_callback(nil, prefix .. request .. '|' .. (reply_actor or actor)
            .. '|cluster_01_sector001_macro|BIM-169|' .. seed .. '|external_bay_macro|dockarea_macro')
    end
    assert(probe:read_occupied_berth(read) and c.berth_args.request == 1)
    assert(probe:read_occupied_berth(read) and c.berth_args.request == 2)
    reply(1, '9007199254740993')
    reply(2, '9007199254740993', '0x1')
    assert(calls == 0)
    reply(2, '18446744073709551615')
    assert(calls == 1 and received.bay_seed == '18446744073709551615'
        and received.sector == 'cluster_01_sector001_macro' and received.station_code == 'BIM-169'
        and received.bay_macro == 'external_bay_macro' and received.dockarea_macro == 'dockarea_macro'
        and received.external and received.occupied_by_ship and not received.actor)
    assert(probe:read_occupied_berth(read))
    reply(3, '18446744073709551616')
    assert(calls == 2 and not received)
    assert(probe:read_occupied_berth(read))
    reply(4, '-9223372036854775808')
    assert(calls == 3 and received.bay_seed == '-9223372036854775808')
    assert(probe:read_occupied_berth(read))
    c.berth_callback(nil, prefix .. '5|' .. actor .. '|UNAVAILABLE')
    assert(calls == 4 and not received)
    assert(probe:read_occupied_berth(read))
    c.controlled = false
    reply(6, '9007199254740993')
    assert(calls == 5 and not received and not probe:read_occupied_berth(read) and #c.faults == 0)
end

do
    local probe, c = owner()
    c.controlled = false
    assert(probe:tick() and not probe:ready())
    assert(c.requests[1].action == 'inspect' and c.requests[1].actor == '0')
    c.answer('inspect', 'READY')
    assert(probe:tick() and c.requests[2].action == 'take' and c.requests[2].actor == actor)
    c.answer('take', 'DISPATCHED')
    assert(probe:tick() and c.requests[3].action == 'verify' and not probe:ready())
    c.controlled = true
    c.answer('verify', 'CONTROLLED')
    assert(probe:tick() and probe:ready() and #c.faults == 0)
    assert(#c.logs == 1 and c.logs[1] == prefix .. 'controlled')
    c.answer('verify', 'CONTROLLED')
    assert(not probe:ready() and c.faults[1] == prefix .. 'UNEXPECTED_RESULT')
end

do
    local probe, c = owner()
    c.controlled = true
    assert(probe:tick())
    c.answer('inspect', 'CONTROLLED')
    assert(probe:tick() and probe:ready() and #c.requests == 1)
end

do
    local probe, c = owner()
    assert(probe:tick())
    c.answer('inspect', 'PILOT_PRESENT')
    assert(not probe:tick() and #c.logs == 0 and #c.requests == 1)
    assert(c.faults[1] == prefix .. 'PILOT_PRESENT')
end

do
    local probe, c = owner()
    c.controlled = false
    assert(probe:tick())
    c.answer('inspect', 'READY')
    assert(probe:tick())
    c.answer('take', 'DISPATCHED', '0x02')
    assert(not probe:tick() and c.faults[1] == prefix .. 'TARGET_CHANGED')
end

do
    local probe, c = owner()
    c.controlled = true
    assert(probe:tick())
    c.answer('inspect', 'CONTROLLED')
    c.controlled = false
    assert(not probe:tick() and c.faults[1] == prefix .. 'LOCAL_CONTROL_MISSING')
end

do
    local probe, c = owner()
    assert(probe:tick())
    c.now = 11
    assert(not probe:tick() and c.faults[1] == prefix .. 'TIMEOUT')
end

do
    local probe, c = owner()
    assert(probe:tick())
    c.paused = false
    assert(not probe:tick() and c.faults[1] == prefix .. 'PAUSE_OR_REGISTRY_LOST')
end
