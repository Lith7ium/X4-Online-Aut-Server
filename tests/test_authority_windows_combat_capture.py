"""Opt-in Windows combat capture: receipt format, verifier hand-off and overlay; no native X4 run."""
from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from authority.checkpoints import Checkpoint
from authority.client_seat_overlay import extend_seat_gate
from authority.combat_capture import (
    capture_config, collect_hits, new_receipt, validate_capture_lua, verifier_arguments, write_receipt,
)
from authority.combat_observation import MD_EVENT_PREFIX, validate_md_read_only
from authority.experimental_windows_fixture_overlay import install_fixture_overlay
from test_authority_combat_observation import _sample_event_line
from test_authority_registry_availability_evidence import EPOCH, RUN, build_xml
import test_authority_windows_full_registry_overlay as full_registry_overlay  # module import: no rerun
from tools.dev import authority_combat_hit_evidence as verifier

CAPTURE_LUA = ROOT / 'src/authority/lua/client_combat_capture.lua'
OBSERVER_MD = ROOT / 'src/authority/md/combat_observer.xml'
CHECKPOINT_ID = 'checkpoint-' + 'c' * 32
SHIPS = ('<component class="ship_s" id="[0x101]" owner="player" code="HVC-420"/>'
         '<component class="ship_s" id="[0x102]" owner="xenon" code="VLY-685"/>')
PILOT = dict(victim='0x7a1', v_label='admitted_pilot', v_code='HVC-420', v_owner='player', v_reg=1,
             v_ctrl=1, v_occ=1, ev_att='0x7b2', ea_label='none', ea_code='VLY-685', ea_owner='xenon',
             ea_reg=2, ea_isship=1, last_att='0x7b2', la_label='none', la_code='VLY-685',
             la_owner='xenon', la_reg=2, la_ctrl=0, la_isship=1)


def _log_line(payload, epoch=EPOCH):
    return ('[Scripts] 812.40 *** Context:md.X4AuthorityCombatObserver.Observe<inst:1>: '
            + MD_EVENT_PREFIX + payload + ('|' + epoch if epoch else ''))


def _pilot_log():
    return '\n'.join((
        '[General] 800.00 unrelated line',
        _log_line(_sample_event_line(1, 'attacked', 9.0, **PILOT)),
        _log_line(_sample_event_line(2, 'shield_damaged', 9.5, **PILOT, amount=52.0, shield=4748.0)),
        # Another ship's hit and a hit on another epoch are not this pilot's capture.
        _log_line(_sample_event_line(3, 'hull_damaged', 9.6, victim='0x7b2', v_code='VLY-685',
                                     v_owner='xenon', v_reg=2)),
        _log_line(_sample_event_line(4, 'hull_damaged', 9.7, **PILOT), epoch='9' * 32),
        _log_line(_sample_event_line(5, 'hull_damaged', 9.8, **PILOT, amount=12.5, shield=0.0, hull=2087.5)),
    )) + '\n'


class CombatCaptureReceiptTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        compressed = gzip.compress(build_xml((1, 1), definitions=SHIPS))
        save = self.directory / 'save.xml.gz'
        save.write_bytes(compressed)
        self.sha256 = hashlib.sha256(compressed).hexdigest()
        self.checkpoint = Checkpoint(CHECKPOINT_ID, save, self.sha256, len(compressed), RUN)
        self.log = self.directory / 'x4authority.log'
        self.log.write_text(_pilot_log(), encoding='utf-8')

    def verify(self, arguments):
        store = Mock()
        store.get.return_value = self.checkpoint
        output = io.StringIO()
        with patch.object(verifier, 'CheckpointStore', return_value=store), \
                patch.object(sys, 'argv', ['authority_combat_hit_evidence.py', *arguments]), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            verifier.main()
        store.get.assert_called_once_with(CHECKPOINT_ID)
        return json.loads(output.getvalue())

    def test_receipt_preserves_offer_seat_and_native_sequences_for_the_verifier(self):
        receipt = collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1),
                               self.log.read_text(encoding='utf-8'))
        self.assertEqual([hit['sequence'] for hit in receipt['hits']], [2, 5])
        self.assertEqual(receipt['hits'][0], {
            'sequence': 2, 'event': 'shield_damaged', 'victim_code': 'HVC-420',
            'victim_occupied_by_player': True, 'victim_controlled_by_player': True,
            'event_attacker': {'index': 2, 'code': 'VLY-685'},
            'last_attacker': {'index': 2, 'code': 'VLY-685'},
            'amount': 52.0, 'shield_after': 4748.0, 'hull_after': 2100.0})
        path = self.directory / 'combat-capture.json'
        write_receipt(path, receipt)
        with self.assertRaises(FileExistsError):
            write_receipt(path, receipt)  # never replaces an earlier capture
        stored = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(stored, receipt)
        self.assertEqual(list(stored)[:5], ['format', 'checkpoint_id', 'checkpoint_sha256',
                                           'registry_epoch', 'piloted_ship_index'])
        for hit in stored['hits']:
            with self.subTest(sequence=hit['sequence']):
                arguments = verifier_arguments(stored, self.directory / 'store', self.log, hit['sequence'])
                self.assertEqual(arguments[arguments.index('--piloted-ship-index') + 1], '1')
                report = self.verify(arguments)
                self.assertEqual(report['checkpoint_id'], CHECKPOINT_ID)
                self.assertEqual(report['checkpoint_sha256'], self.sha256)
                self.assertEqual(report['registry_epoch'], EPOCH)
                self.assertEqual(report['sequence'], hit['sequence'])
                self.assertEqual(report['victim'], {'index': 1, 'code': 'HVC-420'})
                self.assertEqual(report['event_attacker'], hit['event_attacker'])
                self.assertEqual(report['last_attacker'], hit['last_attacker'])
                self.assertTrue(report['victim_occupied_by_player'] and report['victim_controlled_by_player'])

    def test_wrong_offer_seat_or_sequence_is_refused(self):
        receipt = collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1),
                               self.log.read_text(encoding='utf-8'))
        for sequence in (1, 3, 4, 6):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                verifier_arguments(receipt, self.directory / 'store', self.log, sequence)
        other_digest = dict(receipt, checkpoint_sha256='0' * 64)
        with self.assertRaises(SystemExit):
            self.verify(verifier_arguments(other_digest, self.directory / 'store', self.log, 2))
        other_seat = dict(receipt, piloted_ship_index=2, hits=[{'sequence': 2}])
        with self.assertRaises(SystemExit):
            self.verify(verifier_arguments(other_seat, self.directory / 'store', self.log, 2))
        for values in ((CHECKPOINT_ID, 'C' * 64, EPOCH, 1), ('checkpoint-x', self.sha256, EPOCH, 1),
                       (CHECKPOINT_ID, self.sha256, EPOCH[:-1], 1), (CHECKPOINT_ID, self.sha256, EPOCH, 0),
                       (CHECKPOINT_ID, self.sha256, EPOCH, True)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                new_receipt(*values)
        repeated = _pilot_log() + _log_line(_sample_event_line(2, 'hull_damaged', 9.9, **PILOT)) + '\n'
        with self.assertRaisesRegex(ValueError, 'repeats'):
            collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1), repeated)


class CombatCaptureOverlayTest(unittest.TestCase):
    setUp = full_registry_overlay.FullRegistryOverlayTest.setUp

    def test_capture_is_opt_in_read_only_and_after_seat_admission(self):
        options = dict(self.options, profile_id=45850, save_path=r'D:\Test\45850\save', seat_index=1)
        for capture in (False, True):
            with self.subTest(capture=capture):
                view = self.view.parent / f'capture-{capture}'
                (view / 'extensions').mkdir(parents=True)
                (view / '.x4ls-view').write_text('synthetic view')
                manifest = install_fixture_overlay(view, **options, combat_capture=capture)
                target = view / 'extensions/x4_lan_lab'
                config = (target / 'ui/config.lua').read_text()
                probe = (target / 'ui/authority_download_load_probe.lua').read_text()
                scripts = [entry.get('name') for entry in ElementTree.parse(target / 'ui.xml').iter('file')]
                added = {'md/combat_observer.xml', 'ui/client_combat_capture.lua'}
                if not capture:
                    self.assertFalse(added & set(manifest))
                    self.assertNotIn('authority_combat_capture', config)
                    self.assertNotIn('X4LAN_COMBAT_CAPTURE', probe)
                    continue
                self.assertLessEqual(added, set(manifest))
                self.assertIn(',authority_combat_capture={epoch="' + '1' * 32 + '",index=1}}', config)
                self.assertLess(scripts.index('ui/client_combat_capture.lua'),
                                scripts.index('ui/authority_download_load_probe.lua'))
                self.assertLess(probe.index('X4LAN_AUTHORITY_SELECTED_SEAT=client_seat'),
                                probe.index('X4LAN_COMBAT_CAPTURE.arm(client_seat)'))
                self.assertEqual((target / 'md/combat_observer.xml').read_bytes(), OBSERVER_MD.read_bytes())
        for change in ({'combat_capture': True}, {'combat_capture': 1, 'profile_id': 45850,
                                                   'save_path': r'D:\Test\45850\save', 'seat_index': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                install_fixture_overlay(self.view, **(self.options | change))
            self.assertFalse((self.view / 'extensions/x4_lan_lab').exists())

    def test_seat_gate_config_and_module_contract(self):
        text = ("local full_registry_inspect=nil\n"
                "  assert(full_registry_inspect:guard(),'full registry guard lost before admission')\n")
        registry = {'epoch': EPOCH, 'issued_count': 2, 'operational_bits': '10'}
        selected = '  X4LAN_AUTHORITY_SELECTED_SEAT=client_seat\n'
        plain = extend_seat_gate(text, 1, registry)
        self.assertEqual(extend_seat_gate(text, 1, registry, combat_capture=True),
                         plain.replace(selected, selected + '  X4LAN_COMBAT_CAPTURE.arm(client_seat)\n'))
        self.assertEqual(capture_config(registry, 1), f',authority_combat_capture={{epoch="{EPOCH}",index=1}}')
        for index in (0, 2, 3, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                capture_config(registry, index)
        validate_md_read_only(OBSERVER_MD.read_text(encoding='utf-8'))
        source = CAPTURE_LUA.read_text(encoding='utf-8')
        self.assertEqual(validate_capture_lua(source)['controls'], ['Arm', 'Watch'])
        for unsafe in (source + 'SetObjectHull(ship, 1)\n',
                       source.replace("send('Watch'", "send('Disarm'"),
                       source.replace("AddUITriggeredEvent('X4AuthorityCombatObserver'", "AddUITriggeredEvent('Other'"),
                       source + "AddUITriggeredEvent('X4AuthorityCombatObserver', 'Arm', {})\n"):
            with self.subTest(unsafe=unsafe[-60:]), self.assertRaises(ValueError):
                validate_capture_lua(unsafe)


class CombatCaptureLuaTest(unittest.TestCase):
    def load(self, index=1):
        try:
            from lupa.luajit21 import LuaRuntime
        except ImportError:
            self.skipTest('lupa with LuaJIT 2.1 is not installed')
        lua = LuaRuntime(unpack_returned_tuples=True)
        sent, logs, handlers = [], [], {}
        lua.globals().DebugError = logs.append
        lua.globals().RegisterEvent = lambda name, handler: handlers.__setitem__(name, handler)
        lua.globals().AddUITriggeredEvent = lambda screen, control, values: sent.append(
            (screen, control, dict(values.items())))
        lua.execute(f'X4LAN_CONFIG={{authority_combat_capture={{epoch="{EPOCH}",index={index}}}}}')
        module = lua.execute(CAPTURE_LUA.read_text(encoding='utf-8'))
        return module, sent, logs, handlers['X4AuthorityCombatObserver.Status']

    def test_arms_on_offered_epoch_then_watches_only_the_admitted_index(self):
        module, sent, logs, status = self.load()
        self.assertFalse(module.arm(None))
        self.assertEqual(sent, [])
        self.assertTrue(module.arm('seat'))
        self.assertTrue(module.arm('seat'))  # one Arm request until the observer answers
        self.assertEqual(sent, [('X4AuthorityCombatObserver', 'Arm', {
            'epoch': EPOCH, 'watch_player_ship': True, 'watch_player_owned': False, 'request_id': 1})])
        status(None, f'7|Armed|{EPOCH}|{EPOCH}')  # another request is ignored
        self.assertEqual(module.state(), 'arming')
        status(None, f'1|Armed|{EPOCH}|{EPOCH}')
        self.assertTrue(module.arm('seat'))
        self.assertEqual(sent[1], ('X4AuthorityCombatObserver', 'Watch', {
            'registry_index': 1, 'label': 'admitted_pilot', 'as_target': True, 'as_source': False,
            'watch_weapons': False, 'request_id': 2}))
        status(None, '2|Watched|admitted_pilot|0x7a1|HVC-420|player|1|0')
        self.assertEqual(module.state(), 'capturing')
        self.assertTrue(module.arm('seat'))
        self.assertEqual(len(sent), 2)
        self.assertEqual(logs, [f'[X4LAN] AUTH_COMBAT_CAPTURE capturing epoch={EPOCH} index=1 '
                                'code=HVC-420 owner=player'])

    def test_refusals_stop_the_capture_without_further_requests(self):
        for armed, watched in ((f'1|Armed|{EPOCH}', None), (f'1|Armed|{EPOCH}|{"9" * 32}', None),
                               (f'1|Armed|{EPOCH}|{EPOCH}', '2|WatchRefused|admitted_pilot'),
                               (f'1|Armed|{EPOCH}|{EPOCH}', '2|Watched|admitted_pilot|0x7b2|VLY-685|xenon|2|0')):
            with self.subTest(armed=armed, watched=watched):
                module, sent, logs, status = self.load()
                module.arm('seat')
                status(None, armed)
                if watched is not None:
                    module.arm('seat')
                    status(None, watched)
                self.assertEqual(module.state(), 'failed')
                self.assertFalse(module.arm('seat'))
                self.assertEqual(len(sent), 1 if watched is None else 2)
                self.assertEqual(len(logs), 1)
                self.assertIn('AUTH_COMBAT_CAPTURE refused', logs[0])


if __name__ == '__main__':
    unittest.main()
