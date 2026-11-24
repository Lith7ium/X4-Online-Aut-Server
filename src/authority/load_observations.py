"""Parse complete cooperative client-load log snapshots without OS access.

Matching text is not proof of its origin, the bytes X4 loaded, or admission.
The caller owns process and log provenance, freshness and file continuity.
"""
from .load_messages import Offer
from .trusted_load_messages import encode_bound_offer
from .client_registry_overlay import registry_receipt
from .client_seat_overlay import REFUSALS, SEAT_REFUSED_TAG, SEAT_TAG, seat_prefix

MAX_LOG = 256 << 20
MAX_LINE = 65536
LOAD_TAG = b'[X4LAN] AUTH_DOWNLOAD_LOAD '
MARKER_TAG = b'AUTH_MARKER '
REGISTRY_TAG = b'[X4LAN] AUTH_RECOVERY_REGISTRY '
FULL_INSPECT_TAG = b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT '
TICKET_FIELDS = ('world_epoch', 'connection_handle', 'baseline_id', 'load_generation',
                 'checkpoint_id', 'sha256', 'attempt_id', 'slot')
STREAM_FAULTS = (b'auth_participant error', b'auth_identity load error',
                 b'auth_recovered_client_motion error', b'auth_client_playback failure')


def validate_client_receipts(data, ticket, marker):
    """Require exactly request, inspected marker and observed pause, in order.

    ``ticket`` and ``marker`` are caller-supplied expected values, not verified
    credentials. Other log lines may be present. The result authorizes nothing.
    """
    if type(data) is not bytes or len(data) > MAX_LOG or (data and not data.endswith(b'\n')):
        raise ValueError('client log incomplete or oversized')
    fields = '|'.join(str(ticket[key]) for key in TICKET_FIELDS)
    expected = [
        ('load', ('load_requested|' + fields).encode('ascii')),
        ('marker', b'load INSPECTED ' + marker.encode('ascii')),
        ('load', ('pause_observed|' + fields).encode('ascii')),
    ]
    stage = 0
    for line in data.splitlines():
        if len(line) > MAX_LINE:
            raise ValueError('client log line too long')
        rows = []
        if LOAD_TAG in line:
            rows.append(('load', line.split(LOAD_TAG, 1)[1].strip()))
        if MARKER_TAG in line:
            rows.append(('marker', line.split(MARKER_TAG, 1)[1].strip()))
        for row in rows:
            if stage >= len(expected) or row != expected[stage]:
                raise ValueError('load and marker observations differ or are out of order')
            stage += 1
    if stage != len(expected):
        raise ValueError('load and marker observations differ or are out of order')


def validate_bound_offer_receipts(data, bound, *, slot):
    """Check logged fields against one marker-bound offer and local save slot.

    Offer name and size are not present in these log messages. The caller must
    independently establish those, and must correlate the snapshot to its X4.
    """
    encode_bound_offer(bound)
    # Revalidate fields even if a frozen dataclass was modified by its owner.
    offer = Offer(**{name: getattr(bound.offer, name) for name in Offer.__dataclass_fields__})
    if (type(slot) is not str or len(slot) != 8 or not slot.startswith('save_') or
            not slot[5:].isascii() or not slot[5:].isdigit()):
        raise ValueError('invalid local save slot')
    ticket = {name: getattr(offer, name) for name in TICKET_FIELDS if name != 'slot'}
    ticket['slot'] = slot
    return validate_client_receipts(data, ticket, bound.marker)


class BoundReceiptStream:
    """Incrementally check one bounded, ordered offer log and later records.

    The caller must supply consecutive bytes from the same owned log. A
    completed sequence is not admission or proof of X4/process provenance.
    """

    def __init__(self, bound, *, slot, registry_status=None, registry_nonce=None,
                 require_full_inspection=False, seat_index=None, on_stage=None):
        encode_bound_offer(bound)
        offer = Offer(**{name: getattr(bound.offer, name) for name in Offer.__dataclass_fields__})
        if (type(slot) is not str or len(slot) != 8 or not slot.startswith('save_') or
                not slot[5:].isascii() or not slot[5:].isdigit()):
            raise ValueError('invalid local save slot')
        ticket = {name: getattr(offer, name) for name in TICKET_FIELDS if name != 'slot'}
        ticket['slot'] = slot
        fields = '|'.join(str(ticket[key]) for key in TICKET_FIELDS)
        self._expected = [
            ('load', ('load_requested|' + fields).encode('ascii')),
            ('marker', b'load INSPECTED ' + bound.marker.encode('ascii')),
        ]
        self._registry = registry_status is not None or registry_nonce is not None
        if type(require_full_inspection) is not bool:
            raise ValueError('invalid full inspection option')
        self._full_inspection = require_full_inspection
        if self._registry:
            header = registry_receipt(registry_nonce, registry_status)
            if require_full_inspection:
                if registry_status[0] != 'ready':
                    raise ValueError('full inspection requires an initialized registry')
                inspected = f'{bound.marker} {registry_status[2]}'.encode('ascii')
                self._expected.append(('full_inspect', inspected))
            self._expected.append(('registry', header))
        elif require_full_inspection:
            raise ValueError('full inspection requires a registry header')
        self._seat_prefix = None
        if seat_index is not None:
            if not require_full_inspection:
                raise ValueError('client seat requires full registry inspection')
            self._seat_prefix = seat_prefix(seat_index, registry_nonce, registry_status)
            self._expected.append(('seat', self._seat_prefix + b'controlled'))
        self._expected.append(('load', ('pause_observed|' + fields).encode('ascii')))
        self._on_stage = on_stage
        self._seat_index = seat_index
        self._stage = 0
        self._carry = b''
        self._size = 0
        self._failed = False

    def feed(self, data):
        """Return true only after all records and the current line complete."""
        if self._failed:
            raise RuntimeError('load observation stream failed')
        try:
            if type(data) is not bytes or self._size + len(data) > MAX_LOG:
                raise ValueError('client log incomplete or oversized')
            self._size += len(data)
            lines = (self._carry + data).split(b'\n')
            self._carry = lines.pop()
            if len(self._carry) > MAX_LINE:
                raise ValueError('client log line too long')
            for line in lines:
                if len(line) > MAX_LINE:
                    raise ValueError('client log line too long')
                lowered = line.lower()
                if any(marker in lowered for marker in STREAM_FAULTS):
                    raise ValueError('client script reported an error')
                if self._registry and (b'auth_recovery_registry_error' in lowered or
                        b'event: x4authorityshipregistry.correlated' in lowered):
                    raise ValueError('client registry reported an error')
                if self._full_inspection and b'auth_registry_full_inspect_error' in lowered:
                    raise ValueError('client reported a full registry inspection error')
                rows = []
                if self._seat_prefix is not None:
                    if b'auth_download_load error' in lowered:
                        raise ValueError('client seat load owner failed')
                    if SEAT_REFUSED_TAG in line:
                        value = line.split(SEAT_REFUSED_TAG, 1)[1].strip()
                        if not value.startswith(self._seat_prefix):
                            raise ValueError('client seat refusal differs from selection')
                        reason = value[len(self._seat_prefix):]
                        if reason not in REFUSALS:
                            raise ValueError('invalid client seat refusal')
                        raise ValueError('client seat refused: ' + reason.decode('ascii'))
                    if SEAT_TAG in line:
                        rows.append(('seat', line.split(SEAT_TAG, 1)[1].strip()))
                if LOAD_TAG in line:
                    rows.append(('load', line.split(LOAD_TAG, 1)[1].strip()))
                if MARKER_TAG in line:
                    rows.append(('marker', line.split(MARKER_TAG, 1)[1].strip()))
                if self._full_inspection and FULL_INSPECT_TAG in line:
                    rows.append(('full_inspect', line.split(FULL_INSPECT_TAG, 1)[1].strip()))
                if self._registry and REGISTRY_TAG in line:
                    rows.append(('registry', line.split(REGISTRY_TAG, 1)[1].strip()))
                for row in rows:
                    if self._stage >= len(self._expected) or row != self._expected[self._stage]:
                        raise ValueError('load and marker observations differ or are out of order')
                    self._stage += 1
                    if self._on_stage is not None:
                        if row[0] == 'marker':
                            self._on_stage('world_marker_confirmed')
                        elif row[0] == 'seat':
                            self._on_stage('cockpit_control_confirmed', self._seat_index)
            return self._stage == len(self._expected) and not self._carry
        except BaseException:
            self._failed = True
            raise
