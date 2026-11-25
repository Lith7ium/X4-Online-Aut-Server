"""One opt-in Windows fixture join using caller-owned disposable resources.

This composes the existing experimental load, participant and process owners.
It does not prove loaded bytes, native Windows operation, gameplay or a WAN
session. The workspace owns the profile/view/log and must keep them exclusive.
"""
import math
import re
import secrets
import socket
import time

from .checkpoints import Checkpoint
from .company_motion_source import validate_playback_index
from .company_ship_selection import reserve_company_ship
from .experimental_client_owner import CompanyShipChoiceDenied
from .experimental_client_session import client_connection
from .experimental_windows_fixture_overlay import install_fixture_overlay
from .local_participant_control import LocalParticipantControl
from .observed_client_session import ObservedClientSession
from .participant_motion import ParticipantMotion
from .registry_availability_evidence import verify_join_snapshot
from .saved_registry import saved_status
from .windows_x4_process import WindowsX4Process
from .windows_motion_peer import WindowsMotionPeer


class _GameClosed(Exception):
    """The admitted pilot's owned X4 process returned exit code zero."""


class FixtureCleanupError(RuntimeError):
    """An owned resource did not confirm cleanup; do not reuse the profile."""

    def __init__(self, message, *, process_owner, workspace):
        super().__init__(message)
        self.process_owner = process_owner
        self.workspace = workspace


def _annotate(error, message):
    try:
        error.add_note(message)
    except BaseException:
        pass


def validate_motion_request(request, participant_timeout):
    """Validate the explicit stream and optional fixture-only replica control."""
    if request is None:
        return
    fields = {'generation', 'indices', 'timeout'}
    if (type(request) is not dict or
            set(request) - {'saved_playback_fixture', 'saved_replica_control_fixture',
                            'saved_initial_alignment_fixture'} != fields or
            type(request['generation']) is not str or
            not re.fullmatch(r'[0-9a-f]{32}', request['generation']) or
            type(request['indices']) is not list or not 1 <= len(request['indices']) <= 32 or
            any(type(index) is not int or not 1 <= index <= 200000 for index in request['indices']) or
            len(set(request['indices'])) != len(request['indices']) or
            type(request['timeout']) not in (int, float) or not math.isfinite(request['timeout']) or
            not 0 < request['timeout'] <= 30 or request['timeout'] >= participant_timeout or
            participant_timeout + 2 * request['timeout'] + 1 > 300):
        raise ValueError('invalid bounded client motion request')
    if 'saved_playback_fixture' in request and (
            request['saved_playback_fixture'] != 'recovery_enh_870' or request['indices'] != [1318]):
        raise ValueError('invalid saved playback request')
    if 'saved_replica_control_fixture' in request and (
            request['saved_replica_control_fixture'] != 'recovery_enh_870' or
            request.get('saved_playback_fixture') != 'recovery_enh_870'):
        raise ValueError('invalid saved replica control request')
    if 'saved_initial_alignment_fixture' in request and (
            request['saved_initial_alignment_fixture'] != 'recovery_enh_870' or
            request.get('saved_replica_control_fixture') != 'recovery_enh_870' or
            request.get('saved_playback_fixture') != 'recovery_enh_870'):
        raise ValueError('invalid saved initial alignment request')


def _motion_settings(request, registry_status, bound, controller, registry_nonce, port):
    """Combine one explicit source generation with fresh local IPC contexts."""
    if not registry_status or registry_status[0] != 'ready':
        raise ValueError('client motion requires an initialized saved registry')
    excluded = {registry_status[1], bound.offer.world_epoch, bound.offer.attempt_id,
                bound.offer.checkpoint_id.removeprefix('checkpoint-'), controller.lease, registry_nonce}
    if request['generation'] in excluded:
        raise ValueError('motion generation overlaps an existing context')
    excluded.add(request['generation'])
    settings = {'port': port, 'generation': request['generation'], 'timeout': request['timeout']}
    for name in ('lease', 'attempt', 'nonce', 'router_generation'):
        token = secrets.token_hex(16)
        while token in excluded:
            token = secrets.token_hex(16)
        excluded.add(token)
        settings[name] = token
    if len(request['indices']) == 1:
        settings['index'] = request['indices'][0]
    else:
        settings['indices'] = list(request['indices'])
    for option in ('saved_playback_fixture', 'saved_replica_control_fixture',
                   'saved_initial_alignment_fixture'):
        if option in request:
            settings[option] = request[option]
    return settings


def run_fixture_session(*, enabled=False, workspace, server_host, server_port,
                        server_fingerprint, shared_key, file_token, file_slot,
                        windows_dll, dll_sha256, deadline, keepalive_interval,
                        stop_requested, io_timeout=30, participant_timeout=30, motion_request=None,
                        require_registry_snapshot=False, select_ship=None, company_motion=False,
                        company_hull_playback_index=None, on_stage=None, company_purchases=False,
                        play_until_exit=False, cockpit_radar_playback=False):
    """Stage, admit and poll one fixture, stopping its owned job on every exit.

    The workspace must provide prepare/view/save_dir/download_dir/guard,
    register_overlay/read_log/redact_config/close. Its path, ACL, file and log
    assertions are caller responsibilities. No operation is automatically
    retried; cleanup cannot undo a possible claim or participant command.
    ``stop_requested`` is called only after admission. It and the workspace
    callbacks must be bounded and cannot be interrupted by the deadline. No
    profile is deleted or reused here.

    The staged save is inspected on this same thread before launching X4.
    Its streaming reader pumps the load connection between bounded chunks.
    The saved registry header is then required in both the live and final log;
    this does not independently prove runtime actor identity.
    """
    if (enabled is not True or type(require_registry_snapshot) is not bool or not callable(stop_requested) or
            (on_stage is not None and not callable(on_stage)) or
            type(deadline) not in (int, float) or not math.isfinite(deadline) or
            not 0 < deadline - time.monotonic() <= 3600 or
            type(keepalive_interval) not in (int, float) or not math.isfinite(keepalive_interval) or
            not 0 < keepalive_interval <= 300 or
            type(io_timeout) not in (int, float) or not math.isfinite(io_timeout) or not 0 < io_timeout <= 300 or
            type(participant_timeout) not in (int, float) or not math.isfinite(participant_timeout) or
            not 0 < participant_timeout <= 299 or
            type(server_host) is not str or not server_host or type(server_port) is not int or
            not 1 <= server_port <= 65535 or type(file_slot) is not int or not 1 <= file_slot <= 8 or
            type(server_fingerprint) is not str or not re.fullmatch(r'[0-9a-f]{64}', server_fingerprint) or
            type(shared_key) is not bytes or len(shared_key) != 32 or
            type(file_token) is not str or not re.fullmatch(r'[0-9a-f]{32}', file_token) or
            type(dll_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', dll_sha256)):
        raise ValueError('invalid enabled fixture session configuration')
    if select_ship is not None and (not callable(select_ship) or not require_registry_snapshot):
        raise ValueError('company ship choice requires full registry inspection and a selector')
    if type(company_motion) is not bool or (company_motion and (select_ship is None or motion_request is not None)):
        raise ValueError('company motion requires a seat selection and its own transport')
    if type(play_until_exit) is not bool or (play_until_exit and (select_ship is None or motion_request is not None)):
        raise ValueError('open-ended play requires a Company pilot')
    if type(company_purchases) is not bool or (company_purchases and (select_ship is None or not require_registry_snapshot)):
        raise ValueError('Company purchases require a verified selected seat')
    validate_playback_index(company_hull_playback_index, company_motion=company_motion,
                            cockpit_radar_playback=cockpit_radar_playback)
    validate_motion_request(motion_request, participant_timeout)

    outer_error = None
    try:
        workspace.prepare()
        motion_options = {'motion_enabled': True} if company_motion else {}
        with client_connection(server_host=server_host, server_port=server_port,
                server_fingerprint=server_fingerprint, shared_key=shared_key, file_token=file_token,
                file_slot=file_slot, deadline=deadline, io_timeout=io_timeout,
                keepalive_interval=keepalive_interval, **motion_options) as owner:
            controller = process = session = None
            company_source = company_client = purchase_source = None
            motion_socket = motion = None
            launch_attempted = overlay_registered = False
            original = None
            cleanup_errors = []
            process_stopped = False
            result = None
            try:
                if time.monotonic() >= deadline or workspace.guard() is not True:
                    raise RuntimeError('fixture workspace unavailable')
                bound = owner.request_offer()
                seat_index = None if select_ship is None else reserve_company_ship(owner, select_ship)
                if on_stage is not None and seat_index is not None:
                    on_stage('company_ship_reserved', seat_index)
                validate_playback_index(company_hull_playback_index,
                                        company_motion=company_motion, own_index=seat_index)
                if company_motion:
                    owner.request_ship_motion()
                if company_purchases:
                    purchase_epoch = owner.enable_company_purchases()
                download = owner.download(workspace.download_dir)
                staged_path = owner.copy_to_new_slot(download, workspace.save_dir / 'save_009.xml.gz')
                if time.monotonic() >= deadline or workspace.guard() is not True:
                    raise RuntimeError('fixture workspace unavailable before registry inspection')
                checkpoint = Checkpoint(bound.offer.checkpoint_id, staged_path, bound.offer.sha256,
                                        bound.offer.size, bound.marker)
                registry_snapshot = None
                if require_registry_snapshot:
                    registry_snapshot = verify_join_snapshot(checkpoint, pump=owner.poll)
                    registry_status = ('ready', registry_snapshot['epoch'], registry_snapshot['issued_count'],
                                       registry_snapshot['mapping_sha256'])
                    if motion_request is not None:
                        operational_bits = registry_snapshot['operational_bits']
                        if any(index > len(operational_bits) or operational_bits[index - 1] != '1'
                               for index in motion_request['indices']):
                            raise ValueError('requested ship is unavailable for motion in the checkpoint')
                else:
                    registry_status = saved_status(checkpoint, pump=owner.poll)
                if time.monotonic() >= deadline or workspace.guard() is not True:
                    raise RuntimeError('fixture workspace unavailable after registry inspection')
                registry_nonce = secrets.token_hex(16)
                controller = LocalParticipantControl(enabled=True, timeout=participant_timeout)
                motion_settings = None
                if motion_request is not None:
                    motion_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    motion_socket.bind(('127.0.0.1', 0))
                    motion_settings = _motion_settings(motion_request, registry_status, bound,
                        controller, registry_nonce, motion_socket.getsockname()[1])
                source_options = {}
                if company_motion:
                    from .company_motion_source import CompanyMotionSource
                    company_source = CompanyMotionSource(playback_index=company_hull_playback_index,
                                                        cockpit_radar_playback=cockpit_radar_playback,
                                                        on_stage=on_stage)
                    source_options['company_motion'] = company_source
                if company_purchases:
                    from .company_purchase_source import CompanyPurchaseSource
                    purchase_source = CompanyPurchaseSource(expected_epoch=purchase_epoch,
                                                            server_fingerprint=server_fingerprint)
                    source_options['company_purchase'] = purchase_source
                hashes = install_fixture_overlay(workspace.view, new_test_view=True, bound_offer=bound,
                    save_path=str(workspace.save_dir), participant_port=controller.sock.getsockname()[1],
                    participant_lease=controller.lease, windows_dll=windows_dll, dll_sha256=dll_sha256,
                    registry_status=registry_status, registry_nonce=registry_nonce, registry_snapshot=registry_snapshot,
                    motion_settings=motion_settings, seat_index=seat_index, **source_options)
                workspace.register_overlay(hashes)
                overlay_registered = True
                def launch_guard():
                    return time.monotonic() < deadline and workspace.guard() is True and time.monotonic() < deadline

                if launch_guard() is not True:
                    raise RuntimeError('fixture workspace changed before launch')
                process = WindowsX4Process(enabled=True)
                launch_attempted = True
                process.start(workspace.view, profile_id=45841, guard=launch_guard)

                # The initial deadline covers the entire join, including the claim.
                # An admitted player still has finite participant and socket timeouts.
                admission_complete = False

                def expired():
                    return not (play_until_exit and admission_complete) and time.monotonic() >= deadline

                def running_guard():
                    if expired() or workspace.guard() is not True:
                        return False
                    if process.is_running() is not True:
                        if play_until_exit and admission_complete and process.exit_code() == 0:
                            raise _GameClosed()
                        return False
                    return workspace.guard() is True and not expired()

                if motion_settings is not None:
                    motion = ParticipantMotion(motion_socket,
                        {'motion': {**motion_settings, 'epoch': registry_status[1]}}, running_guard,
                        WindowsMotionPeer(process, motion_socket))
                purchase_options = {}
                if purchase_source is not None:
                    purchase_source.bind_validator(WindowsMotionPeer(process, purchase_source.socket))
                    purchase_options = purchase_source.control_callbacks(owner)
                session = ObservedClientSession(owner, bound, controller, slot='save_009',
                    read_log=workspace.read_log, guard=running_guard, deadline=deadline,
                    registry_status=registry_status, registry_nonce=registry_nonce,
                    require_full_inspection=require_registry_snapshot, seat_index=seat_index,
                    apply_motion=motion, motion_control=motion, on_stage=on_stage, **purchase_options)
                if session.start() is not True:
                    raise RuntimeError('fixture admission unconfirmed')
                if play_until_exit:
                    session.continue_until_exit()
                admission_complete = True
                if company_source is not None:
                    from .company_motion_reconnect import ReconnectingCompanyMotionClient
                    validate_peer = WindowsMotionPeer(process, company_source.socket)
                    company_client = ReconnectingCompanyMotionClient.observing(owner.motion_port,
                        owner.ship_motion_credential, company_source, validate_peer)
                first_traffic = {
                    'samples_acknowledged': 'first_own_sample_acknowledged',
                    'snapshots_received': 'first_remote_pose_received',
                } if on_stage is not None else {}
                commands = 0
                try:
                    while True:
                        if expired():
                            raise TimeoutError('fixture session deadline expired')
                        if running_guard() is not True:
                            raise RuntimeError('fixture process or workspace changed')
                        stop = stop_requested()
                        if type(stop) is not bool:
                            raise ValueError('stop callback must return a boolean')
                        if expired():
                            raise TimeoutError('fixture session deadline expired')
                        if running_guard() is not True:
                            raise RuntimeError('fixture process or workspace changed')
                        if stop:
                            break
                        poll_started = time.monotonic()
                        if session.poll(.1):
                            commands += 1
                        if company_client is not None:
                            company_client.poll()
                            if first_traffic:
                                counts = company_client.stats()
                                for counter in tuple(first_traffic):
                                    if counts[counter] > 0:
                                        on_stage(first_traffic.pop(counter))
                        now = time.monotonic()
                        if expired():
                            raise TimeoutError('fixture session deadline expired')
                        delay = .05 - (now - poll_started)
                        if not play_until_exit:
                            delay = min(delay, deadline - now)
                        if delay > 0:
                            time.sleep(delay)
                except _GameClosed:
                    pass
                if motion is not None and motion.confirmed_samples == 0:
                    raise RuntimeError('client motion diagnostic consumed no source sample')
                result = {'client_admitted': True, 'participant_receipts': commands}
                if seat_index is not None:
                    result['reserved_company_ship_index'] = seat_index
                    result['company_seat_at_admission'] = session.company_seat_at_admission
                    if session.company_seat_status == 'controlled':
                        result['controlled_company_ship_index'] = seat_index
                if motion is not None:
                    result['motion_receipts'] = motion.confirmed_samples
            except BaseException as error:
                original = error
            finally:
                if company_client is not None:
                    try:
                        company_client.close()
                        if result is not None:
                            result['company_motion'] = company_client.stats()
                            result['company_observer'] = company_source.observer_stats()
                            if company_client.failure is not None:
                                result['company_motion_failure'] = company_client.failure
                    except BaseException:
                        cleanup_errors.append('company motion client cleanup failed')
                if company_source is not None:
                    try:
                        company_source.close()
                    except BaseException:
                        cleanup_errors.append('company motion source cleanup failed')
                if purchase_source is not None:
                    try:
                        purchase_source.close()
                    except OSError:
                        cleanup_errors.append('company purchase source cleanup failed')
                if motion is not None:
                    try:
                        if motion.running and not motion.stopped:
                            motion.stop()
                        if result is not None and not motion.stopped:
                            raise RuntimeError('client motion stop unconfirmed')
                        motion.close()
                        if result is not None:
                            result['motion_stop_confirmed'] = True
                    except BaseException:
                        cleanup_errors.append('client motion stop or cleanup unconfirmed')
                # A construction failure can leave only the bound socket. On
                # an IPC failure, close it even if cooperative STOP was lost.
                if motion_socket is not None:
                    try:
                        motion_socket.close()
                    except BaseException:
                        cleanup_errors.append('client motion socket cleanup failed')
                if process is not None:
                    try:
                        closed = process.close()
                        process_stopped = closed is True and process.cleanup_confirmed is True
                        if launch_attempted and not process_stopped:
                            cleanup_errors.append('owned X4 stop unconfirmed')
                    except BaseException:
                        cleanup_errors.append('owned X4 cleanup failed')
                if process_stopped and result is not None and original is None:
                    try:
                        if workspace.guard() is not True:
                            raise RuntimeError('fixture workspace unavailable')
                        final_log = workspace.read_log()
                        if (not session.verify_final_log(final_log) or
                                workspace.guard() is not True):
                            raise RuntimeError('final fixture observations incomplete')
                        if seat_index is not None and session.company_seat_status == 'controlled':
                            result['controlled_company_ship_index'] = seat_index
                    except BaseException:
                        cleanup_errors.append('final fixture observations unconfirmed')
                if session is not None:
                    try:
                        session.close()
                    except BaseException:
                        cleanup_errors.append('participant session cleanup failed')
                elif controller is not None:
                    try:
                        controller.close()
                    except BaseException:
                        cleanup_errors.append('participant controller cleanup failed')
                if overlay_registered and (not launch_attempted or process_stopped):
                    try:
                        workspace.redact_config()
                    except BaseException:
                        cleanup_errors.append('fixture configuration redaction failed')
            if cleanup_errors:
                failure = FixtureCleanupError('; '.join(cleanup_errors), process_owner=process, workspace=workspace)
                if original is not None:
                    _annotate(original, str(failure))
                    try:
                        original.fixture_cleanup = failure
                    except BaseException:
                        pass
                    raise original
                raise failure
            if original is not None:
                raise original
            result.update(owned_cleanup_confirmed=True, configuration_redacted=True)
            return result
    except BaseException as error:
        outer_error = error
        raise
    finally:
        try:
            workspace.close()
        except BaseException as cleanup_error:
            if outer_error is not None:
                _annotate(outer_error, 'fixture workspace cleanup failed: ' + type(cleanup_error).__name__)
            else:
                raise FixtureCleanupError('fixture workspace cleanup failed', process_owner=None,
                                          workspace=workspace) from cleanup_error
