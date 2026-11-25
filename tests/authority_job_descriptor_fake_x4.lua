local ffi=require('ffi');ffi.cdef[[void test_context(bool);int test_pose_reads(void);]]
local fake=ffi.load(assert(arg[1]),true);fake.test_context(true)
local epoch,rid=string.rep('a',32),string.rep('b',32)
local issued={};local t=1
function AddUITriggeredEvent(screen,op,args) issued[#issued+1]=args end
function GetCurRealTime() return t end
function ConvertIDTo64Bit(value) return ffi.new('uint64_t',value) end
X4LAN_RESOLVE_STRING=dofile('tools/dev/authority_registry_resolve_string.lua')
local M=dofile('tools/dev/authority_job_descriptor_probe.lua')
local id=ffi.new('uint64_t',9007199254740992)+ffi.new('uint64_t',1)
local cfg={enabled=true,epoch=epoch,request_id=rid,timeout=5,actor=id,expected_save_path='/tmp/45841/save',
 row={epoch=epoch,index=2,sequence=1,classification='new_registry_entry'},guard=function()return true end}
local function response(hex, request_id)
 return (request_id or rid)..'|'..epoch..'|2|ok|'..hex..'|ship_a|argon|0x2|sector_a'
end
local app=M.new_x4(cfg);assert(app:start());assert(issued[1].index==2)
local ok,row=app:response(response('0x20000000000001'));assert(ok and row.x==1 and row.sector_local==nil)
assert(not row.sector_identity_proven and not row.descriptor_atomic and not row.birth_confirmed)
-- Capture readings surround the actual fake-ABI pose call and survive the reader.
local reads_before=fake.test_pose_reads();local capture_calls=0
cfg.include_capture_time=true
cfg.capture_clock=function()
 capture_calls=capture_calls+1
 assert(fake.test_pose_reads()==reads_before+capture_calls-1)
 return 20+capture_calls
end
app=M.new_x4(cfg)
-- Replacing the caller's config after construction cannot replace the clock.
cfg.capture_clock=function()error('foreign clock')end
assert(app:start());ok,row=app:response(response('0x20000000000001'))
assert(ok and capture_calls==2 and row.capture_before_pose==21 and row.capture_after_pose==22)
assert(fake.test_pose_reads()==reads_before+1)
for _,values in ipairs({{0/0,2},{2,1}}) do
 local count=0;cfg.capture_clock=function()count=count+1;return values[count]end
 app=M.new_x4(cfg);assert(app:start());assert(not app:response(response('0x20000000000001')))
end
cfg.capture_clock=nil;assert(not pcall(M.new_x4,cfg))
cfg.capture_clock=function()error('foreign clock failed')end
app=M.new_x4(cfg);assert(app:start());assert(not app:response(response('0x20000000000001')))
cfg.include_capture_time=nil;cfg.capture_clock=nil
-- Once represented as a double at or above 2^53, a source actor is unsafe.
issued={};cfg.actor=9007199254740992
app=M.new_x4(cfg);assert(not app:start());assert(#issued==0)
issued={};cfg.actor=id
function ConvertIDTo64Bit(value) return ffi.new('uint64_t',value)+ffi.new('uint64_t',1) end
app=M.new_x4(cfg);assert(not app:start());assert(#issued==0)
function ConvertIDTo64Bit(value) return ffi.new('uint64_t',value) end
-- Sector context changed between correlation and sampling.
issued={};cfg.actor=id;fake.test_context(false)
app=M.new_x4(cfg);assert(app:start());assert(not app:response(response('0x20000000000001')))
-- A saved-bootstrap actor is a uint64 cdata, not the Lua component reference
-- accepted by X4's ConvertIDTo64Bit. Model that distinction with userdata.
fake.test_context(true);issued={};cfg.saved_baseline=100
cfg.row={epoch=epoch,index=2,classification='saved_baseline'}
local opaque=newproxy();local bad_opaque=newproxy()
function ConvertStringToLuaID(s)assert(s=='9007199254740993');return opaque end
function ConvertIDTo64Bit(ref)assert(ref==opaque,'expected Lua component userdata');return id end
app=M.new_x4(cfg);assert(app:start());assert(#issued==1)
ok,row=app:response(response('0x20000000000001'));assert(ok and row.index==2 and row.sequence==nil)
-- The Company sampler creates a distinct reader for every captured position.
local previous_reads = fake.test_pose_reads()
local iterations = 20000
cfg.include_capture_time = true
cfg.capture_clock = GetCurRealTime
for sequence = 1, iterations do
    t = t + 0.1
    cfg.request_id = string.format('%032x', sequence)
    local reader = M.new_x4(cfg)
    assert(reader:start())
    assert(issued[#issued].request_id == cfg.request_id)
    local accepted, sample = reader:response(response('0x20000000000001', cfg.request_id))
    assert(accepted and sample.index == 2 and sample.x == 1 and sample.capture_after_pose == t)
    issued = {}
end
assert(fake.test_pose_reads() - previous_reads == iterations)
cfg.request_id, cfg.include_capture_time, cfg.capture_clock = rid, nil, nil

-- Exact roundtrip remains mandatory; rounded Lua numbers and wrong refs fail.
issued={};function ConvertIDTo64Bit(ref)assert(ref==opaque);return id+ffi.new('uint64_t',1) end
app=M.new_x4(cfg);assert(not app:start() and #issued==0)
issued={};function ConvertStringToLuaID(_)return 9007199254740992 end
app=M.new_x4(cfg);assert(not app:start() and #issued==0)
issued={};function ConvertStringToLuaID(_)return bad_opaque end
app=M.new_x4(cfg);assert(not app:start() and #issued==0)
print('fake X4 job descriptor checks passed')
