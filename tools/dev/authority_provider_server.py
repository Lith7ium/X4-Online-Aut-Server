"""Start one recovered authority server on a private software-rendered display."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile

import authority_recovered_loopback_server as supervisor
from linux_gate_live import lavapipe_icd


def authorize(tool, path, display, cookie):
    subprocess.run([tool, '-q', '-f', str(path)], text=True,
                   input=f'add {display} MIT-MAGIC-COOKIE-1 {cookie}\n',
                   capture_output=True, timeout=5, check=True)


@contextmanager
def private_display(xvfb, xauth, xdpyinfo):
    with tempfile.TemporaryDirectory(prefix='x4-authority-display-') as directory:
        authorization = Path(directory) / 'authority'
        authorization.touch(mode=0o600, exist_ok=False)
        cookie = secrets.token_hex(16)
        # Xvfb loads the cookie regardless of the record's display number.
        # The client-side record for the allocated display is added below.
        authorize(xauth, authorization, ':9999', cookie)
        ready, notify = os.pipe()
        process = None
        try:
            with (Path(directory) / 'xvfb.log').open('wb') as log:
                process = subprocess.Popen(
                    [xvfb, '-displayfd', str(notify), '-screen', '0', '800x600x24',
                     '-nolisten', 'tcp', '-noreset', '-auth', str(authorization)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, pass_fds=(notify,),
                    start_new_session=True)
        finally:
            os.close(notify)
            if process is None:
                os.close(ready)

        try:
            if not select.select([ready], [], [], 10)[0]:
                raise RuntimeError('Xvfb did not allocate a display')
            number = os.read(ready, 16).strip()
            if not number.isdigit() or process.poll() is not None:
                raise RuntimeError('Xvfb failed to initialize')
            display = ':' + number.decode('ascii')
            authorize(xauth, authorization, display, cookie)
            environment = dict(os.environ, DISPLAY=display, XAUTHORITY=str(authorization))
            subprocess.run([xdpyinfo], env=environment, capture_output=True, check=True, timeout=5)
            # Reject a display that also accepts clients without the private cookie.
            environment['XAUTHORITY'] = str(authorization.parent / 'missing')
            if subprocess.run([xdpyinfo], env=environment, capture_output=True, timeout=5).returncode == 0:
                raise RuntimeError('Xvfb accepts connections without the private cookie')
            yield display, authorization
        finally:
            os.close(ready)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--game', type=Path, required=True, help='directory containing pinned Linux X4')
    parser.add_argument('--store', type=Path, required=True, help='verified recovery source under .local')
    parser.add_argument('--out', type=Path, required=True, help='fresh output directory under .local')
    parser.add_argument('--xvfb', help='Xvfb executable; defaults to PATH')
    parser.add_argument('--vulkan-icd', type=Path, help='Mesa lavapipe ICD; defaults to system detection')
    parser.add_argument('--timeout', type=float, default=3600, help='total run duration in seconds')
    parser.add_argument('--company-motion', action='store_true',
                        help='enable the pinned movement channel and require company ship selection; TLS flags required')
    parser.add_argument('--require-company-ship', action='store_true',
                        help='require every player to select an available company ship; enables full registry checks')
    parser.add_argument('--registry-status', action='store_true', help='verify saved registry status')
    parser.add_argument('--require-registry-snapshot', action='store_true',
                        help='require and verify format-3 saved registry snapshots')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--park-server-avatar', choices=('prepare', 'verify'),
                      help='prepare or verify the parked avatar on the isolated two-ship fixture')
    mode.add_argument('--company-pose-trial', action='store_true',
                      help='run the isolated two-ship pose trial with authenticated company motion')
    mode.add_argument('--verify-company-pose', action='store_true',
                      help='cold-check the saved company pose in an isolated run with no clients')
    tls = parser.add_argument_group('optional TLS gateway')
    tls.add_argument('--tls-bind', help='IPv4 address for the TLS listener')
    tls.add_argument('--tls-port', type=int, help='TLS listener port; 0 selects an available port')
    tls.add_argument('--tls-advertise', help='client-reachable IPv4 address or DNS name')
    tls.add_argument('--tls-advertise-port', type=int, help='client-reachable port, if different')
    tls.add_argument('--tls-certificate', type=Path, help='PEM server certificate')
    tls.add_argument('--tls-private-key', type=Path, help='PEM server private key')
    tls.add_argument('--tls-shared-key-file', type=Path, help='private file containing the shared key')
    args = parser.parse_args(argv)
    server_flags = []
    for flag in ('bind', 'port', 'advertise', 'advertise-port', 'certificate',
                 'private-key', 'shared-key-file'):
        value = getattr(args, 'tls_' + flag.replace('-', '_'))
        if value is not None:
            server_flags.extend(('--tls-' + flag, str(value)))
    if server_flags or args.require_company_ship or args.company_motion:
        if args.park_server_avatar:
            parser.error('--park-server-avatar uses an isolated fixture with no TLS or Company hosting options')
        if args.verify_company_pose:
            parser.error('--verify-company-pose uses an isolated fixture with no TLS or Company hosting options')
    company_motion = args.company_motion or args.company_pose_trial
    require_ship = args.require_company_ship or company_motion
    if args.registry_status:
        server_flags.append('--registry-status')
    if args.require_registry_snapshot or require_ship or args.park_server_avatar or args.verify_company_pose:
        server_flags.append('--require-registry-snapshot')
    if args.park_server_avatar:
        server_flags.extend(('--park-server-avatar', args.park_server_avatar))
    if require_ship:
        server_flags.append('--require-company-ship')
    if company_motion:
        server_flags.append('--company-motion')
    if args.company_pose_trial:
        server_flags.append('--company-pose-trial')
    if args.verify_company_pose:
        server_flags.append('--verify-company-pose')
    xvfb, xauth, xdpyinfo = (shutil.which(name) for name in (args.xvfb or 'Xvfb', 'xauth', 'xdpyinfo'))
    icd = args.vulkan_icd or lavapipe_icd()
    if sys.platform != 'linux' or os.geteuid() == 0:
        parser.error('run as an unprivileged Linux user')
    if not all((xvfb, xauth, xdpyinfo, icd, shutil.which('bwrap'), shutil.which('vulkaninfo'))):
        parser.error('Xvfb, xauth, xdpyinfo, bubblewrap, vulkaninfo and Mesa lavapipe are required')
    old_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with private_display(xvfb, xauth, xdpyinfo) as (display, authorization):
            os.environ['DISPLAY'] = display
            os.environ['XAUTHORITY'] = str(authorization)
            return supervisor.main([
                *server_flags, '--game', str(args.game), '--store', str(args.store),
                '--out', str(args.out), '--display', display, '--timeout', str(args.timeout),
                '--software-vulkan-icd', str(icd.resolve(strict=True)),
            ])
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Provider startup failed: {error}', file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_handler)


if __name__ == '__main__':
    raise SystemExit(main())
