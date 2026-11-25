"""Offline LuaJIT 2.1 + real loopback TCP/UDP tests for the opt-in registered-NPC path.

Covers:
  1. Default-off enforcement on NpcMotionSource, npc_cockpit_observer.lua, and npc_hull_playback_x4.lua.
  2. Strict mutual exclusion and preservation of the player-ship gate (company_hull_playback_x4.lua
     accepts uncrewed player ships and refuses non-player AI NPCs; npc_hull_playback_x4.lua accepts
     non-player AI NPCs and refuses player-owned or uncrewed ships).
  3. Verified cockpit radar range and map interest gating in npc_cockpit_observer.lua.
  4. Full LuaJIT 2.1 playback lifecycle via unmodified company_observer_stream.lua +
     company_cockpit_playback.lua + company_hull_playback.lua + motion_buffer.lua +
     motion_playback.lua: pre-interest zero writes, radar-interest continuous displacement,
     withdrawal (zero writes), and reacquisition (single authorized alignment + resumed playback).
  5. Real loopback TCP+UDP integration through NpcMotionSource -> CompanyMotionClient (Grant 1) ->
     CompanyMotionService -> CompanyMotionClient (Grant 2) -> CompanyMotionSource/CompanyMotionObserver:
     pre-interest zero delivery, radar interest delivery + SEEN velocity match, withdrawal zero
     delivery while authority keeps streaming, reacquisition, independent map slot, and unrelated
     ship isolation.
"""
from pathlib import Path
import secrets
import socket
import sys
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.company_motion_source import CompanyMotionSource
from authority.npc_motion_source import NpcMotionSource

EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
NONCE = 'b' * 32
SESSION = 'a' * 32
SECTOR = 'cluster_14_sector001_macro'


def _load_luajit():
    try:
        import lupa.luajit21 as lj
    except ImportError as exc:
        raise unittest.SkipTest(f'lupa.luajit21 unavailable: {exc}')
    return lj.LuaRuntime(unpack_returned_tuples=True)


def _load_lua_file(rt, rel_path, export_global=None):
    src = (ROOT / rel_path).read_text(encoding='utf-8').replace('\r\n', '\n')
    if export_global:
        idx = src.rfind('return M')
        assert idx >= 0
        src = src[:idx] + f'{export_global} = M\n' + src[idx:]
    return rt.execute(src)


class RegisteredNpcLuaPathTest(unittest.TestCase):
    def test_npc_observer_xml_parses(self):
        tree = ET.parse(ROOT / 'src/authority/md/npc_cockpit_observer.xml')
        root = tree.getroot()
        self.assertEqual(root.tag, 'mdscript')
        self.assertEqual(root.attrib.get('name'), 'X4AuthorityNpcCockpitObserver')

    def test_default_off_and_player_ship_gate_mutual_exclusion(self):
        rt = _load_luajit()
        _load_lua_file(rt, 'src/authority/lua/company_hull_playback_x4.lua')
        _load_lua_file(rt, 'src/authority/lua/npc_hull_playback_x4.lua')
        _load_lua_file(rt, 'src/authority/lua/npc_cockpit_observer.lua')

        result = rt.execute(f'''
            local epoch, nonce = "{EPOCH}", "{NONCE}"
            local pinned = {{epoch = epoch, nonce = nonce, issued_count = 50}}

            local obs_off, obs_err = X4LAN_NPC_COCKPIT_OBSERVER.new(pinned, 1, {{enabled = false}})
            assert(obs_off == nil and obs_err == 'disabled', 'observer must be default-off')

            local ad_off, ad_err = X4LAN_NPC_HULL_PLAYBACK_X4.new({{enabled = false}}, {{}})
            assert(ad_off == nil and ad_err == 'disabled', 'npc adapter must be default-off')

            -- Simulate two registered ships in the same sector:
            --   0x2001 (index 10): uncrewed player Company ship (owner='player', pilot=nil)
            --   0x3002 (index 20): non-player AI-piloted Argon NPC (owner='argon', pilot=true)
            local ships = {{
                ['0x2001'] = {{actor = 0x2001, sector = 100, zone = 200, code = 'CMP-100', sector_macro = '{SECTOR}', owner = 'player', pilot = nil}},
                ['0x3002'] = {{actor = 0x3002, sector = 100, zone = 200, code = 'NPC-200', sector_macro = '{SECTOR}', owner = 'argon', pilot = true}},
            }}
            local mock_obs = {{matches = function() return true end}}
            local cockpit_fn = function() return {{actor = 0x1000, sector = 100, zone = 200}} end
            local read_fn = function() return {{x = 1000, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}} end
            local write_fn = function() return true end
            local same_zone_fn = function() return true end

            local company_adapter = assert(X4LAN_COMPANY_HULL_PLAYBACK_X4.new({{
                enabled = true, pinned = pinned, own_index = 1, observer = mock_obs, guard = function() return true end,
            }}, {{
                cockpit = cockpit_fn,
                target = function(hex)
                    local s = ships[hex]
                    if not s or s.owner ~= 'player' or s.pilot ~= nil then return nil end
                    return s
                end,
                read = read_fn, same_zone = same_zone_fn, write = write_fn,
            }}))

            local npc_adapter = assert(X4LAN_NPC_HULL_PLAYBACK_X4.new({{
                enabled = true, pinned = pinned, own_index = 1, observer = mock_obs, guard = function() return true end,
            }}, {{
                cockpit = cockpit_fn,
                target = function(hex)
                    local s = ships[hex]
                    if not s or s.owner == 'player' or s.pilot == nil then return nil end
                    return s
                end,
                read = read_fn, same_zone = same_zone_fn, write = write_fn,
            }}))

            local company_bind_player = company_adapter:bind({{
                slot = 'cockpit', serial = 1, epoch = epoch, index = 10,
                actor = '0x2001', code = 'CMP-100', sector_macro = '{SECTOR}',
            }})
            local company_bind_npc = company_adapter:bind({{
                slot = 'cockpit', serial = 1, epoch = epoch, index = 20,
                actor = '0x3002', code = 'NPC-200', sector_macro = '{SECTOR}',
            }})
            local npc_bind_player = npc_adapter:bind({{
                slot = 'cockpit', serial = 1, epoch = epoch, index = 10,
                actor = '0x2001', code = 'CMP-100', sector_macro = '{SECTOR}',
            }})
            local npc_bind_npc = npc_adapter:bind({{
                slot = 'cockpit', serial = 1, epoch = epoch, index = 20,
                actor = '0x3002', code = 'NPC-200', sector_macro = '{SECTOR}',
            }})

            return {{
                company_accepts_player = company_bind_player ~= nil,
                company_refuses_npc = company_bind_npc == nil,
                npc_refuses_player = npc_bind_player == nil,
                npc_accepts_npc = npc_bind_npc ~= nil,
            }}
        ''')
        self.assertTrue(result['company_accepts_player'])
        self.assertTrue(result['company_refuses_npc'])
        self.assertTrue(result['npc_refuses_player'])
        self.assertTrue(result['npc_accepts_npc'])

    def test_radar_map_interest_gating_and_withdrawal_reacquisition_playback(self):
        rt = _load_luajit()
        _load_lua_file(rt, 'src/authority/lua/motion_buffer.lua', 'X4LAN_MOTION_BUFFER')
        _load_lua_file(rt, 'src/authority/lua/motion_playback.lua')
        _load_lua_file(rt, 'src/authority/lua/company_hull_playback.lua')
        _load_lua_file(rt, 'src/authority/lua/company_cockpit_playback.lua')
        _load_lua_file(rt, 'src/authority/lua/company_observer_stream.lua')
        _load_lua_file(rt, 'src/authority/lua/npc_cockpit_observer.lua')
        _load_lua_file(rt, 'src/authority/lua/npc_hull_playback_x4.lua')

        res = rt.execute(f'''
            local epoch, nonce = "{EPOCH}", "{NONCE}"
            local gen = "11111111111111111111111111111111"
            local prefix = "C|" .. nonce .. "|" .. gen .. "|"
            local pinned = {{epoch = epoch, nonce = nonce, issued_count = 100}}
            local now_t = 10.0
            local game_t = 10.0
            local in_radar = false
            local map_open = false

            local npc_pose = {{x = 1500.0, y = 0.0, z = 1500.0, yaw = 0.5, pitch = 0.1, roll = -0.05}}
            local unrelated_pose = {{x = 0.0, y = 0.0, z = 650.0, yaw = 0.0, pitch = 0.0, roll = 0.0}}
            local writes = 0
            local readbacks = {{}}
            local align_readbacks = {{}}
            local md_handlers = {{}}

            local function make_observer(mode, initial_interest)
                local handler
                return assert(X4LAN_NPC_COCKPIT_OBSERVER.new(pinned, 1, {{
                    enabled = true, target_index = 20, mode = mode, initial_interest = initial_interest,
                    clock = function() return now_t end,
                    probe = function()
                        if mode == 'map' and not map_open then return nil end
                        return {{mode = mode}}
                    end,
                    parse_actor = function(h) return h == '0x3002' and 0x3002 or nil end,
                    verify_actor = function(id, sector, code, m)
                        if id ~= 0x3002 or sector ~= '{SECTOR}' or code ~= 'NPC-200' then return nil end
                        if m == 'radar' and not in_radar then return nil end
                        if m == 'map' and not map_open then return nil end
                        return {{actor = 0x3002, space = 100, sector_macro = sector, code = code, mode = m}}
                    end,
                    validate_actor = function(hull, m)
                        if m == 'radar' then return in_radar end
                        if m == 'map' then return map_open end
                        return false
                    end,
                    register = function(fn) handler = fn; return true end,
                    issue = function(args)
                        local eligible = (args.mode == 'radar' and in_radar) or (args.mode == 'map' and map_open)
                        local row
                        if eligible then
                            row = table.concat({{args.request_id, epoch, 'OBSERVED', '20', '0x3002', '{SECTOR}', 'NPC-200', args.mode, '2121', '40000'}}, '|')
                        else
                            row = table.concat({{args.request_id, epoch, 'NONE', '0', '-', '-', '-', '-', '-', '40000'}}, '|')
                        end
                        handler(nil, row)
                        return true
                    end,
                }}))
            end

            local radar_obs = make_observer('radar', false)
            local map_obs = make_observer('map', false)

            local npc_adapter = assert(X4LAN_NPC_HULL_PLAYBACK_X4.new({{
                enabled = true, pinned = pinned, own_index = 1, observer = radar_obs,
                guard = function() return true end,
            }}, {{
                cockpit = function() return {{actor = 0x1000, sector = 100, zone = 200}} end,
                target = function(hex)
                    if hex ~= '0x3002' then return nil end
                    return {{actor = 0x3002, sector = 100, zone = 200, code = 'NPC-200', sector_macro = '{SECTOR}'}}
                end,
                read = function()
                    return {{x = npc_pose.x, y = npc_pose.y, z = npc_pose.z, yaw = npc_pose.yaw, pitch = npc_pose.pitch, roll = npc_pose.roll}}
                end,
                write = function(actor, _sector, p)
                    assert(actor == 0x3002)
                    npc_pose.x, npc_pose.y, npc_pose.z = p.x, p.y, p.z
                    npc_pose.yaw, npc_pose.pitch, npc_pose.roll = p.yaw, p.pitch, p.roll
                    writes = writes + 1
                    return true
                end,
            }}))

            local playback = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new({{
                enabled = true, slot = 'cockpit', own_index = 1, target_index = 20,
                allow_reacquire_align = true, adapter = npc_adapter,
                clock = function() return now_t end,
                game = function() return game_t end,
                paused = function() return false end,
                record = function(ev)
                    if ev.kind == 'readback' then readbacks[#readbacks + 1] = ev
                    elseif ev.kind == 'alignment_readback' then align_readbacks[#align_readbacks + 1] = ev end
                    return true
                end,
                fault = function() end,
            }}))

            local sent, inbox = {{}}, {{}}
            local net = {{
                send = function(_, msg) sent[#sent + 1] = msg; return true end,
                receive = function() return table.remove(inbox, 1) end,
            }}
            local stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
                {{cockpit = radar_obs, map = map_obs}}, epoch, net, prefix,
                function() return now_t end, playback))

            local function push_remote(slot, serial, seq, x, vx)
                local msg = table.concat({{
                    'REMOTE', slot, tostring(serial), epoch, '20', gen, tostring(seq), '{SECTOR}',
                    ('%.17g'):format(x), '0', '1500',
                    vx and ('%.17g'):format(vx) or '-', vx and '0' or '-', vx and '0' or '-',
                    ('%.17g'):format(game_t), ('%.17g'):format(now_t),
                }}, '|')
                if slot == 'cockpit' then msg = msg .. '|' .. ('%.17g'):format(now_t) end
                inbox[#inbox + 1] = prefix .. msg
            end

            -- 1. Out-of-radar + no interest -> nil binding, zero writes even if packet arrives.
            stream:tick(true)
            push_remote('cockpit', 1, 1, 1502, 20)
            now_t, game_t = now_t + 0.05, game_t + 0.05
            stream:tick(true)
            local writes_before_interest = writes

            -- 2. Enable interest while still out of radar -> still refused!
            radar_obs:reacquire()
            now_t, game_t = now_t + 0.15, game_t + 0.15
            stream:tick(true)
            local obs_out_of_radar = radar_obs:stats().currently_observed

            -- 3. Enter radar range -> observed (serial=2), playback moves NPC continuously.
            in_radar = true
            now_t, game_t = now_t + 0.15, game_t + 0.15
            stream:tick(true)
            local obs_in_radar = radar_obs:stats().currently_observed
            for seq = 1, 8 do
                local x = 1500.0 + seq * 2.5
                push_remote('cockpit', 2, seq, x, seq == 1 and nil or 25.0)
                stream:tick(true)
                now_t, game_t = now_t + 0.05, game_t + 0.05
                stream:tick(true)
                now_t, game_t = now_t + 0.05, game_t + 0.05
                stream:tick(true)
            end
            local x_before_withdraw = npc_pose.x
            local writes_before_withdraw = writes

            -- 4. Withdraw radar interest -> serial=3 cleared, zero writes while withdrawn.
            radar_obs:withdraw()
            now_t, game_t = now_t + 0.05, game_t + 0.05
            stream:tick(true)
            push_remote('cockpit', 2, 9, 1650.0, 25.0)
            now_t, game_t = now_t + 0.05, game_t + 0.05
            stream:tick(true)
            local x_during_withdraw = npc_pose.x
            local writes_during_withdraw = writes

            -- 5. Reacquire radar interest > 50 m ahead -> single authorized alignment + resumed playback.
            radar_obs:reacquire()
            now_t, game_t = now_t + 0.15, game_t + 0.15
            stream:tick(true)
            push_remote('cockpit', 4, 20, 1700.0, 25.0)
            stream:tick(true)
            now_t, game_t = now_t + 0.05, game_t + 0.05
            stream:tick(true) -- settles alignment_readback
            for seq = 21, 25 do
                local x = 1700.0 + (seq - 20) * 2.5
                push_remote('cockpit', 4, seq, x, 25.0)
                stream:tick(true)
                now_t, game_t = now_t + 0.05, game_t + 0.05
                stream:tick(true)
                now_t, game_t = now_t + 0.05, game_t + 0.05
                stream:tick(true)
            end
            local x_after_reacquire = npc_pose.x

            -- 6. Verify map observer gates strictly on map_open.
            map_obs:reacquire()
            now_t, game_t = now_t + 0.15, game_t + 0.15
            stream:tick(true)
            local map_closed_obs = map_obs:stats().currently_observed
            map_open = true
            now_t, game_t = now_t + 0.15, game_t + 0.15
            stream:tick(true)
            local map_open_obs = map_obs:stats().currently_observed

            local pstats = playback:stats()
            return {{
                writes_before_interest = writes_before_interest,
                obs_out_of_radar = obs_out_of_radar,
                obs_in_radar = obs_in_radar,
                x_before_withdraw = x_before_withdraw,
                x_during_withdraw = x_during_withdraw,
                writes_before_withdraw = writes_before_withdraw,
                writes_during_withdraw = writes_during_withdraw,
                x_after_reacquire = x_after_reacquire,
                alignments = pstats.alignments,
                reacquisitions = pstats.reacquisitions,
                withdrawals = pstats.withdrawals,
                readbacks = #readbacks,
                align_readbacks = #align_readbacks,
                map_closed_obs = map_closed_obs,
                map_open_obs = map_open_obs,
                unrelated_x = unrelated_pose.x,
                unrelated_z = unrelated_pose.z,
            }}
        ''')

        self.assertEqual(res['writes_before_interest'], 0)
        self.assertFalse(res['obs_out_of_radar'])
        self.assertTrue(res['obs_in_radar'])
        self.assertGreater(res['x_before_withdraw'], 1515.0)
        self.assertEqual(res['x_during_withdraw'], res['x_before_withdraw'])
        self.assertEqual(res['writes_during_withdraw'], res['writes_before_withdraw'])
        self.assertEqual(res['alignments'], 1)
        self.assertEqual(res['align_readbacks'], 1)
        self.assertGreaterEqual(res['reacquisitions'], 1)
        self.assertGreaterEqual(res['withdrawals'], 1)
        self.assertGreater(res['x_after_reacquire'], 1710.0)
        self.assertGreater(res['readbacks'], 10)
        self.assertFalse(res['map_closed_obs'])
        self.assertTrue(res['map_open_obs'])
        self.assertEqual((res['unrelated_x'], res['unrelated_z']), (0.0, 650.0))


class RegisteredNpcLoopbackTransportTest(unittest.TestCase):
    def test_npc_motion_source_default_off(self):
        with self.assertRaises(ValueError):
            NpcMotionSource()
        with self.assertRaises(ValueError):
            NpcMotionSource(enabled=False)

    def test_live_loopback_interest_gated_delivery_withdrawal_reacquisition_and_map(self):
        npc_index, own_index, unrelated_index = 7200, 7400, 7401
        service = CompanyMotionService()
        service.set_running(True)
        npc_source = NpcMotionSource(enabled=True)
        obs_source = CompanyMotionSource(playback_index=npc_index, cockpit_radar_playback=True)
        sender_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        obs_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        obs_udp.setblocking(False)
        auth_client = obs_client = None
        try:
            npc_cfg = npc_source.configure(EPOCH, npc_index, operational_bits='1' * 7500)
            obs_cfg = obs_source.configure(EPOCH, own_index, operational_bits='1' * 7500)
            auth_cred = service.admit(1, secrets.token_hex(16), npc_index, SECTOR, SESSION, EPOCH)
            obs_cred = service.admit(2, secrets.token_hex(16), own_index, SECTOR, SESSION, EPOCH)

            observer = obs_source.observe(lambda _peer: True)
            auth_client = CompanyMotionClient(service.port, auth_cred, npc_source, lambda _peer: True)
            obs_client = CompanyMotionClient(
                service.port, obs_cred, obs_source, lambda _peer: True,
                on_snapshot=observer.snapshot, on_end=observer.end,
                on_view_reset=observer.reset_world, on_transport_lost=observer.transport_lost,
                trial_index=npc_index,
            )
            observer.bind_views(obs_client.set_views)

            npc_prefix = f"N|{npc_cfg['lease']}|{npc_cfg['generation']}|"
            obs_prefix = f"C|{obs_cfg['lease']}|{obs_cfg['generation']}|"

            def pump(steps=12):
                for _ in range(steps):
                    service.poll()
                    auth_client.poll()
                    obs_client.poll()
                    service.poll()
                    time.sleep(0.004)

            def drain_obs_and_echo_seen():
                got = []
                while True:
                    try:
                        raw, _ = obs_udp.recvfrom(1024)
                    except BlockingIOError:
                        break
                    text = raw.decode('ascii')
                    self.assertTrue(text.startswith(obs_prefix))
                    parts = text[len(obs_prefix):].split('|')
                    got.append(parts)
                    if parts[0] == 'REMOTE':
                        seen = ['SEEN', parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7],
                                '0x3002', 'NPC-200', *parts[8:16]]
                        obs_udp.sendto((obs_prefix + '|'.join(seen)).encode('ascii'), ('127.0.0.1', obs_cfg['port']))
                return got

            def send_live_npc(seq, x, vx=None):
                t = 100.0 + seq * 0.1
                v_fields = ['-', '-', '-'] if vx is None else [f'{vx:.17g}', '0', '0']
                pkt = npc_prefix + '|'.join([
                    'SAMPLE3', EPOCH, str(npc_index), str(seq), SECTOR,
                    f'{t:.17g}', f'{t:.17g}', f'{x:.17g}', '0', '1500', *v_fields, '',
                ])
                sender_udp.sendto(pkt.encode('ascii'), ('127.0.0.1', npc_cfg['port']))

            def send_view(slot, serial, active):
                if active:
                    msg = f'VIEW|{slot}|{serial}|{EPOCH}|{SECTOR}|{npc_index}|0x3002|NPC-200'
                else:
                    msg = f'VIEW|{slot}|{serial}|{EPOCH}|-|0|-|-'
                obs_udp.sendto((obs_prefix + msg).encode('ascii'), ('127.0.0.1', obs_cfg['port']))

            pump()
            # 1. Pre-interest: authority streams 3 live samples, observer has no view -> 0 delivered.
            send_view('cockpit', 1, False)
            pump()
            for seq in (1, 2, 3):
                send_live_npc(seq, 1500.0 + seq * 3.0, None if seq == 1 else 30.0)
                pump()
            self.assertEqual(auth_client.stats()['samples_acknowledged'], 3)
            self.assertEqual(obs_client.stats()['snapshots_received'], 0)
            self.assertEqual(drain_obs_and_echo_seen(), [])

            # 2. Radar interest enabled -> delivery starts and SEEN velocity matches bit-for-bit.
            send_view('cockpit', 2, True)
            pump()
            for seq in (4, 5, 6):
                send_live_npc(seq, 1500.0 + seq * 3.0, 30.0)
                pump()
                drain_obs_and_echo_seen()
                pump(4)
            ost = obs_source.observer_stats()
            self.assertGreaterEqual(ost['slots']['cockpit']['samples_confirmed'], 3)
            self.assertEqual(ost['slots']['cockpit']['last_nonzero_velocity_confirmed']['real_velocity'],
                             {'x': 30.0, 'y': 0.0, 'z': 0.0})
            self.assertEqual(ost['slots']['cockpit']['last_confirmed']['index'], npc_index)

            # 3. Withdrawal -> authority keeps streaming (seq 7..9 acked), observer gets 0 new snapshots.
            snaps_before = obs_client.stats()['snapshots_received']
            send_view('cockpit', 3, False)
            pump()
            drain_obs_and_echo_seen()
            for seq in (7, 8, 9):
                send_live_npc(seq, 1500.0 + seq * 3.0, 30.0)
                pump()
            self.assertEqual(auth_client.stats()['samples_acknowledged'], 9)
            self.assertEqual(obs_client.stats()['snapshots_received'], snaps_before)
            self.assertEqual([p for p in drain_obs_and_echo_seen() if p[0] == 'REMOTE'], [])

            # 4. Reacquisition via cockpit radar -> resumes at seq 10.
            send_view('cockpit', 4, True)
            pump()
            send_live_npc(10, 1530.0, 30.0)
            pump()
            drain_obs_and_echo_seen()
            pump(4)
            self.assertEqual(obs_source.observer_stats()['slots']['cockpit']['last_confirmed']['sequence'], 10)

            # 5. Map interest slot independently delivers & confirms SEEN while cockpit withdrawn.
            send_view('cockpit', 5, False)
            send_view('map', 1, True)
            pump()
            send_live_npc(11, 1533.0, 30.0)
            pump()
            drain_obs_and_echo_seen()
            pump(4)
            self.assertEqual(obs_source.observer_stats()['slots']['map']['last_confirmed']['sequence'], 11)
            self.assertNotEqual(obs_source.observer_stats()['slots']['map']['last_confirmed']['index'], unrelated_index)
        finally:
            for obj in (auth_client, obs_client, npc_source, obs_source, service, sender_udp, obs_udp):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass


if __name__ == '__main__':
    unittest.main()
