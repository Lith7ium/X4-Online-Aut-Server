"""Player CLI, isolated command and owned seat-log binding. No X4 launches."""
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from authority.client_seat_overlay import COMPANY_PLAYBACK_UI
from authority.company_motion_source import CompanyMotionSource
from authority.company_purchase_source import CompanyPurchaseSource
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer
import authority_client_native as native
import authority_linux_player_client as cli
from authority_client_options import read_connection_settings
import authority_linux_company_seat as company_seat
from authority_linux_company_seat import SeatWatch
from authority_linux_player_namespace import command_with_private_lock

MARKER = '0' * 32
NONCE = 'e' * 32
STATUS = ('ready', 'd' * 32, 2000, 'f' * 64)
INDEX = 1970
OFFER = Offer(world_epoch='a' * 32, connection_handle=1, baseline_id=2, load_generation=3,
              attempt_id='b' * 32, checkpoint_id='checkpoint-' + 'c' * 32,
              sha256='a' * 64, size=128, name='j1.xml.gz')


def receipt_lines(offer=OFFER):
    ticket = native.ticket_for(MarkerBoundOffer(offer, MARKER))
    load = native.download.ReceiptWatch(None, ticket)
    return [
        native.download.TAG + load.expected[0],
        b'AUTH_MARKER load INSPECTED ' + MARKER.encode(),
        b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT ' + MARKER.encode() + b' 2000',
        f'[X4LAN] AUTH_RECOVERY_REGISTRY {NONCE}|startup|{STATUS[1]}|2000'.encode(),
        f'[X4LAN] AUTH_CLIENT_SEAT {NONCE}|{STATUS[1]}|{INDEX}|controlled'.encode(),
        native.download.TAG + load.expected[1],
    ]


class SeatLogTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_participant_only_activates_sources_with_confirmed_resume_and_helm(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_participant_seat_lifecycle.lua'],
                       cwd=root, check=True, timeout=5)

    def test_installs_real_seat_overlay_in_disposable_case(self):
        bound = MarkerBoundOffer(OFFER, MARKER)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game, home = root / 'game', root / 'home'
            (game / 'extensions').mkdir(parents=True)
            home.mkdir()
            case, profile, _, _ = native.snapshot.prepare_case(root / 'run', game, home)
            native.download.install_probe(case, profile, native.ticket_for(bound))
            participant = native.NativeParticipant(Mock(), 100, Mock())
            self.addCleanup(participant.close)
            participant.server_library = root / 'x4lan_net.so'
            participant.server_library.write_bytes(b'synthetic library')
            participant.install(case, include_identity=False)
            native.marked.install_marked_load_probe(case)
            snapshot = {'format': 3, 'source_registry_checked': True,
                        'evidence_scope': 'saved checkpoint bytes only', 'run_id': MARKER,
                        'epoch': STATUS[1], 'issued_count': STATUS[2], 'mapping_sha256': STATUS[3],
                        'availability_bits': '1' * STATUS[2], 'operational_bits': '1' * STATUS[2],
                        'available_count': STATUS[2], 'unavailable_count': 0, 'operational_count': STATUS[2]}
            target = profile / 'save/save_009.xml.gz'
            keepalive = Mock()
            source = CompanyPurchaseSource(expected_epoch=STATUS[1])
            self.addCleanup(source.close)
            with patch.object(company_seat, 'verify_join_snapshot', return_value=snapshot) as read, \
                    patch.object(company_seat.secrets, 'token_hex', return_value=NONCE):
                paths, watch = company_seat.install(case, profile, bound, target, INDEX, keepalive,
                                                    company_purchase=source, combat_capture=True)
            self.assertEqual(read.call_args.args[0].path, target)
            self.assertIs(read.call_args.kwargs['pump'], keepalive)
            lab = case / 'extensions/x4_lan_lab'
            manifest = (lab / 'ui.xml').read_text()
            self.assertLess(manifest.index('ui/client_seat.lua'), manifest.index('ui/authority_download_load_probe.lua'))
            self.assertIn(lab / 'md/client_seat.xml', paths)
            self.assertIn(lab / 'md/client_combat_capture.xml', paths)
            self.assertIn(lab / 'ui/client_combat_capture.lua', paths)
            self.assertLess(manifest.index('ui/client_combat_capture.lua'),
                            manifest.index('ui/authority_two_x4_participant_probe.lua'))
            self.assertEqual(watch.registry_epoch, STATUS[1])
            config = (lab / 'ui/config.lua').read_text()
            self.assertIn(f'authority_combat_capture={{epoch="{STATUS[1]}",index={INDEX},'
                          f'checkpoint="{OFFER.checkpoint_id}",sha256="{OFFER.sha256}"}}', config)
            self.assertIn(lab / 'ui/company_trade_capture.lua', paths)
            self.assertIn(lab / 'ui/company_purchase_source.lua', paths)
            self.assertLess(manifest.index('ui/company_purchase_source.lua'),
                            manifest.index('ui/authority_two_x4_participant_probe.lua'))
            self.assertIn('authority_company_purchase={', (lab / 'ui/config.lua').read_text())
            self.assertIn('cfg.authority_registry_snapshot,1970', (lab / 'ui/authority_download_load_probe.lua').read_text())
            self.assertIn('authority_registry_snapshot={', (lab / 'ui/config.lua').read_text())
            watch.path.write_bytes(b'\n'.join(receipt_lines()) + b'\n')
            watch.verify()
            participant.redact()
            self.assertNotIn('authority_company_purchase={', (lab / 'ui/config.lua').read_text())

    def test_company_playback_modules_only_in_opted_in_private_overlay(self):
        bound = MarkerBoundOffer(OFFER, MARKER)
        snapshot = {'format': 3, 'source_registry_checked': True,
                    'evidence_scope': 'saved checkpoint bytes only', 'run_id': MARKER,
                    'epoch': STATUS[1], 'issued_count': STATUS[2], 'mapping_sha256': STATUS[3],
                    'availability_bits': '1' * STATUS[2], 'operational_bits': '1' * STATUS[2],
                    'available_count': STATUS[2], 'unavailable_count': 0, 'operational_count': STATUS[2]}
        for target, radar in ((None, False), (INDEX + 1, False), (INDEX + 1, True)):
            with self.subTest(target=target, radar=radar), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'game/extensions').mkdir(parents=True)
                (root / 'home').mkdir()
                case, profile, _, _ = native.snapshot.prepare_case(root / 'run', root / 'game', root / 'home')
                native.download.install_probe(case, profile, native.ticket_for(bound))
                native.marked.install_marked_load_probe(case)
                source = CompanyMotionSource(playback_index=target, cockpit_radar_playback=radar)
                self.addCleanup(source.close)
                with patch.object(company_seat, 'verify_join_snapshot', return_value=snapshot):
                    paths, _ = company_seat.install(case, profile, bound, profile / 'save/save_009.xml.gz',
                                                    INDEX, Mock(), company_motion=source)
                lab = case / 'extensions/x4_lan_lab'
                config = (lab / 'ui/config.lua').read_text()
                manifest = (lab / 'ui.xml').read_text()
                self.assertEqual('playback_index=' in config, target is not None)
                self.assertIn('ui/company_observer_stream.lua', manifest)
                self.assertEqual('cockpit_radar_playback=true' in config, radar)
                self.assertEqual(lab / 'md/company_cockpit_observer.xml' in paths, radar)
                for name in ('company_cockpit_observer.lua', 'company_cockpit_playback.lua'):
                    asset = lab / 'ui' / name
                    self.assertEqual(asset in paths, radar)
                    self.assertEqual(asset.exists(), radar)
                    if radar:
                        self.assertLess(manifest.index('ui/' + name), manifest.index('ui/company_motion_source.lua'))
                for name in COMPANY_PLAYBACK_UI:
                    asset = lab / 'ui' / name
                    self.assertEqual(asset.exists(), target is not None)
                    self.assertEqual(asset in paths, target is not None)
                    if target is not None:
                        self.assertLess(manifest.index('ui/' + name), manifest.index('ui/company_motion_source.lua'))
                if target is not None:
                    self.assertIn(f'playback_index={target}', config)
                    self.assertIn('X4LAN_MOTION_BUFFER=M', (lab / 'ui/motion_buffer.lua').read_text())

    def test_company_combat_stages_local_health_receiver_only_on_opt_in(self):
        bound = MarkerBoundOffer(OFFER, MARKER)
        snapshot = {'format': 3, 'source_registry_checked': True,
                    'evidence_scope': 'saved checkpoint bytes only', 'run_id': MARKER,
                    'epoch': STATUS[1], 'issued_count': STATUS[2], 'mapping_sha256': STATUS[3],
                    'availability_bits': '1' * STATUS[2], 'operational_bits': '1' * STATUS[2],
                    'available_count': STATUS[2], 'unavailable_count': 0, 'operational_count': STATUS[2]}
        for combat, capture in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(combat=combat, capture=capture), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'game/extensions').mkdir(parents=True)
                (root / 'home').mkdir()
                case, profile, _, _ = native.snapshot.prepare_case(root / 'run', root / 'game', root / 'home')
                native.download.install_probe(case, profile, native.ticket_for(bound))
                participant = native.NativeParticipant(Mock(), 100, Mock())
                self.addCleanup(participant.close)
                participant.server_library = root / 'x4lan_net.so'
                participant.server_library.write_bytes(b'synthetic library')
                participant.install(case, include_identity=False)
                native.marked.install_marked_load_probe(case)
                source = CompanyMotionSource()
                self.addCleanup(source.close)
                with patch.object(company_seat, 'verify_join_snapshot', return_value=snapshot):
                    paths, _ = company_seat.install(case, profile, bound, profile / 'save/save_009.xml.gz',
                                                    INDEX, Mock(), company_motion=source, company_combat=combat,
                                                    combat_capture=capture)
                lab = case / 'extensions/x4_lan_lab'
                config = (lab / 'ui/config.lua').read_text()
                motion = config.split('authority_company_motion={', 1)[1].split('}', 1)[0]
                cue = lab / 'md/company_cockpit_health.xml'
                self.assertEqual('cockpit_health=true' in motion, combat)
                self.assertEqual(cue in paths, combat)
                self.assertEqual(cue.exists(), combat)
                self.assertEqual((lab / 'md/client_combat_capture.xml').exists(), capture)
                if combat:
                    nonce = re.search(r'\bnonce="([0-9a-f]{32})"', motion).group(1)
                    self.assertIn(f"@$args.$lease == '{nonce}'", cue.read_text())
                    manifest = (lab / 'ui.xml').read_text()
                    self.assertEqual(manifest.count('ui/combat_observer.lua'), 1)
                    self.assertLess(manifest.index('ui/combat_observer.lua'), manifest.index('ui/company_cockpit_health.lua'))
                    self.assertLess(manifest.index('ui/company_cockpit_health.lua'),
                                    manifest.index('ui/authority_two_x4_participant_probe.lua'))
                participant.redact()
                self.assertNotIn('authority_company_motion={', (lab / 'ui/config.lua').read_text())

    def test_playback_target_must_be_operational_and_distinct_in_verified_register(self):
        for target, bits in ((INDEX, '1' * STATUS[2]), (INDEX + 1, '1' * INDEX),
                             (INDEX + 1, '1' * INDEX + '0')):
            with self.subTest(target=target, count=len(bits)):
                source = CompanyMotionSource(playback_index=target)
                self.addCleanup(source.close)
                with self.assertRaises(ValueError):
                    source.configure(STATUS[1], INDEX, operational_bits=bits)
                self.assertIsNone(source.registry)

    def test_no_admission_receipt_until_exact_same_offer_seat_and_pause(self):
        lines = receipt_lines()
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'engine-output.log'
            log = SeatWatch(path, MarkerBoundOffer(OFFER, MARKER), STATUS, NONCE, INDEX)
            self.assertFalse(log.poll())
            for line in lines[:-1]:
                with path.open('ab') as output:
                    output.write(line + b'\n')
                self.assertFalse(log.poll())
            with path.open('ab') as output:
                output.write(lines[-1])
            self.assertFalse(log.poll())
            with path.open('ab') as output:
                output.write(b'\n')
            self.assertTrue(log.poll())
            self.assertEqual(log.stage, 2)
            with path.open('ab') as output:
                output.write(lines[-2] + b'\n')
            with self.assertRaises(ValueError):
                log.verify()

    def test_refusal_and_cross_attempt_fail_before_a_claim(self):
        for replacement in (receipt_lines(replace(OFFER, attempt_id='2' * 32)),
                [*receipt_lines()[:4], receipt_lines()[4].replace(b'1970', b'1971')],
                [*receipt_lines()[:4], receipt_lines()[4].replace(b'AUTH_CLIENT_SEAT ', b'AUTH_CLIENT_SEAT_REFUSED ')
                 .replace(b'controlled', b'PILOT_PRESENT')]):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as out:
                path = Path(out) / 'engine-output.log'
                path.write_bytes(b'\n'.join(replacement) + b'\n')
                gate = SeatWatch(path, MarkerBoundOffer(OFFER, MARKER), STATUS, NONCE, INDEX)
                owner, participant = Mock(), Mock()
                with self.assertRaises(ValueError):
                    native.observe_and_activate(owner, participant, gate, native.ticket_for(
                        MarkerBoundOffer(OFFER, MARKER)), MARKER, Mock(), Mock(), {})
                owner.activate.assert_not_called()
                participant.enroll.assert_not_called()

    def test_replaced_engine_log_with_same_contents_is_rejected(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'engine-output.log'
            bound = MarkerBoundOffer(OFFER, MARKER)
            path.write_bytes(b'\n'.join(receipt_lines()) + b'\n')
            gate = SeatWatch(path, bound, STATUS, NONCE, INDEX)
            gate.verify()
            other = path.with_suffix('.new')
            other.write_bytes(path.read_bytes())
            other.replace(path)
            with self.assertRaisesRegex(ValueError, 'log changed'):
                gate.verify()


class PlayerClientTests(unittest.TestCase):
    def test_local_namespace_keeps_only_client_lock_and_explicit_x_socket(self):
        lock = '/tmp/EGOSOFT_X4.lock'
        command = ['bwrap', '--ro-bind', '/', '/', '--bind', '/tmp/private-case', '/tmp/private-case',
                   '--bind', '/tmp/private-case/x4.lock', lock, '--die-with-parent', '--', 'X4']
        with patch.object(Path, 'is_socket', return_value=True):
            local = command_with_private_lock(command, display=':12.0', authorization=Path('/tmp/test-auth'))
        self.assertEqual(local[:7], ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--ro-bind'])
        self.assertEqual(local[7:12], ['/tmp/.X11-unix/X12', '/tmp/.X11-unix/X12',
                                      '--ro-bind', '/tmp/test-auth', '/tmp/test-auth'])
        position = local.index(lock)
        self.assertEqual(local[position - 2:position + 1], ['--bind', '/tmp/private-case/x4.lock', lock])
        self.assertEqual(command[4:7], ['--bind', '/tmp/private-case', '/tmp/private-case'])
        with patch.object(Path, 'is_socket', return_value=False), self.assertRaisesRegex(ValueError, 'socket'):
            command_with_private_lock(command, display=':12', authorization=Path('/tmp/test-auth'))

    def test_cli_uses_private_pinned_settings_and_selected_index(self):
        with tempfile.TemporaryDirectory() as temp:
            connection = Path(temp) / 'connection.json'
            connection.write_text(json.dumps(dict(server_host='example.invalid', server_port=4433,
                server_fingerprint='a' * 64, shared_key='b' * 64, file_token='c' * 32, file_slot=1)))
            connection.chmod(0o600)
            args = ['--connection-file', str(connection), '--display', ':1', '--out', '.local/run',
                    '--game', '/licensed/X4 Foundations', '--company-ship-index', str(INDEX)]
            with patch.object(native, 'run', return_value={'success': True}) as run, \
                    redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
            self.assertIsNone(run.call_args.args[0].timeout)
            options = run.call_args.kwargs
            self.assertEqual(options['player_settings']['shared_key'], bytes.fromhex('b' * 64))
            self.assertEqual(options['player_settings']['server_fingerprint'], 'a' * 64)
            self.assertEqual(options['select_ship']([], None), INDEX)
            self.assertNotIn('b' * 64, stdout.getvalue())
            features = ('company_motion', 'company_combat', 'combat_capture', 'company_purchases',
                        'company_fleet_orders', 'company_trade_view', 'company_sales', 'company_undock_test')
            self.assertTrue(set(features).isdisjoint(options))
            self.assertNotIn('cockpit_continuous_playback', options)
            self.assertNotIn('private_map_marker', options)
            marker = '--company-map-marker'
            with patch.object(native, 'run', return_value={'success': True}) as marker_run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', marker]), 0)
            self.assertIs(marker_run.call_args.kwargs['private_map_marker'], True)
            self.assertNotIn('cockpit_radar_playback', marker_run.call_args.kwargs)
            for extra in ([marker], ['--company-motion', marker, '--observe-registered-npc', '42', 'NPC-042']):
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as error:
                    cli.main([*args, *extra])
                self.assertIn('--company-map-marker requires --company-motion', error.getvalue())
            recovery = '--company-cockpit-continuous-playback'
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', '--company-cockpit-playback', recovery]), 0)
            self.assertIs(run.call_args.kwargs['cockpit_continuous_playback'], True)
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, '--company-motion', '--company-cockpit-playback-index', str(INDEX + 1), recovery])
            flags = ['--' + name.replace('_', '-') for name in features]
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, *flags, '--company-cockpit-playback-index', str(INDEX + 1)]), 0)
            forwarded = run.call_args.kwargs
            self.assertEqual({name: forwarded[name] for name in features}, dict.fromkeys(features, True))
            self.assertIs(forwarded['cockpit_radar_playback'], True)
            self.assertEqual(forwarded['company_hull_playback_index'], INDEX + 1)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--combat-capture']), 0)
            self.assertIs(run.call_args.kwargs['combat_capture'], True)
            self.assertNotIn('company_purchases', options)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-purchases']), 0)
            self.assertTrue(run.call_args.kwargs['company_purchases'])
            self.assertNotIn('company_fleet_orders', run.call_args.kwargs)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-purchases', '--company-fleet-orders']), 0)
            self.assertIs(run.call_args.kwargs['company_fleet_orders'], True)
            self.assertNotIn('company_trade_view', run.call_args.kwargs)
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, '--company-fleet-orders'])
            self.assertNotIn('company_trade_view', run.call_args.kwargs)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-purchases', '--company-trade-view']), 0)
            self.assertIs(run.call_args.kwargs['company_trade_view'], True)
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, '--company-trade-view'])
            self.assertNotIn('company_sales', run.call_args.kwargs)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-purchases', '--company-trade-view', '--company-sales']), 0)
            self.assertIs(run.call_args.kwargs['company_sales'], True)
            for prerequisite in ([], ['--company-purchases'], ['--company-trade-view']):
                with self.subTest(prerequisite=prerequisite), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    cli.main([*args, '--company-sales', *prerequisite])
            self.assertNotIn('company_undock_test', run.call_args.kwargs)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-purchases', '--company-undock-test']), 0)
            self.assertTrue(run.call_args.kwargs['company_undock_test'])
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, '--company-undock-test'])
            with patch.object(native, 'run', return_value={'success': True}) as run, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', '--timeout', '45']), 0)
            self.assertEqual(run.call_args.args[0].timeout, 45)
            self.assertTrue(run.call_args.kwargs['company_motion'])
            self.assertNotIn('company_hull_playback_index', run.call_args.kwargs)
            self.assertNotIn('company_combat', run.call_args.kwargs)
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', '--company-combat', '--combat-capture']), 0)
            self.assertTrue(run.call_args.kwargs['company_combat'])
            self.assertTrue(run.call_args.kwargs['combat_capture'])
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, '--company-combat'])
            option = ['--company-hull-playback-index', str(INDEX + 1)]
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', *option]), 0)
            self.assertEqual(run.call_args.kwargs['company_hull_playback_index'], INDEX + 1)

            self.assertNotIn('cockpit_radar_playback', run.call_args.kwargs)
            cockpit = ['--company-cockpit-playback-index', str(INDEX + 1)]
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', *cockpit]), 0)
            self.assertEqual(run.call_args.kwargs['company_hull_playback_index'], INDEX + 1)
            self.assertIs(run.call_args.kwargs['cockpit_radar_playback'], True)
            npc = ['--observe-registered-npc', '42', 'NPC-042']
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', *npc]), 0)
            self.assertEqual(run.call_args.kwargs['observed_npc'], (42, 'NPC-042'))
            hold = ['--company-motion', '--observe-registered-npc', '943', 'MFQ-419',
                    '--company-ship-index', '1073', '--trial-registered-npc-hold']
            with patch.object(native, 'run') as run, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main([*args, *hold])
                run.assert_not_called()
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--base-game-only', *hold]), 0)
                self.assertTrue(run.call_args.kwargs['registered_npc_hold_trial'])
                measure = [*hold[:-1], '--trial-registered-npc-measurement']
                self.assertEqual(cli.main([*args, '--base-game-only', *measure]), 0)
                self.assertTrue(run.call_args.kwargs['registered_npc_local_measurement'])
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                cli.main([*args, *measure])
            multi = ['--company-cockpit-playback']
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', *multi]), 0)
            self.assertNotIn('company_hull_playback_index', run.call_args.kwargs)
            self.assertIs(run.call_args.kwargs['cockpit_radar_playback'], True)
            with patch.object(native, 'run') as run, redirect_stderr(io.StringIO()):
                for invalid in (
                    option, cockpit, multi, npc, ['--company-motion', *npc, *multi],
                    ['--company-motion', *option, *cockpit],
                    ['--company-motion', *option, *multi],
                    ['--company-motion', *cockpit, *multi],
                    ['--company-motion', '--company-hull-playback-index', str(INDEX)],
                    ['--company-motion', '--company-cockpit-playback-index', str(INDEX)],
                ):
                    with self.assertRaises(SystemExit):
                        cli.main([*args, *invalid])
            run.assert_not_called()

            connection.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'owner-only'):
                read_connection_settings(connection, require_private=True)
            with patch.object(native, 'run') as run, redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(cli.main(args), 1)
            run.assert_not_called()
            self.assertNotIn('b' * 64, stdout.getvalue())

    def test_cli_accepts_graphics_config_in_interactive_mode(self):
        arguments = ['--connection-file', 'private.json', '--display', ':1', '--out', '.local/run',
                     '--game', '/licensed/X4 Foundations', '--graphics-config', 'my-config.xml']
        with (patch.object(cli, 'read_connection_settings', return_value={}),
              patch.object(native, 'run', return_value={'success': True}) as run,
              redirect_stdout(io.StringIO())):
            self.assertEqual(cli.main(arguments), 0)
            self.assertEqual(run.call_args.args[0].graphics_config, Path('my-config.xml'))
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.main([*arguments, '--timeout', '15'])

    def test_cli_reports_session_full_from_native_client(self):
        self.assertNotEqual(native.error_code(RuntimeError('session_full')), 'session_full')
        error_code = native.error_code(cli.SessionFull('private detail'))
        self.assertEqual(error_code, 'session_full')
        result = {'success': False, 'error_code': error_code}
        arguments = ['--connection-file', 'private.json', '--display', ':1', '--out', '.local/run',
                     '--game', '/licensed/X4 Foundations', '--company-ship-index', str(INDEX)]
        with (patch.object(cli, 'read_connection_settings', return_value={}),
              patch.object(native, 'run', return_value=result),
              redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(cli.main(arguments), 1)
        self.assertEqual(stderr.getvalue(), 'Session Full\n')
        self.assertEqual(stdout.getvalue(), '')

    def test_admitted_player_uses_optional_total_budget_and_keeps_commands_bounded(self):
        for timeout in (None, 15):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                clock = 100
                deadline = clock + (3600 if timeout is None else timeout)
                root = Path(temp)
                out, game, home = (root / name for name in ('run', 'game', 'home'))
                (game / 'extensions').mkdir(parents=True)
                home.mkdir()
                config_home = root / 'preferences'
                stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}))
                profile_source = config_home / 'EgoSoft/X4/123456789/config.xml'
                profile_source.parent.mkdir(parents=True)
                saved_config = ('<root><fullscreen>true</fullscreen><res_width>2560</res_width>'
                                '<res_height>1440</res_height><autosave>true</autosave></root>')
                profile_source.write_text(saved_config, encoding='utf-8')
                private_config = out / 'case/home/.config/EgoSoft/X4/45841/config.xml'
                player = SimpleNamespace(pw_uid=os.getuid(), pw_name='test')
                args = SimpleNamespace(timeout=timeout, display=':1', seed=1, keepalive_interval=None,
                    archived_identity_fixture=None, expected_content_sha256=None)
                save = b'fixture'
                owner = Mock()
                owner.request_offer.return_value = MarkerBoundOffer(
                    replace(OFFER, size=len(save), sha256=hashlib.sha256(save).hexdigest()), MARKER)
                owner.copy_to_new_slot.side_effect = lambda _source, target: target.write_bytes(save)

                process = Mock()
                command_budgets = []

                def spawn(command, _log, processes):
                    bind = command.index(str(private_config))
                    self.assertEqual(command[bind - 1:bind + 2],
                                     ['--ro-bind', str(private_config), str(private_config)])
                    processes.append((process, 1))

                def activate(_attempt, participant, *, participant_timeout):
                    self.assertEqual(participant.deadline, deadline)
                    self.assertEqual(participant_timeout, 30 if timeout is None else 14)
                    owner.poll.side_effect = lambda interval: active_poll(participant, interval)

                def active_poll(participant, interval):
                    nonlocal clock
                    self.assertEqual(interval, .1)
                    participant.request('resume')
                    clock = deadline + 1
                    if owner.poll.call_count == 2:
                        process.poll.return_value = 0

                owner.activate.side_effect = activate
                process.poll.return_value = None
                watch = SimpleNamespace(stage=2, path=None, poll=Mock(return_value=True), verify=Mock(),
                    seat_at_admission='controlled', seat_status='controlled', allow_seat_handoff=Mock())
                for target, name, effect in (
                    (native, 'validate_environment', lambda *args, **kwargs:
                        (out, game, root / 'auth', None, player, home, 'steam')),
                    (native, 'compile_net', lambda _compiler, library: library.write_bytes(b'net')),
                    (native.load, 'readonly_save_command', lambda *args: ['bwrap', '--die-with-parent']),
                    (native, 'command_with_private_lock', lambda command, **kwargs: command),
                    (native.load, 'spawn_owned_gated', spawn),
                    (native, 'reserve_company_ship', lambda *args: INDEX),
                    (native.company_seat, 'install', lambda *args, **kwargs: ([], watch)),
                    (native, 'verify_receipts', lambda *args: None),
                    (native.download, 'game_identity', lambda *args: (os.getpid(), 1)),
                    (native.download, 'stop_owned_resources', lambda *args: True),
                    (native.live, 'steam_identity', lambda *args: 'steam'),
                    (native.time, 'monotonic', lambda: clock),
                ):
                    stack.enter_context(patch.object(target, name, side_effect=effect))
                connection = stack.enter_context(patch.object(native, 'client_connection', return_value=nullcontext(owner)))
                stack.enter_context(patch.object(native.NativeParticipant, 'enroll', autospec=True))
                stack.enter_context(patch.object(native.NativeParticipant, '_peer_guard', autospec=True))
                stack.enter_context(patch.object(native.Participant, 'request', autospec=True,
                    side_effect=lambda participant, operation: command_budgets.append((operation, participant.timeout))))

                result = native.run(args, player_settings={}, select_ship=Mock(), on_admitted=Mock())

                self.assertEqual(connection.call_args.kwargs['deadline'], deadline)
                self.assertTrue(result['success'], result)
                graphics = ET.parse(private_config).getroot()
                self.assertEqual(graphics.findtext('autosave'), 'false')
                self.assertEqual(profile_source.read_text(encoding='utf-8'), saved_config)
                if timeout is None:
                    self.assertEqual(graphics.findtext('fullscreen'), 'true')
                    self.assertEqual(graphics.findtext('res_width'), '2560')
                    self.assertEqual(graphics.findtext('res_height'), '1440')
                    self.assertEqual(result['session_end'], 'game_closed')
                    self.assertEqual(command_budgets, [('pause', 30), ('resume', 30), ('resume', 30)])
                else:
                    self.assertEqual(graphics.findtext('fullscreen'), 'false')
                    self.assertEqual(graphics.findtext('res_width'), '800')
                    self.assertEqual(graphics.findtext('res_height'), '600')
                    self.assertEqual(result['session_end'], 'session_limit')
                    self.assertEqual(command_budgets, [('pause', 14), ('resume', 14)])

    def test_player_polls_company_client_only_during_the_active_run(self):
        calls = []
        owner = Mock()
        owner.poll.side_effect = lambda _: calls.append('control')
        process = Mock()
        process.poll.side_effect = [None, 0]
        company_client = Mock()
        company_client.poll.side_effect = lambda: calls.append('motion')
        self.assertEqual(native.poll_player(owner, process, Mock(), None, lambda: False,
                                            company_client=company_client), 'game_closed')
        self.assertEqual(calls, ['control', 'motion'])

    def test_player_loop_closes_only_its_confirmed_process_and_propagates_unrelated_errors(self):
        owner = Mock()
        process = Mock()
        guard = Mock()
        process.poll.side_effect = [None, 0]
        self.assertEqual(native.poll_player(owner, process, guard, None, lambda: False), 'game_closed')
        owner.poll.assert_called_once_with(.1)
        process.reset_mock()
        self.assertEqual(native.poll_player(owner, process, guard, None, lambda: True), 'user_left')
        process.poll.assert_not_called()
        process.poll.side_effect = None
        process.poll.return_value = None
        guard.side_effect = RuntimeError('protected_configuration_changed')
        with self.assertRaisesRegex(RuntimeError, 'protected_configuration_changed'):
            native.poll_player(owner, process, guard, None, lambda: False)
        process.wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
