local menu=true;local valid=false;local paused=false;local loaded=0;local pauses=0;local refresh=0;local logs={}
local C={GetSaveFolderPath=function() return '/private/save' end,
 IsStartmenu=function() return menu end,IsPlayerValid=function() return valid end,
 IsGamePaused=function() return paused end,IsSaveListLoadingComplete=function() return true end,
 ReloadSaveList=function() refresh=refresh+1 end}
package.preload.ffi=function() return {C=C,NULL={},cdef=function() end,string=function(s) return s end} end
GetSaveList=function()return {{filename='save_009'}}end
LoadGame=function(s)assert(s=='save_009');loaded=loaded+1 end
Pause=function() pauses=pauses+1 end
DebugError=function(s)logs[#logs+1]=s end
local frame
SetScript=function(name,fn)assert(name=='onUpdate');frame=fn end
X4LAN_CONFIG={test_profile='45841',authority_download_load=true,authority_load_save_path='/private/save',
 authority_ticket={world_epoch='epoch',connection_handle=1,baseline_id=2,load_generation=3,
 checkpoint_id='checkpoint-'..string.rep('a',32),sha256=string.rep('b',64),attempt_id=string.rep('c',32),slot='save_009'}}
dofile('src/authority/lua/client_load_barrier.lua');dofile('src/authority/lua/x4_client_load_engine.lua')
dofile('tools/dev/authority_download_load_probe.lua')
frame();frame();assert(refresh==1 and loaded==1 and #logs==1 and logs[1]:find('load_requested|',1,true))
-- Simulate the Lua reinitialization by loading the probe afresh.
menu=false;valid=true;dofile('tools/dev/authority_download_load_probe.lua');frame();frame();frame()
assert(pauses==1 and #logs==1);paused=true;frame();frame()
assert(#logs==2 and logs[2]:find('pause_observed|',1,true))

-- A Windows path can use different separator spellings on each side. The
-- probe still requires an exact match after only this normalization.
local function check_path(actual,configured,accepted)
 menu=true;valid=false;paused=false;logs={};loaded=0;refresh=0
 C.GetSaveFolderPath=function() return actual end
 X4LAN_CONFIG.authority_load_save_path=configured
 dofile('tools/dev/authority_download_load_probe.lua')
 frame();frame()
 if accepted then
  assert(refresh==1 and loaded==1 and #logs==1 and logs[1]:find('load_requested|',1,true))
 else
  assert(refresh==0 and loaded==0 and #logs==1 and logs[1]=='[X4LAN] AUTH_DOWNLOAD_LOAD ERROR')
 end
end
check_path('C:\\Users/Player\\45841/save\\','C:/Users\\Player/45841\\save///',true)
check_path('C:\\Users\\Player\\45841\\save','C:/Users/Other/45841/save',false)
check_path('C:\\Users\\Player\\45841\\save','c:/Users/Player/45841/save',false)
check_path('','',false)
check_path('/','/',false)
check_path('/private/save',nil,false)
check_path('/private/save','/private/save\0unexpected',false)

dofile('src/authority/lua/replica_motion_binding.lua')
local ffi=require('ffi')
ffi.os='Windows'
X4LAN_CONFIG.test_profile='45850'
local alternate='C:/Users/Player/45850/save'
check_path(alternate,alternate,false)
X4LAN_CONFIG.authority_windows_company_profile=true
check_path('C:\\Users\\Player\\45850\\save\\',alternate,true)
check_path('C:/Users/Other/45850/save',alternate,false)
check_path('C:/Users/Player/45851/save',alternate,false)
check_path('/home/Player/45850/save','/home/Player/45850/save',false)
ffi.os='Linux'
check_path(alternate,alternate,false)
