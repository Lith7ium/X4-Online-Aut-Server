"""Give the Linux player a private game lock while retaining one X11 display."""
from pathlib import Path
import re

_LOCK = '/tmp/EGOSOFT_X4.lock'


def command_with_private_lock(command, *, display, authorization):
    """Mount the case's lock at the engine path in this private /tmp only."""
    number = re.fullmatch(r':([0-9]+)(?:\.[0-9]+)?', display)
    if number is None:
        raise ValueError('local_x11_display_required')
    socket = Path('/tmp/.X11-unix') / ('X' + number[1])
    if not socket.is_socket():
        raise ValueError('local_x11_socket_unavailable')
    args = list(command)
    # Place /tmp before the case mounts, including when the checkout is in /tmp.
    root = args.index('--ro-bind')
    if args[root:root + 3] != ['--ro-bind', '/', '/']:
        raise ValueError('unexpected Linux game namespace')
    position = root + 3
    args[position:position] = ['--tmpfs', '/tmp', '--ro-bind', str(socket), str(socket)]
    if authorization.is_relative_to('/tmp'):
        args[position + 5:position + 5] = ['--ro-bind', str(authorization), str(authorization)]
    namespace = args[:args.index('--')]
    if namespace.count(_LOCK) != 1 or namespace[namespace.index(_LOCK) - 2] != '--bind':
        raise ValueError('unexpected Linux game lock')
    return args
