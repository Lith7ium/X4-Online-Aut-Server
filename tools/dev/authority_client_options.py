"""Shared options, terminal selection and pinned TLS settings for authority clients."""
from argparse import Namespace
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import stat
import sys
from threading import Thread

from authority.company_npc_measurement import NPC_CODE, NPC_INDEX, PILOT_INDEX, validate_measurement_trial
from authority.company_ship_selection import CompanyShipSelector
from authority.file_fingerprints import same_path_and_descriptor

_MAX_CONNECTION_BYTES = 4096


def company_channel_options(args: Namespace) -> dict[str, bool]:
    if getattr(args, 'company_combat', False) and not args.company_motion:
        raise ValueError('--company-combat requires --company-motion')
    return {
        name: True for name in ('company_motion', 'company_combat', 'combat_capture')
        if getattr(args, name, False)
    }


def company_trade_options(args: Namespace) -> dict[str, bool]:
    purchases = args.company_purchases
    fleet_orders = getattr(args, 'company_fleet_orders', False)
    trade_view = getattr(args, 'company_trade_view', False)
    sales = getattr(args, 'company_sales', False)
    undock_test = getattr(args, 'company_undock_test', False)
    if fleet_orders and not purchases:
        raise ValueError('--company-fleet-orders requires --company-purchases')
    if sales and not (purchases and trade_view):
        raise ValueError('--company-sales requires --company-purchases and --company-trade-view')
    if trade_view and not purchases:
        raise ValueError('--company-trade-view requires --company-purchases')
    if undock_test and not purchases:
        raise ValueError('--company-undock-test requires --company-purchases')
    return {
        name: True for name, enabled in (
            ('company_purchases', purchases), ('company_fleet_orders', fleet_orders),
            ('company_trade_view', trade_view), ('company_sales', sales),
            ('company_undock_test', undock_test),
        ) if enabled
    }


def company_view_options(args: Namespace, *, hold_npc: bool = False) -> dict[str, bool | int | tuple[int, str]]:
    from authority.company_motion_source import validate_npc_observation, validate_playback_index

    cockpit_index = args.company_cockpit_playback_index
    radar_playback = getattr(args, 'company_cockpit_playback', False) or cockpit_index is not None
    playback_index = cockpit_index if cockpit_index is not None else args.company_hull_playback_index
    observed_npc = validate_npc_observation(getattr(args, 'observe_registered_npc', None),
                                            company_motion=args.company_motion, own_index=args.company_ship_index)
    measurement = getattr(args, 'trial_registered_npc_measurement', False)
    base_game_only = getattr(args, 'base_game_only', False)
    validate_measurement_trial(measurement, base_game_only=base_game_only,
                               observed_npc=observed_npc, pilot_index=args.company_ship_index)
    if hold_npc and (not base_game_only or args.company_ship_index != PILOT_INDEX
                     or observed_npc != (NPC_INDEX, NPC_CODE)):
        raise ValueError('NPC hold trial requires the vanilla HVC-420 pilot and observation of MFQ-419')
    validate_playback_index(playback_index, company_motion=args.company_motion,
                            own_index=args.company_ship_index, cockpit_radar_playback=radar_playback)

    continuous_playback = getattr(args, 'company_cockpit_continuous_playback', False)
    if continuous_playback and (not radar_playback or playback_index is not None or not args.company_motion):
        raise ValueError('--company-cockpit-continuous-playback requires --company-motion and --company-cockpit-playback')

    options: dict[str, bool | int | tuple[int, str]] = {}
    if observed_npc:
        options['observed_npc'] = observed_npc
    if measurement:
        options['registered_npc_local_measurement'] = True
    if playback_index is not None:
        options['company_hull_playback_index'] = playback_index
    if radar_playback:
        options['cockpit_radar_playback'] = True
    if continuous_playback:
        options['cockpit_continuous_playback'] = True
    return options


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('duplicate connection field')
        result[name] = value
    return result


def _check_connection_file(info):
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not 0 < info.st_size <= _MAX_CONNECTION_BYTES):
        raise ValueError('ordinary bounded connection file required')


def _read_settings(path, *, require_private=False):
    """Read one bounded JSON file without printing its contents."""
    path = Path(path)
    named = path.lstat()
    _check_connection_file(named)
    extra_flags = getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with open(path, 'rb', opener=lambda name, flags: os.open(name, flags | extra_flags)) as stream:
        opened = os.fstat(stream.fileno())
        _check_connection_file(opened)
        if not same_path_and_descriptor(named, opened):
            raise ValueError('connection file changed before opening')
        if require_private and (opened.st_uid != os.geteuid() or opened.st_mode & 0o077):
            raise ValueError('owner-only connection file required')
        data = stream.read(_MAX_CONNECTION_BYTES + 1)
    if len(data) > _MAX_CONNECTION_BYTES:
        raise ValueError('connection file too large')
    return json.loads(data, object_pairs_hook=_unique_fields)


def read_connection_settings(path, *, require_private=False):
    """Read bounded credentials without putting them on the command line."""
    settings = _read_settings(path, require_private=require_private)
    expected = {'server_host', 'server_port', 'server_fingerprint',
                'shared_key', 'file_token', 'file_slot'}
    if type(settings) is not dict or set(settings) != expected:
        raise ValueError('unexpected connection fields')
    host = settings['server_host']
    if (type(host) is not str or not 1 <= len(host) <= 253 or
            any(character.isspace() or ord(character) < 32 for character in host)):
        raise ValueError('invalid server host')
    for name, maximum in (('server_port', 65535), ('file_slot', 8)):
        if type(settings[name]) is not int or not 1 <= settings[name] <= maximum:
            raise ValueError('invalid connection number')
    for name, length in (('server_fingerprint', 64), ('shared_key', 64), ('file_token', 32)):
        value = settings[name]
        if type(value) is not str or not re.fullmatch(r'[0-9a-f]{' + str(length) + r'}', value):
            raise ValueError('invalid connection credential format')
    settings['shared_key'] = bytes.fromhex(settings['shared_key'])
    return settings


def prompt_company_ship(ships, keepalive):
    """Read a terminal selection while the owning thread keeps the offer alive."""
    print("Available company ships", file=sys.stderr)
    indices = {ship['ship_index'] for ship in ships}
    for ship in ships:
        label = ' '.join(value for value in (ship['code'], ship['name']) if value)
        print(f"  {ship['ship_index']:>6}  {label}  {ship['sector_macro']}", file=sys.stderr)

    def read_line(reply):
        try:
            reply.put(input())
        except (EOFError, OSError):
            reply.put(None)

    while True:
        print("Enter a listed ship index, or q to leave: ", file=sys.stderr, end='', flush=True)
        reply = Queue(maxsize=1)
        Thread(target=read_line, args=(reply,), daemon=True).start()
        while True:
            keepalive()
            try:
                line = reply.get(timeout=.25)
                break
            except Empty:
                continue
        if line is None or line.strip().lower() == 'q':
            raise KeyboardInterrupt
        value = line.strip()
        if re.fullmatch(r'[0-9]{1,6}', value) and int(value) in indices:
            return int(value)


def _report_company_ship_choice(message):
    print(message, file=sys.stderr, flush=True)


terminal_company_ship_selector = CompanyShipSelector(prompt_company_ship, _report_company_ship_choice)
