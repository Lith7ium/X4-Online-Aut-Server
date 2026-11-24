"""Private one-shot company fixture for the pinned 10,082-entry test seed."""
from pathlib import Path
import re

from authority.registry_availability_evidence import verify_join_snapshot

SCRIPT = Path(__file__).with_suffix('.xml')
SOURCE_DIGEST = '25e1a468a9c622e28fe028ef1e9b9dfe7b416124d894f8bda9d7f9358c38adcd'
EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
COUNT = 10082
FIRST, SECOND = 1970, 7354
SHIPS = {
    FIRST: ('WOO-831', 'ship_arg_s_scout_01_a_macro', 'cluster_14_sector001_macro'),
    SECOND: ('WRN-302', 'ship_arg_s_fighter_02_a_macro', 'cluster_01_sector001_macro'),
}
TAG = b'[X4LAN] AUTH_COMPANY_FIXTURE '

GATE = ''' if not activated and not company_fixture_done then
  assert(C.IsGamePaused() and engine.game_time() == registry_checkpoint_pause_time)
  if not company_fixture_deadline then
   company_fixture_deadline=GetCurRealTime()+10
   assert(RegisterEvent('X4AuthorityCompanyFixturePrepare.Result',function(_,row)
    if company_fixture_reply then
     company_fixture_reply='DUPLICATE'
     DebugError('[X4LAN] AUTH_COMPANY_FIXTURE PREFIXDUPLICATE')
    else
     company_fixture_reply=row or 'MALFORMED'
     DebugError('[X4LAN] AUTH_COMPANY_FIXTURE '..tostring(company_fixture_reply))
    end
   end)~=false)
   assert(AddUITriggeredEvent('X4AuthorityCompanyFixturePrepare','Prepare',
    {nonce='NONCE',epoch='EPOCH',count=COUNT})~=false)
  end
  if not company_fixture_reply then
   assert(GetCurRealTime()<company_fixture_deadline,'company fixture timed out')
   return
  end
  assert(company_fixture_reply=='PREFIXCOMPANY','company fixture refused')
  company_fixture_done=true
 end
'''


def validate_mode(args, *, require_snapshot, tls):
    enabled = getattr(args, 'prepare_disposable_company_fixture', False)
    if type(enabled) is not bool or (enabled and (not require_snapshot or tls is not None)):
        raise ValueError('disposable company fixture requires format-3 recovery without TLS')
    return enabled


def load_source(checkpoint):
    if checkpoint.sha256 != SOURCE_DIGEST:
        raise ValueError('disposable company fixture requires the pinned private seed')
    return _inspect(checkpoint, candidate_owner='ownerless')


def verify_saved(checkpoint):
    return _inspect(checkpoint, candidate_owner='player')


def _inspect(checkpoint, *, candidate_owner):
    evidence = verify_join_snapshot(checkpoint, describe_indices=(FIRST, SECOND))
    if (evidence['epoch'], evidence['issued_count']) != (EPOCH, COUNT):
        raise ValueError('disposable company fixture registry epoch or count changed')
    ships = evidence['described_ships']
    for index, (code, macro, sector) in SHIPS.items():
        attrs = ships[index]
        owner = 'player' if index == FIRST else candidate_owner
        if (attrs['code'], attrs['macro'], attrs['sector'], attrs['owner'],
                attrs['class'], attrs['connection'], attrs['parent_class']) != (
                code, macro, sector, owner, 'ship_s', 'space', 'zone'):
            raise ValueError(f'disposable company fixture saved ship {index} differs')
    return ships


def install(case, nonce):
    """Install the one-shot cue in an already prepared disposable save owner."""
    if type(nonce) is not str or re.fullmatch(r'[0-9a-f]{32}', nonce) is None:
        raise ValueError('invalid company fixture request nonce')
    lab = Path(case) / 'extensions/x4_lan_lab'
    probe = lab / 'ui/authority_world_bridge_probe.lua'
    script = lab / 'md' / SCRIPT.name
    text = probe.read_text(encoding='ascii')
    decl = 'local registry_checkpoint, registry_checkpoint_pause_time, registry_checkpoint_started\n'
    hook = ' if not activated then\n'
    if text.count(decl) != 1 or text.count(hook) != 1 or 'company_fixture_' in text:
        raise ValueError('disposable company fixture requires the private registry bridge owner')
    gate = GATE.replace('PREFIX', _prefix(nonce)).replace('NONCE', nonce)
    gate = gate.replace('EPOCH', EPOCH).replace('COUNT', str(COUNT))
    data = SCRIPT.read_bytes()
    with script.open('xb') as handle:
        handle.write(data)
    probe.write_text(text.replace(decl, decl +
        'local company_fixture_done, company_fixture_reply, company_fixture_deadline\n').replace(
            hook, gate + hook), encoding='ascii')
    return probe, script


def _prefix(nonce):
    return f'{nonce}|{EPOCH}|{SECOND}|'


def verify_log(log, nonce, read_evidence):
    raw = read_evidence(log, 64 << 20)
    rows = [line.split(TAG, 1)[1].strip() for line in raw.splitlines() if TAG in line]
    if rows != [(_prefix(nonce) + 'COMPANY').encode('ascii')]:
        raise ValueError('disposable company fixture mutation receipt missing, refused or repeated')
