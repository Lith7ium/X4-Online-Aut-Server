from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import authority_company_fixture_prepare as fixture


class CompanyFixtureTests(unittest.TestCase):
    def test_mode_is_off_by_default_and_requires_snapshot_without_tls(self):
        options = SimpleNamespace(prepare_disposable_company_fixture=True)
        self.assertFalse(fixture.validate_mode(SimpleNamespace(), require_snapshot=True, tls=None))
        self.assertTrue(fixture.validate_mode(options, require_snapshot=True, tls=None))
        for snapshot, tls in ((False, None), (True, object())):
            with self.assertRaises(ValueError):
                fixture.validate_mode(options, require_snapshot=snapshot, tls=tls)

    def test_source_is_pinned_and_native_save_requires_second_company_owner(self):
        checkpoint = SimpleNamespace(sha256=fixture.SOURCE_DIGEST)
        evidence = {'epoch': fixture.EPOCH, 'issued_count': fixture.COUNT, 'described_ships': {
            index: dict(code=code, macro=macro, sector=sector,
                        owner='player' if index == fixture.FIRST else 'ownerless',
                        **{'class': 'ship_s', 'connection': 'space', 'parent_class': 'zone'})
            for index, (code, macro, sector) in fixture.SHIPS.items()}}
        with patch.object(fixture, 'verify_join_snapshot', return_value=evidence) as observe:
            self.assertEqual(fixture.load_source(checkpoint), evidence['described_ships'])
            observe.assert_called_once_with(checkpoint, describe_indices=(fixture.FIRST, fixture.SECOND))
            with self.assertRaisesRegex(ValueError, 'pinned private seed'):
                fixture.load_source(SimpleNamespace(sha256='0' * 64))
            with self.assertRaisesRegex(ValueError, 'saved ship 7354 differs'):
                fixture.verify_saved(checkpoint)
            evidence['described_ships'][fixture.SECOND]['owner'] = 'player'
            self.assertEqual(fixture.verify_saved(checkpoint), evidence['described_ships'])
            evidence['described_ships'][fixture.FIRST]['owner'] = 'ownerless'
            with self.assertRaisesRegex(ValueError, 'saved ship 1970 differs'):
                fixture.verify_saved(checkpoint)

    def test_installed_gate_runs_after_registry_ready_and_logs_unique_result(self):
        nonce = '8' * 32
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            lab = case / 'extensions/x4_lan_lab'
            (lab / 'ui').mkdir(parents=True)
            (lab / 'md').mkdir()
            probe = lab / 'ui/authority_world_bridge_probe.lua'
            probe.write_text('''local registry_checkpoint, registry_checkpoint_pause_time, registry_checkpoint_started
local activated=false
local function frame()
 if not registry_checkpoint:ready() then return end
 if not activated then
  activated=true
 end
end
''', encoding='ascii')
            _, script = fixture.install(case, nonce)
            text = probe.read_text(encoding='ascii')
            self.assertIn(f"nonce='{nonce}'", text)
            self.assertLess(text.index('registry_checkpoint:ready()'), text.index('AddUITriggeredEvent'))
            self.assertLess(text.index('AddUITriggeredEvent'), text.index('activated=true'))
            self.assertEqual(script.read_bytes(), fixture.SCRIPT.read_bytes())
            subprocess.run(['luajit', '-b', str(probe), '/dev/null'], check=True)
            with self.assertRaises(ValueError):
                fixture.install(case, nonce)
        row = fixture.TAG + (fixture._prefix(nonce) + 'COMPANY\n').encode()
        fixture.verify_log(Path('probe.log'), nonce, lambda *_: row)
        for raw in (b'', row + row, row.replace(b'COMPANY\n', b'PILOT_PRESENT\n')):
            with self.assertRaises(ValueError):
                fixture.verify_log(Path('probe.log'), nonce, lambda *_: raw)
