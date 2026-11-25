-- Opt-in one-actor, read-only diagnostic. Import registers no events.
-- One owner must fence the job queue and the matching registry/descriptor callbacks.
local M={}
local SCREEN='X4AuthorityJobDescriptorProbe'
local function token(s) return type(s)=='string' and #s==32 and s:match('^[0-9a-f]+$')~=nil end
local function integer(n) return type(n)=='number' and n==n and n>=1 and n<=9007199254740991 and n%1==0 end
local function finite(n) return type(n)=='number' and n==n and n>=0 and n<math.huge end
local function coord(n) return type(n)=='number' and n==n and math.abs(n)<math.huge end
local function name(s) return type(s)=='string' and #s>=1 and #s<=128 and s:match('^[%w_%-]+$')~=nil end

function M.new(o)
 if type(o)~='table' or o.enabled~=true then return nil,'disabled' end
 assert(token(o.epoch) and token(o.request_id) and finite(o.timeout) and o.timeout>0 and o.timeout<=120)
 assert(o.allow_superhighway_gap==nil or type(o.allow_superhighway_gap)=='boolean')
 assert(o.recheck_superhighway_boundary==nil or (type(o.recheck_superhighway_boundary)=='boolean'
  and (not o.recheck_superhighway_boundary or o.allow_superhighway_gap)))
 assert(o.include_local_sector==nil or type(o.include_local_sector)=='boolean')
 assert(o.include_orientation==nil or type(o.include_orientation)=='boolean')
 assert(o.include_capture_time==nil or type(o.include_capture_time)=='boolean')
 if o.include_capture_time then assert(type(o.capture_clock)=='function') end
 assert(type(o.row)=='table' and o.row.epoch==o.epoch and integer(o.row.index) and o.actor~=nil)
 if o.saved_baseline~=nil then
  assert(integer(o.saved_baseline) and o.row.index<=o.saved_baseline and o.row.classification=='saved_baseline' and o.row.sequence==nil)
 else assert(o.row.classification=='new_registry_entry' and integer(o.row.sequence)) end
 for _,n in ipairs({'issue','clock','game_time','guard','actor_key','hex_key','snapshot'}) do assert(type(o[n])=='function',n..' required') end
 local state='idle';local busy=false;local generation={};local last,deadline,query_game,actor_key
 local api={}
 local function fail(reason) state='failed';generation={};return false,reason end
 local function call(fn,...)
  if busy then return fail('reentrant') end
  local g,p=generation,state;busy=true;local ok,value=pcall(fn,...);busy=false
  if g~=generation or p~=state then return fail('state_changed') end
  if not ok then return fail('callback_failed') end
  return true,value
 end
 local function check()
  if state=='failed' or state=='closed' or state=='done' then return false,'unavailable' end
  local ok,t=call(o.clock);if not ok then return false,'clock_failed' end
  if not finite(t) or (last and t<last) then return fail('clock_invalid') end
  if deadline and t>=deadline then return fail('clock_timeout') end
  last=t
  local good,valid=call(o.guard);if not good then return false,'guard_failed' end
  if valid~=true then return fail('context_lost') end
  local checked,after=call(o.clock);if not checked then return false,'clock_failed' end
  if not finite(after) or after<last then return fail('clock_invalid') end
  if deadline and after>=deadline then return fail('clock_timeout') end
  last=after
  return true
 end
 function api:start()
  if state~='idle' then return fail('not_idle') end
  local checked,reason=check()
  if not checked then return false,reason end
  local ok,key=call(o.actor_key,o.actor)
  if not ok then return false,key end
  if type(key)~='string' or not key:match('^[1-9]%d*$') then return fail('actor_id') end
  actor_key=key
  local good,game=call(o.game_time)
  if not good then return false,game end
  if not finite(game) then return fail('game_time') end
  query_game=game;state='waiting';generation={};deadline=last+o.timeout
  if not finite(deadline) then return fail('deadline') end
  checked,reason=check()
  if not checked then return false,reason end
  local args={epoch=o.epoch,index=o.row.index,request_id=o.request_id}
  if o.allow_superhighway_gap then args.allow_superhighway_gap=true end
  local sent,accepted=call(o.issue,SCREEN,'Read',args)
  if not sent then return false,accepted end
  if accepted~=true then return fail('dispatch') end
  return check()
 end
 local function complete(check_before_actor)
  if check_before_actor then
   local checked,reason=check()
   if not checked then return false,reason end
  end
  local verified,current=call(o.actor_key,o.actor)
  if not verified then return false,current end
  if current~=actor_key then return fail('actor_changed') end
  local checked,reason=check()
  if not checked then return false,reason end
  state='done';deadline=nil
  return true
 end
 function api:response(value)
  if state~='waiting' then return fail('unexpected_response') end
  local checked,reason=check()
  if not checked then return false,reason end
  if type(value)~='string' or #value>768 then return fail('response_type') end
  local rid,epoch,index,body=value:match('^([0-9a-f]+)|([0-9a-f]+)|([0-9]+)|(.+)$')
  if rid~=o.request_id or epoch~=o.epoch or index~=string.format('%.0f',o.row.index) then return fail('response_mismatch') end
  if body=='transit' or body:sub(1,8)=='transit|' then
   local actor=body:match('^transit|(0[xX][0-9a-fA-F]+)$')
   if not o.allow_superhighway_gap or not actor then return fail('transit_rejected') end
   local ok,key=call(o.hex_key,actor)
   if not ok or key~=actor_key then return fail('transit_rejected') end
   local accepted,rejection=complete()
   if not accepted then return false,rejection end
   return true,nil,'superhighway_transit'
  end
  if body=='unavailable' then return fail('descriptor_unavailable') end
  local actor,macro,owner,sector,sector_macro=body:match('^ok|(0[xX][0-9a-fA-F]+)|([^|]+)|([^|]+)|(0[xX][0-9a-fA-F]+)|([^|]+)$')
  if not actor or not sector or not name(macro) or not name(owner) or not name(sector_macro) then return fail('descriptor_invalid') end
  local ok,key=call(o.hex_key,actor);if not ok then return fail('descriptor_invalid') end
  if key~=actor_key then return fail('actor_changed') end
  local sok,sector_key=call(o.hex_key,sector);if not sok then return fail('sector_id') end
  if type(sector_key)~='string' or not sector_key:match('^[1-9]%d*$') then return fail('sector_id') end
  local good,sample=call(o.snapshot,o.actor,sector)
  if not good then return fail('snapshot_callback') end
  if (type(sample)~='table' or sample.sector_local~=sector_key or not finite(sample.game_before) or
      not finite(sample.game_after) or sample.game_before<query_game or sample.game_after<sample.game_before) then
   return fail('snapshot_invalid')
  end
  if o.include_capture_time and (not finite(sample.capture_before) or not finite(sample.capture_after) or
      sample.capture_after<sample.capture_before or sample.capture_after>1e12) then return fail('capture_clock_invalid') end
  if sample.boundary==true and o.recheck_superhighway_boundary then
   local accepted,rejection=complete(true)
   if not accepted then return false,rejection end
   return true,nil,'superhighway_boundary_recheck',{
    game_before=sample.game_before,game_after=sample.game_after,
    capture_before=sample.capture_before,capture_after=sample.capture_after,
    sector_macro=sector_macro,local_sector_key=sector_key}
  end
  if not coord(sample.x) or not coord(sample.y) or not coord(sample.z) then return fail('snapshot_invalid') end
  if o.include_orientation then
   for _,axis in ipairs({'yaw','pitch','roll'}) do
    if not coord(sample[axis]) or math.abs(sample[axis])>2*math.pi then return fail('orientation_invalid') end
   end
  end
  local accepted,rejection=complete(true)
  if not accepted then return false,rejection end
  local result={epoch=o.epoch,index=o.row.index,sequence=o.row.sequence,macro=macro,owner=owner,
   sector_macro=sector_macro,x=sample.x,y=sample.y,z=sample.z,
   game_before_request=query_game,game_before_pose=sample.game_before,game_after_pose=sample.game_after,
   sector_identity_proven=false,descriptor_atomic=false,birth_confirmed=false,delivery_confirmed=false}
  -- The key is meaningful only inside this exact X4 process; it must never be sent.
  if o.include_capture_time then
   result.capture_before_pose=sample.capture_before;result.capture_after_pose=sample.capture_after
  end
  if o.include_local_sector then result.local_sector_key=sector_key end
  if o.include_orientation then result.yaw=sample.yaw;result.pitch=sample.pitch;result.roll=sample.roll end
  return true,result
 end
 function api:tick() if state=='waiting' then return check() end;return fail('not_waiting') end
 function api:abort() return fail('external_fence') end
 function api:close() state='closed';generation={};deadline=nil;return true end
 function api:state() return state end
 return api
end

-- LuaJIT retains FFI declarations for the lifetime of the Lua state.
-- A flight creates a new reader per sample, so declare the engine ABI once.
local native_ffi
local function get_native_ffi()
    if native_ffi then return native_ffi end
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then
        ffi.cdef('typedef uint64_t UniverseID;')
    end
    if not pcall(ffi.typeof, 'UIPosRot') then
        ffi.cdef('typedef struct { float x,y,z,yaw,pitch,roll; } UIPosRot;')
    end
    ffi.cdef[[
        bool IsGamePaused(void);
        bool IsPlayerValid(void);
        UniverseID GetPlayerControlledShipID(void);
        bool IsValidComponent(UniverseID);
        bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID, const char*);
        UniverseID GetContextByClass(UniverseID, const char*, bool);
        UIPosRot GetObjectPositionInSector(UniverseID);
        double GetCurrentGameTime(void);
        const char* GetSaveFolderPath(void);
    ]]
    native_ffi = ffi
    return ffi
end

function M.new_x4(cfg)
 if type(cfg)~='table' or cfg.enabled~=true then return nil,'disabled' end
 local frozen={};for k,v in pairs(cfg) do frozen[k]=v end;cfg=frozen
 assert(cfg.include_capture_time==nil or type(cfg.include_capture_time)=='boolean')
 if cfg.include_capture_time then assert(type(cfg.capture_clock)=='function') end
 local saved_baseline=cfg.saved_baseline~=nil
 local velocity_sequence=cfg.registered_npc_velocity_sequence
 assert(velocity_sequence==nil or (saved_baseline and cfg.include_capture_time and
  integer(velocity_sequence) and velocity_sequence<=48))
 local function posix_path(value)
  local path=value:gsub('\\','/'):gsub('/+$','')
  if path:sub(1,1)=='/' and path:sub(-11)=='/45841/save' then return path end
 end
 local normalize=cfg.normalize_save_path or posix_path
 assert(type(cfg.guard)=='function' and type(cfg.expected_save_path)=='string' and type(normalize)=='function')
 local expected_path=assert(normalize(cfg.expected_save_path))
 for _,n in ipairs({'AddUITriggeredEvent','GetCurRealTime','ConvertIDTo64Bit'}) do assert(type(_G[n])=='function',n..' unavailable') end
 if saved_baseline then assert(type(ConvertStringToLuaID)=='function','ConvertStringToLuaID unavailable') end
 assert(type(X4LAN_RESOLVE_STRING)=='table' and type(X4LAN_RESOLVE_STRING.actor_u64)=='function'
  and type(X4LAN_RESOLVE_STRING.parse_hex)=='function')
 local ffi = get_native_ffi()
 local C=ffi.C;local o={};for k,v in pairs(cfg) do o[k]=v end
 local function component_key(id)
  id=X4LAN_RESOLVE_STRING.actor_u64(id);assert(id)
  local value=tostring(id):gsub('ULL$','');assert(value:match('^[1-9]%d*$'));return value
 end
 local function actor_key(actor)
  local original=X4LAN_RESOLVE_STRING.actor_u64(actor);assert(original)
  local ref=actor
  if saved_baseline then
   -- Saved bootstrap returns an exact uint64, whereas X4's conversion API
   -- expects a Lua component reference. Reconstitute it without losing bits.
   ref=ConvertStringToLuaID(component_key(original));local kind=type(ref)
   if kind=='number' or kind=='cdata' then
    local direct=X4LAN_RESOLVE_STRING.actor_u64(ref);assert(direct and direct==original)
   else assert(kind=='userdata') end
  end
  local converted=X4LAN_RESOLVE_STRING.actor_u64(ConvertIDTo64Bit(ref))
  assert(converted and original==converted)
  return component_key(converted),ref
 end
 -- A sectorless context only qualifies when both native lookups identify
 -- the same highway. Guard and exact component checks also fence ordinary poses.
 local function context(actor,sector)
  assert(o.guard() and C.IsValidComponent(actor) and C.IsComponentOperational(actor)
   and C.IsComponentClass(actor,'ship') and C.IsValidComponent(sector) and C.IsComponentClass(sector,'sector'))
  local current=C.GetContextByClass(actor,'sector',false)
  if current==sector then return end
  assert(cfg.recheck_superhighway_boundary and current==0 and C.GetPlayerControlledShipID()==actor)
  local zone=C.GetContextByClass(actor,'zone',false)
  assert(zone~=0 and zone==C.GetContextByClass(actor,'highway',false))
  return zone
 end
 o.actor_key=actor_key
 o.hex_key=function(value) local id=X4LAN_RESOLVE_STRING.parse_hex(value);assert(id);return component_key(id) end
 o.clock=GetCurRealTime;o.game_time=function() return tonumber(C.GetCurrentGameTime()) end
 o.guard=function()
  local p=C.GetSaveFolderPath()
  return cfg.guard()==true and p~=nil and p~=ffi.NULL and normalize(ffi.string(p))==expected_path
   and C.IsPlayerValid() and not C.IsGamePaused()
 end
 o.issue=function(screen,operation,args)
  if velocity_sequence then
   local _,actor=actor_key(cfg.actor)
   args.registered_npc_velocity_sequence=velocity_sequence
   args.registered_npc_velocity_actor=actor
  end
  return AddUITriggeredEvent(screen,operation,args)~=false
 end
 o.snapshot=function(actor,sector_hex)
  local a=X4LAN_RESOLVE_STRING.actor_u64(actor);local s=X4LAN_RESOLVE_STRING.parse_hex(sector_hex)
  assert(a and s)
  local highway_before=context(a,s)
  local capture_before=cfg.include_capture_time and cfg.capture_clock() or nil
  local before=tonumber(C.GetCurrentGameTime())
  local p
  if not highway_before then p=C.GetObjectPositionInSector(a) end
  local after=tonumber(C.GetCurrentGameTime())
  local capture_after=cfg.include_capture_time and cfg.capture_clock() or nil
  local highway_after=context(a,s)
  if highway_before then assert(highway_before==highway_after) end
  local result={sector_local=component_key(s),game_before=before,game_after=after,
   capture_before=capture_before,capture_after=capture_after}
  if highway_after then
   result.boundary=true
  else
   result.x=tonumber(p.x);result.y=tonumber(p.y);result.z=tonumber(p.z)
   result.yaw=tonumber(p.yaw);result.pitch=tonumber(p.pitch);result.roll=tonumber(p.roll)
  end
  return result
 end
 return M.new(o)
end
X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE=M
return M
