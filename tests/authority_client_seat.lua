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
