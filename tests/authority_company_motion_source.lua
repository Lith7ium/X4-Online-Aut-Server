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
source.new = function(options) native_options = options; return {} end
source.new_x4({port = 1, lease = 'test', generation = generation, nonce = nonce, index = index},
              '/private/save', {epoch = epoch, issued_count = index}, {open = function() return {} end})
local config = native_options.make_sampler(actor, function() return true end)
assert(config.diagnostics == true and config.recover_expired_pending == true)
local prefix = '[X4LAN] AUTH_COMPANY_MOTION_SOURCE_FAILED '
for _, reason in ipairs({'register', 'tick', 'sample', 'event', 'reset', 'close', 'discard_pending',
                         'sample_dock_values', 'register_actor_key', 'reset_id_roundtrip',
                         'tick_external_binding', 'tick_id_convert'}) do
    config.fault(reason)
    assert(lines[#lines] == prefix .. reason)
end
for _, reason in ipairs({'event_dock_values|payload', 'sample_private_error', 17, {}}) do
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
