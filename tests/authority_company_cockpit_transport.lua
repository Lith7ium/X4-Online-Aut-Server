-- Replay real launcher loopback packets with an inspectable native setter.
local epoch, sector, prefix = ('a'):rep(32), 'sector_a', 'C|test|test|'
X4LAN_MOTION_BUFFER = dofile(_ROOT .. '/src/authority/lua/motion_buffer.lua')
dofile(_ROOT .. '/src/authority/lua/motion_playback.lua')
dofile(_ROOT .. '/src/authority/lua/company_hull_playback.lua')
local native = dofile(_ROOT .. '/src/authority/lua/company_hull_playback_x4.lua')
local cockpit = dofile(_ROOT .. '/src/authority/lua/company_cockpit_playback.lua')
local stream = dofile(_ROOT .. '/src/authority/lua/company_observer_stream.lua')

local observed = {epoch=epoch, sector_macro=sector, index=2, actor='0x200', code='TGT-002'}
local now, rostered, writes, fail_setter = 0, false, 0, false
local position = {x=0,y=0,z=0,yaw=0,pitch=0,roll=0}
local packets, events = {}, {}
local reader = {
    tick=function() return observed end,
    matches=function(_, ep, sec, index, actor, code)
        return ep==epoch and sec==sector and index==2 and actor=='0x200' and code=='TGT-002'
    end,
    is_active_pilot=function() return rostered end,
    close=function() end,
}
local clock = function() return now end
local adapter = native.new({enabled=true,pinned={epoch=epoch,issued_count=2},own_index=1,
    observer=reader,guard=function() return true end}, {
    cockpit=function() return {actor=1,sector=3,zone=4} end,
    target=function() return {actor=512,sector=3,zone=4,sector_macro=sector,code='TGT-002'} end,
    read=function() return position end,
    destination_zone=function() return 4 end,
    write=function(actor, _, requested)
        assert(actor==512)
        writes = writes + 1
        position = requested
        return not fail_setter
    end,
})
local playback = cockpit.new({enabled=true,own_index=1,adapter=adapter,continuous_playback=true,
    clock=clock,game=clock,paused=function() return false end,record=function() return true end,
    fault=function() end,
    on_event=function(kind) events[kind]=(events[kind] or 0)+1 end})
local receiver = stream.new({cockpit=reader, map={tick=function() end,close=function() end}},
    epoch,{send=function() return true end,receive=function() return table.remove(packets,1) end},
    prefix,clock,playback,{
        set_pilot_roster=function(_, indices) rostered=indices[1]==2; return true end,
    })
return {
    rostered=function() return rostered end,
    ingest=function(packet) packets[#packets+1] = prefix..packet end,
    step=function(at) now=at; receiver:tick(true) end,
    writes=function() return writes end,
    x=function() return position.x end,
    event=function(kind) return events[kind] or 0 end,
    stats=function() return playback:stats() end,
    fail_setter=function() fail_setter=true end,
}
