-- Execute the installed generated Lua with fake MD events and X4 functions.
local folder, mode = assert(arg[1]), assert(arg[2])
local menu, player, paused, game_time, clock = true, false, false, 42, 0
local callbacks, queued, logs, requests = {}, {}, {}, {}
local frame
package.loaded.ffi = {
 NULL={}, cdef=function()end, string=function(value)return value end,
 C={GetSaveFolderPath=function()return 'C:/private/45841/save/' end,
  IsStartmenu=function()return menu end, IsPlayerValid=function()return player end,
  IsGamePaused=function()return paused end, GetCurrentGameTime=function()return game_time end,
  IsSaveListLoadingComplete=function()return true end, ReloadSaveList=function()end}}
DebugError=function(value) logs[#logs+1]=value end
GetCurRealTime=function()return clock end
GetSaveList=function()return {{filename='save_009'}} end
LoadGame=function(slot)assert(slot=='save_009');menu=false;player=true end
Pause=function()paused=true end
SetScript=function(name,callback)assert(name=='onUpdate');frame=callback end
RegisterEvent=function(name,callback)callbacks[name]=callback;return true end
local marker_checks=0
X4LAN_AUTHORITY_MARKER_READY=function()
 assert(paused, 'registry load must pause before checking the marker')
 marker_checks=marker_checks+1
 return marker_checks>=3
end
X4LAN_IDENTITY_READY=function()return true end
function AddUITriggeredEvent(screen,operation,payload)
 requests[#requests+1]=operation
 if screen=='X4AuthorityRegistryAvailabilitySnapshot' then
  assert(operation=='Inspect')
  local binding=mode=='wrong_bits' and '100' or '110'
  queued[#queued+1]={'X4AuthorityRegistryAvailabilitySnapshot.Result',
   table.concat({payload.request_id,payload.run_id,'Inspect',payload.first,payload.count,
    'Inspected','110',binding,'100','100'},'|')}
 elseif screen=='X4AuthorityShipRegistry' then
  assert(operation=='Status')
  queued[#queued+1]={'X4AuthorityShipRegistry.Correlated',payload.request_id..'|Epoch|'..string.rep('1',32)}
  queued[#queued+1]={'X4AuthorityShipRegistry.Correlated',payload.request_id..'|Count|3'}
 else error('unexpected MD request') end
 return true
end

dofile(folder..'/config.lua')
dofile(folder..'/client_load_barrier.lua')
dofile(folder..'/x4_client_load_engine.lua')
dofile(folder..'/authority_registry_availability_snapshot.lua')
dofile(folder..'/registry_checkpoint_gate.lua')
local gate = X4LAN_AUTHORITY_REGISTRY_CHECKPOINT_GATE
local new_gate = gate.new
function gate.new(options)
 assert(options.timeout == (tonumber(arg[3]) or 110), 'wrong inspection budget')
 return new_gate(options)
end

dofile(folder..'/client_registry_inspect.lua')
dofile(folder..'/recovery_registry_status.lua')
dofile(folder..'/authority_download_load_probe.lua')
frame();frame()
assert(logs[1] and logs[1]:find('load_requested|',1,true))
-- X4 reloads UI scripts after loading. Keep the underlying saved world state.
dofile(folder..'/authority_download_load_probe.lua')
frame()
assert(paused and marker_checks==0, 'first playable frame must request pause without claiming readiness')
local header_time_changed=false
local event_failed=false
for _=1,20 do
 clock=clock+.2
 local delivery=queued;queued={}
 for _,event in ipairs(delivery) do
  local ok=pcall(assert(callbacks[event[1]]),nil,event[2])
  if not ok then
   assert(mode=='time_changed_header')
   event_failed=true
  end
 end
 frame()
 if mode=='time_changed' and #requests==1 then game_time=game_time+1 end
 if mode=='time_changed_header' and requests[#requests]=='Status' and not header_time_changed then
  game_time=game_time+1;header_time_changed=true
 end
end
local inspect, header, pause, error_row
for index,row in ipairs(logs) do
 if row:find('AUTH_REGISTRY_FULL_INSPECT ',1,true) then inspect=index end
 if row:find('AUTH_RECOVERY_REGISTRY ',1,true) then header=index end
 if row:find('pause_observed|',1,true) then pause=index end
 if row:find('AUTH_DOWNLOAD_LOAD ERROR',1,true) then error_row=index end
end
if mode=='ready' then
 assert(inspect and header and pause and inspect<header and header<pause)
 assert(requests[1]=='Inspect' and requests[2]=='Status' and not error_row)
else
 assert(not header and not pause and error_row)
 assert(requests[1]=='Inspect')
 if mode=='time_changed_header' then assert(requests[2]=='Status')
 else assert(requests[2]==nil) end
 if mode=='time_changed_header' then assert(event_failed) end
end
print('PASS full registry overlay '..mode)
