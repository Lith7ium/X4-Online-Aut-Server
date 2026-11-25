"""Reserve a terminal-selected ship on an already offered authority connection."""
from .experimental_client_owner import CompanyShipChoiceDenied


class CompanyShipSelector:
    """A selector that also reports reservation feedback to the player."""

    def __init__(self, select_ship, report):
        self.select_ship = select_ship
        self.report = report

    def __call__(self, ships, keepalive):
        return self.select_ship(ships, keepalive)


def reserve_company_ship(owner, select_ship):
    """Reserve an explicit choice on the same socket and offer as the download."""
    report = select_ship.report if isinstance(select_ship, CompanyShipSelector) else None
    while True:
        ships = owner.list_company_ships()
        if not ships:
            if report is not None:
                report('No company ships are free. Try joining again when one is available.')
            raise CompanyShipChoiceDenied('no_free_company_ships')
        index = select_ship(ships, owner.poll)
        listed = next((ship for ship in ships if ship['ship_index'] == index), None)
        if type(index) is not int or listed is None:
            raise CompanyShipChoiceDenied('company_ship_unavailable')
        try:
            selected = owner.choose_company_ship(index)
        except CompanyShipChoiceDenied as error:
            if error.reason != 'company_ship_unavailable':
                raise
            # Another participant reserved the ship while this player was choosing.
            if report is not None:
                report(f'Company ship {index} is no longer available.')
            continue
        if selected != listed:
            raise ValueError('chosen ship differs from the offered catalogue')
        return index
