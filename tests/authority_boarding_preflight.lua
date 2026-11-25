-- Simulate the stock Lua ID / C uint64 boundary without launching X4.
local ffi = require('ffi')
local resolve=dofile('tools/dev/authority_registry_resolve_string.lua')
local actor=function(n) return ffi.new('uint64_t',n) end
local phase='positive'
local other_sector=77
local bad_registry, bad_round_trip, bad_container = false, false, false
local C={
 IsPlayerValid=function()return true end,
 IsValidComponent=function()return true end,
 IsComponentOperational=function()return true end,
 IsComponentClass=function(id,class)
  id=tonumber(id)
  return ((id==11 or id==12) and class=='ship') or (id==12 and class=='ship_l')
   or (id==31 and class=='station') or ((id==66 or id==77) and class=='sector')
 end,
 IsUnit=function()return false end,
 GetObjectIDCode=function(id)return tonumber(id)==11 and 'XAA-001' or 'XAA-002' end,
 GetPlayerID=function()return actor(30) end,
 GetPlayerContainerID=function()return actor(bad_container and 0 or 31) end,
 GetContextByClass=function(id)return actor(tonumber(id)==31 and other_sector or 66) end,
 GetObjectPositionInSector=function(id)return {x=tonumber(id),y=0,z=0} end,
 GetNumTiersOfRole=function(role)assert(role=='marine');return 2 end,
 GetNumAllRoles=function()return 2 end,
 GetRoleTiers2=function(rows,size,id,role,arriving)
  assert(size==2 and tonumber(id)==11 and role=='marine' and not arriving)
  if phase=='positive' then
   rows[0].name='Recruit';rows[0].skilllevel=1;rows[0].amount=3
   rows[1].name='Veteran';rows[1].skilllevel=2;rows[1].amount=2
   return 2
  end
  return 0
 end,
 GetPeople2=function(rows,size,id,arriving)
  assert(size==2 and tonumber(id)==11 and not arriving)
  if phase=='zero' then rows[0].id='service';rows[0].amount=1;return 1 end
  return 0
 end,
 GetPeopleCapacity=function(id,macro,pilot)
  assert(tonumber(id)==12 and macro=='' and not pilot)
  return 4
 end,
 IsOrderSelectableFor=function(order,id)assert(order=='Board' and tonumber(id)==11);return true end,
}
package.loaded.ffi={cdef=ffi.cdef,typeof=ffi.typeof,new=ffi.new,NULL=ffi.NULL,string=function(v)
 if type(v)=='string' then return v end
 return ffi.string(v)
end,C=C}
X4LAN_RESOLVE_STRING=resolve
function ConvertStringToLuaID(decimal) return {id=tonumber(decimal)} end
function ConvertIDTo64Bit(ref)
 assert(type(ref)=='table')
 return actor(bad_round_trip and ref.id + 1 or ref.id)
end
function GetComponentData(ref,field)
 assert(type(ref)=='table', 'Invalid argument #1 <component> (got cdata, expected component ID)')
 local id = ref.id
 if phase=='component_error' and id==11 then error('missing component data') end
 if field=='macro' then return 'test_macro',id==11 and 'player' or 'antigone',id==11 end
 if field=='isonlineobject' then return phase=='online' and true or nil end
 if field=='iscapturable' then return true end
 if field=='boardingresistance' and phase~='error' then return 0 end
 error('getter unavailable')
end
local read=dofile('tools/dev/authority_boarding_preflight.lua').read_x4
local epoch=('a'):rep(32)
local config={enabled=true,epoch=epoch,guard=function()return true end,
 lookup=function(e,i)
  assert(e==epoch)
  return actor(i==1 and 11 or (bad_registry and 99 or 12)), i==1 and 'player' or 'scaleplate'
 end,
 ship={index=1,id=actor(11),code='XAA-001',macro='test_macro',trueowner='player'},
 target={index=2,id=actor(12),code='XAA-002',macro='test_macro',trueowner='scaleplate',visible_owner='antigone'}}
-- FFI IDs used directly by the old probe reproduce the engine rejection.
assert(not pcall(GetComponentData, config.ship.id, 'macro'))
local result=assert(read(config))
assert(result.target_visible_owner=='antigone' and result.target_trueowner=='scaleplate')
assert(result.maximum_from_ship==4 and result.marines.value.total==5 and result.resistance.value==0)
assert(result.avatar_on_station and result.avatar_to_ship_m.status=='different_sector')
phase='zero';other_sector=66
result=assert(read(config))
assert(result.marines.value.total==0 and result.maximum_from_ship==0 and result.avatar_to_target_m.value==18)
phase='error'
result=assert(read(config))
assert(result.maximum_from_ship==nil and result.marines.status=='unavailable' and result.resistance.status=='unavailable')
phase='online';assert(not read(config))
phase='positive'
config.target.trueowner='antigone';assert(not read(config))
config.target.trueowner='scaleplate';config.target.visible_owner='scaleplate';assert(not read(config))
config.target.visible_owner='antigone';config.target.id=actor(99)
assert(not read(config))
local function expects_phase(expected)
 local failed, phase = read(config)
 assert(not failed and phase == expected, tostring(phase))
end
config.target.id=actor(12)
phase='component_error'; expects_phase('ship_component_data')
phase='positive'
bad_round_trip=true; expects_phase('ship_lua_id'); bad_round_trip=false
bad_registry=true; expects_phase('target_registry'); bad_registry=false
bad_container=true; expects_phase('avatar_container')
print('simulated preflight passed')
