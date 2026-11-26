"""Offline static, LuaJIT 2.1, and parser tests for read-only combat observation."""
from dataclasses import replace
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

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
from authority.checkpoints import Checkpoint
from test_authority_registry_availability_evidence import EPOCH, RUN, build_xml
from tools.dev.authority_combat_hit_evidence import resolve_hit

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


class CheckpointDamageEvidenceTests(unittest.TestCase):
    def test_live_indices_and_codes_must_agree_with_the_same_save_epoch(self):
        ships = ('<component class="ship_s" id="[0x101]" owner="player" code="HVC-420"/>'
                 '<component class="ship_s" id="[0x102]" owner="xenon" code="VLY-685"/>')
        compressed = gzip.compress(build_xml((1, 1), definitions=ships))
        # Runtime actor handles may differ from the saved component IDs.
        payload = _sample_event_line(8, 'shield_damaged', 10.0, victim='0x456', v_owner='xenon',
                                     last_att='0x789')
        event = parse_md_event(payload + '|' + EPOCH)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'save.xml.gz'
            path.write_bytes(compressed)
            checkpoint = Checkpoint('checkpoint-' + 'a' * 32, path, hashlib.sha256(compressed).hexdigest(),
                                    len(compressed), RUN)
            report = resolve_hit(checkpoint, event)
            self.assertEqual(report['victim'], {'code': 'VLY-685', 'index': 2})
            self.assertEqual(report['last_attacker'], {'code': 'HVC-420', 'index': 1})
            self.assertIsNone(report['event_attacker'])
            self.assertFalse(report['victim_occupied_by_player'])
            self.assertFalse(report['victim_controlled_by_player'])
            piloted_hit = replace(event, victim_reg=1, victim_code='HVC-420', victim_owner='player',
                                  victim_occ=True, victim_ctrl=True)
            pilot_report = resolve_hit(checkpoint, piloted_hit, piloted_ship_index=1)
            self.assertTrue(pilot_report['victim_occupied_by_player'])
            self.assertTrue(pilot_report['victim_controlled_by_player'])
            for observed, admitted_index, expected_error in (
                (piloted_hit, 2, 'admitted pilot ship'),
                (replace(piloted_hit, victim_ctrl=False), 1, 'occupied and controlled'),
                (replace(piloted_hit, victim_occ=False), 1, 'occupied and controlled'),
                (replace(piloted_hit, victim_owner='xenon'), 1, 'Company ownership'),
                (replace(piloted_hit, victim_reg=2, victim_code='VLY-685'), 2, 'Company ownership'),
            ):
                with self.subTest(expected_error=expected_error, observed=observed):
                    with self.assertRaisesRegex(ValueError, expected_error):
                        resolve_hit(checkpoint, observed, piloted_ship_index=admitted_index)
            for mismatch in (replace(event, registry_epoch=None), replace(event, registry_epoch='other'),
                             replace(event, victim_code='HVC-420'), replace(event, last_att_reg=3)):
                with self.subTest(mismatch=mismatch), self.assertRaises(ValueError):
                    resolve_hit(checkpoint, mismatch)


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

    def test_md_requires_current_bidirectional_registry_binding(self):
        root = ET.parse(MD_PATH)
        ready = root.find(".//library[@name='ReadRegistryState']/actions/set_value").get('exact')
        for field in ('epoch', 'last', 'ships', 'byship', 'faulted'):
            self.assertIn(f'md.X4AuthorityShipRegistry.State.${field}?', ready)
        self.assertIn("State.$epoch != ''", ready)
        self.assertIn('State.$armed and', ready)
        self.assertIn('md.X4AuthorityShipRegistry.State.$epoch == State.$epoch', ready)
        self.assertIn('not md.X4AuthorityShipRegistry.State.$faulted', ready)
        epoch = root.find(".//library[@name='ReadRegistryState']/actions/set_value[@name='$registry_epoch']")
        self.assertEqual(epoch.get('exact'), "if $reg_ready then md.X4AuthorityShipRegistry.State.$epoch else ''")

        lookup = root.find(".//library[@name='ResolveRegistryBinding']/actions")
        self.assertEqual(lookup.find("set_value[@name='$registered_index']").get('exact'), '0')
        checked = lookup.find("do_if[@value='$reg_ready']")
        self.assertEqual(checked.find("do_if[@value='not $lookup_by_index and @$lookup_ship.isclass.ship']"
                                      "/set_value").get('exact'),
                         '@md.X4AuthorityShipRegistry.State.$byship.{$lookup_ship}')
        bounded = checked.find("do_if/set_value[@name='$candidate_ship']/..")
        for guard in ('(typeof $candidate_index).isnumeric', '$candidate_index ge 1',
                      '$candidate_index le md.X4AuthorityShipRegistry.State.$last',
                      '$candidate_index == ($candidate_index)i'):
            self.assertIn(guard, bounded.get('value'))
        binding = bounded.find('do_if')
        self.assertIn('$candidate_ship == $lookup_ship', binding.get('value'))
        self.assertIn('@md.X4AuthorityShipRegistry.State.$byship.{$candidate_ship} == $candidate_index',
                      binding.get('value'))
        self.assertEqual(binding.find("set_value[@name='$registered_index']").get('exact'), '$candidate_index')

        emit = root.find(".//library[@name='Emit']/actions")
        self.assertIsNotNone(emit.find("include_actions[@ref='ReadRegistryState']"))
        for actor, output in (('victim', 'v_reg'), ('ev_att', 'ea_reg'), ('last_att', 'la_reg')):
            role = emit.find(f"do_if[@value='@${actor}.exists']")
            self.assertEqual(role.find("set_value[@name='$lookup_ship']").get('exact'), f'${actor}')
            self.assertIsNotNone(role.find("include_actions[@ref='ResolveRegistryBinding']"))
            self.assertEqual(role.find(f"set_value[@name='${output}']").get('exact'), '$registered_index')
            self.assertEqual(emit.find(f"set_value[@name='${output}']").get('exact'), '0')
        payload = emit.find("do_if[@value='$reg_ready']/set_value[@name='$payload']")
        self.assertEqual(payload.get('exact'), "'%s|%s'.[$payload,$registry_epoch]")
        self.assertIsNotNone(emit.find("raise_lua_event[@param='$payload']"))

        arm = root.find(".//cue[@name='Arm']/actions")
        self.assertIsNotNone(arm.find("include_actions[@ref='ReadRegistryState']"))
        status = arm.find("do_if[@value='$reg_ready']/set_value[@name='$status']")
        self.assertEqual(status.get('exact'), "'%s|%s'.[$status,$registry_epoch]")

        watch = root.find(".//cue[@name='Watch']/actions/do_if[@value='State.$armed']")
        self.assertIsNotNone(watch.find("include_actions[@ref='ReadRegistryState']"))
        self.assertIsNotNone(watch.find("include_actions[@ref='ResolveRegistryBinding']"))
        self.assertEqual(watch.find("do_if[@value='$lookup_by_index']/set_value[@name='$ship']").get('exact'),
                         '$registered_ship')


class CombatObservationLuaJitTests(unittest.TestCase):
    def test_lua_registry_epoch_is_optional(self):
        lua = _load_luajit_runtime()
        mod = lua.execute(LUA_PATH.read_text(encoding='utf-8'))
        payload = _sample_event_line(1, 'shield_damaged', 20.0)
        for suffix, epoch in (('', None), ('|', None), ('|bad epoch', None), ('|observed-epoch_1', 'observed-epoch_1')):
            with self.subTest(suffix=suffix):
                event = mod.parse_event(payload + suffix)
                self.assertEqual(event.registry_epoch, epoch)
                self.assertEqual(event.victim_reg, 2)
                self.assertEqual(event.last_att_reg, 1)

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
    def test_log_registry_epoch_comes_from_the_observation(self):
        prefix = '[X4LAN] AUTH_COMBAT_OBS '
        payload = _sample_event_line(1, 'shield_damaged', 20.0)
        lines = [
            '[X4LAN] AUTH_COMBAT_OBS_CTRL action=Arm epoch=requested registry_epoch=observed-epoch_1',
            prefix + payload + '|observed-epoch_1',
            prefix + payload,
            prefix + payload + '|',
            prefix + payload + '|bad epoch',
            prefix + payload + '|' + 'a' * 65,
        ]
        events, _ = parse_log('\n'.join(lines))
        self.assertEqual([event.registry_epoch for event in events], ['observed-epoch_1', None, None, None, None])
        self.assertEqual((events[0].victim_reg, events[0].ev_att_reg, events[0].last_att_reg), (2, 0, 1))

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
