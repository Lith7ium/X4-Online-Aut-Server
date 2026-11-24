"""Read the shared company's ship inventory from verified checkpoint bytes."""
from dataclasses import dataclass
import re

from .registry_availability_evidence import RegistryAvailabilityEvidenceError, verify_join_snapshot

_SECTOR_MACRO = re.compile(r'[A-Za-z0-9_]{1,128}')
_DISPLAY_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_COMPONENT = re.compile(r'\[0x([0-9a-f]+)\]')


@dataclass(frozen=True)
class CompanyShip:
    """An issued ship with a saved operational observation and company ownership."""

    ship_index: int
    component_ref: str
    sector_macro: str
    name: str | None
    code: str | None


def read_company_ships(checkpoint, *, pump=None):
    """Return saved operational player-faction ships ordered by issued index.

    The format-3 verifier checks the checkpoint digest, available bindings and
    source registry. The caller owns separate MD marker checks. This inventory
    establishes neither current occupancy nor permission to board or steer.
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

    verify_join_snapshot(checkpoint, pump=pump, observe_universe=observe)
    return tuple(sorted(candidates, key=lambda ship: ship.ship_index))
