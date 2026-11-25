#!/usr/bin/env python3
"""Opt-in independent Linux client: private native load and cooperative control.

No local server/coordinator is constructed. This internal diagnostic cannot
prove exact file consumption, gameplay, Windows operation or Internet quality.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import sys
import threading
import time
import subprocess

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools/dev')]
from authority.company_ship_selection import reserve_company_ship
from authority.experimental_client_owner import CompanyShipChoiceDenied
from authority.experimental_remote_load_client import SessionFull
from authority.experimental_client_session import client_connection
from authority.client_seat_overlay import REFUSALS
from authority.company_motion_source import CompanyMotionSource, validate_npc_observation, validate_playback_index
from authority.company_purchase_source import CompanyPurchaseSource
import authority_client_stage as stage_cli
import authority_download_load_live as download
import authority_load_live as load
import authority_linux_company_seat as company_seat
from authority_linux_player_namespace import command_with_private_lock
from authority_sector_pair_transfer_live import compile_net
import authority_trusted_marked_live as marked
from authority_two_x4_resume_live import Participant
from authority_tcp_job_client_live import socket_owner
from authority_tcp_native_worker import check_process
import authority_native_client_playback as native_playback
import linux_gate_live as live
import linux_snapshot_capture as snapshot

MAX_NATIVE_BUDGET = 3600


class NativeParticipant(Participant):
    """Fence native calls and verify their connected UDP socket belongs to X4."""
    def __init__(self, owner, deadline, guard, *, keepalive_interval=None):
        super().__init__()
        self.owner, self.deadline, self.guard = owner, deadline, guard
        self.keepalive_interval = keepalive_interval
        self.loading = True
        self.process = None
        self.server_control = False
        self.playback = None
        self.seat_watch = None
        self.confirmations = {'pause': 0, 'resume': 0}
        self.wait_hook = self._wait
        self.sock.settimeout(.1)

    def poll_load_keepalive(self):
        if self.loading and self.keepalive_interval is not None:
            self.owner.poll()

    def _wait(self):
        self.guard()
        self.poll_load_keepalive()
        if self.process is not None:
            check_process(self.process)

    def _peer_guard(self):
        self.guard(full=True)
        self._wait()
        if self.peer is None or not socket_owner(self.peer, self.sock.getsockname(), self.process):
            raise RuntimeError('participant_socket_not_owned')
        self.guard(full=True)

    def enroll(self, epoch, deadline):
        super().enroll(epoch, min(deadline, self.deadline))
        self._peer_guard()

    def request(self, operation):
        if operation == 'pause' and self.server_control and self.playback is not None:
            self.playback.before_pause()
        self._peer_guard()
        if operation == 'resume' and self.seat_watch is not None:
            self.seat_watch.allow_seat_handoff()
        previous = self.timeout
        command_timeout = min(previous, 30)
        if self.deadline is not None:
            command_timeout = min(command_timeout, marked.remaining(self.deadline))
        self.timeout = command_timeout
        try:
            super().request(operation)
            self._peer_guard()
        finally:
            self.timeout = previous
        if self.server_control:
            self.confirmations[operation] += 1


def ticket_for(bound):
    ticket = {key: getattr(bound.offer, key) for key in download.FIELDS if key != 'slot'}
    return dict(ticket, slot='save_009')


def stage_offered_save(owner, new_directory, target):
    """Download, then copy and verify through the owner's single transition.

    copy_to_new_slot requires the downloaded state and itself verifies the
    completed target before advancing to staged. Do not mutate its metadata
    after verification: the owner rechecks its full fingerprint at claim time.
    X4 must not be running yet; its later read-only bind protects game writes.
    """
    downloaded = owner.download(new_directory)
    return owner.copy_to_new_slot(downloaded, target)


def verify_receipts(path, ticket, marker):
    before = marked.file_evidence._read_regular(path, marked.file_evidence.MAX_LOG, collect=True)
    marked.validate_client_receipts(before[2], ticket, marker)
    if marked.file_evidence._read_regular(path, marked.file_evidence.MAX_LOG, collect=True) != before:
        raise ValueError('client_receipts_changed')


def observe_and_activate(owner, participant, watch, ticket, marker, guard, verify_loaded, result, *,
                         registry_check=None, motion_consumer=None, company_purchase_exchange=None,
                         company_purchase_statement=None, company_purchase_statement_result=None,
                         company_undock_exchange=None, company_trade_view_exchange=None, company_fleet_exchange=None,
                         company_sale_exchange=None, play_until_exit=False):
    """No owner load assertion is made until complete native evidence is checked."""
    result['phase'] = 'load_receipts'
    while True:
        guard()
        participant.poll_load_keepalive()
        if watch.poll() and watch.stage == 2:
            verify_loaded()
            verify_receipts(watch.path, ticket, marker)
            if registry_check is not None:
                registry_check()
            break
        time.sleep(.1)
    attempt = ticket['attempt_id']
    owner.load_requested(attempt)
    owner.loaded_marker(attempt, marker)
    owner.paused(attempt, True)
    participant.handle = ticket['connection_handle']
    result['phase'] = 'enroll'
    participant.enroll(ticket['world_epoch'], participant.deadline)
    result['phase'] = 'local_pause'
    participant.request('pause')
    verify_loaded()
    verify_receipts(watch.path, ticket, marker)
    if registry_check is not None:
        registry_check()
    guard(full=True)
    result.update(load_requested=True, loaded_marker_observed=True, pause_observed=True,
                  admission_state='indeterminate')
    participant.loading = False  # activate may receive server participant traffic immediately.
    result['phase'] = 'claim'
    motion_options = {} if motion_consumer is None else {'apply_motion': motion_consumer}
    if company_purchase_exchange is not None:
        motion_options['company_purchase_exchange'] = company_purchase_exchange
    if company_fleet_exchange is not None:
        motion_options['company_fleet_exchange'] = company_fleet_exchange
    if company_sale_exchange is not None:
        motion_options['company_sale_exchange'] = company_sale_exchange
    if company_trade_view_exchange is not None:
        motion_options['company_trade_view_exchange'] = company_trade_view_exchange
    if company_undock_exchange is not None:
        motion_options['company_undock_exchange'] = company_undock_exchange
    if company_purchase_statement is not None:
        motion_options.update(company_purchase_statement=company_purchase_statement,
                              company_purchase_statement_result=company_purchase_statement_result)
    command_timeout = 30 if play_until_exit else min(30, marked.remaining(participant.deadline))
    owner.activate(attempt, participant, participant_timeout=command_timeout, **motion_options)
    guard(full=True)
    if play_until_exit:
        # The load owner has handed off to per-command participant budgets.
        participant.deadline = None
    result['admission_state'] = 'accepted'
    participant.server_control = True
    result['phase'] = 'active'


def validate_environment(args, *, player_mode=False):
    if args.internal_native_client is not True or sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('explicit_unprivileged_linux_opt_in_required')
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('native_main_thread_required')
    out = snapshot.output_path(args.out)
    game = args.game.resolve(strict=True)
    if live.sha(game/'X4') != live.EXE_SHA or not (game/'extensions').is_dir() or not shutil.which('bwrap'):
        raise ValueError('pinned_game_or_namespace_unavailable')
    if not player_mode and live.x4_executables():
        raise ValueError('another_x4_running')
    if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', args.display):
        raise ValueError('local_x11_display_required')
    default_auth = str(Path.home() / '.Xauthority') if player_mode else ''
    auth = Path(os.environ.get('XAUTHORITY', default_auth))
    if not auth.is_file() or not os.access(auth, os.R_OK):
        raise ValueError('readable_xauthority_required')
    if ((args.timeout is not None and
            (not math.isfinite(args.timeout) or not 0 < args.timeout <= MAX_NATIVE_BUDGET)) or
            (not player_mode and (args.timeout is None or not math.isfinite(args.active_seconds) or
                                 not 0 < args.active_seconds < args.timeout)) or
            not 0 <= args.seed <= 0xffffffff):
        raise ValueError('invalid_native_budget')
    library = None
    if player_mode:
        if not shutil.which('cc'):
            raise ValueError('native_udp_compiler_unavailable')
    else:
        library = args.native_library.resolve(strict=True)
        if (args.native_library.is_symlink() or not stat.S_ISREG(library.stat().st_mode) or
                not re.fullmatch(r'[0-9a-f]{64}', args.native_library_sha256) or
                live.sha(library) != args.native_library_sha256):
            raise ValueError('verified_native_library_required')
    if args.archived_identity_fixture is not None and (
            args.archived_identity_fixture_sha256 is None or args.expected_content_sha256 is None):
        raise ValueError('archived_fixture_and_content_pins_required')
    if args.archived_identity_fixture is None and args.archived_identity_fixture_sha256 is not None:
        raise ValueError('archived_fixture_required')
    for digest in (args.archived_identity_fixture_sha256, args.expected_content_sha256):
        if digest is not None and not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid_compatibility_pin')
    user = pwd.getpwuid(os.geteuid())
    home = Path(user.pw_dir)
    steam = live.steam_identity(home, user.pw_uid)
    return out, game, auth.resolve(strict=True), library, user, home, steam


def error_code(error):
    """Map only known failures to fixed, credential-free diagnostic codes."""
    if isinstance(error, SessionFull):
        return SessionFull.code
    known_codes = {'native_client_deadline', 'owned_launcher_stopped', 'owned_x4_changed',
        'owned_x4_disappeared', 'steam_identity_changed', 'protected_configuration_changed',
        'owned_loaded_state_unconfirmed', 'no_server_control_confirmed', 'insufficient_active_budget',
        'participant_socket_not_owned', 'tls_startup_timeout', 'tls_startup_failed', 'tls_bridge_failed',
        'client_cleanup_failed', 'tls_cleanup_failed', 'private_content_configuration_differs',
        'archived_fixture_differs', 'native_library_copy_changed', 'local_x11_socket_unavailable',
        'native UDP module compilation failed', 'owned seat log incomplete', 'owned seat log changed'}
    code = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    if isinstance(error, CompanyShipChoiceDenied) and code in (
            'no_free_company_ships', 'company_ship_unavailable', 'stale_ship_choice',
            'company_ship_catalog_unavailable'):
        return code
    if code and code.startswith('client seat refused: ') and code.removeprefix('client seat refused: ').encode() in REFUSALS:
        return code
    if code == 'staged save changed during load attempt':
        return 'staged_save_changed_during_load'
    return code if code in known_codes else 'operation_failed'


def run(args, *, playback_driver=None, player_settings=None, select_ship=None, on_admitted=None,
        company_motion=False, software_vulkan_icd=None, company_hull_playback_index=None, company_purchases=False,
        cockpit_radar_playback=False, company_undock_test=False, company_trade_view=False, company_fleet_orders=False,
        company_sales=False,
        observed_npc=None, registered_npc_hold_trial=False, registered_npc_alignment_trial=False):
    player_mode = player_settings is not None
    if type(company_motion) is not bool or (company_motion and not player_mode):
        raise ValueError('company motion requires player mode')
    if type(company_purchases) is not bool or (company_purchases and not player_mode):
        raise ValueError('Company purchases require player mode')
    if type(company_fleet_orders) is not bool or (company_fleet_orders and not company_purchases):
        raise ValueError('Company fleet orders require Company purchases')
    if type(company_sales) is not bool or (company_sales and not (company_trade_view and company_purchases)):
        raise ValueError('Company sales require Company purchases and trade view')
    if type(company_trade_view) is not bool or (company_trade_view and not company_purchases):
        raise ValueError('Company trade view requires Company purchases')
    if type(company_undock_test) is not bool or (company_undock_test and not company_purchases):
        raise ValueError('Company undock test requires Company purchases')
    observed_npc = validate_npc_observation(observed_npc, company_motion=company_motion,
                                            playback_index=company_hull_playback_index,
                                            cockpit_radar_playback=cockpit_radar_playback)
    if type(registered_npc_alignment_trial) is not bool or (registered_npc_alignment_trial and not registered_npc_hold_trial):
        raise ValueError('NPC alignment trial requires its reversible hold')
    if type(registered_npc_hold_trial) is not bool or (registered_npc_hold_trial and
            (not getattr(args, 'base_game_only', False) or observed_npc != (943, 'MFQ-419'))):
        raise ValueError('NPC hold trial requires the vanilla MFQ-419 observation')
    validate_playback_index(company_hull_playback_index, company_motion=company_motion,
                            cockpit_radar_playback=cockpit_radar_playback)
    if player_mode and (not callable(select_ship) or not callable(on_admitted) or playback_driver is not None):
        raise ValueError('player seat selection and admission callback required')
    if software_vulkan_icd is not None and (
        not company_motion or args.keepalive_interval is None or not 0 < args.keepalive_interval <= 30
    ):
        raise ValueError('software Vulkan requires company motion and load keepalive')
    playback_scripts = native_playback.read_config(
        getattr(args, 'internal_saved_playback_config', None), args, playback_driver)
    if player_mode:
        out, game, auth, library, user, home, steam = validate_environment(args, player_mode=True)
        connection_settings = player_settings
        if playback_scripts is not None or args.archived_identity_fixture is not None:
            raise ValueError('fixture diagnostics unavailable in player mode')
        library_digest = None
    else:
        out, game, auth, library, user, home, steam = validate_environment(args)
        connection_settings = dict(server_host=args.host, server_port=args.port,
            server_fingerprint=args.fingerprint, file_slot=args.slot,
            shared_key=bytes.fromhex(stage_cli._read_secret_file(args.shared_key_file, 64)),
            file_token=stage_cli._read_secret_file(args.file_token_file, 32))
        library_digest = args.native_library_sha256
    join_deadline = time.monotonic() + (MAX_NATIVE_BUDGET if args.timeout is None else args.timeout)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = dict(success=False, scope='one private Linux native client; cooperative load/control; no gameplay proof',
        exact_loaded_file_proven=False, admission_state='not_attempted', server_pause_confirmations=0,
        server_resume_confirmations=0, owned_cleanup_confirmed=False, archived_identity_fixture_installed=False)
    processes, lockbox, handlers, cleanup_errors, evidence_errors = [], [], {}, [], []
    participant = watch = target = ticket = playback = seat_watch = None
    company_source = company_client = purchase_source = fleet_source = None
    protected = {}
    resources_stopped = False
    cleanup_attempted = False
    result['phase'] = 'connect'
    stop_requested = False

    def cancel(number, frame):
        nonlocal stop_requested
        if player_mode and number == signal.SIGINT and result['phase'] == 'active':
            stop_requested = True
        else:
            snapshot.cancel_capture(number, frame)

    try:
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[number] = signal.signal(number, cancel)
        if player_mode:
            result['phase'] = 'build_native_module'
            library = out / 'x4lan_net.so'
            compile_net(shutil.which('cc'), library)
            library_digest = live.sha(library)
            result['phase'] = 'connect'
        else:
            download.create_owned_lock(user.pw_uid, user.pw_gid, lockbox)
        motion_options = {'motion_enabled': True} if company_motion else {}
        with client_connection(**connection_settings, deadline=join_deadline,
                keepalive_interval=args.keepalive_interval, **motion_options) as owner:
            try:
                case_options = {'base_game_only': True} if getattr(args, 'base_game_only', False) else {}
                case, profile, binds, lock = snapshot.prepare_case(out, game, home, **case_options)
                if any((profile/'save').iterdir()):
                    raise ValueError('private_save_directory_not_empty')
                result['phase'] = 'offer'
                bound = owner.request_offer()
                index = None
                if player_mode:
                    result['phase'] = 'choose_company_ship'
                    index = reserve_company_ship(owner, select_ship)
                    validate_playback_index(company_hull_playback_index,
                                            company_motion=company_motion, own_index=index)
                    if company_motion:
                        owner.request_ship_motion()
                    if company_purchases:
                        capabilities = {}
                        if company_undock_test:
                            capabilities['undock'] = True
                        if company_trade_view:
                            capabilities['trade_view'] = True
                        if company_sales:
                            capabilities['sales'] = True
                        if company_fleet_orders:
                            capabilities['fleet_orders'] = True
                            capabilities['fleet_order_version'] = 2
                        purchase_epoch = owner.enable_company_purchases(**capabilities)
                result['phase'] = 'download'
                target = profile/'save'/snapshot.SLOT
                stage_offered_save(owner, out/'download', target)
                ticket = ticket_for(bound)
                process_identity = None
                next_context_check = 0

                def guard(*, full=False):
                    nonlocal process_identity, next_context_check
                    now = time.monotonic()
                    if participant.deadline is not None and now >= participant.deadline:
                        raise TimeoutError('native_client_deadline')
                    owner_process, started = processes[0]
                    if owner_process.poll() is not None:
                        raise RuntimeError('owned_launcher_stopped')
                    if full or now >= next_context_check:
                        observed = download.game_identity(owner_process, started, game)
                        if observed is not None:
                            if process_identity is not None and observed != process_identity:
                                raise RuntimeError('owned_x4_changed')
                            process_identity = observed
                            image = (Path('/proc')/str(observed[0])/'exe').stat()
                            participant.process = dict(pid=observed[0], start_ticks=observed[1],
                                exe_dev=image.st_dev, exe_ino=image.st_ino)
                        elif process_identity is not None:
                            raise RuntimeError('owned_x4_disappeared')
                        if live.steam_identity(home, user.pw_uid) != steam:
                            raise RuntimeError('steam_identity_changed')
                        next_context_check = now+.5
                    if full and any(live.sha(path) != digest for path, digest in protected.items()):
                        raise ValueError('protected_configuration_changed')
                    return True

                result['phase'] = 'prepare'
                participant = NativeParticipant(owner, join_deadline, guard,
                    keepalive_interval=args.keepalive_interval)
                participant.server_library = library
                config = download.install_probe(case, profile, ticket)
                participant.install(case, include_identity=False)
                marked.install_marked_load_probe(case)
                lab = case/'extensions/x4_lan_lab'
                fixture_path = None
                if args.expected_content_sha256 is not None and live.sha(profile/'content.xml') != args.expected_content_sha256:
                    raise ValueError('private_content_configuration_differs')
                if args.archived_identity_fixture is not None:
                    fixture_bytes = marked.file_evidence._read_regular(args.archived_identity_fixture, 65536, collect=True)[2]
                    if hashlib.sha256(fixture_bytes).hexdigest() != args.archived_identity_fixture_sha256:
                        raise ValueError('archived_fixture_differs')
                    fixture_path = lab/'md/authority_identity_fixture.xml'
                    with fixture_path.open('xb') as output:
                        output.write(fixture_bytes)
                    result['archived_identity_fixture_installed'] = True
                if playback_scripts is not None:
                    playback = native_playback.install(case, profile, bound, ticket, participant,
                        playback_scripts, playback_driver, join_deadline, out)
                    participant.playback = playback
                paths = [config, lab/'ui.xml', profile/'content.xml', profile/'config.xml',
                    lab/'md/authority_checkpoint_marker.xml', lab/'ui/authority_checkpoint_marker_probe.lua',
                    *participant.protected(case)]
                paths += [lab/'ui'/name for name in
                    ('client_load_barrier.lua', 'x4_client_load_engine.lua', 'authority_download_load_probe.lua')]
                if player_mode:
                    source_options = dict(software_rendered=software_vulkan_icd is not None)
                    if company_motion:
                        company_source = CompanyMotionSource(
                            playback_index=company_hull_playback_index, observed_npc=observed_npc,
                            cockpit_radar_playback=cockpit_radar_playback, registered_npc_hold_trial=registered_npc_hold_trial,
                            registered_npc_alignment_trial=registered_npc_alignment_trial)
                        source_options['company_motion'] = company_source
                    if company_purchases:
                        sale_options = {'sales': True} if company_sales else {}
                        purchase_source = CompanyPurchaseSource(expected_epoch=purchase_epoch,
                            server_fingerprint=connection_settings['server_fingerprint'], undock=company_undock_test,
                            trade_view=company_trade_view, **sale_options)
                        source_options['company_purchase'] = purchase_source
                    if company_fleet_orders:
                        from authority.company_fleet_source import CompanyFleetSource

                        fleet_source = CompanyFleetSource(version=owner._company_fleet_order_version)
                        source_options['company_fleet'] = fleet_source
                    seat_paths, seat_watch = company_seat.install(case, profile, bound, target, index,
                        owner.poll, **source_options)
                    paths.extend(seat_paths)
                if fixture_path is not None:
                    paths.append(fixture_path)
                if playback is not None:
                    paths.extend(playback.paths)
                protected = {path: live.sha(path) for path in paths}
                if live.sha(lab/'native/x4lan_net.so') != library_digest:
                    raise ValueError('native_library_copy_changed')
                command = load.readonly_save_command(case, game, binds, lock,
                    args.display, auth, user.pw_name, args.seed, target)
                if software_vulkan_icd is not None:
                    import authority_software_vulkan
                    command = authority_software_vulkan.constrain(command, display=args.display,
                        authorization=auth, icd=software_vulkan_icd)
                    authority_software_vulkan.verify(command, out/'client-software-vulkan.log')
                if player_mode:
                    command = command_with_private_lock(command, display=args.display, authorization=auth)
                position = command.index('--die-with-parent')
                for path in protected:
                    command[position:position] = ['--ro-bind', str(path), str(path)]
                    position += 3
                # The existing signal-fenced helper registers ownership before gate release.
                result['phase'] = 'launch'
                load.spawn_owned_gated(command, out/'process.log', processes)
                watch = seat_watch if seat_watch is not None else download.ReceiptWatch(
                    profile/'engine-output.log', ticket)

                def verify_loaded():
                    guard(full=True)
                    if process_identity is None or live.sha(target) != ticket['sha256']:
                        raise ValueError('owned_loaded_state_unconfirmed')

                registry_check = None
                if seat_watch is not None:
                    registry_check = seat_watch.verify
                    participant.seat_watch = seat_watch
                elif playback is not None:
                    registry_check = playback.verify_registry
                purchase_options = {}
                if purchase_source is not None:
                    def validate_purchase_peer(peer):
                        guard()
                        return participant.process is not None and socket_owner(
                            peer, purchase_source.socket.getsockname(), participant.process)

                    purchase_source.bind_validator(validate_purchase_peer)
                    purchase_options = purchase_source.control_callbacks(owner)
                if fleet_source is not None:
                    def validate_fleet_peer(peer):
                        guard()
                        return participant.process is not None and socket_owner(
                            peer, fleet_source.socket.getsockname(), participant.process)

                    fleet_source.bind_validator(validate_fleet_peer)
                    purchase_options['company_fleet_exchange'] = fleet_source.exchange
                observe_and_activate(owner, participant, watch, ticket, bound.marker,
                    guard, verify_loaded, result, registry_check=registry_check,
                    motion_consumer=(playback_driver.accept_motion
                        if type(playback_driver) is native_playback.LiveSourceInput else None),
                    play_until_exit=player_mode and args.timeout is None, **purchase_options)
                if player_mode:
                    result['reserved_company_ship_index'] = index
                    result['company_seat_at_admission'] = seat_watch.seat_at_admission
                    if seat_watch.seat_status == 'controlled':
                        result['controlled_company_ship_index'] = index
                    if company_source is not None:
                        from authority.company_motion_reconnect import ReconnectingCompanyMotionClient

                        def validate_peer(peer):
                            guard(full=True)
                            return participant.process is not None and socket_owner(
                                peer, company_source.socket.getsockname(), participant.process)

                        company_client = ReconnectingCompanyMotionClient.observing(owner.motion_port,
                            owner.ship_motion_credential, company_source, validate_peer)
                    on_admitted(index)
                    result['session_end'] = poll_player(owner, processes[0][0], guard,
                        participant.deadline, lambda: stop_requested, company_client=company_client,
                        seat_watch=seat_watch)
                    if seat_watch.seat_status == 'controlled':
                        result['controlled_company_ship_index'] = index
                else:
                    active_until = time.monotonic()+args.active_seconds
                    if active_until >= join_deadline:
                        raise TimeoutError('insufficient_active_budget')
                    while time.monotonic() < active_until:
                        guard()
                        owner.poll(.1)
                        if playback is not None:
                            if playback.thread is None and participant.confirmations['resume']:
                                guard(full=True)
                                playback.start(participant.process, protected)
                            playback.poll()
                    guard(full=True)
                if playback is not None:
                    result['playback'] = playback.finish()
                result.update(server_pause_confirmations=participant.confirmations['pause'],
                              server_resume_confirmations=participant.confirmations['resume'])
                if not sum(participant.confirmations.values()):
                    raise RuntimeError('no_server_control_confirmed')
                result['success'] = True
            except BaseException:
                result['failure_phase'] = result['phase']
                raise
            finally:
                result['phase'] = 'cleanup'
                # Native ownership is fenced before owner/tunnel/thread release.
                for number in handlers:
                    signal.signal(number, signal.SIG_IGN)
                if company_client is not None:
                    try:
                        company_client.close()
                        result['company_motion'] = company_client.stats()
                        result['company_observer'] = company_source.observer_stats()
                        if company_client.failure is not None:
                            result['company_motion_failure'] = company_client.failure
                    except BaseException:
                        cleanup_errors.append('company_motion_client_cleanup_failed')
                if company_source is not None:
                    try:
                        company_source.close()
                    except BaseException:
                        cleanup_errors.append('company_motion_source_cleanup_failed')
                if purchase_source is not None:
                    try:
                        purchase_source.close()
                    except OSError:
                        cleanup_errors.append('company_purchase_source_cleanup_failed')
                if fleet_source is not None:
                    try:
                        fleet_source.close()
                    except OSError:
                        cleanup_errors.append('Company fleet order source cleanup failed')
                resources_stopped = download.stop_owned_resources([], processes, result, cleanup_errors)
                cleanup_attempted = True
                if watch is not None and ticket is not None:
                    try:
                        watch.poll()
                        verify_receipts(watch.path, ticket, bound.marker)
                        if seat_watch is not None:
                            seat_watch.verify()
                            if result['success'] and seat_watch.seat_status == 'controlled':
                                result['controlled_company_ship_index'] = index
                        if playback is not None:
                            playback.verify_registry()
                            playback.records()
                        result['final_native_evidence_valid'] = True
                    except BaseException:
                        result['success'] = False
                        result['final_native_evidence_valid'] = False
                        evidence_errors.append('final_native_evidence_invalid')
                    # A failed observation does not establish a live resource.
                    # Input integrity remains a separate release precondition
                    # and must still be checked when receipt validation fails.
                    try:
                        if live.sha(target) != ticket['sha256'] or any(
                                live.sha(path) != digest for path, digest in protected.items()):
                            raise ValueError('final_native_evidence_changed')
                    except BaseException:
                        result['success'] = False
                        cleanup_errors.append('final_native_inputs_invalid')
                if playback is not None:
                    try:
                        playback.close(native_stopped=resources_stopped)
                    except BaseException:
                        cleanup_errors.append('playback_cleanup_failed')
                    result['playback'] = dict(playback.evidence)
                    # Joining can reveal a late worker/producer failure after
                    # finish(). Keep it distinct from successful resource cleanup.
                    if getattr(playback, 'failure', None) is not None:
                        evidence_errors.append('playback_failed')
                if participant is not None:
                    result.update(server_pause_confirmations=participant.confirmations['pause'],
                                  server_resume_confirmations=participant.confirmations['resume'])
                    try:
                        participant.close()
                        if resources_stopped:
                            participant.redact()
                    except BaseException:
                        cleanup_errors.append('participant_cleanup_failed')
    except (Exception, KeyboardInterrupt) as error:
        result.setdefault('failure_phase', result['phase'])
        error_type = next((kind.__name__ for kind in (KeyboardInterrupt, TimeoutError, ValueError, OSError, RuntimeError)
                           if isinstance(error, kind)), 'other_failure')
        result.update(success=False, error_class='native_client_failed', error_type=error_type,
                      error_code=error_code(error))
        if any(note.endswith('cleanup_failed') for note in getattr(error, '__notes__', ())):
            cleanup_errors.append('client_connection_cleanup_failed')
        if error.args in (('client_cleanup_failed',), ('tls_cleanup_failed',)):
            cleanup_errors.append('client_connection_cleanup_failed')
    finally:
        try:
            for number in handlers:
                signal.signal(number, signal.SIG_IGN)
            if not cleanup_attempted:
                resources_stopped = download.stop_owned_resources([], processes, result, cleanup_errors)
            try:
                if live.steam_identity(home, user.pw_uid) != steam:
                    cleanup_errors.append('steam_identity_changed')
            except BaseException:
                cleanup_errors.append('steam_identity_unavailable')
            if lockbox:
                download.finish_owned_lock(*lockbox[0], resources_stopped and not cleanup_errors, cleanup_errors)
            if evidence_errors:
                result.update(success=False, evidence_errors=evidence_errors)
            if cleanup_errors:
                result.update(success=False, cleanup_errors=cleanup_errors)
            result['phase'] = 'finished'
            (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)

    return result


def poll_player(owner, process, guard, deadline, stop_requested, *, company_client=None, seat_watch=None):
    """Serve the admitted client until its own game exits or the user leaves."""
    next_seat_check = 0
    while deadline is None or time.monotonic() < deadline:
        if stop_requested():
            return 'user_left'
        if process.poll() == 0:
            return 'game_closed'
        try:
            guard()
        except RuntimeError as error:
            if error.args not in (('owned_x4_disappeared',), ('owned_launcher_stopped',)):
                raise
            try:
                if process.wait(timeout=.25) == 0:
                    return 'game_closed'
            except subprocess.TimeoutExpired:
                pass
            raise
        owner.poll(.1)
        if seat_watch is not None and seat_watch.seat_status == 'aboard' and time.monotonic() >= next_seat_check:
            seat_watch.poll()
            next_seat_check = time.monotonic() + .25
        if company_client is not None:
            company_client.poll()
    return 'session_limit'


def main(argv=None, *, playback_driver=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--internal-native-client', action='store_true')
    for name in ('game', 'out', 'shared-key-file', 'file-token-file', 'native-library'):
        parser.add_argument('--'+name, required=True, type=Path)
    for name in ('display', 'host', 'fingerprint', 'native-library-sha256'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--slot', required=True, type=int)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--active-seconds', type=float, default=60)
    parser.add_argument('--keepalive-interval', type=float)
    parser.add_argument('--seed', type=int, default=276944566)
    parser.add_argument('--archived-identity-fixture', type=Path)
    parser.add_argument('--archived-identity-fixture-sha256')
    parser.add_argument('--expected-content-sha256')
    parser.add_argument('--internal-saved-playback-config', type=Path)
    try:
        result = run(parser.parse_args(argv), playback_driver=playback_driver)
    except (Exception, KeyboardInterrupt):
        result = dict(success=False, error_class='native_client_refused', exact_loaded_file_proven=False)
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
