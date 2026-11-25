local mod=dofile('src/authority/lua/source_motion_sampler.lua')
local epoch,generation,nonce=string.rep('a',32),string.rep('b',32),string.rep('c',32)
local function rig(options)
 options=options or {};local clock=0;local values={};local outputs={};local faults={};local healthy=true
 local x
 x=assert(mod.new({enabled=true,epoch=epoch,generation=generation,nonce=nonce,max_tracks=10,scan_budget=2,
  recover_expired_pending=options.recover_expired_pending,diagnostics=options.diagnostics,
  interval_from_start=options.interval_from_start,
  interval=.1,timeout=2,min_dt=.02,max_dt=1,include_orientation=options.include_orientation,real_time_motion=options.real_time_motion,clock=function()return clock end,
  guard=function()return healthy end,actor_key=function(a)return tostring(a)end,
  binding_guard=function(row,actor)return actor==row.index+40 end,
  reader_factory=function(row,actor,request)
   return {start=function()return true end,tick=options.reader_tick or function()return true end,
    close=options.reader_close or function()return true end,
    response=function(_,raw)
     assert(raw==request..'|response')
     local v=table.remove(values,1);assert(v)
     local result={epoch=epoch,index=row.index,sequence=row.sequence,sector_macro=v[1],
      game_before_pose=v[2],game_after_pose=v[2],x=v[3],y=0,z=0,local_sector_key=v[4] or '43',descriptor_atomic=false,sector_identity_proven=false}
     if options.real_time_motion then result.capture_before_pose=v[7] or v[6];result.capture_after_pose=v[6] end
     if options.include_orientation then result.yaw=v[5];result.pitch=0;result.roll=0 end
     return true,result
    end}
  end,fault=function(v)faults[#faults+1]=v end}))
 local function register(index)
  return x:register({epoch=epoch,index=index,sequence=index,classification='new_registry_entry'},index+40)
 end
 local function sample(sector,t,pos,local_sector_key,yaw,capture_time,capture_before)
  values[#values+1]={sector,t,pos,local_sector_key,yaw,capture_time,capture_before};clock=clock+.2
  assert(x:tick());local req=x:expected_request();assert(req)
  clock=clock+(options.reply_delay or 0)
  local ok,result=x:event(req..'|response');assert(ok);if result then outputs[#outputs+1]=result end
 end
 return x,register,sample,outputs,faults,function(v)clock=v end,function(v)healthy=v end
end
-- The default waits a whole interval after completion. The opt-in counts
-- time in flight toward the interval and still uses one pending request.
for _,mode in ipairs({'default','start'}) do
 local from_start=mode=='start' and true or nil
 local x,add,sample,_,_,clock=rig({interval_from_start=from_start,reply_delay=.19})
 assert(add(1));sample('sector_a',100,0)
 clock(.40);assert(x:tick() and (x:expected_request()~=nil)==(mode=='start'))
 clock(.50);assert(x:tick() and x:expected_request())
end
-- Full pose follows the short Euler arc and resets both rates across frames.
do
 local x,add,sample,out=rig({include_orientation=true});assert(add(1))
 sample('sector_a',100,0,'43',3.1)
 assert(out[1].version==2 and out[1].orientation.yaw==3.1 and out[1].velocity==nil and out[1].angular_rate==nil)
 out[1].orientation.yaw=0 -- A consumer cannot alter the source's next derivative.
 sample('sector_a',100.5,10,'43',-3.1)
 assert(out[2].velocity.x==20 and math.abs(out[2].angular_rate.yaw-(2*math.pi-6.2)/.5)<1e-9)
 sample('sector_b',101,10,'44',0)
 assert(out[3].velocity==nil and out[3].angular_rate==nil)
 sample('sector_b',101.2,10,'44',3.1)
 assert(out[4].velocity==nil and out[4].angular_rate==nil)
 assert(x:close())
end
-- Same actor, finite-difference velocity; changed sector/time gap resets to unknown.
do
 local x,add,sample,out=rig();assert(add(1))
 sample('sector_a',100,10);assert(out[1].sequence==1 and out[1].velocity==nil)
 sample('sector_a',100.5,20);assert(out[2].sequence==2 and out[2].velocity.x==20)
 sample('sector_b',101,20);assert(out[3].velocity==nil)
 sample('sector_b',103,20);assert(out[4].velocity==nil)
 sample('sector_b',103.5,20);assert(out[5].velocity.x==0)
 assert(x:close())
end
-- A zero displacement is a measured zero vector only with two distinct,
-- sufficiently close source-game timestamps in the same local sector.
do
 local x,add,sample,out=rig();assert(add(1))
 sample('sector_a',100,10);assert(out[1].velocity==nil)
 sample('sector_a',100,10);assert(#out==1)
 sample('sector_a',100.01,10);assert(#out==2 and out[2].velocity==nil)
 sample('sector_a',100.5,10)
 local v=out[3].velocity
 assert(out[3].sequence==3 and v and v.x==0 and v.y==0 and v.z==0)
 sample('sector_a',102,10);assert(out[4].velocity==nil)
 assert(x:close())
end
-- Identical source time is suppressed; an actual sector-frame change resets
-- the rate even when it was first observed at the suppressed timestamp.
do
 local x,add,sample,out=rig();assert(add(1))
 sample('sector_a',100,0,'43');sample('sector_a',100,1,'43');assert(#out==1)
 sample('sector_b',100.5,5,'44');assert(#out==2 and out[2].sequence==2 and out[2].velocity==nil)
 sample('sector_c',100.5,8,'45');assert(#out==2)
 sample('sector_c',101,10,'45');assert(#out==3 and out[3].sequence==3 and out[3].velocity==nil)
 sample('sector_c',101.5,12,'45');assert(#out==4 and out[4].velocity.x==4)
end
-- A changed local sector under the same portable macro is ambiguous on the wire.
do
 local x,add,sample=rig();assert(add(1));sample('sector_a',100,0,'43')
 assert(not pcall(sample,'sector_a',100.5,5,'44') and x:state()=='failed')
end
-- A suppressed intermediate frame cannot hide a change of the last sent frame.
do
 local x,add,sample=rig();assert(add(1));sample('sector_a',100,0,'43')
 sample('sector_b',100,0,'44')
 assert(not pcall(sample,'sector_a',100.5,5,'45') and x:state()=='failed')
end
-- Fair bounded traversal and per-index sequence.
-- Reset retains the sequence while discarding the derivative baseline.
do
 local x,add,sample,out=rig();assert(add(1));sample('sector_a',100,0,'43')
 sample('sector_a',100.5,5,'43');assert(out[2].velocity.x==10)
 assert(x:reset_baseline(1));sample('sector_a',100.5,5,'43');assert(#out==2)
 sample('sector_a',101,10,'43');assert(out[3].sequence==3 and out[3].velocity==nil)
 sample('sector_a',101.5,15,'43');assert(out[4].sequence==4 and out[4].velocity.x==10)
end
do
 local x,add,sample,out=rig();assert(add(1) and add(2))
 sample('sector_a',100,10);sample('sector_b',100.5,20)
 assert(out[1].index==1 and out[2].index==2 and out[1].sequence==1 and out[2].sequence==1)
end
-- Clock/actor loss, foreign response, timeout, duplicate, callback mutation/reentry fence.
do local x,add,sample=rig();assert(add(1));sample('sector_a',100,1);local ok=pcall(sample,'sector_a',99,1);assert(not ok and x:state()=='failed')end
do local x,add=rig();assert(add(1));assert(not add(1) and x:state()=='failed')end
do local x,add=rig();assert(add(1));assert(x:tick());assert(not x:event(string.rep('f',32)..'|response') and x:state()=='failed')end
do local x,add,_,_,_,clock=rig();assert(add(1));assert(x:tick());clock(3);assert(not x:tick() and x:state()=='failed')end
do local x,add,_,_,_,_,health=rig();assert(add(1));health(false);assert(not x:tick() and x:state()=='failed')end
-- Company timeout recovery cannot turn reader failures, clock regressions or
-- failure to close the retired reader into a fresh request.
do
 local x,add=rig({recover_expired_pending=true,reader_tick=function()return false end})
 assert(add(1) and x:tick() and not x:tick() and x:state()=='failed')
end
do
 local x,add,_,_,faults=rig({diagnostics=true,reader_tick=function()return false,'private payload' end})
 assert(add(1) and x:tick() and not x:tick() and x:state()=='failed' and faults[1]=='tick_reader_tick')
end
do
 local x,add,_,_,faults,clock=rig({recover_expired_pending=true,reader_close=function()return false end})
 assert(add(1) and x:tick());clock(2)
 assert(not x:tick() and x:state()=='failed' and faults[1]=='discard_pending')
end
do
 local x,add,_,_,_,clock=rig({recover_expired_pending=true})
 assert(add(1) and x:tick());clock(2)
 local ok,sample,gap=x:tick()
 assert(ok and not sample and gap=='pending_timeout' and not x:expected_request())
 clock(1.5)
 assert(not x:tick() and x:state()=='failed')
end
-- Version 3 rates follow capture seconds, independently of game-clock speed.
for _,game_rate in ipairs({0,.5,1,2}) do
 local x,add,sample,out=rig({real_time_motion=true});assert(add(1))
 sample('sector_a',100,0,'43',nil,10)
 sample('sector_a',100+game_rate*.5,5,'43',nil,10.5)
 assert(out[1].version==3 and out[1].real_velocity==nil and out[1].velocity==nil)
 assert(out[2].capture_time==10.5 and out[2].real_velocity.x==10)
 assert(x:reset_baseline(1));sample('sector_a',100+game_rate*.5,5,'43',nil,11)
 assert(out[3].real_velocity==nil)
 sample('sector_b',100+game_rate,7,'44',nil,11.5);assert(out[4].real_velocity==nil)
end
for _,bad_capture in ipairs({10,9}) do
 local x,add,sample=rig({real_time_motion=true});assert(add(1));sample('sector_a',100,0,'43',nil,10)
 assert(x:reset_baseline(1));assert(not pcall(sample,'sector_a',100,0,'43',nil,bad_capture) and x:state()=='failed')
end
do
 local x,add,sample=rig({real_time_motion=true});assert(add(1));sample('sector_a',100,0,'43',nil,10)
 assert(not pcall(sample,'sector_a',99,0,'43',nil,11) and x:state()=='failed')
end
-- Serialized descriptor readers share one capture clock across all tracks.
-- A later AFTER timestamp must not hide a regressed BEFORE timestamp.
for _,cross_track in ipairs({false,true}) do
 local x,add,sample,out=rig({real_time_motion=true})
 assert(add(1))
 if cross_track then assert(add(2)) end
 sample('sector_a',100,0,'43',nil,10.1,10)
 assert(x:reset_baseline(1))
 local next_after=cross_track and 9.1 or 10.2
 assert(not pcall(sample,'sector_a',100,0,'43',nil,next_after,9))
 assert(x:state()=='failed' and #out==1)
end
-- Adjacent capture intervals may share a boundary, including after reset.
do
 local x,add,sample,out=rig({real_time_motion=true})
 assert(add(1) and add(2))
 sample('sector_a',100,0,'43',nil,10.1,10)
 assert(x:reset_baseline(1))
 sample('sector_a',100,0,'43',nil,10.2,10.1)
 sample('sector_a',100,0,'43',nil,10.3,10.2)
 assert(#out==3 and out[3].index==1 and out[3].sequence==2)
end
assert(not pcall(rig,{real_time_motion=true,include_orientation=true}))
assert(mod.new({enabled=false})==nil)
print('source motion sampler synthetic checks passed')
