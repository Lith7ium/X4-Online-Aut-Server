"""Check guard and native-adapter wiring without starting X4."""
from pathlib import Path
import shutil
import subprocess
import unittest


class CompanyHullPlaybackX4Tests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_fenced_sector_write_and_independent_native_read(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_hull_playback_x4.lua'],
                       cwd=root, timeout=10, check=True)

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_continuous_playback_at_travel_speed(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_cockpit_continuous_playback.lua'],
                       cwd=root, timeout=10, check=True)
