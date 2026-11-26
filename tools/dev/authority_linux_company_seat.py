"""Install and observe the shared company-seat gate on an owned Linux client."""
import json
from pathlib import Path
import secrets
import shutil

from authority.checkpoints import Checkpoint
from authority.combat_capture import capture_config, capture_files
from authority.client_registry_overlay import extend_full_inspect, validate_full_snapshot
from authority.client_seat_overlay import (
    COMPANY_FLEET_MD, COMPANY_FLEET_UI, company_fleet_config,
    company_motion_config, company_motion_md, company_motion_ui,
    company_purchase_config, company_purchase_md, company_purchase_ui, export_motion_buffer, extend_seat_gate,
)
from authority.load_observations import BoundReceiptStream, MAX_LOG
from authority.registry_availability_evidence import verify_join_snapshot
import authority_marked_admission_evidence as files
import authority_recovered_client_registry as registry

ROOT = Path(__file__).resolve().parents[2]
_UI = {
    'authority_registry_availability_snapshot.lua': 'tools/dev/authority_registry_availability_snapshot.lua',
    'registry_checkpoint_gate.lua': 'src/authority/lua/registry_checkpoint_gate.lua',
    'client_registry_inspect.lua': 'src/authority/lua/client_registry_inspect.lua',
    'authority_registry_resolve_string.lua': 'tools/dev/authority_registry_resolve_string.lua',
    'client_seat.lua': 'src/authority/lua/client_seat.lua',
}
_MD = {
    'authority_registry_availability_snapshot.xml': 'tools/dev/authority_registry_availability_snapshot.xml',
    'client_seat.xml': 'src/authority/md/client_seat.xml',
    'authority_identity_fixture.xml': 'tools/dev/authority_identity_fixture.xml',
}


def install(case, profile, bound, target, index, pump, *, company_motion=None, company_purchase=None,
            company_fleet=None, software_rendered=False, combat_capture=False):
    """Verify the staged format-3 save and compose the gate before X4 starts."""
    offer = bound.offer
    checkpoint = Checkpoint(offer.checkpoint_id, target, offer.sha256, offer.size, bound.marker)
    saved = verify_join_snapshot(checkpoint, pump=pump)
    status = ('ready', saved['epoch'], saved['issued_count'], saved['mapping_sha256'])
    nonce = secrets.token_hex(16)
    full = validate_full_snapshot(saved, status, nonce, bound.marker)
    suffix = '' if company_motion is None else company_motion_config(company_motion, index, full)
    if company_purchase is not None:
        suffix += company_purchase_config(company_purchase, index, full)
    if combat_capture:
        suffix += capture_config(full, index, offer.checkpoint_id, offer.sha256)

    if company_fleet is not None:
        if company_purchase is None:
            raise ValueError('Company fleet orders require purchase control')
        suffix += company_fleet_config(company_fleet, full)
    paths = registry.install(case, nonce, status, load_gate='marker')

    lab = case / 'extensions/x4_lan_lab'
    ui, md = lab / 'ui', lab / 'md'
    owner = ui / 'authority_download_load_probe.lua'
    probe = extend_full_inspect(owner.read_text(encoding='utf-8'), software_rendered=software_rendered)
    owner.write_text(extend_seat_gate(probe, index=index, full_registry=full,
                                     company_motion=company_motion is not None,
                                     company_purchases=company_purchase is not None), encoding='utf-8')

    manifest = lab / 'ui.xml'
    body = manifest.read_text(encoding='utf-8')
    hook = '    <file name="ui/recovery_registry_status.lua" />\n'
    if body.count(hook) != 1:
        raise ValueError('unexpected registry module insertion point')
    ui_sources = _UI if company_motion is None else _UI | company_motion_ui(company_motion)
    md_sources = _MD if company_motion is None else _MD | company_motion_md(company_motion)
    if company_purchase is not None:
        ui_sources = ui_sources | company_purchase_ui(company_purchase)
        md_sources = md_sources | company_purchase_md(company_purchase)
    if company_fleet is not None:
        ui_sources = ui_sources | COMPANY_FLEET_UI
        md_sources = md_sources | COMPANY_FLEET_MD
    modules = ''.join(f'    <file name="ui/{name}" />\n' for name in ui_sources)
    if combat_capture:
        scripts = capture_files((ROOT / 'src/authority/md/combat_observer.xml').read_bytes(),
                                (ROOT / 'src/authority/lua/client_combat_capture.lua').read_bytes())
        for relative_path, content in scripts.items():
            destination = lab / relative_path
            with destination.open('xb') as output:
                output.write(content)
            paths.append(destination)
        modules += '    <file name="ui/client_combat_capture.lua" />\n'
    manifest.write_text(body.replace(hook, modules + hook), encoding='utf-8')

    for folder, sources in ((ui, ui_sources), (md, md_sources)):
        for name, original in sources.items():
            target_file = folder / name
            if name == 'motion_buffer.lua':
                target_file.write_bytes(export_motion_buffer((ROOT / original).read_bytes()))
            else:
                shutil.copyfile(ROOT / original, target_file)
            paths.append(target_file)
    config = ui / 'config.lua'
    text = config.read_text(encoding='ascii')
    if not text.endswith('}\n'):
        raise ValueError('unexpected seat configuration')
    settings = ','.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in full.items())
    config.write_text(text[:-2] + ',authority_registry_snapshot={' + settings + '}' + suffix + '}\n',
                      encoding='ascii')
    return paths, SeatWatch(profile / 'engine-output.log', bound, status, nonce, index)


class SeatWatch:
    """Validate the full ordered receipt stream from the same append-only log."""
    def __init__(self, path, bound, status, nonce, index):
        self.path = path
        self.registry_epoch = status[1]
        self.stage = 0
        self._identity = None
        self._snapshot = b''
        self._receipts = BoundReceiptStream(bound, slot='save_009', registry_status=status,
                                            registry_nonce=nonce, require_full_inspection=True,
                                            seat_index=index)

    def poll(self):
        try:
            fingerprint, _, snapshot = files._read_regular(self.path, MAX_LOG, collect=True)
        except FileNotFoundError:
            if self._identity is not None:
                raise ValueError('owned seat log disappeared')
            return False
        except files.EvidenceFileGrowing as growth:
            fingerprint, snapshot = growth.fingerprint, growth.partial
        identity = fingerprint[:2]
        if ((self._identity is not None and self._identity != identity)
                or not snapshot.startswith(self._snapshot)):
            raise ValueError('owned seat log changed')
        complete = self._receipts.feed(snapshot[len(self._snapshot):])
        self._identity, self._snapshot = identity, snapshot
        self.stage = 2 if complete else 0
        return complete

    def verify(self):
        if self.poll() is not True:
            raise ValueError('owned seat log incomplete')

    def allow_seat_handoff(self):
        self._receipts.allow_seat_handoff()

    @property
    def seat_at_admission(self):
        return self._receipts.seat_at_admission

    @property
    def seat_status(self):
        return self._receipts.seat_status
