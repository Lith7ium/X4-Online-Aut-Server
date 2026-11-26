-- Verify the isolated Company velocity cutoff ordering with synchronous modelled MD responses.
local implementation=assert(loadfile(arg[1]))()
local clock=1
local callback
local anchor_count=0
local ops, output={},{}
local epoch=string.rep('1',32)
local bound={epoch=epoch,slot='cockpit',serial=1,index=1073,actor='0x42',sector_macro='cluster_06_sector002_macro',
    code='HVC-420',generation=string.rep('2',32)}
local function feed(a,b)
    local extra=''
    if a.op=='apply' then extra='|100,0,0' end
    if a.op=='zero' then extra='|20500,100000,-1500|100,0,0' end
    if a.op=='stop' then extra='|20500,100000,-1500|0,0,0' end
    local velocity=a.op=='apply' and '100,0,0' or '0,0,0'
    callback(nil,table.concat({a.request,b,bound.actor,bound.sector_macro,'0x43',clock,
        '20500,100000,-1500',velocity,'1,0,0','0,1,0','0,0,1'},'|')..extra)
end
local adapter=implementation.new({enabled=true,pinned={epoch=epoch,issued_count=5048},own_index=1074,
    guard=function()return true end,paused=function()return false end,
    clock=function()return clock end,game=function()return clock end,
    observer={is_active_pilot=function() return true end,matches=function() return true end},
    register=function(fn) callback=fn end, record=function(row) output[#output+1]=row end,
    anchor=function() anchor_count=anchor_count+1;return true end,
    issue=function(args)
        if args.op=='zero' then assert(anchor_count==1) end
        ops[#ops+1]=args.op
        feed(args,({baseline='baseline',apply='dispatched',zero='zeroed',stop='released',
                    release_readback='release_readback'})[args.op])
        return true
    end,
})
local function sample(n,velocity)
    return {version=3,epoch=epoch,index=1073,generation=bound.generation,sector_macro=bound.sector_macro,
        sequence=n,capture_time=clock,source_time=clock,real_velocity=velocity}
end
assert(adapter:sample(bound,sample(1)))
clock=1.1
assert(adapter:sample(bound,sample(2,{x=100,y=0,z=0})))
clock=6.101
assert(adapter:tick() and output[3].before_sector_velocity.x==100 and output[3].sector_velocity.x==0)
clock=6.16
assert(adapter:tick() and output[4].before_sector_velocity.x==0)
clock=6.22
assert(adapter:tick() and output[5].observed_near_zero)
assert(anchor_count==1 and table.concat(ops,',')=='baseline,apply,zero,stop,release_readback')
assert(output[2].dispatched_sector_velocity.x==100)
for _, index in ipairs({1,3,4,5}) do
    assert(output[index].dispatched_sector_velocity==nil)
end
print('silence -> anchor -> zero -> release -> independent stock read order passes')
