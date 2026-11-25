"""Read two explicitly granted fixture-player tracks in an existing session.

This bounded, private diagnostic owns the existing serialized TCP reader while
it is running. It does not launch X4, authenticate a grant or prove gameplay.
The default does not change ships; an explicit callback may attempt one server
position write. The caller owns both client processes, the server, and all
cleanup, and must fence them after any uncertain operation. ``verify`` must
check those owned resources without pumping the host or using its TCP reader.
The bound test delivery remains owned until this diagnostic server shuts down;
this helper is not a reusable production-session lifecycle.
"""
import copy
import math
import time

from authority.experimental_tcp_client_authority_motion import ExperimentalTCPClientAuthorityMotion, grant_value
from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
from authority.experimental_tcp_motion import motion_value, motion_digest
from authority.load_messages import MAX_INTEGER

MAX_PULLS_PER_PLAYER = 4096


def validate_sample_write_report(report, sample):
    """Bind local XYZ readback evidence to the original, validated SAMPLE2."""
    fields = {'registry_index', 'sector_macro', 'source_sequence', 'sample_sha256',
              'target', 'before', 'after', 'initial_distance', 'target_error',
              'position_only', 'sustained_motion_proven'}
    if (type(report) is not dict or set(report) != fields or
            report['position_only'] is not True or report['sustained_motion_proven'] is not False or
            type(report['registry_index']) is not int or report['registry_index'] != sample['index'] or
            report['sector_macro'] != sample['sector_macro'] or
            type(report['source_sequence']) is not int or report['source_sequence'] != sample['sequence'] or
            report['sample_sha256'] != motion_digest(sample)):
        raise ValueError('fixture sample write evidence differs')
    for name in ('target', 'before', 'after'):
        point = report[name]
        if (type(point) is not list or len(point) != 3 or any(type(value) not in (int, float) or
                not math.isfinite(value) or abs(value) > 1e7 for value in point)):
            raise ValueError('invalid fixture write position')
    if report['target'] != [sample[axis] for axis in ('x', 'y', 'z')]:
        raise ValueError('fixture write target differs from source')
    for name in ('initial_distance', 'target_error'):
        if type(report[name]) not in (int, float) or not math.isfinite(report[name]):
            raise ValueError('invalid fixture write distance')
    initial = math.dist(report['before'], report['target'])
    final = math.dist(report['after'], report['target'])
    if (not 1 <= initial <= 50 or final > .5 or
            not math.isclose(initial, report['initial_distance'], rel_tol=1e-9, abs_tol=1e-9) or
            not math.isclose(final, report['target_error'], rel_tol=1e-9, abs_tol=1e-9)):
        raise ValueError('fixture write displacement not confirmed')
    return copy.deepcopy(report)


def observe(*, host, world, grants, verify, deadline, operation_timeout=30, enabled=False,
            verify_transaction_boundary=None, progress=None, rejoin_callback=None, suspend_on_exit=False,
            sample_write_slot=None, write_sample_once=None, pulse_client_once=None):
    """Observe two rate-bearing 6D samples per handle; return no grant secrets.

    Grants map admitted connection handles to the caller's private assertions.
    An initial rate-less baseline is required. Unknown subsequent rates are
    skipped; zero is a valid measured rate. Velocity is a source-time derivative,
    not an engine-native velocity or proof of visible motion. The optional
    boundary guard runs immediately before and after each participant pull.
    An optional empty progress dict receives per-handle registry index, attempted
    pulls, returned samples, validated complete-rate samples and pulls lacking
    a confirmed post-boundary check, even on failure. When the optional boundary
    callback is absent, all pulls count as lacking that additional check.
    """
    if (enabled is not True or not callable(verify) or type(deadline) not in (int, float) or not math.isfinite(deadline) or
            type(operation_timeout) not in (int, float) or not math.isfinite(operation_timeout) or
            not 0 < operation_timeout <= 300 or
            (verify_transaction_boundary is not None and not callable(verify_transaction_boundary)) or
            (progress is not None and (type(progress) is not dict or progress)) or
            (rejoin_callback is not None and not callable(rejoin_callback)) or
            type(suspend_on_exit) is not bool or
            ((sample_write_slot is None) != (write_sample_once is None)) or
            (pulse_client_once is not None and (sample_write_slot is None or not callable(pulse_client_once))) or
            (sample_write_slot is not None and (type(sample_write_slot) is not int or
             sample_write_slot not in (1, 2) or not callable(write_sample_once) or rejoin_callback is not None)) or
            type(grants) is not dict or len(grants) != 2 or
            any(type(handle) is not int or not 1 <= handle <= MAX_INTEGER for handle in grants)):
        raise ValueError('invalid fixture observation context')
    original_grants = copy.deepcopy(grants)
    selected = {handle: grant_value(grant) for handle, grant in original_grants.items()}
    handles = tuple(sorted(selected))
    if (set(grant['index'] for grant in selected.values()) != {3, 4} or
            len({grant['incarnation'] for grant in selected.values()}) != 2 or
            len({grant['token'] for grant in selected.values()}) != 2):
        raise ValueError('fixture grants must identify distinct players')
    epoch = world.session.world_epoch
    if any(grant['session_epoch'] != epoch for grant in selected.values()):
        raise ValueError('fixture grant session differs')
    initial_players = world.session.players
    initial_ready = world.session.ready_players

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('fixture player observation deadline')
        if (host._world is not world or world.session.world_epoch != epoch or
                world.session.state != 'running' or world.session.pending is not None or
                world.session.players != initial_players or world.session.ready_players != initial_ready or
                tuple(handle for handle, _ in initial_players) != handles or initial_ready != initial_players or
                grants != original_grants):
            raise RuntimeError('fixture participant context changed')
        if verify() is False:
            raise RuntimeError('fixture resource guard failed')
        if (time.monotonic() >= deadline or host._world is not world or
                world.session.world_epoch != epoch or world.session.state != 'running' or
                world.session.pending is not None or world.session.players != initial_players or
                world.session.ready_players != initial_ready or grants != original_grants):
            raise RuntimeError('fixture context changed during guard')

    check()
    delivery = ExperimentalTCPJobDelivery(host, enabled=True,
        timeout=min(operation_timeout, deadline - time.monotonic()))
    try:
        check()
        if delivery.bind_test_incarnations({handle: selected[handle]['incarnation'] for handle in handles}, enabled=True) is not True:
            raise RuntimeError('fixture participant bindings unconfirmed')
        check()
        owners = {}
        def create_owner(handle, grant):
            def grant_guard(candidate):
                check()
                return candidate == grant
            return ExperimentalTCPClientAuthorityMotion(delivery, enabled=True, grant=grant,
                guard=grant_guard, connection_handle=handle)
        for handle in handles:
            owners[handle] = create_owner(handle, selected[handle])
        evidence = {handle: [] for handle in handles}
        last = {handle: None for handle in handles}
        attempts = {handle: 0 for handle in handles}
        received = {handle: 0 for handle in handles}
        with_velocity = {handle: 0 for handle in handles}
        without_post_boundary = {handle: 0 for handle in handles}
        write_result = None
        write_attempted = False
        movement_baseline = movement_fence = movement_sample = throttle_receipt = None
        movement_deadline = min(deadline, time.monotonic() + operation_timeout) if pulse_client_once is not None else None

        def record_progress(handle):
            if progress is not None:
                progress[handle] = {'registry_index': selected[handle]['index'], 'pulls_attempted': attempts[handle],
                                    'samples_received': received[handle], 'samples_with_complete_rates': with_velocity[handle],
                                    'pulls_without_post_boundary': without_post_boundary[handle]}

        for handle in handles:
            record_progress(handle)
        def collect_samples(resume_handle=None):
            nonlocal write_result, write_attempted, movement_baseline, movement_fence, movement_sample, throttle_receipt, movement_deadline
            baseline_pending = resume_handle is not None
            resume_baseline = None
            def needs_sample(handle):
                awaiting_movement = (pulse_client_once is not None and write_result is None and
                                     selected[handle]['index'] == sample_write_slot + 2)
                return len(evidence[handle]) < 2 or awaiting_movement
            while any(needs_sample(handle) for handle in handles):
                for handle in handles:
                    if not needs_sample(handle):
                        continue
                    check()
                    if movement_deadline is not None and write_result is None and time.monotonic() >= movement_deadline:
                        raise TimeoutError('fixture client movement not observed before deadline')
                    if attempts[handle] >= MAX_PULLS_PER_PLAYER:
                        raise RuntimeError('fixture source sample limit exceeded')
                    if verify_transaction_boundary is not None:
                        boundary_ok = verify_transaction_boundary()
                        check()
                        if boundary_ok is not True:
                            raise RuntimeError('fixture transaction boundary guard failed')
                    attempts[handle] += 1
                    without_post_boundary[handle] += 1
                    record_progress(handle)
                    sample = owners[handle].pull()
                    if sample is not None:
                        received[handle] += 1
                        record_progress(handle)
                    if verify_transaction_boundary is not None:
                        boundary_ok = verify_transaction_boundary()
                        check()
                        if boundary_ok is not True:
                            raise RuntimeError('fixture transaction boundary guard failed')
                        without_post_boundary[handle] -= 1
                        record_progress(handle)
                    else:
                        check()
                    if sample is not None:
                        sample = motion_value(sample)
                        grant = selected[handle]
                        prior = last[handle]
                        if (sample['version'] != 2 or sample['epoch'] != grant['epoch'] or
                                sample['generation'] != grant['generation'] or sample['index'] != grant['index'] or
                                sample['sector_macro'] != grant['sector_macro'] or
                                sample['sequence'] != (1 if prior is None else prior['sequence'] + 1) or
                                (prior is None and sample['velocity'] is not None) or
                                (prior is not None and sample['source_time'] <= prior['source_time'])):
                            raise ValueError('fixture player sample differs from granted track')
                        if baseline_pending and handle == resume_handle:
                            if sample['velocity'] is not None or sample['angular_rate'] is not None:
                                raise ValueError('resumed fixture source lacks a fresh baseline')
                            baseline_pending = False
                            resume_baseline = {'connection_handle': handle, 'registry_index': grant['index'],
                                               'sequence': sample['sequence'], 'source_time': sample['source_time'],
                                               'velocity': sample['velocity'], 'angular_rate': sample['angular_rate']}
                        last[handle] = sample
                        if sample_write_slot is not None and grant['index'] == sample_write_slot + 2 and not write_attempted:
                            # The exclusive TCP pull has already released the reader.
                            position = [sample[axis] for axis in ('x', 'y', 'z')]
                            ready_to_write = pulse_client_once is None
                            if pulse_client_once is not None and movement_baseline is None:
                                movement_baseline = {'sequence':sample['sequence'], 'source_time':sample['source_time'],
                                                     'position':position}
                                candidate_grant = copy.deepcopy(grant)
                                check()
                                throttle_receipt = pulse_client_once(handle, candidate_grant)
                                check()
                                expected_throttle = {'start_dispatched':True, 'start_acknowledged':True,
                                    'stop_acknowledged':True, 'movement_proven':False,
                                    'human_input_proven':False, 'stationary_proven':False}
                                if (candidate_grant != grant or type(throttle_receipt) is not dict or
                                        set(throttle_receipt) != set(expected_throttle) | {'stop_game_time'} or
                                        any(type(throttle_receipt[key]) is not bool or throttle_receipt[key] is not value
                                            for key, value in expected_throttle.items()) or
                                        type(throttle_receipt['stop_game_time']) not in (int, float) or
                                        not math.isfinite(throttle_receipt['stop_game_time']) or
                                        not movement_baseline['source_time'] <= throttle_receipt['stop_game_time'] <= 1e12):
                                    raise ValueError('fixture throttle result or grant differs')
                                if verify_transaction_boundary is not None:
                                    if verify_transaction_boundary() is not True:
                                        raise RuntimeError('fixture throttle boundary guard failed')
                                    check()
                                # The fence and candidate require additional full pulls. The
                                # enclosing session deadline still applies to every operation.
                                movement_deadline = min(deadline, time.monotonic() + 300)
                            elif pulse_client_once is not None:
                                if (sample['sequence'] <= movement_baseline['sequence'] or
                                        sample['source_time'] <= movement_baseline['source_time']):
                                    raise ValueError('fixture movement sample is stale')
                                if movement_fence is None:
                                    if sample['source_time'] > throttle_receipt['stop_game_time']:
                                        movement_fence = {'sequence': sample['sequence'],
                                                          'source_time': sample['source_time'],
                                                          'position': position}
                                elif (sample['sequence'] > movement_fence['sequence'] and
                                      sample['source_time'] > movement_fence['source_time']):
                                    ready_to_write = math.dist(movement_baseline['position'], position) >= 1
                            if ready_to_write:
                                if movement_deadline is not None and time.monotonic() >= movement_deadline:
                                    raise TimeoutError('fixture client movement not observed before deadline')
                                # Mark before callback: a lost result may follow a real native write.
                                write_attempted = True
                                candidate, candidate_grant = copy.deepcopy(sample), copy.deepcopy(grant)
                                sample_digest = motion_digest(sample)
                                check()
                                report = write_sample_once(handle, candidate_grant, candidate)
                                check()
                                if candidate_grant != grant or motion_digest(candidate) != sample_digest:
                                    raise ValueError('fixture write callback changed its source or grant')
                                report = validate_sample_write_report(report, sample)
                                if verify_transaction_boundary is not None:
                                    if verify_transaction_boundary() is not True:
                                        raise RuntimeError('fixture write boundary guard failed')
                                    check()
                                write_result = copy.deepcopy(report)
                                if pulse_client_once is not None:
                                    movement_sample = {'sequence':sample['sequence'], 'source_time':sample['source_time'],
                                                       'position':position, 'sample_sha256':sample_digest}
                        if sample['velocity'] is not None:
                            with_velocity[handle] += 1
                        record_progress(handle)
                        if sample['velocity'] is not None and len(evidence[handle]) < 2:
                            evidence[handle].append({key: copy.deepcopy(sample[key]) for key in
                                ('sequence', 'source_time', 'x', 'y', 'z', 'orientation', 'velocity', 'angular_rate')})
                    # Pump only after the pull releases the exclusive reader. Do not
                    # ask the world to advance an unexpected lifecycle operation.
                    check()
                    host.tick(0, defer_world=True)
                    check()
                if any(needs_sample(handle) for handle in handles):
                    check()
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
                    check()
            return resume_baseline
        collect_samples()
        rejoin_evidence = None
        if rejoin_callback is not None:
            check()
            old_handles = handles
            before_players = [{'connection_handle': handle, 'registry_index': selected[handle]['index'],
                               'sector_macro': selected[handle]['sector_macro'],
                               'samples': copy.deepcopy(evidence[handle])} for handle in handles]
            old_selected = copy.deepcopy(selected)
            old_owners = dict(owners)
            old_last = copy.deepcopy(last)
            # The callback must preserve the delivery and surviving owner, and
            # return only after the actual session has resumed with a new peer.
            transition = rejoin_callback(delivery, owners, create_owner)
            if type(transition) is not dict or set(transition) != {'departed', 'survivor', 'replacement', 'grant'}:
                raise ValueError('invalid fixture rejoin result')
            departed, survivor, replacement = (transition[name] for name in ('departed', 'survivor', 'replacement'))
            replacement_grant = grant_value(transition['grant'])
            if (set((departed, survivor)) != set(old_handles) or replacement in old_handles or
                    type(replacement) is not int or replacement <= 0 or
                    replacement_grant['index'] != old_selected[departed]['index'] or
                    replacement_grant['sector_macro'] != old_selected[departed]['sector_macro'] or
                    replacement_grant['session_epoch'] != epoch or
                    replacement_grant['token'] in {g['token'] for g in old_selected.values()} or
                    replacement_grant['incarnation'] in {g['incarnation'] for g in old_selected.values()} or
                    set(owners) != {survivor, replacement} or
                    owners[survivor] is not old_owners[survivor] or
                    owners[replacement] in old_owners.values()):
                raise ValueError('fixture rejoin identity differs')
            initial_players = world.session.players
            initial_ready = world.session.ready_players
            handles = tuple(sorted((survivor, replacement)))
            if tuple(handle for handle, _ in initial_players) != handles or initial_ready != initial_players:
                raise ValueError('fixture rejoin participant set differs')
            selected = {survivor: old_selected[survivor], replacement: replacement_grant}
            check()
            evidence = {handle: [] for handle in handles}
            last = {survivor: old_last[survivor], replacement: None}
            for mapping in (attempts, received, with_velocity, without_post_boundary):
                mapping[replacement] = 0
            record_progress(replacement)
            resume_baseline = collect_samples(resume_handle=survivor)
            rejoin_evidence = {'surviving_handle': survivor, 'replacement_handle': replacement,
                               'survivor_fresh_baseline_observed': True, 'survivor_baseline': resume_baseline,
                               'players_before_rejoin': before_players}
        if pulse_client_once is not None and (movement_baseline is None or movement_fence is None or movement_sample is None or throttle_receipt is None):
            raise RuntimeError('fixture client movement not confirmed')
        if sample_write_slot is not None and write_result is None:
            raise RuntimeError('fixture sample write not confirmed')
        if suspend_on_exit:
            check()
            if delivery.suspend_client_authority() is not True:
                raise RuntimeError('fixture authority suspension unconfirmed')
            check()
        return {'fixture_player_samples_observed': True,
                'players': [{'connection_handle': handle, 'registry_index': selected[handle]['index'],
                             'sector_macro': selected[handle]['sector_macro'], 'samples': evidence[handle]}
                            for handle in handles],
                'server_x4_motion_applied': False, 'native_velocity_proven': False,
                **({'server_position_once': write_result, 'server_x4_position_once_applied': True,
                    'source_to_server_frame_assumed': True, 'client_movement_proven': movement_sample is not None,
                    **({'client_movement': {'baseline':movement_baseline, 'fence':movement_fence, 'moved':movement_sample,
                                              'throttle':copy.deepcopy(throttle_receipt)}} if movement_sample is not None else {}),
                    'same_run_frame_proven': False, 'windows_frame_proven': False} if write_result is not None else {}),
                'cross_process_identity_proven': False, 'multiplayer_gameplay_proven': False,
                **({'rejoin': rejoin_evidence} if rejoin_evidence is not None else {})}
    except BaseException:
        delivery._failed = True
        host._failed = True
        raise
