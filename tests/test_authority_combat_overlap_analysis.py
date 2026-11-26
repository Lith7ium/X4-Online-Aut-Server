import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from xml.etree import ElementTree

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools' / 'dev')]

import authority_combat_overlap_analysis as A

P = '[Scripts] 1 *** Context:md.X4AuthorityCombatOverlapTrace.Fired<inst:1>: [X4LAN] AUTH_'
EPOCH = 'a' * 32
SETUP = f'{P}COMBAT_PROBE setup_ok p1=0x101(HVC-420,w=6) p2=0x102(KHI-630) npc_target=0x9(XEN-123)'
GATLING = 'bullet_gen_s_gatling_01_mk2_macro'
LASER = 'bullet_gen_s_laser_01_mk2_macro'


def fire(age, shooter, proj, bullet=GATLING):
    return f'{P}OVERLAP_FIRE age={age} shooter={shooter} weapon=0xab{shooter} proj={proj} exists=1 pmacro={bullet}'


def impact(age, kind, amount, value, gone=''):
    count = len([g for g in gone.split(';') if g])
    return f'{P}OVERLAP_IMPACT age={age} kind={kind} amount={amount} value={value} comp=0x9 gone={count} alive=0 ids={gone}'


def sweep(age, tag, gone=''):
    count = len([g for g in gone.split(';') if g])
    return f'{P}OVERLAP_SWEEP age={age} tag={tag} gone={count} alive=0 ids={gone}'


def attack(age, shooter, *, target='0x9', method='hitbybullet', actor=None):
    actor = actor or f'0x10{shooter}'
    return (f'{P}OVERLAP_ATTACKED_OBJECT age={age} source={actor} victim={target} method={method} '
            f'comp=0x9 weapon=0xab{shooter} weapon_shooter={shooter}')


def event(phase, age, kind, shooter=1, **fields):
    values = {
        'phase': phase, 'age': age, 'kind': kind,
        'victim': '0x9', 'victim_code': 'XEN-123', 'victim_reg': 4,
        'victim_label': 'npc_target', 'victim_owner': 'xenon', 'registry_epoch': EPOCH,
        'ev_att': f'0x10{shooter}', 'ev_att_reg': shooter, 'ev_att_owner': 'player',
        'ev_att_label': A.SHOOTER_LABELS[str(shooter)], 'ev_att_code': 'HVC-420' if shooter == 1 else 'KHI-630',
        'ev_att_ctrl': shooter == 1, 'ev_att_isship': True,
        'last_att_label': 'company_player', 'comp': '0x9', 'amount': 26, 'shield': 100, 'hull': 2500,
        'param2': 'hitbybullet', 'param3': f'[component.{{0x9L}},component.{{0xab{shooter}L}}]',
    }
    return SimpleNamespace(**(values | fields))


def fixture(*, mixed=False):
    lines, events = [SETUP], []
    if mixed:
        for source, amount, bullet in (('1', 26, GATLING), ('2', 36, LASER)):
            lines.append(f'{P}COMBAT_DRIVER WEAPON_PROFILE label={A.SHOOTER_LABELS[source]} '
                         f'ok=true weapons=6 mixed=false modified=false bullet={bullet} hs_shot={amount} '
                         'root_hullshield_only=true')
    for phase, age, shooters in (('control_pilot', 1, [1]), ('control_second', 2, [2]), ('overlap', 3, [1, 2])):
        lines.append(f'{P}COMBAT_PHASE phase={phase}')
        for shooter in shooters:
            alternate = mixed and shooter == 2
            amount = 36 if alternate else 26
            lines.extend((fire(age, shooter, f'0x{age}{shooter}', LASER if alternate else GATLING),
                          attack(age + 0.1, shooter), impact(age + 0.1, 'shield', amount, 100)))
            events.extend((event(phase, age + 0.1, 'object_attacked_object', shooter),
                           event(phase, age + 0.1, 'shield_damaged', shooter, amount=amount)))
    lines.extend((f'{P}COMBAT_PHASE phase=miss_pilot', fire(4.1, 1, '0x41'),
                  f'{P}OVERLAP_DODGE age=4.2 target=0x9', sweep(5.5, 'miss_pilot_end', '1:0x41:1.4;')))
    for age, tag in ((4, 'before_miss_pilot'), (5.6, 'after_miss_pilot')):
        events.append(event('miss_pilot', age, 'snapshot', param2=tag, param3='1',
                            hull=2500, maxhull=2500, shield=500, maxshield=1200))
    for seq, row in enumerate(events, 1):
        row.seq = seq
    return '\n'.join(lines), events


class OverlapFrameTest(unittest.TestCase):
    def test_bullets_seen_gone_next_frame_resolve_the_previous_frame(self):
        text = '\n'.join([
            f'{P}COMBAT_PHASE phase=overlap age=1',
            fire(1.0, 1, '0xa'), fire(1.0, 2, '0xb'), fire(1.0, 2, '0xc'),
            # Frame 1.1: one bullet per shooter; one of them empties the shield and spills into hull.
            impact(1.1, 'shield', 26, 18), impact(1.1, 'shield', 18, 0), impact(1.1, 'hull', 8, 2492),
            # Frame 1.2 reports frame 1.1's bullets as gone, then holds one hit of its own.
            impact(1.2, 'hull', 26, 2466, '1:0xa:0.11;2:0xb:0.11;'),
            sweep(1.3, 'overlap_stop', '2:0xc:0.2;'),
            fire(1.4, 1, '0xd'),
            sweep(3.0, 'overlap_end', '1:0xd:1.6;'),
        ])
        frames = A.analyse(text, [])['overlap']['frame_detail']
        self.assertEqual([f['class'] for f in frames], ['ambiguous_both_shooters', 'one_shooter_count_matches'])
        self.assertEqual((frames[0]['overflow_pairs'], frames[0]['bullets_needed'], frames[0]['per_callback_ambiguous']), (1, 2, True))
        self.assertEqual((frames[1]['observed_by'], frames[1]['shooters']), ('stop_sweep', ['2']))

    def test_stop_sweep_in_the_same_frame_and_end_sweep_stay_unresolved(self):
        text = '\n'.join([
            f'{P}COMBAT_PHASE phase=control_pilot age=1',
            fire(1.0, 1, '0xa'), fire(1.0, 1, '0xb'),
            impact(1.1, 'shield', 26, 100),
            sweep('1.10', 'control_pilot_stop', '1:0xa:0.1;'),
            impact(1.2, 'shield', 26, 74),
            sweep(3.0, 'control_pilot_end', '1:0xb:1.9;'),
        ])
        result = A.analyse(text, [])['control_pilot']
        self.assertEqual(result['frame_classes'], {'unresolved_stop_sweep_same_frame': 1, 'unresolved_last_frame': 1})
        self.assertEqual(result['bullets_gone_without_impact_frame'], 2)

    def test_a_miss_is_bullets_fired_without_any_impact(self):
        text = '\n'.join([
            f'{P}COMBAT_PHASE phase=miss_pilot age=1',
            fire(1.0, 1, '0xa'),
            sweep(3.5, 'miss_pilot_end', '1:0xa:2.5;'),
        ])
        by_phase = A.analyse(text, [])
        miss = by_phase['miss_pilot']
        self.assertEqual((miss['shots_by_shooter'], miss['impact_callbacks'], miss['bullets_gone_without_impact_frame']), ({'1': 1}, 0, 1))
        self.assertFalse(A.accepted(by_phase))

    def test_acceptance_needs_independent_coincident_witnesses_and_scoped_miss(self):
        text, events = fixture()
        analysis = A.analyse(text, events, epoch=EPOCH)
        self.assertTrue(A.accepted(analysis))
        self.assertEqual(analysis['overlap']['coincident_direct_hit_frames'], ['3.1'])
        self.assertEqual(analysis['overlap']['named_attack_vs_inferred_frame'],
                         {'agree': 0, 'disagree': 0, 'compared': 0, 'unverified': 1})
        missing = [row for row in events if (row.phase, row.kind, row.ev_att_reg)
                   != ('overlap', 'object_attacked_object', 2)]
        self.assertFalse(A.accepted(A.analyse(text, missing, epoch=EPOCH)))
        side_damage = event('overlap', 3.1, 'shield_damaged', victim='0x102', victim_label='second_shooter')
        self.assertFalse(A.accepted(A.analyse(text, events + [side_damage], epoch=EPOCH)))
        self.assertFalse(A.accepted(A.analyse(text.replace('OVERLAP_DODGE', 'OLD_DODGE'), events, epoch=EPOCH)))

    def test_wrong_target_method_or_shooter_cannot_witness_a_coincident_hit(self):
        text, events = fixture()
        for changes in ({'target': '0x102'}, {'method': 'collision'}, {'actor': '0x101'}):
            with self.subTest(changes=changes):
                altered = text.replace(attack(3.1, 2), attack(3.1, 2, **changes))
                analysis = A.analyse(altered, events, epoch=EPOCH)
                self.assertFalse(A.accepted(analysis))
                self.assertEqual(analysis['overlap']['direct_hit_shooters'], ['1'])
                self.assertEqual(analysis['overlap']['coincident_direct_hit_frames'], [])

    def test_incoincident_observer_ages_and_unmatched_health_fail_acceptance(self):
        text, events = fixture()
        second = next(row for row in events if (row.phase, row.kind, row.ev_att_reg)
                      == ('overlap', 'object_attacked_object', 2))
        second.age = 3.11
        text = text.replace(attack(3.1, 2), attack(3.11, 2))
        self.assertFalse(A.accepted(A.analyse(text, events, epoch=EPOCH)))
        text, events = fixture()
        post_miss = next(row for row in events if row.param2 == 'after_miss_pilot')
        post_miss.hull -= 26
        self.assertFalse(A.accepted(A.analyse(text, events, epoch=EPOCH)))


class MixedWeaponTest(unittest.TestCase):
    def test_mixed_damage_is_independent_of_lastattacker_and_cannot_replace_direct_witness(self):
        text, events = fixture(mixed=True)
        overlap = A.analyse(text, events, epoch=EPOCH)
        self.assertTrue(A.accepted(overlap))
        baselines, result = A.analyse_damage(text, events, overlap, epoch=EPOCH)
        self.assertEqual([baselines[source]['amount'] for source in ('1', '2')], [26, 36])
        rows = result['overlap']['rows']
        self.assertEqual([row['source_by_amount'] for row in rows], ['1', '2'])
        self.assertEqual([row['lastattacker'] for row in rows], ['1', '1'])
        self.assertEqual(result['overlap']['mixed_game_ages'], 1)
        self.assertEqual(result['overlap']['preceding_same_age_notification_compared'], 0)
        events = [row for row in events if (row.phase, row.kind, row.ev_att_reg)
                  != ('overlap', 'object_attacked_object', 2)]
        overlap = A.analyse(text, events, epoch=EPOCH)
        self.assertFalse(A.accepted(overlap))
        self.assertEqual(A.analyse_damage(text, events, overlap, epoch=EPOCH)[1]['overlap']['assigned_hits'], 2)
        self.assertEqual(A.analyse_damage(text.replace('hs_shot=36', 'hs_shot=35'), events, overlap, epoch=EPOCH)[1], {})

    def test_depletion_and_overflow_require_unique_adjacent_callbacks_in_the_same_reported_age(self):
        def shield(seq, age, amount):
            return event('overlap', age, 'shield_damaged', seq=seq, amount=amount, shield=0)

        def hull(seq, age, amount):
            return event('overlap', age, 'hull_damaged', seq=seq, amount=amount, shield=0)

        for damage, expected in (
            ([shield(1, 2.0, 26), hull(2, 2.0, 10)], [('2', 'overflow')]),
            ([shield(1, 2.0, 10), hull(2, 2.0, 26)], [(None, 'ambiguous_overflow')] * 2),
            ([shield(1, 2.0, 20), hull(3, 2.0, 16)], [(None, 'ambiguous_overflow')] * 2),
            ([shield(1, 2.000001, 20), hull(2, 2.000002, 16)], [(None, 'unresolved')] * 2),
        ):
            with self.subTest(expected=expected):
                got = A._attribute_damage(damage, damage, {26: '1', 36: '2'}, A.fixture_actors(SETUP), EPOCH)
                self.assertEqual([(row['source_by_amount'], row['kind']) for row in got['rows']], expected)

    def test_matching_damage_amounts_cannot_distinguish_two_guns(self):
        text, events = fixture(mixed=True)
        text = text.replace('hs_shot=36', 'hs_shot=26').replace('amount=36', 'amount=26')
        for row in events:
            if row.amount == 36:
                row.amount = 26
        overlap = A.analyse(text, events, epoch=EPOCH)
        self.assertTrue(A.accepted(overlap))
        baselines, result = A.analyse_damage(text, events, overlap, epoch=EPOCH)
        self.assertEqual([baselines[source]['amount'] for source in ('1', '2')], [26, 26])
        self.assertEqual(result, {})

    def test_same_age_notification_does_not_cross_an_observer_sequence_gap(self):
        notice = event('overlap', 2.0, 'object_attacked', shooter=2, seq=1)
        first = event('overlap', 2.0, 'shield_damaged', shooter=2, seq=2, amount=36)
        second = event('overlap', 2.0, 'hull_damaged', shooter=1, seq=4, amount=26)
        events = [notice, first, second]
        result = A._attribute_damage(events, events[1:], {26: '1', 36: '2'}, A.fixture_actors(SETUP), EPOCH)
        self.assertEqual([row['preceding_same_age_notification'] for row in result['rows']], ['2', None])
        self.assertEqual(result['preceding_same_age_notification_compared'], 1)

    def test_alternate_loadout_is_staged_only_in_disposable_overlap_view(self):
        from authority_combat_observation_windows import compose_extension
        with tempfile.TemporaryDirectory() as directory:
            extensions = Path(directory)
            kwargs = dict(profile=45850, gamestart='custom_creative', epoch=EPOCH,
                          second_weapon='weapon_gen_s_laser_01_mk2_macro')
            with self.assertRaisesRegex(ValueError, 'require --scenario overlap'):
                compose_extension(extensions, **kwargs)
            view = compose_extension(extensions, scenario='overlap', second_range=250, restore_target=True, **kwargs)
            root = ElementTree.parse(view / 'libraries/loadouts.xml')
            original = root.find(".//loadout[@id='x4lan_company_pulsar']")
            second = root.find(".//loadout[@id='x4lan_overlap_second']")
            self.assertEqual([item.get('macro') for item in original.findall('.//weapon')],
                             ['weapon_gen_s_gatling_01_mk2_macro'] * 6)
            self.assertEqual([item.get('macro') for item in second.findall('.//weapon')], [kwargs['second_weapon']] * 6)
            config = (view / 'ui/config.lua').read_text()
            self.assertIn('second_range = 250', config)
            self.assertIn('restore_target = true', config)


if __name__ == '__main__':
    unittest.main()
