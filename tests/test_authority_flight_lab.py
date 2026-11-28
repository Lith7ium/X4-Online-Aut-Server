"""Run the flight lab checks without starting X4."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from authority_flight_lab import BASELINE, compare, run_lab

ROOT = Path(__file__).resolve().parents[1]


class FlightLabLuaTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_lab_lua_checks(self):
        subprocess.run(['luajit', 'tests/authority_flight_lab.lua'], cwd=ROOT, timeout=120, check=True)


@unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
class FlightLabScorecardTests(unittest.TestCase):
    def setUp(self):
        self.baseline = json.loads((ROOT / BASELINE).read_text(encoding='utf-8'))

    def test_off_equals_baseline(self):
        self.assertEqual(compare(self.baseline, run_lab(ROOT)), [])

    def test_gain_holds_on_three_seeds(self):
        for result in run_lab(ROOT, seeds=(1, 2, 3), wan=('speed_limit',)):
            if result['key'].startswith(('travel_brake/poor/', 'steady_345/poor/')):
                self.assertLess(result['longest_freeze_s'], 0.5, result['key'])

    def test_loopback_not_worse(self):
        before = {item['key']: item for item in self.baseline}
        for result in run_lab(ROOT, wan=('speed_limit',)):
            if '/loopback/' in result['key']:
                self.assertLessEqual(result['position_error_m']['p95'],
                                     before[result['key']]['position_error_m']['p95'] + 1.0, result['key'])


if __name__ == '__main__':
    unittest.main()
