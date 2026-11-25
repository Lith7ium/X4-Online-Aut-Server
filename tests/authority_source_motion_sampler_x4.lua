-- Actual source sampler and existing X4 descriptor reader with fake X4 ABI;
-- the correlated MD response is simulated. No native game is started.
local ffi=require('ffi');ffi.cdef[[void TestReset(void);void TestUnpause(void);void TestAdvance(double);]]
ffi.C.TestReset();ffi.C.TestUnpause()
local sampler=dofile('src/authority/lua/source_motion_sampler.lua')
local descriptor=dofile('tools/dev/authority_job_descriptor_probe.lua')
X4LAN_RESOLVE_STRING=dofile('tools/dev/authority_registry_resolve_string.lua')
local epoch,generation,nonce=string.rep('a',32),string.rep('b',32),string.rep('c',32)
local sent,out,errors={}, {}, {};local now=0
function GetCurRealTime()return now end
local conversion='good';local dock_reads=0;local opaque=newproxy(true)
local exact_high=X4LAN_RESOLVE_STRING.parse_hex('0x20000000000001')
function ConvertIDTo64Bit(v)
 if conversion=='opaque_error' then error('conversion failed') end
 if conversion=='bad_roundtrip' or conversion=='opaque_wrong' then return 42 end
 if conversion=='opaque_rounded' then return 9007199254740992 end
 if conversion=='opaque_good' and v==opaque then return exact_high end
 return v
end
function ConvertStringToLuaID(v)
 if conversion=='wrong_id' then return 42 elseif conversion=='rounded_id' then return 9007199254740992
 elseif conversion=='high_exact' then return exact_high
 elseif conversion=='opaque_good' or conversion=='opaque_wrong' or conversion=='opaque_rounded' or conversion=='opaque_error' then return opaque
 elseif conversion=='unsupported' then return {} end
 return tonumber(v)
end
local docked=false
function GetComponentData(_,a,b,c)dock_reads=dock_reads+1;assert(a=='isdocked' and b=='isdocking' and c=='isinternallystored');return docked,false,false end
function AddUITriggeredEvent(screen,op,args)assert(screen=='X4AuthorityJobDescriptorProbe' and op=='Read');sent[#sent+1]=args;return true end
local x=assert(sampler.new_x4({enabled=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
 interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
 expected_save_path='/private/45841/save',guard=function()return true end,binding_guard=function(row,a)return row.index==1 and a==41 end,
 fault=function(v)errors[#errors+1]=v end}))
assert(x:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},41))
assert(x:tick());local req=sent[1].request_id;assert(req==x:expected_request())
local ok,result=x:event(req..'|'..epoch..'|1|ok|0x29|ship_a|argon|0x2b|sector_a');assert(ok);out[#out+1]=result
assert(out[1].x==-400 and out[1].y==100000 and out[1].velocity==nil and out[1].source_time==100 and out[1].local_sector_key==nil)
now=.2;ffi.C.TestAdvance(.2);assert(x:tick());req=sent[2].request_id;assert(req~=sent[1].request_id)
ok,result=x:event(req..'|'..epoch..'|1|ok|0x29|ship_a|argon|0x2b|sector_a');assert(ok);out[#out+1]=result
assert(out[2].sequence==2 and out[2].velocity and out[2].velocity.x==0 and #errors==0)

-- A newly docked actor fences the sampler before another sample is issued.
docked=true;now=.4;assert(not x:tick() and x:state()=='failed');assert(#out==2)

-- Actual descriptor/FFI code with the fake ABI exposes all six pose values
-- only when explicitly requested; it does not establish native-game behavior.
docked=false
local full=assert(sampler.new_x4({enabled=true,include_orientation=true,epoch=epoch,generation=generation,nonce=nonce,
 max_tracks=10,scan_budget=2,interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
 expected_save_path='/private/45841/save',guard=function()return true end,binding_guard=function()return true end,fault=function()end}))
assert(full:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},41))
assert(full:tick());req=sent[#sent].request_id
ok,result=full:event(req..'|'..epoch..'|1|ok|0x29|ship_a|argon|0x2b|sector_a')
assert(ok and result.version==2 and result.orientation.yaw==0 and result.orientation.pitch==0 and result.orientation.roll==0)
assert(result.velocity==nil and result.angular_rate==nil)

-- Never read docking state for an ID altered or rounded by conversion.
docked=false
for _,mode in ipairs({'wrong_id','rounded_id','bad_roundtrip'})do
 conversion=mode;local before=dock_reads
 local y=assert(sampler.new_x4({enabled=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
  interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
  expected_save_path='/private/45841/save',guard=function()return true end,binding_guard=function()return true end,
  fault=function()end}))
 assert(not y:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},41))
 assert(y:state()=='failed' and dock_reads==before)
end

-- A uint64 actor above 2^53 is accepted only if the local Lua-ID round-trips
-- exactly; a rounded Lua number must be rejected before any property read.
local high=assert(X4LAN_RESOLVE_STRING.parse_hex('0x20000000000001'))
local function new_high()return assert(sampler.new_x4({enabled=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
 interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
 expected_save_path='/private/45841/save',guard=function()return true end,binding_guard=function()return true end,fault=function()end}))end
conversion='rounded_id';local before=dock_reads;local bad=new_high()
assert(not bad:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},high) and dock_reads==before)
conversion='high_exact';local good=new_high()
assert(good:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},high) and dock_reads>before)

-- X4 may expose LuaID as an opaque userdata. It is admissible only if its
-- conversion back to the independently captured uint64 is exact; no number
-- or string representation of the userdata is used as an identity.
conversion='opaque_good';local prior=dock_reads;local opaque_good=new_high()
assert(type(opaque)=='userdata' and opaque_good:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},high) and dock_reads>prior)
for _,mode in ipairs({'opaque_wrong','opaque_rounded','opaque_error','unsupported'})do
 conversion=mode;prior=dock_reads;local invalid=new_high()
 assert(not invalid:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},high) and dock_reads==prior)
end

-- Opt-in fault stages identify a failed conversion or docking read without
-- emitting actor IDs or raw property values; the failure still fences sampling.
local function diagnostic(guard)
 local reasons={}
 local z=assert(sampler.new_x4({enabled=true,diagnostics=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
  interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
  expected_save_path='/private/45841/save',guard=guard or function()return true end,
  binding_guard=function()return true end,fault=function(v)reasons[#reasons+1]=v end}))
 return z,reasons
end
conversion='wrong_id';local invalid,why=diagnostic()
assert(not invalid:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},41))
assert(why[1]=='register_id_roundtrip')
conversion='good';docked=true;local unavailable,why2=diagnostic()
assert(not unavailable:register({epoch=epoch,index=1,sequence=1,classification='new_registry_entry'},41))
assert(why2[1]=='register_dock_values')

-- Fixed failure stages distinguish a lost outer guard, a refused native
-- dispatch, and an inner guard lost between the sampler and reader checks.
docked=false
local allowed=true
local guarded,guard_errors=diagnostic(function()return allowed end)
local row={epoch=epoch,index=1,sequence=1,classification='new_registry_entry'}
assert(guarded:register(row,41))
allowed=false
assert(not guarded:tick() and guarded:state()=='failed' and guard_errors[1]=='tick_guard_rejected')
assert(not guarded:tick() and #guard_errors==1)

local dispatch=AddUITriggeredEvent
AddUITriggeredEvent=function()return false end
local refused,dispatch_errors=diagnostic()
assert(refused:register(row,41))
assert(not refused:tick() and refused:state()=='failed' and dispatch_errors[1]=='tick_reader_start_dispatch')

AddUITriggeredEvent=function(...)
 now=now+5
 return dispatch(...)
end
local expired,timeout_errors=diagnostic()
assert(expired:register(row,41))
assert(not expired:tick() and expired:state()=='failed' and timeout_errors[1]=='tick_reader_start_clock_timeout')

AddUITriggeredEvent=dispatch
local pending_guard,checks=false,0
local waiting,wait_errors=diagnostic(function()
 if not pending_guard then return true end
 checks=checks+1
 return checks==1
end)
assert(waiting:register(row,41) and waiting:tick())
pending_guard=true
assert(not waiting:tick() and waiting:state()=='failed' and wait_errors[1]=='tick_reader_tick_context_lost')

-- Existing saved entries have no fabricated job sequence or job receipt.
-- Their separate caller guard must remain true on every local read.
conversion='good';docked=false;now=2
local baseline_ok=true;local baseline_reads=0;local unexpected_job_guard=0
local function saved_opts()
 return {enabled=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
  interval=.1,timeout=5,min_dt=.02,max_dt=1,descriptor=descriptor,resolve=X4LAN_RESOLVE_STRING,
  expected_save_path='/private/45841/save',guard=function()return true end,
  binding_guard=function()unexpected_job_guard=unexpected_job_guard+1;return false end,
  saved_baseline_limit=10054,saved_baseline_guard=function(row,actor)
   baseline_reads=baseline_reads+1;return baseline_ok and row.classification=='saved_baseline' and row.sequence==nil and row.index==1 and actor==41
  end,fault=function()end}
end
local saved=assert(sampler.new_x4(saved_opts()))
assert(saved:register({epoch=epoch,index=1,classification='saved_baseline'},41))
assert(saved:tick());req=sent[#sent].request_id
ok,result=saved:event(req..'|'..epoch..'|1|ok|0x29|ship_a|argon|0x2b|sector_a')
assert(ok and result.sequence==1 and result.velocity==nil and result.index==1)
now=2.2;ffi.C.TestAdvance(.2);assert(saved:tick());req=sent[#sent].request_id
ok,result=saved:event(req..'|'..epoch..'|1|ok|0x29|ship_a|argon|0x2b|sector_a')
assert(ok and result.sequence==2 and result.velocity and result.velocity.x==0 and baseline_reads>0 and unexpected_job_guard==0)
baseline_ok=false;now=2.4;assert(not saved:tick() and saved:state()=='failed')
for _,bad in ipairs({{epoch=epoch,index=10055,classification='saved_baseline'},
                     {epoch=epoch,index=1,sequence=1,classification='saved_baseline'}})do
 local t=assert(sampler.new_x4(saved_opts()));assert(not t:register(bad,41) and t:state()=='failed')
end
local no_opt=saved_opts();no_opt.saved_baseline_limit=nil;no_opt.saved_baseline_guard=nil
local t=assert(sampler.new_x4(no_opt));assert(not t:register({epoch=epoch,index=1,classification='saved_baseline'},41))
local incomplete=saved_opts();incomplete.saved_baseline_guard=nil;assert(not pcall(sampler.new_x4,incomplete))
local probe={enabled=true,epoch=epoch,request_id=nonce,timeout=5,actor=41,
 row={epoch=epoch,index=1,classification='saved_baseline'}}
assert(not pcall(descriptor.new,probe)) -- no implicit baseline mode
probe.saved_baseline=1;probe.row.index=2;assert(not pcall(descriptor.new,probe))
probe.row.index=1;probe.row.sequence=1;assert(not pcall(descriptor.new,probe))

-- Drive the real native adapter across the MD/pose boundary. The fake
-- X4 symbols change sector either before the snapshot or inside its pose read.
ffi.cdef[[
 void TestMotionContext(uint64_t sector,uint64_t zone,uint64_t highway,uint64_t controlled,bool enter);
 int TestPoseReads(void);
]]
local old_sector='ok|0x29|ship_a|argon|0x2b|sector_a'
local boundary='superhighway_boundary_recheck'
local function crossing(opt_in)
 ffi.C.TestMotionContext(43,0,0,41,false)
 ffi.C.TestReset();ffi.C.TestUnpause()
 baseline_ok=true;now=10
 local opts,failures=saved_opts(),{}
 opts.allow_superhighway_gap=true
 opts.recheck_superhighway_boundary=opt_in
 opts.real_time_motion=true
 opts.recover_expired_pending=true
 opts.capture_clock=function()return now end
 opts.diagnostics=true
 opts.fault=function(reason)failures[#failures+1]=reason end
 local source=assert(sampler.new_x4(opts))
 assert(source:register({epoch=epoch,index=1,classification='saved_baseline'},41))
 return source,failures
end
local function request(source)
 now=now+.2;ffi.C.TestAdvance(.2)
 assert(source:tick())
 return assert(source:expected_request())
end
local function respond(source,rid,body)
 return source:event(rid..'|'..epoch..'|1|'..(body or old_sector))
end
for _,inside_pose in ipairs({false,true}) do
 local source,failures=crossing(true)
 local success,pose=respond(source,request(source))
 assert(success and pose.sequence==1)
 success,pose=respond(source,request(source))
 assert(success and pose.sequence==2 and pose.real_velocity)
 local rid=request(source)
 local reads=ffi.C.TestPoseReads()
 ffi.C.TestMotionContext(inside_pose and 43 or 0,45,45,41,inside_pose)
 local gap
 success,pose,gap=respond(source,rid)
 assert(success and pose==nil and gap==boundary and source:state()=='ready')
 assert(ffi.C.TestPoseReads()==reads+(inside_pose and 1 or 0))

 local followup=request(source)
 assert(followup~=rid)
 if inside_pose then
  success,pose,gap=respond(source,followup,'transit|0x29')
  assert(success and pose==nil and gap=='superhighway_transit')
  followup=request(source)
 end
 ffi.C.TestMotionContext(43,0,0,41,false)
 success,pose,gap=respond(source,followup)
 assert(success and not gap and pose.sequence==3 and not pose.real_velocity and #failures==0)
end

for _,context in ipairs({
 {false,0,45,45,41}, -- disabled
 {true,99,45,45,41}, -- a different nonzero sector
 {true,0,0,0,41},   -- no zone
 {true,0,45,46,41}, -- different native zone and highway
 {true,0,45,45,42}, -- player now controls another ship
}) do
 local source,failures=crossing(context[1])
 local rid=request(source)
 ffi.C.TestMotionContext(context[2],context[3],context[4],context[5],false)
 assert(not respond(source,rid) and source:state()=='failed')
 assert(failures[1]=='sample_reader_response_snapshot_callback')
end

for _,failure in ipairs({
 {old_sector,'sample_boundary_recheck_exhausted'},
 {'unavailable','sample_reader_response_descriptor_unavailable'},
 {'ok|garbage','sample_reader_response_descriptor_invalid'},
 {'transit|0x2a','sample_reader_response_transit_rejected'},
}) do
 local source,failures=crossing(true)
 local rid=request(source)
 ffi.C.TestMotionContext(0,45,45,41,false)
 local accepted,pose,gap=respond(source,rid)
 assert(accepted and not pose and gap==boundary)
 assert(not respond(source,request(source),failure[1]) and source:state()=='failed')
 assert(failures[1]==failure[2])
end

local source=crossing(true)
local rid=request(source)
ffi.C.TestMotionContext(0,45,45,41,false)
local accepted,_,gap=respond(source,rid)
assert(accepted and gap==boundary)
request(source)
now=now+5
assert(not source:tick() and source:state()=='failed')
