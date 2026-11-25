"""Strict 12-fixture evidence and isolated probe tests; no X4 process."""
from pathlib import Path
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
from authority_fixture_fleet_support import IDS, validate


def records():
    source, guest = [], []
    for ident in IDS:
        row = f'[X4LAN] NATIVE_GATE AUTHORITY_FLEET_ENROLL tick=30 id={ident} sector=' + 'a'*64
        source.append(row); guest.append(row)
        for seq, tick in enumerate((90, 150, 210, 270), 1):
            source.append(f'[X4LAN] NATIVE_GATE AUTHORITY_FLEET_SOURCE tick={tick} id={ident} seq={seq} time={tick}.0 x={seq} y=2 z=3')
            guest.append(f'[X4LAN] NATIVE_GATE AUTHORITY_FLEET_RECEIVE tick={tick} id={ident} request={seq} seq={seq} time={tick}.5 x={seq} y=2 z=3')
    return source, guest


class FleetEvidenceTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'the fleet daemon reads its config from an inherited fd (pass_fds is POSIX-only)')
    def test_sealed_daemon_package_private_pipe_and_udp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); package=root/'authority'; package.mkdir()
            for name in ('__init__.py','ship_state.py','fixture_udp.py','fixture_fleet_udp.py'):
                shutil.copy(ROOT/'src/authority'/name, package/name)
            shutil.copy(ROOT/'tools/dev/authority_fixture_fleet_support.py', root/'authority_fixture_fleet_support.py')
            read_fd, write_fd=os.pipe()
            process=subprocess.Popen([sys.executable,str(root/'authority_fixture_fleet_support.py'),'--config-fd',str(read_fd)],
                                     pass_fds=(read_fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            os.close(read_fd)
            try:
                with os.fdopen(write_fd,'w') as pipe:
                    json.dump({'source':'a'*32,'guest':'b'*32},pipe)
                self.assertTrue(select.select([process.stdout],[],[],5)[0])
                ready=process.stdout.readline()
                self.assertRegex(ready,r'^AUTHORITY_FLEET_READY port=\d+\n$')
                port=int(ready.split('=')[1]); destination=('127.0.0.1',port)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as source, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as guest:
                    source.settimeout(2);guest.settimeout(2)
                    for sock,role,lease in ((source,'SOURCE','a'*32),(guest,'GUEST1','b'*32)):
                        sock.sendto(f'FLEET1|HELLO|{role}|{lease}'.encode(),destination)
                        self.assertEqual(sock.recv(512),f'FLEET1|READY|{role}'.encode())
                    source.sendto(f'FLEET1|PUT|{"a"*32}|fixture-01|test-frame|1|1|2|3'.encode(),destination)
                    for seq in range(1,4):
                        guest.sendto(f'FLEET1|GET|{"b"*32}|fixture-01|test-frame|{seq}'.encode(),destination)
                        try:
                            received=guest.recv(512); break
                        except socket.timeout:
                            pass
                    else:
                        self.fail('no guest position received')
                    self.assertIn(b'|1|1.0|2.0|3.0',received)
            finally:
                process.send_signal(signal.SIGTERM)
                stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0)
            for secret in ('a'*32,'b'*32):
                self.assertNotIn(secret,ready+stdout+stderr)

    def test_12_independent_fixtures_and_age(self):
        source, guest = records()
        result = validate(source, guest, 300)
        self.assertTrue(result['pass'], result)
        self.assertEqual(set(result['fixtures']), set(IDS))
        self.assertEqual(result['fixtures'][IDS[0]]['maximum_capture_age_s'], .5)

    def test_missing_or_misleading_fixture_cannot_pass(self):
        source, guest = records()
        variants = [
            [line for line in guest if 'id=fixture-07 ' not in line],
            [line.replace('sector='+'a'*64, 'sector='+'b'*64, 1) for line in guest],
            [line.replace('x=1 y=2', 'x=9 y=2', 1) for line in guest],
            [line.replace('time=90.5', 'time=93.0', 1) for line in guest],
            guest + [guest[-1]],
            guest + ['[X4LAN] NATIVE_GATE AUTHORITY_FLEET_ERROR tick=299'],
        ]
        for bad in variants:
            self.assertFalse(validate(source, bad, 300)['pass'])
        self.assertFalse(validate(source, guest, 299)['pass'])

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_probe_enrolls_and_samples_all_fixture_ids(self):
        script = r'''
local ffi=require('ffi');ffi.cdef('typedef uint64_t UniverseID;')
X4LAN_SHA256={digest=function() return string.rep('a',64) end}
local logs={};local net={send=function() return true end,receive=function() end,close=function() end}
package.loadlib=function() return function() return {monotonic=function() return 100 end,open=function(host,port) assert(host=='127.0.0.1' and port==1234);return net end} end end
X4LAN_AUTHORITY_SHIP_ENGINE={new=function() return {
 key=function(h) return tostring(h) end,operational=function() return true end,is_ship=function() return true end,
 sector=function() return ffi.new('uint64_t',99) end,
 pose=function(h) return {x=tonumber(h),y=0,z=0} end,
} end}
local wire=assert(loadfile('src/authority/lua/fixture_fleet_wire.lua'))()
X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE=wire
local fake={sent={}}
X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE={new=function(conn,role,lease,frames)
 assert(role=='SOURCE');return {step=function(_,samples)
  local rows={}; for id,p in pairs(samples) do rows[#rows+1]={fixture=id,sequence=1} end; return {},rows end}
end}
local gate={saved_identity=function(v)
 local n=tonumber(v);if n==99 then return string.rep('a',16) end;return string.format('%016x',n) end}
local cfg={native_gate_authority_fleet=true,test_profile='45841',slot='p1',native_gate_authority_fleet_port=1234,
 native_gate_authority_fleet_lease=string.rep('c',32),native_gate_snapshot_fixture={}}
local actors={};for i=1,12 do actors[i]=i;cfg.native_gate_snapshot_fixture[i]=string.format('%016x',i) end
local probe=assert(loadfile('src/authority/lua/fixture_fleet_probe.lua'))()
probe.sample(cfg,30,actors,function(s) logs[#logs+1]=s end,gate)
assert(#logs==24, table.concat(logs,'\n'))
local seen={};for _,s in ipairs(logs) do local id=s:match('AUTHORITY_FLEET_SOURCE tick=30 id=(fixture%-%d%d)');if id then seen[id]=true end end
for i=1,12 do assert(seen[string.format('fixture-%02d',i)]) end
'''
        result = subprocess.run(['luajit', '-'], input=script, text=True, cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('luajit') and shutil.which('cc'),
                         'Linux compiler and LuaJIT required')
    def test_x4_ui_load_order_publishes_wire_and_registers_real_udp_roles(self):
        """Execute UI files for side effects, as X4 does; no mock wire module."""
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            lib = Path(tmp)/'x4lan_net.so'
            subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                            str(ROOT/'src/native/net.c'),'-ldl','-o',str(lib)],check=True,capture_output=True)
            server.bind(('127.0.0.1',0));server.settimeout(2)
            script = r'''
local ffi=require('ffi');ffi.cdef('typedef uint64_t UniverseID;')
X4LAN_SHA256={digest=function() return string.rep('a',64) end}
local real_load=package.loadlib
package.loadlib=function(path,symbol) return real_load(arg[1],symbol) end
X4LAN_AUTHORITY_SHIP_ENGINE={new=function() return {
 key=function(h) return tostring(h) end,operational=function() return true end,is_ship=function() return true end,
 sector=function() return ffi.new('uint64_t',99) end,pose=function(h) return {x=tonumber(h),y=0,z=0} end,
} end}
local gate={saved_identity=function(v) local n=tonumber(v);if n==99 then return string.rep('a',16) end;return string.format('%016x',n) end}
local actors={};local saved={};for i=1,12 do actors[i]=i;saved[i]=string.format('%016x',i) end
X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE=nil
assert(loadfile('src/authority/lua/fixture_fleet_wire.lua'))() -- ignore returned value, like X4 UI
assert(type(X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE)=='table')
for _,slot in ipairs({'p1','p2'}) do
 local logs={};local probe=assert(loadfile('src/authority/lua/fixture_fleet_probe.lua'))()
 local cfg={native_gate_authority_fleet=true,test_profile='45841',slot=slot,native_gate_authority_fleet_port=tonumber(arg[2]),
  native_gate_authority_fleet_lease=string.rep(slot=='p1' and 'a' or 'b',32),native_gate_snapshot_fixture=saved}
 probe.sample(cfg,30,actors,function(s) logs[#logs+1]=s end,gate)
 assert(#logs==12,table.concat(logs,'\n'))
 for _,row in ipairs(logs) do assert(row:find('AUTHORITY_FLEET_ENROLL',1,true) and not row:find('ERROR',1,true)) end
end
'''
            result=subprocess.run(['luajit','-',str(lib),str(server.getsockname()[1])],input=script,text=True,
                                  cwd=ROOT,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            datagrams=[server.recv(512),server.recv(512)]
            self.assertEqual({packet.split(b'|')[2] for packet in datagrams},{b'SOURCE',b'GUEST1'})

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_probe_error_stages_are_bounded_and_never_log_exceptions(self):
        script = r'''
local ffi=require('ffi');ffi.cdef('typedef uint64_t UniverseID;')
X4LAN_SHA256={digest=function() return string.rep('a',64) end}
local fault=''
local net={send=function() return true end,receive=function() end,close=function() end}
package.loadlib=function() if fault=='transport_initial' then error('secret-transport') end
 return function() return {monotonic=function() return 100 end,open=function() return net end} end end
X4LAN_AUTHORITY_SHIP_ENGINE={new=function() return {
 key=function(h) return tostring(h) end,operational=function() return true end,is_ship=function() return true end,
 sector=function() if fault=='sector_handle' then error('secret-sector') end return ffi.new('uint64_t',99) end,
 pose=function() if fault=='sample_position' then error('secret-position') end return {x=1,y=2,z=3} end,
} end}
X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE={new=function() return {step=function()
 if fault=='wire_step' then error('secret-wire') end;return {},{} end} end}
local gate={saved_identity=function(v)
 local n=tonumber(v)
 if n==99 then
  if fault=='sector_saved_lookup' then error('secret-lookup') end
  if fault=='sector_saved_validate' then return string.rep('0',16) end
  return string.rep('a',16)
 end
 if fault=='actor_saved_match' then return string.rep('b',16) end
 return string.format('%016x',n)
end}
local cfg={native_gate_authority_fleet=true,test_profile='45841',slot='p1',native_gate_authority_fleet_port=1234,
 native_gate_authority_fleet_lease=string.rep('c',32),native_gate_snapshot_fixture={}}
local actors={};for i=1,12 do actors[i]=i;cfg.native_gate_snapshot_fixture[i]=string.format('%016x',i) end
for _,expected in ipairs({'transport_initial','actor_saved_match','sector_handle','sector_saved_lookup',
                         'sector_saved_validate','sample_position','wire_step'}) do
 fault=expected;local logs={};local probe=assert(loadfile('src/authority/lua/fixture_fleet_probe.lua'))()
 probe.sample(cfg,29,actors,function(s) logs[#logs+1]=s end,gate)
 probe.sample(cfg,30,actors,function(s) logs[#logs+1]=s end,gate)
 local n=#logs;assert(n>=1)
 local failure=logs[n]
 local fixture=(expected=='transport_initial' or expected=='wire_step') and 0 or 1
 assert(failure==string.format('AUTHORITY_FLEET_ERROR tick=30 stage=%s fixture=%d',expected,fixture),failure)
 for _,row in ipairs(logs) do assert(not row:find('secret',1,true) and not row:find(string.rep('c',32),1,true)) end
 probe.sample(cfg,31,actors,function(s) logs[#logs+1]=s end,gate)
 assert(#logs==n)
end
'''
        result = subprocess.run(['luajit', '-'], input=script, text=True, cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_clock_failures_and_regression_are_fail_closed(self):
        script = r'''
local fault,calls='',0
local ffi={new=function(kind,value) return value end}
package.loaded.ffi=ffi
X4LAN_AUTHORITY_SHIP_ENGINE={new=function() return {
 key=function(h) return tostring(h) end,operational=function() return true end,is_ship=function() return true end,
 sector=function() return 99 end,pose=function() return {x=1,y=2,z=3} end,
} end}
local net={close=function() end}
package.loadlib=function() return function()
 if fault=='clock_symbol' then return {open=function() return net end} end
 return {open=function() return net end,monotonic=function()
  calls=calls+1
  if fault=='clock_call' then error('secret-call-error') end
  if fault=='clock_value' then return nil end
  if fault=='clock_nan' then return 0/0 end
  if fault=='clock_inf' then return math.huge end
  if fault=='clock_regression' and calls>1 then return 99 end
  return 100
 end}
end end
X4LAN_AUTHORITY_FIXTURE_FLEET_WIRE={new=function() return {step=function() return {},{} end} end}
X4LAN_SHA256={digest=function() return string.rep('a',64) end}
local gate={saved_identity=function(v) local n=tonumber(v);if n==99 then return string.rep('a',16) end;return string.format('%016x',n) end}
local cfg={native_gate_authority_fleet=true,test_profile='45841',slot='p1',native_gate_authority_fleet_port=1234,
 native_gate_authority_fleet_lease=string.rep('c',32),native_gate_snapshot_fixture={}}
local actors={};for i=1,12 do actors[i]=i;cfg.native_gate_snapshot_fixture[i]=string.format('%016x',i) end
for _,expected in ipairs({'clock_symbol','clock_call','clock_value','clock_nan','clock_inf','clock_regression'}) do
 fault=expected;calls=0;local logs={};local probe=assert(loadfile('src/authority/lua/fixture_fleet_probe.lua'))()
 probe.sample(cfg,30,actors,function(s) logs[#logs+1]=s end,gate)
 local ordinal=expected=='clock_regression' and 1 or 0
 local phase=(expected=='clock_nan' or expected=='clock_inf') and 'clock_value' or expected
 assert(#logs==1 and logs[1]==string.format('AUTHORITY_FLEET_ERROR tick=30 stage=%s fixture=%d',phase,ordinal),table.concat(logs,'\n'))
 assert(not logs[1]:find('secret',1,true))
end
'''
        result = subprocess.run(['luajit', '-'], input=script, text=True, cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
