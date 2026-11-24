"""Constrain a recovered server's stock renderer to Mesa software Vulkan."""
import re
import shutil
import subprocess
from pathlib import Path


def constrain(command, *, display, authorization, icd):
    """Keep Steam shared memory and mount only the chosen X11 pathname."""
    number = re.fullmatch(r':([0-9]+)(?:\.0)?', display)
    if number is None:
        raise ValueError('software Vulkan requires a local screen-zero X11 display')
    display_socket = Path('/tmp/.X11-unix') / ('X' + number[1])
    if not display_socket.is_socket():
        raise ValueError('private X11 socket unavailable')
    if not icd.is_absolute() or not icd.is_file():
        raise ValueError('absolute Mesa software Vulkan ICD required')
    icd = icd.resolve(strict=True)
    result = command.copy()
    devices = result.index('--dev-bind')
    result[devices:devices + 3] = ['--dev', '/dev', '--bind', '/dev/shm', '/dev/shm']
    lock = result.index('/tmp/EGOSOFT_X4.lock') - 2
    result[lock:lock] = [
        '--tmpfs', '/tmp', '--ro-bind', str(display_socket), str(display_socket),
        '--ro-bind', str(authorization), str(authorization),
    ]
    boundary = result.index('--')
    result[boundary:boundary] = [
        '--setenv', 'VK_DRIVER_FILES', str(icd), '--setenv', 'VK_ICD_FILENAMES', str(icd),
        '--setenv', 'SDL_VIDEODRIVER', 'x11',
    ]
    return result


def verify(command, output):
    """Verify the same namespace and environment that the game will enter."""
    vulkaninfo = shutil.which('vulkaninfo')
    if vulkaninfo is None:
        raise ValueError('vulkaninfo required to verify software Vulkan')
    prefix = command[:command.index('--') + 1]
    devices = subprocess.run(
        prefix + ['/usr/bin/find', '/dev', '-path', '/dev/shm', '-prune', '-o',
                  '-type', 'c', '-print', '-o', '-type', 'b', '-print'],
        capture_output=True, text=True, timeout=10, check=True)
    standard = {'/dev/full', '/dev/null', '/dev/random', '/dev/tty', '/dev/urandom', '/dev/zero',
                '/dev/pts/ptmx', '/dev/ptmx'}
    if set(devices.stdout.splitlines()) - standard:
        raise RuntimeError('unexpected device in software Vulkan namespace')
    result = subprocess.run(prefix + [vulkaninfo, '--summary'], capture_output=True,
                            text=True, timeout=45)
    output.write_text(result.stdout + result.stderr, encoding='utf-8')
    types = re.findall(r'^\s*deviceType\s*=\s*(\S+)\s*$', result.stdout, re.MULTILINE)
    if result.returncode or types != ['PHYSICAL_DEVICE_TYPE_CPU'] or 'llvmpipe' not in result.stdout:
        raise RuntimeError('Mesa software Vulkan verification failed; see ' + output.name)
