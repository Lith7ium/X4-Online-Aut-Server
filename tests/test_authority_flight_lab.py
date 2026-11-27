"""Run the flight lab checks without starting X4."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FlightLabLuaTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_lab_lua_checks(self):
        subprocess.run(['luajit', 'tests/authority_flight_lab.lua'], cwd=ROOT, timeout=120, check=True)


if __name__ == '__main__':
    unittest.main()
