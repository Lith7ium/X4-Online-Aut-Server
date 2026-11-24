"""Ship choices during a marked load offer. The server keeps native references private."""
import json
import re

from .load_messages import MAX_MESSAGE
from .trusted_marker_load_session import (
    CompanyShipCatalogUnavailable, CompanyShipUnavailable, StaleShipChoice,
)


def _public_ship(ship):
    return {'ship_index': ship.ship_index, 'sector_macro': ship.sector_macro,
            'name': ship.name, 'code': ship.code}


def _encode(response):
    return json.dumps(response, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _page(ships, attempt_id):
    response = {'version': 1, 'kind': 'company_ships', 'attempt_id': attempt_id,
                'ships': [], 'next_index': None}
    for ship in ships:
        if len(response['ships']) == 16:
            break
        response['ships'].append(_public_ship(ship))
        # Reserve room for the cursor even when this is the last ship.
        response['next_index'] = ship.ship_index
        if len(_encode(response)) > MAX_MESSAGE:
            response['ships'].pop()
            break
    if len(response['ships']) < len(ships):
        if not response['ships']:
            raise RuntimeError('a verified ship exceeds the control frame limit')
        response['next_index'] = response['ships'][-1]['ship_index']
    else:
        response['next_index'] = None
    return response


def company_ship_reply(message, session, connection):
    """Handle a decoded list or choose request on its original offer socket."""
    listing = message['kind'] == 'list_company_ships'
    field = 'after_index' if listing else 'ship_index'
    if (set(message) != {'version', 'kind', 'attempt_id', field} or
            type(message['version']) is not int or message['version'] != 1 or
            type(message['attempt_id']) is not str or
            not re.fullmatch(r'[0-9a-f]{32}', message['attempt_id']) or
            type(message[field]) is not int or not (0 if listing else 1) <= message[field] <= 200000):
        raise ValueError('invalid company ship request')
    attempt_id = message['attempt_id']
    response = {'version': 1, 'attempt_id': attempt_id}
    try:
        if listing:
            ships = session.list_company_ships(connection, attempt_id,
                                               after_index=message[field], limit=17)
            response = _page(ships, attempt_id)
        else:
            ship = session.choose_company_ship(connection, attempt_id, message[field])
            response.update(kind='company_ship_chosen', ship=_public_ship(ship))
    except StaleShipChoice:
        response['kind'] = 'stale_ship_choice'
    except CompanyShipUnavailable:
        response['kind'] = 'company_ship_unavailable'
    except CompanyShipCatalogUnavailable:
        response['kind'] = 'company_ship_catalog_unavailable'
    encoded = _encode(response)
    if len(encoded) > MAX_MESSAGE:
        raise RuntimeError('company ship reply exceeds the control frame limit')
    return encoded + b'\n'
