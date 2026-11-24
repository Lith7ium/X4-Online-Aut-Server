"""Offline source and lifetime checks for opt-in format-3 server recovery."""
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('registry_snapshot_server',
    ROOT / 'tools/dev/authority_recovered_loopback_server.py')
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
import authority_recovered_registry_snapshot as snapshots


class ConfirmedSnapshotSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        origin = Path(temporary.name) / 'prior'
        self.md = origin / 'case/extensions/x4_lan_lab/md'
        self.md.mkdir(parents=True)
        for name, path in (
            ('authority_checkpoint_marker.xml', ROOT / 'src/authority/md/checkpoint_marker.xml'),
            ('authority_identity_fixture.xml', ROOT / 'tools/dev/authority_identity_fixture.xml'),
            ('authority_ship_registry.xml', ROOT / 'tools/dev/authority_ship_registry.xml'),
            ('authority_registry_availability_snapshot.xml',
             ROOT / 'tools/dev/authority_registry_availability_snapshot.xml'),
        ):
            shutil.copyfile(path, self.md / name)
        content = origin / 'case/home/.config/EgoSoft/X4/45841/content.xml'
        content.parent.mkdir(parents=True)
        content.write_bytes(b'<content/>')
        self.profile = Path(temporary.name) / 'profile'
        self.profile.mkdir()
        (self.profile / 'content.xml').write_bytes(content.read_bytes())
        checkpoint_dir = origin / 'checkpoints'
        checkpoint_dir.mkdir()
        for name in ('CURRENT', 'CONFIRMED'):
            (checkpoint_dir / name).write_text('same pointer\n')
        checkpoint_id = 'checkpoint-' + '1' * 32
        generation = checkpoint_dir / checkpoint_id
        generation.mkdir()
        self.checkpoint = SimpleNamespace(checkpoint_id=checkpoint_id, sha256='f' * 64,
                                          marker='b' * 32, path=generation / 'save.xml.gz')
        self.store = SimpleNamespace(directory=checkpoint_dir,
                                     latest_marked_confirmed=lambda: self.checkpoint)
        self.evidence = {'format': 3, 'source_registry_checked': True, 'epoch': 'a' * 32,
                         'run_id': self.checkpoint.marker, 'issued_count': 3,
                         'availability_bits': '110', 'operational_bits': '100',
                         'availability_sha256': '1' * 64, 'operational_sha256': '2' * 64,
                         'mapping_sha256': '3' * 64}
        self.result = {'supervisor_version': 1, 'success': True, 'final_world_state': 'paused',
                       'owned_processes_remaining': [], 'final_checkpoint_id': checkpoint_id,
                       'final_checkpoint_sha256': 'f' * 64,
                       'registry_snapshot': {'required': True, 'initial_inspect_verified': True,
                           'final_receipt': snapshots.receipt(self.checkpoint, self.evidence)}}
        self.result_path = origin / 'result.json'
        self.write_result()

    def write_result(self):
        self.result_path.write_text(json.dumps(self.result))

    def verify(self):
        with patch('authority.registry_availability_evidence.verify_join_snapshot',
                   return_value=self.evidence):
            return server.verify_previous_source(self.store, self.checkpoint, self.profile,
                                                 require_registry_snapshot=True)

    def test_exact_confirmed_format3_source_is_accepted(self):
        self.assertEqual(self.verify(),
                         ((ROOT / 'tools/dev/authority_identity_fixture.xml').read_bytes(), self.evidence))

    def test_standalone_or_changed_source_is_refused(self):
        cases = (
            lambda: (self.md / 'authority_registry_availability_snapshot.xml').unlink(),
            lambda: (self.md / 'authority_registry_availability_snapshot.xml').write_text('changed'),
            lambda: (self.md / 'unexpected.xml').write_text('<mdscript/>'),
            lambda: (self.store.directory / 'CURRENT').write_text('other pointer\n'),
            lambda: setattr(self, 'checkpoint', SimpleNamespace(checkpoint_id='other',
                                                                  sha256='f' * 64, marker='b' * 32)),
            lambda: self.result.update(success=False),
            lambda: self.result.pop('registry_snapshot'),
            lambda: self.result['registry_snapshot']['final_receipt'].update(mapping_sha256='0' * 64),
            lambda: self.result_path.unlink(),
        )
        for change in cases:
            with self.subTest(change=change.__code__.co_firstlineno):
                self.setUp()
                change()
                self.write_result() if self.result_path.exists() else None
                with self.assertRaises((ValueError, OSError)):
                    self.verify()

    def test_first_capture_source_requires_legacy_scripts_and_clean_supervisor(self):
        (self.md / 'authority_registry_availability_snapshot.xml').unlink()
        ready = ('ready', self.evidence['epoch'], self.evidence['issued_count'], '6' * 64)
        self.result['registry_status'] = {
            'state': 'ready', 'epoch': ready[1], 'count': ready[2],
            'mapping_sha256': ready[3], 'checkpoint_id': self.checkpoint.checkpoint_id}
        self.write_result()
        fixture, prior = server.verify_previous_source(
            self.store, self.checkpoint, self.profile, registry_status=True,
            return_supervisor_result=True, require_ready_registry_script=True)
        self.assertTrue(fixture)
        self.assertEqual(snapshots.verify_bootstrap_status(prior, self.checkpoint, ready), ready)
        for change in ('missing_registry', 'extra_script', 'stale_receipt', 'unconfirmed'):
            with self.subTest(change=change):
                if change == 'missing_registry':
                    registry = self.md / 'authority_ship_registry.xml'
                    registry.unlink()
                    with self.assertRaises(ValueError):
                        server.verify_previous_source(self.store, self.checkpoint, self.profile,
                            registry_status=True, return_supervisor_result=True,
                            require_ready_registry_script=True)
                    shutil.copyfile(ROOT / 'tools/dev/authority_ship_registry.xml', registry)
                elif change == 'extra_script':
                    (self.md / 'unrelated.xml').write_text('<mdscript/>')
                    with self.assertRaises(ValueError):
                        server.verify_previous_source(self.store, self.checkpoint, self.profile,
                            registry_status=True, return_supervisor_result=True,
                            require_ready_registry_script=True)
                    (self.md / 'unrelated.xml').unlink()
                elif change == 'stale_receipt':
                    with self.assertRaises(ValueError):
                        snapshots.verify_bootstrap_status(prior, self.checkpoint,
                                                          ('ready', ready[1], ready[2], '0' * 64))
                else:
                    self.result['success'] = False
                    self.write_result()
                    with self.assertRaises(ValueError):
                        server.verify_previous_source(self.store, self.checkpoint, self.profile,
                            registry_status=True, return_supervisor_result=True,
                            require_ready_registry_script=True)


class SnapshotModeTests(unittest.TestCase):
    def test_one_shot_uses_existing_save_control_and_defers_confirmation(self):
        calls = Mock()
        adapter = SimpleNamespace(pause=calls.pause, save=calls.save,
                                  pending_marker='b' * 32,
                                  checkpoint_published=calls.checkpoint_published)
        saved = SimpleNamespace(checkpoint_id='saved')
        store = SimpleNamespace(publish=calls.publish, confirm=calls.confirm)
        calls.save.return_value = 'private-save-source'
        calls.publish.return_value = saved
        self.assertIs(server.capture_first_registry_snapshot(adapter, store, calls.verify), saved)
        self.assertEqual(calls.mock_calls, [call.pause(), call.save(),
            call.publish('private-save-source', marker='b' * 32),
            call.checkpoint_published(saved), call.verify()])

    def test_opt_in_rejects_other_diagnostic_owners(self):
        self.assertFalse(snapshots.validate_mode(SimpleNamespace()))
        self.assertTrue(snapshots.validate_mode(SimpleNamespace(
            require_registry_snapshot=True, diagnostic_guard_cache_ms=0)))
        for name, value in (('registry_status', True), ('registry_enroll', True),
                            ('diagnostic_client', True), ('fixture_overlap', True),
                            ('diagnostic_guard_cache_ms', 250)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                snapshots.validate_mode(SimpleNamespace(require_registry_snapshot=True,
                                                        **{name: value}))
        self.assertFalse(snapshots.validate_mode(SimpleNamespace(
            bootstrap_registry_snapshot=True)))
        for name, value in (('require_registry_snapshot', True), ('registry_status', True),
                            ('diagnostic_client', True)):
            with self.subTest(bootstrap_conflict=name), self.assertRaises(ValueError):
                snapshots.validate_mode(SimpleNamespace(bootstrap_registry_snapshot=True,
                                                      **{name: value}))

    def test_epoch_count_and_missing_lifetime_are_stable(self):
        baseline = {'epoch': 'a' * 32, 'issued_count': 4,
                    'run_id': 'b' * 32, 'availability_bits': '1101',
                    'mapping_sha256': '1' * 64}
        next_save = {**baseline, 'run_id': 'c' * 32,
                     'availability_bits': '1001', 'mapping_sha256': '2' * 64}
        snapshots.verify_transition(baseline, next_save)
        for change in ({'epoch': 'd' * 32}, {'issued_count': 5},
                       {'run_id': baseline['run_id']}, {'availability_bits': '1111'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                snapshots.verify_transition(baseline, {**next_save, **change})

    def test_the_shuttle_pair_only_appends_two_operational_bindings(self):
        baseline = {'epoch': 'a' * 32, 'issued_count': 3, 'run_id': 'b' * 32,
                    'availability_bits': '101'}
        saved = {**baseline, 'issued_count': 5, 'run_id': 'c' * 32,
                 'availability_bits': '10111', 'operational_bits': '00111'}
        snapshots.verify_transition(baseline, saved, appended_indices=(4, 5))
        for indices, change in (
                ((), {}), ((5, 4), {}), ((4, 5), {'issued_count': 6}),
                ((4, 5), {'availability_bits': '11111'}),
                ((4, 5), {'availability_bits': '10110'}),
                ((4, 5), {'operational_bits': '00110'})):
            with self.subTest(indices=indices, change=change), self.assertRaises(ValueError):
                snapshots.verify_transition(baseline, {**saved, **change}, appended_indices=indices)

    def test_state_keeps_only_latest_verified_receipt(self):
        source = SimpleNamespace(checkpoint_id='source', sha256='1' * 64, marker='a' * 32)
        saved = SimpleNamespace(checkpoint_id='saved', sha256='2' * 64, marker='b' * 32)
        first = {'format': 3, 'source_registry_checked': True, 'run_id': source.marker,
                 'epoch': 'c' * 32, 'issued_count': 3, 'availability_bits': '110',
                 'availability_sha256': '3' * 64, 'operational_sha256': '4' * 64,
                 'mapping_sha256': '5' * 64}
        second = {**first, 'run_id': saved.marker, 'availability_bits': '100',
                  'mapping_sha256': '6' * 64}
        state = snapshots.RegistrySnapshotState(source, first)
        state.nonce = 'd' * 32
        self.assertIsNone(state.metadata()['final_receipt'])
        with patch.object(snapshots.bridge_gate, 'verify_bridge_observation') as bridge:
            state.verify_initial(Path('engine-output.log'))
            with patch.object(snapshots, 'verify_join_snapshot', return_value=second):
                self.assertEqual(state.verify_save(saved, Path('engine-output.log')),
                                 snapshots.receipt(saved, second))
        self.assertEqual(bridge.call_count, 2)
        self.assertEqual(bridge.call_args.kwargs['previous_run_id'], source.marker)
        self.assertEqual(state.metadata()['verified_save_count'], 1)
        self.assertEqual(state.metadata()['final_receipt']['mapping_sha256'], '6' * 64)
        self.assertEqual(state.metadata()['source_receipt']['mapping_sha256'], '5' * 64)

    def test_first_capture_has_distinct_receipt_and_no_inspect_claim(self):
        source = SimpleNamespace(checkpoint_id='source', sha256='1' * 64, marker='a' * 32)
        saved = SimpleNamespace(checkpoint_id='first', sha256='2' * 64, marker='b' * 32)
        status = ('ready', 'c' * 32, 3, 'd' * 64)
        prior = {'registry_status': {'state': 'ready', 'epoch': status[1],
                                   'count': status[2], 'mapping_sha256': status[3],
                                   'checkpoint_id': source.checkpoint_id}}
        self.assertEqual(snapshots.verify_bootstrap_status(prior, source, status), status)
        for changed in ({'state': 'uninitialized'}, {'count': 4},
                        {'mapping_sha256': '0' * 64}, {'checkpoint_id': 'old'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                snapshots.verify_bootstrap_status(
                    {'registry_status': {**prior['registry_status'], **changed}}, source, status)
        first = snapshots.FirstCaptureState(source, status)
        with tempfile.TemporaryDirectory() as temp, patch.object(
                snapshots.bridge_gate, 'install_first_capture', return_value=[Path('installed')]) as install:
            case = Path(temp)
            registry = case / 'extensions/x4_lan_lab/md/authority_ship_registry.xml'
            registry.parent.mkdir(parents=True)
            self.assertEqual(first.install(case, reserved_tokens=('e' * 32,), read_source=server.read_evidence),
                             [registry, Path('installed')])
            self.assertEqual(registry.read_bytes(), (ROOT / 'tools/dev/authority_ship_registry.xml').read_bytes())
        self.assertEqual(install.call_args.kwargs['source_marker'], source.marker)
        self.assertEqual(install.call_args.kwargs['epoch'], status[1])
        self.assertEqual(install.call_args.kwargs['issued_count'], 3)
        saved_evidence = {'format': 3, 'source_registry_checked': True, 'run_id': saved.marker,
                          'epoch': status[1], 'issued_count': 3,
                          'availability_sha256': '3' * 64, 'operational_sha256': '4' * 64,
                          'mapping_sha256': '5' * 64}
        with patch.object(snapshots, 'verify_join_snapshot', return_value=saved_evidence), \
             patch.object(snapshots.bridge_gate, 'verify_bridge_observation') as observe:
            first.verify_save(saved, Path('engine-output.log'))
            first.recheck(saved, Path('engine-output.log'))
        self.assertEqual(observe.call_args.kwargs['first_capture_source_marker'], source.marker)
        self.assertTrue(first.metadata()['first_capture_verified'])
        self.assertFalse(first.metadata()['initial_inspect_verified'])
        self.assertEqual(first.metadata()['final_receipt'], snapshots.receipt(saved, saved_evidence))
        self.assertEqual(snapshots.verify_previous_receipt(
            {'registry_snapshot': first.metadata()}, saved, saved_evidence),
            snapshots.receipt(saved, saved_evidence))
        with self.assertRaises(ValueError):
            first.verify_save(saved, Path('engine-output.log'))


if __name__ == '__main__':
    unittest.main()
