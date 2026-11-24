"""Run one stock X4 purchase in the isolated, parked two-ship world."""
import os
from pathlib import Path
import time


SOURCE_DIGEST = '1c7117f327df18df402295755968a19591cc238b9f38519b43348bfce3ee2ff1'
PROBES = Path(__file__).resolve().parent
READ_CUE = 'authority_trade_observer.xml'
PURCHASE_CUES = ('authority_company_trade_dock.xml', 'authority_company_stock_trade.xml')
UI_DRIVER = 'authority_company_trade_driver.lua'


def install(case, mode, epoch, nonce):
    extension = case / 'extensions/x4_lan_lab'
    purchase = mode in ('buy', 'six-unit')
    files = [extension / 'md' / name for name in (READ_CUE, *(PURCHASE_CUES if purchase else ()))]
    if purchase:
        files.append(extension / 'ui' / UI_DRIVER)
    if mode == 'six-unit':
        files.append(extension / 'ui/authority_trade_ledger.lua')
    for file in files:
        with file.open('xb') as output:
            output.write((PROBES / file.name).read_bytes())
    if purchase:
        # Attach to the existing world update after the verified initial pause.
        bridge = extension / 'ui/authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        tick = ' local ok,reason=bridge:update();assert(ok,reason)\n'
        hook = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
        entry = f'    <file name="ui/{UI_DRIVER}" />\n'
        if mode == 'six-unit':
            entry = '    <file name="ui/authority_trade_ledger.lua" />\n' + entry
        audit = 'true' if mode == 'six-unit' else 'false'
        for path, marker, replacement in (
            (manifest, hook, entry + hook),
            (bridge, tick, tick + f''' if registry_checkpoint:ready() then
  if not trade_tick then trade_tick = X4LAN_COMPANY_TRADE_DRIVER.new("{epoch}", "{nonce}", {audit}) end
  trade_tick(C.IsGamePaused())
 end
'''),
        ):
            original = path.read_text(encoding='ascii')
            if original.count(marker) != 1:
                raise ValueError(f'missing private trade trial hook in {path.name}')
            updated = original.replace(marker, replacement)
            if path == bridge:
                updated = 'local trade_tick\n' + updated
            path.write_text(updated, encoding='ascii')
            files.append(path)
    return files


def loaded_observation(log, read_evidence):
    prefix = b'[X4LAN] AUTH_TRADE_READ '
    records = [line.split(prefix, 1)[1].strip().decode('ascii')
               for line in read_evidence(log, 128 << 20).splitlines() if prefix in line]
    if (not records or not records[0].startswith('BEGIN ') or records[-1] != 'END'
            or len(records) > 32 or records.count('END') != 1):
        raise ValueError(f'incomplete native trade observation ({len(records)} rows)')
    return records


def await_records(log, nonce, deadline, check):
    prefix = f'[X4LAN] AUTH_TRADE_TRIAL {nonce}|'.encode('ascii')
    observations = []
    with log.open('rb') as stream:
        partial = b''
        while time.monotonic() < deadline:
            check()
            data = stream.read(256 << 10)
            if not data:
                time.sleep(.25)
                continue
            lines = (partial + data).split(b'\n')
            partial = lines.pop()
            if len(partial) > 65536:
                raise ValueError('malformed native trade log')
            for line in lines:
                if prefix not in line:
                    continue
                row = line.split(prefix, 1)[1].strip().decode('ascii')
                observations.append(row)
                if len(observations) > 210 or row.startswith('FAILED|'):
                    raise ValueError(f'trade trial stopped: {observations}')
                if row == 'DONE':
                    return observations
    raise TimeoutError(f'trade trial timed out: {observations}')


def verify_settlement(rows, nonce):
    phases = [row.split('|', 1)[0] for row in rows]
    if phases != ['START', 'DOCK', 'RESULT', 'RESULT', 'OBSERVED', 'RESULT', 'DONE']:
        raise ValueError(f'unexpected native trade sequence: {rows}')
    quote = rows[2].split('|')
    receipt = rows[4].split('|')
    if (not rows[1].startswith(f'DOCK|{nonce}|READY|') or len(quote) != 4
            or quote[1:3] != [nonce, 'quote'] or not quote[3].isdigit() or int(quote[3]) <= 0
            or rows[3] != f'RESULT|{nonce}|submitted|{quote[3]}'
            or not rows[5].startswith(f'RESULT|{nonce}|already_attempted|')
            or len(receipt) != 10 or receipt[1:5] != [nonce, quote[3], '1', '-1']
            or int(receipt[7]) != -int(quote[3]) or any(v not in ('true', '1') for v in receipt[8:])):
        raise ValueError(f'engine event, balances, cargo or docking disagree: {rows}')
    return {'price_cents': int(quote[3]), 'ship_cargo_delta': 1, 'station_cargo_delta': -1,
            'ship_money_delta_cents': int(receipt[5]), 'station_money_delta_cents': int(receipt[6]),
            'company_money_delta_cents': int(receipt[7]), 'records': rows}


def archive_cues(case, out, mode):
    names = [READ_CUE]
    if mode in ('buy', 'six-unit'):
        names.extend(PURCHASE_CUES)
    for name in names:
        source = case / 'extensions/x4_lan_lab/md' / name
        os.link(source, out / name)
        source.unlink()
