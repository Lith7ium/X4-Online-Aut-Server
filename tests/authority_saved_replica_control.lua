-- Pure controller tests: no X4, sockets or engine mutation.
local module=dofile('src/authority/lua/saved_replica_control.lua')
local mode=assert(os.getenv('CONTROL_CASE'))
local now,game=1,1
local requests,faults={},{}
local healthy=true
local options={enabled=true,binding={actor_key='41',sector_key='43',zone_key='45',index=1318,
 epoch='4ba7be438ebb04ec5bb85fdb95b5a488',sector_macro='cluster_06_sector001_macro'},nonce=string.rep('a',32),timeout=4,
 clock=function()return now end,game=function()return game end,guard=function()return healthy end,
 inspect=function()return true end,issue=function(action,args)requests[#requests+1]={action=action,args=args};return true end,
 fault=function(reason,uncertain)faults[#faults+1]={reason,uncertain}end}
if mode=='mfq' or mode=='mfq_mismatched' then
 options.fixture='recovery_mfq_419'
 if mode=='mfq_mismatched' then assert(not pcall(module.new,options));return end
 options.binding.index=943;options.binding.epoch='26a1a365c66788aa06f0aeb9e18ff62e'
end
local controller=assert(module.new(options))
if mode=='mfq' then
 assert(controller:prepare() and #requests==1)
 assert(requests[1].args.fixture=='recovery_mfq_419' and requests[1].args.index==943)
 return
end
local function step(kind,action,advance)
 local last=requests[#requests];assert(last and (not action or last.action==action))
 assert(controller:event({request_id=last.args.request_id,kind=kind}))
 now=now+(advance or .11);game=game+(advance or .11)
 assert(controller:tick())
 if requests[#requests]==last and (controller:state()=='preparing' or controller:state()=='releasing') then
  now=now+.11;game=game+.11;assert(controller:tick())
 end
end
if mode=='disabled' then assert(not module.new({}));return end
if mode=='invalid_fixture' then options.binding.index=1319;assert(not pcall(module.new,options));return end
if mode=='pre_guard' then healthy=false;assert(not controller:prepare() and #requests==0 and not controller:outcome_uncertain());return end
assert(controller:prepare() and controller:state()=='preparing' and controller:outcome_uncertain())
if mode=='duplicate' then
 local value={request_id=requests[1].args.request_id,kind='Created'}
 assert(controller:event(value));assert(not controller:event(value));assert(faults[1][2]);return
elseif mode=='wrong_request' then
 assert(not controller:event({request_id='wrong',kind='Created'}));assert(controller:outcome_uncertain());return
elseif mode=='wrong_order' then
 assert(controller:event({request_id=requests[1].args.request_id,kind='Held'}));assert(not controller:tick());return
elseif mode=='timeout' then now=6;assert(not controller:tick() and controller:outcome_uncertain());return
elseif mode=='backward_game' then game=.9;assert(not controller:tick());return
elseif mode=='identity' then options.binding.actor_key='42';assert(not controller:tick());return
end
step('Created','Prepare')
step('Waiting','Probe')
step('Started','Probe')
assert(requests[#requests].action=='Probe')
step('Started','Probe')
assert(requests[#requests].action=='Hold')
step('Held','Hold')
assert(controller:state()=='prepared' and not controller:outcome_uncertain())
if mode=='expiry' then game=30;assert(not controller:tick() and controller:outcome_uncertain());return end
if mode=='revalidate' then
 assert(controller:prepare());assert(requests[#requests].action=='Check')
 step('Held','Check');assert(controller:state()=='prepared');return
end
assert(controller:release() and controller:state()=='releasing' and controller:outcome_uncertain())
step('Released','Release')
step('Waiting','Sample')
step('Restored','Sample')
assert(controller:state()=='releasing')
step('Restored','Sample')
assert(controller:state()=='released' and not controller:outcome_uncertain())
if mode=='late' then assert(not controller:event({request_id=requests[#requests].args.request_id,kind='Restored'}));return end
assert(mode=='success')
assert(not controller:prepare() and controller:state()=='failed')
