-- Disposable local test only. No save commands, multiplayer transport, or trust proof.
local cfg=X4LAN_CONFIG
if not cfg or not cfg.authority_participant_port then return end
local ffi=require('ffi');ffi.cdef[[bool IsPlayerValid(void);]]
local net,state,engine,next_hello,failed=nil,nil,nil,0,false
local company_motion, company_purchase, company_fleet
local admitted_seat
local running, sources_running=false,false
local function set_sources_running(value)
 if sources_running==value then return end
 sources_running=value
 if company_motion then company_motion:set_running(value) end
 if company_purchase then company_purchase:set_running(value) end
 if company_fleet then company_fleet:set_running(value) end
end

local function stop_undock()
 if company_purchase and company_purchase.stop_undock then company_purchase:stop_undock() end
end

local function frame()
 if not ffi.C.IsPlayerValid() then
  if admitted_seat then stop_undock() end
  set_sources_running(false)
  return
 end
 if not state then
  local control_config={enabled=true,test_profile='45841'}
  if cfg.authority_windows_company_profile==true then
   control_config.authority_windows_company_profile=true
   control_config.test_profile=cfg.test_profile
   control_config.authority_load_save_path=cfg.authority_load_save_path
  end
  engine=assert(X4LAN_AUTHORITY_X4_CONTROL_ENGINE.new(control_config))
  local lib=X4LAN_LOCAL_UDP_MODULE.load(ffi.os,package.loadlib)
  net=lib.open('127.0.0.1',cfg.authority_participant_port)
  if cfg.authority_company_motion then
   company_motion=assert(X4LAN_COMPANY_MOTION_SOURCE.new_x4(cfg.authority_company_motion,
     cfg.authority_load_save_path,cfg.authority_registry_snapshot,lib,
     control_config.authority_windows_company_profile and control_config.test_profile or nil))
  end
  if cfg.authority_company_purchase then
   company_purchase=assert(X4LAN_COMPANY_PURCHASE_SOURCE.new_x4(
     cfg.authority_company_purchase,cfg.authority_registry_snapshot,lib))
  end
  if cfg.authority_company_fleet then
   company_fleet=assert(X4LAN_COMPANY_FLEET_SOURCE.new_x4(
     cfg.authority_company_fleet,cfg.authority_registry_snapshot,lib))
  end
  state=assert(X4LAN_AUTHORITY_CONTROL_STATE.new({enabled=true,owner='isolated-client',
    world_epoch=cfg.authority_ticket.world_epoch,timeout_seconds=120},engine,function(row)
    assert(net:send('RECEIPT|'..cfg.authority_participant_lease..'|'..cfg.authority_ticket.world_epoch..'|'..
      string.format('%.0f',row.request_id)..'|'..row.operation..'|'..row.status))
    if row.operation=='resume' and row.status=='confirmed' then
     local selected=X4LAN_AUTHORITY_SELECTED_SEAT
     if admitted_seat and selected~=admitted_seat then stop_undock() end
     if selected and not admitted_seat then
      assert(selected:admit(), 'Company seat resume unconfirmed')
      admitted_seat=selected
     end
     running=true
    end
   end))
 end
 local now=GetCurRealTime()
 if now>=next_hello then
  assert(net:send('HELLO|'..cfg.authority_participant_lease..'|'..cfg.authority_ticket.world_epoch))
  next_hello=now+.25
 end
 for _=1,16 do
  local packet=net:receive();if not packet then break end
  local lease,epoch,id,op=packet:match('^CMD|([0-9a-f]+)|([0-9a-f]+)|(%d+)|([a-z]+)$')
  assert(lease==cfg.authority_participant_lease and epoch==cfg.authority_ticket.world_epoch)
  assert(id and #id<=16 and not id:match('^0') and (op=='pause' or op=='resume'))
  if op=='pause' then running=false;set_sources_running(false) end
  assert(state:submit('isolated-client',epoch,tonumber(id),op))
 end
 assert(state:on_update())
 if running and admitted_seat then assert(admitted_seat:tick(), 'Company seat update failed') end
 local helm_running=running and admitted_seat~=nil and admitted_seat:ready()
 if admitted_seat and X4LAN_AUTHORITY_SELECTED_SEAT~=admitted_seat then stop_undock() end
 if running and sources_running and not helm_running then stop_undock() end
 set_sources_running(helm_running)
 if company_fleet then
  company_fleet:prepare(admitted_seat)
  company_fleet:tick()
 end
 if company_motion then company_motion:tick() end
 if company_purchase then
  if helm_running then company_purchase:prepare_seat() end
  company_purchase:tick()
 end
end
SetScript('onUpdate',function()
 if failed then return end
 local ok,err=pcall(frame)
 if not ok then
  failed=true
  pcall(stop_undock)
  if company_motion then pcall(company_motion.set_running,company_motion,false) end
  if company_purchase then pcall(company_purchase.set_running,company_purchase,false) end
  if company_fleet then pcall(company_fleet.set_running,company_fleet,false) end
  DebugError('[X4LAN] AUTH_PARTICIPANT ERROR '..tostring(err))
 end
end)
