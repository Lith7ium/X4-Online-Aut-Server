-- Actual native adapter and fixed fixture inspector, with fake X4 ABI.
local ffi=require('ffi')
ffi.cdef[[
 void TestSetRecoveryCode(bool);
 void TestSetMFQCode(void);
 void TestSetPlayerControlsActor(bool);
 void TestPause(bool);
 int TestWrites(void);
 int TestReads(void);
]]
ffi.C.TestSetRecoveryCode(true)
local mode=assert(os.getenv('CONTROL_NATIVE_CASE'))
local now=1
local requests,events={},{}
function GetCurRealTime()return now end
function RegisterEvent(name,callback)assert(not events[name]);events[name]=callback;return true end
function AddUITriggeredEvent(screen,operation,arguments)
 assert(screen=='X4AuthoritySavedReplicaControl')
 requests[#requests+1]={operation=operation,args=arguments};return true
end
function ConvertStringToLuaID(value)
 assert(type(value)=='string' and value:match('^[1-9]%d*$'),'hexadecimal conversion input')
 return ffi.new('uint64_t',tonumber(value))
end
function ConvertIDTo64Bit(value)return value end
function GetComponentData(reference,...)
 local field=select(1,...)
 if field=='isdocked' then return mode=='docked',false,false end
 if field=='macro' and tonumber(reference)==41 then return mode=='fixture' and 'wrong' or 'ship_arg_m_trans_container_02_b_macro','antigone' end
 if field=='macro' and tonumber(reference)==43 then return 'cluster_06_sector001_macro' end
 error('unexpected field')
end
X4LAN_RESOLVE_STRING=dofile('tools/dev/authority_registry_resolve_string.lua')
X4LAN_REPLICA_MOTION_BINDING=dofile('src/authority/lua/replica_motion_binding.lua')
X4LAN_SAVED_MOTION_APPLY=dofile('src/authority/lua/saved_motion_apply.lua')
X4LAN_MOTION_BUFFER=dofile('src/authority/lua/motion_buffer.lua')
local module=dofile('src/authority/lua/saved_replica_control.lua')
local faults={}
local options={enabled=true,fixture='recovery_enh_870',expected_save_path='/private/45841/save',resolve=X4LAN_RESOLVE_STRING,
 binding={actor_key='41',sector_key='43',zone_key='45',index=1318,epoch='4ba7be438ebb04ec5bb85fdb95b5a488',sector_macro='cluster_06_sector001_macro'},
 nonce=string.rep('a',32),timeout=4,clock=function()return now end,guard=function()return true end,
 fault=function(reason,uncertain)faults[#faults+1]={reason,uncertain}end}
if mode=='profile' then options.expected_save_path='/other/45841/save' end
local mfq = mode:match('^mfq_') ~= nil
if mfq then
 options.fixture = 'recovery_mfq_419'
 options.binding.index = 943
 options.binding.epoch = '26a1a365c66788aa06f0aeb9e18ff62e'
 if mode ~= 'mfq_alternate_code' then ffi.C.TestSetMFQCode() end
 if mode == 'mfq_player' then ffi.C.TestSetPlayerControlsActor(true) end
end
local controller=assert(module.new_x4(options))
if mode=='collision' then assert(not pcall(module.new_x4,options) and #requests==0);return end
if mode=='pause' then ffi.C.TestPause(true) end
if mode == 'mfq_alternate_code' or mode == 'mfq_player' then
 assert(not controller:prepare() and controller:state() == 'failed' and not controller:outcome_uncertain())
 assert(#requests == 0 and #faults == 1 and ffi.C.TestReads() == 0 and ffi.C.TestWrites() == 0)
 return
end
if mode=='fixture' or mode=='pause' or mode=='docked' or mode=='profile' then
 assert(not controller:prepare() and #requests==0 and not controller:outcome_uncertain());return
end
assert(controller:prepare() and #requests==1 and requests[1].operation=='Prepare')
assert(ffi.C.TestReads()>0 and ffi.C.TestWrites()==0)
local callback=assert(events['X4AuthoritySavedReplicaControl.Result'])
local request=requests[1].args.request_id
if mfq then
 local args = requests[1].args
 assert(args.fixture == 'recovery_mfq_419' and args.epoch == '26a1a365c66788aa06f0aeb9e18ff62e')
 assert(args.index == 943 and args.owner == options.nonce and args.request_sequence == 1)
 assert(args.actor_key == '41' and args.sector_key == '43' and args.zone_key == '45')
 assert(tonumber(args.actor) == 41 and tonumber(args.sector) == 43 and tonumber(args.zone) == 45)
 if mode == 'mfq_wrong_reply' then
  callback(nil, request..'|Created|0x2a|0x2b|0x2d')
  assert(controller:state() == 'failed' and controller:outcome_uncertain())
  assert(#requests == 1 and ffi.C.TestWrites() == 0)
  return
 end
 callback(nil, request..'|Created|0x29|0x2b|0x2d')
 assert(controller:tick() and requests[2].operation == 'Probe')
 assert(#faults == 0 and ffi.C.TestWrites() == 0)
 return
end
callback(nil,request..'|Created|'..(mode=='wrong_actor' and '0x2a' or '0x29')..'|0x2b|0x2d')
if mode=='duplicate' then callback(nil,request..'|Created|0x29|0x2b|0x2d');assert(controller:state()=='failed' and controller:outcome_uncertain());return end
if mode=='wrong_actor' then assert(controller:state()=='failed' and controller:outcome_uncertain());return end
assert(controller:tick() and requests[2].operation=='Probe')
callback(nil,requests[2].args.request_id..'|Waiting|0x29|0x2b|0x2d')
assert(controller:tick() and requests[3].operation=='Probe')
callback(nil,request..'|Created|0x29|0x2b|0x2d')
assert(controller:state()=='failed' and controller:outcome_uncertain() and #faults==1)
