"""Extract selected stock X4 script files read-only into a local cache for source tracing.

The base game's catalogs and the stock DLC catalogs are read through
src/lockstep/launcher/gamedata.py. Nothing is written to the installation. The
output defaults to .local/stock-source, which is never committed: the extracted
files are Egosoft's and must not be redistributed.

    python tools/dev/authority_stock_source_extract.py [--game DIR] [--out DIR]

Without --game, the installation is found through Steam's library folders.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from lockstep.launcher import game, gamedata  # noqa: E402

# Order scripts, map/interaction UI Lua, MD, libraries/schemas and English text.
WANTED = re.compile(
    r'^(aiscripts/[^/]+\.xml'
    r'|ui/addons/ego_detailmonitor/[^/]+\.lua'
    r'|ui/addons/ego_interactmenu/[^/]+\.lua'
    r'|ui/addons/ego_helper/[^/]+\.lua'
    r'|md/[^/]+\.xml'
    r'|libraries/[^/]+\.(xml|xsd)'
    r'|t/0001-l044\.xml)$',
    re.IGNORECASE,
)


def extract(install, out, wanted=WANTED):
    """Copy every wanted catalog entry to out/<extension>/<path>; returns the count."""
    count = 0
    for ext, cat in gamedata.catalogs(install):
        offset = 0
        with open(cat.with_suffix('.dat'), 'rb') as dat:
            for path, size in gamedata.index(cat):
                if wanted.match(path.replace('\\', '/')):
                    target = Path(out).joinpath(ext, *gamedata.safe_parts(path))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    dat.seek(offset)
                    target.write_bytes(dat.read(size))
                    count += 1
                offset += size
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--game', type=Path, help='X4 installation (default: found through Steam)')
    parser.add_argument('--out', type=Path, default=ROOT / '.local' / 'stock-source')
    args = parser.parse_args(argv)
    install = args.game or game.find_install()
    count = extract(install, args.out)
    print(f'extracted {count} stock files from {install} into {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
