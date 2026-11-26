"""Write the local receipt for one opt-in pilot combat capture after X4 stops.

Use the ``combat_capture`` block from the pilot's fixture result (checkpoint ID,
save digest, registry epoch, admitted index) and that pilot's own log. The
host can check the active capture with authority_combat_hit_evidence.py using
--capture-epoch and --piloted-ship-index.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.combat_capture import collect_hits, new_receipt, write_receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--sha256', required=True, help="save digest from this pilot's load offer")
    parser.add_argument('--registry-epoch', required=True)
    parser.add_argument('--piloted-ship-index', type=int, required=True)
    parser.add_argument('--log', type=Path, required=True, help="the pilot's X4 debug log")
    parser.add_argument('--out', type=Path, required=True, help='new receipt JSON (must not exist)')
    args = parser.parse_args(argv)
    try:
        offer = new_receipt(args.checkpoint, args.sha256, args.registry_epoch, args.piloted_ship_index)
        with args.log.open(encoding='utf-8', errors='replace') as lines:
            receipt = collect_hits(offer, lines)
        write_receipt(args.out, receipt)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({'receipt': str(args.out), 'sequences': [hit['sequence'] for hit in receipt['hits']]}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
