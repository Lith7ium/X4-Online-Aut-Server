-- Inert factory. Caller owns an isolated profile, the UDP endpoint and the
-- event/update registrations. UDP loss faults the supervisor; no retransmit.
local M={}
function M.new(cfg,engine,net)
 if type(cfg)~='table' or cfg.enabled~=true then return nil,'disabled' end
 assert(type(cfg.lease)=='string' and #cfg.lease==32 and cfg.lease:match('^[0-9a-f]+$'))
 assert(type(cfg.world_epoch)=='string' and #cfg.world_epoch==32 and cfg.world_epoch:match('^[0-9a-f]+$'))
 assert(cfg.empty_owned_slot==true and net~=nil and type(net.send)=='function' and type(net.receive)=='function')
 local owner='isolated-supervisor';local epoch=cfg.world_epoch;local lease=cfg.lease
 assert(cfg.marker_required==nil or type(cfg.marker_required)=='boolean')
 if cfg.marker_required then assert(type(cfg.store_marker)=='function') end
 local marker_timeout=cfg.marker_timeout_seconds
 if marker_timeout==nil then marker_timeout=120 end
 assert(type(marker_timeout)=='number' and marker_timeout>0 and marker_timeout<=330,
  'invalid marker timeout')
 local verified=false;local faulted=false;local state;local waiting_marker=nil
 local function send(s) assert(net:send(s)==true,'control send failed') end
 local function report(row)
  if row.status=='failed' then faulted=true end
  send('RECEIPT|'..lease..'|'..epoch..'|'..string.format('%.0f',row.request_id)..'|'..row.operation..'|'..row.status)
 end
 state=assert(X4LAN_AUTHORITY_CONTROL_STATE.new({enabled=true,owner=owner,world_epoch=epoch,
  enable_verified_reuse=true,timeout_seconds=120},engine,report))
 local bridge={state=state}
 local function submit_save(number)
  local params={slot='save_009',exclusive_owner=true}
  if verified then params.overwrite_verified_owned_slot=true else params.empty_owned_slot=true end
  local ok,reason=state:submit(owner,epoch,number,'save',params)
  if not ok then faulted=true;return false,reason end
  return true
 end
 function bridge:marker_stored(marker)
  local clock_ok,now=pcall(engine.real_time)
  if not clock_ok or faulted or not waiting_marker or marker~=waiting_marker.marker or type(now)~='number'
    or now~=now or now>=waiting_marker.deadline then
   faulted=true;return false,'marker_receipt'
  end
  -- The native owner exposes readiness so a queued packet cannot be skipped
  -- by this asynchronous callback. Legacy injected transports without
  -- readiness retain their old behaviour. This sampled check is not an atomic
  -- fence against a packet arriving after the check or the start of save.
  if net.pending~=nil then
   local pending_marker=waiting_marker
   local checked,packet_waiting=pcall(net.pending,net)
   if not checked or packet_waiting~=false or faulted or waiting_marker~=pending_marker then
    faulted=true;return false,'marker_pending_packet'
   end
   clock_ok,now=pcall(engine.real_time)
   if not clock_ok or faulted or waiting_marker~=pending_marker or type(now)~='number' or
      now~=now or now>=pending_marker.deadline then
    faulted=true;return false,'marker_pending_packet'
   end
  end
  local number=waiting_marker.number;waiting_marker=nil
  return submit_save(number)
 end
 function bridge:update()
  if faulted then return false,'faulted' end
  for _=1,16 do
   -- A transport exception must also fence a marker callback already in
   -- flight; the outer update owner alone cannot cancel that callback.
   local received,packet=pcall(net.receive,net)
   if not received then faulted=true;return false,'receive' end
   if not packet then break end
   if waiting_marker then faulted=true;return false,'marker_pending' end
   local typ,l,e,id,op,marker=packet:match('^([A-Z]+)|([0-9a-f]+)|([0-9a-f]+)|(%d+)|([a-z]+)|([0-9a-f]+)$')
   if not typ then typ,l,e,id,op=packet:match('^([A-Z]+)|([0-9a-f]+)|([0-9a-f]+)|(%d+)|?([a-z]*)$') end
   if l~=lease or e~=epoch or not id or #id>16 or id:match('^0') then faulted=true;return false,'packet' end
   local number=tonumber(id)
   if not number or number>9007199254740991 then faulted=true;return false,'request_id' end
   if typ=='CMD' and (op=='pause' or op=='save' or op=='resume') then
    if op=='save' then
     if cfg.marker_required then
      if not marker or #marker~=32 then faulted=true;return false,'marker_required' end
      local now=engine.real_time()
      if type(now)~='number' or now~=now then faulted=true;return false,'clock' end
      waiting_marker={number=number,marker=marker,deadline=now+marker_timeout}
      local ok=pcall(cfg.store_marker,marker)
      if not ok then faulted=true;return false,'marker_store' end
     else
      if marker then faulted=true;return false,'unexpected_marker' end
      local ok,reason=submit_save(number)
      if not ok then return false,reason end
     end
    else
     if marker then faulted=true;return false,'unexpected_marker' end
     local ok,reason=state:submit(owner,epoch,number,op,nil)
     if not ok then faulted=true;return false,reason end
    end
   elseif typ=='VERIFY' and op=='' then
    local ok,reason=state:confirm_save(owner,epoch,number,true)
    if not ok then faulted=true;return false,reason end
    verified=true;send('VERIFIED|'..lease..'|'..epoch..'|'..id)
   else faulted=true;return false,'packet' end
  end
  if waiting_marker then
   local now=engine.real_time()
   if type(now)~='number' or now~=now or now>=waiting_marker.deadline then
    faulted=true;return false,'marker_timeout'
   end
  end
  local ok,reason=state:on_update()
  if not ok then faulted=true end
  return ok,reason
 end
 return bridge
end
X4LAN_AUTHORITY_LOCAL_CONTROL_BRIDGE=M
return M
