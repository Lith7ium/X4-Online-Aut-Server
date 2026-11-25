-- Real fixed-fixture inspector/setter and playback with a fake X4 ABI, no game.
local ffi = require('ffi')
ffi.cdef[[void TestSetRecoveryCode(bool);void TestAlignmentMode(int);int TestWrites(void);float TestYaw(void);]]
ffi.C.TestSetRecoveryCode(true)
local mode = assert(os.getenv('ALIGNMENT_CASE'))
local now = 1
local prepared = mode ~= 'unprepared'
local events, faults = {}, {}
function GetCurRealTime() return now end
function ConvertStringToLuaID(value)
 assert(value:match('^[1-9]%d*$'))
 return ffi.new('uint64_t', tonumber(value))
end
function ConvertIDTo64Bit(value) return value end
function GetComponentData(reference, ...)
 local field = select(1, ...)
 if field == 'isdocked' then return mode == 'docked', false, false end
 if field == 'macro' and tonumber(reference) == 41 then
  if mode == 'stale_during_inspection' then now = 3 end
  return (mode == 'fixture' or mode == 'mfq_wrong_macro') and 'wrong' or 'ship_arg_m_trans_container_02_b_macro', 'antigone'
 end
 if field == 'macro' and tonumber(reference) == 43 then return 'cluster_06_sector001_macro' end
 error('unexpected component field')
end
function AddUITriggeredEvent() error('initial alignment does not introduce an MD request') end
local resolver = dofile('tools/dev/authority_registry_resolve_string.lua')
X4LAN_REPLICA_MOTION_BINDING = dofile('src/authority/lua/replica_motion_binding.lua')
local module = dofile('src/authority/lua/saved_motion_apply.lua')
local buffer = dofile('src/authority/lua/motion_buffer.lua')
local playback = dofile('src/authority/lua/motion_playback.lua')
local snapshot = {frame='cluster_06_sector001_macro', sequence=7, source_time=77,
 pose={1000, 25, -10}, rate={0, 0, 0}}
local options = {enabled=true, test_profile='45841', fixture='recovery_enh_870',
 expected_save_path='/private/45841/save', resolve=resolver, guard=function() return true end,
 binding={actor_key='41', sector_key='43', zone_key='45', index=1318,
 epoch='4ba7be438ebb04ec5bb85fdb95b5a488', sector_macro=snapshot.frame},
 max_age=1, max_step=50, buffer=buffer, buffer_options={delay=0, horizon=0, stale=.75},
 initial_alignment_fixture='recovery_enh_870', initial_alignment_state={used=false, uncertain=false},
 prepared=function() return prepared end, initial_sample={snapshot=snapshot, arrival=.9},
 record=function(event)
  if mode == 'record_failure' then error('observer failure') end
  events[#events+1] = event
  return true
 end,
 fault=function(reason, uncertain, stage) faults[#faults+1]={reason=reason, uncertain=uncertain, stage=stage} end,
 playback={new=function(callbacks)
  if mode == 'running_limit' then return playback.new(callbacks) end
  return callbacks
 end}}
if mode == 'stale' then options.initial_sample.arrival = 0 end
if mode == 'future' then options.initial_sample.arrival = 2 end
if mode == 'profile' then options.expected_save_path = '/other/45841/save' end
if mode == 'actor' then options.binding.actor_key = '42' end
if mode == 'frame' then snapshot.frame = 'wrong_sector' end
if mode == 'readback' then ffi.C.TestAlignmentMode(2) end
if mode == 'identity_after_write' then ffi.C.TestAlignmentMode(3) end
if mode == 'player' then ffi.C.TestAlignmentMode(4) end
if mode == 'invalid_prewrite_pose' then ffi.C.TestAlignmentMode(5) end
if mode == 'default_off' then options.initial_alignment_fixture = nil end
local mfq_case = mode:match('^mfq_') ~= nil
if mfq_case then
 options.fixture = 'recovery_mfq_419'
 options.initial_alignment_fixture = options.fixture
 options.binding.epoch = '26a1a365c66788aa06f0aeb9e18ff62e'
 options.binding.index = 943
 ffi.C.TestAlignmentMode(6)
 if mode == 'mfq_wrong_epoch' then options.binding.epoch = '4ba7be438ebb04ec5bb85fdb95b5a488' end
 if mode == 'mfq_wrong_index' then options.binding.index = 1318 end
 if mode == 'mfq_wrong_code' then ffi.C.TestAlignmentMode(0) end
 if mode == 'mfq_player' then ffi.C.TestAlignmentMode(7) end
 if mode == 'mfq_guard' then options.guard = function() return false end end
 if mode == 'mfq_unprepared' then prepared = false end
 if mode == 'mfq_stale' then options.initial_sample.arrival = 0 end
 if mode == 'mfq_no_alignment' then options.initial_alignment_fixture = nil end
 if mode == 'mfq_wrong_epoch' or mode == 'mfq_wrong_index' or mode == 'mfq_no_alignment' then
  assert(not pcall(module.new_playback_x4, options) and ffi.C.TestWrites() == 0)
  return
 end
end
local real_time_case = mode:match('^v3_') ~= nil or mfq_case
if real_time_case then
 options.sample_version = 3
 snapshot.version = 3
 snapshot.capture_time = 123.5
 snapshot.real_rate = {4, 0, 0}
 snapshot.rate = nil
end
if mode == 'v3_bad_capture' then snapshot.capture_time = 0/0 end
if mode == 'v3_bad_game' then snapshot.source_time = 1e12+1 end
if mode == 'v3_bad_rate' then snapshot.real_rate[1] = 5001 end
if mode == 'v3_bad_rate_type' then snapshot.real_rate = false end
if mode == 'v3_bad_rate_key' then snapshot.real_rate.extra = 0 end
if mode == 'v3_mixed_fields' then snapshot.rate = {0, 0, 0} end
local mutations = {
 v3_mutate_capture=function() snapshot.capture_time = 456 end,
 v3_mutate_version=function() snapshot.version = 1 end,
 v3_mutate_rate=function() snapshot.real_rate[1] = 10 end,
 v3_mutate_rate_reference=function() snapshot.real_rate = {4, 0, 0} end,
 v3_mutate_game=function() snapshot.source_time = 78 end,
 v3_mutate_pose=function() snapshot.pose[1] = 1001 end,
 v3_mutate_pose_reference=function() snapshot.pose = {1000, 25, -10} end,
 v3_mutate_snapshot_reference=function()
  local replacement = {}
  for key, value in pairs(snapshot) do replacement[key] = value end
  options.initial_sample.snapshot = replacement
 end,
 v3_mutate_arrival=function() options.initial_sample.arrival = .95 end,
 v3_mutate_key=function() snapshot.extra = true end,
}
if mutations[mode] then
 options.prepared = function() mutations[mode](); return true end
end
if mode == 'v3_postwrite_mutation' then
 options.prepared = function()
  if ffi.C.TestWrites() > 0 then snapshot.capture_time = 456 end
  return true
 end
end
local result = module.new_playback_x4(options)
if mode == 'default_off' then
 assert(result and ffi.C.TestWrites() == 0 and #events == 0)
 return
end
local rejected_before_write = {stale=true, future=true, unprepared=true, profile=true, actor=true,
 frame=true, fixture=true, docked=true, player=true, invalid_prewrite_pose=true, stale_during_inspection=true,
 mfq_wrong_code=true, mfq_wrong_macro=true, mfq_player=true, mfq_guard=true, mfq_unprepared=true, mfq_stale=true}
if mutations[mode] or mode:match('^v3_bad_') or mode == 'v3_mixed_fields' then
 rejected_before_write[mode] = true
end
if rejected_before_write[mode] then
 assert(not result and ffi.C.TestWrites() == 0 and #faults == 1 and not faults[1].uncertain)
 return
end
if mode == 'readback' or mode == 'identity_after_write' or mode == 'record_failure' or mode == 'v3_postwrite_mutation' then
 assert(not result and ffi.C.TestWrites() == 1 and #faults == 1 and faults[1].uncertain)
 assert(options.initial_alignment_state.uncertain)
 return
end
assert(result and ffi.C.TestWrites() == 1 and #events == 1 and #faults == 0)
local record = events[1]
assert(record.kind == 'initial_alignment' and record.source_sequence == 7 and record.source_time == 77)
assert(record.arrival == .9 and record.local_time == 1 and record.before.x == 0)
if real_time_case then assert(record.capture_time == 123.5) else assert(record.capture_time == nil) end
assert(record.target.x == 1000 and record.actual.x == 1000 and record.error == 0 and record.tolerance == 3)
assert(math.abs(ffi.C.TestYaw() - 90) < .0001) -- Native setter receives degrees; readback remains radians.
assert(options.initial_alignment_state.used and not options.initial_alignment_state.uncertain)
if mode == 'running_limit' then
 assert(result:push({frame=snapshot.frame, sequence=8, source_time=78, pose={1100,25,-10},rate={0,0,0}},1))
 now=1.01
 assert(not result:tick() and ffi.C.TestWrites() == 1)
 return
end
assert(mode == 'success' or mode == 'v3_success' or mode == 'mfq_success')
if mfq_case then
 assert(record.epoch == '26a1a365c66788aa06f0aeb9e18ff62e' and record.index == 943)
 assert(not pcall(module.new_x4, options)) -- The one-shot factory still excludes MFQ.
end
assert(not module.new_playback_x4(options) and ffi.C.TestWrites() == 1) -- Consumed state cannot retry.
