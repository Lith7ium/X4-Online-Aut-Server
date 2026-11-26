import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools' / 'dev')]

import authority_combat_overlap_analysis as A

P = '[Scripts] 1 *** Context:md.X4AuthorityCombatOverlapTrace.Fired<inst:1>: [X4LAN] AUTH_'


def fire(age, shooter, proj):
    return f'{P}OVERLAP_FIRE age={age} shooter={shooter} weapon=0xw{shooter} proj={proj} exists=1 pmacro=bullet_gen_s_gatling_01_mk2_macro'


def impact(age, kind, amount, value, gone=''):
    count = len([g for g in gone.split(';') if g])
    return f'{P}OVERLAP_IMPACT age={age} kind={kind} amount={amount} value={value} comp=0x9 gone={count} alive=0 ids={gone}'


def sweep(age, tag, gone=''):
    count = len([g for g in gone.split(';') if g])
    return f'{P}OVERLAP_SWEEP age={age} tag={tag} gone={count} alive=0 ids={gone}'


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
            sweep(1.1, 'control_pilot_stop'),
            impact(1.2, 'shield', 26, 74, '1:0xa:0.2;'),
            sweep(3.0, 'control_pilot_end', '1:0xb:1.9;'),
        ])
        result = A.analyse(text, [])['control_pilot']
        self.assertEqual(result['frame_classes'], {'unresolved_stop_sweep_same_frame': 1, 'unresolved_last_frame': 1})

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


def ev(seq, age, kind, amount=0.0, shield=500.0, hull=2500.0, last='company_player', attacker='none', phase='overlap'):
    from types import SimpleNamespace
    return SimpleNamespace(seq=seq, age=age, kind=kind, amount=amount, shield=shield, hull=hull, phase=phase,
                           victim_label='npc_target', last_att_label=last, ev_att_label=attacker)


class DamageAttributionTest(unittest.TestCase):
    baselines = {'1': {'26': 4}, '2': {'36': 4}}

    def test_shield_only_amounts_name_the_shooter_and_are_compared(self):
        events = [
            ev(1, 2.0, 'object_attacked', attacker='company_player'),
            ev(2, 2.0, 'shield_damaged', 26),
            ev(3, 2.0, 'object_attacked', last='second_shooter', attacker='second_shooter'),
            ev(4, 2.0, 'shield_damaged', 36, last='second_shooter'),
            ev(5, 2.01, 'shield_damaged', 36, last='company_player'),
        ]
        result = A.damage_attribution(events, 'overlap', self.baselines)['shield_only']
        self.assertEqual((result['callbacks'], result['lastattacker_agrees'], result['lastattacker_differs']), (3, 2, 1))
        self.assertEqual((result['notification_in_frame_agrees'], result['no_notification_yet_in_frame']), (2, 1))

    def test_overflow_counts_only_when_one_bullet_explains_it(self):
        clean = [ev(1, 3.0, 'shield_damaged', 10, shield=0), ev(2, 3.0, 'hull_damaged', 16)]
        result = A.damage_attribution(clean, 'overlap', self.baselines)
        self.assertEqual((result['classes'], result['overflow_one_bullet']['by_damage']), ({'overflow_one_bullet': 1}, {'1': 1}))
        # 10 + 26 = 36 could be one laser bullet or a gatling remainder next to a separate gatling hit.
        unclear = [ev(1, 3.0, 'shield_damaged', 10, shield=0), ev(2, 3.0, 'hull_damaged', 26)]
        self.assertEqual(A.damage_attribution(unclear, 'overlap', self.baselines)['classes'],
                         {'overflow_ambiguous': 1, 'hull_only': 1})

    def test_same_gun_on_both_ships_assigns_nothing(self):
        result = A.damage_attribution([ev(1, 2.0, 'shield_damaged', 26)], 'overlap', {'1': {'26': 3}, '2': {'26': 3}})
        self.assertEqual((result['damage_values'], result['classes']), ({}, {'shield_unknown_amount': 1}))


if __name__ == '__main__':
    unittest.main()
