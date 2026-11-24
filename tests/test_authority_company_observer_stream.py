"""Run the game-side wire and two-slot observation checks in LuaJIT."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CompanyObserverWireTests(unittest.TestCase):
    def test_local_sight_and_map_receipts(self):
        subprocess.run(['luajit', 'tests/authority_company_observer_stream.lua'], cwd=ROOT, check=True)
