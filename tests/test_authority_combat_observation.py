"""Offline static, LuaJIT 2.1, and parser tests for read-only combat observation."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from authority.combat_observation import (
    parse_log,
    parse_md_event,
    summarize_by_phase,
    summarize_phase,
    validate_lua_read_only,
    validate_md_read_only,
)

MD_PATH = ROOT / 'src' / 'authority' / 'md' / 'combat_observer.xml'
LUA_PATH = ROOT / 'src' / 'authority' / 'lua' / 'combat_observer.lua'


def _load_luajit_runtime():
    from lupa.luajit21 import LuaRuntime
    return LuaRuntime(unpack_returned_tuples=True)


def _sample_event_line(
    seq, kind, age, *,
    victim='0x102', v_label='company_ff_target', v_code='VLY-685', v_owner='player', v_reg=2,
    v_ctrl=0, v_occ=0,
    ev_att='0', ea_label='none', ea_code='none', ea_owner='none', ea_reg=0, ea_ctrl=0, ea_isship=0, ea_pilot='0',
    last_att='0x101', la_label='company_player', la_code='HVC-420', la_owner='player', la_reg=1, la_ctrl=1, la_isship=1,
    last_att_time=10.0,
    comp=None, is_root=1, comp_macro='ship_arg_s_fighter_03_a_macro',
    p2='null', p3='52', amount=52.0,
    shield=4748.0, maxshield=4800.0, shieldpct=98.9167,
    hull=2100.0, maxhull=2100.0, hullpct=100.0,
    collided=0,
):
    comp = victim if comp is None else comp
    fields = [
        seq, kind, age,
        victim, v_label, v_code, v_owner, v_reg, v_ctrl, v_occ,
        ev_att, ea_label, ea_code, ea_owner, ea_reg, ea_ctrl, ea_isship, ea_pilot,
        last_att, la_label, la_code, la_owner, la_reg, la_ctrl, la_isship, last_att_time,
        comp, is_root, comp_macro, p2, p3, amount,
        shield, maxshield, shieldpct, hull, maxhull, hullpct, collided,
    ]
    return '|'.join(str(x) for x in fields)


class CombatObservationContractTests(unittest.TestCase):
    def test_md_observer_is_strictly_read_only_and_isolated(self):
        info = validate_md_read_only(MD_PATH.read_text(encoding='utf-8'))
        self.assertTrue(info['read_only'])
        self.assertTrue(info['instantiated_cues_isolated'])
        self.assertIn('event_object_shield_damaged', info['events_registered'])
        self.assertIn('event_object_hull_damaged', info['events_registered'])
        self.assertIn('event_object_attacked', info['events_registered'])
        self.assertIn('event_object_attacked_object', info['events_registered'])
        self.assertIn('event_player_owned_attacked', info['events_registered'])
        self.assertIn('event_player_owned_attacked_object', info['events_registered'])
        self.assertIn('event_player_ship_hit', info['events_registered'])
        self.assertIn('event_object_collided', info['events_registered'])

    def test_md_validator_rejects_mutating_tags(self):
        bad_xml = MD_PATH.read_text(encoding='utf-8').replace(
            '</actions>\n    </cue>\n   </cues>',
            '<set_object_hull object="$victim" exact="100"/></actions>\n    </cue>\n   </cues>',
            1,
        )
        with self.assertRaises(ValueError):
            validate_md_read_only(bad_xml)

    def test_lua_observer_is_strictly_read_only(self):
        info = validate_lua_read_only(LUA_PATH.read_text(encoding='utf-8'))
        self.assertTrue(info['read_only'])

    def test_lua_validator_rejects_mutating_calls(self):
        with self.assertRaises(ValueError):
            validate_lua_read_only('local x = 1\nC.SetWeaponLaunchedMacro(w, "x")\n')


class CombatObservationLuaJitTests(unittest.TestCase):
    def test_luajit_parse_classify_readback_and_weapon_profile(self):
        lua = _load_luajit_runtime()
        mod = lua.execute(LUA_PATH.read_text(encoding='utf-8'))

        p1 = _sample_event_line(1, 'shield_damaged', 10.0, amount=52.0, shield=4748.0, shieldpct=98.9167)
        p2 = _sample_event_line(
            2, 'shield_damaged', 12.0, amount=20.0, shield=0.0, shieldpct=0.0, last_att_time=10.0,
        )
        p3 = _sample_event_line(
            3, 'hull_damaged', 12.0, amount=32.0, shield=0.0, shieldpct=0.0,
            hull=2068.0, hullpct=98.4762, last_att_time=10.0,
        )
        p4 = _sample_event_line(
            4, 'shield_damaged', 13.0, amount=250.0, shield=0.0, shieldpct=0.0, collided=1, last_att_time=13.0,
        )

        logs = []
        lua_code = """
        return function(M, p1, p2, p3, p4)
            local fake_health = {
                ['lua:258'] = { 100.0, 98.9167, nil, nil, nil, nil, 'player', 'ship_arg_s_fighter_03_a_macro' },
            }
            local deps = {
                ConvertIDTo64Bit = function(s) return s == '0x102' and 258 or 257 end,
                ConvertStringToLuaID = function(dec) return 'lua:' .. tostring(dec) end,
                GetComponentData = function(ref, ...)
                    local row = fake_health[ref]
                    if not row then return 'weapon_gen_s_gatling_01_mk2_macro' end
                    return unpack(row)
                end,
                GetMacroData = function(m, key)
                    if key == 'infolibrary' then return 'weapons' end
                    return nil
                end,
                GetLibraryEntry = function(lib, m)
                    if lib == 'weapons' and m == 'weapon_gen_s_gatling_01_mk2_macro' then
                        return {
                            hullshielddpshot = 52,
                            shieldonlydpshot = 0,
                            hullonlydpshot = 0,
                            hullnoshielddpshot = 0,
                            hullshieldareadpshot = 0,
                            shieldonlyareadpshot = 0,
                            hullonlyareadpshot = 0,
                            hullnoshieldareadpshot = 0,
                            isbeamweapon = 0,
                            bulletspeed = 1620,
                        }
                    end
                    return nil
                end,
                C = {
                    GetNumUpgradeSlots = function(u64, _, kind) return kind == 'weapon' and 6 or 0 end,
                    GetUpgradeSlotCurrentComponent = function(u64, kind, slot) return 900 + slot end,
                },
            }
            local captured = {}
            local obs = M.new({
                deps = deps,
                log = function(line) captured[#captured + 1] = line end,
            })
            local e1 = obs.ingest(p1)
            fake_health['lua:258'] = { 100.0, 0.0, nil, nil, nil, nil, 'player', 'ship_arg_s_fighter_03_a_macro' }
            local e2 = obs.ingest(p2)
            fake_health['lua:258'] = { 98.4762, 0.0, nil, nil, nil, nil, 'player', 'ship_arg_s_fighter_03_a_macro' }
            local e3 = obs.ingest(p3)
            local e4 = obs.ingest(p4)
            local wp = M.inspect_weapon_profile('0x101', deps)
            local s = obs.summary()
            return e1.classification, e2.classification, e3.classification, e4.classification,
                   s.total_events, s.lastattacktime_lag_events, s.lua_readback_ok,
                   s.max_shieldpct_diff, wp.weapon_count, wp.uniform_macro, wp.weapons[1].hullshielddpshot,
                   #captured
        end
        """
        runner = lua.execute(lua_code)
        (
            c1, c2, c3, c4, total, lag, rb_ok, sh_diff, wcount, umacro, hs_shot, log_count,
        ) = runner(mod, p1, p2, p3, p4)
        self.assertEqual(c1, 'shield_absorbed')
        self.assertEqual(c2, 'shield_depleted')
        self.assertEqual(c3, 'hull_overflow_same_age')
        self.assertEqual(c4, 'collision_flagged')
        self.assertEqual(total, 4)
        self.assertEqual(lag, 2)
        self.assertEqual(rb_ok, 4)
        self.assertAlmostEqual(sh_diff, 0.0, places=6)
        self.assertEqual(wcount, 6)
        self.assertEqual(umacro, 'weapon_gen_s_gatling_01_mk2_macro')
        self.assertEqual(hs_shot, 52)
        self.assertEqual(log_count, 4)


class CombatObservationAnalyzerTests(unittest.TestCase):
    def test_parse_log_and_summarize_phases(self):
        lines = [
            '[X4LAN] AUTH_COMBAT_PHASE phase=ff_shield_only',
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                1, 'shield_damaged', 20.0, amount=52.0, shield=4748.0, shieldpct=98.9167, last_att_time=20.0,
            ),
            '[X4LAN] AUTH_COMBAT_LUA seq=1 kind=shield_damaged class=shield_absorbed victim=company_ff_target '
            'md_sh=98.917 md_hp=100.000 lua_ok=true lua_shpct=98.9167 lua_hppct=100 lua_sh=nil lua_shmax=nil lua_hp=nil lua_hpmax=nil',
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                2, 'object_attacked', 20.0,
                ev_att='0x101', ea_label='company_player', ea_code='HVC-420', ea_owner='player', ea_reg=1, ea_ctrl=1, ea_isship=1,
                p2='killmethod.hitbybullet', p3='null', amount=0.0, shield=4748.0, shieldpct=98.9167, last_att_time=20.0,
            ),
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                3, 'shield_damaged', 20.2, amount=52.0, shield=4696.0, shieldpct=97.8333, last_att_time=20.0,
            ),
            '[X4LAN] AUTH_COMBAT_PHASE phase=ff_hull_overflow',
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                4, 'shield_damaged', 22.0, amount=10.0, shield=0.0, shieldpct=0.0, last_att_time=20.0,
            ),
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                5, 'hull_damaged', 22.0, amount=42.0, shield=0.0, shieldpct=0.0, hull=2058.0, hullpct=98.0, last_att_time=20.0,
            ),
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                6, 'object_attacked', 22.0,
                ev_att='0x101', ea_label='company_player', ea_code='HVC-420', ea_owner='player', ea_reg=1, ea_ctrl=1, ea_isship=1,
                p2='killmethod.hitbybullet', p3='null', amount=0.0, shield=0.0, shieldpct=0.0, hull=2058.0, hullpct=98.0, last_att_time=22.0,
            ),
        ]
        events, readbacks = parse_log('\n'.join(lines))
        self.assertEqual(len(events), 6)
        self.assertIn(1, readbacks)
        by_phase = summarize_by_phase(
            events, readbacks,
            phase_victims={'ff_shield_only': 'company_ff_target', 'ff_hull_overflow': 'company_ff_target'},
        )
        p1 = by_phase['ff_shield_only']
        self.assertTrue(p1['shield_only_absorbed'])
        self.assertEqual(p1['shield_damage_callbacks'], 2)
        self.assertEqual(p1['hull_damage_callbacks'], 0)
        self.assertEqual(p1['object_attacked_count'], 1)
        self.assertEqual(p1['lastattacktime_lag_count'], 1)
        self.assertAlmostEqual(p1['lastattacktime_max_lag_s'], 0.2, places=6)
        self.assertEqual(p1['lua_keys_present']['shieldpercent'], 1)
        self.assertEqual(p1['lua_keys_present']['shield'], 0)

        p2 = by_phase['ff_hull_overflow']
        self.assertFalse(p2['shield_only_absorbed'])
        self.assertEqual(p2['overflow_same_age_hull_hits'], 1)
        self.assertAlmostEqual(p2['hull_damage_total'], 42.0, places=4)

    def test_player_ship_hit_and_snapshot_abs_readback_agreement(self):
        lines = [
            '[X4LAN] AUTH_COMBAT_PHASE phase=npc_to_player_occupied',
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                10, 'player_ship_hit', 16.0263,
                victim='0x101', v_label='company_player', v_code='TZG-167', v_owner='player', v_reg=1, v_ctrl=1, v_occ=1,
                ev_att='0x105', ea_label='npc_attacker', ea_code='OVG-912', ea_owner='xenon', ea_reg=5, ea_ctrl=0, ea_isship=1,
                last_att='0x105', la_label='npc_attacker', la_code='OVG-912', la_owner='xenon', la_reg=5, la_ctrl=0, la_isship=1,
                last_att_time=16.0263,
                p2='0x17cbe5', p3='bullet_xen_s_laser_01_mk1_macro', amount=0.0,
                shield=2343.0, maxshield=2366.0, shieldpct=99.0279, hull=3600.0, maxhull=3600.0, hullpct=100.0,
            ),
            '[X4LAN] AUTH_COMBAT_OBS ' + _sample_event_line(
                11, 'snapshot', 17.5,
                victim='0x101', v_label='company_player', v_code='TZG-167', v_owner='player', v_reg=1, v_ctrl=1, v_occ=1,
                p2='after_npc_to_player_occupied', p3='1', amount=0.0,
                shield=2343.0, maxshield=2366.0, shieldpct=99.0279, hull=3600.0, maxhull=3600.0, hullpct=100.0,
            ),
            '[X4LAN] AUTH_COMBAT_LUA seq=11 kind=snapshot class=snapshot victim=company_player '
            'md_sh=99.028 md_hp=100.000 lua_ok=true lua_shpct=99 lua_hppct=100 lua_sh=2343 lua_shmax=2366 lua_hp=3600 lua_hpmax=3600',
        ]
        events, readbacks = parse_log('\n'.join(lines))
        summary = summarize_phase(events, readbacks, victim_label='company_player')
        self.assertEqual(summary['player_ship_hit_count'], 1)
        self.assertEqual(summary['player_ship_hit_details'][0]['projectile'], '0x17cbe5')
        self.assertEqual(summary['player_ship_hit_details'][0]['projectile_macro'], 'bullet_xen_s_laser_01_mk1_macro')
        self.assertAlmostEqual(summary['snapshot_max_lua_md_abs_sh_diff'], 0.0, places=6)
        self.assertAlmostEqual(summary['snapshot_max_lua_md_abs_hp_diff'], 0.0, places=6)
        events, wrong_actor = parse_log('\n'.join(lines).replace('victim=company_player', 'victim=unrelated_ship'))
        for unmatched in ({}, wrong_actor):
            summary = summarize_phase(events, unmatched, victim_label='company_player')
            self.assertEqual(summary['lua_readbacks_checked'], 0)
            self.assertIsNone(summary['snapshot_max_lua_md_abs_sh_diff'])
            self.assertIsNone(summary['snapshot_max_lua_md_abs_hp_diff'])


if __name__ == '__main__':
    unittest.main()
