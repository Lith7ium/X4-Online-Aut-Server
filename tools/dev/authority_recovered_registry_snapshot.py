"""Saved-byte and bridge-receipt guard for opt-in format-3 recovery.

This module does not launch X4, publish a checkpoint or confirm admission.
The caller owns source provenance, the game process and save lifecycle.
"""
from pathlib import Path
import secrets

from authority.registry_availability_evidence import verify_join_snapshot
import authority_registry_checkpoint_install as bridge_gate

ROOT = Path(__file__).resolve().parents[2]


def validate_mode(args):
    required = getattr(args, 'require_registry_snapshot', False)
    bootstrap = getattr(args, 'bootstrap_registry_snapshot', False)
    if type(required) is not bool:
        raise ValueError('invalid registry snapshot option')
    if type(bootstrap) is not bool or (required and bootstrap):
        raise ValueError('registry snapshot bootstrap and recovery modes conflict')
    if bootstrap and any(value for name, value in vars(args).items()
                         if name not in ('bootstrap_registry_snapshot', 'require_registry_snapshot')
                         and name.startswith(('diagnostic_', 'fixture_', 'registry_'))):
        raise ValueError('registry snapshot bootstrap requires one normal recovered server owner')
    if required and any(value for name, value in vars(args).items()
                        if name != 'require_registry_snapshot'
                        and name.startswith(('diagnostic_', 'fixture_', 'registry_'))):
        raise ValueError('registry snapshot requires the normal recovered server without diagnostic owners')
    return required


def receipt(checkpoint, evidence):
    """Compact identity for a confirmed saved snapshot, reusable by bootstrap."""
    if evidence.get('source_registry_checked') is not True or evidence.get('format') != 3:
        raise ValueError('verified format-3 source registry required')
    return {
        'checkpoint_id': checkpoint.checkpoint_id,
        'sha256': checkpoint.sha256,
        'run_id': checkpoint.marker,
        'epoch': evidence['epoch'],
        'issued_count': evidence['issued_count'],
        'availability_sha256': evidence['availability_sha256'],
        'operational_sha256': evidence['operational_sha256'],
        'mapping_sha256': evidence['mapping_sha256'],
    }


def verify_previous_receipt(previous_result, checkpoint, evidence):
    expected = receipt(checkpoint, evidence)
    record = previous_result.get('registry_snapshot') if type(previous_result) is dict else None
    if (type(record) is not dict or not (
            (record.get('required') is True and record.get('initial_inspect_verified') is True)
            or (record.get('bootstrap') is True and record.get('first_capture_verified') is True))
            or record.get('final_receipt') != expected):
        raise ValueError('previous supervisor has no matching verified final registry receipt')
    return expected


def verify_bootstrap_status(previous_result, checkpoint, saved_status):
    """Require a confirmed prior supervisor's ready registry, without snapshot."""
    prior = previous_result.get('registry_status') if type(previous_result) is dict else None
    if (type(saved_status) is not tuple or len(saved_status) != 4 or saved_status[0] != 'ready'
            or type(prior) is not dict or prior.get('state') != 'ready'
            or prior.get('epoch') != saved_status[1] or prior.get('count') != saved_status[2]
            or prior.get('mapping_sha256') != saved_status[3]
            or prior.get('checkpoint_id') != checkpoint.checkpoint_id):
        raise ValueError('confirmed source ready registry differs from prior supervisor receipt')
    return saved_status


def verify_transition(previous, current, *, appended_indices=()):
    """No ID revival; operational observations may change independently."""
    old_count = previous['issued_count']
    if appended_indices:
        if (appended_indices != (old_count + 1, old_count + 2)
                or current['availability_bits'][old_count:] != '11'
                or current['operational_bits'][old_count:] != '11'):
            raise ValueError('shuttle transition requires exactly two new operational bindings')
    if (current['epoch'] != previous['epoch'] or
            current['issued_count'] != old_count + len(appended_indices) or
            current['run_id'] == previous['run_id'] or
            any(old == '0' and new == '1' for old, new in zip(
                previous['availability_bits'], current['availability_bits']))):
        raise ValueError('saved registry epoch/count or missing-binding lifetime changed')


def _install_source_registry(case, read_source):
    path = Path(case) / 'extensions/x4_lan_lab/md/authority_ship_registry.xml'
    with path.open('xb') as output:
        output.write(read_source(ROOT / 'tools/dev/authority_ship_registry.xml'))
    return path


class RegistrySnapshotState:
    """Track one recovered source and the latest fully verified saved run."""

    def __init__(self, source_checkpoint, source_evidence):
        self.latest_evidence = source_evidence
        self.latest_receipt = receipt(source_checkpoint, source_evidence)
        self.source_receipt = dict(self.latest_receipt)
        self.nonce = None
        self.initial_inspect_verified = False
        self.save_count = 0

    def install(self, case, *, reserved_tokens, read_source, timeout=110):
        if self.nonce is not None:
            raise ValueError('registry gate already installed')
        registry_md = _install_source_registry(case, read_source)
        reserved = set(reserved_tokens) | {self.latest_evidence['epoch'], self.latest_evidence['run_id']}
        nonce = secrets.token_hex(16)
        while nonce in reserved:
            nonce = secrets.token_hex(16)
        protected = bridge_gate.install(case, nonce, self.latest_evidence, timeout=timeout)
        self.nonce = nonce
        return [registry_md, *protected]

    def verify_initial(self, log_path):
        bridge_gate.verify_bridge_observation(log_path, self.nonce, self.latest_evidence)
        self.initial_inspect_verified = True

    def verify_save(self, checkpoint, log_path, *, appended_indices=()):
        if not self.initial_inspect_verified:
            raise ValueError('initial registry inspection not verified')
        current = verify_join_snapshot(checkpoint)
        verify_transition(self.latest_evidence, current, appended_indices=appended_indices)
        bridge_gate.verify_bridge_observation(log_path, self.nonce, current,
                                              previous_run_id=self.latest_evidence['run_id'])
        self.latest_evidence = current
        self.latest_receipt = receipt(checkpoint, current)
        self.save_count += 1
        return dict(self.latest_receipt)

    def metadata(self):
        return {'required': True, 'source_receipt': dict(self.source_receipt),
                'initial_inspect_verified': self.initial_inspect_verified,
                'verified_save_count': self.save_count,
                'final_receipt': dict(self.latest_receipt) if self.save_count else None,
                'native_load_bytes_proven': False}


class FirstCaptureState:
    """One save from a confirmed ready registry, without claiming an inspect."""

    def __init__(self, source_checkpoint, saved_status):
        if type(saved_status) is not tuple or len(saved_status) != 4 or saved_status[0] != 'ready':
            raise ValueError('ready saved registry required for first capture')
        self.source_checkpoint = source_checkpoint
        self.status = saved_status
        self.nonce = None
        self.final_receipt = None

    def install(self, case, *, reserved_tokens, read_source, timeout=110):
        if self.nonce is not None:
            raise ValueError('first capture already installed')
        registry_md = _install_source_registry(case, read_source)
        reserved = set(reserved_tokens) | {
            self.source_checkpoint.marker, self.status[1]}
        nonce = secrets.token_hex(16)
        while nonce in reserved:
            nonce = secrets.token_hex(16)
        protected = bridge_gate.install_first_capture(case, nonce,
            source_marker=self.source_checkpoint.marker, epoch=self.status[1],
            issued_count=self.status[2], timeout=timeout)
        self.nonce = nonce
        return [registry_md, *protected]

    def verify_save(self, checkpoint, log_path):
        if self.final_receipt is not None:
            raise ValueError('first registry capture already verified')
        record = self.recheck(checkpoint, log_path)
        self.final_receipt = record
        return dict(record)

    def recheck(self, checkpoint, log_path):
        evidence = verify_join_snapshot(checkpoint)
        if evidence['epoch'] != self.status[1] or evidence['issued_count'] != self.status[2]:
            raise ValueError('first snapshot epoch or issued count differs from source registry')
        bridge_gate.verify_bridge_observation(log_path, self.nonce, evidence,
            first_capture_source_marker=self.source_checkpoint.marker)
        record = receipt(checkpoint, evidence)
        if self.final_receipt is not None and record != self.final_receipt:
            raise ValueError('first capture receipt changed before confirmation')
        return record

    def metadata(self):
        return {'bootstrap': True, 'source_checkpoint_id': self.source_checkpoint.checkpoint_id,
                'source_sha256': self.source_checkpoint.sha256,
                'source_marker': self.source_checkpoint.marker,
                'epoch': self.status[1], 'issued_count': self.status[2],
                'first_capture_verified': self.final_receipt is not None,
                'initial_inspect_verified': False,
                'final_receipt': None if self.final_receipt is None else dict(self.final_receipt),
                'no_client_admission': True, 'native_load_bytes_proven': False}
