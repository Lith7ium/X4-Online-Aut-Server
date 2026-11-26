#!/usr/bin/env python3
"""Run an isolated recovered X4 coordinator with loopback-only client endpoints.

Experimental development entry point. It accepts the recorded recovery fixture
or an explicitly selected, cleanly completed output of this same supervisor.
It never touches normal saves. The X4 process, profile, checkpoint store and
networking are owned by this invocation. By default, all listeners bind to
loopback. An explicit TLS configuration can expose one pinned TLS listener.
Actual client loading, gameplay and cross-platform operation are not proven.
"""
import argparse
from functools import partial
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import socket
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
import authority_recovery_bridge_live as bridge
import authority_recovery_join_source as source
import authority_recovery_live as recovery
import authority_two_x4_live as owned
import authority_load_live as load
import authority_recovered_registry_snapshot as registry_snapshots
from authority_recovered_tls_gateway import RecoveredTLSGateway, gateway_settings
import linux_gate_live as live
import linux_snapshot_capture as snapshot
from authority.checkpoints import CheckpointStore
from authority.company_purchase_ledger import CompanyPurchaseLedger, verify_purchase_ledger
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_tcp_control import ExperimentalTCPControl
from authority.experimental_tcp_participants import ExperimentalTCPParticipants, ParticipantGuardFailure
from authority.local_control_bridge import LocalControlBridge
from authority.load_messages import _pairs, _reject_constant
from authority.recovery import stage_confirmed_recovery
from authority.save_transfer import SaveOffers
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from authority_control_cycle_evidence import read_cycle_evidence


def record_fixture_throttle_uncertainty(result, error):
    """Persist only fixed, nonsecret evidence from the actual IPC failure."""
    from authority_fixture_client_throttle_ipc import ThrottleUnconfirmed, TIMING_NAMES
    if type(result) is not dict or type(result.get('fixture_sample_write')) is not dict or type(error) is not ThrottleUnconfirmed:
        raise ValueError('invalid fixture throttle diagnostic context')
    phases = {'unknown', 'waiting_ready', 'validating_ready', 'sending_go', 'waiting_started',
              'validating_started', 'waiting_stopped', 'validating_stopped', 'final_check'}
    error_classes = {'timeout', 'invalid_value', 'runtime_error', 'os_error', 'other_error', 'interrupted'}
    flags = ('attempted', 'started', 'stopped', 'started_phase_datagram_received', 'stopped_phase_datagram_received')
    if (type(error.phase) is not str or error.phase not in phases or
            type(error.error_class) is not str or error.error_class not in error_classes or
            any(type(getattr(error, name)) is not bool for name in flags) or
            type(error.elapsed_ms) is not int or error.elapsed_ms < 0):
        raise ValueError('invalid fixture throttle diagnostic values')
    timing = error.work_timing
    if (type(timing) is not dict or set(timing) != set(TIMING_NAMES) or
            any(type(entry) is not dict or set(entry) != {'calls', 'elapsed_ms'} or
                any(type(value) is not int or value < 0 for value in entry.values())
                for entry in timing.values())):
        raise ValueError('invalid fixture throttle work timing')
    result['fixture_sample_write']['throttle_uncertain'] = {
        **{name: getattr(error, name) for name in flags}, 'phase': error.phase,
        'error_class': error.error_class, 'elapsed_ms': error.elapsed_ms,
        'work_timing': {name: dict(timing[name]) for name in TIMING_NAMES}}


def read_evidence(path, limit=65536):
    """Read bounded local evidence, refusing symlinks and in-place changes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('invalid source evidence file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if (len(data) > limit or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) !=
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError('source evidence changed')
        return data
    finally:
        if fd >= 0: os.close(fd)


def verify_previous_source(store, checkpoint, profile, *, registry_status=False, witness_source=False,
                           require_registry_snapshot=False, return_supervisor_result=False,
                           require_ready_registry_script=False, return_purchase_reference=False,
                           observe_registry=None):
    """Accept only a compatible, cleanly ended local supervisor generation.

    This is local provenance, not authentication or a concurrent-writer fence.
    The caller must own the source and keep its writers and X4 processes stopped.
    stage_confirmed_recovery has independently checked the save's marker.
    """
    origin = store.directory.parent
    if store.directory.name != 'checkpoints': raise ValueError('unsupported source store')
    path = origin/'result.json'
    try:
        result = json.loads(read_evidence(path, (1 << 20) if require_registry_snapshot else 65536).decode('utf-8'),
                            object_pairs_hook=_pairs,
                            parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid source supervisor result') from error
    if (type(result) is not dict or type(result.get('supervisor_version')) is not int or result['supervisor_version'] != 1 or
            result.get('success') is not True or result.get('final_world_state') != 'paused' or
            result.get('owned_processes_remaining') != [] or 'cleanup_errors' in result or 'cleanup_error' in result or
            result.get('final_checkpoint_id') != checkpoint.checkpoint_id or
            result.get('final_checkpoint_sha256') != checkpoint.sha256):
        raise ValueError('source supervisor did not end with this confirmed checkpoint')
    if store.latest_marked_confirmed() != checkpoint:
        raise ValueError('source confirmed checkpoint changed')
    if read_evidence(store.directory/'CURRENT',4096) != read_evidence(store.directory/'CONFIRMED',4096):
        raise ValueError('source current checkpoint is not confirmed')
    case = origin/'case'; md = case/'extensions/x4_lan_lab/md'
    names={p.name for p in md.iterdir()}
    base_names={'authority_checkpoint_marker.xml','authority_identity_fixture.xml'}
    valid_names = (base_names,base_names|{'authority_ship_registry.xml'}) if registry_status else (base_names,)
    if require_ready_registry_script:
        valid_names = (base_names|{'authority_ship_registry.xml'},)
    if require_registry_snapshot:
        valid_names = (base_names|{'authority_ship_registry.xml',
                                  'authority_registry_availability_snapshot.xml'},)
    if witness_source:
        valid_names = (base_names|{'authority_ship_registry.xml','authority_registry_witness.xml'},)
        if (result.get('witness_diagnostic',{}).get('phase') != 'create' or
                read_evidence(md/'authority_registry_witness.xml') != read_evidence(ROOT/'tools/dev/authority_registry_witness.xml')):
            raise ValueError('saved witness script provenance unavailable')
    legacy_undock = 'authority_company_undock_trial.xml'
    previous_undock = (require_registry_snapshot and not witness_source and legacy_undock in names
                       and names - {legacy_undock} in valid_names)
    if md.is_symlink() or (names not in valid_names and not previous_undock):
        raise ValueError('source saved-state scripts differ')
    if previous_undock and read_evidence(md / legacy_undock) != read_evidence(ROOT / 'tools/dev' / legacy_undock):
        raise ValueError('source stock undock cue differs')
    if 'authority_ship_registry.xml' in names and read_evidence(md/'authority_ship_registry.xml') != read_evidence(ROOT/'tools/dev/authority_ship_registry.xml'):
        raise ValueError('source registry script differs')
    if require_registry_snapshot and (read_evidence(md/'authority_registry_availability_snapshot.xml') !=
                                      read_evidence(ROOT/'tools/dev/authority_registry_availability_snapshot.xml')):
        raise ValueError('source snapshot script differs')
    if read_evidence(case/'home/.config/EgoSoft/X4/45841/content.xml') != read_evidence(Path(profile)/'content.xml'):
        raise ValueError('source and recovery content differ')
    if read_evidence(md/'authority_checkpoint_marker.xml') != read_evidence(ROOT/'src/authority/md/checkpoint_marker.xml'):
        raise ValueError('source marker script differs')
    fixture = read_evidence(md/'authority_identity_fixture.xml')
    if fixture != read_evidence(ROOT/'tools/dev/authority_identity_fixture.xml'):
        raise ValueError('source identity fixture differs')
    if store.latest_marked_confirmed() != checkpoint:
        raise ValueError('source checkpoint changed during verification')
    if require_registry_snapshot:
        from authority.registry_availability_evidence import verify_join_snapshot
        observe = {'observe_universe': observe_registry} if observe_registry is not None else {}
        saved_evidence = verify_join_snapshot(checkpoint, **observe)
        registry_snapshots.verify_previous_receipt(result, checkpoint, saved_evidence)
        reference = result.get('company_purchase_ledger')
        verify_purchase_ledger(checkpoint, reference, registry_epoch=saved_evidence['epoch'])
        if previous_undock:
            if reference is None:
                raise ValueError('source stock undock cue requires a sealed Company ledger')
            from authority_native_undock import verify_saved_undock_quiescent
            verify_saved_undock_quiescent(checkpoint)
        if return_purchase_reference:
            return fixture, saved_evidence, reference
        return fixture, saved_evidence
    if result.get('company_purchase_ledger') is not None:
        raise ValueError('a saved Company ledger requires its format-3 registry')
    verify_purchase_ledger(checkpoint, None)
    if return_supervisor_result:
        return fixture, result
    return fixture


def verify_fixture_source(store, checkpoint, profile):
    """Validate either the initial fixture save or a stopped supervisor output."""
    import authority_saved_registry_checkpoint_source as fixture_source
    original_result = store.directory.parent/'registry-save-result.json'
    original_source = original_result.exists() or original_result.is_symlink()
    result_path = original_result if original_source else store.directory.parent/'result.json'
    result_bytes = read_evidence(result_path)
    verifier = fixture_source.verify if original_source else fixture_source.verify_recovered
    scripts = verifier(store.directory, checkpoint, profile)
    if read_evidence(result_path) != result_bytes:
        raise ValueError('fixture source result changed during verification')
    return scripts, result_path, result_bytes



def witness_mode(args):
    """Allow joining a witness source only in the explicit single-client probe."""
    join = getattr(args, 'registry_witness_join', False)
    if type(join) is not bool or (join and (
            getattr(args, 'diagnostic_client', False) is not True or
            getattr(args, 'registry_witness', None) is not None or
            getattr(args, 'registry_enroll', False) or getattr(args, 'diagnostic_overlap', False) or
            getattr(args, 'diagnostic_clients', None) is not None)):
        raise ValueError('witness join requires exactly one diagnostic client')
    phase = 'verify' if join else getattr(args, 'registry_witness', None)
    if phase not in (None, 'create', 'verify'):
        raise ValueError('invalid witness phase')
    if phase and not join and (getattr(args, 'registry_enroll', False) or
            getattr(args, 'diagnostic_client', False) or getattr(args, 'diagnostic_overlap', False) or
            getattr(args, 'diagnostic_clients', None) is not None):
        raise ValueError('witness diagnostic cannot admit clients')
    return join, phase


def diagnostic_source_index(args, witness_join):
    """An explicit single index is only valid in the private witness join."""
    index=getattr(args,'diagnostic_source_index',None)
    if index is not None and (witness_join is not True or type(index) is not int or not 1<=index<=200000):
        raise ValueError('diagnostic source index requires private witness join')
    return index


def diagnostic_source_candidates(args, witness_join, index):
    values=getattr(args,'diagnostic_source_candidates',None)
    if values is None:return None
    if (witness_join is not True or index is not None or type(values) not in (tuple,list) or
        not 1<=len(values)<=32 or any(type(n) is not int or not 1<=n<=200000 for n in values) or
        len(set(values))!=len(values)):
        raise ValueError('bounded source candidates require private witness join')
    return tuple(values)


def validate_diagnostic_tls(args, source_enabled):
    """Permit TLS for the existing load-only single or overlapping clients."""
    certificate = getattr(args, 'diagnostic_tls_certificate', None)
    private_key = getattr(args, 'diagnostic_tls_private_key', None)
    if ((certificate is None) != (private_key is None) or
            (certificate is not None and
             (not (getattr(args, 'diagnostic_client', False) or
                   getattr(args, 'diagnostic_overlap', False)) or source_enabled))):
        raise ValueError('diagnostic TLS requires load-only diagnostic clients and both certificate files')
    return certificate, private_key


def diagnostic_client_motion(args, source_enabled):
    enabled = getattr(args, 'diagnostic_client_motion', False)
    if type(enabled) is not bool or (enabled and source_enabled is not True):
        raise ValueError('client motion requires a private source diagnostic')
    return enabled


def diagnostic_local_pair(args, witness_join, source_index, source_candidates):
    """Enable one local source pair only for an explicitly pinned index."""
    enabled = getattr(args, 'diagnostic_local_pair', False)
    if type(enabled) is not bool or (enabled and
            (witness_join is not True or type(source_index) is not int or not 1 <= source_index <= 200000
             or source_candidates is not None)):
        raise ValueError('local pair requires one explicit private witness-join source index')
    return enabled


def diagnostic_local_pair_delivery(args, local_pair, client_motion):
    """A single pair delivery requires both existing private opt-ins."""
    enabled = getattr(args, 'diagnostic_local_pair_delivery', False)
    if type(enabled) is not bool or (enabled and (local_pair is not True or client_motion is not True)):
        raise ValueError('local pair delivery requires local pair and client motion')
    return enabled


def diagnostic_client_sector(args, pair_delivery):
    """One client sector read after the sole confirmed pair delivery."""
    enabled = getattr(args, 'diagnostic_client_sector', False)
    if type(enabled) is not bool or (enabled and pair_delivery is not True):
        raise ValueError('client sector requires private pair delivery')
    return enabled


def diagnostic_saved_apply(args, witness_join, source_index, pair_delivery):
    """Allow only the pinned one-client recovery fixture to request a write."""
    enabled = getattr(args, 'diagnostic_saved_apply', False)
    if type(enabled) is not bool or (enabled and
            (witness_join is not True or source_index != 1318 or pair_delivery is not True)):
        raise ValueError('saved apply requires the pinned private pair-delivery witness join')
    return 'recovery_enh_870' if enabled else None


def diagnostic_saved_playback(args, witness_join, source_index, client_motion, local_pair, saved_apply):
    """Opt into one bounded ENH-870 playback through the retained TCP owner."""
    enabled = getattr(args, 'diagnostic_saved_playback', False)
    if type(enabled) is not bool or (enabled and
            (witness_join is not True or source_index != 1318 or client_motion is not True or
             local_pair is not False or saved_apply is not None)):
        raise ValueError('saved playback requires one pinned witness client without pair or setter mode')
    return 'recovery_enh_870' if enabled else None


def diagnostic_latest_playback(args, saved_playback, long_motion):
    """Keep replaceable motion explicit and limited to the bounded fixture."""
    enabled = getattr(args, 'diagnostic_latest_playback', False)
    if type(enabled) is not bool or (enabled and (saved_playback is None or long_motion)):
        raise ValueError('latest playback requires bounded saved playback')
    return enabled


def diagnostic_real_time_motion(args, saved_playback, latest_playback):
    """Opt into v3 only for bounded latest ENH-870 playback."""
    enabled = getattr(args, 'diagnostic_real_time_motion', False)
    if type(enabled) is not bool or (enabled and
            (saved_playback != 'recovery_enh_870' or latest_playback is not True)):
        raise ValueError('real-time motion requires latest bounded ENH-870 saved playback')
    return enabled


def require_real_time_observation(observation):
    """Require the public source result to preserve its explicit v3 units."""
    if type(observation) is not dict or observation.get('motion_version') != 3:
        raise RuntimeError('real-time motion observation required')
    samples = observation.get('samples')
    if (type(samples) is not list or len(samples) < 2 or
            type(observation.get('second_sample_real_velocity_present')) is not bool or
            any(type(sample) is not dict or sample.get('version') != 3 or
                'capture_time' not in sample or 'real_velocity' not in sample or 'velocity' in sample
                for sample in samples) or
            observation['second_sample_real_velocity_present'] != (samples[1]['real_velocity'] is not None)):
        raise RuntimeError('real-time motion sample fields missing')


def record_saved_apply_intent(out, sample):
    """Durably mark a possible effect before dispatch; never overwrite a marker."""
    from authority.experimental_tcp_motion import motion_digest
    digest = motion_digest(sample)
    if (sample['epoch'] != '4ba7be438ebb04ec5bb85fdb95b5a488' or
            sample['index'] != 1318 or sample['sequence'] != 2):
        raise ValueError('saved apply intent sample differs from pinned fixture')
    data = (json.dumps({'fixture': 'recovery_enh_870', 'index': sample['index'],
                       'sequence': sample['sequence'], 'sample_sha256': digest,
                       'meaning': 'a client write may be requested after this record'},
                      sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open('saved-apply-intent.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                fd = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if fd >= 0:
                os.close(fd)
        os.fsync(directory)
    finally:
        os.close(directory)
    return True


def record_local_pair_result(result, profile, source_index, remaining):
    """Record only final private-log evidence after owned processes have stopped."""
    evidence = {'index': source_index, 'native_velocity_proven': False,
                'client_velocity_proven': False}
    result['local_pair_diagnostic'] = evidence
    try:
        if remaining != []:
            evidence['status'] = 'not_verified_cleanup'
        elif profile is None or not os.path.lexists(profile / 'engine-output.log'):
            evidence['status'] = 'not_observed'
        else:
            from authority_source_pair_log import read_source_pair_log
            evidence.update(read_source_pair_log(profile / 'engine-output.log'))
    except BaseException as error:
        # The diagnostic must not interrupt the supervisor's remaining cleanup.
        evidence.update(status='invalid', error_type=type(error).__name__)
    if result['success'] and evidence['status'] not in ('estimated', 'unavailable'):
        result.update(success=False, error='final local pair evidence unavailable or invalid')


def diagnostic_long_motion(args, witness_join, source_enabled, client_motion):
    enabled = getattr(args, 'diagnostic_long_motion', False)
    if type(enabled) is not bool or (enabled and not (witness_join and source_enabled and client_motion)):
        raise ValueError('long motion requires one private witness join and client motion')
    return enabled


def diagnostic_guard_timing(args, source_enabled):
    enabled = getattr(args, 'diagnostic_guard_timing', False)
    if type(enabled) is not bool or (enabled and source_enabled is not True):
        raise ValueError('guard timing requires a private source diagnostic')
    return enabled


def diagnostic_guard_cache(args, *, source_index, source_candidates, client_motion, local_pair,
                           saved_apply, client_sector, saved_playback, latest_playback,
                           real_time_motion, long_motion):
    """Limit bounded diagnostic reuse to the exact latest ENH-870 motion path."""
    age_ms = getattr(args, 'diagnostic_guard_cache_ms', 0)
    if type(age_ms) is not int or age_ms not in (0, 250):
        raise ValueError('diagnostic guard cache age must be 0 or 250 ms')
    if age_ms and not (source_index == 1318 and source_candidates is None and client_motion is True and
            local_pair is False and saved_apply is None and client_sector is False and
            saved_playback == 'recovery_enh_870' and latest_playback is True and
            real_time_motion is True and long_motion is False):
        raise ValueError('diagnostic guard cache requires bounded latest real-time ENH-870 playback')
    return age_ms


def finish_source_diagnostic(active, active_clients, guard_context, *, verify, probe,
                             verify_after=None, require_client_receipts=False, pair_delivery=False,
                             sector_intermediate_result=None, saved_playback=False, latest_playback=False):
    """Fence a retained client until the source probe has cooperatively stopped."""
    if (active_clients or len(guard_context)!=1 or not callable(verify) or not callable(probe) or
            (verify_after is not None and not callable(verify_after)) or type(require_client_receipts) is not bool or
            type(pair_delivery) is not bool or (pair_delivery and not require_client_receipts) or
            type(latest_playback) is not bool or (latest_playback and not saved_playback) or
            type(saved_playback) is not bool or (saved_playback and
                (not require_client_receipts or pair_delivery or sector_intermediate_result is not None)) or
            (sector_intermediate_result is not None and
             (type(sector_intermediate_result) is not dict or not pair_delivery))):
        raise ValueError('single source diagnostic context required')
    active_clients.append(active)
    verify()
    observation = probe(*guard_context[0], active)
    if (type(observation) is not dict or observation.get('source_stopped_before_disconnect') is not True):
        raise RuntimeError('source stop not confirmed before client finish')
    if require_client_receipts:
        from authority_paired_playback_evidence import SAMPLE_COUNT
        expected_receipts = SAMPLE_COUNT if saved_playback else (1 if pair_delivery else 2)
        receipts = observation.get('client_receipt_sample_sha256')
        if latest_playback:
            collection = observation.get('latest_playback')
            readback = observation.get('playback_readback')
            if (type(collection) is not dict or type(readback) is not dict or
                    collection.get('queued_sequences') != list(range(1, SAMPLE_COUNT + 1))):
                raise RuntimeError('latest playback collection required')
            acknowledged = collection.get('acknowledged_sequences')
            if (type(acknowledged) is not list or not 2 <= len(acknowledged) <= SAMPLE_COUNT or
                    any(type(sequence) is not int for sequence in acknowledged) or
                    acknowledged[0] != 1 or acknowledged[-1] != SAMPLE_COUNT or
                    any(left >= right for left, right in zip(acknowledged, acknowledged[1:])) or
                    readback.get('latest') is not True or
                    readback.get('acknowledged_sequences') != acknowledged or
                    readback.get('final_source_sequence') != SAMPLE_COUNT or
                    observation.get('saved_playback', {}).get('source_sample_count') != SAMPLE_COUNT):
                raise RuntimeError('latest playback receipt subsequence differs')
            expected_receipts = len(acknowledged)
        if (observation.get('client_motion_receipts_confirmed') is not True
                or observation.get('client_worker_stop_confirmed') is not True
                or type(receipts) is not list or len(receipts) != expected_receipts
                or (pair_delivery and (observation.get('pair_delivery') is not True or
                    observation.get('pair_sample_velocity_present') is not True))
                or any(type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in receipts)):
            raise RuntimeError('expected client receipts and worker stop required')
        if saved_playback and (type(observation.get('playback_readback')) is not dict or
                observation['playback_readback'].get('native_position_changed') is not True or
                observation['playback_readback'].get('source_samples') != expected_receipts):
            raise RuntimeError('paired native playback readback required')
    (verify if verify_after is None else verify_after)()
    active_clients.remove(active)
    client_result = active.finish()
    if sector_intermediate_result is not None:
        sector = client_result['client_sector_observation']
        # Publish only the already finalized, explicitly public fields; the
        # enclosing run and its departure save have not yet succeeded.
        sector_intermediate_result['client_sector_intermediate'] = {
            'stage': 'client_finalized_before_departure_save',
            'sector_macro': sector['sector_macro'],
            'participant_id': sector['participant_id'],
            'motion_sample_sha256': sector['motion_sample_sha256'],
            'point_in_time_only': sector['point_in_time_only'],
            'cross_process_sector_identity_proven': sector['cross_process_sector_identity_proven'],
        }
    return observation, client_result


def run_source_probe_with_progress(result, probe, *, include_guard_callsites=False, **kwargs):
    """Retain non-secret progress even when the diagnostic raises."""
    import authority_recovered_source_probe
    if type(result) is not dict or not callable(probe):
        raise ValueError('source progress requires a result and probe')
    diagnostics = authority_recovered_source_probe.SourceProbeDiagnostics(
        include_guard_callsites=include_guard_callsites)
    try:
        return probe(diagnostics=diagnostics, **kwargs)
    finally:
        result['source_diagnostic_progress'] = diagnostics.snapshot()


def redact_recovery_config(text):
    redacted,count=re.subn(r'authority_control_lease="[0-9a-f]{32}"',
        'authority_control_lease="redacted"',text)
    if count!=1:raise ValueError('invalid lease')
    if 'authority_company_pose_lease=' in redacted:
        redacted, count = re.subn(r'authority_company_pose_lease="[0-9a-f]{32}"',
                                  'authority_company_pose_lease="redacted"', redacted)
        if count != 1:
            raise ValueError('invalid company pose lease')
    if 'authority_market_survey_lease=' in redacted:
        redacted, count = re.subn(r'authority_market_survey_lease="[0-9a-f]{32}"',
                                  'authority_market_survey_lease="redacted"', redacted)
        if count != 1:
            raise ValueError('invalid market survey lease')
    for name in ('lease','attempt','generation','nonce','saved_nonce','candidate_nonce','pair_nonce'):
        key='authority_motion_'+name+'='
        if key in redacted:
            redacted,count=re.subn(key+r'"[0-9a-f]{32}"',key+'"redacted"',redacted)
            if count!=1:raise ValueError('invalid source secret')
    for name in ('generation', 'lease', 'attempt', 'nonce'):
        key = 'fixture_sample_write_' + name + '='
        if key in redacted:
            redacted, count = re.subn(key + r'"[0-9a-f]{32}"', key + '"redacted"', redacted)
            if count != 1: raise ValueError('invalid fixture write secret')
    return redacted


def redact_stopped_server_config(config, remaining):
    """Never change a protected probe while an owned X4 may still use it."""
    if not config.is_file():return
    if remaining!=[]:raise RuntimeError('owned X4 stop unconfirmed; server config retained')
    config.write_text(redact_recovery_config(config.read_text(encoding='ascii')),encoding='ascii')


def verify_witness_source(store, checkpoint, guard, source_scripts, *, check_source=None):
    """Fence the pinned source and the witness scripts at every owner guard."""
    if check_source is not None:
        if guard is None or not callable(check_source) or check_source() is not True:
            raise ValueError('bounded witness source check failed')
    elif guard is None:
        # Create diagnostics start from a registry without a sealed witness.
        if store.latest_marked_confirmed()!=checkpoint or live.sha(checkpoint.path)!=checkpoint.sha256:
            raise ValueError('witness diagnostic source changed')
    elif guard.check() is not True:
        raise ValueError('witness source guard did not confirm source')
    if any(read_evidence(path)!=data for path,data in source_scripts.items()):
        raise ValueError('witness diagnostic source scripts changed')
    return True

class _WitnessFinished(Exception):
    """Private, no-listener diagnostic completed its isolated checkpoint."""


def verify_final_recovery(watch):
    """Drain the owned, closed game's log; reject duplicates or partial lines."""
    while True:
        offset = watch.offset
        if watch.poll(): break
        if watch.offset == offset: raise ValueError('incomplete recovery log')
    if watch.stage != 3: raise ValueError('recovery evidence incomplete')


def verify_final_witness(path, nonce, epoch, count, phase):
    """Re-read the closed X4 log, so late duplicate receipts fail closed."""
    import authority_recovery_registry_witness as witness
    return witness.matches_log(path, nonce, epoch, count, phase)


def confirm_witness_after_cleanup(store, checkpoint, result, remaining, errors):
    """Confirm only after the owned process and all final evidence pass.

    CheckpointStore.confirm can fail after its pointer replacement. A failed
    result must therefore never be treated as an eligible recovery source.
    """
    if result.get('success') is not True or remaining != [] or errors:
        raise ValueError('witness cleanup or final evidence incomplete')
    store.confirm(checkpoint)


def capture_first_registry_snapshot(adapter, store, verify):
    """Use the ordinary save controller once; caller confirms after cleanup."""
    adapter.pause()
    source = adapter.save()
    saved = store.publish(source, marker=adapter.pending_marker)
    adapter.checkpoint_published(saved)
    verify()
    return saved


def settle_shutdown(control, world, store, check, *, purchases=None, combat_service=None):
    """Disconnect clients and settle their last-disconnect pause/save once."""
    if combat_service is not None:
        while combat_service.native_pending:
            check()
            combat_service.settle_pending()
            if combat_service.native_pending:
                time.sleep(.01)
    if purchases is not None:
        while not purchases.before_world_transition():
            check()
            time.sleep(.05)
    control.close()
    while world.session.pending is not None:
        check()
        world.step()
    check()
    if world.session.state != 'paused' or world.session.players:
        raise RuntimeError('world did not settle paused after shutdown')
    if world.session.baseline_id is None:
        return None
    saved = store.latest_marked_confirmed()
    if world._baseline != (world.session.baseline_id, saved):
        raise ValueError('final baseline differs from confirmed checkpoint')
    return saved


def stop_owned_processes(processes, helpers_stopped, errors):
    """Return known survivors or None whenever stopping any process is uncertain."""
    if not helpers_stopped:
        return None
    remaining = []
    uncertain = False
    for proc, started in reversed(processes):
        try:
            if started is None:
                uncertain = True
                if proc.poll() is None: proc.terminate(); proc.wait(timeout=15)
                errors.append('launcher identity unavailable')
            else:
                remaining.extend(snapshot.stop_owned(proc, started))
        except BaseException as error:
            uncertain = True
            errors.append('owned cleanup: '+type(error).__name__)
    return None if uncertain else remaining


def cleanup_diagnostics(diagnostics, errors):
    """Attempt every helper cleanup even if another client's cleanup fails."""
    clean = True
    for diagnostic in reversed(diagnostics):
        try: diagnostic.cleanup_diagnostic()
        except BaseException as error:
            clean = False
            errors.append('diagnostic helper cleanup unconfirmed: '+type(error).__name__)
    return clean


def finish_recovery_lock(lockbox, remaining, services_stopped, errors):
    """Release the created lock only after processes and file services stop."""
    if not lockbox:
        return
    lock_fd, lock_stat = lockbox[0]
    if remaining is None or remaining or not services_stopped:
        errors.append('lock retained: owned process or service stop unconfirmed')
        try: os.close(lock_fd)
        except BaseException as error: errors.append('lock descriptor: '+type(error).__name__)
    else:
        try: live.release_lock_target(snapshot.LOCK_TARGET,lock_fd,lock_stat)
        except BaseException as error: errors.append('lock cleanup: '+type(error).__name__)



class RecoveredGuardedParticipants(ExperimentalTCPParticipants):
    """Fence every recovered-server participant exchange with its owner guard."""

    def __init__(self, host, *, enabled, timeout, guard_process,
                 retire_disconnected_on_resume=False, retire_disconnected_on_pause=False):
        if not callable(guard_process):
            raise ValueError('participant process guard required')
        super().__init__(host, enabled=enabled, timeout=timeout,
                         retire_disconnected_on_resume=retire_disconnected_on_resume,
                         retire_disconnected_on_pause=retire_disconnected_on_pause)
        self._guard_process = guard_process

    def check_process(self):
        try:
            self._guard_process()
        except Exception as error:
            raise ParticipantGuardFailure('recovered X4 process guard failed') from error
        return True

    def _exchange(self, conn, peer, others, raw, expected, deadline, check=None, validate_answer=None,
                  *, retire=None, retire_on_broken_pipe=False):
        def check_exchange():
            self.check_process()
            if check is not None:
                check()
        return super()._exchange(conn, peer, others, raw, expected, deadline,
                                 check=check_exchange, validate_answer=validate_answer, retire=retire,
                                 retire_on_broken_pipe=retire_on_broken_pipe)

    def _quiet(self, peers, deadline, *, retire=None):
        self.check_process()
        result = super()._quiet(peers, deadline, retire=retire)
        self.check_process()
        return result


def serve(listener, host, *, deadline, check, should_stop=lambda: False, tick_interval=.1,
          motion_service=None, pose_trial=None, purchases=None, npc_source=None, combat_service=None):
    """Accept and pump local peers until deadline, including while world is empty.

    Returns state observations; exceptions fence the caller's entire session.
    The caller owns the listener, host and all cleanup. Timeouts only end this
    development process; they do not assert that a running world was saved.
    """
    if listener.family != socket.AF_INET or listener.getsockname()[0] != '127.0.0.1':
        raise ValueError('loopback listener required')
    if (not callable(check) or not callable(should_stop) or not 0 < tick_interval <= 1
            or (deadline is not None and not math.isfinite(deadline))):
        raise ValueError('invalid supervisor configuration')
    listener.setblocking(False)
    accepted = 0
    admitted = 0
    admitted_handles = set()
    last_pose_request = None
    next_pose_wake = 0.0
    while (deadline is None or time.monotonic() < deadline) and not should_stop():
        check()
        # Limit accepts per tick so connection attempts cannot starve X4 actions.
        for _ in range(8):
            try: conn, address = listener.accept()
            except BlockingIOError: break
            try:
                if address[0] != '127.0.0.1':
                    raise ValueError('non-loopback peer')
                host.adopt(conn, defer_attach=True)
                accepted += 1
            except ValueError:
                conn.close()
            except BaseException:
                conn.close()
                raise
        channel = (pose_trial.channel if pose_trial is not None and motion_service is not None
                   and motion_service.running else None)
        pending = channel.pending if channel is not None else None
        now = time.monotonic()
        defer_world = purchases is not None or combat_service is not None
        tick_options = {'defer_world': True} if defer_world else {}
        if pending is not None and (pending is not last_pose_request or now >= next_pose_wake):
            # One early wake per request per control interval. Repeated HELLOs
            # cannot spin the loop, and a new native command can wake immediately.
            last_pose_request = pending
            next_pose_wake = now + tick_interval
            tick_options['wake_reader'] = channel.socket
        # Defer any pause requested during socket polling until the in-flight booking is checked.
        host.tick(tick_interval, **tick_options)
        if defer_world:
            session = host._world.session
            if session.pending is not None:
                if combat_service is not None:
                    combat_service.settle_pending()
                trade_ready = purchases is None or purchases.before_world_transition()
                combat_ready = combat_service is None or not combat_service.native_pending
                if trade_ready and combat_ready:
                    host.tick(0)
            if purchases is not None and session.pending is None and session.state == 'running':
                purchases.step()
        if motion_service is not None:
            if npc_source is not None:
                npc_source.poll()
            motion_service.poll()
            if pose_trial is not None and motion_service.running:
                pose_trial.poll(motion_service)
        if combat_service is not None and host._world.session.pending is None:
            combat_service.poll()
        current_handles = {peer['handle'] for peer in host._control._peers.values() if peer['stage'] == 'admitted'}
        admitted += len(current_handles - admitted_handles)
        admitted_handles = current_handles
        # poll() returns immediately without peers: wait for a new connection.
        if not host._control._peers:
            import select
            wait = tick_interval if deadline is None else min(tick_interval, max(0, deadline - time.monotonic()))
            select.select([listener], [], [], wait)
    check()
    return {'accepted_connections': accepted, 'admitted_connections': admitted}





def verify_diagnostic_context(*, verify_server, active_clients, check_process, check_cancel):
    """Hash server and clients before and after each participant check.

    Each client's active check owns only its own protected inputs. Callbacks
    must raise on failure; this function never retries or resumes an operation.
    """
    check_cancel()
    verify_server()
    for active in tuple(active_clients):
        active.participant.check_active(server_checked=True)
        verify_server()
    check_process()
    check_cancel()

def validate_overlap_identities(first, second):
    """Correlate the three independently tracked Linux X4 process identities."""
    identities = []
    for result in (first, second):
        pairs = []
        for role in ('server', 'client'):
            pair = (result.get(role+'_x4_pid'), result.get(role+'_x4_start_ticks'))
            if any(type(value) is not int or value <= 0 for value in pair):
                raise ValueError('invalid diagnostic X4 identity')
            pairs.append(pair)
        identities.append(pairs)
    if (identities[0][0] != identities[1][0] or
            len({identities[0][0], identities[0][1], identities[1][1]}) != 3):
        raise ValueError('diagnostic X4 identities are not three distinct processes')

def fixture_player_source_sectors(value, *, fixture_mode):
    """Only the explicit two-client fixture may request local player sources."""
    if value is None:
        return None
    if (fixture_mode is not True or type(value) is not list or len(value) != 2 or
            any(type(sector) is not str or not re.fullmatch(r'[A-Za-z0-9_]{1,128}', sector)
                for sector in value) or len(set(value)) != 2):
        raise ValueError('player source requires two distinct fixture sectors')
    return tuple(value)


def open_fixture_sampling_window(window, *, active_clients, selected_clients, directory_names,
                                 out, server_save, server_digest):
    """Pin exactly the currently owned private saves for one sampling phase."""
    if (tuple(active_clients) != tuple(selected_clients) or len(selected_clients) != 2 or
            tuple(directory_names) not in (('overlap-01', 'overlap-02'), ('overlap-02', 'overlap-rejoin'))):
        raise ValueError('fixture sampling clients changed')
    expected_paths = [out/name/'client/case/home/.config/EgoSoft/X4/45841/save/save_009.xml.gz'
                      for name in directory_names]
    if any(active.save_target != path for active, path in zip(selected_clients, expected_paths)):
        raise ValueError('fixture client save path differs from owned private profile')
    entries = [(server_save, server_digest)] + [
        (active.save_target, active.save_digest) for active in selected_clients]
    window.open(entries)
    if (tuple(active_clients) != tuple(selected_clients) or
            any(active.save_target != path for active, path in zip(selected_clients, expected_paths)) or
            entries != [(server_save, server_digest)] + [
                (active.save_target, active.save_digest) for active in selected_clients]):
        raise ValueError('fixture sampling context changed while pinning')


def open_first_fixture_sampling_window(window, *, active_clients, active, out, server_save, server_digest):
    """Pin the server and first client's exact saves before the second join."""
    expected_path = out/'overlap-01/client/case/home/.config/EgoSoft/X4/45841/save/save_009.xml.gz'
    if tuple(active_clients) != (active,) or active.save_target != expected_path:
        raise ValueError('first fixture measurement client or save path changed')
    entries = [(server_save, server_digest), (active.save_target, active.save_digest)]
    window.open(entries, expected_count=2)
    if (tuple(active_clients) != (active,) or active.save_target != expected_path or
            entries != [(server_save, server_digest), (active.save_target, active.save_digest)]):
        raise ValueError('first fixture measurement saves changed while pinning')


def run_overlapping_diagnostics(*, start, active_clients, world, host, control, deadline, verify, clock=None,
                                marker_script='X4AuthorityCheckpointMarker', observe_players=None,
                                measure_first=None):
    """Own two overlapping diagnostics and settle their ordered departures."""
    import authority_recovered_server_client as client
    if active_clients: raise ValueError('diagnostics must start with an empty active set')
    if measure_first is not None and (not callable(measure_first) or clock is None):
        raise ValueError('measurement requires an owner callback and the server clock')
    for index in range(2):
        active = start(index)
        active_clients.append(active)
        verify()
        if index == 0 and measure_first is not None:
            first_peer = active.participant.server_tcp
            first_players = tuple(world.session.players)
            if (world.session.state != 'running' or world.session.pending is not None or
                    len(first_players) != 1 or tuple(world.session.ready_players) != first_players or
                    first_peer not in control._peers):
                raise RuntimeError('first diagnostic participant is not running')
            def check_first_client():
                verify()
                if (len(active_clients) != 1 or active_clients[0] is not active or
                        world.session.state != 'running' or world.session.pending is not None or
                        tuple(world.session.players) != first_players or
                        tuple(world.session.ready_players) != first_players or
                        first_peer not in control._peers):
                    raise RuntimeError('first participant changed during process measurement')
            def tick_first_client():
                host.tick(min(.1, client.recovery.marked.remaining(deadline)))
            check_first_client()
            measurement = measure_first(active, clock, tick_first_client, check_first_client)
            check_first_client()
    if (world.session.state != 'running' or len(world.session.ready_players) != 2 or
            len(world.session.players) != 2):
        raise RuntimeError('two active diagnostic participants not confirmed')
    player_observation = None
    if observe_players is not None:
        if not callable(observe_players): raise ValueError('invalid player observer')
        player_observation = observe_players(tuple(active_clients))
        verify()
    # The remaining participant is guarded during each intentional departure.
    first = active_clients.pop(0)
    first_result = first.finish()
    first_peer = first.participant.server_tcp
    client.recovery.tcp.await_eof(first_peer, deadline, verify)
    while first_peer in control._peers:
        verify(); host.tick(min(.1, client.recovery.marked.remaining(deadline)))
    if (world.session.state != 'running' or len(world.session.players) != 1 or
            len(world.session.ready_players) != 1):
        raise RuntimeError('remaining diagnostic participant did not stay running')
    clock_result = None
    if clock is not None:
        remaining = tuple(world.session.ready_players)
        def check_remaining():
            verify()
            if (world.session.state != 'running' or world.session.pending is not None or
                    tuple(world.session.ready_players) != remaining or len(world.session.players) != 1 or
                    first_peer in control._peers):
                raise RuntimeError('remaining participant changed during clock observation')
        def tick(): host.tick(min(.1, client.recovery.marked.remaining(deadline)))
        clock_result = clock.observe(deadline, tick, check_remaining)
    second = active_clients.pop(0)
    second_result = second.finish()
    baseline = client.settle_departure(host, world, control, second.participant.server_tcp,
        deadline, verify, second_result['client_ticket'], second_result['client_loaded_marker'],
        marker_script=marker_script)
    second_result.update(departure_checkpoint_id=baseline.checkpoint_id,
                         departure_checkpoint_sha256=baseline.sha256,
                         departure_checkpoint_marker=baseline.marker)
    validate_overlap_identities(first_result, second_result)
    result = {'diagnostic_overlap_confirmed':True, 'diagnostic_remaining_session_running':True,
              'diagnostic_overlap_clients':[first_result, second_result]}
    if clock_result is not None: result['diagnostic_remaining_server_clock'] = clock_result
    if player_observation is not None: result['fixture_player_source_diagnostic'] = player_observation
    if measure_first is not None:
        result['diagnostic_process_measurement'] = measurement
    return result

def diagnostic_process_measure_seconds(args):
    duration = getattr(args, 'diagnostic_process_measure_seconds', None)
    if (duration is not None and
            (type(duration) not in (int, float) or not math.isfinite(duration) or
             not 5 <= duration <= 300 or getattr(args, 'diagnostic_overlap', False) is not True)):
        raise ValueError('process measurement requires overlap and a duration of 5 to 300 seconds')
    return duration


def run(args):
    require_snapshot = registry_snapshots.validate_mode(args)
    require_company_ship = getattr(args, 'require_company_ship', False)
    if type(require_company_ship) is not bool or (require_company_ship and not require_snapshot):
        raise ValueError('company ship admission requires a format-3 checkpoint')
    tls = gateway_settings(args)
    company_motion = getattr(args, 'company_motion', False)
    if type(company_motion) is not bool or (company_motion and (not require_company_ship or tls is None)):
        raise ValueError('company movement requires company-ship admission and the pinned TLS gateway')
    company_combat = getattr(args, 'company_combat', False)
    if type(company_combat) is not bool or (company_combat and not company_motion):
        raise ValueError('Company gun combat requires authenticated Company movement admission')
    npc_args = getattr(args, 'observe_registered_npc', None)
    npc_measurement = getattr(args, 'trial_registered_npc_measurement', False)
    if type(npc_measurement) is not bool or (npc_measurement and tuple(npc_args or ()) != ('943', 'MFQ-419')):
        raise ValueError('registered NPC measurement requires observation of index 943 MFQ-419')
    npc_target = npc_source = None
    if npc_args is not None:
        if not company_motion or getattr(args, 'base_game_only', False) is not True:
            raise ValueError('registered NPC observation requires pinned Company motion and the base game')
        from authority_registered_npc_source import NpcCheckpointTarget, RegisteredNpcSource

        npc_target = NpcCheckpointTarget(*npc_args)
    company_purchases = getattr(args, 'company_purchases', False)
    if type(company_purchases) is not bool or (company_purchases and (not require_company_ship or tls is None)):
        raise ValueError('company purchases require company-ship admission and the pinned TLS gateway')
    company_fleet_orders = getattr(args, 'company_fleet_orders', False)
    company_sales = getattr(args, 'company_sales', False)
    if type(company_sales) is not bool or (company_sales and not company_purchases):
        raise ValueError('Company sales require authenticated Company purchases and the market view')
    if type(company_fleet_orders) is not bool or (company_fleet_orders and not company_purchases):
        raise ValueError('Company fleet orders require Company purchases')
    company_undock = getattr(args, 'company_undock_test', False)
    if type(company_undock) is not bool or (company_undock and not company_purchases):
        raise ValueError('company undock test requires Company purchases')
    parking = getattr(args, 'park_server_avatar', None)
    trade_mode = getattr(args, 'company_trade_trial', None)
    cockpit_capture = getattr(args, 'company_cockpit_purchase', None)
    shuttle_mode = getattr(args, 'company_shuttle_pair', None)
    if shuttle_mode is not None:
        if (shuttle_mode not in ('prepare', 'verify') or not require_snapshot or parking is not None
                or tls is not None or require_company_ship or company_motion or trade_mode or cockpit_capture
                or getattr(args, 'company_pose_trial', False) or getattr(args, 'verify_company_pose', False)):
            raise ValueError('shuttle pair preparation and verification require isolated format-3 recovery')
        import authority_company_shuttle_pair as shuttle_probe
    cockpit_purchase = None
    if cockpit_capture is not None:
        if trade_mode is not None:
            raise ValueError('select one private Company trade experiment')
        trade_mode = 'six-unit'
    if trade_mode:
        if (trade_mode not in ('buy', 'six-unit', 'inspect') or not require_snapshot or parking is not None
                or require_company_ship or company_motion or tls is not None
                or getattr(args, 'company_pose_trial', False) or getattr(args, 'verify_company_pose', False)):
            raise ValueError('company trade trial requires an isolated recovered provider')
        import authority_company_trade_trial as trade_probe
        if cockpit_capture is None:
            trade_nonce = secrets.token_hex(16)
        else:
            from authority_cockpit_purchase import load_cockpit_capture, cockpit_receipt
            cockpit_purchase, trade_nonce, capture_sha256 = load_cockpit_capture(cockpit_capture)
    if parking is not None:
        import authority_station_parking as parking_probe
        parking_probe.mode(args, require_snapshot=require_snapshot, tls=tls,
                           company_ships=require_company_ship, company_motion=company_motion)
    pose_live = getattr(args, 'company_pose_trial', False)
    pose_cold = getattr(args, 'verify_company_pose', False)
    if pose_live or pose_cold:
        if (not require_snapshot or parking is not None or (pose_live and pose_cold)
                or (pose_live and not company_motion)
                or (pose_cold and (company_motion or require_company_ship or tls is not None))):
            raise ValueError('incompatible settings for the isolated company pose trial')
        import authority_company_pose_overlay as pose_overlay
        import authority_company_pose_trial as company_pose
    pose_channel = pose_trial = None
    motion_service = None
    gun_damage_provider = combat_service = None
    purchase_provider = undock_provider = market_provider = fleet_provider = purchases = purchase_ledger = None
    bootstrap_snapshot = getattr(args, 'bootstrap_registry_snapshot', False)
    software_icd = getattr(args, 'software_vulkan_icd', None)
    # Software rendering can slow the full paused registry pass. Extend only
    # that pass and the two deadlines which enclose it before native SAVE.
    software_snapshot = software_icd is not None and (require_snapshot or bootstrap_snapshot)
    snapshot_timeout = 300 if software_snapshot else 110
    marker_timeout = 330 if software_snapshot else None
    save_timeout = 480 if software_snapshot else None
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('unprivileged Linux user required')
    out = snapshot.output_path(args.out)
    source_store = CheckpointStore(load.local_directory(args.store))
    if out.resolve().is_relative_to(source_store.directory.parent) or source_store.directory.parent.is_relative_to(out.resolve()):
        raise ValueError('output and source must be separate')
    game = args.game.resolve(strict=True)
    if live.sha(game/'X4') != live.EXE_SHA or not (game/'extensions').is_dir() or not shutil.which('bwrap'):
        raise ValueError('pinned X4 or bubblewrap unavailable')
    if live.x4_executables(): raise ValueError('another X4 process is running')
    if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', args.display): raise ValueError('local X11 display required')
    auth = Path(os.environ.get('XAUTHORITY', ''))
    if not auth.is_file() or not os.access(auth, os.R_OK): raise ValueError('readable XAUTHORITY required')
    auth = auth.resolve(strict=True)
    if (not math.isfinite(args.timeout) or not 0 < args.timeout <= 86400 or
            type(args.seed) is not int or not 0 <= args.seed <= 0xffffffff):
        raise ValueError('invalid timeout or seed')
    user = pwd.getpwuid(os.geteuid()); home = Path(user.pw_dir)
    steam = live.steam_identity(home, user.pw_uid)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    operations=[]
    result = {'supervisor_version':1, 'success':False, 'scope':'isolated recovered Linux X4, loopback only; not gameplay or Internet',
              'windows_tested':False, 'internet_tested':False, 'exact_loaded_file_proven':False,
              'engine_operations':operations}
    processes=[]; handlers={}; lockbox=[]; services=[]; sock=listener=control=clock_sock=source_sock=sample_write_sock=None
    endpoint=out/'endpoint.json'; deadline=time.monotonic()+args.timeout
    gateway = None
    if tls is not None:
        result['scope'] = 'isolated recovered Linux X4 with pinned TLS gateway; gameplay and Internet unverified'
    watch=target=checkpoint=expected=profile=None
    local_pair=False;source_index=None
    witness_pending=None
    diagnostics = []
    active_clients = []
    guard_context = []
    fixture_save_guards = None
    playback_window = None
    try:
        measure_seconds = diagnostic_process_measure_seconds(args)
        fixture_mode = getattr(args, 'fixture_overlap', False)
        if type(fixture_mode) is not bool or (fixture_mode and (
                getattr(args, 'diagnostic_overlap', False) is not True or
                getattr(args, 'registry_status', False) or getattr(args, 'registry_enroll', False) or
                getattr(args, 'registry_witness', None) is not None or
                getattr(args, 'registry_witness_join', False) or
                getattr(args, 'diagnostic_single_owner', False))):
            raise ValueError('fixture overlap requires the dedicated two-client diagnostic')
        player_source_sectors = fixture_player_source_sectors(
            getattr(args, 'fixture_player_source_sectors', None), fixture_mode=fixture_mode)
        quick_fixture_guards = getattr(args, 'fixture_player_source_quick_guards', False)
        if type(quick_fixture_guards) is not bool or (quick_fixture_guards and player_source_sectors is None):
            raise ValueError('quick guards require the private fixture player-source diagnostic')
        latest_fixture_source = getattr(args, 'fixture_player_source_latest_cache', False)
        if type(latest_fixture_source) is not bool or (latest_fixture_source and player_source_sectors is None):
            raise ValueError('latest cache requires the private fixture player-source diagnostic')
        freeze_fixture_source = getattr(args, 'fixture_player_source_freeze_drain', False)
        if type(freeze_fixture_source) is not bool or (freeze_fixture_source and not latest_fixture_source):
            raise ValueError('freeze drain requires the private fixture latest-cache source')
        fixture_rejoin = getattr(args, 'fixture_player_source_rejoin', False)
        if (type(fixture_rejoin) is not bool or
                (fixture_rejoin and not freeze_fixture_source)):
            raise ValueError('fixture rejoin requires freeze drain')
        sample_write_slot = getattr(args, 'fixture_player_sample_write_slot', None)
        sample_write_after_throttle = getattr(args, 'fixture_player_sample_write_after_throttle', False)
        if (type(sample_write_after_throttle) is not bool or
                (sample_write_after_throttle and (sample_write_slot is None or not latest_fixture_source))):
            raise ValueError('fixture movement write requires a latest-sample source and selected slot')
        if (sample_write_slot is not None and
                (type(sample_write_slot) is not int or sample_write_slot not in (1, 2) or
                 player_source_sectors is None or fixture_rejoin)):
            raise ValueError('fixture sample write requires a non-rejoin fixture source session')
        if sample_write_slot is not None:
            original_sectors = ('cluster_33_sector001_macro', 'cluster_45_sector001_macro')
            if player_source_sectors[sample_write_slot - 1] != original_sectors[sample_write_slot - 1]:
                raise ValueError('fixture sample writer requires its original test sector')
        fixture_scripts = None
        fixture_helpers = None
        marker_script = 'X4AuthorityCheckpointMarker'
        if fixture_mode:
            import authority_fixture_overlap as fixture_helpers
            marker_script = fixture_helpers.MARKER_SCRIPT
            result.update(fixture_overlap=True, fixture_saved_ships_must_be_undocked=True,
                          fixture_registry_count=4, fixture_general_gameplay_proven=False)
        witness_join, witness_phase = witness_mode(args)
        source_index=diagnostic_source_index(args,witness_join)
        source_candidates=diagnostic_source_candidates(args,witness_join,source_index)
        local_pair=diagnostic_local_pair(args,witness_join,source_index,source_candidates)
        source_enabled=source_index is not None or source_candidates is not None
        client_motion=diagnostic_client_motion(args,source_enabled)
        pair_delivery=diagnostic_local_pair_delivery(args,local_pair,client_motion)
        saved_apply_fixture=diagnostic_saved_apply(args,witness_join,source_index,pair_delivery)
        saved_playback_fixture=diagnostic_saved_playback(args,witness_join,source_index,
            client_motion,local_pair,saved_apply_fixture)
        client_sector=diagnostic_client_sector(args,pair_delivery)
        guard_timing=diagnostic_guard_timing(args,source_enabled)
        long_motion=diagnostic_long_motion(args,witness_join,source_enabled,client_motion)
        latest_playback=diagnostic_latest_playback(args,saved_playback_fixture,long_motion)
        real_time_motion=diagnostic_real_time_motion(args,saved_playback_fixture,latest_playback)
        diagnostic_guard_cache_ms = diagnostic_guard_cache(args, source_index=source_index,
            source_candidates=source_candidates, client_motion=client_motion, local_pair=local_pair,
            saved_apply=saved_apply_fixture, client_sector=client_sector,
            saved_playback=saved_playback_fixture, latest_playback=latest_playback,
            real_time_motion=real_time_motion, long_motion=long_motion)
        owner_diagnostic = getattr(args, 'diagnostic_single_owner', False)
        if (type(owner_diagnostic) is not bool or
                (owner_diagnostic and (not getattr(args, 'diagnostic_client', False) or source_enabled or
                                       witness_phase or getattr(args, 'registry_status', False) or
                                       getattr(args, 'registry_enroll', False)))):
            raise ValueError('single-owner diagnostic requires one load-only diagnostic client')
        tls_certificate, tls_private_key = validate_diagnostic_tls(args, source_enabled)
        if quick_fixture_guards:
            if (source_enabled is not False or client_motion is not False or
                    tls_certificate is not None or tls_private_key is not None):
                raise ValueError('quick guards are limited to the private local fixture source')
            from authority_fixture_save_guards import FixtureSaveGuards
            fixture_save_guards = FixtureSaveGuards(live.sha)
            result['fixture_save_guard'] = {
                'mode': 'pinned_metadata_between_digest_boundaries',
                'exclusive_writer_and_fd_owner_assumed': True,
                'unchanged_bytes_between_boundaries_proven': False,
                'exact_loaded_file_proven': False,
                'metrics': fixture_save_guards.metrics,
            }
        case_options = {'base_game_only': True} if getattr(args, 'base_game_only', False) else {}
        if software_icd is not None:
            case_options['software_rendering'] = True
        case, profile, binds, lock = snapshot.prepare_case(out, game, home, **case_options)
        stage = out/'staged-profile'; stage.mkdir(mode=0o700)
        checkpoint, staged = stage_confirmed_recovery(source_store, stage, marker_script=marker_script)
        if fixture_mode:
            fixture_scripts, fixture_source_result_path, fixture_source_result = verify_fixture_source(
                source_store, checkpoint, profile)
            fixture = None
        elif source_store.directory == source.DEFAULT_STORE.resolve():
            if require_snapshot or bootstrap_snapshot:
                raise ValueError('registry snapshot requires a confirmed previous supervisor source')
            if witness_join: raise ValueError('witness join requires an own verified source')
            fixture = source.verify(source_store.directory, checkpoint, profile)
        else:
            fixture = verify_previous_source(
                source_store, checkpoint, profile,
                registry_status=(bootstrap_snapshot or getattr(args, 'registry_status', False)
                                 or getattr(args, 'registry_enroll', False) or bool(witness_phase)),
                witness_source=witness_phase == 'verify', require_registry_snapshot=require_snapshot,
                return_supervisor_result=bootstrap_snapshot, require_ready_registry_script=bootstrap_snapshot,
                return_purchase_reference=require_snapshot,
                **({'observe_registry': npc_target.observe} if npc_target is not None else {}))
        bootstrap_status = None
        first_capture_state = None
        if bootstrap_snapshot:
            fixture, prior_result = fixture
            import authority_recovery_registry_status as registry_status_reader
            bootstrap_status = registry_snapshots.verify_bootstrap_status(
                prior_result, checkpoint, registry_status_reader.saved_status(checkpoint))
            first_capture_state = registry_snapshots.FirstCaptureState(checkpoint, bootstrap_status)
            result['registry_snapshot'] = first_capture_state.metadata()
        registry_snapshot_state = None
        if require_snapshot:
            fixture, saved_evidence, ledger_reference = fixture
            if company_purchases or ledger_reference is not None:
                purchase_ledger = CompanyPurchaseLedger(saved_evidence['epoch'], out / 'company-purchases.sqlite3')
                if ledger_reference is not None:
                    purchase_ledger.restore(checkpoint, ledger_reference)
            if shuttle_mode is not None:
                shuttle_source = source_store.directory.parent / 'result.json'
                shuttle_provenance = read_evidence(shuttle_source, 1 << 20)
                shuttle_expected = shuttle_probe.source_expected(
                    shuttle_mode, checkpoint, json.loads(shuttle_provenance, object_pairs_hook=_pairs,
                                                         parse_constant=_reject_constant))
                shuttle_result = {'mode': shuttle_mode, 'source_sha256': checkpoint.sha256,
                                  'client_endpoint_opened': False}
                result['company_shuttle_pair'] = shuttle_result
            if trade_mode:
                if trade_mode != 'inspect' and checkpoint.sha256 != trade_probe.SOURCE_DIGEST:
                    raise ValueError('stock purchase trial requires its original private two-ship world')
                result['company_trade_trial'] = {'mode': trade_mode}
                if cockpit_purchase is not None:
                    result['company_trade_trial'].update(cockpit_purchase=cockpit_purchase,
                                                         request_id=trade_nonce, capture_sha256=capture_sha256)
            if pose_live or pose_cold:
                pose_channel = company_pose.GamePoseChannel(saved_evidence['epoch'])
                if pose_cold:
                    prior_pose = json.loads(read_evidence(source_store.directory.parent/'result.json', 1 << 20),
                                            object_pairs_hook=_pairs, parse_constant=_reject_constant)
                    pose_channel.read_company_catalogue(checkpoint)
                if pose_live:
                    result['company_pose_trial'] = {'source_sha256': checkpoint.sha256}
                    pose_trial = company_pose.CompanyPoseTrial(pose_channel, result['company_pose_trial'])
            registry_snapshot_state = registry_snapshots.RegistrySnapshotState(checkpoint, saved_evidence)
            result['registry_snapshot'] = registry_snapshot_state.metadata()
            if parking is not None:
                parking_source_result = json.loads(read_evidence(source_store.directory.parent/'result.json', 1 << 20))
                parking_expected = parking_probe.source_location(parking, checkpoint, parking_source_result)
                parking_inventory = parking_probe.verify_saved(
                    checkpoint, parking_expected, include_sites=parking == 'prepare')
                result['station_parking'] = {'mode': parking, 'source_sha256': checkpoint.sha256,
                                             'inventory': parking_inventory, 'client_endpoint_opened': False}
        target = profile/'save/save_008.xml.gz'
        if any(target.parent.iterdir()): raise ValueError('new profile save directory not empty')
        os.link(staged, target, follow_symlinks=False); target.chmod(0o400)
        if live.sha(target) != checkpoint.sha256: raise ValueError('recovery save changed')
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0)); sock.settimeout(.25)
        lease, epoch = secrets.token_hex(16), secrets.token_hex(16)
        protected = (fixture_helpers.install_server(case, profile, fixture_scripts, sock.getsockname()[1], lease, epoch)
                     if fixture_mode else bridge.install_probe(case, profile, sock.getsockname()[1], lease, epoch,
                                                               early_pause=require_snapshot, marker_timeout=marker_timeout))
        if require_snapshot:
            protected.extend(registry_snapshot_state.install(case, reserved_tokens=(lease, epoch),
                                                             read_source=read_evidence, timeout=snapshot_timeout))
            if company_purchases:
                from authority_native_purchase import NativePurchaseProvider
                purchase_provider = NativePurchaseProvider()
                protected.extend(purchase_provider.install(case))
            if company_undock:
                from authority_native_undock import NativeUndockProvider
                undock_provider = NativeUndockProvider()
                protected.extend(undock_provider.install(case))
            if trade_mode:
                protected.extend(trade_probe.install(case, trade_mode, saved_evidence['epoch'], trade_nonce,
                                                     cockpit_purchase=cockpit_purchase))
            if pose_channel is not None:
                protected.extend(pose_overlay.install(case, profile, port=pose_channel.port,
                                                      lease=pose_channel.lease, epoch=pose_channel.epoch,
                                                      writable=pose_live))
            if shuttle_mode is not None:
                shuttle_nonce = secrets.token_hex(16)
                protected.extend(shuttle_probe.install(case, shuttle_nonce, shuttle_mode, shuttle_expected))
            if parking is not None:
                parking_nonce = secrets.token_hex(16)
                protected.extend(parking_probe.install(case, parking_nonce, parking, parking_expected,
                    fallback_sites=parking_inventory.get('fallback_sites', ())))
        if bootstrap_snapshot:
            protected.extend(first_capture_state.install(case, reserved_tokens=(lease, epoch),
                                                         read_source=read_evidence, timeout=snapshot_timeout))
        sample_writer = None
        if sample_write_slot is not None:
            import authority_fixture_player_sample_install as sample_install
            sample_write_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sample_write_sock.bind(('127.0.0.1', 0))
            registry_epoch = fixture_helpers.fixture_source.fixture_save.base.archive.REGISTRY_EPOCH
            reserved = {lease, epoch, registry_epoch}
            def fresh_sample_token():
                value = secrets.token_hex(16)
                while value in reserved:
                    value = secrets.token_hex(16)
                reserved.add(value)
                return value
            sample_writer = {'epoch': registry_epoch, 'generation': fresh_sample_token(),
                             'lease': fresh_sample_token(), 'attempt': fresh_sample_token(),
                             'nonce': fresh_sample_token()}
            protected.extend(sample_install.install(case, profile, slot=sample_write_slot,
                port=sample_write_sock.getsockname()[1], **sample_writer))
            result['fixture_sample_write'] = {'scope': 'one experimental server-local XYZ write',
                'source_to_server_frame_assumed': True, 'same_run_frame_proven': False,
                'windows_frame_proven': False, 'native_velocity_proven': False,
                'rotation_from_sample_applied': False, 'client_movement_proven': False, 'gameplay_proven': False,
                'dispatch_attempted': False, 'immediate_readback_confirmed': False}
        registry_check = None
        enrollment = None
        if getattr(args,'registry_status',False) or getattr(args,'registry_enroll',False) or witness_phase:
            import authority_recovery_registry_status as registry_check
            registry_expected=registry_check.saved_status(checkpoint)
            if saved_playback_fixture:
                from authority_native_client_playback import STATUS
                if registry_expected != STATUS:
                    raise ValueError('saved playback requires the pinned recovery registry')
            if saved_apply_fixture is not None and (registry_expected[0] != 'ready' or
                    registry_expected[1] != '4ba7be438ebb04ec5bb85fdb95b5a488'):
                raise ValueError('saved apply registry epoch differs from pinned fixture')
            if registry_expected[0]=='ready':
                old_registry=source_store.directory.parent/'case/extensions/x4_lan_lab/md/authority_ship_registry.xml'
                if read_evidence(old_registry)!=read_evidence(ROOT/'tools/dev/authority_ship_registry.xml'):
                    raise ValueError('saved registry script provenance unavailable')
            registry_nonce=secrets.token_hex(16)
            if getattr(args,'registry_enroll',False):
                if registry_expected!=('uninitialized',):raise ValueError('refusing to initialize existing registry')
                enrollment={'epoch':secrets.token_hex(16),'nonce':secrets.token_hex(16),
                    'limit':200000,'save_path':str(profile/'save')}
            protected.extend(registry_check.install(case,registry_nonce,registry_expected,enroll=enrollment))
        witness_check = None
        witness_source_guard = None
        if witness_phase:
            if registry_expected[0]!='ready':raise ValueError('witness requires a populated registry')
            import authority_recovery_registry_witness as witness_check
            import authority_registry_witness_save_evidence as witness_save
            if witness_phase=='verify':
                witness_save.verify(checkpoint,registry_expected[1],registry_expected[2])
            source_scripts=[source_store.directory.parent/'case/extensions/x4_lan_lab/md/authority_ship_registry.xml']
            if witness_phase=='verify':
                source_scripts.append(source_store.directory.parent/'case/extensions/x4_lan_lab/md/authority_registry_witness.xml')
            witness_source_scripts={p:read_evidence(p) for p in source_scripts}
            if witness_phase=='verify':
                import authority_witness_source_guard
                witness_source_guard=authority_witness_source_guard.WitnessSourceGuard(source_store,checkpoint)
                if witness_source_guard.status!=registry_expected:
                    raise ValueError('witness source status changed during initialization')
            verify_witness_source(source_store,checkpoint,witness_source_guard,witness_source_scripts)
            witness_nonce=secrets.token_hex(16)
            protected.extend(witness_check.install(case,witness_nonce,registry_expected[1],registry_expected[2],witness_phase,allow_resume=witness_join))
        if saved_playback_fixture:
            from authority_playback_sampling_window import PlaybackSamplingWindow
            playback_window = PlaybackSamplingWindow(live.sha, witness_source_guard)
            result['paired_playback'] = {'fixture': saved_playback_fixture,
                'save_guard_mode': 'metadata_between_digest_boundaries',
                'exclusive_writer_and_fd_owner_assumed': True,
                'unchanged_bytes_between_boundaries_proven': False,
                'native_velocity_proven': False, 'windows_tested': False,
                'save_guard_metrics': playback_window.metrics}
        source_settings=None
        if source_enabled:
            if any(index>registry_expected[2] for index in (source_candidates or (source_index,))):
                raise ValueError('diagnostic index exceeds confirmed registry')
            import authority_recovered_source_motion_install
            source_sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            source_sock.bind(('127.0.0.1',0))
            reserved={lease,epoch,registry_expected[1],registry_nonce,witness_nonce}
            def fresh():
                value=secrets.token_hex(16)
                while value in reserved or value[:16] in {v[:16] for v in reserved}:
                    value=secrets.token_hex(16)
                reserved.add(value);return value
            source_settings={'port':source_sock.getsockname()[1],'lease':fresh(),'attempt':fresh(),
                'generation':fresh(),'nonce':fresh(),'saved_nonce':fresh(),'timeout':120}
            if real_time_motion:
                source_settings['real_time_motion'] = True
            if source_candidates is not None:
                source_settings['selection']={'nonce':fresh(),'candidates':source_candidates}
            if local_pair:
                source_settings['local_pair']={'nonce':fresh(),'index':source_index}
                if pair_delivery:
                    source_settings['local_pair']['delivery']=True
            protected.extend(authority_recovered_source_motion_install.install(case,profile,registry_expected,source_settings))
        clock = None
        if getattr(args, 'diagnostic_overlap', False):
            import authority_overlap_clock
            clock_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            clock_sock.bind(('127.0.0.1', 0))
            protected.append(authority_overlap_clock.install(case, clock_sock.getsockname()[1]))
            clock = authority_overlap_clock.OverlapClock(clock_sock, lease)
        if not fixture_mode:
            protected.append(bridge.install_saved_identity_fixture(case, fixture))
        if company_purchases:
            from authority_native_market_survey import NativeMarketSurveyProvider
            market_provider = NativeMarketSurveyProvider()
            protected.extend(market_provider.install(case))
        if company_fleet_orders:
            from authority_native_fleet_order import NativeFleetOrderProvider

            fleet_provider = NativeFleetOrderProvider()
            protected.extend(fleet_provider.install(case))
        if company_combat:
            from authority_company_gun_damage import NativeGunDamageProvider

            def combat_world_running():
                return motion_service is not None and world.session.state == 'running' and world.session.pending is None

            def combat_grant_current(binding):
                return combat_world_running() and motion_service.combat_grant_current(binding)

            gun_damage_provider = NativeGunDamageProvider(
                combat_grant_current, can_fire=lambda binding: motion_service.combat_helm_current(binding))
            protected.extend(gun_damage_provider.install(case))
        if npc_target is not None:
            npc_source = RegisteredNpcSource(npc_target.confirmed(saved_evidence), trial_measurement=npc_measurement)
            protected.extend(npc_source.install(case))
        protected = list(dict.fromkeys(protected))
        expected = {path:live.sha(path) for path in protected}
        store_path = out/'checkpoints'; store_path.mkdir(mode=0o700)
        store = CheckpointStore(store_path)
        bridge.acquire_lock_with_handlers(handlers, lockbox, user.pw_uid, user.pw_gid)
        stop = [False]
        def request_stop(_number, _frame): stop[0] = True
        for number in handlers: signal.signal(number, request_stop)
        command = load.readonly_save_command(case,game,binds,lock,args.display,auth,user.pw_name,args.seed,target)
        pos = command.index('--die-with-parent')
        for path in protected:
            command[pos:pos] = ['--ro-bind',str(path),str(path)]; pos += 3
        if software_icd is not None:
            import authority_software_vulkan as software_vulkan
            command = software_vulkan.constrain(command, display=args.display, authorization=auth,
                                                icd=software_icd,
                                                display_hostname=getattr(args, 'display_hostname', None))
            software_vulkan.verify(command, out/'software-vulkan.log')
        proc, started = owned.spawn_owned_gated(command, out/'process.log', processes)
        identity = None
        watch = recovery.RecoveryWatch(profile/'engine-output.log', checkpoint.marker)
        witness_errors = witness_check.ErrorWatch(profile/'engine-output.log') if witness_check else None
        serving = [False]; recovered = [False]
        def check():
            nonlocal identity
            if stop[0] and not serving[0]: raise InterruptedError('startup cancelled')
            if proc.poll() is not None: raise RuntimeError('owned launcher exited')
            if witness_errors is not None: witness_errors.poll()
            actual = owned.client.game_identity(proc, started, game)
            if actual is not None:
                if identity is not None and actual != identity: raise RuntimeError('owned X4 changed')
                identity = actual
            elif identity is not None: raise RuntimeError('owned X4 disappeared')
            if live.steam_identity(home,user.pw_uid) != steam: raise RuntimeError('Steam identity changed')
            if watch.stage and identity is None: raise RuntimeError('recovery receipt without owned X4')
            if recovered[0]:
                watch.poll()
                if watch.stage != 3: raise ValueError('recovery evidence changed')
        def verify():
            check()
            if identity is None: raise RuntimeError('recovery receipt without owned X4')
            if playback_window is not None:
                save_valid = playback_window.check_save(target, checkpoint.sha256)
            else:
                save_valid = (live.sha(target) == checkpoint.sha256 if fixture_save_guards is None else
                              fixture_save_guards.check(target, checkpoint.sha256))
            if not save_valid or any(live.sha(p) != h for p,h in expected.items()):
                raise ValueError('recovery save or protected configuration changed')
            if witness_check:
                verify_witness_source(source_store,checkpoint,witness_source_guard,witness_source_scripts,
                    **({'check_source': playback_window.check_source} if playback_window is not None else {}))
            check()
        peer = bridge.activate(sock, watch, lease, epoch, deadline, check, verify)
        if require_snapshot:
            registry_snapshot_state.verify_initial(profile/'engine-output.log')
            if shuttle_mode is not None:
                shuttle_log = profile / 'engine-output.log'
                shuttle_pair = shuttle_probe.verify_log(shuttle_log, shuttle_nonce, read_evidence)
                if shuttle_mode == 'verify' and shuttle_pair != shuttle_expected:
                    raise ValueError('cold shuttle candidates differ from preparation')
                shuttle_result.update(pair=shuttle_pair, native_candidates=True)
            if parking is not None:
                parking_location = parking_probe.verify_log(profile/'engine-output.log', parking_nonce, read_evidence)
                if parking_expected is not None and parking_location != parking_expected:
                    raise ValueError('cold parking location changed')
                result['station_parking'].update(native_parked=True, location=parking_location,
                                                 both_company_seats_available=True)
            result['registry_snapshot'] = registry_snapshot_state.metadata()
        if registry_check is not None:
            registry_check.matches_log(profile/'engine-output.log',registry_nonce,'startup',registry_expected)
            if witness_check:
                witness_check.matches_log(profile/'engine-output.log',witness_nonce,registry_expected[1],registry_expected[2],witness_phase)
            if enrollment is not None:
                count=registry_check.enrollment_receipt(profile/'engine-output.log',enrollment['nonce'],enrollment['epoch'],enrollment['limit'])
                registry_expected=('bootstrap',enrollment['epoch'],count)
        recovered[0] = True
        check()
        result.update(source_checkpoint_id=checkpoint.checkpoint_id, source_sha256=checkpoint.sha256,
                      load_marker_observed=True, pause_observed=True)
        if any(provider is not None for provider in (pose_channel, undock_provider, market_provider, fleet_provider,
                                                     gun_damage_provider, npc_source)):
            game_stat = (game/'X4').stat()
            process_identity = {'pid': identity[0], 'start_ticks': identity[1],
                                'exe_dev': game_stat.st_dev, 'exe_ino': game_stat.st_ino}
        if undock_provider is not None:
            undock_provider.bind(process_identity, check)
        if market_provider is not None:
            market_provider.bind(process_identity, check)
        if fleet_provider is not None:
            fleet_provider.bind(process_identity, check)
        if gun_damage_provider is not None:
            gun_damage_provider.bind(process_identity, check)
        if npc_source is not None:
            npc_source.bind(process_identity)
        if pose_channel is not None:
            pose_channel.bind(process_identity, check)
            if pose_cold:
                result['company_pose_reload'] = company_pose.verify_reloaded(pose_channel, prior_pose, checkpoint)
                result.update(success=True, final_world_state='paused')
                raise _WitnessFinished()
        def verify_checkpoint(store_arg, checkpoint_id, save_name):
            nonlocal registry_expected
            evidence=read_cycle_evidence(store_arg,checkpoint_id,save_name)
            if fixture_mode:
                fixture_helpers.verify_checkpoint_mapping(store_arg.get(checkpoint_id))
            if bootstrap_snapshot:
                saved = store_arg.get(checkpoint_id)
                first_capture_state.verify_save(saved, profile/'engine-output.log')
                result['registry_snapshot'] = first_capture_state.metadata()
            if require_snapshot:
                saved = store_arg.get(checkpoint_id)
                appended = tuple(ship['index'] for ship in shuttle_pair['ships']) if shuttle_mode == 'prepare' else ()
                candidate = npc_source.join_candidate() if npc_source is not None else None
                observe = {'observe_registry': candidate.observe} if candidate is not None else {}
                registry_snapshot_state.verify_save(saved, profile/'engine-output.log', appended_indices=appended, **observe)
                if candidate is not None:
                    npc_source.confirm_join_checkpoint(saved, registry_snapshot_state.latest_evidence, candidate)
                result['registry_snapshot'] = registry_snapshot_state.metadata()
                if purchase_ledger is not None:
                    result['company_purchase_ledger'] = purchase_ledger.checkpoint(saved)
            if registry_check is not None:
                saved=store_arg.get(checkpoint_id)
                actual=registry_check.saved_status(saved)
                if witness_check:
                    if actual[:3]!=registry_expected[:3]:raise ValueError('witness registry epoch or count changed')
                    witness_save.verify(saved,actual[1],actual[2])
                else:
                    registry_check.checkpoint_status(registry_expected,actual)
                registry_check.matches_log(profile/'engine-output.log',registry_nonce,saved.marker,actual)
                registry_expected=actual
                if enrollment is not None:
                    result['registry_status'].update(state='ready',mapping_sha256=actual[3],checkpoint_id=checkpoint_id)
            return evidence
        adapter = LocalControlBridge(enabled=True, sock=sock, peer=peer, lease=lease, world_epoch=epoch,
            profile=profile, store=store, verify_checkpoint=verify_checkpoint, timeout=130, marker_required=True,
            marker_script=marker_script, save_timeout=save_timeout)
        if registry_check is not None:
            result['registry_status']={'source_checkpoint_id':checkpoint.checkpoint_id,
                'source_sha256':checkpoint.sha256,'state':registry_expected[0],
                'epoch':registry_expected[1] if len(registry_expected)>1 else None,
                'count':registry_expected[2] if len(registry_expected)>2 else None,
                'mapping_sha256':registry_expected[3] if len(registry_expected)>3 else None,
                'coverage_proven':False,'native_load_bytes_proven':False}
        if witness_check and not witness_join:
            # A private checkpoint solely to supply the second diagnostic process.
            # No TCP listener, client admission, or normal world session exists here.
            check()
            adapter.pause()
            saved_source=adapter.save()
            saved=store.publish(saved_source,marker=adapter.pending_marker)
            adapter.checkpoint_published(saved)
            verify()
            witness_pending=saved
            result.update(success=True,final_world_state='paused',final_checkpoint_id=saved.checkpoint_id,
                final_checkpoint_sha256=saved.sha256,
                witness_diagnostic={'phase':witness_phase,'epoch':registry_expected[1],
                    'count':registry_expected[2],'no_client_admission':True,
                    'historical_identity_proven':False,'exact_loaded_file_proven':False})
            raise _WitnessFinished()
        if trade_mode:
            log = profile / 'engine-output.log'
            trial_result = result['company_trade_trial']
            trial_result['loaded_observation'] = trade_probe.loaded_observation(log, read_evidence)
            if trade_mode != 'inspect':
                check()
                adapter.resume()
                records = trade_probe.await_records(log, trade_nonce, deadline, check)
                trial_result['records'] = records
                from authority_trade_settlement import purchase_settlement
                # Preserve the private game save even if accounting is inconclusive.
                # Only a verified purchase can enter the confirmed checkpoint store.
                adapter.pause()
                # A completed decline has no economic mutation to preserve. Retain the
                # private native save for every submission, even if the ledger disagrees.
                submitted = any(row.startswith(f'RESULT|{trade_nonce}|submitted|') for row in records)
                if cockpit_purchase is not None and not submitted:
                    receipt = cockpit_receipt(cockpit_purchase, records, trade_nonce, saved_evidence['epoch'])
                    if receipt['outcome'] != 'refused':
                        raise ValueError('native completion lacks a submission or a verified refusal')
                    trial_result['refusal_evidence'] = receipt.pop('native_evidence')
                    trial_result['cockpit_receipt'] = receipt
                    verify()
                    result.update(success=True, final_world_state='paused')
                    raise _WitnessFinished()
                raw_save = adapter.save()
                verify()
                trial_result['native_save'] = {'name': raw_save.name, 'sha256': live.sha(raw_save),
                                               'marker': adapter.pending_marker}
                if cockpit_purchase is None:
                    settlement = purchase_settlement(records, trade_nonce, saved_evidence['epoch'],
                                                     trade_mode, require_absolute_cargo=True)
                else:
                    receipt = cockpit_receipt(cockpit_purchase, records, trade_nonce, saved_evidence['epoch'])
                    if receipt['outcome'] != 'confirmed':
                        raise ValueError('submitted native order lacks a confirmed cockpit receipt')
                    settlement = receipt.pop('native_evidence')
                    trial_result['cockpit_receipt'] = receipt
                trial_result['settlement'] = settlement
                if settlement['verified'] is not True:
                    raise ValueError(f"trade accounting unverified: {settlement['reason']}")
                saved = store.publish(raw_save, marker=adapter.pending_marker)
                adapter.checkpoint_published(saved)
                verify()
                witness_pending = saved
                result.update(final_checkpoint_id=saved.checkpoint_id, final_checkpoint_sha256=saved.sha256)
            result.update(success=True, final_world_state='paused')
            raise _WitnessFinished()
        if bootstrap_snapshot:
            # The ordinary control bridge owns pause, marker receipt, native save,
            # title, publication and VERIFY ordering. No client endpoint exists.
            check()
            saved = capture_first_registry_snapshot(adapter, store, verify)
            witness_pending = saved
            result.update(success=True, final_world_state='paused',
                          final_checkpoint_id=saved.checkpoint_id,
                          final_checkpoint_sha256=saved.sha256)
            raise _WitnessFinished()
        if shuttle_mode is not None:
            check()
            if shuttle_mode == 'prepare':
                witness_pending = capture_first_registry_snapshot(adapter, store, verify)
                result.update(final_checkpoint_id=witness_pending.checkpoint_id,
                              final_checkpoint_sha256=witness_pending.sha256)
            result.update(success=True, final_world_state='paused')
            raise _WitnessFinished()
        if parking is not None:
            check()
            if parking == 'prepare':
                saved = capture_first_registry_snapshot(adapter, store, verify)
                result['station_parking']['saved'] = parking_probe.verify_saved(saved, parking_location)
                witness_pending = saved
                result.update(final_checkpoint_id=saved.checkpoint_id, final_checkpoint_sha256=saved.sha256)
            result.update(success=True, final_world_state='paused')
            raise _WitnessFinished()
        service = owned.start_owned_file_service(secrets.token_hex(16), services)
        world = WorldCoordinator(epoch, adapter, store, SaveOffers(service, store))
        company_ship_reader = None
        if pose_live:
            company_ship_reader = pose_channel.read_company_catalogue
        elif company_undock:
            from authority.company_ships import read_company_ship_catalogue
            company_ship_reader = partial(read_company_ship_catalogue, include_captains=True)
        session = TrustedMarkerLoadSession(world, enabled=True, marker_script=marker_script,
                                         require_company_ship=require_company_ship,
                                         company_ship_reader=company_ship_reader)
        if company_motion:
            from authority.company_motion_service import CompanyMotionService
            application_drops = None
            if pose_trial is not None:
                application_drops = {}
                pose_trial.report['application_drops'] = application_drops
            motion_service = CompanyMotionService(application_drops=application_drops)
        if gun_damage_provider is not None:
            from authority.company_combat_service import CompanyCombatService

            def current_health_window(binding):
                if combat_world_running() and motion_service.combat_helm_current(binding):
                    return motion_service.window
                return None

            combat_service = CompanyCombatService(
                motion_service.current_combat_grant, gun_damage_provider.begin_impact,
                begin_health_read=gun_damage_provider.begin_health_read, health_window=current_health_window)
        if npc_source is not None:
            def npc_unreserved():
                reservations = session.running_ship_reservations()
                if reservations is None:
                    return False
                current, occupied = reservations
                receipt = registry_snapshot_state.latest_receipt
                return ((receipt['checkpoint_id'], receipt['sha256'], receipt['run_id'])
                        == (current.checkpoint_id, current.sha256, current.marker)
                        and not npc_source.reserved_or_unverified(current, occupied))

            npc_source.attach(motion_service, npc_unreserved)
        undock_capability = {'company_undock': True} if company_undock else {}
        control = ExperimentalTCPControl(session, enabled=True, idle_timeout=300, keepalive_enabled=True,
                                         motion_service=motion_service, company_purchases=company_purchases,
                                         company_trade_view=market_provider is not None,
                                         company_sales=company_sales,
                                         company_fleet_orders=fleet_provider is not None,
                                         **undock_capability)
        host = ExperimentalLocalHost(world, control, service, enabled=True)
        overlap_guard = [None]
        def verify_process():
            if overlap_guard[0] is None: return check()
            return overlap_guard[0]()
        world._participants = RecoveredGuardedParticipants(
            host, enabled=True, timeout=120, guard_process=verify_process,
            retire_disconnected_on_resume=require_company_ship,
            retire_disconnected_on_pause=require_company_ship)
        if purchase_provider is not None:
            from authority.company_purchase_scheduler import CompanyPurchaseScheduler
            purchase_options = {'undock': undock_provider} if undock_provider is not None else {}
            if fleet_provider is not None:
                purchase_options['fleet'] = fleet_provider
            if company_sales:
                purchase_options['sales'] = True
            purchases = CompanyPurchaseScheduler(world._participants, purchase_provider, purchase_ledger,
                                                 guard=world._participants.check_process, market=market_provider,
                                                 **purchase_options)
        listener=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0)); listener.listen(8)
        # Guard every synchronous engine call, including potentially long save operations.
        original = world._engine
        class GuardedEngine:
            def __getattr__(self, name):
                value = getattr(original, name)
                if name in ('pause','resume','save','checkpoint_published') and callable(value):
                    def call(*args):
                        verify_process()
                        if motion_service is not None and name == 'pause':
                            if pose_trial is None:
                                motion_service.set_running(False)
                            else:
                                pose_trial.before_pause(motion_service)
                            motion_service.poll()
                        result = value(*args)
                        verify_process()
                        if pose_trial is not None:
                            if name == 'pause':
                                pose_trial.after_pause()
                            elif name == 'resume':
                                pose_trial.start_window()
                            elif name == 'checkpoint_published' and result is True:
                                pose_trial.checkpoint_published(args[0])
                        if motion_service is not None and name == 'resume':
                            motion_service.set_running(True)
                        operations.append(name)
                        return result
                    return call
                return value
        world._engine = GuardedEngine()
        serving[0] = True
        diagnostic_count = getattr(args, 'diagnostic_clients', None)
        if diagnostic_count is not None and (type(diagnostic_count) is not int or diagnostic_count != 2):
            raise ValueError('only two sequential diagnostic clients are supported')
        if diagnostic_count is not None and getattr(args, 'diagnostic_client', False):
            raise ValueError('choose one diagnostic mode')
        overlap = getattr(args, 'diagnostic_overlap', False)
        if type(overlap) is not bool or (overlap and (getattr(args, 'diagnostic_client', False) or diagnostic_count is not None)):
            raise ValueError('choose one diagnostic mode')
        if overlap:
            import authority_recovered_server_client as diagnostic_client
            player_grants = []
            if player_source_sectors is not None:
                registry_epoch = fixture_helpers.fixture_source.fixture_save.base.archive.REGISTRY_EPOCH
                used_tokens = {epoch, registry_epoch}
                if sample_writer is not None:
                    used_tokens.update(sample_writer.values())
                def fresh_player_token():
                    value = secrets.token_hex(16)
                    while value in used_tokens:
                        value = secrets.token_hex(16)
                    used_tokens.add(value)
                    return value
                def new_player_grant(slot, sector):
                    return {'session_epoch': epoch, 'epoch': registry_epoch,
                        'generation': (sample_writer['generation'] if sample_writer is not None and slot == sample_write_slot
                                       else fresh_player_token()), 'index': slot + 2, 'sector_macro': sector,
                        'token': fresh_player_token(), 'incarnation': fresh_player_token()}
                for slot, sector in enumerate(player_source_sectors, 1):
                    player_grants.append(new_player_grant(slot, sector))
            def cancelled():
                if stop[0]: raise InterruptedError('diagnostic cancelled')
            def base_overlap():
                # Each retained client's own context checks its X4 and files;
                # the outer aggregate separately verifies the server once.
                cancelled(); check(); cancelled()
            def server_overlap():
                cancelled(); verify(); cancelled()
            def verify_overlap():
                verify_diagnostic_context(verify_server=verify, active_clients=active_clients,
                    check_process=check, check_cancel=cancelled)
            def cancel_overlap(): verify_overlap()
            overlap_guard[0] = verify_overlap
            def start_overlap(index, *, rejoining=False):
                diagnostic_class = fixture_helpers.FixtureServerClient if fixture_mode else diagnostic_client.ServerClient
                diagnostic = diagnostic_class(deadline, source_store.directory, keep_worker=True)
                diagnostics.append(diagnostic)
                diagnostic_out = out/('overlap-rejoin' if rejoining else 'overlap-%02d' % (index+1)); diagnostic_out.mkdir(mode=0o700)
                return diagnostic_client.run_one(diagnostic, args=args, out=diagnostic_out, game=game,
                    home=home, uid=user.pw_uid, steam=steam, auth=auth, user=user.pw_name,
                    processes=processes, server_proc=proc, server_started=started, server_identity=identity,
                    checkpoint=checkpoint, recovery_target=target, server_expected=expected,
                    recovery_watch=watch, listener=listener, host=host, service=service, world=world,
                    session=session, control=control, check_server=verify_overlap, check_base=base_overlap,
                    check_server_base=server_overlap, fixture_scripts=fixture_scripts,
                    fixture_slot=index+1 if fixture_mode else None,
                    fixture_player_source=({'sector': player_source_sectors[index],
                        'grant': player_grants[index], 'timeout': 15,
                        'latest_cache': latest_fixture_source, 'freeze_drain': freeze_fixture_source,
                        'throttle': sample_write_after_throttle and index + 1 == sample_write_slot} if player_grants else None),
                    server_out=out, cancel_check=cancel_overlap,
                    verify_client_save=fixture_save_guards.check if fixture_save_guards else None)
            def observe_fixture_players(clients):
                import authority_fixture_player_source_observe
                def fence_observation():
                    host._failed = True
                    delivery_owner = getattr(host._control, '_job_delivery_owner', None)
                    if delivery_owner is not None:
                        delivery_owner._failed = True
                by_handle = {}
                for index, active in enumerate(clients):
                    peer = control._peers.get(active.participant.server_tcp)
                    if peer is None or peer['stage'] != 'admitted':
                        raise RuntimeError('fixture player admission changed')
                    handle = peer['handle']
                    if handle in by_handle: raise RuntimeError('fixture player handle reused')
                    by_handle[handle] = player_grants[index]
                progress = {}
                result['fixture_player_source_progress'] = progress
                options = {'progress': progress, 'suspend_on_exit': True}
                if sample_write_slot is not None:
                    from authority_fixture_player_sample_write_ipc import FixturePlayerSampleWriteIPC
                    identity_stat = (game / 'X4').stat()
                    process_identity = {'pid': identity[0], 'start_ticks': identity[1],
                                        'exe_dev': identity_stat.st_dev, 'exe_ino': identity_stat.st_ino}
                    expected_players = world.session.players
                    expected_ready_players = world.session.ready_players
                    def write_sample(handle, grant, sample):
                        if (control._peers.get(by_handle_peer[handle], {}).get('handle') != handle or
                                by_handle.get(handle) != grant):
                            raise RuntimeError('fixture sample grant owner changed')
                        def same_grant(candidate):
                            return candidate == by_handle.get(handle)
                        def writer_guard():
                            verify_overlap()
                            if (world.session.state != 'running' or world.session.pending is not None or
                                    world.session.players != expected_players or
                                    world.session.ready_players != expected_ready_players or
                                    control._peers.get(by_handle_peer[handle], {}).get('handle') != handle):
                                raise RuntimeError('fixture write session changed')
                            return True
                        writer = FixturePlayerSampleWriteIPC(sample_write_sock, enabled=True,
                            lease=sample_writer['lease'], attempt=sample_writer['attempt'], grant=grant,
                            process=process_identity, guard=writer_guard, grant_guard=same_grant,
                            timeout=min(120, max(0, deadline-time.monotonic())))
                        try:
                            writer.wait_ready()
                            return writer.apply_once(sample)
                        finally:
                            result['fixture_sample_write']['dispatch_attempted'] = writer.write_attempted
                            result['fixture_sample_write']['immediate_readback_confirmed'] = writer.write_confirmed
                            writer.close()
                    by_handle_active = {control._peers[active.participant.server_tcp]['handle']: active
                                        for active in clients}
                    by_handle_peer = {handle: active.participant.server_tcp
                                      for handle, active in by_handle_active.items()}
                    options.update(sample_write_slot=sample_write_slot, write_sample_once=write_sample)
                    if sample_write_after_throttle:
                        import authority_fixture_client_throttle_ipc as throttle_ipc
                        def pulse_client(handle, grant):
                            active = by_handle_active[handle]
                            if by_handle.get(handle) != grant:
                                raise RuntimeError('fixture throttle grant owner changed')
                            process = getattr(active.participant, '_fixture_throttle_process', None)
                            if type(process) is not dict:
                                raise RuntimeError('fixture throttle process identity unavailable')
                            def running():
                                return (world.session.state == 'running' and world.session.pending is None and
                                        world.session.players == expected_players and
                                        world.session.ready_players == expected_ready_players)
                            def throttle_guard():
                                verify_overlap()
                                if (not running() or by_handle.get(handle) != grant or
                                        control._peers.get(by_handle_peer[handle], {}).get('handle') != handle):
                                    raise RuntimeError('fixture throttle session changed')
                                return True
                            def pump():
                                throttle_guard()
                                host.tick(0, defer_world=True)
                                throttle_guard()
                            try:
                                receipt = active.participant.pulse_fixture_throttle(
                                    process=dict(process), guard=throttle_guard, running=running, pump=pump, timeout=15)
                            except throttle_ipc.ThrottleUnconfirmed as error:
                                record_fixture_throttle_uncertainty(result, error)
                                raise RuntimeError('fixture throttle unconfirmed') from None
                            result['fixture_sample_write']['throttle_receipt'] = dict(receipt)
                            return receipt
                        options['pulse_client_once'] = pulse_client
                sampling_window = None
                if fixture_save_guards is not None:
                    from authority_fixture_save_guards import FixtureSamplingWindow
                    def digest_guard():
                        if time.monotonic() >= deadline:
                            raise TimeoutError('fixture digest deadline expired')
                        cancelled(); check(); cancelled()
                        return True
                    sampling_window = FixtureSamplingWindow(fixture_save_guards, verify_overlap, digest_guard)
                    options['verify_transaction_boundary'] = sampling_window.boundary
                if fixture_rejoin:
                    def rejoin_player(delivery, owners, create_owner):
                        if len(active_clients) != 2 or tuple(active_clients) != tuple(clients):
                            raise RuntimeError('fixture clients changed before rejoin')
                        first, survivor = active_clients
                        if sampling_window is not None:
                            sampling_window.close()
                        first_peer = first.participant.server_tcp
                        survivor_peer = survivor.participant.server_tcp
                        first_handle = control._peers[first_peer]['handle']
                        survivor_handle = control._peers[survivor_peer]['handle']
                        survivor_owner = owners[survivor_handle]
                        if delivery.suspend_client_authority() is not True:
                            raise RuntimeError('fixture departure suspension unconfirmed')
                        active_clients.pop(0)
                        departure_result = first.finish()
                        diagnostic_client.recovery.tcp.await_eof(first_peer, deadline, verify_overlap)
                        while first_peer in control._peers:
                            verify_overlap()
                            host.tick(min(.1, diagnostic_client.recovery.marked.remaining(deadline)))
                        if (world.session.state != 'running' or world.session.pending is not None or
                                len(world.session.ready_players) != 1 or first_handle in delivery._client_authority_selected_owners):
                            raise RuntimeError('fixture survivor or departure unconfirmed')
                        del owners[first_handle]
                        if delivery.resume_client_authority() is not True:
                            raise RuntimeError('fixture survivor authority unconfirmed')
                        if delivery.suspend_client_authority() is not True:
                            raise RuntimeError('fixture rejoin suspension unconfirmed')
                        player_grants[0] = new_player_grant(1, player_source_sectors[0])
                        replacement = start_overlap(0, rejoining=True)
                        active_clients.append(replacement)
                        verify_overlap()
                        replacement_peer = replacement.participant.server_tcp
                        replacement_entry = control._peers.get(replacement_peer)
                        if (replacement_entry is None or replacement_entry['stage'] != 'admitted' or
                                owners.get(survivor_handle) is not survivor_owner):
                            raise RuntimeError('fixture replacement or survivor changed')
                        replacement_handle = replacement_entry['handle']
                        owners[replacement_handle] = create_owner(replacement_handle, player_grants[0])
                        if delivery.resume_client_authority() is not True:
                            raise RuntimeError('fixture replacement authority unconfirmed')
                        if sampling_window is not None:
                            open_fixture_sampling_window(sampling_window, active_clients=active_clients,
                                selected_clients=(survivor, replacement), directory_names=('overlap-02', 'overlap-rejoin'),
                                out=out, server_save=target, server_digest=checkpoint.sha256)
                        result['fixture_rejoin_departure'] = departure_result
                        return {'departed': first_handle, 'survivor': survivor_handle,
                                'replacement': replacement_handle, 'grant': player_grants[0]}
                    options['rejoin_callback'] = rejoin_player
                observation_started = time.monotonic()
                try:
                    if sampling_window is not None:
                        open_fixture_sampling_window(sampling_window, active_clients=active_clients, selected_clients=clients,
                            directory_names=('overlap-01', 'overlap-02'), out=out,
                            server_save=target, server_digest=checkpoint.sha256)
                    observation = authority_fixture_player_source_observe.observe(
                        host=host, world=world, grants=by_handle, verify=verify_overlap,
                        deadline=deadline, operation_timeout=120, enabled=True, **options)
                    if sample_write_after_throttle:
                        result['fixture_sample_write']['client_movement_proven'] = observation.get('client_movement_proven') is True
                    if sampling_window is not None:
                        sampling_window.close()
                    return observation
                except BaseException:
                    fence_observation()
                    raise
                finally:
                    if fixture_save_guards is not None:
                        fixture_save_guards.metrics['observation_seconds'] = time.monotonic() - observation_started
                        try:
                            fixture_save_guards.close()
                        except BaseException:
                            fence_observation()
                            raise
            measure_first = None
            if measure_seconds is not None:
                from authority_overlap_process_measurement import measure_single_client_phase, write_report
                def measure_first(active, clock_owner, tick, check_first_client):
                    window = None
                    if fixture_save_guards is not None:
                        from authority_fixture_save_guards import FixtureSamplingWindow
                        if fixture_save_guards._guards is not None:
                            raise RuntimeError('measurement requires closed fixture sampling guards')
                        def digest_guard():
                            if time.monotonic() >= deadline:
                                raise TimeoutError('measurement digest deadline expired')
                            cancelled(); check(); cancelled()
                            return True
                        window = FixtureSamplingWindow(fixture_save_guards, check_first_client, digest_guard)
                    try:
                        if window is not None:
                            open_first_fixture_sampling_window(window, active_clients=active_clients,
                                active=active, out=out, server_save=target, server_digest=checkpoint.sha256)
                        measured = measure_single_client_phase(
                            server_identity=identity, client_identity=active.x4_identity,
                            supervisor_identity=(os.getpid(), live.start_ticks(os.getpid())),
                            clock=clock_owner, tick=tick, check=check_first_client,
                            duration=measure_seconds, deadline=deadline)
                        if window is not None:
                            window.close()
                        measured['save_guard_mode'] = ('pinned_metadata_with_full_sha256_boundaries'
                                                       if window is not None else 'full_sha256_checks')
                        measured['other_protected_files_full_hashes'] = True
                        if window is not None:
                            measured['fixture_save_guard_metrics'] = dict(fixture_save_guards.metrics)
                        summary = write_report(out, measured)
                        result['diagnostic_process_measurement'] = summary
                        return summary
                    except BaseException as error:
                        host._failed = True
                        if fixture_save_guards is not None:
                            try:
                                fixture_save_guards.close()
                            except BaseException as cleanup_error:
                                raise BaseExceptionGroup('measurement and guard cleanup failed',
                                                         [error, cleanup_error]) from None
                        raise
            result.update(run_overlapping_diagnostics(start=start_overlap, active_clients=active_clients,
                world=world, host=host, control=control, deadline=deadline, verify=verify_overlap, clock=clock,
                marker_script=marker_script, observe_players=observe_fixture_players if player_grants else None,
                measure_first=measure_first))
            overlap_guard[0] = None
        if getattr(args, 'diagnostic_client', False) or diagnostic_count == 2:
            # Opt-in local test clients, all resources still registered to
            # this supervisor and protected by its single global lock.
            import authority_recovered_server_client as diagnostic_client
            count = diagnostic_count or 1
            results = []
            baseline = None
            def cancel_diagnostic():
                if stop[0]: raise InterruptedError('diagnostic cancelled')
            playback_identity_scope = None
            playback_identity_client = None
            def verify_diagnostic():
                from authority_owned_game_discovery import retained_scope
                with retained_scope(playback_identity_scope, playback_identity_client, active_clients):
                    return verify_diagnostic_body()
            def verify_diagnostic_body():
                cancel_diagnostic()
                verify()
                if source_enabled:
                    for active in tuple(active_clients):
                        if active.participant.check_active(server_checked=True) is not True:
                            raise RuntimeError('diagnostic client unavailable')
                        verify()
                cancel_diagnostic()
            def base_diagnostic():
                cancel_diagnostic();check();cancel_diagnostic()
            def guard_ready(guard, saved, pump):
                if guard_context or guard.status!=registry_expected:
                    raise ValueError('confirmed diagnostic registry changed')
                guard_context.append((guard,saved,pump))
                if guard_timing: guard.enable_timing()
            if source_enabled:overlap_guard[0]=verify_diagnostic
            for index in range(count):
                if owner_diagnostic:
                    from authority_recovered_owner_client import RecoveredOwnerClient
                    diagnostic = RecoveredOwnerClient(deadline, source_store.directory)
                    diagnostic._owner_guard_slot = overlap_guard
                else:
                    diagnostic = diagnostic_client.ServerClient(deadline, source_store.directory, keep_worker=source_enabled)
                diagnostics.append(diagnostic)
                diagnostic_out = out if count == 1 else out/('diagnostic-%02d' % (index+1))
                if count != 1: diagnostic_out.mkdir(mode=0o700)
                client_motion_timeout = 25
                diagnostic_result = diagnostic_client.run_one(diagnostic, args=args, out=diagnostic_out, game=game,
                    home=home, uid=user.pw_uid, steam=steam, auth=auth, user=user.pw_name,
                    processes=processes, server_proc=proc, server_started=started, server_identity=identity,
                    checkpoint=checkpoint, recovery_target=target, server_expected=expected,
                    recovery_watch=watch, listener=listener, host=host, service=service, world=world,
                    session=session, control=control, check_server=verify_diagnostic, server_out=out,
                    expected_baseline=baseline, cancel_check=cancel_diagnostic,
                    check_base=base_diagnostic if source_enabled else None,
                    check_server_base=verify if source_enabled else None,
                    witness_guard_ready=guard_ready if source_enabled else None,
                    verify_client_save=playback_window.check_save if playback_window is not None else None,
                    client_motion=({'indices':list(source_candidates or (source_index,)),
                        'generation':source_settings['generation'],'timeout':client_motion_timeout,
                        **({'saved_apply_fixture':saved_apply_fixture} if saved_apply_fixture else {}),
                        **({'saved_playback_fixture':saved_playback_fixture} if saved_playback_fixture else {}),
                        **({'player_sector':True} if client_sector else {})} if client_motion else None))
                if saved_playback_fixture:
                    from authority_owned_game_discovery import OwnedGameDiscovery
                    discovery = OwnedGameDiscovery(((proc, started, game, identity),
                        (diagnostic_result.launcher, diagnostic_result.launcher_started,
                         game, diagnostic_result.x4_identity)))
                    playback_identity_scope = discovery.scope
                    playback_identity_client = diagnostic_result
                if source_enabled:
                    import authority_recovered_source_probe
                    expected_members=tuple(world.session.ready_players)
                    def verify_motion_stopped():
                        cancel_diagnostic();verify()
                        if (diagnostic.check_motion_stopped() is not True or world.session.state!='running' or
                                world.session.pending is not None or tuple(world.session.ready_players)!=expected_members or
                                tuple(world.session.players)!=expected_members):
                            raise RuntimeError('stopped client or membership changed')
                        verify();cancel_diagnostic()
                        return True
                    def run_source(guard,saved,pump,active):
                        source_context=dict(source_settings,epoch=guard.status[1],baseline=guard.status[2])
                        playback_options = {}
                        if saved_playback_fixture:
                            from authority_paired_playback_evidence import PairedPlaybackEvidence
                            client_log = diagnostic_out/'client/case/home/.config/EgoSoft/X4/45841/engine-output.log'
                            evidence_options = {'latest': True} if latest_playback else {}
                            if real_time_motion:
                                evidence_options['sample_version'] = 3
                            evidence = PairedPlaybackEvidence(client_log,
                                guard=lambda: (verify_diagnostic() is None), deadline=deadline, **evidence_options)
                            def observe_playback(sample):
                                try:
                                    return evidence.received(sample)
                                finally:
                                    if evidence.initial_comparison is not None:
                                        result['paired_playback']['initial_comparison'] = dict(evidence.initial_comparison)
                            def process_guard():
                                base_diagnostic()
                                return True
                            def begin_playback():
                                return playback_window.open(
                                    [(target, checkpoint.sha256), (active.save_target, active.save_digest)],
                                    guard, process_guard=process_guard,
                                    deadline=min(deadline, time.monotonic() + 60))
                            playback_options = {'saved_playback_fixture': saved_playback_fixture,
                                'playback_begin': begin_playback,
                                'playback_identity_scope': playback_identity_scope,
                                'playback_liveness_check': active.participant.check_active_liveness,
                                'playback_checkpoint_check': lambda: playback_window.check_checkpoint(guard),
                                'playback_checkpoint_stop': lambda: playback_window.stop_checkpoint(guard),
                                'playback_sample_observer': observe_playback}
                            if latest_playback:
                                result['paired_playback']['batch_latest_saved_checks'] = True
                                playback_options.update(latest_playback=True,
                                    playback_checkpoint_lifetime=lambda: playback_window.check_checkpoint_lifetime(guard))
                        # X4's source owner has its own 120-second pending-operation
                        # deadlines, including binding begun at resume. A longer
                        # host budget does not extend those or guarantee completion.
                        budgets = None
                        if client_motion:
                            budgets = {'total': 2400 if long_motion else 1200, 'source_bind_stage': 120,
                                       'source_operation': 600 if long_motion else 300,
                                       'tcp_send': 480 if long_motion else 240, 'client_bind': client_motion_timeout}
                        observation = run_source_probe_with_progress(result,authority_recovered_source_probe.run,sock=source_sock,
                            include_guard_callsites=guard_timing,
                            settings=source_context,index=source_index,baseline=saved,guard=guard,pump=pump,
                            active=active,host=host,world=world,identity=identity,verify=verify_diagnostic,
                            verify_process=check,candidate_indices=source_candidates,client_motion=client_motion,
                            verify_stopped=verify if client_motion else None, diagnostic_timeouts=budgets,
                            private_long_diagnostic=long_motion,
                            diagnostic_guard_cache_ms=diagnostic_guard_cache_ms,
                            **playback_options,
                            **({'saved_apply_fixture':saved_apply_fixture,
                                'record_apply_intent':lambda sample: record_saved_apply_intent(out, sample)}
                               if saved_apply_fixture else {}),
                            **({'sector_poll':active.sector_poll,'sector_stop':active.sector_stop,
                                'sector_arm':active.sector_arm,
                                'sector_incarnation':active.participant._sector_incarnation} if client_sector else {}))
                        if real_time_motion:
                            require_real_time_observation(observation)
                        if saved_playback_fixture:
                            observation['playback_readback'] = evidence.stopped(stopped_guard=verify_motion_stopped)
                            playback_window.close(process_guard=process_guard)
                            result['paired_playback']['readback_before_departure'] = observation['playback_readback']
                        return observation
                    result['source_diagnostic'],diagnostic_result=finish_source_diagnostic(
                        diagnostic_result,active_clients,guard_context,verify=verify_diagnostic,probe=run_source,
                        verify_after=verify_motion_stopped if client_motion else None,
                        require_client_receipts=client_motion,pair_delivery=pair_delivery,
                        saved_playback=saved_playback_fixture is not None,
                        latest_playback=latest_playback,
                        sector_intermediate_result=result if client_sector else None)
                playback_identity_scope = None
                if count != 1:
                    baseline = diagnostic_client.settle_departure(host, world, control, diagnostic.server_tcp, deadline, verify_diagnostic,
                        diagnostic_result['client_ticket'], diagnostic_result['client_loaded_marker'])
                    diagnostic_result.update(departure_checkpoint_id=baseline.checkpoint_id,
                                             departure_checkpoint_sha256=baseline.sha256,
                                             departure_checkpoint_marker=baseline.marker)
                results.append(diagnostic_result)
            if witness_join:
                baseline = diagnostic_client.settle_departure(host, world, control, diagnostic.server_tcp, deadline, verify_diagnostic,
                    diagnostic_result['client_ticket'], diagnostic_result['client_loaded_marker'])
                listener.close(); listener = None
                final = settle_shutdown(control, world, store, check); control = None
                if final != baseline: raise ValueError('witness join final checkpoint changed')
                verify()
                result.update(success=True, final_world_state='paused', final_checkpoint_id=final.checkpoint_id,
                    final_checkpoint_sha256=final.sha256, diagnostic_client=results[0],
                    witness_join={'server_witness_verified':True,'client_witness_verified':False,
                        'historical_identity_proven':False,'cross_process_identity_proven':False,
                        'loopback_only':True,'no_further_client_admission':True})
                raise _WitnessFinished()
            if count == 1: result['diagnostic_client'] = results[0]
            else: result['diagnostic_clients'] = results
        # Publish only after the private diagnostic sockets have been accepted
        # and the selected listener is ready. Both files contain bearer secrets.
        if stop[0] or time.monotonic() >= deadline:
            raise InterruptedError('provider startup cancelled')
        if tls is not None:
            gateway = RecoveredTLSGateway(tls, out=out, control_port=listener.getsockname()[1],
                                          file_port=service.port, file_token=service.token,
                                          motion_port=motion_service.port if motion_service is not None else None,
                                          combat_port=combat_service.port if combat_service is not None else None)
            gateway.start(min(5, max(0, deadline - time.monotonic())))
        else:
            fd = os.open(endpoint, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump({'version':1,'control_host':'127.0.0.1','control_port':listener.getsockname()[1],
                           'file_host':'127.0.0.1','file_port':service.port,'file_token':service.token,'file_slot':1}, stream)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())

        def gateway_stopped():
            return gateway is not None and not gateway.running

        service_deadline = None if getattr(args, 'persistent', False) else deadline
        result.update(serve(listener, host, deadline=service_deadline, check=check, motion_service=motion_service, pose_trial=pose_trial,
                            purchases=purchases, npc_source=npc_source, combat_service=combat_service,
                            should_stop=lambda: stop[0] or gateway_stopped()))
        failed_gateway = gateway_stopped()
        try:
            if gateway is not None:
                gateway.close()
                gateway = None
            else:
                endpoint.unlink()
        finally:
            # Stop intake, then settle the last-disconnect save before stopping X4.
            listener.close()
            listener = None
            saved = settle_shutdown(control, world, store, check, purchases=purchases, combat_service=combat_service)
            control = None
        if failed_gateway:
            raise RuntimeError('TLS gateway stopped while serving')
        verify()
        result.update(success=True, final_world_state=world.session.state,
                      final_checkpoint_id=None if saved is None else saved.checkpoint_id,
                      final_checkpoint_sha256=None if saved is None else saved.sha256)
    except _WitnessFinished:
        pass
    except (Exception, KeyboardInterrupt, BaseExceptionGroup) as error:
        result['error'] = str(error) or type(error).__name__
        if isinstance(error, BaseExceptionGroup):
            # Keep cancellation and a simultaneous guard-exit failure visible.
            pending_errors = [error]
            error_types = []
            while pending_errors and len(error_types) < 16:
                current = pending_errors.pop()
                if isinstance(current, BaseExceptionGroup):
                    pending_errors.extend(reversed(current.exceptions[:16]))
                else:
                    error_types.append(type(current).__name__)
            result['error_types'] = error_types
    finally:
        errors=[]
        if playback_window is not None:
            try: playback_window.cleanup()
            except BaseException as error: errors.append('playback save guard cleanup: '+type(error).__name__)
        for number in handlers:
            try: signal.signal(number,signal.SIG_IGN)
            except BaseException as error: errors.append('cannot fence signals: '+type(error).__name__)
        if gateway is not None:
            try: gateway.close()
            except BaseException as error: errors.append('TLS gateway cleanup: '+type(error).__name__)
        try: endpoint.unlink(missing_ok=True)
        except BaseException as error: errors.append('endpoint cleanup: '+type(error).__name__)
        if listener is not None:
            try: listener.close()
            except BaseException as error: errors.append('listener cleanup: '+type(error).__name__)
        if control is not None and not (control._closed or control._failed):
            try: control.close()
            except BaseException as error: errors.append('control cleanup: '+type(error).__name__)
        if npc_source is not None:
            try:
                npc_source.close()
                result['registered_npc_observation'] = npc_source.stats()
            except (OSError, RuntimeError, ValueError) as error:
                errors.append('registered NPC source cleanup: ' + type(error).__name__)
        if combat_service is not None:
            try:
                combat_service.close()
            except OSError as error:
                errors.append('Company combat service cleanup: ' + type(error).__name__)
        if gun_damage_provider is not None:
            try:
                gun_damage_provider.close()
            except OSError as error:
                errors.append('native gun damage cleanup: ' + type(error).__name__)
        if motion_service is not None:
            result['company_motion_samples_received'] = motion_service.accepted_samples
            result['company_motion_samples_with_velocity'] = motion_service.samples_with_velocity
            result['company_motion_last_ship'] = motion_service.last_ship_position
            result['company_motion_participants'] = motion_service.participant_traffic()
            try:
                motion_service.close()
            except OSError as error:
                errors.append('movement service cleanup: ' + type(error).__name__)
        if purchase_ledger is not None:
            try:
                purchase_ledger.close()
            except Exception as error:
                errors.append('purchase ledger cleanup: ' + type(error).__name__)
        if purchase_provider is not None:
            try:
                purchase_provider.close()
            except OSError as error:
                errors.append('purchase provider cleanup: ' + type(error).__name__)
        if undock_provider is not None:
            try:
                undock_provider.close()
            except OSError as error:
                errors.append('undock provider cleanup: ' + type(error).__name__)
        if market_provider is not None:
            try:
                market_provider.close()
            except OSError as error:
                errors.append('market survey cleanup: ' + type(error).__name__)
        if fleet_provider is not None:
            try:
                fleet_provider.close()
            except OSError as error:
                errors.append('Company fleet order cleanup: ' + type(error).__name__)
        if pose_channel is not None:
            pose_channel.close()
        services_stopped = True
        for service in services:
            try: owned.close_owned_file_service(service)
            except BaseException as error:
                services_stopped = False
                errors.append('service cleanup: '+type(error).__name__)
        if sock is not None:
            try: sock.close()
            except BaseException as error: errors.append('socket cleanup: '+type(error).__name__)
        if clock_sock is not None:
            try: clock_sock.close()
            except BaseException as error: errors.append('clock socket cleanup: '+type(error).__name__)
        if source_sock is not None:
            try: source_sock.close()
            except BaseException as error: errors.append('source socket cleanup: '+type(error).__name__)
        if sample_write_sock is not None:
            try: sample_write_sock.close()
            except BaseException as error: errors.append('sample write socket cleanup: '+type(error).__name__)
        helpers_stopped = cleanup_diagnostics(diagnostics, errors)
        remaining = stop_owned_processes(processes, helpers_stopped, errors)
        result['owned_processes_remaining']=remaining
        if remaining: errors.append('owned processes remain')
        if result['success'] and remaining == []:
            try:
                verify_final_recovery(watch)
                if witness_check:
                    verify_final_witness(profile/'engine-output.log',witness_nonce,
                                         registry_expected[1],registry_expected[2],witness_phase)
                if (live.sha(target) != checkpoint.sha256 or
                        any(live.sha(path) != digest for path,digest in expected.items())):
                    raise ValueError('final protected recovery inputs changed')
                if witness_check:
                    verify_witness_source(source_store,checkpoint,witness_source_guard,witness_source_scripts)
                if sample_write_slot is not None:
                    import authority_fixture_player_sample_install as sample_install
                    observation = result.get('fixture_player_source_diagnostic', {}).get('server_position_once')
                    if type(observation) is not dict:
                        raise ValueError('fixture sample write observation missing')
                    sample_install.verify_log(profile / 'engine-output.log', nonce=sample_writer['nonce'],
                                              digest=observation['sample_sha256'])
                if fixture_mode:
                    if (source_store.latest_marked_confirmed() != checkpoint or
                            read_evidence(fixture_source_result_path) != fixture_source_result):
                        raise ValueError('fixture checkpoint source changed')
                    source_md = source_store.directory.parent/'case/extensions/x4_lan_lab/md'
                    if any(read_evidence(source_md/name, 1 << 20) != data for name, data in fixture_scripts.items()):
                        raise ValueError('fixture source scripts changed')
                    if fixture_source_result_path.name == 'result.json':
                        import authority_saved_registry_checkpoint_source as fixture_source
                        fixture_source.verify_current_confirmed(source_store.directory)
                if require_snapshot:
                    verify_previous_source(source_store, checkpoint, profile,
                                           require_registry_snapshot=True)
                if shuttle_mode is not None:
                    if (read_evidence(shuttle_source, 1 << 20) != shuttle_provenance
                            or shuttle_probe.verify_log(shuttle_log, shuttle_nonce, read_evidence) != shuttle_pair):
                        raise ValueError('shuttle source or native receipt changed before cleanup')
                    if shuttle_mode == 'verify' and any(store.directory.iterdir()):
                        raise ValueError('cold shuttle verification published a checkpoint')
                    pair_checkpoint = witness_pending if shuttle_mode == 'prepare' else checkpoint
                    shuttle_result['saved'] = shuttle_probe.verify_saved(pair_checkpoint, shuttle_pair)
                    shuttle_result['cue_sha256'] = shuttle_probe.archive_cues(out, case, expected, live.sha)
                if pose_channel is not None:
                    pose_overlay.archive_cue(case, out)
                if purchase_provider is not None:
                    purchase_provider.archive_cues(case, out)
                if gun_damage_provider is not None:
                    gun_damage_provider.archive_cues(case, out)
                if undock_provider is not None:
                    undock_provider.archive_cue(case, out)
                if market_provider is not None:
                    market_provider.archive_cue(case, out)
                if fleet_provider is not None:
                    fleet_provider.archive_cue(case, out)
                if npc_source is not None:
                    npc_source.archive_cue(out)
                if trade_mode:
                    trade_probe.archive_cues(case, out, trade_mode)
                if parking is not None:
                    if parking_probe.verify_log(profile/'engine-output.log', parking_nonce, read_evidence) != parking_location:
                        raise ValueError('final parking receipt changed')
                    if parking == 'prepare':
                        if parking_probe.verify_saved(witness_pending, parking_location) != result['station_parking']['saved']:
                            raise ValueError('final parked checkpoint evidence changed')
                        result['station_parking']['park_cue_sha256'] = parking_probe.archive_cues(
                            out, case, expected, live.sha)
                    elif list(store.directory.iterdir()):
                        raise ValueError('read-only parked verification published a checkpoint')
                if bootstrap_snapshot:
                    verify_previous_source(source_store, checkpoint, profile,
                                           registry_status=True, return_supervisor_result=True,
                                           require_ready_registry_script=True)
                    first_capture_state.recheck(witness_pending, profile/'engine-output.log')
            except BaseException as error:
                result.update(success=False, error='final recovery evidence invalid: '+str(error))
        if local_pair:
            record_local_pair_result(result, profile, source_index, remaining)
        finish_recovery_lock(lockbox, remaining, services_stopped, errors)
        try:
            if live.steam_identity(home,user.pw_uid) != steam: errors.append('Steam identity changed')
        except BaseException as error: errors.append('Steam identity unavailable: '+type(error).__name__)
        if remaining == []:
            for diagnostic in reversed(diagnostics):
                try: diagnostic.redact()
                except BaseException as error: errors.append('diagnostic lease cleanup: '+type(error).__name__)
        config=out/'case/extensions/x4_lan_lab/ui/config.lua'
        try:
            redact_stopped_server_config(config,remaining)
        except BaseException as error: errors.append('lease cleanup: '+type(error).__name__)
        if witness_pending is not None:
            try: confirm_witness_after_cleanup(store,witness_pending,result,remaining,errors)
            except BaseException as error:
                result.update(success=False,error='witness checkpoint not safely confirmed: '+str(error))
        if errors: result.update(success=False,cleanup_errors=errors)
        if guard_context and getattr(guard_context[0][0], 'timing', None) is not None:
            result['witness_guard_client_timing'] = guard_context[0][0].timing.snapshot()
        try: (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        finally:
            for number,handler in handlers.items():
                try: signal.signal(number,handler)
                except BaseException: pass
    return result


def main(argv=None, *, display_hostname=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,required=True); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--store',type=Path,default=source.DEFAULT_STORE)
    parser.add_argument('--display',required=True); parser.add_argument('--timeout',type=float,default=3600)
    parser.add_argument('--persistent', action='store_true',
                        help='keep serving after startup until stopped; --timeout still bounds startup')
    parser.add_argument('--software-vulkan-icd', type=Path,
                        help='use this Mesa CPU Vulkan ICD and remove GPU devices from X4')
    parser.add_argument('--seed',type=int,default=276944566)
    tls = parser.add_argument_group('optional pinned TLS gateway')
    tls.add_argument('--tls-bind', help='IPv4 address for the TLS listener')
    tls.add_argument('--tls-port', type=int, help='TLS listener port; 0 selects an available port')
    tls.add_argument('--tls-advertise', help='client-reachable IPv4 address or DNS name')
    tls.add_argument('--tls-advertise-port', type=int, help='client-reachable port, if different from the listener')
    tls.add_argument('--tls-certificate', type=Path, help='PEM server certificate')
    tls.add_argument('--tls-private-key', type=Path, help='PEM server private key')
    tls.add_argument('--tls-shared-key-file', type=Path, help='64 lowercase hex characters from 32 random bytes')
    poses = parser.add_mutually_exclusive_group()
    poses.add_argument('--company-pose-trial', '--company-ship-positions', action='store_true',
                       help='apply admitted flight samples to available ships in their current sector')
    poses.add_argument('--verify-company-pose', action='store_true',
                       help='read the saved company ship positions after a cold restart')
    parser.add_argument('--company-motion', action='store_true',
                        help='enable the separate participant-bound TLS movement channel')
    parser.add_argument('--company-combat', action='store_true',
                        help='enable dedicated TLS delivery of confirmed Company primary-gun hits')
    parser.add_argument('--trial-registered-npc-measurement', action='store_true',
                        help='receive bounded read-only cockpit measurements for the private MFQ-419 trial')
    parser.add_argument('--observe-registered-npc', nargs=2, metavar=('INDEX', 'SHIP_CODE'),
                        help='observe one confirmed base-game NPC during the first running movement window')
    parser.add_argument('--company-purchases', action='store_true',
                        help='enable Company purchases for a verified company ship over pinned TLS')
    parser.add_argument('--company-fleet-orders', action='store_true',
                        help='enable stock orders for eligible Company fleet ships')
    parser.add_argument('--company-sales', action='store_true',
                        help='allow admitted pilots to sell from a confirmed Company trading berth')
    parser.add_argument('--company-undock-test', action='store_true',
                        help='allow one witnessed Company departure after an admitted purchase')
    parser.add_argument('--require-company-ship', action='store_true',
                        help='require every joining player to reserve a free shared-company ship')
    parser.add_argument('--registry-status',action='store_true',help='check saved registry status before activation and each save')
    parser.add_argument('--base-game-only', action='store_true',
                        help='exclude official DLC from the private game view and profile')
    parser.add_argument('--require-registry-snapshot',action='store_true',
                        help='require confirmed format-3 source and full live registry observations for every save')
    parser.add_argument('--bootstrap-registry-snapshot',action='store_true',
                        help='one-shot first format-3 capture from a confirmed ready-registry supervisor source; no endpoint')
    trade = parser.add_mutually_exclusive_group()
    trade.add_argument('--company-trade-trial', choices=('buy', 'six-unit', 'inspect'),
                       help='isolated two-ship stock trade experiment without network clients')
    trade.add_argument('--company-cockpit-purchase', type=Path, metavar='RESULT_JSON',
                       help='process one recorded stock cockpit purchase in the private recovered world')
    parser.add_argument('--company-shuttle-pair', choices=('prepare', 'verify'),
                        help='prepare or cold-check the private two-shuttle starting world')
    parser.add_argument('--park-server-avatar', choices=('prepare', 'verify'),
                        help='isolated private station parking fixture and read-only cold verification')
    parser.add_argument('--registry-enroll',action='store_true',help='experimentally enroll existing ships into an uninitialized registry')
    parser.add_argument('--registry-witness',choices=('create','verify'),help='isolated paused diagnostic; never admit clients')
    parser.add_argument('--registry-witness-join',action='store_true',help='private one-client witness join experiment; not identity proof')
    source_options=parser.add_mutually_exclusive_group()
    source_options.add_argument('--diagnostic-source-index',type=int,help='private saved-registry index to sample')
    source_options.add_argument('--diagnostic-source-candidates',type=int,nargs='+',help='bounded explicit indices to inspect for a private source sample')
    parser.add_argument('--diagnostic-client-motion',action='store_true',help='require private Linux client motion receipts: two normally, one with local pair delivery')
    parser.add_argument('--diagnostic-local-pair',action='store_true',help='estimate one local source pair for an explicit private source index')
    parser.add_argument('--diagnostic-local-pair-delivery',action='store_true',help='deliver exactly one known-rate local pair sample to the private client')
    parser.add_argument('--diagnostic-client-sector',action='store_true',help='observe one private client sector after its pair receipt')
    parser.add_argument('--diagnostic-saved-apply',action='store_true',help='private one-shot ENH-870 preflight and possible write; not gameplay')
    parser.add_argument('--diagnostic-saved-playback',action='store_true',help='private bounded ENH-870 source playback and native readback; not gameplay')
    parser.add_argument('--diagnostic-latest-playback',action='store_true',help='use replaceable position delivery for bounded saved playback')
    parser.add_argument('--diagnostic-real-time-motion',action='store_true',help='use v3 real-time capture in bounded latest saved playback')
    parser.add_argument('--diagnostic-guard-cache-ms',type=int,choices=(0,250),default=0,
                        help='opt in to a 250 ms cache for full source/checkpoint validation in latest real-time saved playback')
    parser.add_argument('--diagnostic-long-motion',action='store_true',help='long bounded private witness-join motion diagnostic')
    parser.add_argument('--diagnostic-guard-timing',action='store_true',help='record local guard pump, send and response wait times')
    parser.add_argument('--diagnostic-process-measure-seconds', type=float, metavar='SECONDS',
                        help='private overlap: measure the running server, first client and supervisor before the second join for 5 to 300 seconds')
    parser.add_argument('--diagnostic-tls-certificate',type=Path,help='private local diagnostic TLS certificate')
    parser.add_argument('--diagnostic-tls-private-key',type=Path,help='private local diagnostic TLS key')
    parser.add_argument('--diagnostic-single-owner',action='store_true',help='private single-owner load-only diagnostic')
    parser.add_argument('--fixture-overlap',action='store_true',help='use the confirmed four-entry player fixture for the private overlap diagnostic')
    parser.add_argument('--fixture-player-source-sectors', nargs=2, metavar=('SLOT1', 'SLOT2'),
                        help='opt in to read-only 6D player-source probes in the named fixture sectors')
    parser.add_argument('--fixture-player-source-quick-guards', action='store_true',
                        help='private fixture only: metadata guards between transaction digest boundaries')
    parser.add_argument('--fixture-player-source-latest-cache', action='store_true',
                        help='private fixture only: continuously sample and deliver the newest local pose')
    parser.add_argument('--fixture-player-source-freeze-drain', action='store_true',
                        help='private fixture latest source: drain before pause and resume by correlated ACK')
    parser.add_argument('--fixture-player-sample-write-slot', type=int, choices=(1, 2),
                        help='private diagnostic: write exactly one fixture SAMPLE2 position; frames assumed')
    parser.add_argument('--fixture-player-sample-write-after-throttle', action='store_true',
                        help='private diagnostic: observe changed client position after one bounded throttle impulse')
    parser.add_argument('--fixture-player-source-rejoin', action='store_true',
                        help='private fixture: depart and rejoin one player after both sources bind')
    diagnostic_options = parser.add_mutually_exclusive_group()
    diagnostic_options.add_argument('--diagnostic-client', action='store_true',
                        help='start one private Linux diagnostic client before serving other loopback clients')
    diagnostic_options.add_argument('--diagnostic-overlap', action='store_true',
                        help='start two overlapping private Linux diagnostic clients before serving other peers')
    diagnostic_options.add_argument('--diagnostic-clients', type=int, choices=(2,),
                        help='start two sequential private Linux diagnostic clients before serving other peers')
    args = parser.parse_args(argv)
    args.display_hostname = display_hostname
    try: result=run(args)
    except (OSError,ValueError,RuntimeError) as error: parser.exit(1,'REFUSED: '+str(error)+'\n')
    print(json.dumps(result,indent=2)); return 0 if result['success'] else 1

if __name__ == '__main__': sys.exit(main())
