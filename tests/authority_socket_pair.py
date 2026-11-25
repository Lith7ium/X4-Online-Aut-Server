"""Provide connected sockets outside the Authority transport's IPv4 family."""
from os import name as platform_name
import socket


def non_ipv4_connected_pair():
    if platform_name != 'nt' and hasattr(socket, 'AF_UNIX') and hasattr(socket, 'socketpair'):
        return socket.socketpair(socket.AF_UNIX)

    # Windows socketpair is emulated and need not accept AF_UNIX.
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
        listener.settimeout(2)
        listener.bind(('::1', 0))
        listener.listen(1)
        client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            client.settimeout(2)
            client.connect(listener.getsockname())
            peer, address = listener.accept()
            if address != client.getsockname():
                peer.close()
                raise RuntimeError('unexpected IPv6 loopback peer')
            return client, peer
        except BaseException:
            client.close()
            raise
