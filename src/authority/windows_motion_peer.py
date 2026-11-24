"""Sample Windows UDP ownership for one local X4 motion peer.

The IP Helper UDP tables expose local addresses, ports and owning PIDs. They do
not expose a UDP remote endpoint, so this check cannot prove the peer's remote
tuple. Its separately sampled IPv4 and IPv6 tables are not an atomic snapshot.
The caller must keep the process owner and destination socket alive.
"""
import ctypes
import ipaddress
import os
import socket
import struct

from .windows_x4_process import WindowsX4Process


_AF_INET = 2
_AF_INET6 = 23  # Windows AF_INET6; do not use Python's platform constant.
_UDP_TABLE_OWNER_PID = 1
_ERROR_INSUFFICIENT_BUFFER = 122
_MAX_TABLE_BYTES = 16 * 1024 * 1024
_MAX_TABLE_ATTEMPTS = 3
_DWORD = ctypes.c_uint32
# Explicit loopback binding, or the implicit wildcard binding Winsock gives
# the shipped DLL's connected UDP socket on native Windows.
_OWNER_ADDRESSES = ('127.0.0.1', '0.0.0.0')


class _Udp4Row(ctypes.Structure):
    _fields_ = [('local_addr', _DWORD), ('local_port', _DWORD), ('pid', _DWORD)]


class _Udp6Row(ctypes.Structure):
    _fields_ = [('local_addr', ctypes.c_ubyte * 16), ('local_scope_id', _DWORD),
                ('local_port', _DWORD), ('pid', _DWORD)]


class _Udp4Table(ctypes.Structure):
    _fields_ = [('count', _DWORD), ('rows', _Udp4Row * 1)]


class _Udp6Table(ctypes.Structure):
    _fields_ = [('count', _DWORD), ('rows', _Udp6Row * 1)]


if ctypes.sizeof(_Udp4Row) != 12 or ctypes.sizeof(_Udp6Row) != 28:
    raise RuntimeError('unexpected Windows UDP row layout')


def _parse_table(raw, family):
    """Decode one returned Windows owner-PID table into (address, port, PID)."""
    if type(raw) is not bytes or not 4 <= len(raw) <= _MAX_TABLE_BYTES:
        raise ValueError('invalid UDP table buffer')
    table_type = (_Udp4Table if family == _AF_INET else
                  _Udp6Table if family == _AF_INET6 else None)
    if table_type is None:
        raise ValueError('invalid UDP table size')
    row_type = _Udp4Row if family == _AF_INET else _Udp6Row
    row_offset = table_type.rows.offset
    row_size = ctypes.sizeof(row_type)
    count = struct.unpack_from('<I', raw)[0]
    required = row_offset + count * row_size
    if count > (_MAX_TABLE_BYTES - row_offset) // row_size or required > len(raw):
        raise ValueError('invalid UDP table size')
    rows = []
    for index in range(count):
        offset = row_offset + index * row_size
        row = row_type.from_buffer_copy(raw, offset)
        if family == _AF_INET:
            address = socket.inet_ntoa(int(row.local_addr).to_bytes(4, 'little'))
        else:
            address = str(ipaddress.IPv6Address(bytes(row.local_addr)))
        # Windows stores the network-order port in the low two bytes of a DWORD.
        port = int.from_bytes(int(row.local_port).to_bytes(4, 'little')[:2], 'big')
        rows.append((address, port, int(row.pid)))
    return rows


def _load_udp_table_function():
    """Load the IP Helper entry point once for a validator instance."""
    if os.name != 'nt':
        raise OSError('Windows UDP tables are unavailable on this platform')
    iphlpapi = ctypes.WinDLL('iphlpapi', use_last_error=True)
    function = iphlpapi.GetExtendedUdpTable
    function.restype = _DWORD
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DWORD), ctypes.c_int,
                         _DWORD, ctypes.c_int, _DWORD]
    return function


def _get_extended_udp_table(family, function):
    """Read one bounded UDP_TABLE_OWNER_PID table using a loaded API function."""
    if family not in (_AF_INET, _AF_INET6):
        raise ValueError('unsupported Windows address family')
    size = _DWORD(0)
    result = int(function(None, ctypes.byref(size), 0, family, _UDP_TABLE_OWNER_PID, 0))
    if result != _ERROR_INSUFFICIENT_BUFFER or not 4 <= size.value <= _MAX_TABLE_BYTES:
        raise OSError('GetExtendedUdpTable size query failed')
    for _ in range(_MAX_TABLE_ATTEMPTS):
        capacity = size.value
        buffer = ctypes.create_string_buffer(capacity)
        actual = _DWORD(capacity)
        result = int(function(buffer, ctypes.byref(actual), 0, family, _UDP_TABLE_OWNER_PID, 0))
        if result == 0:
            if actual.value > capacity or actual.value < 4:
                raise OSError('GetExtendedUdpTable returned an invalid size')
            return _parse_table(buffer.raw[:actual.value], family)
        if result != _ERROR_INSUFFICIENT_BUFFER or not capacity < actual.value <= _MAX_TABLE_BYTES:
            raise OSError('GetExtendedUdpTable failed')
        size = actual
    raise OSError('GetExtendedUdpTable table kept changing')


def _windows_runtime():
    return os.name == 'nt'


class WindowsMotionPeer:
    """Callable peer validator bound to one running Windows process and socket."""
    def __init__(self, process, destination_socket):
        if (not isinstance(process, WindowsX4Process) or type(getattr(process, 'pid', None)) is not int or
                process.pid <= 0 or
                type(destination_socket) is not socket.socket or
                destination_socket.family != socket.AF_INET or
                destination_socket.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_DGRAM):
            raise ValueError('Windows process owner and IPv4 UDP socket required')
        local = destination_socket.getsockname()
        if (type(local) is not tuple or len(local) < 2 or local[0] != '127.0.0.1' or
                type(local[1]) is not int or local[1] <= 0):
            raise ValueError('bound loopback UDP socket required')
        self._process = process
        self._pid = process.pid
        self._socket = destination_socket
        self._socket_fd = destination_socket.fileno()
        self._destination = (local[0], local[1])
        self._api_function = None

    def _valid_owner(self):
        try:
            return self._process.pid == self._pid and self._process.owns_running_pid(self._pid) is True
        except (OSError, RuntimeError, ValueError):
            return False

    def _socket_valid(self):
        try:
            return (self._socket.fileno() == self._socket_fd and
                    self._socket.family == socket.AF_INET and
                    self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_DGRAM and
                    self._socket.getsockname()[:2] == self._destination)
        except (OSError, ValueError):
            return False

    def __call__(self, peer):
        if not _windows_runtime() or not self._socket_valid():
            return False
        if (type(peer) is not tuple or len(peer) != 2 or peer[0] != '127.0.0.1' or
                type(peer[1]) is not int or not 1 <= peer[1] <= 65535):
            return False
        if not self._valid_owner():
            return False
        try:
            if self._api_function is None:
                self._api_function = _load_udp_table_function()
            rows4 = _get_extended_udp_table(_AF_INET, self._api_function)
            rows6 = _get_extended_udp_table(_AF_INET6, self._api_function)
        except (OSError, ValueError, TypeError, OverflowError):
            return False
        if not self._valid_owner() or not self._socket_valid():
            return False
        source_port = peer[1]
        # The shipped network DLL's nonblocking UDP socket is implicitly bound,
        # which native Windows lists as 0.0.0.0 rather than 127.0.0.1. The
        # loopback peer, owning PID and single row for this port still apply.
        exact = [row for row in rows4 if row[0] in _OWNER_ADDRESSES and
                 row[1] == source_port and row[2] == self._pid]
        same_port = [row for row in rows4 + rows6 if row[1] == source_port]
        return len(exact) == 1 and len(same_port) == 1
