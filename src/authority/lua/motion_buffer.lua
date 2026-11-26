-- Read-only motion reconstruction for a single explicitly identified fixture.
-- Adapted from the X4LAN_MOTION LAN-lab motion buffer.
-- No game API calls, object lookup, velocity writes or networking.
local M = {}
local MAX_SEQUENCE = 9007199254740991
local PI = math.pi

local function finite(value)
 return type(value) == 'number' and value == value and value ~= math.huge and value ~= -math.huge
end

local function frame(value)
 return type(value) == 'string' and #value > 0 and #value <= 128 and
  value:match('^[A-Za-z0-9_.%-]+$') ~= nil
end

local function angle(radians)
 return (radians + PI) % (2 * PI) - PI
end

-- X4's sector basis is Ry(yaw) * Rx(-pitch) * Rz(-roll).
local function quaternion(attitude)
 local y, p, r = attitude.yaw / 2, attitude.pitch / 2, attitude.roll / 2
 local cy, cp, cr = math.cos(y), math.cos(p), math.cos(r)
 local sy, sp, sr = math.sin(y), math.sin(p), math.sin(r)
 return {-cy*sp*cr - sy*cp*sr, sy*cp*cr - cy*sp*sr,
         sy*sp*cr - cy*cp*sr, cy*cp*cr + sy*sp*sr}
end

local function dot(a, b)
 return a[1]*b[1] + a[2]*b[2] + a[3]*b[3] + a[4]*b[4]
end

local function euler(q)
 local x, y, z, w = q[1], q[2], q[3], q[4]
 local sine_pitch = math.max(-1, math.min(1, 2*(y*z - w*x)))
 local pitch = math.asin(sine_pitch)
 if math.abs(sine_pitch) >= 1 - 1e-12 then
  -- At the pole only the combined heading/roll is observable.
  return {yaw=math.atan2(2*(w*y - x*z), 1 - 2*(y*y + z*z)), pitch=pitch, roll=0}
 end
 return {yaw=math.atan2(2*(x*z + w*y), 1 - 2*(x*x + y*y)), pitch=pitch,
         roll=math.atan2(-2*(x*y + w*z), 1 - 2*(x*x + z*z))}
end

local function interpolate_attitude(a, b, fraction)
 local cosine = dot(a, b)
 local sign = cosine < 0 and -1 or 1
 cosine = math.min(1, math.abs(cosine))
 local left, right = 1 - fraction, fraction
 if cosine < .999999 then
  local theta = math.acos(cosine)
  local sine = math.sin(theta)
  left, right = math.sin(left*theta) / sine, math.sin(right*theta) / sine
 end
 local q = {}
 for index = 1, 4 do q[index] = left*a[index] + sign*right*b[index] end
 local length = math.sqrt(dot(q, q))
 for index = 1, 4 do q[index] = q[index] / length end
 return euler(q)
end

function M.attitude_changed(a, b)
 return math.abs(dot(quaternion(a), quaternion(b))) < math.cos(.00001 / 2)
end

function M.copy_attitude(value)
 if type(value) ~= 'table' or getmetatable(value) ~= nil then return nil end
 local result = {}
 for _, axis in ipairs({'yaw', 'pitch', 'roll'}) do
  if not finite(value[axis]) or math.abs(value[axis]) > 2 * PI then return nil end
  result[axis] = value[axis]
 end
 return result
end

local function vector(values, is_rate, dimensions)
 if type(values) ~= 'table' or #values ~= dimensions then return false end
 for index = 1, dimensions do
  local limit = index <= 3 and (is_rate and 100000 or 50000000) or 2 * PI
  if not finite(values[index]) or math.abs(values[index]) > limit then return false end
 end
 return true
end

local function copy(values)
 if not values then return nil end
 local result = {}
 for index = 1, #values do result[index] = values[index] end
 return result
end

local function speed(rate)
 if not rate then return 0 end
 return math.sqrt(rate[1]^2 + rate[2]^2 + rate[3]^2)
end

local function timestamp(seconds)
 return finite(seconds) and seconds >= 0 and seconds <= 1e12
end

local function valid_sample(sample, dimensions)
 return type(sample) == 'table' and sample.version ~= 3 and sample.version ~= 4 and sample.attitude == nil and
  sample.capture_time == nil and sample.real_rate == nil and
  frame(sample.frame) and type(sample.sequence) == 'number' and
  sample.sequence % 1 == 0 and sample.sequence >= 1 and sample.sequence <= MAX_SEQUENCE and
  timestamp(sample.source_time) and vector(sample.pose, false, dimensions) and
  (sample.rate == nil or vector(sample.rate, true, dimensions))
end

local function valid_capture_sample(sample, dimensions)
 if type(sample) ~= 'table' or getmetatable(sample) ~= nil or dimensions ~= 3 then return false end
 local allowed = {version=true, frame=true, sequence=true, source_time=true,
  capture_time=true, pose=true, real_rate=true, attitude=true}
 for key in pairs(sample) do
  if not allowed[key] then return false end
 end
 if sample.real_rate ~= nil then
  if not vector(sample.real_rate, true, dimensions) then return false end
  for index = 1, dimensions do
   if math.abs(sample.real_rate[index]) > 5000 then return false end
  end
 end
 local valid_version = (sample.version == 3 and sample.attitude == nil)
  or (sample.version == 4 and M.copy_attitude(sample.attitude) ~= nil)
 return valid_version and frame(sample.frame) and type(sample.sequence) == 'number' and
  sample.sequence % 1 == 0 and sample.sequence >= 1 and sample.sequence <= MAX_SEQUENCE and
  timestamp(sample.source_time) and timestamp(sample.capture_time) and
  vector(sample.pose, false, dimensions)
end

-- A wrapped-Euler derivative is useful for extrapolation, but is not an engine
-- native angular-velocity vector. Samples must have matching frames, increasing
-- sequence numbers and a positive source-time interval within max_gap. Missing
-- arguments are rejected; sequence numbers need not be consecutive.
function M.estimate(previous, sample, max_gap)
 local dimensions = type(sample) == 'table' and type(sample.pose) == 'table' and #sample.pose or 0
 if (dimensions ~= 3 and dimensions ~= 6) or not valid_sample(previous, dimensions) or
    not valid_sample(sample, dimensions) or previous.frame ~= sample.frame or
    not finite(max_gap) or max_gap <= 0 or max_gap > 60 then return nil end
 local source_seconds = sample.source_time - previous.source_time
 if source_seconds <= .001 or source_seconds > max_gap or sample.sequence <= previous.sequence then return nil end
 local rate = {}
 for index = 1, dimensions do
  local delta = sample.pose[index] - previous.pose[index]
  rate[index] = (index > 3 and angle(delta) or delta) / source_seconds
 end
 if not vector(rate, true, dimensions) then return nil end
 return rate
end

function M.new(options)
 options = options or {}
 assert(type(options) == 'table', 'options must be a table')
 local known = {delay=true, horizon=true, stale=true, teleport_distance=true,
  clock_leak=true, slew=true, dimensions=true, hold_only=true, deferred_arrival=true, clock_basis=true}
 for key in pairs(options) do assert(known[key], 'unknown motion option') end
 local cfg = {delay=options.delay or .15, horizon=options.horizon or .35,
  stale=options.stale or .75, teleport_distance=options.teleport_distance or 10000,
  clock_leak=options.clock_leak or .002, slew=options.slew or .1}
 local clock_basis = options.clock_basis
 if clock_basis == nil then clock_basis = 'source_time' end
 assert(clock_basis == 'source_time' or clock_basis == 'capture_time', 'invalid motion clock basis')
 local capture_clock = clock_basis == 'capture_time'
 local dimensions = options.dimensions or 6
 assert(not capture_clock or dimensions == 3, 'capture motion requires XYZ dimensions')
 assert(dimensions == 3 or dimensions == 6, 'invalid motion dimensions')
 assert(options.hold_only == nil or type(options.hold_only) == 'boolean', 'invalid hold mode')
 local hold_only = options.hold_only == true
 assert(options.deferred_arrival == nil or type(options.deferred_arrival) == 'boolean', 'invalid deferred arrival mode')
 local deferred_arrival = options.deferred_arrival == true
 for key, value in pairs(cfg) do
  assert(finite(value) and value >= 0, 'invalid motion option: '..key)
 end
 assert(cfg.delay <= 60 and cfg.horizon <= 60 and cfg.stale > 0 and cfg.stale <= 60 and
  cfg.teleport_distance <= 50000000 and cfg.clock_leak <= 1 and cfg.slew <= 1, 'motion option out of bounds')

 local samples, offset, last_arrival, last_sequence, last_sample_time = {}, nil, nil, 0, nil
 local last_source_game, version
 local lag, lag_at, last_render = nil, nil, nil
 local track = {}

 -- The selected remote clock and local observer clock have unrelated origins.
 -- Normalize only the reconstruction timestamp; retain game time for ordering.
 function track:push(sample, arrival)
  local valid
  if capture_clock then
   valid = valid_capture_sample(sample, dimensions)
  else
   valid = valid_sample(sample, dimensions) and sample.version ~= 3 and
    sample.capture_time == nil and sample.real_rate == nil
  end
  if not valid or (version and sample.version ~= version) or not timestamp(arrival) then return false, 'invalid' end
  local sample_time = sample[clock_basis]
  local sample_rate = sample.rate
  if capture_clock then sample_rate = sample.real_rate end
  if sample.sequence <= last_sequence or (last_sample_time and sample_time <= last_sample_time) or
     (capture_clock and last_source_game and sample.source_time < last_source_game) or
     (last_arrival and arrival < last_arrival) or
     (not deferred_arrival and last_render and arrival < last_render) then return false, 'stale' end
  local last = samples[#samples]
  local reason = 'append'
  if last and sample.frame ~= last.frame then
   reason = 'frame'
  elseif last and sample_time - last.time > cfg.stale then
   reason = 'gap'
  elseif last then
   local distance_squared = 0
   for index = 1, 3 do distance_squared = distance_squared + (sample.pose[index] - last.pose[index])^2 end
   local distance = math.sqrt(distance_squared)
   local allowed_distance = cfg.teleport_distance + speed(last.rate) * (sample_time - last.time)
   if distance > allowed_distance then reason = 'teleport' end
  end
  if reason ~= 'append' then
   -- A new continuous motion segment needs a new source-to-observer clock
   -- estimate as well as new samples. Keeping the old minimum offset after a
   -- long interruption can put every new sample beyond the prediction horizon.
   samples = {}
   offset, lag, lag_at = nil, nil, nil
  end
  if not hold_only then
   if offset and last_arrival and arrival > last_arrival then
    offset = offset + cfg.clock_leak * (arrival - last_arrival)
   end
   offset = math.min(offset or math.huge, arrival - sample_time)
  end
  local rate = copy(sample_rate)
  if reason ~= 'append' then rate = nil end
  samples[#samples + 1] = {frame=sample.frame, sequence=sample.sequence, time=sample_time,
                           pose=copy(sample.pose), rate=rate,
                           attitude=sample.attitude and M.copy_attitude(sample.attitude),
                           rotation=sample.attitude and quaternion(sample.attitude)}
  version = sample.version
  while #samples > 16 do table.remove(samples, 1) end
  last_arrival, last_sequence, last_sample_time = arrival, sample.sequence, sample_time
  last_source_game = sample.source_time
  return true, reason
 end

 function track:render(now)
  if not timestamp(now) or (last_render and now < last_render) or
     (last_arrival and now < last_arrival) then return nil, 'invalid-clock' end
  if #samples == 0 then return nil, 'empty' end
  last_render = now
  if hold_only then
   local last = samples[#samples]
   return {frame=last.frame, source_sequence=last.sequence, pose=copy(last.pose),
           attitude=M.copy_attitude(last.attitude), phase='held-clock-unverified'}
  end

  local wanted_lag = offset + cfg.delay
  -- d(selected remote render time)/d(observer time). A snap or repeated clock value
  -- has no established derivative. A moving lag changes the displayed rate.
  local clock_factor
  if not lag or not lag_at or math.abs(wanted_lag - lag) > 1 then
   lag = wanted_lag
  else
   local elapsed = now - lag_at
   local old_lag = lag
   local step = cfg.slew * elapsed
   lag = wanted_lag > lag and math.min(wanted_lag, lag + step) or math.max(wanted_lag, lag - step)
   if elapsed > 0 then
    -- At a reached target, lag is locally constant on the forward side.
    if lag == wanted_lag then clock_factor = 1
    elseif wanted_lag > old_lag then clock_factor = 1 - cfg.slew
    else clock_factor = 1 + cfg.slew end
   end
  end
  lag_at = now
  local target_time = now - lag
  local first, last = samples[1], samples[#samples]
  local result = {frame=last.frame, source_sequence=last.sequence, pose={}, attitude=M.copy_attitude(last.attitude), phase='held'}
  if target_time <= first.time then
   result.pose = copy(first.pose)
   result.attitude = M.copy_attitude(first.attitude)
   result.source_sequence = first.sequence
   return result
  end

  for index = 2, #samples do
   local earlier, later = samples[index - 1], samples[index]
   if target_time <= later.time then
    local fraction = (target_time - earlier.time) / (later.time - earlier.time)
    for dimension = 1, dimensions do
     local delta = later.pose[dimension] - earlier.pose[dimension]
     result.pose[dimension] = dimension > 3 and angle(earlier.pose[dimension] + angle(delta) * fraction) or
      earlier.pose[dimension] + delta * fraction
    end
    if earlier.rotation then
     result.attitude = fraction == 1 and M.copy_attitude(later.attitude)
      or interpolate_attitude(earlier.rotation, later.rotation, fraction)
    end
    result.phase = 'interpolated'
    result.source_sequence = later.sequence
    -- At a knot, adjacent segments may have different slopes. Never infer a
    -- native angular vector from wrapped Euler-coordinate rates.
    if clock_factor and target_time ~= later.time then
     local velocity, valid = {}, true
     for dimension = 1, 3 do
      velocity[dimension] = (later.pose[dimension] - earlier.pose[dimension]) /
       (later.time - earlier.time) * clock_factor
      if not finite(velocity[dimension]) or math.abs(velocity[dimension]) > 100000 then valid = false end
     end
     if valid then result.linear_velocity = velocity end
    end
    return result
   end
  end

  local prediction_seconds = math.min(cfg.horizon, math.max(0, target_time - last.time))
  if last.rate then
   for dimension = 1, dimensions do
    local value = last.pose[dimension] + last.rate[dimension] * prediction_seconds
    result.pose[dimension] = dimension > 3 and angle(value) or value
   end
   if not vector(result.pose, false, dimensions) then return nil, 'out-of-bounds' end
   result.phase = target_time - last.time < cfg.horizon and 'extrapolated' or 'frozen'
   if result.phase == 'frozen' and clock_factor then
    result.linear_velocity = {0, 0, 0}
   elseif clock_factor then
    local velocity, valid = {}, true
    for dimension = 1, 3 do
     velocity[dimension] = last.rate[dimension] * clock_factor
     if not finite(velocity[dimension]) or math.abs(velocity[dimension]) > 100000 then valid = false end
    end
    if valid then result.linear_velocity = velocity end
   end
  else
   result.pose = copy(last.pose)
   result.phase = 'held-no-rate'
  end
  return result
 end
 return track
end
return M
