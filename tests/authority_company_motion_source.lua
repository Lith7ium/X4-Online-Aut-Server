local sampler = dofile('src/authority/lua/source_motion_sampler.lua')
local source = dofile('src/authority/lua/company_motion_source.lua')
local epoch, generation, nonce = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local index, actor = 1961, 716
local selected, clock, frame, key, position = false, 1, 'sector_a', '111', 10
local request, packets, closed = nil, {}, 0
local binding, timeouts, faults = true, 0, {}

local owner = source.new({
    epoch = epoch, index = index,
    actor = function() assert(selected); return actor end,
    available = function() return selected end,
    pending_timeout = function() timeouts = timeouts + 1 end,
    send = function(payload) packets[#packets+1] = payload end,
    make_sampler = function(id, guard)
        assert(id == actor)
        return sampler.new({
            enabled = true, epoch = epoch, generation = generation, nonce = nonce,
            max_tracks = 1, scan_budget = 1, interval = .10, timeout = 1,
            min_dt = .02, max_dt = 1, real_time_motion = true, recover_expired_pending = true,
            clock = function() return clock end, guard = guard, actor_key = tostring,
            binding_guard = function() return false end,
            saved_baseline_limit = 2000,
            saved_baseline_guard = function(row, value)
                return row.index == index and value == actor and selected and binding
            end,
            reader_factory = function(row, value, id)
                assert(row.index == index and value == actor)
                return {
                    start = function() request = id; return true end,
                    close = function() closed = closed + 1; return true end,
                    tick = function() return true end,
                    response = function(_, raw)
                        assert(raw == id .. '|reply')
                        return true, {epoch = epoch, index = index, sector_macro = frame,
                            local_sector_key = key, descriptor_atomic = false, sector_identity_proven = false,
                            game_before_pose = 10, game_after_pose = 10,
                            capture_before_pose = clock - .001, capture_after_pose = clock,
                            x = position, y = 0, z = 0}
                    end,
                }
            end,
            fault = function(reason) faults[#faults+1] = reason end,
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

-- Once the owner is available again it must use fresh correlators, retain the
-- outgoing sequence and establish a fresh rate baseline. Both a tick and a
-- late matching MD event can be the first operation after the deadline.
selected = true
capture(4, 40)
assert(packets[7]:match('|5|sector_b.-|%-|%-|%-|$'))
for pass = 1, 2 do
    clock = 5 + (pass-1)*3
    owner:tick()
    local late = request
    clock = clock + 1
    local before = #packets
    if pass == 1 then owner:tick() else owner:event(late .. '|reply') end
    assert(packets[before + 1] == 'GAP' and timeouts == pass)
    assert(closed == 1 + pass and #faults == 0)
    owner:event(late .. '|reply')
    owner:tick()
    assert(request ~= late)
    owner:event(late .. '|reply')
    assert(#packets == before + 1)
    owner:event(request .. '|reply')
    assert(packets[before + 2]:match('|' .. (5+pass) .. '|sector_b.-|%-|%-|%-|$'))
end

-- An expired request cannot hide a changed controlled actor. The next reset
-- fails the binding check, and no further sample is emitted even if it returns.
clock = 12
owner:tick()
binding = false
clock = 13
owner:tick()
assert(timeouts == 3 and #faults == 0)
owner:tick()
assert(#faults == 1 and faults[1] == 'reset')
local final_count = #packets
binding = true
capture(14, 40)
assert(#packets == final_count and #faults == 1)

-- Exercise the native adapter's actual diagnostic callback without invoking X4.
local lines, native_options = {}, nil
DebugError = function(line) lines[#lines + 1] = line end
RegisterEvent = function() return true end
X4LAN_REPLICA_MOTION_BINDING = {normalize_profile_save_path = function(path) return path end}
X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER = {new_x4 = function(options) return options end}
local make_source = source.new
source.new = function(options) native_options = options; return {} end
source.new_x4({port = 1, lease = 'test', generation = generation, nonce = nonce, index = index},
              '/private/save', {epoch = epoch, issued_count = index}, {open = function() return {} end})
local config = native_options.make_sampler(actor, function() return true end)
assert(config.real_time_motion and config.real_time_orientation and not config.include_orientation)
assert(config.diagnostics and config.recover_expired_pending and config.allow_superhighway_gap
    and config.recheck_superhighway_boundary)
native_options.superhighway_gap('superhighway_boundary_recheck')
assert(lines[#lines] == '[X4LAN] AUTH_COMPANY_MOTION_SOURCE_GAP superhighway_boundary_recheck')
local prefix = '[X4LAN] AUTH_COMPANY_MOTION_SOURCE_FAILED '
for _, reason in ipairs({'register', 'tick', 'sample', 'event', 'reset', 'close', 'discard_pending',
                         'sample_dock_values', 'register_actor_key', 'reset_id_roundtrip',
                         'tick_external_binding', 'tick_id_convert', 'tick_guard_rejected',
                         'tick_reader_start_dispatch', 'tick_reader_tick_clock_timeout',
                         'sample_reader_response_snapshot_callback', 'sample_reader_response_descriptor_unavailable',
                         'sample_reader_response_descriptor_invalid', 'sample_reader_response_transit_rejected',
                         'sample_boundary_recheck_exhausted'}) do
    config.fault(reason)
    assert(lines[#lines] == prefix .. reason)
end
for _, reason in ipairs({'event_dock_values|payload', 'sample_private_error', 'tick_reader_start_raw error or payload', 17, {}}) do
    config.fault(reason)
    assert(lines[#lines] == prefix .. 'unknown')
end
config.fault(nil)
assert(lines[#lines] == prefix .. 'unknown')

dofile('src/authority/lua/replica_motion_binding.lua')
local current_path = 'C:\\Users\\Player\\45858\\save\\'
package.loaded.ffi = {
    os = 'Windows', NULL = {}, cdef = function() end, string = function(value) return value end,
    C = {
        GetSaveFolderPath = function() return current_path end,
        IsPlayerValid = function() return true end,
        IsGamePaused = function() return false end,
    },
}
X4LAN_RESOLVE_STRING = {parse_hex = function() return actor end}
source.arm({controlled_actor = function() return '0x41' end})
local windows_path = 'C:/Users/Player/45858/save'
local motion_config = {port = 1, lease = 'test', generation = generation, nonce = nonce, index = index}
local pinned = {epoch = epoch, issued_count = index}
local udp = {open = function() return {} end}
assert(not pcall(source.new_x4, motion_config, windows_path, pinned, udp))
source.new_x4(motion_config, windows_path, pinned, udp, '45858')
assert(native_options.available())
config = native_options.make_sampler(actor, function() return true end)
assert(config.expected_save_path == windows_path)
assert(config.normalize_save_path(current_path) == windows_path)
assert(config.normalize_save_path('C:/Users/Player/45857/save') == nil)
current_path = 'C:/Users/Other/45858/save'
assert(not native_options.available())

-- Exercise the real correlated descriptor, sampler, and Company owner.
local descriptor = dofile('tools/dev/authority_job_descriptor_probe.lua')
local function transit_case(response, allow_gap, return_to_origin, oriented)
    local now, current_actor, x = 1, actor, 10
    local issued, sent, failed = {}, {}, {}
    local client = make_source({
        epoch = epoch, index = index,
        actor = function() return current_actor end,
        available = function() return true end,
        send = function(payload) sent[#sent + 1] = payload end,
        make_sampler = function(id, guard)
            return sampler.new({
                enabled = true, epoch = epoch, generation = generation, nonce = nonce,
                max_tracks = 1, scan_budget = 1, interval = .10, timeout = 1,
                min_dt = .02, max_dt = 1, real_time_motion = true,
                allow_superhighway_gap = allow_gap, real_time_orientation = oriented,
                clock = function() return now end, guard = guard, actor_key = tostring,
                binding_guard = function() return false end, saved_baseline_limit = index,
                saved_baseline_guard = function(row, value)
                    return row.index == index and value == id and current_actor == id
                end,
                reader_factory = function(row, value, rid)
                    return descriptor.new({
                        enabled = true, epoch = epoch, request_id = rid, timeout = 1,
                        row = row, actor = value, saved_baseline = index,
                        allow_superhighway_gap = allow_gap, guard = guard,
                        include_local_sector = true, include_capture_time = true, include_orientation = oriented,
                        capture_clock = function() return now end,
                        clock = function() return now end, game_time = function() return now end,
                        actor_key = tostring, hex_key = function(hex) return tostring(tonumber(hex)) end,
                        issue = function(_, _, args)
                            assert(args.allow_superhighway_gap == (allow_gap and true or nil))
                            issued[#issued + 1] = args.request_id
                            return true
                        end,
                        snapshot = function(_, sector)
                            return {sector_local = tostring(tonumber(sector)), x = x, y = 0, z = 0,
                                game_before = now, game_after = now,
                                capture_before = now, capture_after = now,
                                yaw = now == 1 and 0 or 3, pitch = -.25, roll = .5}
                        end,
                    })
                end,
                fault = function(reason) failed[#failed + 1] = reason end,
            })
        end,
    })
    local function reply(t, result, response_epoch, response_index)
        now = t
        client:tick()
        client:event(table.concat({assert(issued[#issued]), response_epoch or epoch,
            response_index or index, result}, '|'))
    end
    local old = 'ok|0x2cc|ship_macro|owner|0x111|sector_a'
    local new = return_to_origin and old or 'ok|0x2cc|ship_macro|owner|0x112|sector_b'
    local result_sector = return_to_origin and 'sector_a' or 'sector_b'
    client:set_running(true)
    reply(1, old)
    x = 12
    reply(1.2, old)
    local format = oriented and 'SAMPLE4' or 'SAMPLE3'
    local angles = oriented and '3|%-0%.25|0%.5|' or ''
    assert(sent[1] == format .. '|' .. epoch .. '|1961|1|sector_a|1|1|10|0|0|-|-|-|'
        .. (oriented and '0|-0.25|0.5|' or ''))
    assert(sent[2]:match('^' .. format .. '|.*|2|sector_a.*|10%.?[%d]*|0|0|' .. angles .. '$'))
    if response then
        now = 1.4
        client:tick()
        local rid = issued[#issued]
        if response == 'binding' then current_actor = actor + 1 end
        local packet = table.concat({rid, response == 'epoch' and generation or epoch,
            response == 'index' and index + 1 or index, ({
                binding = 'transit|0x2cc', epoch = 'transit|0x2cc', index = 'transit|0x2cc',
            })[response] or response}, '|')
        client:event(packet)
        assert(#failed == 1 and sent[3] == 'GAP')
        current_actor = actor
        reply(2, new)
        assert(#issued == 3 and #sent == 3)
        return
    end

    reply(1.4, 'transit|0x2cc')
    assert(sent[3] == 'GAP' and #issued == 3)
    local stale = issued[3]
    now = 1.45
    client:tick()
    assert(#issued == 3)
    reply(1.6, 'transit|0x2cc')
    assert(#sent == 3 and #issued == 4)
    now = 1.8
    client:tick()
    client:event(stale .. '|' .. epoch .. '|' .. index .. '|transit|0x2cc')
    assert(#sent == 3 and #issued == 5)
    x = 700
    reply(1.8, new)
    assert(#failed == 0 and sent[4]:match('|3|' .. result_sector .. '.*|%-|%-|%-|' .. angles .. '$'))
    x = 702
    reply(2, new)
    assert(sent[5]:match('|4|' .. result_sector .. '.*|10%.?[%d]*|0|0|' .. angles .. '$'))
end

transit_case(nil, true)
transit_case(nil, true, true)
transit_case(nil, true, false, true)
for _, response in ipairs({'unavailable', 'transit|0x2cd', 'transit|0x2cc|junk',
                            'transit0x2cc', 'binding', 'epoch', 'index'}) do
    transit_case(response, true)
end
transit_case('transit|0x2cc', false)
