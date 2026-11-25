"""Shared terminal selection and pinned TLS connection settings for authority clients."""
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import stat
import sys
from threading import Thread

from authority.company_ship_selection import CompanyShipSelector


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('duplicate connection field')
        result[name] = value
    return result


def _read_settings(path, *, require_private=False):
    """Read one bounded private JSON file without printing its contents."""
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or
            getattr(info, 'st_file_attributes', 0) & 0x400 or
            not 0 < info.st_size <= 4096):
        raise ValueError('ordinary bounded connection file required')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
    if require_private:
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if require_private:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or opened.st_mode & 0o077 or not os.path.samestat(info, opened)):
                raise ValueError('owner-only connection file required')
        data = stream.read(4097)
    if len(data) > 4096:
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
