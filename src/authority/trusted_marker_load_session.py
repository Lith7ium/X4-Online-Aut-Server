"""Opt-in marker-bound load claims from trusted cooperative clients.

No listener, transport authentication, X4 process or save loader is provided.
The caller must exclusively own and serialize the coordinator, its store and
offer service, and bind each opaque connection object to the intended live
transport. A claim is an assertion by that trusted client, not independent
proof of bytes read, a continuous pause, or a consistent game world.
"""
from bisect import bisect_right
from dataclasses import dataclass
from itertools import islice
import re
import threading

from .company_ships import CompanyShip, CompanyShipCatalogue, read_company_ship_catalogue
from .experimental_load_session import ExperimentalLoadSession
from .experimental_client_load import HonestClientLoadClaim
from .load_messages import MAX_INTEGER, Offer, Receipt
from .marker_evidence import verify_saved_marker

_MARKER = re.compile(r'[0-9a-f]{32}\Z')
COMPANY_PILOT_LIMIT = 4


class StaleShipChoice(ValueError):
    """The supplied connection and attempt do not have a current offer."""


class CompanyPilotCapacity(ValueError):
    """All Company pilot places are occupied by active or joining clients."""


class CompanyShipUnavailable(ValueError):
    """The requested ship cannot be reserved by this attempt."""


class CompanyShipCatalogUnavailable(ValueError):
    """The current checkpoint could not provide verified company ships."""


@dataclass(frozen=True)
class MarkerBoundOffer:
    """Offer and expected marker from the same confirmed checkpoint."""
    offer: Offer
    marker: str


@dataclass(frozen=True)
class MarkerLoadOutcome:
    """The coordinator accepted a trusted claim, not a byte-level proof."""
    attempt_id: str
    ready_under_trusted_client_assumption: bool = True
    exact_loaded_bytes_proven: bool = False
    client_authenticated: bool = False
    motion_ship: CompanyShip | None = None
    registry_epoch: str | None = None


class TrustedMarkerLoadSession:
    """Experimental single-owner adapter; never construct without explicit opt-in.

    Methods serialize this wrapper only. Calls directly into its underlying
    coordinator or offer service, filesystem mutations, and transport identity
    verification remain the exclusive caller's responsibility. A failure after
    a possible mutation faults the wrapper; the owner must fence outstanding
    operations and retire or safely settle the coordinator and offer service.
    """
    def __init__(self, world, *, enabled=False, marker_script='X4AuthorityCheckpointMarker',
                 company_ship_reader=None, require_company_ship=False):
        if enabled is not True:
            raise ValueError('trusted marker session requires explicit opt-in')
        if marker_script not in ('X4AuthorityCheckpointMarker', 'X4AuthorityPlayerFixtureMarker'):
            raise ValueError('unsupported marker script')
        self._inner = ExperimentalLoadSession(world)
        self._world = world
        self._lock = threading.Lock()
        self._connections = {}
        self._markers = {}
        self._next_peer = 1
        self._failed = False
        self._marker_script = marker_script
        self._verified_marker_checkpoint = None
        if company_ship_reader is not None and not callable(company_ship_reader):
            raise ValueError('company ship reader must be callable')
        if type(require_company_ship) is not bool:
            raise ValueError('invalid company ship admission policy')
        self._require_company_ship = require_company_ship
        self._company_ship_reader = company_ship_reader
        self._ship_catalog = None
        self._pending_ships = {}
        self._admitted_ships = {}

    def _live(self):
        if self._failed or self._inner._failed:
            raise RuntimeError('trusted marker session faulted')

    def _peer(self, connection):
        entry = self._connections.get(id(connection))
        if entry is None or entry[0] is not connection:
            raise ValueError('unknown or stale connection context')
        return entry[1]

    def attach(self, connection):
        """Attach one caller-owned, identity-stable live connection context."""
        with self._lock:
            self._live()
            if connection is None or isinstance(connection, (str, bytes, int, float, bool, tuple, frozenset)):
                raise ValueError('opaque connection object required')
            if id(connection) in self._connections:
                raise ValueError('connection already attached')
            # Starting the offer flow reserves a place before it can request a
            # world pause. Connection probes do not call attach().
            if self._require_company_ship and len(self._connections) >= COMPANY_PILOT_LIMIT:
                raise CompanyPilotCapacity('Company pilot capacity reached')
            peer = 'trusted-marker-' + str(self._next_peer)
            self._next_peer += 1
            try:
                handle = self._inner.attach(peer)
                self._connections[id(connection)] = (connection, peer)
            except BaseException:
                self._failed = True
                raise
            return handle

    def disconnect(self, connection):
        with self._lock:
            self._live()
            peer = self._peer(connection)
            try:
                self._inner.disconnect(peer)
                self._markers.pop(peer, None)
                self._pending_ships.pop(peer, None)
                self._admitted_ships.pop(peer, None)
                del self._connections[id(connection)]
            except BaseException:
                self._failed = True
                raise

    def begin(self, connection):
        """Make a fresh offer tied to a confirmed, marker-verified checkpoint."""
        with self._lock:
            self._live()
            peer = self._peer(connection)
            try:
                baseline = self._world._baseline
                checkpoint = self._world._store.latest_marked_confirmed()
                if baseline is None or baseline[1] != checkpoint:
                    raise ValueError('baseline is not the confirmed marked checkpoint')
                # latest_marked_confirmed() has just re-hashed the saved bytes.
                # Reuse the XML marker proof only for that exact generation.
                if checkpoint != self._verified_marker_checkpoint:
                    if verify_saved_marker(checkpoint, checkpoint.marker,
                            script_name=self._marker_script, cue_name='MarkerState') is not True:
                        raise ValueError('checkpoint marker not verified')
                    self._verified_marker_checkpoint = checkpoint
                self._markers.pop(peer, None)
                self._pending_ships.pop(peer, None)
                offer = self._inner.begin(peer)
                if (self._world._baseline != baseline or offer.checkpoint_id != checkpoint.checkpoint_id
                        or offer.sha256 != checkpoint.sha256 or offer.size != checkpoint.size):
                    raise ValueError('checkpoint changed while offering')
                self._markers[peer] = (offer.attempt_id, checkpoint)
                return MarkerBoundOffer(offer, checkpoint.marker)
            except BaseException:
                self._failed = True
                raise

    def _ship_offer(self, connection, attempt_id):
        try:
            peer = self._peer(connection)
            bound = self._markers[peer]
            if bound[0] != attempt_id:
                raise ValueError('attempt ID changed')
            self._current_offer(peer)
        except (KeyError, ValueError):
            raise StaleShipChoice('no matching current ship choice offer') from None
        checkpoint = bound[1]
        try:
            confirmed = self._world._store.latest_marked_confirmed()
        except (ValueError, OSError):
            raise StaleShipChoice('confirmed checkpoint unavailable') from None
        if confirmed != checkpoint:
            raise StaleShipChoice('confirmed checkpoint changed')
        return peer, checkpoint

    def _current_offer(self, peer):
        _, offer = self._inner._active[peer]
        receipt = Receipt(**{field: getattr(offer, field) for field in Receipt.__dataclass_fields__})
        self._inner._current(peer, receipt)

    def _available_ships(self, peer, checkpoint, after_index):
        # An admitted peer keeps its allocation across subsequent saves. A
        # pending peer can block only a still-usable offer for this generation.
        if self._ship_catalog is None or self._ship_catalog[0] != checkpoint:
            reader = self._company_ship_reader or read_company_ship_catalogue
            try:
                catalogue = reader(checkpoint)
                epoch = None
                if isinstance(catalogue, CompanyShipCatalogue):
                    epoch = catalogue.registry_epoch
                    if type(epoch) is not str or not _MARKER.fullmatch(epoch):
                        raise ValueError('invalid catalogue registry epoch')
                    catalogue = catalogue.ships
                ships = tuple(sorted(catalogue, key=lambda ship: ship.ship_index))
                self._ship_catalog = (checkpoint, ships, tuple(ship.ship_index for ship in ships), epoch)
            except (ValueError, OSError):
                raise CompanyShipCatalogUnavailable('company ship catalogue unavailable') from None

        occupied = [ship for holder, ship in self._admitted_ships.items() if holder != peer]
        for holder, ship in self._pending_ships.items():
            if holder != peer and self._markers.get(holder, (None, None))[1] == checkpoint:
                try:
                    self._current_offer(holder)
                except (KeyError, ValueError):
                    continue
                occupied.append(ship)
        occupied_indices = {ship.ship_index for ship in occupied}
        occupied_components = {ship.component_ref for ship in occupied}
        _, ships, indices, _ = self._ship_catalog
        admitted = self._admitted_ships.get(peer)
        for offset in range(bisect_right(indices, after_index), len(ships)):
            ship = ships[offset]
            if admitted is not None and (ship.ship_index, ship.component_ref) != (
                    admitted.ship_index, admitted.component_ref):
                continue
            if ship.ship_index not in occupied_indices and ship.component_ref not in occupied_components:
                yield ship

    def list_company_ships(self, connection, attempt_id, *, after_index=0, limit=None):
        """List verified company ships available to this still-offered connection."""
        with self._lock:
            self._live()
            peer, checkpoint = self._ship_offer(connection, attempt_id)
            if type(after_index) is not int or not 0 <= after_index <= MAX_INTEGER:
                raise ValueError('invalid company ship page cursor')
            if limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_INTEGER):
                raise ValueError('invalid company ship page size')
            return tuple(islice(self._available_ships(peer, checkpoint, after_index), limit))

    def choose_company_ship(self, connection, attempt_id, ship_index):
        """Reserve one available ship; a failed switch keeps the earlier choice."""
        with self._lock:
            self._live()
            peer, checkpoint = self._ship_offer(connection, attempt_id)
            if type(ship_index) is not int or not 1 <= ship_index <= MAX_INTEGER:
                raise CompanyShipUnavailable('invalid company ship index')
            ship = next(self._available_ships(peer, checkpoint, ship_index - 1), None)
            if ship is None or ship.ship_index != ship_index:
                raise CompanyShipUnavailable('company ship unavailable')
            self._pending_ships[peer] = ship
            return ship

    def accept(self, connection, claim, *, pre_accept=None, ship_motion=False):
        """Accept one trusted claim; an optional side-effect-free guard runs last.

        The guard runs under this wrapper's lock after checkpoint checks and
        must return exactly True. It must not call back into this wrapper.
        Ship motion requires a reservation for this attempt and catalogue
        evidence from the same confirmed checkpoint.
        """
        with self._lock:
            self._live()
            peer = self._peer(connection)
            if (type(claim) is not HonestClientLoadClaim or type(claim.receipt) is not Receipt
                    or type(claim.marker) is not str or not _MARKER.fullmatch(claim.marker)
                    or claim.load_event_observed is not True or claim.paused_observed is not True):
                raise ValueError('invalid trusted marker claim')
            if type(ship_motion) is not bool:
                raise ValueError('invalid ship motion admission mode')
            _, message = self._inner._current(peer, claim.receipt)
            bound = self._markers.get(peer)
            if bound is None or bound[0] != message.attempt_id or claim.marker != bound[1].marker:
                raise ValueError('marker does not match current attempt')
            if self._require_company_ship and peer not in self._pending_ships and peer not in self._admitted_ships:
                raise CompanyShipUnavailable('choose a free company ship before admission')
            motion_ship = registry_epoch = None
            if ship_motion:
                motion_ship = self._pending_ships.get(peer)
                if motion_ship is None:
                    raise CompanyShipUnavailable('ship motion requires a reserved company ship')
                if self._ship_catalog is None or self._ship_catalog[0] != bound[1] or self._ship_catalog[3] is None:
                    raise CompanyShipCatalogUnavailable('motion registry epoch unavailable for the offered checkpoint')
                registry_epoch = self._ship_catalog[3]
            # No mutation has occurred yet. The store's manifest/digest and
            # confirmed pointer must still describe the same checkpoint. The
            # caller owns the store exclusively throughout this operation.
            if self._world._store.latest_marked_confirmed() != bound[1]:
                raise ValueError('confirmed checkpoint changed')
            if pre_accept is not None:
                if not callable(pre_accept) or pre_accept() is not True:
                    raise ValueError('pre-accept guard did not confirm readiness')
            try:
                self._world.mark_loaded_trusting_marker(message.world_epoch, message.connection_handle,
                    message.baseline_id, message.load_generation, message.checkpoint_id, message.sha256,
                    trusted_marker_claim=True)
                del self._inner._active[peer]
                del self._markers[peer]
                reserved = self._pending_ships.pop(peer, None)
                if reserved is not None:
                    self._admitted_ships[peer] = reserved
            except BaseException:
                self._failed = True
                raise
            return MarkerLoadOutcome(message.attempt_id, motion_ship=motion_ship, registry_epoch=registry_epoch)
