"""Private native provider trial of two verified WRN poses and a marked save."""
import argparse
import json
from pathlib import Path
import secrets
from unittest.mock import patch

import authority_company_idle_npc_pose_trial as trial
import authority_company_pose_trial as pose
import authority_company_trade_trial as trade
import authority_company_undock_trial as undock
import authority_provider_server as provider

_TRADE_ENTRY = "        if trade_mode:\n            log = profile / 'engine-output.log'\n"
_TRADE_FINISH = "\n            result.update(success=True, final_world_state='paused')\n" \
                "            raise _WitnessFinished()\n        if bootstrap_snapshot:"
_NATIVE = """            check()
            adapter.resume()
            trial_result['native_undock'] = private_undock.await_result(
                log, trade_nonce, min(deadline, time.monotonic() + 130), check)
            game_stat = (game/'X4').stat()
            private_channel().bind({'pid': identity[0], 'start_ticks': identity[1],
                                    'exe_dev': game_stat.st_dev, 'exe_ino': game_stat.st_ino}, check)
            proof = trial_result['native_idle_npc_pose'] = {}
            private_pose.verify_two_poses(private_channel(), private_generation, check, proof)
            adapter.pause()
            private_pose.verify_paused_pose(private_channel(), private_generation, proof)
            raw_save = adapter.save()
            saved = store.publish(raw_save, marker=adapter.pending_marker)
            adapter.checkpoint_published(saved)
            verify()
            witness_pending = saved
            result.update(final_checkpoint_id=saved.checkpoint_id, final_checkpoint_sha256=saved.sha256)
"""


def compose(source):
    """Replace only the inspect diagnostic after the standard provider load."""
    if source.count(_TRADE_ENTRY) != 1 or source.count(_TRADE_FINISH) != 1:
        raise ValueError('unexpected private provider diagnostic anchors')
    entry = source.index(_TRADE_ENTRY)
    start = source.index("            if trade_mode != 'inspect':\n", entry)
    end = source.index(_TRADE_FINISH, start)
    return source[:start] + _NATIVE.rstrip('\n') + source[end:]


def run(args):
    checkpoint = undock.source_checkpoint(args.store)
    generation = secrets.token_hex(16)
    original_install = trade.install
    channel = None
    supervisor = provider.supervisor
    path = Path(supervisor.__file__)
    scoped = dict(supervisor.__dict__, private_undock=undock, private_pose=trial,
                  private_generation=generation, private_channel=lambda: channel)
    exec(compile(compose(path.read_text()), str(path), 'exec'), scoped)

    def stage(case, mode, epoch, nonce, **kwargs):
        nonlocal channel
        if channel is not None or mode != 'inspect':
            raise ValueError('one isolated inspect-mode pose bridge required')
        channel = pose.GamePoseChannel(epoch)
        channel.read_company_catalogue(checkpoint)
        if (channel.ship_codes.get(trial.INDEX), channel.ship_sectors.get(trial.INDEX)) != (trial.SHIP, trial.SECTOR):
            raise ValueError('confirmed Company WRN reservation differs')
        channel.ship_codes = {trial.INDEX: trial.SHIP}
        channel.ship_sectors = {trial.INDEX: trial.SECTOR}
        files = original_install(case, mode, epoch, nonce, **kwargs)
        files += trial.install(case, epoch, nonce, channel, generation, twice=True)
        return list(dict.fromkeys(files))

    try:
        with patch.object(trade, 'install', stage), patch.object(supervisor, 'main', scoped['main']):
            result = provider.main(['--game', str(args.game), '--store', str(args.store),
                                    '--out', str(args.out), '--xvfb', str(args.xvfb),
                                    '--timeout', '600', '--company-trade-trial', 'inspect'])
    finally:
        if channel is not None:
            channel.close()
    if result != 0:
        return result
    from authority_company_idle_npc_pose_save import verify
    proof = verify(args.store, args.out, game=args.game)
    with (args.out / 'verified-pose-checkpoint.json').open('x') as output:
        json.dump(proof, output, indent=2)
        output.write('\n')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for flag in ('game', 'store', 'out', 'xvfb'):
        parser.add_argument('--' + flag, type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
