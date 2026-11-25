-- Default-off, asynchronous, read-only local replica binding. No hook or wire.
local M={}
local MAX=9007199254740991
local function integer(v)return type(v)=='number' and v==v and v>=1 and v<=MAX and v%1==0 end
local function finite(v)return type(v)=='number' and v==v and v>=0 and v<math.huge end
local function token(v)return type(v)=='string' and #v==32 and v:match('^[0-9a-f]+$')~=nil end
-- Lexically normalize separators only; this does not establish filesystem provenance or resolve filesystem case.
function M.normalize_profile_save_path(value, windows_company_profile)
 if windows_company_profile~=nil and
  (type(windows_company_profile)~='string' or not windows_company_profile:match('^4585[01345678]$')) then
  return nil
 end
 if type(value)~='string' or #value==0 then
  return nil
 end
 if value:find('\0',1,true) or value:find('%c') then
  return nil
 end
 if value:sub(1,1)=='\\' and value:sub(2,2)~='\\' then
  return nil
 end

 local path=value:gsub('\\','/')
 local prefix
 local remainder
 if path:match('^%a:/') then
  prefix=path:sub(1,2)
  remainder=path:sub(4)
 elseif path:sub(1,2)=='//' then
  local server,share,tail=path:match('^//([^/]+)/([^/]+)(/.*)$')
  if not server or not share or server=='.' or server=='..' or server=='?' or share=='.' or share=='..' then
   return nil
  end
  prefix='//'..server..'/'..share
  remainder=tail:sub(2)
 elseif not windows_company_profile and path:sub(1,1)=='/' then
  prefix='/'
  remainder=path:sub(2)
 else
  return nil
 end

 for segment in remainder:gmatch('[^/]+') do
  if segment=='.' or segment=='..' then
   return nil
  end
 end
 while remainder:sub(-1)=='/' do
  remainder=remainder:sub(1,-2)
 end

 local separator=prefix=='/' and '' or '/'
 local normalized=prefix..separator..remainder
 local suffix='/'..(windows_company_profile or '45841')..'/save'
 local profile_path=windows_company_profile and '/'..remainder or normalized
 if profile_path:sub(-#suffix)~=suffix then
  return nil
 end
 return normalized
end
local function name(v)return type(v)=='string' and #v>=1 and #v<=128 and v:match('^[A-Za-z0-9_]+$')~=nil end
-- Exact conversion independent of FFI or Lua number rounding.
local function decimal(hex)
 if type(hex)~='string' or #hex>18 or not hex:match('^0[xX][0-9a-fA-F]+$') then return nil end
 local s='0'
 for i=3,#hex do
  local carry=tonumber(hex:sub(i,i),16);local out={}
  for j=#s,1,-1 do local n=(s:byte(j)-48)*16+carry;out[#out+1]=string.char(48+n%10);carry=math.floor(n/10)end
  while carry>0 do out[#out+1]=string.char(48+carry%10);carry=math.floor(carry/10)end
  s=table.concat(out):reverse():gsub('^0+','');if s=='' then s='0' end
 end
 return s~='0' and s or nil
end
local function new_binding(o,screen)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 local c={};for k,v in pairs(o)do c[k]=v end
 assert(c.md_issue==nil or type(c.md_issue)=='function')
 assert(token(c.epoch) and token(c.generation) and token(c.nonce) and integer(c.index) and name(c.sector_macro))
 assert(finite(c.timeout) and c.timeout>0 and c.timeout<=300 and finite(c.max_age) and c.max_age>0 and c.max_age<=300)
 for _,k in ipairs({'clock','guard','issue','inspect','complete'})do assert(type(c[k])=='function',k..' required')end
 assert(c.capture_binding==nil or type(c.capture_binding)=='function')
 local phase,busy='idle',false;local last,deadline,pending,previous,actor_key,sector_key,zone_key,request_seq=nil,nil,nil,0,nil,nil,nil,0
 local api={}
 local function fail(reason)phase='failed';pending=nil;deadline=nil;return false,reason end
 local function call(fn,...)
  if busy then return fail('reentrant') end
  local before=phase;busy=true;local ok,a,b,d,e=pcall(fn,...);busy=false
  if not ok or phase~=before then return fail('callback') end
  return true,a,b,d,e
 end
 local function check()
  if phase=='failed' or phase=='closed' then return false end
  local ok,t=call(c.clock)
  if not ok or not finite(t) or (last and t<last) or (deadline and t>=deadline) or
     (pending and (t<pending.arrival or t-pending.arrival>c.max_age)) then return fail('clock_or_stale') end
  last=t;local g,v=call(c.guard);if not g or v~=true then return fail('guard') end
  local k,u=call(c.clock)
  if not k or not finite(u) or u<t or (deadline and u>=deadline) or
     (pending and (u<pending.arrival or u-pending.arrival>c.max_age)) then return fail('clock_or_stale') end
  last=u;return true
 end
 function api:begin(sample,arrival)
  if phase~='idle' then return fail('not_idle') end
  if type(sample)~='table' or getmetatable(sample)~=nil or sample.epoch~=c.epoch or sample.generation~=c.generation or
     sample.index~=c.index or sample.sector_macro~=c.sector_macro or not integer(sample.sequence) or sample.sequence<=previous or not finite(arrival) then return fail('identity') end
  local sequence=sample.sequence
  if not check() then return false end
  if arrival>last or last-arrival>c.max_age or request_seq>=MAX then return fail('stale_or_capacity') end
  request_seq=request_seq+1
  pending={id=c.nonce..'-'..string.format('%.0f',c.index)..'-'..string.format('%.0f',request_seq),sequence=sequence,arrival=arrival}
  deadline=last+c.timeout;if not finite(deadline) then return fail('deadline') end
  phase='pending'
  local ok,sent=call(c.issue,screen,'Bind',
   {request_id=pending.id,epoch=c.epoch,index=c.index,sector_macro=c.sector_macro})
  if not ok or sent~=true or not check() then return fail('dispatch') end
  return true
 end
 function api:event(value)
  if phase~='pending' then return fail('unexpected_event') end
  if not check() or type(value)~='string' or #value>256 then return fail('response') end
  local id,kind,rest=value:match('^([0-9a-f]+%-%d+%-%d+)|([A-Za-z]+)|(.*)$')
  if id~=pending.id then return fail('correlation') end
  if kind=='Unavailable' and rest=='' then return fail('unavailable') end
  local idx,actor,sector=rest:match('^([1-9]%d*)|(0[xX][0-9a-fA-F]+)|(0[xX][0-9a-fA-F]+)$')
  if kind~='Bound' or idx~=string.format('%.0f',c.index) then return fail('response') end
  local expected_actor,expected_sector=decimal(actor),decimal(sector)
  if not expected_actor or not expected_sector or (actor_key and actor_key~=expected_actor) or
     (sector_key and sector_key~=expected_sector) then return fail('identity_changed') end
  local ok,a,s,valid,zone=call(c.inspect,actor,sector,c.index,c.sector_macro)
  if not ok or a~=expected_actor or s~=expected_sector or valid~=true or type(zone)~='string' or
     not zone:match('^[1-9]%d*$') or #zone>20 or (zone_key and zone_key~=zone) or not check() then return fail('ffi_binding') end
  -- Local references are provisional until event() returns true. This opt-in
  -- callback is never used for transport and receives exact decimal strings.
  if c.capture_binding then
   local captured,accepted=call(c.capture_binding,expected_actor,expected_sector,zone,c.index,pending.sequence)
   if not captured or accepted~=true or not check() then return fail('capture') end
  end
  local result={epoch=c.epoch,generation=c.generation,index=c.index,sequence=pending.sequence,sector_macro=c.sector_macro}
  phase='delivering';local good,accepted=call(c.complete,result)
  if not good or accepted~=true or result.epoch~=c.epoch or result.generation~=c.generation or result.index~=c.index or
     result.sequence~=pending.sequence or result.sector_macro~=c.sector_macro or getmetatable(result)~=nil then return fail('completion') end
  for k in pairs(result)do if k~='epoch' and k~='generation' and k~='index' and k~='sequence' and k~='sector_macro' then return fail('completion') end end
  if not check() then return false end
  previous=pending.sequence;actor_key=expected_actor;sector_key=expected_sector;zone_key=zone;pending=nil;deadline=nil;phase='idle';return true
 end
 function api:tick()return check()end
 function api:state()return phase end
 function api:close()phase='closed';pending=nil;deadline=nil;return true end
 return api
end
function M.new(o)return new_binding(o,'X4AuthorityReplicaMotionBind')end

-- Real local X4 FFI binding. The caller owns the one UI event router/onUpdate
-- and routes X4AuthorityReplicaMotionBind.Result to api:event; no wire IDs here.
local function new_x4(o,screen,baseline)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 assert(type(o.expected_save_path)=='string' and type(o.guard)=='function' and type(o.complete)=='function')
 assert(type(o.resolve)=='table' and o.resolve==X4LAN_RESOLVE_STRING and type(o.resolve.parse_hex)=='function' and type(o.resolve.actor_u64)=='function')
 for _,n in ipairs({'GetCurRealTime','AddUITriggeredEvent','ConvertStringToLuaID','ConvertIDTo64Bit','GetComponentData'})do assert(type(_G[n])=='function')end
 local c={};for k,v in pairs(o)do c[k]=v end
 c.expected_save_path=M.normalize_profile_save_path(c.expected_save_path)
 assert(c.expected_save_path,'invalid profile-45841 save path')
 local ffi=require('ffi')
 if not pcall(ffi.typeof,'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
 ffi.cdef[[bool IsPlayerValid(void);bool IsGamePaused(void);bool IsValidComponent(UniverseID);bool IsComponentOperational(UniverseID);
 bool IsComponentClass(UniverseID,const char*);UniverseID GetContextByClass(UniverseID,const char*,bool);const char* GetSaveFolderPath(void);]]
 local C=ffi.C;local parse,u64=c.resolve.parse_hex,c.resolve.actor_u64
 local clock,issue,to_lua,to64,data=GetCurRealTime,c.md_issue or AddUITriggeredEvent,ConvertStringToLuaID,ConvertIDTo64Bit,GetComponentData
 local external,expected=c.guard,c.expected_save_path
 local function environment()
  local path=C.GetSaveFolderPath()
  local normalized=path~=nil and path~=ffi.NULL and M.normalize_profile_save_path(ffi.string(path)) or nil
  return normalized==expected and C.IsPlayerValid() and not C.IsGamePaused()
 end
 local function guard()return environment() and external()==true and environment()end
 local function key(v)local n=u64(v);return n and tostring(n):gsub('ULL$','') or nil end
 local function inspect(ah,sh)
  if not guard() then return nil end
  local a,s=parse(ah),parse(sh);if not a or not s then return nil end
  local ak,sk=key(a),key(s);if not ak or not sk then return nil end
  local ar,sr=to_lua(ak),to_lua(sk)
  local function admissible(v)
   if type(v)=='number' then return integer(v) end
   return type(v)=='cdata' or type(v)=='userdata'
  end
  if not admissible(ar) or not admissible(sr) or key(to64(ar))~=ak or key(to64(sr))~=sk or not guard() then return nil end
  if not C.IsValidComponent(a) or not C.IsComponentOperational(a) or not C.IsComponentClass(a,'ship') or
     not C.IsValidComponent(s) or not C.IsComponentClass(s,'sector') or C.GetContextByClass(a,'sector',false)~=s then return nil end
  local z=C.GetContextByClass(a,'zone',false);local zk=key(z)
  if not zk or not C.IsValidComponent(z) or not C.IsComponentClass(z,'zone') then return nil end
  local dock,docking,stored=data(ar,'isdocked','isdocking','isinternallystored')
  if dock~=false or docking~=false or stored~=false or not guard() then return nil end
  if not C.IsValidComponent(a) or not C.IsComponentOperational(a) or not C.IsComponentClass(a,'ship') or
     not C.IsValidComponent(s) or not C.IsComponentClass(s,'sector') or C.GetContextByClass(a,'sector',false)~=s then return nil end
  local d2,g2,s2=data(ar,'isdocked','isdocking','isinternallystored')
  if d2~=false or g2~=false or s2~=false or key(C.GetContextByClass(a,'zone',false))~=zk or
     not C.IsValidComponent(z) or not C.IsComponentClass(z,'zone') or not guard() then return nil end
  return ak,sk,true,zk
 end
 c.clock=clock;c.guard=guard;c.inspect=inspect
 c.issue=function(target,op,args)
  if baseline then args.baseline=baseline end
  if c.md_issue then return issue(target,op,args)==true end
  return issue(target,op,args)~=false
 end
 return new_binding(c,screen)
end
function M.new_x4(o)return new_x4(o,'X4AuthorityReplicaMotionBind')end
-- Separate read-only MD screen; callers must independently fence the saved
-- checkpoint and load incarnation. This does not confer a Create receipt.
function M.new_saved_baseline_x4(o)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 assert(integer(o.baseline) and integer(o.index) and o.index<=o.baseline)
 assert(type(o.guard)=='function' and type(o.saved_baseline_guard)=='function')
 local c={};for k,v in pairs(o)do c[k]=v end
 local guard,baseline_guard,epoch,baseline=c.guard,c.saved_baseline_guard,c.epoch,c.baseline
 c.guard=function()return guard()==true and baseline_guard(epoch,baseline)==true and guard()==true end
 return new_x4(c,'X4AuthoritySavedBaselineMotionBind',baseline)
end
X4LAN_REPLICA_MOTION_BINDING=M
return M
