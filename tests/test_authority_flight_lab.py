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
    def test_scorecard_matches_baseline(self):
        baseline = json.loads((ROOT / BASELINE).read_text(encoding='utf-8'))
        self.assertEqual(compare(baseline, run_lab(ROOT)), [])


if __name__ == '__main__':
    unittest.main()
