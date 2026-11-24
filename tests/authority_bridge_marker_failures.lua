-- Synthetic controller and MD callbacks: failed/missing marker must never save.
assert(loadfile('src/authority/lua/control_state.lua'))()
assert(loadfile('src/authority/lua/local_control_bridge.lua'))()
local lease,epoch,marker=string.rep('a',32),string.rep('b',32),string.rep('c',32)
local function scenario(kind)
 local time,paused,saves=0,false,0
 local queue={}
 local net={send=function() return true end,receive=function() return table.remove(queue,1) end}
 local engine={ready=function()return true end,is_paused=function()return paused end,
  game_time=function()return 10 end,real_time=function()return time end,
  pause=function()paused=true end,resume=function()paused=false end,
  saving_possible=function()return true end,save=function()saves=saves+1 end}
 local requested
 local bridge=assert(X4LAN_AUTHORITY_LOCAL_CONTROL_BRIDGE.new({enabled=true,empty_owned_slot=true,
  lease=lease,world_epoch=epoch,marker_required=true,
  marker_timeout_seconds=kind=='provider' and 330 or nil,
  store_marker=function(m)requested=m end},engine,net))
 queue[1]='CMD|'..lease..'|'..epoch..'|1|pause';assert(bridge:update())
 time=.2;assert(bridge:update())
 queue[1]='CMD|'..lease..'|'..epoch..'|2|save|'..marker;assert(bridge:update())
 assert(requested==marker and saves==0)
 if kind=='wrong' then
  local ok=bridge:marker_stored(string.rep('d',32));assert(not ok)
 elseif kind=='timeout' then
  time=121;local ok=bridge:update();assert(not ok)
 elseif kind=='late' then
  time=121;local ok=bridge:marker_stored(marker);assert(not ok)
 elseif kind=='provider' then
  time=300;assert(bridge:update())
  assert(bridge:marker_stored(marker));assert(saves==1)
  time=421;local ok=bridge:update();assert(not ok, 'native save still has a 120-second deadline')
  return
 elseif kind=='duplicate' then
  assert(bridge:marker_stored(marker));assert(saves==1)
  local ok=bridge:marker_stored(marker);assert(not ok);return
 end
 assert(saves==0);local ok=bridge:update();assert(not ok)
end
for _,kind in ipairs({'wrong','timeout','late','duplicate','provider'}) do scenario(kind) end
