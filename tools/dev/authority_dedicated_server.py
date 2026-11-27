#!/usr/bin/env python3
"""Start the provider as a long-running dedicated world from the newest clean restart source.

Each start uses a fresh run directory under --runs. The restart source is the checkpoints
directory of the newest run whose result.json reports a clean, paused stop with a confirmed
final checkpoint. Without one, the --initial-store is used. A crashed or unclean run is
skipped, because the supervisor accepts only a cleanly stopped source
(verify_previous_source). This wrapper selects; the supervisor still verifies everything.

It is written for a systemd user service. After waiting for the game user's Steam client,
it replaces itself with the provider. systemd's SIGTERM then reaches the supervisor, which
saves and pauses the world before X4 stops. The world keeps running while nobody is
connected (--persistent --keep-world-running).
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / 'tools/dev/authority_provider_server.py'
RUN_NAME = re.compile(r'run-\d{8}T\d{6}Z-\d{3}')
OWNED = ('--game', '--store', '--out', '--timeout', '--persistent', '--keep-world-running',
         '--empty-checkpoint-seconds')
EX_TEMPFAIL = 75
X4_LOCK = Path('/tmp/EGOSOFT_X4.lock')
MAX_RESULT_BYTES = 1 << 20


def _reject_constant(value):
    raise ValueError('non-finite JSON number: ' + value)


def clean_stop(run):
    """Return None if run ended cleanly with a confirmed final checkpoint, otherwise the reason."""
    path = run / 'result.json'
    if path.is_symlink() or not path.is_file():
        return 'no result.json (crashed or still running)'
    if path.stat().st_size > MAX_RESULT_BYTES:
        return 'result.json too large'
    try:
        result = json.loads(path.read_bytes().decode('utf-8'), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        return 'unreadable result.json'
    if type(result) is not dict or result.get('supervisor_version') != 1:
        return 'unsupported result.json'
    if (result.get('success') is not True or result.get('final_world_state') != 'paused'
            or result.get('owned_processes_remaining') != [] or 'cleanup_error' in result
            or 'cleanup_errors' in result):
        return 'not a clean paused stop'
    if not isinstance(result.get('final_checkpoint_id'), str) or not isinstance(result.get('final_checkpoint_sha256'), str):
        return 'no final checkpoint'
    store = run / 'checkpoints'
    if store.is_symlink() or not store.is_dir() or not (store / 'CONFIRMED').is_file():
        return 'no confirmed checkpoint store'
    return None


def restart_source(runs, initial_store):
    """Return (store, skipped): the newest clean run's checkpoints, or the initial store."""
    skipped = []
    names = sorted((p.name for p in runs.iterdir() if RUN_NAME.fullmatch(p.name)), reverse=True)
    for name in names:
        run = runs / name
        if run.is_symlink() or not run.is_dir():
            skipped.append((name, 'not a run directory'))
            continue
        reason = clean_stop(run)
        if reason is None:
            return run / 'checkpoints', skipped
        skipped.append((name, reason))
    return initial_store, skipped


def prunable_runs(runs, source, keep):
    """Return the run directories that pruning may delete, oldest first.

    Always kept: the newest `keep` runs, the source run and every run newer than it.
    Newer runs ended uncleanly and may still be needed to investigate a crash.
    Without a source run (the initial store is used) nothing is pruned.
    """
    if source.name != 'checkpoints' or source.parent.parent != runs or not RUN_NAME.fullmatch(source.parent.name):
        return []
    names = sorted(name for name in os.listdir(runs) if RUN_NAME.fullmatch(name)
                   and not (runs / name).is_symlink() and (runs / name).is_dir())
    newest = set(names[-keep:])
    return [runs / name for name in names if name < source.parent.name and name not in newest]


def remove_run(run):
    """Delete one finished run directory and its launch record, never following symlinks."""
    if run.is_symlink() or not run.is_dir():
        raise ValueError(f'{run.name} is not a run directory')
    # Saved cases can contain read-only directories; make only real directories writable.
    for folder, directories, _files in os.walk(run, followlinks=False):
        for name in directories:
            path = Path(folder, name)
            if not path.is_symlink():
                os.chmod(path, stat.S_IMODE(path.lstat().st_mode) | stat.S_IRWXU)
    shutil.rmtree(run)
    record = run.parent / (run.name + '.launch.json')
    if record.is_file() and not record.is_symlink():
        record.unlink()


def next_run(runs, now):
    stamp = now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for number in range(1000):
        run = runs / f'run-{stamp}-{number:03d}'
        if not run.exists() and not run.is_symlink():
            return run
    raise ValueError('no free run name')


def beneath_local(path, root, what):
    local = (root / '.local').resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved == local or not resolved.is_relative_to(local):
        raise ValueError(f'{what} must be a non-symlink path beneath {local}')
    return resolved


def provider_command(args, store, out, passthrough):
    for flag in passthrough:
        if flag.split('=', 1)[0] in OWNED:
            raise ValueError(f'{flag.split("=", 1)[0]} is set by the dedicated launcher')
    return [sys.executable, str(PROVIDER), *passthrough,
            '--game', str(args.game), '--store', str(store), '--out', str(out),
            '--timeout', str(args.startup_timeout), '--persistent', '--keep-world-running',
            '--empty-checkpoint-seconds', str(args.empty_checkpoint_seconds)]


def steam_running(home, uid, proc=Path('/proc')):
    """A light pre-check of the game user's Steam client; the supervisor checks it again."""
    try:
        pid = int((home / '.steam/steam.pid').read_text().strip())
        status = (proc / str(pid) / 'status').read_text()
    except (OSError, ValueError):
        return False
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    uids = fields.get('Uid', '').split()
    return fields.get('Name', '').strip() == 'steam' and bool(uids) and uids[0] == str(uid)


def x4_processes(uid, proc=Path('/proc')):
    """Return the PIDs of this user's processes named X4, the game's executable name."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / 'status').read_text()
        except OSError:
            continue  # the process ended or is not readable
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        uids = fields.get('Uid', '').split()
        if fields.get('Name', '').strip() == 'X4' and uids and uids[0] == str(uid):
            found.append(int(entry.name))
    return found


def clear_stale_x4_lock(uid, *, lock=X4_LOCK, proc=Path('/proc')):
    """Remove the supervisor's X4 lock only when no X4 of this user still runs.

    The supervisor keeps the lock when it cannot confirm that its processes stopped,
    for example after it was killed. Under systemd the previous run's processes are gone
    before a restart; this check also requires an empty regular file owned by this user
    and no running X4 process of this user. Return True if a stale lock was removed.
    """
    try:
        info = lock.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size != 0:
        raise ValueError(f'{lock} is not an empty lock file of this user; remove it by hand after checking')
    running = x4_processes(uid, proc)
    if running:
        raise ValueError(f'X4 is still running (PID {", ".join(map(str, running))}); not removing {lock}')
    current = lock.lstat()
    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise ValueError(f'{lock} changed while it was checked')
    lock.unlink()
    return True


def wait_for_steam(seconds, *, home, uid, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + seconds
    while not steam_running(home, uid):
        if clock() >= deadline:
            return False
        sleep(5)
    return True


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--game', type=Path, required=True, help='directory containing pinned Linux X4')
    parser.add_argument('--initial-store', type=Path, required=True,
                        help='verified first source under .local, used until a run stops cleanly')
    parser.add_argument('--runs', type=Path, required=True, help='directory under .local for the run directories')
    parser.add_argument('--empty-checkpoint-seconds', type=float, default=900.0,
                        help='save the empty running world this often (60-86400, default 900)')
    parser.add_argument('--startup-timeout', type=float, default=3600.0, help='bound for provider startup')
    parser.add_argument('--steam-wait-seconds', type=float, default=600.0,
                        help='wait this long for the Steam client before giving up (default 600)')
    parser.add_argument('--keep-runs', type=int,
                        help='before starting, delete runs older than the restart source beyond the newest N (1-1000)')
    parser.add_argument('--clear-stale-x4-lock', action='store_true',
                        help=f'remove {X4_LOCK} left by a killed run when no X4 of this user is running')
    parser.add_argument('--dry-run', action='store_true', help='print the selection and command, start nothing')
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough = []
    if '--' in argv:
        index = argv.index('--')
        argv, passthrough = argv[:index], argv[index + 1:]
    args = parser.parse_args(argv)
    if not math.isfinite(args.empty_checkpoint_seconds) or not 60 <= args.empty_checkpoint_seconds <= 86400:
        parser.error('--empty-checkpoint-seconds must be 60 to 86400')
    if not math.isfinite(args.startup_timeout) or not 0 < args.startup_timeout <= 86400:
        parser.error('--startup-timeout must be above 0 and at most 86400')
    if not math.isfinite(args.steam_wait_seconds) or not 0 <= args.steam_wait_seconds <= 86400:
        parser.error('--steam-wait-seconds must be 0 to 86400')
    if args.keep_runs is not None and not 1 <= args.keep_runs <= 1000:
        parser.error('--keep-runs must be 1 to 1000')
    return args, passthrough


def main(argv=None, *, root=ROOT, now=None, execv=os.execv, steam_wait=None, clear_lock=None):
    args, passthrough = parse(argv)
    try:
        if not args.dry_run and hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise ValueError('run as the unprivileged game user, not root')
        runs = beneath_local(args.runs, root, '--runs')
        initial = beneath_local(args.initial_store, root, '--initial-store')
        if not initial.is_dir():
            raise ValueError('--initial-store must be an existing checkpoint directory')
        runs.mkdir(mode=0o700, parents=True, exist_ok=True)
        store, skipped = restart_source(runs, initial)
        out = next_run(runs, now or datetime.now(timezone.utc))
        command = provider_command(args, store, out, passthrough)
        prune = prunable_runs(runs, store, args.keep_runs) if args.keep_runs is not None else []
    except (OSError, ValueError) as error:
        print(f'Dedicated launcher refused: {error}', file=sys.stderr)
        return 2
    record = {'launcher_version': 1, 'source': str(store), 'source_is_initial': store == initial,
              'skipped_runs': [{'run': name, 'reason': reason} for name, reason in skipped],
              'out': str(out)}
    if args.dry_run:
        record['would_prune_runs'] = [run.name for run in prune]
        print(json.dumps(record, indent=2))
        print(' '.join(command))
        return 0
    wait = steam_wait or (lambda: wait_for_steam(args.steam_wait_seconds, home=Path.home(), uid=os.getuid()))
    if not wait():
        print('Dedicated launcher: the Steam client of this user is not running', file=sys.stderr)
        return EX_TEMPFAIL
    if args.clear_stale_x4_lock:
        try:
            record['cleared_stale_x4_lock'] = (clear_lock or (lambda: clear_stale_x4_lock(os.getuid())))()
        except (OSError, ValueError) as error:
            print(f'Dedicated launcher: {error}', file=sys.stderr)
            return EX_TEMPFAIL
    # Prune only when a start really follows. A failed deletion leaves that run for the operator.
    record['pruned_runs'], errors = [], []
    for run in prune:
        try:
            remove_run(run)
            record['pruned_runs'].append(run.name)
        except (OSError, ValueError) as error:
            errors.append({'run': run.name, 'error': type(error).__name__})
    if errors:
        record['prune_errors'] = errors
    print(json.dumps(record, indent=2))
    # Keep a record of each selection beside its run. The provider requires that out does not exist yet.
    (runs / (out.name + '.launch.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    sys.stdout.flush()
    execv(command[0], command)
    return 1  # only reached with a test double


if __name__ == '__main__':
    raise SystemExit(main())
