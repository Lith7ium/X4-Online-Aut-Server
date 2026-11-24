local sampler = dofile('src/authority/lua/source_motion_sampler.lua')
local source = dofile('src/authority/lua/company_motion_source.lua')
local epoch, generation, nonce = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local index, actor = 1961, 716
local selected, clock, frame, key, position = false, 1, 'sector_a', '111', 10
local request, packets, closed = nil, {}, 0

local owner = source.new({
    epoch = epoch, index = index,
    actor = function() assert(selected); return actor end,
    available = function() return selected end,
    send = function(payload) packets[#packets+1] = payload end,
    make_sampler = function(id, guard)
        assert(id == actor)
        return sampler.new({
            enabled = true, epoch = epoch, generation = generation, nonce = nonce,
            max_tracks = 1, scan_budget = 1, interval = .10, timeout = 1,
            min_dt = .02, max_dt = 1, real_time_motion = true,
            clock = function() return clock end, guard = guard, actor_key = tostring,
            binding_guard = function() return false end,
            saved_baseline_limit = 2000,
            saved_baseline_guard = function(row, value)
                return row.index == index and value == actor and selected
            end,
            reader_factory = function(row, value, id)
                assert(row.index == index and value == actor)
                return {
                    start = function() request = id; return true end,
                    close = function() closed = closed + 1; return true end,
                    tick = function() return true end,
                    response = function()
                        return true, {epoch = epoch, index = index, sector_macro = frame,
                            local_sector_key = key, descriptor_atomic = false, sector_identity_proven = false,
                            game_before_pose = 10, game_after_pose = 10,
                            capture_before_pose = clock - .001, capture_after_pose = clock,
                            x = position, y = 0, z = 0}
                    end,
                }
            end,
            fault = function(reason) error(reason) end,
        })
    end,
})

local function capture(t, x)
    clock, position = t, x
    owner:tick()
    assert(request)
    owner:event(request .. '|reply')
end

owner:tick()
assert(not request)
selected = true
owner:tick()
assert(not request)
owner:set_running(true)
capture(1, 10)
assert(packets[1] == 'SAMPLE3|' .. epoch .. '|1961|1|sector_a|10|1|10|0|0|-|-|-|')
capture(1.5, 15)
assert(packets[2] == 'SAMPLE3|' .. epoch .. '|1961|2|sector_a|10|1.5|15|0|0|10|0|0|')
clock = 2
owner:tick()
local canceled = request
owner:set_running(false)
owner:event(canceled .. '|reply')
assert(closed == 1 and packets[3] == 'GAP' and #packets == 3)
owner:set_running(true)
capture(2.5, 300)
assert(request ~= canceled and packets[4]:match('|3|sector_a.-|%-|%-|%-|$'))
frame, key = 'sector_b', '112'
capture(3, 900)
assert(packets[5]:match('|4|sector_b.-|%-|%-|%-|$'))
selected = false
owner:tick()
assert(packets[6] == 'GAP')
owner:tick()
assert(#packets == 6)
