-- One explicitly selected test ship per process; the controller owns only its temporary Wait.
-- The caller must stop/drain playback before release. No AI-stack restoration claim.
-- A 30-second finite Wait has a conservative 29-second game-time lifetime here.
-- Release observes two advancing samples, separated by at least 0.1 game seconds;
-- it does not reproduce native08's seven-sample, six-second observation span.
-- Zero linear velocity is requested, never reported as a measured native value.
local M = {}
local fixtures = {
  recovery_enh_870 = {epoch = '4ba7be438ebb04ec5bb85fdb95b5a488', index = 1318},
  recovery_mfq_419 = {epoch = '26a1a365c66788aa06f0aeb9e18ff62e', index = 943},
}
local SCREEN = 'X4AuthoritySavedReplicaControl'
local registered = false
local function finite(value)
  return type(value) == 'number' and value == value and value >= 0 and value < math.huge
end
local function decimal(value)
  return type(value) == 'string' and #value <= 20 and value:match('^[1-9]%d*$') ~= nil
end
function M.new(options)
  if type(options) ~= 'table' or options.enabled ~= true then
    return nil, 'disabled'
  end
  local binding = options.binding
  local fixture_name = options.fixture or 'recovery_enh_870'
  local fixture = assert(fixtures[fixture_name], 'unknown replica control fixture')
  assert(type(binding) == 'table' and binding.index == fixture.index and binding.epoch == fixture.epoch and
    binding.sector_macro == 'cluster_06_sector001_macro')
  for _, key in ipairs({'actor_key', 'sector_key', 'zone_key'}) do
    assert(decimal(binding[key]))
  end
  assert(type(options.nonce) == 'string' and #options.nonce == 32 and options.nonce:match('^[0-9a-f]+$'))
  assert(finite(options.timeout) and options.timeout > 0 and options.timeout <= 30)
  for _, key in ipairs({'clock', 'game', 'guard', 'inspect', 'issue', 'fault'}) do
    assert(type(options[key]) == 'function', key)
  end
  local identity = {}
  for key, value in pairs(binding) do
    identity[key] = value
  end
  local state, step = 'new', nil
  local serial, ui_tick = 0, 0
  local pending, reply, deadline, last_time, last_game, last_tick = nil, nil, nil, nil, nil, nil, nil
  local mutation_possible, uncertain, busy = false, false, false
  local wait_started, last_checked_game = nil, nil
  local api = {}
  local function fail(reason)
    if state ~= 'failed' then
      state = 'failed'
      uncertain = mutation_possible
      pending = nil
      reply = nil
      pcall(options.fault, reason, uncertain)
    end
    return false
  end
  local function check()
    if state == 'failed' then
      return false
    end
    local game_time = options.game()
    if not finite(game_time) or (last_checked_game and game_time < last_checked_game) or
      (wait_started and state ~= 'released' and game_time - wait_started >= 29) then
      return false
    end
    last_checked_game = game_time
    local now = options.clock()
    if not finite(now) or (last_time and now < last_time) or (deadline and now >= deadline) then
      return false
    end
    last_time = now
    for key, value in pairs(identity) do
      if binding[key] ~= value then
        return false
      end
    end
    return options.guard() == true and options.inspect(identity) == true and options.guard() == true
  end
  local function dispatch(operation)
    if pending or not check() or serial >= 100000 then
      return false
    end
    serial = serial + 1
    pending = options.nonce..'-'..string.format('%.0f', serial)
    if operation == 'Prepare' or operation == 'Hold' or operation == 'Release' then
      mutation_possible = true
    end
    return options.issue(operation, {request_id = pending, fixture = fixture_name, epoch = identity.epoch, index = identity.index,
      actor_key = identity.actor_key, sector_key = identity.sector_key, zone_key = identity.zone_key, owner = options.nonce, request_sequence = serial}) == true and check()
  end
  local function operation(callback)
    if busy or state == 'failed' then
      return fail('reentrant_or_failed')
    end
    busy = true
    local ok, result = pcall(callback)
    busy = false
    if not ok or result ~= true then
      return fail('operation')
    end
    return true
  end
  function api:prepare()
    return operation(function()
      if state ~= 'new' and state ~= 'prepared' then
        return false
      end
      if not check() then
        return false
      end
      deadline = last_time + options.timeout
      if state == 'new' then
        wait_started = options.game()
      end
      step = state == 'new' and 'created' or 'checked'
      state = 'preparing'
      last_game = nil
      last_tick = nil
      return dispatch(step == 'created' and 'Prepare' or 'Check')
    end)
  end
  function api:release()
    return operation(function()
      if state ~= 'prepared' or not check() then
        return false
      end
      deadline = last_time + options.timeout
      state = 'releasing'
      step = 'released_request'
      last_game = nil
      last_tick = nil
      return dispatch('Release')
    end)
  end
  function api:event(value)
    -- Native callback only queues
    -- Existing UI owner is the sole state driver.
    if state == 'failed' then
      return false
    end
    if not pending or reply or type(value) ~= 'table' or value.request_id ~= pending then
      return fail('event_correlation')
    end
    if value.kind ~= 'Created' and value.kind ~= 'Waiting' and value.kind ~= 'Started' and
      value.kind ~= 'Held' and value.kind ~= 'Released' and value.kind ~= 'Restored' then
      return fail('event_kind')
    end
    reply = {request_id = value.request_id, kind = value.kind}
    return true
  end
  function api:tick()
    return operation(function()
      ui_tick = ui_tick + 1
      if not check() then
        return false
      end
      if state == 'new' or state == 'prepared' or state == 'released' then
        return true
      end
      if reply then
        local kind = reply.kind
        pending = nil
        reply = nil
        if step == 'created' and kind == 'Created' then
          step = 'first_started'
        elseif (step == 'first_started' or step == 'settled') and kind == 'Waiting' then -- Retry without advancing.
        elseif step == 'first_started' and kind == 'Started' then
          last_game = options.game()
          last_tick = ui_tick
          if not finite(last_game) then
            return false
          end
          step = 'settled'
        elseif step == 'settled' and kind == 'Started' then
          local now = options.game()
          if not finite(now) or now <= last_game or ui_tick <= last_tick then
            return false
          end
          last_game = now
          last_tick = ui_tick
          step = 'hold'
        elseif (step == 'hold' or step == 'checked') and kind == 'Held' then
          state = 'prepared'
          step = nil
          deadline = nil
          return true
        elseif step == 'released_request' and kind == 'Released' then
          step = 'first_release_sample'
        elseif (step == 'first_release_sample' or step == 'last_release_sample') and kind == 'Waiting' then
          -- Cancellation/prior-order resumption can settle asynchronously.
        elseif step == 'first_release_sample' and kind == 'Restored' then
          last_game = options.game()
          last_tick = ui_tick
          if not finite(last_game) then
            return false
          end
          step = 'last_release_sample'
        elseif step == 'last_release_sample' and kind == 'Restored' then
          local now = options.game()
          if not finite(now) or now <= last_game or ui_tick <= last_tick then
            return false
          end
          state = 'released'
          step = nil
          deadline = nil
          mutation_possible = false
          return true
        else
          return false
        end
      end
      if pending then
        return true
      end
      local now = options.game()
      if not finite(now) or (last_game and now < last_game) then
        return false
      end
      if last_game and (now - last_game < .1 or ui_tick <= last_tick) then
        return true
      end
      if step == 'first_started' or step == 'settled' then
        return dispatch('Probe')
      end
      if step == 'hold' then
        return dispatch('Hold')
      end
      if step == 'first_release_sample' or step == 'last_release_sample' then
        return dispatch('Sample')
      end
      return false
    end)
  end
  function api:state()
    return state
  end
  function api:outcome_uncertain()
    return uncertain or (mutation_possible and (state == 'preparing' or state == 'releasing'))
  end
  return api
end
function M.new_x4(options)
  if type(options) ~= 'table' or options.enabled ~= true then
    return nil, 'disabled'
  end
  assert(fixtures[options.fixture] and type(options.resolve) == 'table' and
  options.resolve == X4LAN_RESOLVE_STRING and type(options.resolve.parse_hex) == 'function' and
  type(options.resolve.actor_u64) == 'function')
  assert(not registered and type(RegisterEvent) == 'function' and type(AddUITriggeredEvent) == 'function')
  assert(type(X4LAN_SAVED_MOTION_APPLY) == 'table')
  local ffi = require('ffi')
  local reader = assert(X4LAN_SAVED_MOTION_APPLY.new_inspector_x4({
    enabled = true, fixture = options.fixture, test_profile = '45841', expected_save_path = options.expected_save_path,
    resolve = options.resolve, binding = options.binding, guard = options.guard,
  }))
  local function key(value)
    local identifier = options.resolve.actor_u64(value)
    return identifier and tostring(identifier):gsub('ULL$', '') or nil
  end
  local function reference(value)
    local native = ffi.new('uint64_t', 0)
    for digit in value:gmatch('.') do
      native = native * ffi.new('uint64_t', 10) + ffi.new('uint64_t', tonumber(digit))
    end
    if key(native) ~= value then
      return nil
    end
    local reference = ConvertStringToLuaID(value)
    if key(ConvertIDTo64Bit(reference)) ~= value then
      return nil
    end
    return reference
  end
  local native_options = {}
  for key, value in pairs(options) do
    native_options[key] = value
  end
  native_options.guard = reader.guard
  native_options.game = reader.game
  native_options.inspect = function()
    -- read() checks the local ship, sector, zone and pilot exclusion before and after reading its pose.
    return type(reader.read()) == 'table'
  end
  native_options.issue = function(operation, arguments)
    local actor = reference(arguments.actor_key)
    local sector = reference(arguments.sector_key)
    local zone = reference(arguments.zone_key)
    if not actor or not sector or not zone or not native_options.guard() then
      return false
    end
    arguments.actor = actor
    arguments.sector = sector
    arguments.zone = zone
    return AddUITriggeredEvent(SCREEN, operation, arguments) ~= false
  end
  local controller = assert(M.new(native_options))
  registered = true -- No retry after partial registration or an uncertain native request.
  assert(RegisterEvent(SCREEN..'.Result', function(_, value)
    if type(value) ~= 'string' or #value > 256 then
      controller:event(false)
      return
    end
    local request, kind, actor, sector, zone = value:match('^([0-9a-f]+%-%d+)|([A-Za-z]+)|(0[xX][0-9a-fA-F]+)|(0[xX][0-9a-fA-F]+)|(0[xX][0-9a-fA-F]+)$')
    local binding = options.binding
    if not request or key(options.resolve.parse_hex(actor)) ~= binding.actor_key or
      key(options.resolve.parse_hex(sector)) ~= binding.sector_key or key(options.resolve.parse_hex(zone)) ~= binding.zone_key then
      controller:event(false)
      return
    end
    controller:event({request_id = request, kind = kind})
  end) ~= false)
  return controller
end
X4LAN_SAVED_REPLICA_CONTROL = M
return M
