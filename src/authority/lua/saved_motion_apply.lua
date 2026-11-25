-- Disposable, explicitly enabled one-shot diagnostic. No clearance claim.
-- All actor/sector/zone identities remain local to this process.
local M={}
-- Isolated test fixtures, not a general ship-selection API.
local DEFAULT_FIXTURE={index=1318,sector='cluster_115_sector001_macro',code='WRG-830',
 macro='ship_ter_m_corvette_01_a_macro',owner='pioneers'}
local RECOVERY_FIXTURE={name='recovery_enh_870',epoch='4ba7be438ebb04ec5bb85fdb95b5a488',
 index=1318,sector='cluster_06_sector001_macro',code='ENH-870',
 macro='ship_arg_m_trans_container_02_b_macro',owner='antigone'}
-- MFQ is reserved for explicitly aligned playback. It is not a one-shot fixture.
local MFQ_PLAYBACK_FIXTURE={name='recovery_mfq_419',epoch='26a1a365c66788aa06f0aeb9e18ff62e',
 index=943,sector=RECOVERY_FIXTURE.sector,code='MFQ-419',
 macro=RECOVERY_FIXTURE.macro,owner=RECOVERY_FIXTURE.owner}
local function fixture(name)
 if name==nil then return DEFAULT_FIXTURE end
 if name==RECOVERY_FIXTURE.name then return RECOVERY_FIXTURE end
 error('unknown saved motion fixture')
end
local function finite(x)return type(x)=='number' and x==x and x~=math.huge and x~=-math.huge end
local function token(v,n)return type(v)=='string' and #v==n and v:match('^[0-9a-f]+$')~=nil end
local function natural(v)return type(v)=='string' and v:match('^[1-9]%d*$') and #v<=20 end
local function pose(p)
 if type(p)~='table' then return false end
 for _,k in ipairs({'x','y','z','yaw','pitch','roll'})do if not finite(p[k])then return false end end
 return true
end
local function distance(a,b)return math.sqrt((a.x-b.x)^2+(a.y-b.y)^2+(a.z-b.z)^2)end
function M.new(options)
 if type(options)~='table' or options.enabled~=true then return nil,'disabled' end
 local configuration={};for key,value in pairs(options)do configuration[key]=value end
 local selected_fixture=fixture(configuration.fixture)
 for _,callback_name in ipairs({'clock','game','guard','issue','inspect','write','log','fault'})do
  assert(type(configuration[callback_name])=='function',callback_name)
 end
 assert(finite(configuration.timeout) and configuration.timeout>1 and configuration.timeout<=30)
 assert(configuration.request_nonce==nil or token(configuration.request_nonce,32))
 assert(configuration.readonly==nil or configuration.readonly==true)
 assert(configuration.immediate_readback==nil or configuration.immediate_readback==true)
 -- Separate experimental observation, never the delayed one-shot receipt.
 assert(configuration.observe_immediate_only==nil or
  (configuration.observe_immediate_only==true and configuration.immediate_readback==true and not configuration.readonly))
 local state,callback_busy,already_used='idle',false,false
 local last_clock_time,deadline,pending_request,write_was_entered=nil,nil,nil,false
 local api={}
 local function copy_request_snapshot()
  return {request=pending_request.request,epoch=pending_request.epoch,generation=pending_request.generation,
   digest=pending_request.digest,index=pending_request.index,sequence=pending_request.sequence,
   sector_macro=pending_request.sector_macro,actor_key=pending_request.actor_key,
   sector_key=pending_request.sector_key,zone_key=pending_request.zone_key,
   target={x=pending_request.target.x,y=pending_request.target.y,z=pending_request.target.z}}
 end
 local function fail(reason)
  if state~='failed' then state='failed';pcall(configuration.fault,reason,write_was_entered)end
  return false
 end
 local function check()
  if state=='failed' then return false end
  local previous_state=state
  local clock_read_ok,current_time=pcall(configuration.clock)
  if not clock_read_ok or state~=previous_state or not finite(current_time) or current_time<0 or
     (last_clock_time and current_time<last_clock_time) or (deadline and current_time>=deadline)then return false end
  last_clock_time=current_time
  local guard_ok,guard_result=pcall(configuration.guard)
  if not guard_ok or guard_result~=true or state~=previous_state then return false end
  local second_clock_read_ok,second_clock_time=pcall(configuration.clock)
  if not second_clock_read_ok or state~=previous_state or not finite(second_clock_time) or
     second_clock_time<current_time or (deadline and second_clock_time>=deadline)then return false end
  last_clock_time=second_clock_time
  return true
 end
 local function report(line)
  local previous_state=state
  local callback_ok,callback_result=pcall(configuration.log,line)
  return callback_ok and callback_result==true and state==previous_state and check()
 end
 local function dispatch_request(phase)
  local request_id=(configuration.request_nonce and configuration.request_nonce..'-' or '')..
   pending_request.request..'-'..phase
  pending_request.id=request_id
  local previous_state=state
  local callback_ok,callback_result=pcall(configuration.issue,request_id,copy_request_snapshot())
  return callback_ok and callback_result==true and state==previous_state and check()
 end
 function api:begin(request)
  if callback_busy or state~='idle' or already_used then return fail('begin')end
  already_used=true
  if type(request)~='table' or not token(request.request,32) or not token(request.epoch,32) or
     not token(request.generation,32) or not token(request.digest,64) or request.index~=selected_fixture.index or
     (selected_fixture.epoch and request.epoch~=selected_fixture.epoch) or
     type(request.sequence)~='number' or request.sequence<1 or request.sequence%1~=0 or
     request.sector_macro~=selected_fixture.sector or not natural(request.actor_key) or
     not natural(request.sector_key) or not natural(request.zone_key) or type(request.target)~='table' or
     not finite(request.target.x) or not finite(request.target.y) or not finite(request.target.z) then
   return fail('identity')
  end
  pending_request={request=request.request,epoch=request.epoch,generation=request.generation,
   digest=request.digest,index=request.index,sequence=request.sequence,sector_macro=request.sector_macro,
   actor_key=request.actor_key,sector_key=request.sector_key,zone_key=request.zone_key,
   target={x=request.target.x,y=request.target.y,z=request.target.z}}
  local stage='begin_guard'
  callback_busy=true
  local callback_ok=pcall(function()
   assert(check())
   deadline=last_clock_time+configuration.timeout
   state='pre'
   stage='dispatch_guard'
   assert(check())
   stage='dispatch_issue'
   assert(dispatch_request('pre'))
  end)
  callback_busy=false
  if not callback_ok then return fail(stage)end
  return true
 end
 function api:event(response)
  if callback_busy or (state~='pre' and state~='post') then return fail('event')end
  local stage=state=='pre' and 'pre_guard' or 'post_guard'
  callback_busy=true
  local callback_ok=pcall(function()
   assert(check())
   stage=state=='pre' and 'pre_response' or 'post_response'
   assert(response==pending_request.id..'|ok')
   local before_state=state
   stage=state=='pre' and 'pre_inspect' or 'post_inspect'
   local inspection_ok,actual_pose=pcall(configuration.inspect,copy_request_snapshot())
   assert(inspection_ok and pose(actual_pose) and state==before_state and check())
   stage=state=='pre' and 'pre_game' or 'post_game'
   local game_read_ok,current_game_time=pcall(configuration.game)
   assert(game_read_ok and finite(current_game_time) and current_game_time>=0 and state==before_state and check())
   if state=='pre' then
    stage='pre_distance'
    local target_distance=distance(actual_pose,pending_request.target)
    assert(finite(target_distance))
    if configuration.readonly then
     local preflight_result=target_distance<5 and 'NEAR' or
      (target_distance<=50 and 'READY' or 'FAR')
     stage='preflight_log'
     assert(report(('PREFLIGHT %s %d %d %s %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %s'):format(
      pending_request.request,pending_request.index,pending_request.sequence,pending_request.digest,
      actual_pose.x,actual_pose.y,actual_pose.z,pending_request.target.x,pending_request.target.y,
      pending_request.target.z,current_game_time,target_distance,preflight_result)))
     stage='pre_distance'
     assert(preflight_result~='FAR')
     state=preflight_result=='NEAR' and 'near' or 'ready'
     deadline=nil
     return
    end
    assert(target_distance>=5 and target_distance<=50)
    stage='pre_intent'
    assert(report(('INTENT %s %d %d %s %.17g %.17g %.17g %.17g %.17g %.17g %.17g'):format(
     pending_request.request,pending_request.index,pending_request.sequence,pending_request.digest,
     actual_pose.x,actual_pose.y,actual_pose.z,pending_request.target.x,pending_request.target.y,
     pending_request.target.z,current_game_time)))
    stage='pre_write_marker'
    assert(report('WRITE_ENTERED '..pending_request.request))
    stage='write'
    write_was_entered=true -- exceptions here can follow a partial effect; never retry.
    local target_pose={x=pending_request.target.x,y=pending_request.target.y,z=pending_request.target.z,
     yaw=actual_pose.yaw*180/math.pi,pitch=actual_pose.pitch*180/math.pi,roll=actual_pose.roll*180/math.pi}
    local write_ok,write_result=pcall(configuration.write,copy_request_snapshot(),target_pose)
    assert(write_ok and write_result==true and state=='pre' and check())
    if configuration.immediate_readback then
     -- Point-in-time observation only: the engine may defer the write or
     -- immediately move the actor. Never treat this as the later readback.
     stage='immediate_inspect'
     local immediate_inspection_ok,immediate_pose=pcall(configuration.inspect,copy_request_snapshot())
     assert(immediate_inspection_ok and pose(immediate_pose) and state=='pre' and check())
     stage='immediate_game'
     local immediate_game_read_ok,immediate_game_time=pcall(configuration.game)
     assert(immediate_game_read_ok and finite(immediate_game_time) and immediate_game_time>=current_game_time and
      state=='pre' and check())
     stage='immediate_log'
     assert(report(('IMMEDIATE %s %d %d %s %.17g %.17g %.17g %.17g'):format(
      pending_request.request,pending_request.index,pending_request.sequence,pending_request.digest,
      immediate_pose.x,immediate_pose.y,immediate_pose.z,immediate_game_time)))
     if configuration.observe_immediate_only then state='observed';deadline=nil;return end
    end
    pending_request.write_time=last_clock_time
    pending_request.game=current_game_time
    state='settling'
   else
    if configuration.immediate_readback then
     stage='post_observation'
     local target_distance=distance(actual_pose,pending_request.target)
     local elapsed_since_write=last_clock_time-pending_request.write_time
     assert(finite(target_distance) and finite(elapsed_since_write))
     assert(report(('POST_OBSERVED %s %d %d %s %.17g %.17g %.17g %.17g %.17g %.17g %.17g'):format(
      pending_request.request,pending_request.index,pending_request.sequence,pending_request.digest,
      actual_pose.x,actual_pose.y,actual_pose.z,current_game_time,pending_request.game,
      elapsed_since_write,target_distance)))
    end
    stage='post_readback'
    assert(current_game_time>pending_request.game and last_clock_time-pending_request.write_time>=1 and
     distance(actual_pose,pending_request.target)<=3)
    stage='post_log'
    assert(report(('READBACK %s %d %d %s %.17g %.17g %.17g %.17g %.17g'):format(
     pending_request.request,pending_request.index,pending_request.sequence,pending_request.digest,
     actual_pose.x,actual_pose.y,actual_pose.z,current_game_time,last_clock_time-pending_request.write_time)))
    state='done'
    deadline=nil
   end
  end)
  callback_busy=false
  if not callback_ok then return fail(stage)end
  return true
 end
 function api:tick()
  if callback_busy or state=='idle' or state=='failed' then return fail('tick')end
  local stage='tick_guard'
  callback_busy=true
  local callback_ok=pcall(function()
   assert(check())
   if state=='settling' and last_clock_time-pending_request.write_time>=1 then
    state='post'
    stage='post_dispatch'
    assert(dispatch_request('post'))
   end
  end)
  callback_busy=false
  if not callback_ok then return fail(stage)end
  return true
 end
 function api:state()return state end
 return api
end

local function native_callbacks(o, selected_fixture, read_only)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 local selected=selected_fixture or fixture(o.fixture)
 local c={};for k,v in pairs(o)do c[k]=v end
 assert(type(X4LAN_REPLICA_MOTION_BINDING)=='table' and
  type(X4LAN_REPLICA_MOTION_BINDING.normalize_profile_save_path)=='function')
 local normalize_save_path=X4LAN_REPLICA_MOTION_BINDING.normalize_profile_save_path
 c.expected_save_path=assert(normalize_save_path(c.expected_save_path),
  'invalid profile-45841 save path')
 assert(c.test_profile=='45841')
 assert(type(o.resolve)=='table' and o.resolve==X4LAN_RESOLVE_STRING and type(o.guard)=='function')
 local ffi=require('ffi')
 if not pcall(ffi.typeof,'UniverseID')then ffi.cdef('typedef uint64_t UniverseID;')end
 if not pcall(ffi.typeof,'UIPosRot')then ffi.cdef('typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;')end
 ffi.cdef[[const char* GetSaveFolderPath(void);bool IsPlayerValid(void);bool IsGamePaused(void);
  bool IsValidComponent(UniverseID);bool IsComponentOperational(UniverseID);bool IsComponentClass(UniverseID,const char*);
  UniverseID GetContextByClass(UniverseID,const char*,bool);UniverseID GetPlayerControlledShipID(void);
  UniverseID GetPlayerOccupiedShipID(void);UniverseID GetPlayerContainerID(void);const char* GetObjectIDCode(UniverseID);
  UIPosRot GetObjectPositionInSector(UniverseID);void SetObjectSectorPos(UniverseID,UniverseID,UIPosRot);double GetCurrentGameTime(void);]]
 local C=ffi.C
 local native_stage='initial'
 c.failure_stage=function()return native_stage end
 local function key(v)local n=c.resolve.actor_u64(v);return n and tostring(n):gsub('ULL$','') or nil end
 local function ref(text)
  assert(natural(text));local x=ConvertStringToLuaID(text);local kind=type(x)
  if kind=='number' or kind=='cdata' then assert(key(x)==text)else assert(kind=='userdata')end
  local id=o.resolve.actor_u64(ConvertIDTo64Bit(x));assert(id and key(id)==text)
  return x,id
 end
 local external=o.guard
 c.guard=function()
  native_stage='profile'
  local p=C.GetSaveFolderPath()
  if p==nil or p==ffi.NULL or normalize_save_path(ffi.string(p))~=c.expected_save_path or
     not C.IsPlayerValid() or C.IsGamePaused() or external()~=true then return false end
  native_stage='profile_recheck'
  local q=C.GetSaveFolderPath()
  return q~=nil and q~=ffi.NULL and normalize_save_path(ffi.string(q))==c.expected_save_path and C.IsPlayerValid() and not C.IsGamePaused()
 end
 c.clock=GetCurRealTime;c.game=function()return tonumber(C.GetCurrentGameTime())end
 local function ids(p)
  local ar,a=ref(p.actor_key);local sr,s=ref(p.sector_key);local zr,z=ref(p.zone_key)
  return ar,a,sr,s,zr,z
 end
 local function live(p)
  native_stage='references'
  local ar,a,sr,s,zr,z=ids(p)
  assert(c.guard())
  native_stage='actor'
  assert(C.IsValidComponent(a) and C.IsComponentOperational(a) and C.IsComponentClass(a,'ship'))
  native_stage='sector'
  assert(C.IsValidComponent(s) and C.IsComponentClass(s,'sector') and C.GetContextByClass(a,'sector',false)==s)
  native_stage='zone'
  assert(C.IsValidComponent(z) and C.IsComponentClass(z,'zone') and C.GetContextByClass(a,'zone',false)==z)
  native_stage='player'
  local controlled,occupied,container=C.GetPlayerControlledShipID(),C.GetPlayerOccupiedShipID(),C.GetPlayerContainerID()
  assert(controlled~=a and occupied~=a and container~=a and container~=0 and C.IsValidComponent(container))
  native_stage='player_container'
  local own=C.GetContextByClass(container,'ship',true)
  if own~=0 then assert(own~=a)else assert(C.GetContextByClass(container,'station',true)~=0)end
  native_stage='docking'
  local docked,docking,stored=GetComponentData(ar,'isdocked','isdocking','isinternallystored')
  assert(docked==false and docking==false and stored==false)
  native_stage='fixture'
  local macro,owner=GetComponentData(ar,'macro','owner');assert(macro==selected.macro and owner==selected.owner)
  native_stage='fixture_sector'
  local sector_macro=GetComponentData(sr,'macro');assert(sector_macro==p.sector_macro)
  native_stage='fixture_code'
  local code=C.GetObjectIDCode(a);assert(code~=nil and code~=ffi.NULL and ffi.string(code)==selected.code)
  assert(c.guard());native_stage='validated';return a,s
 end
 c.inspect=function(p)
  local a=live(p);native_stage='position_read';local x=C.GetObjectPositionInSector(a);assert(live(p)==a)
  return {x=tonumber(x.x),y=tonumber(x.y),z=tonumber(x.z),yaw=tonumber(x.yaw),pitch=tonumber(x.pitch),roll=tonumber(x.roll)}
 end
 if read_only then
  c.check_binding=function(p)
   local ok=pcall(live,p)
   return ok
  end
  return c
 end
 assert(o.md_issue==nil or type(o.md_issue)=='function')
 local md_issue=o.md_issue or AddUITriggeredEvent
 c.issue=function(id,p)
  local ar,a,sr,s,zr,z=ids(p);assert(a and s and z)
  local args={request_id=id,epoch=p.epoch,index=p.index,actor=ar,sector=sr,zone=zr}
  if selected.name then args.fixture=selected.name end
  local accepted = md_issue('X4AuthoritySavedMotionApply','Inspect',args)
  if o.md_issue then return accepted == true end
  return accepted ~= false
 end
 c.write=function(p,t)
  local a,s=live(p);native_stage='setter';C.SetObjectSectorPos(a,s,ffi.new('UIPosRot',t));assert(live(p)==a);return true
 end
 return c
end

function M.new_x4(options)
 local callbacks, reason = native_callbacks(options)
 if not callbacks then return nil, reason end
 return M.new(callbacks)
end

-- One explicitly selected fixture placement before sustained playback exists.
-- Three metres is the existing bounded native playback endpoint proof tolerance.
-- This exception never changes the later playback correction limit (50 metres).
local INITIAL_ALIGNMENT_READBACK_TOLERANCE = 3
local function align_initial_fixture(options, callbacks, binding, selected)
 local mutation_requested = false
 local alignment_state = options.initial_alignment_state
 local ok = pcall(function()
  assert(type(alignment_state) == 'table' and alignment_state.used == false and alignment_state.uncertain == false)
  alignment_state.used = true
  assert((selected == RECOVERY_FIXTURE or selected == MFQ_PLAYBACK_FIXTURE) and
   options.initial_alignment_fixture == selected.name and type(options.prepared) == 'function')
  assert(binding.epoch == selected.epoch and binding.index == selected.index)
  local sample = options.initial_sample
  assert(type(sample) == 'table' and getmetatable(sample) == nil and type(sample.snapshot) == 'table')
  local snapshot, arrival = sample.snapshot, sample.arrival
  assert(getmetatable(snapshot) == nil)
  local allowed_fields = {frame=true, sequence=true, source_time=true, pose=true}
  if options.sample_version == 3 then
   allowed_fields.version = true
   allowed_fields.capture_time = true
   allowed_fields.real_rate = true
  else
   allowed_fields.version = true
   allowed_fields.rate = true
  end
  for key in pairs(sample) do assert(key == 'snapshot' or key == 'arrival') end
  for key in pairs(snapshot) do assert(allowed_fields[key]) end
  assert(snapshot.frame == binding.sector_macro and (finite(snapshot.sequence) and snapshot.sequence >= 1 and snapshot.sequence <= 9007199254740991 and snapshot.sequence % 1 == 0) and
   finite(snapshot.source_time) and snapshot.source_time >= 0 and snapshot.source_time <= 1e12)
  local function copy_vector(vector, limit)
   assert(type(vector) == 'table' and getmetatable(vector) == nil and #vector == 3)
   for key in pairs(vector) do assert(key == 1 or key == 2 or key == 3) end
   local result = {}
   for index = 1, 3 do
    assert(finite(vector[index]) and math.abs(vector[index]) <= limit)
    result[index] = vector[index]
   end
   return result
  end
  local pose_reference = snapshot.pose
  local original_pose = copy_vector(pose_reference, 10000000)
  local rate_reference = snapshot.rate
  if options.sample_version == 3 then rate_reference = snapshot.real_rate end
  local original_rate
  if rate_reference ~= nil then original_rate = copy_vector(rate_reference, 5000) end
  local original = {version=snapshot.version, frame=snapshot.frame, sequence=snapshot.sequence,
   source_time=snapshot.source_time, capture_time=snapshot.capture_time}
  if options.sample_version == 3 then
   assert(snapshot.version == 3 and finite(snapshot.capture_time) and
    snapshot.capture_time >= 0 and snapshot.capture_time <= 1e12 and snapshot.rate == nil)
  else
   assert((snapshot.version == nil or snapshot.version == 1) and
    snapshot.capture_time == nil and snapshot.real_rate == nil)
  end
  local function vector_unchanged(vector, expected)
   if expected == nil then return vector == nil end
   if type(vector) ~= 'table' or getmetatable(vector) ~= nil or #vector ~= 3 then return false end
   for key in pairs(vector) do if key ~= 1 and key ~= 2 and key ~= 3 then return false end end
   for index = 1, 3 do if vector[index] ~= expected[index] then return false end end
   return true
  end
  -- Callbacks share these objects with the owner. Detect replacement as well
  -- as in-place changes before any setter, and retain original record metadata.
  local function snapshot_unchanged()
   if options.initial_sample ~= sample or getmetatable(sample) ~= nil or
      sample.snapshot ~= snapshot or sample.arrival ~= arrival or getmetatable(snapshot) ~= nil then return false end
   for key in pairs(sample) do if key ~= 'snapshot' and key ~= 'arrival' then return false end end
   for key in pairs(snapshot) do if not allowed_fields[key] then return false end end
   for _, key in ipairs({'version', 'frame', 'sequence', 'source_time', 'capture_time'}) do
    if snapshot[key] ~= original[key] then return false end
   end
   local current_rate = snapshot.rate
   if options.sample_version == 3 then current_rate = snapshot.real_rate end
   return snapshot.pose == pose_reference and current_rate == rate_reference and
    vector_unchanged(snapshot.pose, original_pose) and vector_unchanged(current_rate, original_rate)
  end
  local target = {x=original_pose[1], y=original_pose[2], z=original_pose[3]}
  assert(finite(options.max_age) and options.max_age > 0 and options.max_age <= 30)
  local function check_fresh_prepared()
   assert(snapshot_unchanged())
   local now = callbacks.clock()
   assert(snapshot_unchanged() and finite(now) and finite(arrival) and arrival >= 0 and arrival <= now and
    now - arrival < options.max_age)
   assert(callbacks.guard() == true and snapshot_unchanged())
   assert(options.prepared() == true and snapshot_unchanged())
   return now
  end
  check_fresh_prepared()
  local actual = callbacks.inspect(binding)
  for _, axis in ipairs({'x', 'y', 'z'}) do assert(finite(actual[axis])) end
  for _, angle in ipairs({'yaw', 'pitch', 'roll'}) do assert(finite(actual[angle])) end
  check_fresh_prepared()
  -- Mark before calling the setter: a throwing or reentrant request may have written.
  mutation_requested = true
  alignment_state.uncertain = true
  assert(callbacks.write(binding, {x=target.x, y=target.y, z=target.z,
   yaw=actual.yaw*180/math.pi, pitch=actual.pitch*180/math.pi, roll=actual.roll*180/math.pi}) == true)
  check_fresh_prepared()
  local readback = callbacks.inspect(binding)
  for _, axis in ipairs({'x', 'y', 'z'}) do assert(finite(readback[axis])) end
  local dx, dy, dz = readback.x-target.x, readback.y-target.y, readback.z-target.z
  local error_metres = math.sqrt(dx*dx + dy*dy + dz*dz)
  assert(error_metres <= INITIAL_ALIGNMENT_READBACK_TOLERANCE)
  local verified_at = check_fresh_prepared()
  assert(options.record({kind='initial_alignment', index=binding.index, epoch=binding.epoch,
   sector_macro=binding.sector_macro, source_sequence=original.sequence,
   source_time=original.source_time, capture_time=original.capture_time,
   arrival=arrival, local_time=verified_at,
   before={x=actual.x, y=actual.y, z=actual.z},
   actual={x=readback.x, y=readback.y, z=readback.z}, target=target,
   error=error_metres, tolerance=INITIAL_ALIGNMENT_READBACK_TOLERANCE}) == true)
  check_fresh_prepared()
  alignment_state.uncertain = false
 end)
 if not ok then
  pcall(options.fault, 'initial_alignment_failed', mutation_requested,
   'initial_alignment', callbacks.failure_stage())
  return false
 end
 return true
end

local function copy_binding(value, selected)
 assert(type(value) == 'table')
 local binding = {epoch=value.epoch, index=value.index, sector_macro=value.sector_macro,
  actor_key=value.actor_key, sector_key=value.sector_key, zone_key=value.zone_key}
 assert(token(binding.epoch,32) and binding.index == selected.index and binding.sector_macro == selected.sector and
  (not selected.epoch or binding.epoch == selected.epoch) and natural(binding.actor_key) and
  natural(binding.sector_key) and natural(binding.zone_key))
 return binding
end

-- A hold controller can inspect a local recovery binding before preparing it.
function M.new_inspector_x4(options)
 if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
 local selected = options.fixture == MFQ_PLAYBACK_FIXTURE.name and MFQ_PLAYBACK_FIXTURE
  or fixture(options.fixture)
 assert(selected == RECOVERY_FIXTURE or selected == MFQ_PLAYBACK_FIXTURE,
  'an explicit recovery fixture is required')
 local binding = copy_binding(options.binding, selected)
 local callbacks = assert(native_callbacks(options, selected, true))
 return {
  read=function() return callbacks.inspect(binding) end,
  guard=function() return callbacks.check_binding(binding) end,
  game=callbacks.game,
 }
end

-- Separate sustained-playback factory. The one-shot state machine above is unchanged.
function M.new_playback_x4(options)
 if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
 local configuration = {}
 for key, value in pairs(options) do configuration[key] = value end
 options = configuration
 local sample_version = options.sample_version or 1
 assert(sample_version == 1 or sample_version == 3, 'unsupported playback sample version')
 options.sample_version = sample_version
 assert(type(options.playback) == 'table' and type(options.playback.new) == 'function')
 assert(finite(options.max_step) and options.max_step > 0 and options.max_step <= 50)
 local selected
 if options.fixture == MFQ_PLAYBACK_FIXTURE.name then
  -- The caller must supply the confirmed local binding and enforce the required
  -- hold and critical-section checks through prepared() and guard().
  assert(options.initial_alignment_fixture == MFQ_PLAYBACK_FIXTURE.name,
   'MFQ playback requires explicit initial alignment')
  selected = MFQ_PLAYBACK_FIXTURE
 else
  selected = fixture(options.fixture)
 end
 local binding = copy_binding(options.binding, selected)
 local callbacks = assert(native_callbacks(options, selected))
 if options.initial_alignment_fixture ~= nil then
  if not align_initial_fixture(options, callbacks, binding, selected) then return nil, 'initial_alignment_failed' end
 end
 local ffi = require('ffi')
 return options.playback.new({enabled=true, buffer=options.buffer, frame=binding.sector_macro,
  sample_version=sample_version,
  clock=callbacks.clock, game=callbacks.game, paused=function() return ffi.C.IsGamePaused() end,
  guard=callbacks.guard, max_age=options.max_age, max_step=options.max_step,
  buffer_options=options.buffer_options,
  fault=function(reason,uncertain,stage)
   -- Capture before the observer can stop/reenter or alter callback state.
   local native_stage=(stage=='read' or stage=='read_pose' or stage=='write' or stage=='guard') and callbacks.failure_stage() or nil
   return options.fault(reason,uncertain,stage,native_stage)
  end,
  record=function(event)
   event.index=binding.index
   event.epoch=binding.epoch
   event.sector_macro=binding.sector_macro
   return options.record(event)
  end,
  read=function() return callbacks.inspect(binding) end,
  write=function(target)
   -- Read actual orientation immediately before each XYZ-only native write.
   local actual = callbacks.inspect(binding)
   return callbacks.write(binding, {x=target.x, y=target.y, z=target.z,
    yaw=actual.yaw*180/math.pi, pitch=actual.pitch*180/math.pi, roll=actual.roll*180/math.pi})
  end})
end
X4LAN_SAVED_MOTION_APPLY=M
return M
