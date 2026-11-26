local playback = dofile('src/authority/lua/motion_playback.lua')
local buffer = dofile('src/authority/lua/motion_buffer.lua')

local function sample(sequence, game_time, capture_time, position, rate)
 return {version=3, frame='sector_a', sequence=sequence, source_time=game_time,
  capture_time=capture_time, pose={position,0,0}, real_rate=rate}
end
local function near(actual, expected)
 assert(math.abs(actual-expected)<1e-8, tostring(actual)..' differs from '..tostring(expected))
end
local function track(options)
 options = options or {}
 options.dimensions = 3
 options.clock_basis = 'capture_time'
 options.delay = .1
 options.horizon = .2
 options.stale = .75
 options.clock_leak = 0
 return buffer.new(options)
end

-- Game-time speed and stalls do not change capture-clock reconstruction.
for _,game_rate in ipairs({0,.5,1,2}) do
 local motion = track()
 assert(motion:push(sample(1,100,10,0),1))
 assert(motion:push(sample(2,100+game_rate*.2,10.2,10,{50,0,0}),1.2))
 local rendered = assert(motion:render(1.2))
 assert(rendered.phase=='interpolated')
 near(rendered.pose[1],5)
 rendered = assert(motion:render(1.4))
 near(rendered.pose[1],15)
 rendered = assert(motion:render(1.8))
 assert(rendered.phase=='frozen')
 near(rendered.pose[1],20)
end

-- Missing rates hold the measured pose. The .75 second gap uses capture time.
do
 local motion = track()
 assert(motion:push(sample(1,100,10,0),1))
 assert(motion:push(sample(2,100,10.2,10),1.2))
 local rendered = assert(motion:render(1.5))
 assert(rendered.phase=='held-no-rate' and rendered.pose[1]==10)
 local accepted,reason = motion:push(sample(3,100,11,20,{50,0,0}),2)
 assert(accepted and reason=='gap')
 rendered = assert(motion:render(2.5))
 assert(rendered.phase=='held-no-rate' and rendered.pose[1]==20)
 assert(not motion:push(sample(4,99,11.2,20),2.6))
end

-- Version and rate domains cannot be mixed or silently dropped.
for _,change in ipairs({
 function(value) value.version=1 end,
 function(value) value.capture_time=nil end,
 function(value) value.rate={1,0,0} end,
 function(value) value.orientation={} end,
 function(value) value.real_rate={0/0,0,0} end,
}) do
 local motion = track()
 local value = sample(1,100,10,0)
 change(value)
 assert(not motion:push(value,1))
end
for _,capture_time in ipairs({10,9}) do
 local motion = track()
 assert(motion:push(sample(1,100,10,0),1))
 assert(not motion:push(sample(2,100,capture_time,0),1.2))
end
do
 local legacy = buffer.new({dimensions=3})
 assert(not legacy:push(sample(1,100,10,0),1))
 local old = {version=2,frame='sector_a',sequence=1,source_time=100,pose={0,0,0},rate={1,0,0}}
 assert(legacy:push(old,1)) -- Existing optional legacy version fields remain valid.
 assert(not legacy:push({frame='sector_a',sequence=2,source_time=100,pose={0,0,0}},1.2))
end

-- The legacy derivative helper must never interpret capture-domain samples
-- using their game timestamps. Legacy version 1/2 and absent versions remain valid.
do
 local first = sample(1,100,10,0)
 local second = sample(2,100.2,10.2,10)
 assert(buffer.estimate(first,second,.75)==nil)
 for _,field in ipairs({'capture_time','real_rate'}) do
  local old_first = {frame='sector_a',sequence=1,source_time=100,pose={0,0,0}}
  local old_second = {frame='sector_a',sequence=2,source_time=100.2,pose={10,0,0}}
  old_second[field] = field=='capture_time' and 10.2 or {50,0,0}
  assert(buffer.estimate(old_first,old_second,.75)==nil)
 end
 for _,version in ipairs({false,1,2}) do
  local old_first = {frame='sector_a',sequence=1,source_time=100,pose={0,0,0}}
  local old_second = {frame='sector_a',sequence=2,source_time=100.2,pose={10,0,0}}
  if version then old_first.version=version;old_second.version=version end
  near(assert(buffer.estimate(old_first,old_second,.75))[1],50)
 end
end

-- Capture rates retain the wire's inclusive 5000m/s component bound.
-- The separate legacy rate limit remains unchanged.
for _,rate in ipairs({-5000,5000,-5000.001,5000.001}) do
 local motion = track()
 local accepted = motion:push(sample(1,100,10,0,{rate,0,0}),1)
 assert(accepted==(math.abs(rate)<=5000))
end
do
 local legacy = buffer.new({dimensions=3})
 assert(legacy:push({frame='sector_a',sequence=1,source_time=100,
  pose={0,0,0},rate={100000,0,0}},1))
end

local function controller(change)
 local environment = {now=1, game=10, x=0, writes=0, events={}}
 local options = {enabled=true,sample_version=3,buffer=buffer,frame='sector_a',max_age=1,max_step=50,
  buffer_options={delay=.1,horizon=.2,stale=.75,clock_leak=0},
  clock=function() return environment.now end,
  game=function() return environment.game end,
  paused=function() return false end,
  guard=function() return true end,
  read=function() return {x=environment.x,y=0,z=0,yaw=0,pitch=0,roll=0} end,
  write=function(target) environment.x=target.x;environment.writes=environment.writes+1;return true end,
  record=function(event) environment.events[#environment.events+1]=event;return true end,
  fault=function() end}
 if change then change(options) end
 return assert(playback.new(options)),environment
end

for _,game_rate in ipairs({0,.5,1,2}) do
 local motion,environment = controller()
 assert(motion:push(sample(1,100,10,0),1) and motion:tick())
 environment.now=1.15
 assert(motion:tick())
 environment.now=1.2
 assert(motion:push(sample(2,100+game_rate*.2,10.2,10,{50,0,0}),1.2))
 assert(motion:tick())
 near(environment.x,5)
 local observation = environment.events[#environment.events]
 assert(observation.latest_source_time==100+game_rate*.2 and observation.latest_capture_time==10.2)
 environment.now=1.3
 assert(motion:tick())
 local readback = environment.events[#environment.events-1]
 assert(readback.kind=='readback' and readback.latest_capture_time==10.2)
 assert(readback.latest_source_time==100+game_rate*.2)
end

-- Deferred binding uses original arrival, including its one-second expiry.
do
 local motion,environment = controller()
 assert(motion:push(sample(1,100,10,0),1) and motion:tick())
 environment.now=1.15
 assert(motion:tick())
 environment.now=1.2
 assert(motion:push(sample(2,100,10.2,10),1.1) and motion:tick())
 near(environment.x,5.25) -- The existing lag slew follows the earlier arrival gradually.
 environment.now=2.1
 assert(motion:tick() and motion:state()=='stale')
 local accepted,reason = motion:push(sample(3,100,10.3,20),1.1)
 assert(not accepted and reason=='expired')
end

-- Omitting the opt-in retains the legacy clock domain and rejects v3 input.
do
 local motion,environment = controller(function(options) options.sample_version=nil end)
 assert(not motion:push(sample(1,100,10,0),1))
 assert(motion:state()=='failed' and environment.writes==0)
end

-- Existing correction bound is still inclusive at 50m and rejects larger steps.
for _,position in ipairs({50,50.001}) do
 local motion,environment = controller()
 assert(motion:push(sample(1,100,10,position),1))
 local accepted = motion:tick()
 assert(accepted==(position==50))
 assert(environment.writes==(position==50 and 1 or 0))
end
for _,change in ipairs({
 function(options) options.sample_version=false end,
 function(options) options.sample_version=2 end,
 function(options) options.buffer_options.clock_basis='source_time' end,
}) do assert(not pcall(controller,change)) end
for _,change in ipairs({
 function(value) value.source_time=99 end,
 function(value) value.capture_time=10 end,
 function(value) value.rate={1,0,0} end,
 function(value) value.version=1 end,
}) do
 local motion,environment = controller()
 assert(motion:push(sample(1,100,10,0),1))
 environment.now=1.2
 local value = sample(2,100,10.2,1)
 change(value)
 assert(not motion:push(value,1.2) and motion:state()=='failed' and environment.writes==0)
end
print('capture-clock buffer and playback checks passed')

do
 local hull_playback = assert(loadfile('src/authority/lua/company_hull_playback.lua'))()
 assert(hull_playback.MAX_PREDICTION_SECONDS == 5)
 local epoch, generation = string.rep('a', 32), string.rep('b', 32)
 local function trial(change)
  local binding = {slot='cockpit', serial=7, epoch=epoch, generation=generation, index=7354,
   sector_macro='sector_a', actor='0x123456789abcdef', code='WRN-302'}
  local e = {now=2, game=10, visible=true, paused=false, point={x=0,y=0,z=0},
   writes=0, reads=0, records={}, fault=nil}
  local options = {enabled=true, own_index=1970, binding=binding, buffer=buffer,
   clock=function() return e.now end,
   game=function() return e.game end,
   paused=function() return e.paused end,
   guard=function(expected)
    return e.visible and expected.actor==binding.actor and expected.serial==binding.serial
   end,
   read=function(expected)
    assert(expected.index==7354)
    e.reads=e.reads+1
    return e.point
   end,
   write=function(expected, target, before)
    assert(expected.index==7354 and expected.generation==generation and expected.slot=='cockpit')
    assert(before.x==e.point.x and before.y==e.point.y and before.z==e.point.z)
    assert(target.yaw==nil and target.pitch==nil and target.roll==nil)
    if not e.ignore_write then e.point=target end
    e.writes=e.writes+1
    return true
   end,
   record=function(event) e.records[#e.records+1]=event; return true end,
   fault=function(reason, uncertain) e.fault=reason; e.uncertain=uncertain end,
  }
  if change then change(options) end
  return hull_playback.new(options), e, binding
 end
 local function hull_sample(sequence, capture, x, y, z, rate)
  return {version=3, epoch=epoch, generation=generation, index=7354, sector_macro='sector_a',
   sequence=sequence, source_time=42 + sequence / 4, capture_time=capture,
   x=x, y=y, z=z, real_velocity=rate}
 end
 local zero = {x=0,y=0,z=0}
 local rate = {x=50,y=25,z=-10}

 assert(hull_playback.new({enabled=false}) == nil)
 assert(trial(function(o) o.binding.slot='map' end) == nil)
 assert(trial(function(o) o.own_index=7354 end) == nil)
 assert(trial(function(o) o.binding.actor='0x0' end) == nil)

 -- Large unrelated clock origins, bounded XYZ interpolation and delayed, independently read proof.
 do
  local hull, e, binding = trial()
  assert(hull:push(binding, hull_sample(1, 9000, 0, 0, 0, zero), e.now))
  assert(hull:tick() and e.writes==0)
  e.now=2.2
  assert(hull:push(binding, hull_sample(2, 9000.2, 10, 5, -2, rate), e.now))
  assert(hull:tick() and e.writes==1)
  near(e.point.x, 2.5); near(e.point.y, 1.25); near(e.point.z, -.5)
  assert(e.records[#e.records].kind=='observation' and e.reads==2)
  e.now=2.25
  assert(hull:tick() and e.writes==2 and e.reads==3)
  local result=e.records[#e.records-1]
  assert(result.kind=='readback' and result.source_sequence==2 and result.capture_time==9000.2
   and result.source_time==42.5 and result.slot=='cockpit' and result.error==0)
  near(result.actual.x, 2.5); near(result.target.y, 1.25); near(result.elapsed, .05)
  e.now=2.6
  assert(hull:tick() and e.writes==3)
  near(e.point.x, 22.5); near(e.point.y, 11.25); near(e.point.z, -4.5)
  e.now=2.96
  assert(hull:tick() and e.writes==4)
  e.now=7.2
  assert(not hull:tick() and hull:state()=='stale' and e.writes==4)
 end

 -- Local arrival opens the five-second prediction window. Raw source and
 -- capture clocks have unrelated origins and must be returned unchanged.
 do
  local hull,e,binding=trial()
  local velocity={x=10,y=0,z=0}
  assert(hull:push(binding,hull_sample(1,86000,0,0,0,velocity),e.now))
  assert(hull:tick() and e.writes==0)
  e.now=2.76
  assert(hull:tick() and e.writes==1)
  near(e.point.x, 6.1)
  e.now=6.99
  assert(hull:tick() and e.writes==2)
  near(e.point.x, 48.4)
  local seen=e.records[#e.records-1]
  assert(seen.kind=='readback' and seen.capture_time==86000 and seen.source_time==42.25)
  near(seen.actual.x,6.1)
  e.now=7
  assert(not hull:tick() and hull:state()=='stale' and e.writes==2)
  assert(e.records[#e.records].kind=='readback')
  near(e.records[#e.records].actual.x,48.4)
  e.now=7.4
  assert(not hull:push(binding,hull_sample(2,86005.4,54,0,0,velocity),e.now))
  assert(not hull:tick() and e.writes==2)
 end

 -- A consistent fresh sample before expiry extends only the local deadline.
 do
  local hull,e,binding=trial()
  local velocity={x=1,y=0,z=0}
  assert(hull:push(binding,hull_sample(1,90000,0,0,0,velocity),e.now))
  e.now=6.99
  assert(hull:push(binding,hull_sample(2,90005.02,5.02,0,0,velocity),e.now))
  e.now=7.76
  assert(hull:tick() and e.writes==1)
  assert(hull:state()=='active' and e.point.x>5.02)
  e.now=11.99
  assert(not hull:tick() and hull:state()=='stale' and e.writes==1)
 end

 -- Delayed relative delivery can shift the buffer's remote estimate.
 -- A just-arrived position cannot be extrapolated by transit time alone.
 do
  local hull,e,binding=trial()
  local velocity={x=-10,y=0,z=0}
  assert(hull:push(binding,hull_sample(1,90000,0,0,0,velocity),e.now))
  e.now=3.4
  assert(hull:push(binding,hull_sample(2,90000.1,-1,0,0,velocity),e.now))
  assert(hull:tick() and e.writes==1)
  near(e.point.x, -1)
  e.now=4.16
  assert(hull:tick() and e.writes==2)
  near(e.point.x, -8.6)
 end

 -- A long native update gap may make the next correction too large even
 -- though its sample has not expired.
 do
  local hull,e,binding=trial()
  assert(hull:push(binding,hull_sample(1,90000,0,0,0,rate),e.now))
  e.now=6.99
  assert(not hull:tick() and hull:state()=='correction_out_of_bounds' and e.writes==0)
 end

 -- The target can be based on an older buffered sample than the newest arrival.
 do
  local hull,e,binding=trial()
  assert(hull:push(binding,hull_sample(10,9000,3,0,0,zero),e.now))
  e.now=2.02
  assert(hull:push(binding,hull_sample(11,9000.02,4,0,0,zero),e.now))
  assert(hull:tick() and e.writes==1)
  e.now=2.04
  assert(hull:tick() and e.writes==1)
  local readback=e.records[#e.records-1]
  assert(readback.kind=='readback' and readback.source_sequence==10
   and readback.source_time==44.5 and readback.capture_time==9000)
 end

 -- Missing rates cannot move the hull or extend a running track.
 do
  local hull,e,binding=trial()
  local ok,why=hull:push(binding,hull_sample(1,9000,20,0,0),e.now)
  assert(not ok and why=='waiting_for_velocity' and hull:tick() and e.writes==0)
  assert(hull:push(binding,hull_sample(2,9000.1,4,0,0,zero),e.now))
  assert(hull:tick() and e.writes==1)
  e.now=6.9
  assert(hull:push(binding,hull_sample(3,9000.2,5,0,0),e.now))
  assert(hull:tick() and e.writes==1 and e.point.x==4)
  e.now=7
  assert(not hull:tick() and hull:state()=='stale' and e.writes==1)
 end

 for _, case in ipairs({
  {'capture_gap', 2.1, 9060.1, 1},
  {'arrival_gap', 7, 9000.1, 1},
  {'source_discontinuity', 2.1, 9000.1, 51},
 }) do
  local hull,e,binding=trial()
  assert(hull:push(binding,hull_sample(1,9000,0,0,0,zero),e.now))
  e.now=case[2]
  assert(not hull:push(binding,hull_sample(2,case[3],case[4],0,0,zero),e.now))
  assert(hull:state()==case[1] and not hull:tick() and e.writes==0)
 end

 for _, value in ipairs({50,50.001}) do
  local hull,e,binding=trial()
  assert(hull:push(binding,hull_sample(1,9000,value,0,0,zero),e.now))
  assert(hull:tick()==(value==50))
  assert(e.writes==(value==50 and 1 or 0))
 end

 for _, lose in ipairs({'paused','binding_lost','binding_changed','sample_identity_changed',
                        'readback_out_of_bounds','readback_not_later'}) do
  local hull,e,binding=trial()
  e.ignore_write=lose=='readback_out_of_bounds'
  assert(hull:push(binding,hull_sample(1,9000,5,0,0,zero),e.now))
  assert(hull:tick() and e.writes==1)
  if lose~='readback_not_later' then e.now=2.1 end
  if lose=='paused' then e.paused=true
  elseif lose=='binding_lost' then e.visible=false
  elseif lose=='binding_changed' then
   binding.serial=8
   assert(not hull:push(binding,hull_sample(2,9000.1,5,0,0,zero),e.now))
  elseif lose=='sample_identity_changed' then
   local foreign=hull_sample(2,9000.1,5,0,0,zero)
   foreign.generation=string.rep('c',32)
   assert(not hull:push(binding,foreign,e.now))
  end
  assert(not hull:tick() and hull:state()==lose and e.writes==1 and hull:outcome_uncertain())
  assert(#e.records==1 and e.records[1].kind=='observation')
  e.visible=true; e.paused=false
  assert(not hull:tick() and e.writes==1)
 end
end
print('company hull playback checks passed')

-- REMOTE4 follows measured sector attitude through the ordinary cockpit
-- stream, controller and bound native adapter using a deterministic engine.
do
    local dir = 'src/authority/lua/'
    X4LAN_MOTION_BUFFER = dofile(dir .. 'motion_buffer.lua')
    X4LAN_MOTION_PLAYBACK = dofile(dir .. 'motion_playback.lua')
    X4LAN_COMPANY_HULL_PLAYBACK = dofile(dir .. 'company_hull_playback.lua')
    local adapter_module = dofile(dir .. 'company_hull_playback_x4.lua')
    local cockpit_module = dofile(dir .. 'company_cockpit_playback.lua')
    local stream_module = dofile(dir .. 'company_observer_stream.lua')
    local epoch, generation = ('a'):rep(32), ('b'):rep(32)
    local now, queue, sent, writes, readings = 10, {}, {}, {}, {}
    local actual = {x=50, y=0, z=0, yaw=0, pitch=0, roll=0}
    local observed = {epoch=epoch, index=2, actor='0x22', sector_macro='sector', code='NPC-123'}
    local reader = {
        close=function() end,
        tick=function() return observed end,
        matches=function(_, e, sector, index, actor, code)
            return e == epoch and sector == 'sector' and index == 2 and actor == '0x22' and code == 'NPC-123'
        end,
    }
    local adapter = adapter_module.new({
        enabled=true, pinned={epoch=epoch, issued_count=2}, own_index=1, observer=reader,
        guard=function() return true end,
    }, {
        cockpit=function() return {actor=1, sector=4, zone=5} end,
        target=function(actor)
            assert(actor == '0x22')
            return {actor=2, sector=4, zone=5, sector_macro='sector', code='NPC-123'}
        end,
        read=function(actor) assert(actor == 2); return actual end,
        destination_zone=function(sector) assert(sector == 4); return 5 end,
        write=function(actor, sector, pose)
            assert(actor == 2 and sector == 4 and pose.x == 50 and pose.y == 0 and pose.z == 0)
            actual = pose
            writes[#writes + 1] = pose
            return true
        end,
    })
    local cockpit = cockpit_module.new({
        enabled=true, own_index=1, adapter=adapter,
        clock=function() return now end, game=function() return now end,
        paused=function() return false end,
        fault=function(r) error(r) end,
        record=function(row) readings[#readings + 1] = row; return true end,
    })
    local stream = stream_module.new({cockpit=reader, map=reader}, epoch, {
        receive=function() return table.remove(queue, 1) end,
        send=function(_, packet) sent[#sent + 1] = packet; return true end,
    }, 'related|', function() return now end, cockpit)

    local function packet(kind, slot, sequence, speed, capture, angles, timed)
        local fields = {'related', kind, slot, '1', epoch, '2', generation, tostring(sequence),
                        'sector', '50', '0', '0', speed, speed, speed, tostring(capture), tostring(capture)}
        if timed then fields[#fields + 1] = tostring(now) end
        for _, value in ipairs(angles or {}) do fields[#fields + 1] = tostring(value) end
        queue[#queue + 1] = table.concat(fields, '|')
    end
    packet('REMOTE4', 'cockpit', 1, '-', 10, {0, .3, -.4}, true)
    stream:tick(true)
    assert(#writes == 0 and sent[#sent]:match('related|SEEN4|cockpit|.*|0|0%.3|%-0%.4$'))
    now = 10.2
    packet('REMOTE4', 'cockpit', 2, '0', 10.2, {math.rad(170), 0, 0}, true)
    stream:tick(true)
    assert(#writes == 1 and math.abs(actual.yaw - math.rad(170)) < 1e-12)
    now = 10.4
    packet('REMOTE4', 'cockpit', 3, '0', 10.4, {math.rad(-170), 0, 0}, true)
    stream:tick(true)
    assert(#writes == 2 and math.deg(actual.yaw) > 170 and math.deg(actual.yaw) < 180)
    assert(readings[#readings - 1].actual_attitude and readings[#readings - 1].target_attitude)
    now = 10.45
    stream:tick(true)
    assert(math.abs(math.abs(actual.yaw) - math.pi) < 1e-12)
    now = 10.6
    stream:tick(true)
    assert(math.abs(actual.yaw - math.rad(-170)) < 1e-12)
    local held_count = #writes
    now = 11
    packet('REMOTE4', 'map', 4, '0', 11, {0, 0, 0}, false)
    packet('REMOTE4', 'cockpit', 4, '0', 11, {'nan', 0, 0}, true)
    stream:tick(true)
    assert(#writes == held_count)
    assert(sent[#sent]:match('related|SEEN4|map|.*|0|0|0$'))
    now = 15.4
    stream:tick(true)
    assert(cockpit:stats().stale_cutoffs == 1 and #writes == held_count)
    now = 16
    packet('REMOTE', 'cockpit', 4, '0', 16, nil, true)
    stream:tick(true)
    assert(#writes == held_count, 'same lifetime must not switch to a different pose contract')
    stream:close()
end

do
    local buffer = dofile('src/authority/lua/motion_buffer.lua')
    -- Independently compute the validated X4 sector basis columns from an
    -- Euler sample and measure relative rotation from the basis dot products.
    local function basis(a)
        local cy, cp, cr = math.cos(a.yaw), math.cos(a.pitch), math.cos(a.roll)
        local sy, sp, sr = math.sin(a.yaw), math.sin(a.pitch), math.sin(a.roll)
        return {cr*cy+sr*sp*sy, -sr*cp, -cr*sy+sr*sp*cy,
                sr*cy-cr*sp*sy, cr*cp, -sr*sy-cr*sp*cy, cp*sy, sp, cp*cy}
    end
    local function separation(a, b)
        local A, B, trace = basis(a), basis(b), 0
        for axis = 1, 9 do trace = trace + A[axis]*B[axis] end
        return math.acos(math.max(-1, math.min(1, (trace - 1) / 2)))
    end
    local function mid(a, b)
        local track = buffer.new({clock_basis='capture_time', dimensions=3, delay=.25})
        for i, value in ipairs({a, b}) do
            assert(track:push({version=4, frame='sector', sequence=i, source_time=i,
                               capture_time=i/2, pose={0,0,0}, attitude=value}, i/2))
        end
        return assert(track:render(1)).attitude
    end
    local a = {yaw=.37, pitch=.8, roll=-.53}
    local b = {yaw=-1.24, pitch=.18, roll=.9}
    local between = mid(a, b)
    assert(math.abs(separation(a, between) - separation(a, b)/2) < 1e-12)
    assert(math.abs(separation(b, between) - separation(a, b)/2) < 1e-12)
    for _, pole in ipairs({-1, 1}) do
        local pitched = {yaw=.6, pitch=pole*math.pi/2, roll=.4}
        local equivalent = {yaw=.6 - pole*.4, pitch=pitched.pitch, roll=0}
        local vertical = mid(pitched, equivalent)
        assert(separation(pitched, vertical) < 1e-6)
        assert(not buffer.attitude_changed(pitched, equivalent))
    end
end
