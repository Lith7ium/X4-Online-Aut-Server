"""Read the shared company's ship inventory from verified checkpoint bytes."""
from dataclasses import dataclass, field, replace
import re

from .registry_availability_evidence import RegistryAvailabilityEvidenceError, verify_join_snapshot

_SECTOR_MACRO = re.compile(r'[A-Za-z0-9_]{1,128}')
_DISPLAY_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_COMPONENT = re.compile(r'\[0x([0-9a-f]+)\]')
_DECIMAL_SEED = re.compile(r'-?[0-9]{1,20}')


@dataclass(frozen=True)
class CompanyShip:
    """An issued ship with a saved operational observation and company ownership."""

    ship_index: int
    component_ref: str
    sector_macro: str
    name: str | None
    code: str | None
    captain_seed: str | None = None


@dataclass(frozen=True)
class CompanyShipCatalogue:
    """Ships and registry epoch jointly verified from the same checkpoint."""

    registry_epoch: str
    ships: tuple[CompanyShip, ...]


def read_company_ships(checkpoint, *, pump=None):
    """Return saved operational player-faction ships ordered by issued index."""
    return read_company_ship_catalogue(checkpoint, pump=pump).ships


def _component_ref(value):
    match = _COMPONENT.fullmatch(value)
    if match is None:
        return None
    return match[1].lstrip('0') or None


def _signed_captain_seed(value):
    """Format a saved 64-bit seed for an exact native largeint text comparison.

    The save can spell the same bit pattern as unsigned decimal. An unsigned
    high bit becomes the corresponding negative signed value. The native
    caller must still refuse a live seed with a different spelling.
    """
    if value is None or not _DECIMAL_SEED.fullmatch(value):
        return None
    seed = int(value)
    if not -(1 << 63) <= seed < 1 << 64:
        return None
    return str(seed - (1 << 64) if seed >= 1 << 63 else seed)


@dataclass
class _SavedNpc:
    post: str | None = None
    seed: str | None = None


@dataclass
class _ShipCrew:
    depth: int
    index: int
    controls: int = 0
    pilot: str | None = None
    components: dict[str, _SavedNpc | None] = field(default_factory=dict)

    def captain_seed(self):
        npc = self.components.get(self.pilot) if self.controls == 1 and self.pilot else None
        if npc is not None and npc.post == 'aipilot':
            return _signed_captain_seed(npc.seed)
        return None


class _SavedCaptainScan:
    """Keep component identities local to their nearest tracked ship."""

    def __init__(self):
        self._open = []
        self.seeds = {}

    def finish_before(self, depth):
        while self._open and self._open[-1][0] >= depth:
            level, _, crew, _ = self._open.pop()
            if crew is not None and level == crew.depth:
                seed = crew.captain_seed()
                if seed is not None:
                    self.seeds[crew.index] = seed

    def observe(self, element, depth, attrs, ship_index):
        self.finish_before(depth)
        if not self._open and (ship_index is None or attrs.get('owner') != 'player'):
            return

        _, parent, crew, parent_npc = self._open[-1] if self._open else (0, '', None, None)
        npc = None
        if element == 'component':
            kind = attrs.get('class', '')
            if ship_index is not None and attrs.get('owner') == 'player':
                crew = _ShipCrew(depth, ship_index)
            elif kind in ('ship', 'station') or kind.startswith(('ship_', 'station_')):
                crew = None
            elif crew is not None:
                ref = _component_ref(attrs.get('id', ''))
                if ref:
                    if ref not in crew.components and kind == 'npc' and attrs.get('owner') == 'player':
                        npc = _SavedNpc()
                    crew.components[ref] = npc
        elif crew is not None:
            if element == 'control' and depth == crew.depth + 1:
                crew.controls += 1
            elif (element == 'post' and parent == 'control' and depth == crew.depth + 2
                  and attrs.get('id') == 'aipilot'):
                ref = _component_ref(attrs.get('component', ''))
                crew.pilot = ref if crew.pilot is None and ref else ''
            elif parent_npc is not None:
                if element == 'entity':
                    parent_npc.post = attrs.get('post', '') if parent_npc.post is None else ''
                elif element == 'npcseed':
                    parent_npc.seed = attrs.get('seed', '') if parent_npc.seed is None else ''
        self._open.append((depth, element, crew, npc))


def read_company_ship_catalogue(checkpoint, *, pump=None, include_captains=False):
    """Return the verified registry epoch and operational company ships.

    The format-3 verifier checks the checkpoint digest, available bindings and
    source registry. The caller owns separate MD marker checks. This inventory
    establishes neither current occupancy nor permission to board or steer.
    The optional captain seed identifies an unambiguous saved ship assignment.
    """
    candidates = []
    sectors = []

    def observe(element, depth, attrs, ship_index):
        while sectors and depth <= sectors[-1][0]:
            sectors.pop()
        if element == 'component' and attrs.get('class') == 'sector':
            sectors.append((depth, attrs.get('macro', '')))
        if ship_index is None or attrs.get('owner') != 'player':
            return
        if len(sectors) != 1 or not _SECTOR_MACRO.fullmatch(sectors[0][1]):
            raise RegistryAvailabilityEvidenceError('operational company ship has no unambiguous sector macro')
        raw_name = attrs.get('name', '').strip()
        name = raw_name if (0 < len(raw_name) <= 128 and raw_name.isprintable()
                            and '{' not in raw_name and '}' not in raw_name) else None
        raw_code = attrs.get('code', '')
        code = raw_code if _DISPLAY_CODE.fullmatch(raw_code) else None
        raw_id = _COMPONENT.fullmatch(attrs['id']).group(1)
        candidates.append(CompanyShip(ship_index, f'[0x{int(raw_id, 16):x}]', sectors[0][1], name, code))

    observer = observe
    if include_captains:
        captains = _SavedCaptainScan()

        def observe_with_captains(element, depth, attrs, ship_index):
            observe(element, depth, attrs, ship_index)
            captains.observe(element, depth, attrs, ship_index)

        observer = observe_with_captains
    snapshot = verify_join_snapshot(checkpoint, pump=pump, observe_universe=observer)
    if include_captains:
        captains.finish_before(0)
        candidates = [replace(ship, captain_seed=captains.seeds[ship.ship_index])
                      if ship.ship_index in captains.seeds else ship for ship in candidates]
    return CompanyShipCatalogue(snapshot['epoch'], tuple(sorted(candidates, key=lambda ship: ship.ship_index)))
