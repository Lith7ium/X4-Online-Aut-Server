"""Opt-in Windows combat capture: receipt format, verifier hand-off and overlay; no native X4 run."""
from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
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
    capture_config, collect_hits, new_receipt, private_capture_observer, validate_capture_lua,
    verifier_arguments, write_receipt,
)
from authority.combat_observation import MD_EVENT_PREFIX, validate_md_read_only
from authority import experimental_windows_fixture_overlay as overlay
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
    return ('[Scripts] 812.40 *** Context:md.X4AuthorityClientCombatCapture.Observe<inst:1>: '
            + '[X4AuthorityClientCombatCapture] '
            + MD_EVENT_PREFIX + payload + ('|' + epoch if epoch else ''))


def _pilot_log(digest):
    marker = (f'[X4LAN] AUTH_COMBAT_CAPTURE capturing epoch={EPOCH} index=1 checkpoint={CHECKPOINT_ID} '
              f'sha256={digest} code=HVC-420 owner=player')
    return '\n'.join((
        '[General] 800.00 unrelated line',
        _log_line(_sample_event_line(11, 'shield_damaged', 8.0, **PILOT)),
        marker,
        _log_line(_sample_event_line(1, 'attacked', 9.0, **PILOT)),
        _log_line(_sample_event_line(2, 'shield_damaged', 9.5, **PILOT, amount=52.0, shield=4748.0)),
        # Another ship's hit and a hit on another epoch are not this pilot's capture.
        _log_line(_sample_event_line(3, 'hull_damaged', 9.6, victim='0x7b2', v_code='VLY-685',
                                     v_owner='xenon', v_reg=2)),
        _log_line(_sample_event_line(4, 'hull_damaged', 9.7, **PILOT), epoch='9' * 32),
        _log_line(_sample_event_line(5, 'hull_damaged', 9.8, **PILOT, amount=12.5, shield=0.0, hull=2087.5)),
        _log_line(_sample_event_line(12, 'hull_damaged', 9.81, **PILOT)).replace(
            '[X4AuthorityClientCombatCapture] ', ''),
        f'[X4LAN] AUTH_COMBAT_CAPTURE stopped epoch={EPOCH} index=1',
        _log_line(_sample_event_line(13, 'hull_damaged', 9.9, **PILOT)),
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
        self.log.write_text(_pilot_log(self.sha256), encoding='utf-8')

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
        repeated = _pilot_log(self.sha256) + _log_line(_sample_event_line(2, 'hull_damaged', 9.9, **PILOT)) + '\n'
        with self.assertRaisesRegex(ValueError, 'repeats'):
            collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1), repeated)
        for checkpoint, digest in ((CHECKPOINT_ID, '1' * 64), ('checkpoint-' + 'f' * 32, self.sha256)):
            with self.subTest(checkpoint=checkpoint, digest=digest), self.assertRaisesRegex(ValueError, 'no active'):
                collect_hits(new_receipt(checkpoint, digest, EPOCH, 1), _pilot_log(self.sha256))

    def test_all_hits_use_one_offered_registry_scan(self):
        receipt = collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1),
                               self.log.read_text(encoding='utf-8'))
        arguments = verifier_arguments(receipt, self.directory / 'store', self.log)
        self.assertNotIn('--sequence', arguments)
        with patch.object(verifier, 'verify_join_snapshot', wraps=verifier.verify_join_snapshot) as scan:
            report = self.verify(arguments)
        scan.assert_called_once()
        self.assertEqual([hit['sequence'] for hit in report['hits']], [2, 5])
        self.assertEqual([hit['event'] for hit in report['hits']], ['shield_damaged', 'hull_damaged'])
        self.assertTrue(all(hit['victim'] == {'index': 1, 'code': 'HVC-420'} for hit in report['hits']))

    def test_single_sequence_refuses_hit_from_another_capture_window(self):
        original = self.log.read_text(encoding='utf-8')
        receipt = collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1), original)
        arguments = verifier_arguments(receipt, self.directory / 'store', self.log, 2)
        for current, replacement in ((CHECKPOINT_ID, 'checkpoint-' + 'f' * 32),
                                     (self.sha256, '1' * 64)):
            with self.subTest(changed_window_field=current), self.assertRaises(SystemExit):
                self.log.write_text(original.replace(current, replacement, 1), encoding='utf-8')
                self.verify(arguments)

    def test_only_private_observer_duplicates_invalidate_capture_sequence(self):
        original = self.log.read_text(encoding='utf-8')
        duplicate = _log_line(_sample_event_line(2, 'shield_damaged', 9.51, **PILOT,
                                                  amount=1.0, shield=4747.0))
        stop = f'[X4LAN] AUTH_COMBAT_CAPTURE stopped epoch={EPOCH} index=1'
        generic_row = duplicate.replace('[X4AuthorityClientCombatCapture] ', '').replace(
            'md.X4AuthorityClientCombatCapture.', 'md.X4AuthorityCombatObserver.')
        mixed = original.replace(stop, generic_row + '\n' + stop)
        self.log.write_text(mixed, encoding='utf-8')
        receipt = collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1), mixed)
        self.assertEqual([hit['sequence'] for hit in receipt['hits']], [2, 5])
        report = self.verify(verifier_arguments(receipt, self.directory / 'store', self.log, 2))
        self.assertEqual((report['sequence'], report['amount']), (2, 52.0))

        repeated = original.replace(stop, duplicate + '\n' + stop)
        self.log.write_text(repeated, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'repeats'):
            collect_hits(new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1), repeated)
        with self.assertRaises(SystemExit):
            self.verify(verifier_arguments(receipt, self.directory / 'store', self.log, 2))

    def test_streamed_capture_accepts_reordered_json_and_preserves_output_on_invalid_data(self):
        receipt = new_receipt(CHECKPOINT_ID, self.sha256, EPOCH, 1)
        with self.log.open(encoding='utf-8') as lines:
            captured = collect_hits(json.loads(json.dumps(receipt, sort_keys=True)), lines)
        self.assertEqual([hit['sequence'] for hit in captured['hits']], [2, 5])
        target = self.directory / 'capture.json'
        captured['hits'][0]['amount'] = float('nan')
        with self.assertRaises(ValueError):
            write_receipt(target, captured)
        self.assertFalse(target.exists())


class CombatCaptureOverlayTest(unittest.TestCase):
    setUp = full_registry_overlay.FullRegistryOverlayTest.setUp

    def test_capture_overlay_preserves_source_line_endings(self):
        read_source = overlay._source
        options = dict(self.options, profile_id=45850, save_path=r'D:\Test\45850\save',
                       seat_index=1, combat_capture=True)
        outputs = {}
        for ending in (b'\n', b'\r\n'):
            view = self.view.parent / f'capture-eol-{len(ending)}'
            (view / 'extensions').mkdir(parents=True)
            (view / '.x4ls-view').touch()

            def source(path, maximum):
                data = read_source(path, maximum)
                if path.suffix in ('.lua', '.xml'):
                    return data.replace(b'\r\n', b'\n').replace(b'\n', ending)
                return data

            with self.subTest(ending=ending), patch.object(overlay, '_source', side_effect=source):
                manifest = install_fixture_overlay(view, **options)
                lab = view / 'extensions/x4_lan_lab'
                outputs[ending] = {}
                for name in ('ui/authority_download_load_probe.lua', 'ui/local_udp_module.lua',
                             'md/client_combat_capture.xml', 'ui/client_combat_capture.lua'):
                    data = (lab / name).read_bytes()
                    normalized = data.replace(b'\r\n', b'\n')
                    self.assertEqual(data, normalized.replace(b'\n', ending), name)
                    self.assertEqual(manifest[name], hashlib.sha256(data).hexdigest())
                    outputs[ending][name] = normalized
        self.assertEqual(outputs[b'\n'], outputs[b'\r\n'])

    def test_load_probe_hook_rejects_duplicates_with_either_line_ending(self):
        read_source = overlay._source
        hook = b" elseif state=='observing' then\n"
        for ending in (b'\n', b'\r\n'):
            def source(path, maximum):
                data = read_source(path, maximum)
                if path.name == 'authority_download_load_probe.lua':
                    return (data.replace(b'\r\n', b'\n') + hook).replace(b'\n', ending)
                return data

            with self.subTest(ending=ending), patch.object(overlay, '_source', side_effect=source):
                with self.assertRaisesRegex(ValueError, 'unexpected load probe hook'):
                    install_fixture_overlay(self.view, **self.options)

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
                added = {'md/client_combat_capture.xml', 'ui/client_combat_capture.lua'}
                if not capture:
                    self.assertFalse(added & set(manifest))
                    self.assertNotIn('authority_combat_capture', config)
                    self.assertNotIn('X4LAN_COMBAT_CAPTURE', probe)
                    continue
                self.assertLessEqual(added, set(manifest))
                offer = options['bound_offer'].offer
                self.assertIn(capture_config(self.snapshot, 1, offer.checkpoint_id, offer.sha256), config)
                self.assertLess(scripts.index('ui/client_combat_capture.lua'),
                                scripts.index('ui/authority_two_x4_participant_probe.lua'))
                self.assertNotIn('X4LAN_COMBAT_CAPTURE', probe)
                observer = (target / 'md/client_combat_capture.xml').read_text()
                self.assertEqual(observer, private_capture_observer(OBSERVER_MD.read_text()))
                self.assertIn('<mdscript name="X4AuthorityClientCombatCapture"', observer)
                self.assertNotIn('X4AuthorityCombatObserver', observer)
                self.assertEqual(observer.count('<set_value name="State.$seq" exact="0"/>'), 1)
                tree = ElementTree.fromstring(observer)
                for name in ('ShieldDamaged', 'HullDamaged', 'ObjectAttackedObject'):
                    gate = tree.find(f".//cue[@name='{name}']/conditions/check_value")
                    self.assertEqual(gate.get('value'), 'State.$armed and @player.controlled == event.object'
                                     ' and @player.occupiedship == event.object')
                contact_event = tree.find(".//cue[@name='ObjectAttackedObject']/conditions/event_object_attacked_object")
                self.assertEqual(contact_event.attrib, {'group': 'State.$sources', 'method': 'killmethod.hitbybullet'})
                contact = tree.find(".//cue[@name='PlayerShipHit']/conditions/check_value")
                self.assertEqual(contact.get('value'), 'State.$armed and State.$watch_player_ship'
                                 ' and State.$targets.indexof.{@event.param} gt 0'
                                 ' and @player.controlled == event.param and @player.occupiedship == event.param')
        for change in ({'combat_capture': True}, {'combat_capture': 1, 'profile_id': 45850,
                                                   'save_path': r'D:\Test\45850\save', 'seat_index': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                install_fixture_overlay(self.view, **(self.options | change))
            self.assertFalse((self.view / 'extensions/x4_lan_lab').exists())

    def test_seat_gate_config_and_module_contract(self):
        text = ("local full_registry_inspect=nil\n"
                "  assert(full_registry_inspect:guard(),'full registry guard lost before admission')\n")
        registry = {'epoch': EPOCH, 'issued_count': 2, 'operational_bits': '10'}
        self.assertNotIn('X4LAN_COMBAT_CAPTURE', extend_seat_gate(text, 1, registry))
        digest = 'a' * 64
        self.assertEqual(capture_config(registry, 1, CHECKPOINT_ID, digest),
                         f',authority_combat_capture={{epoch="{EPOCH}",index=1,checkpoint="{CHECKPOINT_ID}",sha256="{digest}"}}')
        for index in (0, 2, 3, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                capture_config(registry, index, CHECKPOINT_ID, digest)
        validate_md_read_only(OBSERVER_MD.read_text(encoding='utf-8'))
        source = CAPTURE_LUA.read_text(encoding='utf-8')
        self.assertEqual(validate_capture_lua(source)['controls'], ['Arm', 'Disarm', 'Watch'])
        for unsafe in (source + 'SetObjectHull(ship, 1)\n',
                       source.replace("send('Watch'", "send('Snapshot'"),
                       source.replace("AddUITriggeredEvent('X4AuthorityClientCombatCapture'", "AddUITriggeredEvent('Other'"),
                       source + "AddUITriggeredEvent('X4AuthorityClientCombatCapture', 'Arm', {})\n"):
            with self.subTest(unsafe=unsafe[-60:]), self.assertRaises(ValueError):
                validate_capture_lua(unsafe)


class CombatCaptureLifecycleTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT is not installed')
    def test_participant_controls_private_capture_on_the_live_helm(self):
        script = ROOT / 'tests/authority_windows_combat_capture_lifecycle.lua'
        for scenario in ('pause', 'refusal', 'wrong-actor', 'wrong-epoch', 'fault',
                         'player-loss', 'helm-loss', 'disabled'):
            with self.subTest(scenario=scenario):
                subprocess.run(['luajit', script, scenario], cwd=ROOT, check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
