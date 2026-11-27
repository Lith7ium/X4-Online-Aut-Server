"""Synthetic census data for offline tests of witness parsing and installation."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import authority_census_pair_inputs as inputs


def make_census_witness(candidates=None):
    """Build a complete synthetic census, optionally with chosen eligible sectors."""
    test_sectors = set(candidates or ())
    sector_count = 152
    names = [f'sector_{number:03d}' for number in range(sector_count - len(test_sectors))]
    rows = {
        name: {'sector_macro': name, 'sector_saved_component_ref': f'[0x{number:x}]', 'witnesses': []}
        for number, name in enumerate(sorted([*test_sectors, *names]), 1)
    }
    eligible = {index: sector for sector, indices in (candidates or {}).items() for index in indices}
    for index in range(1, inputs.archive.COUNT + 1):
        sector = eligible.get(index, names[(index - 1) % len(names)])
        rows[sector]['witnesses'].append({
            'index': index,
            'saved_under_dockingbay': False,
            'saved_immediate_parent_zone': candidates is None or index in eligible,
        })
    return {
        'saved_sector_count': sector_count,
        'saved_registry_count': inputs.archive.COUNT,
        'runtime_binding_proven': False,
        'cross_instance_frame_identity_proven': False,
        'exact_loaded_file_proven': False,
        'registry_entries_without_sector_witness': 0,
        'scope': 'one pinned census checkpoint; saved structural evidence only',
        'checkpoint_id': inputs.archive.CHECKPOINT_ID,
        'sha256': inputs.archive.SHA256,
        'registry_epoch': inputs.archive.REGISTRY_EPOCH,
        'sectors': list(rows.values()),
    }


@contextmanager
def synthetic_sector_witness():
    """Use the real validator with test-only path and digest pins."""
    data = make_census_witness({
        'cluster_01_sector001_macro': (41, 42),
        'cluster_115_sector001_macro': (1318, 1322),
    })
    raw = json.dumps(data, separators=(',', ':')).encode('utf-8')
    load_witness = inputs.load_witness
    with TemporaryDirectory() as directory:
        fixture = Path(directory) / 'synthetic-sector-witness.json'
        fixture.write_bytes(raw)

        def load_fixture(path=fixture):
            return load_witness(path)

        with (patch.object(inputs, 'WITNESS_PATH', fixture),
              patch.object(inputs, 'WITNESS_SHA256', hashlib.sha256(raw).hexdigest()),
              patch.object(inputs, 'load_witness', load_fixture)):
            yield
