"""One read-only checkpoint NPC on the owned recovered Linux provider."""
from collections import deque
from pathlib import Path
import os
import re
import secrets
import socket
import time

from authority.company_npc_measurement import NPC_CODE, NPC_INDEX, REGISTRY_EPOCH, NpcMeasurementIngress
from authority.experimental_tcp_motion import check_motion_progress, motion_value
from authority_tcp_job_client_live import socket_owner


ROOT = Path(__file__).resolve().parents[2]
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_OWNER = re.compile(r'[a-z][a-z0-9_]{0,63}')
_COMPONENT = re.compile(r'\[(0x[0-9a-f]{1,16})\]')
_TOKEN = re.compile(r'[0-9a-f]{32}')
_DESCRIPTOR = 'authority_job_descriptor_probe.xml'
_MODULES = ('tools/dev/authority_registry_resolve_string.lua',
            'tools/dev/authority_job_descriptor_probe.lua', 'src/authority/lua/saved_baseline_source_bootstrap.lua',
            'src/authority/lua/source_motion_sampler.lua',
            'tools/dev/authority_registered_npc_source.lua')
_MAX_AGE = 2.0


class NpcCheckpointTarget:
    """Select a single matching row during the confirmed format-3 save scan."""

    def __init__(self, index, code):
        if (type(index) is not str or not re.fullmatch(r'[1-9][0-9]{0,5}', index)
                or not 1 <= int(index) <= 200000 or type(code) is not str or not _CODE.fullmatch(code)):
            raise ValueError('registered NPC requires one index and exact X4 ship code')
        self.index, self.code = int(index), code
        self._saved = None
        self._invalid = False

    def observe(self, element, _depth, attrs, index):
        if element != 'component' or index != self.index:
            return
        kind, owner = attrs.get('class', ''), attrs.get('owner', '')
        ref = _COMPONENT.fullmatch(attrs.get('id', ''))
        if (self._saved is not None or (kind != 'ship' and not kind.startswith('ship_'))
                or owner == 'player' or not _OWNER.fullmatch(owner) or attrs.get('code') != self.code
                or ref is None or not int(ref[1], 16)):
            self._invalid = True
            return
        self._saved = ref[1], owner

    def confirmed(self, snapshot):
        epoch, count, run_id = snapshot['epoch'], snapshot['issued_count'], snapshot['run_id']
        if self._invalid:
            raise ValueError('registered NPC differs from the requested saved identity')
        if (self._saved is None or not _TOKEN.fullmatch(epoch) or not _TOKEN.fullmatch(run_id)
                or not self.index <= count <= 200000
                or snapshot.get('source_registry_checked') is not True
                or any(snapshot.get(bits, '')[self.index - 1:self.index] != '1'
                       for bits in ('availability_bits', 'operational_bits'))):
            raise ValueError('registered NPC unavailable in the confirmed checkpoint')
        actor, owner = self._saved
        return {'epoch': epoch, 'index': self.index, 'code': self.code, 'actor': actor,
                'owner': owner, 'count': count, 'run_id': run_id}


class RegisteredNpcSource:
    """Read process-owned native samples and feed one server-owned publication."""

    def __init__(self, target, *, clock=time.monotonic, trial_measurement=False):
        if type(trial_measurement) is not bool or (trial_measurement and
                (target['epoch'], target['index'], target['code']) != (REGISTRY_EPOCH, NPC_INDEX, NPC_CODE)):
            raise ValueError('private measurement requires the confirmed MFQ-419 provider binding')
        self._trial_measurement = trial_measurement
        self._measurement = None
        self.target, self._clock = dict(target), clock
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._lease, self._generation, self._nonce, self._lookup_nonce = (secrets.token_hex(16) for _ in range(4))
        self._prefix = f'N|{self._lease}|{self._generation}|'.encode('ascii')
        self._process = self._peer = self._binding = self._previous = None
        self._descriptor = self._runtime_ref = None
        self._previous_poll_time = self._end_time = None
        self._longest_poll_gap = 0.0
        self._stale_samples = 0
        self._join_checkpoint = self._join_component = None
        self._running_seen = False
        self._service = self._reservation_guard = self._end_reason = None
        self._samples = self._velocity_samples = 0
        self._initial_publications = []
        self._recent_publications = deque(maxlen=32)

    def join_candidate(self):
        if not self._running_seen:
            return NpcCheckpointTarget(str(self.target['index']), self.target['code'])
        return None

    def confirm_join_checkpoint(self, checkpoint, snapshot, candidate):
        """Keep a selection only while an exact, independently verified offer backs it."""
        if self._running_seen:
            return
        self._join_checkpoint = self._join_component = None
        try:
            saved = candidate.confirmed(snapshot)
        except ValueError:
            return
        if (saved['run_id'] == checkpoint.marker and saved['run_id'] != self.target['run_id']
                and all(saved[key] == self.target[key] for key in ('epoch', 'index', 'code', 'owner'))):
            self._join_checkpoint = checkpoint
            self._join_component = f"[0x{int(saved['actor'], 16):x}]"

    def reserved_or_unverified(self, checkpoint, reserved):
        return (self._join_checkpoint is None or self._join_checkpoint != checkpoint
                or any(ship.ship_index == self.target['index'] or ship.component_ref == self._join_component
                       for ship in reserved))

    def install(self, case):
        lab = Path(case).absolute() / 'extensions/x4_lan_lab'
        ui, md = lab / 'ui', lab / 'md'
        bridge, manifest = ui / 'authority_world_bridge_probe.lua', lab / 'ui.xml'
        if (self._descriptor is not None or lab.resolve(strict=True) != lab or lab.stat().st_uid != os.geteuid()
                or any(path.resolve(strict=True) != path for path in (ui, md, bridge, manifest))):
            raise ValueError('NPC source requires the private recovery overlay')
        owner, modules = bridge.read_text(encoding='ascii'), manifest.read_text(encoding='ascii')
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        anchor = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        if owner.count(tick) != 1 or 'local registry_checkpoint' not in owner or modules.count(anchor) != 1:
            raise ValueError('NPC source requires the one registry-aware recovery update')
        scripts = [(ROOT / name, ui / Path(name).name) for name in _MODULES]
        descriptor = md / _DESCRIPTOR
        scripts.append((ROOT / 'tools/dev' / _DESCRIPTOR, descriptor))
        resolver_source, resolver = scripts[0]
        shared_entry = f'    <file name="ui/{resolver.name}" />\n'
        if resolver.is_file() and not resolver.is_symlink() and resolver.read_bytes() == resolver_source.read_bytes():
            if modules.count(shared_entry) != 1:
                raise ValueError('existing NPC resolver is not uniquely staged')
            scripts.pop(0)
        if any(path.exists() or path.is_symlink() for _, path in scripts):
            raise FileExistsError('NPC source scripts already present')
        for source, path in scripts:
            with path.open('xb') as output:
                output.write(source.read_bytes())
        self._descriptor = descriptor
        target = self.target
        hook = f''' if not registered_npc_started and registry_checkpoint:admission_ready()
  and registry_checkpoint:state().current_run_id ~= "{target['run_id']}" then
  registered_npc_started = true
  local initialized, advance = pcall(function()
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   return X4LAN_REGISTERED_NPC_SOURCE.new_x4({{
    epoch="{target['epoch']}", index={target['index']}, code="{target['code']}", owner="{target['owner']}",
    count={target['count']}, lease="{self._lease}", generation="{self._generation}",
    nonce="{self._nonce}", lookup_nonce="{self._lookup_nonce}", save_path=cfg.authority_load_save_path,
   }}, assert(lib.open('127.0.0.1',{self._socket.getsockname()[1]})), lib.monotonic,
    function() return registry_checkpoint:admission_ready() end)
  end)
  if initialized then
   registered_npc_tick = advance
  else
   DebugError('[X4LAN] AUTH_REGISTERED_NPC_SOURCE END|initialize_failed')
  end
 end
 if registered_npc_tick then registered_npc_tick() end
'''
        bridge.write_text('local registered_npc_started, registered_npc_tick\n' + owner.replace(tick, tick + hook),
                          encoding='ascii')
        entries = ''.join(f'    <file name="ui/{path.name}" />\n' for _, path in scripts[:-1])
        manifest.write_text(modules.replace(anchor, entries + anchor), encoding='ascii')
        return [*(path for _, path in scripts), bridge, manifest]

    def archive_cue(self, out):
        """Remove the read-only cue from the recovery overlay after X4 is gone."""
        path = self._descriptor
        if (path is None or path.resolve(strict=True) != path
                or path.read_bytes() != (ROOT / 'tools/dev' / _DESCRIPTOR).read_bytes()):
            raise ValueError('registered NPC descriptor changed')
        os.link(path, Path(out) / path.name, follow_symlinks=False)
        path.unlink()

    def bind(self, process):
        if self._process is not None or type(process) is not dict or process.keys() != {
                'pid', 'start_ticks', 'exe_dev', 'exe_ino'}:
            raise ValueError('one owned NPC source process required')
        self._process = dict(process)

    def attach(self, service, reservation_guard, *, on_measurement=None):
        if self._service is not None or not callable(reservation_guard):
            raise ValueError('one NPC publication guard required')
        if on_measurement is not None and not self._trial_measurement:
            raise ValueError('NPC measurement is not enabled')
        if self._trial_measurement:
            ingress = NpcMeasurementIngress(
                reservation_guard, on_measurement if on_measurement is not None else lambda _row: None)
            service.install_npc_measurement(ingress)
            self._measurement = ingress
        self._service, self._reservation_guard = service, reservation_guard

    def _withdraw(self, reason):
        self._end_reason = reason
        self._end_time = self._clock()
        if self._binding is not None:
            self._service.withdraw_npc_source(self._binding)
            self._binding = None

    def _decode(self, fields):
        _, epoch, index, sequence, code, owner, actor, sector, source, capture, x, y, z, vx, vy, vz = fields
        target = self.target
        if ((epoch, index, code, owner) != (target['epoch'], str(target['index']), target['code'], target['owner'])
                or not re.fullmatch(r'[1-9][0-9]{0,15}', sequence)
                or not re.fullmatch(r'0[xX][0-9a-fA-F]{1,16}', actor) or not int(actor, 16)):
            raise ValueError('NPC sample identity changed')
        runtime_ref = f'0x{int(actor, 16):x}'
        if self._runtime_ref is not None and self._runtime_ref != runtime_ref:
            raise ValueError('NPC runtime component changed')
        velocity = None if (vx, vy, vz) == ('-', '-', '-') else dict(zip('xyz', map(float, (vx, vy, vz))))
        sample = motion_value({'version': 3, 'epoch': epoch, 'index': target['index'], 'generation': self._generation,
                               'sequence': int(sequence), 'sector_macro': sector, 'source_time': float(source),
                               'capture_time': float(capture), 'x': float(x), 'y': float(y), 'z': float(z),
                               'real_velocity': velocity})
        if self._previous is not None and sample['sequence'] <= self._previous['sequence']:
            raise ValueError('NPC source sequence regressed')
        check_motion_progress(sample, self._previous)
        return sample, runtime_ref

    def poll(self):
        if self._end_reason is not None:
            return
        service = self._service
        if not service.running:
            if self._running_seen:
                self._withdraw('motion_window_ended')
            return
        self._running_seen = True
        now = self._clock()
        if self._previous_poll_time is not None:
            self._longest_poll_gap = max(self._longest_poll_gap, now - self._previous_poll_time)
        self._previous_poll_time = now
        try:
            unreserved = self._reservation_guard() is True
        except (OSError, RuntimeError, ValueError):
            unreserved = False
        if not unreserved:
            return self._withdraw('source_guard_failed')

        samples, checked_peer = [], None
        for _ in range(32):
            try:
                packet, peer = self._socket.recvfrom(513)
            except BlockingIOError:
                break
            except OSError:
                return self._withdraw('source_socket_failed')
            if not packet.startswith(self._prefix) or self._peer not in (None, peer):
                continue
            if checked_peer != peer:
                try:
                    if not socket_owner(peer, self._socket.getsockname(), self._process):
                        if self._peer is not None:
                            return self._withdraw('peer_ownership_lost')
                        continue
                except (OSError, RuntimeError, ValueError):
                    return self._withdraw('peer_ownership_lost')
                self._peer = checked_peer = peer
            if packet == self._prefix + b'END':
                return self._withdraw('native_source_ended')
            try:
                fields = packet[len(self._prefix):].decode('ascii').split('|')
                if len(fields) == 2 and fields[0] == 'END' and re.fullmatch(r'[a-z][a-z_]{0,63}', fields[1]):
                    return self._withdraw('native_' + fields[1])
                if len(packet) > 512 or len(fields) != 16 or fields[0] != 'SAMPLE3':
                    raise ValueError('invalid NPC frame')
                sample, runtime_ref = self._decode(fields)
            except (ValueError, OverflowError):
                return self._withdraw('invalid_native_sample')
            capture = sample['capture_time']
            if capture > self._clock():
                return self._withdraw('sample_clock_or_age_invalid')
            if self._previous is not None and capture - self._previous['capture_time'] >= _MAX_AGE:
                return self._withdraw('source_expired')
            self._previous, self._runtime_ref = sample, runtime_ref
            samples.append(sample)
        # A late polling tick can contain older frames followed by a current one.
        # Validate the entire sequence, but only publish captures still fresh now.
        drain_time = self._clock()
        fresh = [sample for sample in samples if drain_time - sample['capture_time'] < _MAX_AGE]
        self._stale_samples += len(samples) - len(fresh)
        if not fresh:
            if self._previous is not None and drain_time - self._previous['capture_time'] >= _MAX_AGE:
                self._withdraw('source_expired')
            return
        if self._binding is None:
            try:
                self._binding = service.register_npc_source(self.target['epoch'], self.target['index'],
                    self.target['code'], reservation_guard=self._reservation_guard)
            except (OSError, RuntimeError, ValueError):
                return self._withdraw('publication_refused')
        # Keep the first pose in each sector so the service sees its no-rate baseline.
        for original in fresh:
            sample = dict(original, generation=self._binding.generation)
            try:
                accepted = service.publish_npc_sample(self._binding, sample)
            except (OSError, RuntimeError, ValueError):
                return self._withdraw('publication_rejected')
            if not accepted:
                return self._withdraw('publication_lost')
            self._samples += 1
            self._velocity_samples += int(sample['real_velocity'] is not None)
            if len(self._initial_publications) < 64:
                self._initial_publications.append(sample)
            self._recent_publications.append(sample)

    def stats(self):
        result = {'epoch': self.target['epoch'], 'ship_index': self.target['index'], 'ship_code': self.target['code'],
                'owner': self.target['owner'], 'saved_component_ref': self.target['actor'],
                'join_component_ref': self._join_component, 'runtime_component_ref': self._runtime_ref,
                'join_checkpoint_id': self._join_checkpoint.checkpoint_id if self._join_checkpoint else None,
                'samples': self._samples, 'stale_samples_discarded': self._stale_samples,
                'longest_running_poll_gap': self._longest_poll_gap, 'end_monotonic_time': self._end_time,
                'initial_publications': list(self._initial_publications),
                'samples_with_velocity': self._velocity_samples, 'last_native_sample': self._previous,
                'recent_publications': list(self._recent_publications), 'end_reason': self._end_reason}

        if self._measurement is not None:
            result['trial_measurement'] = {
                'accepted': self._measurement.accepted,
                'end_reason': self._measurement.retired_reason,
            }
        return result

    def close(self):
        try:
            if self._end_reason is None:
                self._withdraw('provider_closed')
            if self._measurement is not None:
                self._measurement.retire('provider_closed')
        finally:
            self._socket.close()
