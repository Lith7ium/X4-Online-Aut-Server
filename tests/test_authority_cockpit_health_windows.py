"""Offline checks for the native Windows cockpit health correction runner."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools' / 'dev')]

from authority.company_cockpit_health import CompanyCockpitHealthBridge  # noqa: E402
import authority_cockpit_health_windows as runner  # noqa: E402

GENERATION, EPOCH = 'c' * 32, runner.EPOCH


def line(kind, body):
    return f'[=ERROR=] 12.5 [X4LAN] AUTH_COCKPIT_HEALTH_{kind} {body}'


def complete_run():
    lines = []
    for sequence, before, after, target in ((1, (1000, 3000), (401, 1650), (400, 1650)),
                                            (2, (450, 1650), (900.5, 2550), (900, 2550)),
                                            (5, (990, 2550), (650.2, 2100), (650, 2100))):
        lines += [
            line('TRACE', f'age=1 control=Prepare request={sequence} window=1 sequence={sequence}.0 '
                          f'target_shield={target[0]} target_hull={target[1]} shield={before[0]} hull={before[1]}'),
            line('TRACE', f'age=1 control=Apply request={sequence} window=1 sequence={sequence} '
                          f'target_shield=null target_hull=null shield={before[0]} hull={before[1]}'),
            line('DRIVER', f'RECEIPT window=1 sequence={sequence} status=confirmed shield={target[0]} maxshield=1000 '
                           f'hull={target[1]} maxhull=3000 lua_shield={target[0]} lua_hull={target[1]} mono=1'),
            line('READBACK', f'tag=receipt-{sequence}-confirmed age=1 ship=0x1 occupied=1 operational=1 '
                             f'shield={after[0]} maxshield=1000 hull={after[1]} maxhull=3000 shieldpct=1 hullpct=1'),
        ]
    lines += [
        line('DRIVER', 'RX HEALTH revision=1 window=1 sequence=3 anchor=20 deadline_in=-0.7000 shield=250 '
                       'maxshield=1000 hull=900 maxhull=3000'),
        line('READBACK', 'tag=pre_replay age=1 ship=0x1 occupied=1 operational=1 shield=940 maxshield=1000 '
                         'hull=2550 maxhull=3000 shieldpct=1 hullpct=1'),
        line('READBACK', 'tag=post_replay age=1 ship=0x1 occupied=1 operational=1 shield=960 maxshield=1000 '
                         'hull=2550 maxhull=3000 shieldpct=1 hullpct=1'),
    ]
    plan = [dict(label=label, window=1, sequence=sequence) for label, sequence in
            (('down', 1), ('up', 2), ('stale', 3), ('fresh', 5))]
    held = {'message': f'HEALTH|1|{GENERATION}|{EPOCH}|1|1|3|20|1234.5|250|1000|900|3000'}
    return lines, plan, held


class CockpitHealthRunnerTests(unittest.TestCase):
    def evaluate(self, lines, plan, held, stats=None):
        events = [event for event in map(runner.parse_line, lines) if event is not None]
        return runner.evaluate(events, plan, held, stats or {'confirmed': 3, 'unknown': 0, 'rejected': 0},
                               (1000.0, 3000.0))

    def test_complete_run_is_accepted_with_md_readbacks_and_an_ignored_held_update(self):
        report = self.evaluate(*complete_run())
        self.assertTrue(report['accepted'], report)
        self.assertEqual(report['down']['before_md'], {'shield': 1000.0, 'hull': 3000.0})
        self.assertEqual(report['up']['direction'], 'up')
        self.assertEqual(report['stale_pre_gap']['deadline_in_at_arrival'], -0.7)

    def test_a_held_update_that_reaches_the_md_or_changes_hull_is_rejected(self):
        lines, plan, held = complete_run()
        written = lines + [line('TRACE', 'age=2 control=Prepare request=4 window=1 sequence=3 target_shield=250 '
                                         'target_hull=900 shield=960 hull=2550')]
        self.assertFalse(self.evaluate(written, plan, held)['accepted'])
        changed = [text.replace('tag=post_replay age=1 ship=0x1 occupied=1 operational=1 shield=960 maxshield=1000 '
                                'hull=2550', 'tag=post_replay age=1 ship=0x1 occupied=1 operational=1 shield=250 '
                                'maxshield=1000 hull=900') for text in lines]
        self.assertFalse(self.evaluate(changed, plan, held)['accepted'])

    def test_wrong_direction_or_unknown_receipts_are_rejected(self):
        lines, plan, held = complete_run()
        flat = [text.replace('shield=450 hull=1650', 'shield=950 hull=1650') for text in lines]
        self.assertFalse(self.evaluate(flat, plan, held)['up']['ok'])
        self.assertFalse(self.evaluate(lines, plan, held, {'confirmed': 3, 'unknown': 1, 'rejected': 0})['accepted'])

    def test_fixture_refuses_unarmed_reads_and_holds_exactly_one_update(self):
        now = [10.0]
        bridge = CompanyCockpitHealthBridge((GENERATION, EPOCH, 1), clock=lambda: now[0])
        fixture = runner.FixtureServer(bridge)
        gate = runner.hold_next_update(bridge)
        bridge.on_clock(1, True)
        sent = []
        bridge.deliver(lambda message: sent.append(message) or True)
        bridge.on_sample({'sequence': 7, 'capture_time': now[0]})
        self.assertIsNone(fixture.step())
        self.assertEqual(fixture.refused, 1)
        now[0] += 1.0
        bridge.on_sample({'sequence': 8, 'capture_time': now[0]})
        fixture.answer = ('stale', runner.health_state(3, 250, 1000, 900, 3000))
        gate['armed'] = True
        self.assertEqual(fixture.step(), ('stale', 2))
        bridge.deliver(lambda message: sent.append(message) or True)
        self.assertEqual(sent, ['HEALTH_CLEAR|1'])
        self.assertTrue(gate['held'].startswith(f'HEALTH|1|{GENERATION}|{EPOCH}|1|1|2|8|13|250|1000|900|3000'))
        self.assertFalse(gate['armed'])
        self.assertEqual(bridge.stats()['delivered'], 1)

    def test_extension_carries_the_lease_bound_receiver_and_no_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            receiver = (ROOT / runner.RECEIVER_MD).read_bytes().replace(b'__COCKPIT_HEALTH_LEASE__', b'd' * 32)
            target = runner.compose_extension(tmp, profile=45856, epoch=EPOCH, port=47000, lease='d' * 32,
                                              generation='e' * 32, receiver_md=receiver, network_dll=b'dll')
            self.assertEqual(sorted(p.name for p in (target / 'md').iterdir()), [
                'authority_cockpit_health_probe.xml', 'authority_ship_registry.xml', 'company_cockpit_health.xml'])
            for path in (target / 'md').iterdir():
                ET.fromstring(path.read_bytes())
            ui = (target / 'ui.xml').read_text(encoding='utf-8')
            order = [ui.index(name) for name in ('config.lua', 'combat_observer.lua', 'company_cockpit_health.lua',
                                                 'company_motion_source.lua', 'authority_cockpit_health_x4.lua')]
            self.assertEqual(order, sorted(order))
            self.assertNotIn(b'__COCKPIT_HEALTH_LEASE__', (target / 'md' / 'company_cockpit_health.xml').read_bytes())

    @unittest.skipUnless(shutil.which('luajit'), 'luajit is not installed')
    def test_driver_wiring_with_the_real_receiver_and_motion_source_host(self):
        subprocess.run([shutil.which('luajit'), str(ROOT / 'tests/authority_cockpit_health_driver.lua'), str(ROOT)],
                       timeout=10, check=True)


if __name__ == '__main__':
    unittest.main()
