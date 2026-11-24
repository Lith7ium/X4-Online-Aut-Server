-- Disposable local test only. No save commands, multiplayer transport, or trust proof.
local cfg=X4LAN_CONFIG
if not cfg or not cfg.authority_participant_port then return end
local ffi=require('ffi');ffi.cdef[[bool IsPlayerValid(void);]]
local net,state,engine,next_hello,failed=nil,nil,nil,0,false
local company_motion
local function frame()
 if company_motion and not ffi.C.IsPlayerValid() then company_motion:tick() end
 if not ffi.C.IsPlayerValid() then return end
 if not state then
  engine=assert(X4LAN_AUTHORITY_X4_CONTROL_ENGINE.new({enabled=true,test_profile='45841'}))
  local lib=X4LAN_LOCAL_UDP_MODULE.load(ffi.os,package.loadlib)
  net=lib.open('127.0.0.1',cfg.authority_participant_port)
  if cfg.authority_company_motion then
   company_motion=assert(X4LAN_COMPANY_MOTION_SOURCE.new_x4(cfg.authority_company_motion,
     cfg.authority_load_save_path,cfg.authority_registry_snapshot,lib))
  end
  state=assert(X4LAN_AUTHORITY_CONTROL_STATE.new({enabled=true,owner='isolated-client',
    world_epoch=cfg.authority_ticket.world_epoch,timeout_seconds=120},engine,function(row)
    if company_motion and row.operation=='resume' and row.status=='confirmed' then
     company_motion:set_running(true)
    end
    assert(net:send('RECEIPT|'..cfg.authority_participant_lease..'|'..cfg.authority_ticket.world_epoch..'|'..
      string.format('%.0f',row.request_id)..'|'..row.operation..'|'..row.status))
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
  if company_motion and op=='pause' then company_motion:set_running(false) end
  assert(state:submit('isolated-client',epoch,tonumber(id),op))
 end
 assert(state:on_update())
 if company_motion then company_motion:tick() end
end
SetScript('onUpdate',function()
 if failed then return end
 local ok,err=pcall(frame)
 if not ok then failed=true;DebugError('[X4LAN] AUTH_PARTICIPANT ERROR '..tostring(err)) end
end)
