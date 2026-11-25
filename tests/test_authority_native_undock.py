"""Offline checks for the provider's one-shot stock undock evidence."""
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from authority_native_undock import NativeUndockProvider


BINDING = dict(registry_epoch='a' * 32, ship_index=7354, ship_code='WRN-302',
               captain_seed='2211373338772438693')
REQUEST = dict(request_id='b' * 32, sector='sector_a', station_code='BIM-169',
               bay_seed='507326418098681806', bay_macro='external_bay', dockarea_macro='dock_area')
ACTORS = ('0x101', '0x102', '0x103', '0x104')


class NativeUndockTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.provider = NativeUndockProvider(clock=lambda: self.now)
        self.addCleanup(self.provider.close)
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.peer.close)
        self.peer.settimeout(1)
        self.peer.connect(self.provider._socket.getsockname())
        self.provider.bind(dict(pid=123, start_ticks=456, exe_dev=789, exe_ino=100), lambda: True)
        owner = patch('authority_native_undock.socket_owner', return_value=True)
        self.addCleanup(owner.stop)
        owner.start()
        self.peer.send(f'UNDOCK_HELLO|{self.provider._lease}'.encode())

    def row(self, status, *, witness='0', actors=ACTORS, station=REQUEST['station_code']):
        ordered = (REQUEST['sector'], station, REQUEST['bay_seed'], REQUEST['bay_macro'], REQUEST['dockarea_macro'])
        row = (REQUEST['request_id'], status, BINDING['registry_epoch'], str(BINDING['ship_index']),
               BINDING['ship_code'], *ordered, BINDING['captain_seed'], witness, 'Wait', 'running', *actors)
        self.peer.send('|'.join(('UNDOCK_RESULT', self.provider._lease, *row)).encode())

    def begin(self):
        self.assertIsNone(self.provider.begin(BINDING, REQUEST))
        wire = self.peer.recv(1024).decode().split('|')
        self.assertEqual(wire[:4], ['UNDOCK', self.provider._lease, REQUEST['request_id'], BINDING['registry_epoch']])
        self.assertEqual(wire[-1], BINDING['captain_seed'])

    def test_only_correlated_later_readback_confirms(self):
        self.begin()
        self.row('awaiting')
        self.assertIsNone(self.provider.poll())
        self.row('undocked', witness='1')
        expected = dict(REQUEST, outcome='confirmed')
        self.assertEqual(self.provider.poll(), expected)
        self.assertEqual(self.provider.begin(BINDING, REQUEST), expected)
        with self.assertRaisesRegex(ValueError, 'request ID reused'):
            self.provider.begin(BINDING, dict(REQUEST, bay_seed='1'))

    def test_changed_actors_leave_dispatch_unknown_and_fenced(self):
        self.begin()
        self.row('awaiting')
        self.assertIsNone(self.provider.poll())
        self.row('undocked', witness='1', actors=(*ACTORS[:3], '0x105'))
        self.assertEqual(self.provider.poll(), dict(REQUEST, outcome='unknown'))
        another = dict(REQUEST, request_id='c' * 32)
        self.assertEqual(self.provider.begin(BINDING, another),
                         dict(another, outcome='refused', reason='provider_limit'))

    def test_late_readback_has_no_confirmation(self):
        self.begin()
        self.row('awaiting')
        self.assertIsNone(self.provider.poll())
        self.now = 61
        self.row('undocked', witness='1')
        self.assertEqual(self.provider.poll(), dict(REQUEST, outcome='unknown'))

    def test_busy_is_a_definite_refusal(self):
        self.begin()
        self.row('busy')
        self.assertEqual(self.provider.poll(), dict(REQUEST, outcome='refused', reason='busy'))

    def test_unmatched_starting_berth_has_no_confirmation(self):
        self.begin()
        self.row('awaiting', station='ABC-123')
        self.assertEqual(self.provider.poll(), dict(REQUEST, outcome='unknown'))

    def test_stages_original_trial_only_in_private_provider_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            lab = case / 'extensions/x4_lan_lab'
            (lab / 'ui').mkdir(parents=True)
            (lab / 'md').mkdir()
            bridge = lab / 'ui/authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n local ok,reason=bridge:update();assert(ok,reason)\n')
            xml = lab / 'ui.xml'
            xml.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            cue, driver, _, _ = self.provider.install(case)
            self.assertEqual(cue.read_bytes(), (Path(__file__).resolve().parents[1] / 'tools/dev' / cue.name).read_bytes())
            self.assertIn("AddUITriggeredEvent(screen, 'Run'", driver.read_text())
            self.assertIn('native_undock_tick(C.IsGamePaused())', bridge.read_text())
            self.assertLess(xml.read_text().index(driver.name), xml.read_text().index(bridge.name))
