-- Run from the source root with: luajit tests/authority_company_cockpit_continuous_playback.lua
-- The receiving cockpit, playback buffer and native adapter are real Lua modules.
-- The engine applies requested poses on its next deterministic frame.
local source = 'src/authority/lua/'
X4LAN_MOTION_BUFFER = dofile(source .. 'motion_buffer.lua')
dofile(source .. 'motion_playback.lua')
dofile(source .. 'company_hull_playback.lua')
local native = dofile(source .. 'company_hull_playback_x4.lua')
local cockpit = dofile(source .. 'company_cockpit_playback.lua')

local epoch, generation = ('a'):rep(32), ('b'):rep(32)
local own_index, target_index = 1, 2
local own_actor, target_actor, sector_id, zone = 101, 202, 303, 404
local function xyz(x, y, z)
    return {x = x, y = y, z = z, yaw = 0, pitch = 0, roll = 0}
end
local function distance(a, b)
    return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2 + (a.z - b.z)^2)
end

local function fixture(version)
    local f = {
        now = 10, sequence = 0, received = nil,
        own_pose = xyz(0, 0, 3000), actual = xyz(0, 0, 0),
        own_actor = own_actor, can_observe = true,
        writes = {}, readbacks = {}, faults = {},
    }
    local observed = {
        epoch = epoch, index = target_index, actor = '0xca',
        code = 'TGT-002', sector_macro = 'sector_a',
    }
    local observer = {
        matches = function(_, seen_epoch, frame, index, actor, code)
            return f.can_observe and seen_epoch == observed.epoch and frame == observed.sector_macro
                and index == observed.index and actor == observed.actor and code == observed.code
        end,
        is_active_pilot = function(_, index) return f.can_observe and index == target_index end,
        current_radar_range = function() return f.can_observe and 40000 or nil end,
    }
    local adapter = native.new({
        enabled = true, pinned = {epoch = epoch, issued_count = 2}, own_index = own_index,
        observer = observer, guard = function() return true end,
    }, {
        cockpit = function() return {actor = f.own_actor, sector = sector_id, zone = zone} end,
        target = function(actor)
            assert(actor == observed.actor)
            return {actor = target_actor, sector = sector_id, zone = zone,
                sector_macro = observed.sector_macro, code = observed.code}
        end,
        read = function(actor)
            assert(actor == target_actor, 'receiver tried to read the own actor as its target')
            return f.actual
        end,
        write = function(actor, sector, target)
            assert(actor == target_actor and actor ~= f.own_actor, 'receiver wrote to the own ship')
            assert(sector == sector_id, 'unexpected destination')
            f.pending = target
            f.writes[#f.writes + 1] = {pose = target, time = f.now}
            return true
        end,
        destination_zone = function(sector)
            assert(sector == sector_id)
            return zone
        end,
        within_radar = function(actor, actual, target, radius)
            assert(actor == f.own_actor)
            return distance(f.own_pose, actual) <= radius and distance(f.own_pose, target) <= radius
        end,
    })
    local controller = cockpit.new_multi({
        enabled = true, continuous_playback = true, allow_reacquire_align = true,
        own_index = own_index, slot_names = {'cockpit3'}, adapters = {cockpit3 = adapter},
        clock = function() return f.now end, game = function() return f.now end,
        paused = function() return false end,
        record = function(event)
            if event.kind == 'readback' then
                assert(event.elapsed > 0 and event.index == target_index)
                f.readbacks[#f.readbacks + 1] = event
            end
            return true
        end,
        fault = function(reason) f.faults[#f.faults + 1] = reason end,
    })
    function f:sample(at, x, y, vx, vy, view)
        self.now = at
        self.sequence = self.sequence + 1
        local sample = {
            version = version, epoch = epoch, index = view and view.index or target_index,
            generation = generation, sector_macro = observed.sector_macro, sequence = self.sequence,
            source_time = at + 100, capture_time = at + 200, x = x, y = y, z = 0,
            real_velocity = {x = vx, y = vy, z = 0},
        }
        if version == 4 then sample.attitude = {yaw = math.min(self.sequence / 40, .7), pitch = 0, roll = 0} end
        local ok, reason = controller:sample(view or observed, 1, sample, at, 'cockpit3')
        if ok then self.received = at end
        return ok, reason
    end
    function f:frame(at)
        self.now = at
        if self.pending then self.actual, self.pending = self.pending, nil end
        controller:view(self.can_observe and observed or nil, 1, 'cockpit3')
        controller:tick(at)
    end
    function f:frames(first, count)
        for i = 0, count - 1 do self:frame(first + i * .05) end
    end
    function f:warmup()
        for i = 0, 11 do
            local time = 10 + i * .05
            assert(self:sample(time, i * 75, 0, 1500, 0))
            self:frame(time)
        end
        assert(self.actual.x > 300 and #self.readbacks > 2, 'fixture failed to establish 1500 m/s playback')
    end
    function f:own_untouched()
        assert(distance(self.own_pose, xyz(0, 0, 3000)) == 0)
    end
    return f
end

local function sparse_render_and_exact_cutoff(version)
    local f = fixture(version)
    f:warmup()
    local before, count = f.actual.x, #f.writes
    assert((10.95 - 10.55) * 1500 > 300)
    f:frame(10.95)
    assert(#f.writes > count, 'visible same-sector ship froze after a 0.4 s render gap')
    f:frames(11, 8)
    assert(f.actual.x - before > 300, 'visible ship did not continue on the last velocity')
    assert(f:sample(11.4, 2100, 0, 1500, 0))
    local resumed_sequence = f.sequence
    f:frames(11.4, 12)
    assert(f.readbacks[#f.readbacks].source_sequence >= resumed_sequence,
           'receiver did not apply the subsequent source sequence')
    local after_sample = f.actual.x
    f:frames(12, 88)
    assert(f.actual.x > after_sample + 300, 'ship did not extrapolate during the running loss window')

    local deadline = f.received + 5
    f:frame(deadline - .001)
    local last = f.writes[#f.writes]
    assert(last.time == deadline - .001, 'playback froze before the five-second deadline')
    local writes = #f.writes
    f:frame(deadline)
    local stopped_at = f.actual
    f:frames(deadline + .05, 4)
    assert(#f.writes == writes and distance(f.actual, stopped_at) == 0,
           'receiver wrote after the exact five-second deadline')
    f:own_untouched()
end

local function fresh_lateral_correction(version)
    local f = fixture(version)
    f:warmup()
    f:frames(10.60, 8)
    local before = f.actual
    -- The new track has changed course in the same sector. A 450 m lateral
    -- advance over 0.50 s is also within the old rate's movement allowance.
    assert(math.abs(before.y - 450) > 300)
    local ok, reason = f:sample(11.05, 975, 450, 0, 1000)
    assert(ok, 'fresh lateral track was refused: ' .. tostring(reason))
    f:frames(11.05, 2)
    assert(f:sample(11.15, 975, 550, 0, 1000))
    f:frames(11.15, 2)
    assert(f:sample(11.25, 975, 600, 0, 0))
    local settled = xyz(975, 600, 0)
    for i = 0, 79 do
        local at = 11.25 + i * .05
        if i > 0 and i % 3 == 0 then assert(f:sample(at, settled.x, settled.y, 0, 0)) end
        f:frame(at)
    end
    assert(distance(f.actual, settled) < 30, 'same-sector correction did not converge')
    assert(f.actual.y - before.y > 300 and #f.readbacks > 3)
    if version == 4 then
        assert(f.actual.yaw > 0, 'continuous v4 playback lost measured attitude')
    end
    f:own_untouched()
end

local function local_actor_changes()
    local f = fixture(3)
    f:warmup()
    local writes = #f.writes
    -- The formerly observed hull now represents the local controlled actor.
    -- Its identity remains in the view; the native adapter must still check.
    f.own_actor = target_actor
    f:frame(10.95)
    local ok = f:sample(11, 1500, 0, 1500, 0)
    f:frames(11, 6)
    assert(not ok and #f.writes == writes, 'controller kept writing after an own-ship identity change')
    f.can_observe = false
    f:frame(11.35)
    f:own_untouched()
end

for _, version in ipairs({3, 4}) do
    sparse_render_and_exact_cutoff(version)
    fresh_lateral_correction(version)
end
local_actor_changes()
print('Company continuous playback acceptance passed for v3 and v4')
