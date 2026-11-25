"""Stock server receiver contract and a LuaJIT test of the isolated bridge."""
from pathlib import Path
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / 'src/authority/md/company_ship_pose.xml'
LUA = ROOT / 'src/authority/lua/company_ship_pose.lua'


class CompanyShipPoseTests(unittest.TestCase):
    def test_sector_frame_read_and_only_apply_can_write(self):
        doc = ET.parse(MD).getroot()
        self.assertEqual(doc.get('name'), 'X4AuthorityCompanyShipPose')
        event, = doc.iter('event_ui_triggered')
        self.assertEqual(event.attrib, {'screen': "'X4AuthorityCompanyShipPose'", 'control': "'Request'"})
        process = doc.find("./cues/library[@name='ProcessPose']/actions")
        apply = doc.find("./cues/library[@name='ApplyPose']/actions")
        self.assertEqual(process.find("do_if[@value=\"$action == 'apply'\"]/include_actions").get('ref'), 'ApplyPose')
        warp, = doc.iter('warp')
        self.assertIs(warp, apply.find('do_if/warp'))
        self.assertEqual(warp.attrib, {'object': '$ship', 'sector': '$sector'})
        position, = warp
        self.assertEqual(position.attrib, {axis: f'$args.${axis} * 1m' for axis in 'xyz'})
        self.assertEqual(process.find('do_else/create_position').attrib,
                         {'name': '$pose', 'space': '$sector', 'object': '$ship'})
        self.assertIsNotNone(apply.find("do_if/set_value[@exact=\"'DISPATCHED'\"]"))
        self.assertFalse({'teleport_player', 'save_game', 'set_owner', 'create_order'} & {node.tag for node in doc.iter()})

    def test_saved_captain_requires_live_idle_helm_and_the_same_inspected_ship(self):
        root = ET.parse(MD)
        guard = root.find(".//library[@name='CheckRetainedCaptain']//set_value[@name='$retainedCaptainReady']")
        for required in ("'%s'.[$captain.seed] == $args.$captain_seed", '$ship.pilot == $captain',
                         '$captain.assignedcontrolled == $ship', '$captain.ship == $ship', 'not $captain.isbusy',
                         'not $ship.orders.count', 'not $ship.tradeorders.count', 'not @$ship.tradecomputer.exists',
                         "@$ship.order.id == 'Wait'", '$ship.order.state != orderstate.critical',
                         'not player.controlled', '@player.station.isclass.station',
                         '@State.$crewBinding.$ship == $ship', '@State.$crewBinding.$captain == $captain',
                         '@State.$crewBinding.$sector == $ship.sector', '@State.$crewBinding.$generation == $args.$generation'):
            self.assertIn(required, guard.get('exact'))
        self.assertNotIn("'transfer'", guard.get('exact'))
        self.assertNotIn("'locate'", guard.get('exact'))
        capture = root.find(".//library[@name='CheckShip']//set_value[@name='State.$crewBinding']/..")
        self.assertEqual(capture.get('value'), "$action == 'inspect' and $retainedCaptainReady")
        refusal = root.find(".//library[@name='CheckShip']//set_value[@exact=\"'PILOT_PRESENT'\"]/..")
        self.assertIn('$args.$captain_seed?', refusal.get('value'))
        self.assertIn('not $retainedCaptainReady', refusal.get('value'))

    def test_transfer_consumes_bound_locate_and_requires_one_matching_sector(self):
        doc = ET.parse(MD).getroot()
        request = doc.find("./cues/cue[@name='Request']/actions/do_if")
        consume = request.find("do_if/remove_value[@name='State.$located']")
        self.assertIsNotNone(consume)
        process = doc.find("./cues/library[@name='ProcessPose']/actions")
        transfer = process.find("do_elseif[@value=\"$action == 'transfer'\"]/do_if")
        self.assertTrue(all(field in transfer.get('value') for field in (
            '$locate_id', '$source_frame', '$epoch', '$index', '$generation', '$frame',
            '$x', '$y', '$z', '$located.$sector', '$located.$ship')))
        self.assertEqual(transfer.find('find_sector').attrib, {
            'name': '$targets', 'macro': 'macro.{$args.$frame}', 'multiple': 'true', 'required': 'false'})
        unique = transfer.find('do_if')
        self.assertIn('$targets.count == 1', unique.get('value'))
        self.assertIn('$targets.{1}.macro.id == $args.$frame', unique.get('value'))
        self.assertEqual(unique.find("set_value[@name='$sector']").get('exact'), '$targets.{1}')
        self.assertEqual(unique.find('include_actions').get('ref'), 'ApplyPose')
        self.assertEqual(len(list(doc.iter('warp'))), 1)

        apply = doc.find("./cues/library[@name='ApplyPose']/actions")
        self.assertIn('not $docking', apply.find('do_if').get('value'))
        refusal = apply.find("do_elseif[@value='$coordinatesvalid and $docking']")
        self.assertEqual([(node.get('name'), node.get('exact')) for node in refusal],
                         [('$status', "'DOCKING_OR_STORED'")])

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_bridge_preflight_fences_writes_and_keeps_independent_readback(self):
        script = r'''
local pose = dofile(arg[1])
local lease, epoch, generation, other = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32), ('d'):rep(32)
local frame, actor = 'cluster_06_sector002_macro', '0x20000000000019'
local cfg = {lease=lease, epoch=epoch, allow_apply=true}
local now, ready, paused, live = 0, false, true, true
local callback, pending, received
local sent, actions, reads = {}, 0, 0
local bridge = pose.new(cfg, {
    ready=function() return ready end, clock=function() return now end, paused=function() return paused end,
    register=function(cb) callback=cb return true end,
    issue=function(args) pending=args; actions=actions+1; return true end,
    actor=function(args, id, read)
        assert(id == actor and args.frame == frame)
        if not live then return false end
        if read then reads=reads+1; return true, 1.25, -2, 3, 'WRN-302' end
        return true
    end,
}, {
    send=function(_, row) sent[#sent+1]=row; return true end, close=function() end,
    receive=function() local ret=received; received=nil; return ret end,
})
local function request(key, action, grant, x)
    local id=key:rep(32)
    now=now+0.1
    received=table.concat({'P',lease,action,id,epoch,'7354',grant or generation,frame,
                          action=='inspect' and '0' or actor,x or '1.25','-2','3'}, '|')
    bridge:tick()
end
local function reply(status, xyz, wrong_generation)
    local args=assert(pending)
    pending=nil
    local md=table.concat({args.request_id,epoch,'7354',wrong_generation or args.generation,
                           args.action,status,actor,frame,xyz}, '|')
    callback(nil,md)
    return md
end
local function ends(expected) assert(sent[#sent]:sub(-#expected)==expected, sent[#sent]) end

bridge:tick()
assert(#sent==0)
ready=true
bridge:tick()
ends('|HELLO')
request('0', 'apply')
ends('|BRIDGE_LOCAL_REFUSED|0|'..frame..'|0|0|0|0|0|0|-')
assert(actions==0)
request('1', 'inspect')
assert(actions==1 and pending.x==1.25)
local md=reply('INSPECTED','1|-2|3')
ends('RESULT|'..md..'|1.25|-2|3|WRN-302')
assert(reads==1)
request('2', 'apply')
ends('|BRIDGE_LOCAL_REFUSED|0|'..frame..'|0|0|0|0|0|0|-')
assert(actions==1)
paused=false
request('3', 'apply', other)
assert(actions==1)
request('4', 'apply', generation, '1e999')
assert(actions==1)
live=false
request('5', 'apply')
assert(actions==1)
live=true
request('6', 'apply')
assert(actions==2 and pending.action=='apply')
md=reply('DISPATCHED','0|0|0')
ends('RESULT|'..md..'|0|0|0|-')
assert(reads==1)
paused=true
request('7', 'verify')
md=reply('OBSERVED','1|-2|3')
ends('RESULT|'..md..'|1.25|-2|3|WRN-302')
assert(reads==2)
paused=false
request('8', 'apply')
reply('DISPATCHED','0|0|0',other)
ends('|BRIDGE_MD_INVALID|0|'..frame..'|0|0|0|0|0|0|-')
request('9', 'apply')
ends('|BRIDGE_LOCAL_REFUSED|0|'..frame..'|0|0|0|0|0|0|-')
assert(actions==4)
bridge:close()
request('b','inspect')
assert(actions==4)
'''
        subprocess.run(['luajit', '-', str(LUA)], input=script, text=True, check=True)

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_cross_sector_locate_is_live_and_authorizes_only_one_exact_transfer(self):
        script = r'''
local pose = dofile(arg[1])
local lease, epoch, generation = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local actor, dest = '0x19', 'sector_b'
local function exercise(writable)
    local callback, packet, issue, reply, actual = nil, nil, nil, nil, 'sector_a'
    local sequence = 0
    local api = pose.new({lease=lease, epoch=epoch, allow_apply=writable}, {
        clock=function() return 1 end, ready=function() return true end, paused=function() return false end,
        issue=function(args) issue=args return true end,
        register=function(cb) callback=cb return true end,
        actor=function(_, id, read, frame)
            if frame ~= actual or id ~= actor then return false end
            if read then return true, 1,2,3,'ABC-123' end
            return true
        end
    }, {receive=function() local p=packet;packet=nil;return p end, close=function() end,
        send=function(_,value) reply=value;return true end})
    local function send(action, x, target, grant)
        sequence=sequence+1
        issue,reply=nil,nil
        packet=table.concat({'P',lease,action,string.format('%032x',sequence),epoch,17,grant or generation,
            target or dest,action=='locate' and '0' or actor,x or 500,600,700},'|')
        api:tick()
    end
    local function result(status, frame, position)
        callback(nil, table.concat({issue.request_id,epoch,17,generation,issue.action,status,actor,
                                    frame,position or '1|2|3'},'|'))
    end
    local function locate()
        send('locate'); result('LOCATED',actual)
        assert(reply:find('|LOCATED|'..actor..'|'..actual..'|1|2|3|1|2|3|ABC-123',1,true))
        return issue.request_id
    end
    local function refused()
        assert(not issue and reply:find('BRIDGE_LOCAL_REFUSED',1,true))
    end
    locate(); send('transfer',501); refused()
    locate(); send('transfer',500,'sector_c'); refused()
    locate(); send('transfer',500,dest,('e'):rep(32)); refused()
    locate(); actual='sector_c'; send('transfer'); refused()
    actual='sector_a'
    send('locate'); result('LOCATED','sector_c'); assert(reply:find('BRIDGE_LOCAL_REFUSED',1,true))
    send('transfer'); refused()
    local id=locate()
    send('transfer')
    if writable then
        assert(issue.locate_id==id and issue.source_frame=='sector_a' and issue.frame==dest)
        result('DISPATCHED',dest,'0|0|0')
        assert(reply:find('|DISPATCHED|'..actor..'|'..dest..'|0|0|0|0|0|0|-',1,true))
        send('transfer'); refused()
        actual=dest
        send('verify'); result('OBSERVED',dest)
        assert(reply:find('|OBSERVED|'..actor..'|'..dest..'|1|2|3|1|2|3|ABC-123',1,true))
        locate(); send('transfer'); refused()
    else
        refused()
    end
end
exercise(true)
exercise(false)
'''
        subprocess.run(['luajit', '-', str(LUA)], input=script, text=True, check=True)

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_x4_bridge_distinguishes_bound_docking_from_uncertain_or_late_refusals(self):
        script = r'''
local pose = dofile(arg[1])
local lease, epoch, generation = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local actor, source, dest = '0x19', 'sector_a', 'sector_b'
local callback, issued, reply, packet
local actual_frame, owner, docked, docking, stored = source, 'player', false, false, false
local sequence = 0
local now, logs = 1, {}
local C = {
    IsGamePaused=function() return false end, IsPlayerValid=function() return true end,
    GetSaveFolderPath=function() return '/save' end,
    IsValidComponent=function(id) return id == 25 or id == 41 or id == 42 end,
    IsComponentOperational=function(id) return id == 25 end,
    IsComponentClass=function(id, class) return id == 25 and class == 'ship' or id == 41 and class == 'sector' end,
    GetContextByClass=function(id, class) return id == 25 and class == 'sector' and 41 or 0 end,
    GetPlayerOccupiedShipID=function() return 0 end, GetPlayerControlledShipID=function() return 0 end,
    GetPlayerContainerID=function() return 42 end,
    GetObjectPositionInSector=function() return {x=0,y=0,z=0} end,
    GetObjectIDCode=function() return 'WRN-302' end,
}
package.loaded.ffi = {C=C, typeof=function() end, cdef=function() end, string=function(value) return value end}
X4LAN_RESOLVE_STRING = {parse_hex=tonumber}
X4LAN_REPLICA_MOTION_BINDING = {normalize_profile_save_path=function(value) return value end}
ConvertStringToLuaID, ConvertIDTo64Bit = tonumber, tonumber
function GetComponentData(id)
    if id == 41 then return actual_frame end
    assert(id == 25)
    return owner, docked, docking, stored
end
function AddUITriggeredEvent(_, _, args) issued=args end
function RegisterEvent(_, handle) callback=handle end
function DebugError(line) logs[#logs+1]=line end
local function new_bridge()
    return pose.new_x4({lease=lease, epoch=epoch, port=1, allow_apply=true},
        function() return true end, '/save', {monotonic=function() return now end, open=function()
            return {
                send=function(_, value) reply=value end, close=function() error('unexpected bridge close') end,
                receive=function() local value=packet;packet=nil;return value end,
            }
        end})
end
local bridge = new_bridge()
local function request(action, frame, requested_actor, captain_seed)
    sequence=sequence+1
    issued,reply=nil,nil
    local id=string.format('%032x', sequence)
    local fields={'P',lease,action,id,epoch,'17',generation,frame,requested_actor or actor,1,2,3}
    if captain_seed then fields[#fields+1] = captain_seed end
    packet=table.concat(fields,'|')
    bridge:tick()
    return id
end
local function answer(status, frame, resolved_actor, position)
    callback(nil, table.concat({assert(issued).request_id,epoch,'17',generation,issued.action,status,
                               resolved_actor or actor,frame,position or '0|0|0'},'|'))
end
local function inspect(action)
    request(action=='apply' and 'inspect' or 'locate', action=='apply' and source or dest, '0')
    answer(action=='apply' and 'INSPECTED' or 'LOCATED', source)
end
local function expect_refusal(action, frame, status, resolved_actor, id)
    assert(reply == table.concat({'P',lease,'RESULT',id,epoch,'17',generation,
                                 action,status,resolved_actor,frame,'0|0|0|0|0|0|-'},'|'), reply)
end
for _, seed in ipairs({'-9223372036854775808', '0', '9223372036854775807', '2211373338772438693'}) do
    request('inspect', source, '0', seed)
    assert(issued and issued.captain_seed == seed)
    answer('INSPECTED', source)
    local id = request('apply', source)
    assert(not issued)
    expect_refusal('apply', source, 'BRIDGE_LOCAL_REFUSED', '0', id)
    request('apply', source, actor, seed)
    assert(issued and issued.captain_seed == seed)
    answer('DISPATCHED', source)
end
for _, invalid in ipairs({'01','-0','1e4','+1','9223372036854775808','-9223372036854775809','1|'}) do
    request('inspect', source, '0', invalid)
    assert(not issued and not reply)
end
for _, action in ipairs({'apply', 'transfer'}) do
    local frame = action=='apply' and source or dest
    inspect(action)
    docking=true
    local id=request(action,frame)
    assert(not issued)
    expect_refusal(action, frame, 'DOCKING_OR_STORED', actor, id)
    docking=false
    id=request(action,frame)
    assert(not issued)
    expect_refusal(action, frame, 'BRIDGE_LOCAL_REFUSED', '0', id)

    inspect(action)
    docked=true;stored=nil
    id=request(action,frame)
    assert(not issued)
    expect_refusal(action, frame, 'BRIDGE_LOCAL_REFUSED', '0', id)
    docked=false;stored=false

    inspect(action)
    docked=true
    id=request(action,frame,'0x23')
    assert(not issued)
    expect_refusal(action, frame, 'BRIDGE_LOCAL_REFUSED', '0', id)
    docked=false

    for _, reason in ipairs({'DOCKING_OR_STORED', 'PILOT_PRESENT'}) do
        inspect(action)
        id=request(action,frame)
        answer(reason, frame)
        expect_refusal(action, frame, reason, actor, id)
        id=request(action,frame)
        assert(not issued)
        expect_refusal(action, frame, 'BRIDGE_LOCAL_REFUSED', '0', id)
    end
end
inspect('apply')
stored=true;actual_frame=dest
local id=request('apply',source)
assert(not issued)
expect_refusal('apply',source,'BRIDGE_LOCAL_REFUSED','0',id)
stored=false;actual_frame=source
inspect('apply')
id=request('apply',source)
answer('DISPATCHED',source)
assert(reply:find('|DISPATCHED|'..actor..'|'..source..'|',1,true))
request('verify', source)
docking=true
answer('OBSERVED', source)
expect_refusal('verify', source, 'BRIDGE_LOCAL_REFUSED', '0', issued.request_id)
docking=false

inspect('apply')
id=request('apply',source)
answer('DOCKING_OR_STORED', source, '0x23')
expect_refusal('apply', source, 'BRIDGE_MD_INVALID', '0', id)
-- An invalid MD result fences future writes on this bridge.
inspect('apply')
id=request('apply',source)
assert(not issued)
expect_refusal('apply', source, 'BRIDGE_LOCAL_REFUSED', '0', id)

for _, reason in ipairs({'tick_deadline', 'matching_prefix_after_deadline'}) do
    bridge = new_bridge()
    inspect('apply')
    local timed_out = request('apply', source)
    assert(issued and issued.request_id == timed_out)
    local previous_logs = #logs
    now = now + 1.875
    bridge:tick()
    assert(#logs == previous_logs)
    now = now + 0.375
    if reason == 'tick_deadline' then
        bridge:tick()
    else
        callback(nil, ('f'):rep(32) .. '|unrelated')
        assert(#logs == previous_logs)
        callback(nil, timed_out .. '|unvalidated callback contents')
    end
    expect_refusal('apply', source, 'BRIDGE_MD_TIMEOUT', '0', timed_out)
    assert(#logs == previous_logs + 1)
    assert(logs[#logs] == '[X4LAN] AUTH_COMPANY_POSE_TIMEOUT reason=' .. reason
           .. ' action=apply index=17 elapsed_s=2.25')
    answer('DISPATCHED', source)
    expect_refusal('apply', source, 'BRIDGE_MD_TIMEOUT', '0', timed_out)
    assert(#logs == previous_logs + 1)
    inspect('apply')
    local fenced = request('apply', source)
    assert(not issued)
    expect_refusal('apply', source, 'BRIDGE_LOCAL_REFUSED', '0', fenced)
end
'''
        subprocess.run(['luajit', '-', str(LUA)], input=script, text=True, check=True)


if __name__ == '__main__':
    unittest.main()
