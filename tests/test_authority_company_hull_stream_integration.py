"""Exercise the complete company hull playback chain without starting X4."""
from pathlib import Path
import shutil
import subprocess
import unittest


class CompanyHullStreamIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_verified_cockpit_samples_to_fenced_native_readback(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_hull_stream_integration.lua'],
                       cwd=root, timeout=10, check=True)
