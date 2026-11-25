#!/usr/bin/env python3
"""Exercise the installed X4 9.00 Undock row and handler with fake native APIs."""

import argparse
from hashlib import md5
from pathlib import Path
import subprocess


MENU_PATH = 'ui/addons/ego_detailmonitor/menu_docked.lua'
MENU_MD5 = 'cedebd1369740718f6de503597b69d4e'


def read_menu(game):
    index = game / '08.cat'
    offset = 0
    for entry in index.read_text(encoding='utf-8').splitlines():
        name, size, _, digest = entry.rsplit(' ', 3)
        size = int(size)
        if name == MENU_PATH:
            with index.with_suffix('.dat').open('rb') as archive:
                archive.seek(offset)
                menu = archive.read(size)
            if digest != MENU_MD5 or md5(menu).hexdigest() != MENU_MD5:
                raise ValueError('unsupported installed Docked menu')
            return menu.decode('utf-8-sig').splitlines()
        offset += size
    raise ValueError('Docked menu unavailable in the installed archive')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', type=Path, help='installed X4 Foundations directory')
    lines = read_menu(parser.parse_args().game)
    # Keep the original Lua in memory and supply only the needed code to LuaJIT.
    row = '\n'.join(lines[1178:1180])
    functions = '\n'.join(lines[1256:1275] + lines[1477:1491])
    harness = Path(__file__).with_suffix('.lua').read_text(encoding='utf-8')
    source = f'local row_code = [====[{row}]====]\nlocal functions = [====[{functions}]====]\n{harness}'
    subprocess.run(['luajit', '-'], input=source, text=True, check=True, timeout=5)


if __name__ == '__main__':
    main()
