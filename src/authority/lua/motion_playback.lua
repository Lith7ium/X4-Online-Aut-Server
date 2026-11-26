-- Default-off XYZ playback. One existing UI owner supplies samples and ticks.
-- Arrival/render times share the local real-time clock. Legacy samples retain
-- game-clock reconstruction; explicit version 3 uses source capture seconds.
local M = {}

local function finite(value)
 return type(value) == 'number' and value == value and math.abs(value) < math.huge
end

local function copy_pose(value)
 local result = {}
 for _, key in ipairs({'x', 'y', 'z', 'yaw', 'pitch', 'roll'}) do
  assert(finite(value[key]), 'invalid actual pose')
  result[key] = value[key]
 end
 return result
end

local function distance(first, second)
 return math.sqrt((first.x-second.x)^2 + (first.y-second.y)^2 + (first.z-second.z)^2)
end

function M.new(options)
 if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
 local configuration = {}
 for key, value in pairs(options) do configuration[key] = value end
 for _, key in ipairs({'clock', 'game', 'paused', 'guard', 'read', 'write', 'record', 'fault'}) do
  assert(type(configuration[key]) == 'function', key)
 end
 assert(type(configuration.buffer) == 'table' and type(configuration.buffer.new) == 'function')
 assert(type(configuration.frame) == 'string' and configuration.frame ~= '')
 assert(finite(configuration.max_age) and configuration.max_age > 0 and configuration.max_age <= 30)
 assert(finite(configuration.max_step) and configuration.max_step > 0 and configuration.max_step <= 50000000)
 local sample_version = configuration.sample_version
 if sample_version == nil then sample_version = 1 end
 assert(sample_version == 1 or sample_version == 3 or sample_version == 4, 'invalid playback sample version')
 local capture_clock = sample_version ~= 1
 local follows_attitude = sample_version == 4
 assert(not follows_attitude or type(configuration.attitude_changed) == 'function')
 local clock_basis = capture_clock and 'capture_time' or 'source_time'
 local buffer_options = {}
 for key, value in pairs(configuration.buffer_options or {}) do buffer_options[key] = value end
 assert(buffer_options.dimensions == nil or buffer_options.dimensions == 3)
 assert(buffer_options.hold_only == nil or buffer_options.hold_only == false)
 assert(buffer_options.clock_basis == nil or buffer_options.clock_basis == clock_basis,
  'playback clock basis conflicts with sample version')
 buffer_options.clock_basis = clock_basis
 buffer_options.dimensions = 3
 buffer_options.hold_only = false
 -- Binding completion can follow frames already rendered from earlier samples.
 -- The controller still validates the original receive timestamp and its age.
 buffer_options.deferred_arrival = true
 local function new_buffer() return configuration.buffer.new(buffer_options) end
 local buffer = new_buffer()
 local state, busy, uncertain = 'waiting', false, false
 local stage, failure_stage = 'state', nil
 local last_clock, last_game, last_sequence, last_source, last_arrival = nil, nil, 0, nil, nil
 local last_capture = nil
 local fresh_arrival, invalidated_at, previous_write = nil, nil, nil
 local api = {}

 local function fail(reason)
  if state ~= 'failed' then
   failure_stage = stage -- Retain the first failure before callbacks can stop/reenter.
   state = 'failed' -- Disable writes before invoking any external callback.
   pcall(configuration.fault, reason, uncertain, failure_stage)
  end
  return false
 end

 local function clock()
  stage = 'clock'
  local value = configuration.clock()
  assert(finite(value) and value >= 0 and (not last_clock or value >= last_clock))
  last_clock = value
  return value
 end

 local function check()
  stage = 'state'
  assert(state ~= 'failed' and state ~= 'stopped')
  local previous_state = state
  local now = clock()
  stage = 'guard'
  assert(configuration.guard() == true and state == previous_state)
  assert(clock() >= now and state == previous_state)
 end

 local function invalidate(now, next_state)
  stage = 'buffer_reset'
  buffer = new_buffer()
  fresh_arrival = nil
  invalidated_at = now
  state = next_state
 end

 local function paused(now)
  local previous_state = state
  stage = 'paused'
  local value = configuration.paused()
  assert(type(value) == 'boolean' and state == previous_state)
  if value then
   if state ~= 'paused' then invalidate(now, 'paused') end
   return true
  end
  -- A binding received during the pause can complete only after resumption.
  -- It is not a fresh post-resume sample merely because completion was late.
  if state == 'paused' then invalidate(now, 'waiting') end
  return false
 end

 local function record(event, operation)
  local previous_state = state
  stage = operation
  assert(configuration.record(event) == true and state == previous_state)
  check()
 end

 function api:push(snapshot, arrival)
  if busy or state == 'failed' or state == 'stopped' then return fail('push_state') end
  busy = true
  local accepted, rejection = false, nil
  local ok = pcall(function()
   local now = clock()
   if paused(now) then rejection='paused'; return end
   check()
   stage = 'sample'
   assert(type(snapshot) == 'table' and snapshot.frame == configuration.frame)
   if capture_clock then
    assert(snapshot.version == sample_version and finite(snapshot.capture_time) and
     snapshot.capture_time >= 0 and (not last_capture or snapshot.capture_time > last_capture))
   else
    assert(snapshot.version ~= 3 and snapshot.version ~= 4 and snapshot.capture_time == nil and snapshot.real_rate == nil)
   end
   stage = 'arrival'
   assert(finite(arrival) and arrival >= 0 and arrival <= now and
    (not last_arrival or arrival >= last_arrival))
   stage = 'sequence'
   assert(type(snapshot.sequence) == 'number' and snapshot.sequence % 1 == 0 and
    snapshot.sequence > last_sequence and finite(snapshot.source_time) and
    (not last_source or (capture_clock and snapshot.source_time >= last_source) or
     snapshot.source_time > last_source))
   -- Do not infer age from the remote clock's unrelated absolute origin.
   if now-arrival >= configuration.max_age or (invalidated_at and arrival <= invalidated_at) then
    rejection='expired'; return
   end
   stage = 'buffer_push'
   assert(buffer:push(snapshot, arrival) == true)
   check()
   last_sequence, last_source, last_arrival = snapshot.sequence, snapshot.source_time, arrival
   if capture_clock then last_capture = snapshot.capture_time end
   fresh_arrival = arrival
   state = 'active'
   accepted = true
  end)
  busy = false
  if not ok then return fail('push') end
  return accepted, rejection
 end

 function api:tick()
  if busy or state == 'failed' then return fail('tick_state') end
  if state == 'stopped' then return true end
  busy = true
  local ok = pcall(function()
   local now = clock()
   if paused(now) then return end
   check()
   if not fresh_arrival and not previous_write then return end
   local previous_state = state
   stage = 'read'
   local observed = configuration.read()
   stage = 'read_pose'
   local actual = copy_pose(observed)
   assert(state == previous_state)
   check()
   stage = 'game_read'
   local game_time = configuration.game()
   stage = 'game_value'
   assert(finite(game_time) and game_time >= 0)
   stage = 'game_clock'
   assert(not last_game or game_time >= last_game)
   last_game = game_time
   assert(state == previous_state)
   check()
   if previous_write then
    record({kind='readback', actual=copy_pose(actual), target=copy_pose(previous_write.target),
     source_sequence=previous_write.sequence, latest_source_time=previous_write.latest_source_time,
     latest_capture_time=previous_write.latest_capture_time,
     local_time=now, game_time=game_time, elapsed=now-previous_write.local_time,
     error=distance(actual, previous_write.target)}, 'readback_record')
    previous_write = nil
    uncertain = false
   end
   if not fresh_arrival then return end
   if now-fresh_arrival >= configuration.max_age then
    invalidate(now, 'stale')
    return
   end
   stage = 'render'
   local rendered = assert(buffer:render(now))
   stage = 'render_frame'
   assert(rendered.frame == configuration.frame and #rendered.pose == 3)
   local target = {x=rendered.pose[1], y=rendered.pose[2], z=rendered.pose[3],
    yaw=actual.yaw, pitch=actual.pitch, roll=actual.roll}
   if follows_attitude then
    target.yaw, target.pitch, target.roll =
     rendered.attitude.yaw, rendered.attitude.pitch, rendered.attitude.roll
   end
   target = copy_pose(target)
   record({kind='observation', actual=copy_pose(actual), target=copy_pose(target),
    source_sequence=rendered.source_sequence, latest_source_time=last_source,
    latest_capture_time=last_capture,
    phase=rendered.phase, local_time=now, game_time=game_time}, 'observation_record')
   stage = 'distance'
   local step = distance(actual, target)
   assert(finite(step) and step <= configuration.max_step, 'correction too large')
   if step == 0 and (not follows_attitude or not configuration.attitude_changed(actual, target)) then return end
   check()
   if clock()-fresh_arrival >= configuration.max_age then invalidate(clock(), 'stale'); return end
   uncertain = true -- An exception can follow a partial native effect.
   previous_write = {target=copy_pose(target), sequence=rendered.source_sequence,
    latest_source_time=last_source, latest_capture_time=last_capture, local_time=now}
   stage = 'write'
   local written = configuration.write(copy_pose(target), copy_pose(actual))
   stage = 'write_state'
   assert(written == true and state == 'active')
   check()
  end)
  busy = false
  if not ok then return fail('tick') end
  return true
 end

 function api:stop()
  -- STOP must disable writes immediately, including from a reentrant callback.
  if state == 'failed' or state == 'stopped' then return true end
  state = 'stopped'
  buffer = nil
  fresh_arrival = nil
  return true
 end
 function api:state() return state end
 function api:outcome_uncertain() return uncertain end
 function api:failure_stage() return failure_stage end
 return api
end

X4LAN_MOTION_PLAYBACK = M
return M
