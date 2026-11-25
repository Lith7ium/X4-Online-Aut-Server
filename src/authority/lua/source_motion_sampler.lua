-- Default-off source motion sampler. The caller owns the sole X4 update hook,
-- MD event router and transport; this module neither registers nor writes X4.
local M={}
local MAX=9007199254740991
local function token(v)return type(v)=='string' and #v==32 and v:match('^[0-9a-f]+$')~=nil end
local function integer(v)return type(v)=='number' and v==v and v>=1 and v<=MAX and v%1==0 end
local function finite(v)return type(v)=='number' and v==v and v~=math.huge and v~=-math.huge end
local function name(v)return type(v)=='string' and #v>=1 and #v<=128 and v:match('^[A-Za-z0-9_]+$')~=nil end
local function copy_orientation(value)
 if not value then return nil end
 return {yaw=value.yaw,pitch=value.pitch,roll=value.roll}
end
local function valid_row(r,epoch,baseline)
 if type(r)~='table' or getmetatable(r)~=nil or r.epoch~=epoch or not integer(r.index) then return false end
 if r.classification=='new_registry_entry' then return integer(r.sequence) end
 return r.classification=='saved_baseline' and baseline~=nil and r.index<=baseline and r.sequence==nil
end
function M.new(o)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 local c={};for k,v in pairs(o)do c[k]=v end
 assert(token(c.epoch) and token(c.generation) and token(c.nonce))
 assert(c.epoch~=c.generation and c.epoch~=c.nonce and c.generation~=c.nonce)
 for _,k in ipairs({'clock','guard','binding_guard','actor_key','reader_factory','fault'})do assert(type(c[k])=='function',k..' required')end
 assert((c.saved_baseline_limit==nil and c.saved_baseline_guard==nil) or
  (integer(c.saved_baseline_limit) and type(c.saved_baseline_guard)=='function'))
 assert(integer(c.max_tracks) and c.max_tracks<=1000000 and integer(c.scan_budget) and c.scan_budget<=1024)
 assert(finite(c.interval) and c.interval>0 and c.interval<=60 and finite(c.timeout) and c.timeout>0 and c.timeout<=120)
 assert(c.interval_from_start==nil or type(c.interval_from_start)=='boolean')
 assert(finite(c.min_dt) and finite(c.max_dt) and c.min_dt>0 and c.min_dt<=c.max_dt and c.max_dt<=60)
 assert(c.include_orientation==nil or type(c.include_orientation)=='boolean')
 assert(c.real_time_motion==nil or type(c.real_time_motion)=='boolean')
 assert(c.allow_superhighway_gap==nil or type(c.allow_superhighway_gap)=='boolean')
 assert(c.recheck_superhighway_boundary==nil or (type(c.recheck_superhighway_boundary)=='boolean'
  and (not c.recheck_superhighway_boundary or c.allow_superhighway_gap)))
 assert(c.recover_expired_pending==nil or type(c.recover_expired_pending)=='boolean')
 assert(not (c.real_time_motion and c.include_orientation))
 local state,busy='ready',false
 local entries,order,actors={}, {}, {}
 local cursor,request_number,last_clock,last_game,pending=1,0,nil,nil,nil
 local last_capture_after=nil
 local tick_detail
 local api={}
 local function fail(reason)
  if state~='failed' then state='failed';pcall(c.fault,reason)end
  return false
 end
 -- A private sentinel distinguishes a measured deadline from guard failures
 -- and arbitrary reader errors. Only the Company source opts in to recovery.
 local expired_pending={}
 local function before_deadline(t)
  if not pending or t<pending.deadline then return true end
  if c.recover_expired_pending and not pending.entry.boundary_recheck then
   last_clock=t
   error(expired_pending,0)
  end
  return false
 end
 local function finish_failure(op,err)
  if err~=expired_pending then return fail(op) end
  if not api:discard_pending() then return false end
  return true,nil,'pending_timeout'
 end
 local function check()
  if state=='failed' or state=='closed' then return false end
  local before=state
  local ok,t=pcall(c.clock)
  if not ok or state~=before or not finite(t) or t<0 then tick_detail='clock_invalid';return false end
  if last_clock and t<last_clock then tick_detail='clock_regressed';return false end
  if not before_deadline(t) then tick_detail='deadline';return false end
  last_clock=t
  local g,v=pcall(c.guard)
  if not g then tick_detail='guard_error';return false end
  if v~=true or state~=before then tick_detail='guard_rejected';return false end
  local yes,u=pcall(c.clock)
  if not yes or state~=before or not finite(u) then tick_detail='clock_invalid';return false end
  if u<t then tick_detail='clock_regressed';return false end
  if not before_deadline(u) then tick_detail='deadline';return false end
  last_clock=u;return true
 end
 local function row_ok(e)
  return valid_row(e.row,c.epoch,c.saved_baseline_limit) and e.row.index==e.index and e.row.sequence==e.source_sequence
 end
 local function checked_binding(entry)
  if not row_ok(entry) then return false end
  local old=state
  local ok,key=pcall(c.actor_key,entry.actor)
  if not ok or state~=old or key~=entry.key then return false end
  local binding=entry.row.classification=='saved_baseline' and c.saved_baseline_guard or c.binding_guard
  local good,allowed=pcall(binding,entry.row,entry.actor)
  return good and allowed==true and state==old and row_ok(entry) and check()
 end
 function api:register(row,actor)
  if busy or state~='ready' or not valid_row(row,c.epoch,c.saved_baseline_limit) or actor==nil or entries[row.index] or #order>=c.max_tracks then return fail('register')end
  local copy={epoch=row.epoch,index=row.index,sequence=row.sequence,classification=row.classification}
  busy=true
  local ok=pcall(function()
   assert(check())
   local key=c.actor_key(actor)
   assert(type(key)=='string' and key:match('^[1-9]%d*$') and not actors[key])
   local entry={row=copy,index=copy.index,source_sequence=copy.sequence,actor=actor,key=key,next_due=0,sequence=0}
   assert(checked_binding(entry) and not entries[copy.index] and not actors[key])
   entries[copy.index]=entry;actors[key]=copy.index;order[#order+1]=copy.index
  end)
  busy=false;if not ok then return fail('register')end;return true
 end
 -- The descriptor returns fixed failure codes. Drop anything outside that set
 -- before it reaches the fault callback, including a custom reader's errors.
 local descriptor_faults={clock_failed=true,clock_invalid=true,clock_timeout=true,context_lost=true,
  guard_failed=true,dispatch=true,reentrant=true,state_changed=true,callback_failed=true,
  unavailable=true,not_waiting=true,not_idle=true,actor_id=true,game_time=true,deadline=true}
 local function reader_ok(reader,operation)
  tick_detail='reader_'..operation
  local ok,reason=reader[operation](reader)
  if ok==true then tick_detail=nil;return true end
  if c.diagnostics and descriptor_faults[reason] then tick_detail=tick_detail..'_'..reason end
  return false
 end
 function api:tick()
  if busy or state=='failed' or state=='closed' then return fail('tick')end
  busy=true;tick_detail=nil
  local ok,err=pcall(function()
   assert(check())
   if state=='pending' then assert(reader_ok(pending.reader,'tick') and check());return end
   assert(state=='ready')
   local n=#order
   for _=1,math.min(c.scan_budget,n)do
    if cursor>n then cursor=1 end
    local entry=entries[order[cursor]];cursor=cursor+1
    if entry and last_clock>=entry.next_due then
     assert(checked_binding(entry))
     tick_detail='request_counter'
     assert(request_number<MAX)
     request_number=request_number+1
     tick_detail='request_format'
     local request=c.nonce:sub(1,16)..string.format('%016x',request_number)
     tick_detail='reader_factory'
     local reader=assert(c.reader_factory(entry.row,entry.actor,request))
     tick_detail=nil
     assert(checked_binding(entry))
     pending={entry=entry,reader=reader,request=request,started_at=last_clock,deadline=last_clock+c.timeout}
     state='pending';assert(reader_ok(reader,'start') and check());break
    end
   end
  end)
  busy=false
  if not ok then
   local label=c.diagnostics and tick_detail and 'tick_'..tick_detail or 'tick'
   return finish_failure(label,err)
  end
  return true
 end
 function api:expected_request()return state=='pending' and pending.request or nil end
 local function next_due()
  -- The registered NPC can count time waiting for the descriptor toward the interval.
  return (c.interval_from_start and pending.started_at or last_clock)+c.interval
 end
 local response_faults={response_type=true,response_mismatch=true,descriptor_unavailable=true,
  descriptor_invalid=true,transit_rejected=true,actor_changed=true,sector_id=true,
  snapshot_callback=true,snapshot_invalid=true,capture_clock_invalid=true,orientation_invalid=true}
 function api:event(value)
  if busy or state~='pending' or type(value)~='string' or value:sub(1,32)~=pending.request or value:sub(33,33)~='|' then return fail('event')end
  busy=true
  local result,gap,response_fault
  local ok,err=pcall(function()
   assert(check());local e=pending.entry;assert(checked_binding(e))
   local accepted,sample,withdrawal,witness=pending.reader:response(value)
   if accepted~=true and c.diagnostics and (response_faults[sample] or descriptor_faults[sample]) then
    response_fault='sample_reader_response_'..sample
   end
   if accepted==true and sample==nil and c.allow_superhighway_gap then
    if withdrawal=='superhighway_transit' then
     e.boundary_recheck=nil
    elseif withdrawal=='superhighway_boundary_recheck' and c.recheck_superhighway_boundary then
     if e.boundary_recheck then
      if c.diagnostics then response_fault='sample_boundary_recheck_exhausted' end
      error('repeated stale sector descriptor')
     end
     assert(type(witness)=='table' and name(witness.sector_macro)
      and type(witness.local_sector_key)=='string' and witness.local_sector_key:match('^[1-9]%d*$')
      and (e.sent_sector~=witness.sector_macro or e.sent_local_sector_key==witness.local_sector_key)
      and finite(witness.game_before) and finite(witness.game_after)
      and witness.game_before>=(last_game or 0) and witness.game_after>=witness.game_before and witness.game_after<=1e12)
     if c.real_time_motion then
      assert(finite(witness.capture_before) and finite(witness.capture_after)
       and witness.capture_before>=(last_capture_after or 0) and witness.capture_after>=witness.capture_before
       and witness.capture_after<=1e12)
      last_capture_after=witness.capture_after
     end
     last_game=witness.game_after
     e.boundary_recheck=true
    else error('unexpected descriptor withdrawal') end
    assert(check() and checked_binding(e) and state=='pending')
    e.previous=nil
    e.next_due=next_due()
    pending=nil;state='ready';gap=withdrawal
    return
   end
   assert(accepted==true and type(sample)=='table' and getmetatable(sample)==nil and sample.epoch==c.epoch and sample.index==e.row.index
    and sample.sequence==e.row.sequence and sample.descriptor_atomic==false and sample.sector_identity_proven==false
    and name(sample.sector_macro) and type(sample.local_sector_key)=='string' and sample.local_sector_key:match('^[1-9]%d*$')
    and finite(sample.game_before_pose) and finite(sample.game_after_pose)
    and sample.game_before_pose>=0 and sample.game_after_pose>=sample.game_before_pose and sample.game_after_pose<=1e12
    and finite(sample.x) and finite(sample.y) and finite(sample.z)
    and math.abs(sample.x)<=1e7 and math.abs(sample.y)<=1e7 and math.abs(sample.z)<=1e7)
   if c.real_time_motion then
    assert(finite(sample.capture_before_pose) and finite(sample.capture_after_pose) and
     sample.capture_before_pose>=0 and sample.capture_after_pose>=sample.capture_before_pose and sample.capture_after_pose<=1e12)
   end
   local sampled={
    capture_before_pose=sample.capture_before_pose,capture_after_pose=sample.capture_after_pose,
    sector_macro=sample.sector_macro,local_sector_key=sample.local_sector_key,
    game_after_pose=sample.game_after_pose,x=sample.x,y=sample.y,z=sample.z}
   if c.include_orientation then
    sampled.orientation={}
    for _,axis in ipairs({'yaw','pitch','roll'}) do
     assert(finite(sample[axis]) and math.abs(sample[axis])<=2*math.pi)
     sampled.orientation[axis]=sample[axis]
    end
   end
   assert(checked_binding(e) and e.sequence<MAX)
   sample=sampled
   local t=sample.game_after_pose;assert(not last_game or t>=last_game,'source game clock regressed')
   local previous=e.previous;local velocity,angular_rate=nil,nil
   local capture_time=sample.capture_after_pose
   if c.real_time_motion then
    assert(not last_capture_after or sample.capture_before_pose>=last_capture_after,
     'source capture clock regressed between readers')
    assert(not e.last_capture_time or capture_time>e.last_capture_time,'source capture clock did not advance')
   end
   assert(not e.sent_sector or e.sent_sector~=sample.sector_macro or
    e.sent_local_sector_key==sample.local_sector_key,'sent local sector identity changed under same macro')
   e.boundary_recheck=nil
   -- Legacy samples suppress duplicate game times after a baseline reset.
   -- Real-time samples instead retain both capture-clock progression fences.
   if not c.real_time_motion and not previous and e.last_emitted_time and t==e.last_emitted_time then
    e.next_due=next_due();pending=nil;state='ready';result=nil;return
   end
   local rate_interval_seconds=nil
   if previous then
    rate_interval_seconds=t-previous.time
    if c.real_time_motion then
     rate_interval_seconds=capture_time-previous.capture_time
    end
    assert(t>=previous.last_time,'source game clock regressed')
    assert(previous.sector~=sample.sector_macro or previous.local_sector_key==sample.local_sector_key,
     'local sector identity changed under same macro')
    if previous.sector==sample.sector_macro and previous.local_sector_key==sample.local_sector_key then
     local dt=rate_interval_seconds
     if dt>=c.min_dt and dt<=c.max_dt then
      local x,y,z=(sample.x-previous.x)/dt,(sample.y-previous.y)/dt,(sample.z-previous.z)/dt
      if finite(x) and finite(y) and finite(z) and math.abs(x)<=5000 and math.abs(y)<=5000 and math.abs(z)<=5000 then velocity={x=x,y=y,z=z}end
      if velocity and c.include_orientation then
       angular_rate={}
       for _,axis in ipairs({'yaw','pitch','roll'}) do
        local delta=(sample.orientation[axis]-previous.orientation[axis]+math.pi)%(2*math.pi)-math.pi
        local rate=delta/dt
        if not finite(rate) or math.abs(rate)>2*math.pi then angular_rate=nil;velocity=nil;break end
        angular_rate[axis]=rate
       end
      end
     end
    end
   end
   if not c.real_time_motion and previous and t==previous.last_time then
    if previous.sector~=sample.sector_macro or previous.local_sector_key~=sample.local_sector_key then
     e.previous={sector=sample.sector_macro,local_sector_key=sample.local_sector_key,time=t,last_time=t,
      x=sample.x,y=sample.y,z=sample.z,orientation=copy_orientation(sample.orientation)}
    end
    e.next_due=next_due();pending=nil;state='ready';result=nil;return
   end
   -- The receiver has only seen the last emitted sector, not suppressed reads.
   if e.sent_sector and e.sent_sector~=sample.sector_macro then velocity=nil;angular_rate=nil end
   local v={version=c.include_orientation and 2 or 1,epoch=c.epoch,generation=c.generation,index=e.row.index,sequence=e.sequence+1,
    sector_macro=sample.sector_macro,source_time=t,x=sample.x,y=sample.y,z=sample.z,velocity=velocity}
   if c.real_time_motion then
    v.version=3
    v.velocity=nil
    v.capture_time=capture_time
    v.real_velocity=velocity
   elseif c.include_orientation then v.orientation=sample.orientation;v.angular_rate=angular_rate end
   assert(check() and state=='pending')
   result=v
   last_game=t
   e.last_emitted_time=t
   if c.real_time_motion then
    last_capture_after=capture_time
    e.last_capture_time=capture_time
   end
   e.sequence=e.sequence+1
   e.sent_sector=sample.sector_macro
   e.sent_local_sector_key=sample.local_sector_key
   e.next_due=next_due()
   if not previous or previous.sector~=sample.sector_macro or
      previous.local_sector_key~=sample.local_sector_key or rate_interval_seconds>=c.min_dt then
    e.previous={capture_time=capture_time,sector=sample.sector_macro,
     local_sector_key=sample.local_sector_key,time=t,last_time=t,
     x=sample.x,y=sample.y,z=sample.z,orientation=copy_orientation(sample.orientation)}
   else previous.last_time=t end
   pending=nil;state='ready'
  end)
  busy=false;if not ok then return finish_failure(response_fault or 'sample',err)end;return true,result,gap
 end
 function api:state()return state end
 function api:discard_pending()
  if busy or state~='pending' then return fail('discard_pending')end
  busy=true
  local reader=pending.reader
  pending=nil;state='ready'
  local ok,closed=pcall(reader.close,reader)
  busy=false
  if not ok or closed~=true or state~='ready' then return fail('discard_pending')end
  return true
 end
 function api:reset_baseline(index)
  if busy or state~='ready' or not integer(index) or not entries[index] then return fail('reset')end
  busy=true
  local ok=pcall(function()
   local entry=entries[index]
   assert(check() and checked_binding(entry) and state=='ready')
   entry.previous=nil
   assert(check() and checked_binding(entry) and state=='ready')
  end)
  busy=false;if not ok then return fail('reset')end;return true
 end
 function api:close()if busy or state=='pending' then return fail('close')end;state='closed';return true end
 return api
end
-- This factory uses the existing native descriptor reader. The existing X4
-- owner must route the single correlated event by expected_request() and drive
-- tick; registering a second handler or update script is deliberately avoided.
function M.new_x4(c)
 if type(c)~='table' or c.enabled~=true then return nil,'disabled' end
 local frozen={};for k,v in pairs(c)do frozen[k]=v end;c=frozen
 if c.real_time_motion then assert(type(c.capture_clock)=='function') end
 assert(type(c.descriptor)=='table' and type(c.descriptor.new_x4)=='function')
 assert(type(c.resolve)=='table' and type(c.resolve.actor_u64)=='function' and c.resolve==X4LAN_RESOLVE_STRING)
 assert(type(c.expected_save_path)=='string' and type(c.guard)=='function' and type(c.binding_guard)=='function' and type(c.fault)=='function'
  and type(GetCurRealTime)=='function' and type(GetComponentData)=='function' and type(ConvertStringToLuaID)=='function'
  and type(ConvertIDTo64Bit)=='function')
 local opts={};for k,v in pairs(c)do opts[k]=v end
 assert(c.diagnostics==nil or type(c.diagnostics)=='boolean')
 local binding_stage='none';local report_fault=c.fault
 opts.fault=function(reason)
  if c.diagnostics and binding_stage~='none' then return report_fault(reason..'_'..binding_stage) end
  return report_fault(reason)
 end
 local resolve_actor,new_descriptor=c.resolve.actor_u64,c.descriptor.new_x4
 local component_data,to_lua_id,to64=GetComponentData,ConvertStringToLuaID,ConvertIDTo64Bit
 local external_binding,external_baseline=c.binding_guard,c.saved_baseline_guard
 opts.clock=GetCurRealTime
 opts.actor_key=function(actor)
  local v=resolve_actor(actor);if not v then return nil end
  local s=tostring(v):gsub('ULL$','');return s:match('^[1-9]%d*$') and s or nil
 end
 local function guarded_binding(binding,row,actor)
  local function free()
   binding_stage='actor_key'
   local key=opts.actor_key(actor);if not key then return false end
   local original=resolve_actor(actor);if not original then return false end
   binding_stage='id_convert'
   local id=to_lua_id(key);if not id then return false end
   binding_stage='id_roundtrip'
   -- X4 LuaIDs may be opaque userdata. Do not inspect or stringify them:
   -- only their exact converted uint64 can establish identity. Numeric and
   -- cdata values must additionally pass the direct, non-rounding check.
   local kind=type(id)
   if kind=='number' or kind=='cdata' then
    local from_id=resolve_actor(id);if not from_id or from_id~=original then return false end
   elseif kind~='userdata' then return false end
   local roundtrip=resolve_actor(to64(id));if not roundtrip or roundtrip~=original then return false end
   binding_stage='dock_values'
   local docked,docking,stored=component_data(id,'isdocked','isdocking','isinternallystored')
   return docked==false and docking==false and stored==false
  end
  if not free() then return false end
  binding_stage='external_binding'
  if binding(row,actor)~=true or not free() then return false end
  binding_stage='none';return true
 end
 opts.binding_guard=function(row,actor)return guarded_binding(external_binding,row,actor)end
 if external_baseline then
  opts.saved_baseline_guard=function(row,actor)return guarded_binding(external_baseline,row,actor)end
 end
 opts.reader_factory=function(row,actor,request)
  return new_descriptor({enabled=true,epoch=c.epoch,request_id=request,timeout=c.timeout,
   row=row,actor=actor,expected_save_path=c.expected_save_path,normalize_save_path=c.normalize_save_path,
   guard=c.guard,include_local_sector=true,include_orientation=c.include_orientation,
   include_capture_time=c.real_time_motion,capture_clock=c.capture_clock,
   allow_superhighway_gap=c.allow_superhighway_gap,recheck_superhighway_boundary=c.recheck_superhighway_boundary,
   saved_baseline=row.classification=='saved_baseline' and c.saved_baseline_limit or nil})
 end
 return M.new(opts)
end
X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER=M
return M
