"""Stage and verify the private server-side Stock Undock trial."""
import json
from pathlib import Path
import time

from authority.checkpoints import CheckpointStore

ROOT = Path(__file__).resolve().parents[2]
DIGEST = 'cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5'
CHECKPOINT = 'checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca'
CUE = 'authority_company_undock_trial.xml'
DRIVER = 'authority_company_undock_trial.lua'
PREFIX = '[X4LAN] AUTH_COMPANY_UNDOCK_TRIAL '

# The portable descriptors come from the same saved dock and captain as DIGEST.
DRIVER_SOURCE = r'''
local trial_nonce, epoch = %s, %s
local screen = 'X4AuthorityCompanyUndockTrial'
local step, pending, sequence, next_check = 'initial', nil, 0, 0
local release_id, witnessed, attempted_actors
local fields = {epoch, '7354', 'WRN-302', 'cluster_01_sector001_macro', 'BIM-169',
    '507326418098681806', 'dockingbay_arg_s_01_lowtech_macro', 'dockarea_arg_m_station_02_lowtech_macro',
    '2211373338772438693'}

local function request(id, check_only, wrong_berth)
    pending = id
    assert(AddUITriggeredEvent(screen, 'Run', {
        nonce=id, epoch=fields[1], index=tonumber(fields[2]), ship_code=fields[3],
        sector=fields[4], station_code=fields[5], bay_seed=wrong_berth and '1' or fields[6],
        bay_macro=fields[7], dockarea_macro=fields[8], captain_seed=fields[9],
        check_only=check_only,
    }) ~= false)
end

assert(RegisterEvent(screen .. '.Result', function(_, row)
    if not pending or type(row) ~= 'string' or row:sub(1, #pending + 1) ~= pending .. '|' then return end
    local value = {}
    for part in (row .. '|'):gmatch('(.-)|') do value[#value + 1] = part end
    assert(#value == 18)
    for i, expected in ipairs(fields) do
        if step == 'wrong' and i == 6 then expected = '1' end
        assert(value[i + 2] == expected)
    end
    local status = value[2]
    DebugError('[X4LAN] AUTH_COMPANY_UNDOCK_TRIAL ' .. trial_nonce .. '|' .. row)
    if step == 'initial' then
        assert(status == 'busy' and value[13] == 'TradePerform', 'saved trade no longer active')
        step = 'wrong'
    elseif step == 'wrong' then
        assert(status == 'berth_mismatch', 'wrong berth unexpectedly accepted')
        step = 'wait'
    elseif step == 'wait' then
        assert(status == 'busy' or status == 'idle')
        if status == 'idle' then step = 'release' end
    elseif step == 'release' then
        if status == 'awaiting' then
            assert(not attempted_actors, 'stock order dispatched more than once')
            attempted_actors = table.concat(value, '|', 15, 18)
            return
        end
        assert(attempted_actors and status == 'undocked' and value[12] == '1'
            and attempted_actors == table.concat(value, '|', 15, 18), 'undock evidence incomplete')
        witnessed, step = true, 'duplicate'
    elseif step == 'duplicate' then
        assert(witnessed and status == 'duplicate_or_attempted')
        step = 'replay'
    elseif step == 'replay' then
        assert(witnessed and status == 'duplicate_or_attempted')
        step = 'done'
        DebugError('[X4LAN] AUTH_COMPANY_UNDOCK_TRIAL ' .. trial_nonce .. '|DONE')
    end
    pending = nil
end) ~= false)

function company_undock_trial_tick(paused)
    if pending or step == 'done' or (paused and step ~= 'initial') then return end
    local now = GetCurRealTime()
    if now < next_check then return end
    next_check = now + 0.25
    sequence = sequence + 1
    assert(sequence <= 160, 'stock trade never reached idle')
    local id = trial_nonce .. '-' .. sequence
    if step == 'release' then release_id = id end
    request(step == 'duplicate' and release_id or id, step == 'initial' or step == 'wait', step == 'wrong')
end
'''


def _append(path, anchor, text, *, before=False):
    body = path.read_text()
    if body.count(anchor) != 1:
        raise ValueError(f'unexpected developer hook in {path.name}')
    replacement = text + anchor if before else anchor + text
    path.write_text(body.replace(anchor, replacement))


def install(case, mode, epoch, nonce, **kwargs):
    extension = case / 'extensions/x4_lan_lab'
    cue, lua = extension / 'md' / CUE, extension / 'ui' / DRIVER
    with cue.open('xb') as output:
        output.write((ROOT / 'tools/dev' / CUE).read_bytes())
    with lua.open('x', encoding='ascii') as output:
        output.write(DRIVER_SOURCE % (json.dumps(nonce), json.dumps(epoch)))
    bridge = extension / 'ui/authority_world_bridge_probe.lua'
    manifest = extension / 'ui.xml'
    _append(bridge, ' local ok,reason=bridge:update();assert(ok,reason)\n',
            ' if registry_checkpoint:ready() then company_undock_trial_tick(C.IsGamePaused()) end\n')
    _append(manifest, '    <file name="ui/authority_world_bridge_probe.lua" />\n',
            f'    <file name="ui/{DRIVER}" />\n', before=True)
    return [cue, lua, bridge, manifest]


def await_result(log, nonce, deadline, check):
    prefix = PREFIX + nonce + '|'
    statuses = []
    rows = []
    with log.open() as stream:
        while time.monotonic() < deadline:
            check()
            for line in stream:
                if '[=ERROR=]' in line and ('CompanyUndockTrial' in line or CUE in line or DRIVER in line):
                    raise ValueError(f'Undock trial script error: {line.strip()}')
                if prefix not in line:
                    continue
                receipt = line.split(prefix, 1)[1].strip()
                if receipt == 'DONE':
                    if (statuses[:2] != ['busy', 'berth_mismatch']
                            or statuses[-4:] != ['awaiting', 'undocked', 'duplicate_or_attempted', 'duplicate_or_attempted']
                            or statuses.count('awaiting') != 1):
                        raise ValueError('Unexpected undock trial receipt sequence')
                    return {'undock_observed': True, 'statuses': statuses, 'rows': rows}
                statuses.append(receipt.split('|')[1])
                rows.append(receipt)
            time.sleep(.1)
    raise TimeoutError(f'No terminal undock receipt; observed stages: {statuses}')


def source_checkpoint(store):
    checkpoint = CheckpointStore(store).latest_marked_confirmed()
    if checkpoint.checkpoint_id != CHECKPOINT or checkpoint.sha256 != DIGEST:
        raise ValueError('Undock trial requires the copied confirmed 1dac checkpoint')
    return checkpoint
