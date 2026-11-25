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
        running = function() return not c.paused end,
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
        inspect = function(id, owner_id, controlled, context)
            assert(id == actor and owner_id == 'player')
            if c.controlled ~= controlled then return 'LOCAL_CONTROL_MISSING' end
            if context == 'aboard' and not c.aboard then return 'PLAYER_CONTEXT' end
        end,
        log = function(row) c.logs[#c.logs + 1] = row end,
        fault = function(row) c.faults[#c.faults + 1] = row end,
    }))
    function c.answer(action, status, id, captain)
        c.callback(nil, prefix .. action .. '|' .. status .. '|' .. (id or actor)
            .. '|player' .. (captain and '|' .. captain or ''))
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


local captain = '0x20000000000002'
local function reserve_passenger()
    local probe, c = owner()
    c.controlled = false
    assert(probe:tick() and not probe:admission_ready() and not probe:admit())
    c.answer('inspect', 'CREWED', actor, captain)
    assert(probe:tick() and c.requests[2].action == 'board' and c.requests[2].captain == captain)
    c.answer('board', 'BOARD_DISPATCHED', actor, captain)
    c.now = 2
    assert(probe:tick() and c.requests[3].action == 'aboard')
    c.answer('aboard', 'BOARD_WAIT', actor, captain)
    c.now = 3
    assert(probe:tick() and c.requests[4].action == 'aboard')
    c.aboard = true
    c.answer('aboard', 'ABOARD', actor, captain)
    assert(probe:tick() and probe:admission_ready())
    assert(not probe:ready() and not probe:controlled_actor())
    assert(not probe:read_occupied_berth(function() error('passenger berth') end))
    assert(#c.logs == 1 and c.logs[1] == prefix .. 'aboard')
    return probe, c
end

do
    local probe, c = reserve_passenger()
    c.paused = false
    c.now = 40
    assert(probe:tick() and #c.requests == 4 and not probe:admission_ready())
    c.paused = true
    assert(probe:admit() and probe:tick() and #c.requests == 4)
    c.paused = false
    assert(probe:tick() and c.requests[5].action == 'relief')
    assert(probe:tick() and #c.requests == 5)
    c.answer('relief', 'RELIEF_WAIT', actor, captain)
    c.now = 80
    assert(probe:tick() and c.requests[6].action == 'relief')
    c.answer('relief', 'RELIEF_DISPATCHED', actor, captain)
    c.now = 81
    assert(probe:tick() and c.requests[7].action == 'helm' and not probe:ready())
    c.answer('helm', 'HELM_WAIT', actor, captain)
    c.now = 82
    assert(probe:tick() and c.requests[8].action == 'helm')
    c.answer('helm', 'HELM_DISPATCHED', actor, captain)
    c.now = 83
    assert(probe:tick() and c.requests[9].action == 'helmsample')
    c.answer('helmsample', 'HELM_WAIT', actor, captain)
    c.now = 84
    assert(probe:tick() and c.requests[10].action == 'helmsample')
    c.controlled = true
    c.answer('helmsample', 'CONTROLLED', actor, captain)
    assert(probe:tick() and probe:ready() and probe:controlled_actor() == actor)
    assert(c.logs[2] == prefix .. 'controlled' and #c.faults == 0)
    c.controlled = false
    assert(not probe:ready() and not probe:controlled_actor())
end

do
    local probe, c = reserve_passenger()
    assert(probe:admit())
    c.paused = false
    assert(probe:tick())
    c.answer('relief', 'RELIEF_WAIT', actor, '0x5')
    assert(not probe:tick() and c.faults[1] == prefix .. 'CAPTAIN_CHANGED')
end

do
    local probe, c = owner()
    c.controlled = false
    assert(probe:tick())
    c.answer('inspect', 'SHIP_DOCKED_OR_STORED')
    assert(not probe:tick() and not probe:admission_ready())
    assert(c.faults[1] == prefix .. 'SHIP_DOCKED_OR_STORED')
end
