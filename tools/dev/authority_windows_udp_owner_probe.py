#!/usr/bin/env python3
"""Probe the local Windows UDP owner row for the unmodified X4 test DLL."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import socket
import struct
import time

from authority_windows_network_smoke import Lua, checked_dll, lua_string

ROOT = Path(__file__).resolve().parents[2]
NETWORK_DLL = ROOT / 'network/x4lan_net_64.dll'
NETWORK_SHA256 = '6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0'
LUA_SHA256 = 'e0712ac93f0d7f3a28236d3153b82e85d35e6bcf8e606af250f664580eaf3b63'
PAYLOAD = b'x4-own-udp-diagnostic'
LOOPBACK = '127.0.0.1'
ROW = struct.Struct('<4s2s2xI')
DWORD = ctypes.c_uint32
TABLE_LIMIT = 16 * 1024 * 1024
SAMPLES = 5


def own_row_category(raw: bytes, pid: int, port: int) -> str:
    """Decode address bytes only for our PID and the observed source port."""
    if not 4 <= len(raw) <= TABLE_LIMIT:
        raise ValueError('invalid table size')
    count = struct.unpack_from('<I', raw)[0]
    if 4 + count * ROW.size > len(raw):
        raise ValueError('truncated table')
    rows = []
    for offset in range(4, 4 + count * ROW.size, ROW.size):
        if struct.unpack_from('<I', raw, offset + 8)[0] != pid:
            continue
        if int.from_bytes(raw[offset + 4:offset + 6], 'big') == port:
            rows.append(raw[offset:offset + 4])
    if not rows:
        return 'missing'
    if len(rows) > 1:
        return 'multiple'
    if rows[0] == b'\x7f\0\0\1':
        return 'loopback'
    if rows[0] == bytes(4):
        return 'wildcard'
    return 'other'


def open_udp_table():
    function = ctypes.WinDLL('iphlpapi').GetExtendedUdpTable
    function.restype = DWORD
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWORD), ctypes.c_int,
                         DWORD, ctypes.c_int, DWORD]
    return function


def query_udp4(function):
    # UDP_TABLE_OWNER_PID (1), IPv4 only. Query and retry a growing table,
    # preserving no raw data after categorizing our own test socket.
    capacity = DWORD(0)
    if function(None, ctypes.byref(capacity), False, socket.AF_INET, 1, 0) != 122:
        raise RuntimeError('UDP table size query failed')
    for _ in range(3):
        if not 4 <= capacity.value <= TABLE_LIMIT:
            raise ValueError('UDP table outside size limit')
        buffer = ctypes.create_string_buffer(capacity.value)
        actual = DWORD(capacity.value)
        status = function(buffer, ctypes.byref(actual), False, socket.AF_INET, 1, 0)
        if status == 0 and 4 <= actual.value <= capacity.value:
            return buffer.raw[:actual.value]
        if status != 122 or actual.value <= capacity.value:
            raise RuntimeError('UDP table read failed')
        capacity = actual
    raise RuntimeError('UDP table size kept growing')


def qpc_reader():
    kernel = ctypes.WinDLL('kernel32')
    counter = kernel.QueryPerformanceCounter
    frequency = kernel.QueryPerformanceFrequency
    for function in (counter, frequency):
        function.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        function.restype = ctypes.c_int
    ticks_per_second = ctypes.c_longlong()
    if not frequency(ctypes.byref(ticks_per_second)) or ticks_per_second.value <= 0:
        raise RuntimeError('QPC frequency unavailable')

    def read():
        ticks = ctypes.c_longlong()
        if not counter(ctypes.byref(ticks)) or ticks.value < 0:
            raise RuntimeError('QPC counter unavailable')
        return ticks.value / ticks_per_second.value

    return read


@contextmanager
def model_source(port):
    """Python Winsock equivalent. The DLL code is not executed in this mode."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as source:
        source.setblocking(False)
        source.connect((LOOPBACK, port))
        if source.send(PAYLOAD) != len(PAYLOAD):
            raise RuntimeError('short UDP send')
        yield qpc_reader()


@contextmanager
def dll_source(port, lua_path, network_path):
    lua = Lua(lua_path)
    try:
        lua.run(f'''
assert(_VERSION == 'Lua 5.1')
local entry = assert(package.loadlib({lua_string(str(network_path))}, 'luaopen_x4lan_net'))
net = assert(entry())
assert(type(net.open) == 'function' and type(net.monotonic) == 'function')
conn = assert(net.open('127.0.0.1', {port}))
assert(conn:send({lua_string(PAYLOAD.decode('ascii'))}))
return 'sent'
''')
        yield lambda: float(lua.run("return string.format('%.17g', assert(net.monotonic()))"))
    finally:
        lua.close()


def probe(args, report):
    report['stage'] = 'platform'
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError('Windows x64 required')
    report['runtime'] = 'wine' if hasattr(ctypes.WinDLL('ntdll'), 'wine_get_version') else 'native_windows'
    report['stage'] = 'network_dll_hash'
    network, _ = checked_dll(str(NETWORK_DLL), NETWORK_SHA256)
    report['network_dll_sha256_matches'] = True
    if not args.model:
        report['stage'] = 'lua_host_hash'
        lua_path = Path(args.lua_dll).absolute()
        if lua_path.name.lower() != 'lua51_64.dll':
            raise ValueError('unexpected Lua DLL name')
        lua_path, _ = checked_dll(str(lua_path), LUA_SHA256)
        report['lua_host_sha256_matches'] = True
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as receiver:
        receiver.bind((LOOPBACK, 0))
        receiver.settimeout(2)
        port = receiver.getsockname()[1]
        source = model_source(port) if args.model else dll_source(port, lua_path, network)
        report['stage'] = 'source_open_send'
        with source as clock:
            report['network_dll_executed'] = not args.model
            report['stage'] = 'loopback_receive'
            message, peer = receiver.recvfrom(128)
            if message != PAYLOAD or peer[0] != LOOPBACK or peer[1] == port:
                raise ValueError('unexpected probe packet')
            report['received_loopback_packets'] = 1
            report['stage'] = 'monotonic_clock'
            first = clock()
            time.sleep(.025)
            second = clock()
            if not all(math.isfinite(value) and value >= 0 for value in (first, second)) or second <= first:
                raise ValueError('monotonic clock did not advance')
            report['monotonic_clock'] = 'advanced'
            report['stage'] = 'udp_owner_table'
            query = open_udp_table()
            counts = Counter()
            for index in range(SAMPLES):
                counts[own_row_category(query_udp4(query), os.getpid(), peer[1])] += 1
                if index + 1 < SAMPLES:
                    time.sleep(.02)
            report['owner_row_counts'] = dict(sorted(counts.items()))
            report['owner_row'] = next(iter(counts)) if len(counts) == 1 else 'inconsistent'
    report['stage'] = 'complete'
    report['status'] = 'complete'


class CategoryArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, '{"status":"failed","stage":"arguments"}\n')


def main(argv=None):
    parser = CategoryArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--lua-dll', help='Installed, pinned lua51_64.dll to load read-only')
    mode.add_argument('--model', action='store_true', help='Run the Python Winsock model only')
    args = parser.parse_args(argv)
    report = {'status': 'failed', 'mode': 'winsock_model' if args.model else 'shipped_dll',
              'network_dll_sha256_matches': False, 'network_dll_executed': False,
              'received_loopback_packets': 0}
    try:
        probe(args, report)
    except (OSError, ValueError, TypeError, RuntimeError, AttributeError, struct.error):
        pass  # Report only categories; exception text may contain a local path.
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
