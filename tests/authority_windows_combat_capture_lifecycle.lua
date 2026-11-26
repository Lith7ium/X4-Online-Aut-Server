-- Exercise the real participant update with fake local engine and UDP endpoints.
local valid, helm, paused, update = true, false, true, nil
local incoming, events, handlers, logs = {}, {}, {}, {}
local epoch, lease = string.rep('a', 32), string.rep('b', 32)
local screen = 'X4AuthorityClientCombatCapture'
local checkpoint, digest = 'checkpoint-' .. string.rep('d', 32), string.rep('e', 64)
local mode = arg[1] or 'pause'
package.loaded.ffi = {os = 'Windows', cdef = function() end, C = {
    IsPlayerValid = function() return valid end,
    IsGamePaused = function() return paused end,
}}
X4LAN_CONFIG = {
    authority_participant_port = 4321, authority_participant_lease = lease,
    authority_ticket = {world_epoch = epoch},
    authority_combat_capture = mode ~= 'disabled' and {epoch = epoch, index = 1, checkpoint = checkpoint, sha256 = digest} or nil,
}
function RegisterEvent(name, handler) handlers[name] = handler end
function AddUITriggeredEvent(name, action, values)
    events[#events + 1] = {name, action, values}
end
function DebugError(message) logs[#logs + 1] = message end
function GetCurRealTime() return 1 end
function SetScript(name, fn) assert(name == 'onUpdate'); update = fn end
X4LAN_LOCAL_UDP_MODULE = {load = function()
    return {open = function()
        return {send = function() return true end, receive = function() return table.remove(incoming, 1) end}
    end}
end}
X4LAN_AUTHORITY_X4_CONTROL_ENGINE = {new = function() return {} end}
X4LAN_AUTHORITY_CONTROL_STATE = {new = function(_, _, receipt)
    return {
        submit = function(self, _, _, id, operation)
            self.request = {request_id = id, operation = operation, status = 'confirmed'}
            return true
        end,
        on_update = function(self)
            if self.request then
                paused = self.request.operation == 'pause'
                receipt(self.request)
                self.request = nil
            end
            return true
        end,
    }
end}
local admitted = false
local seat = {
    admit = function() admitted = true; return true end,
    tick = function() return true end,
    ready = function() return helm end,
    controlled_actor = function() return helm and '0x7a1' or nil end,
    admission_ready = function() return true end,
}
X4LAN_AUTHORITY_SELECTED_SEAT = seat
dofile('src/authority/lua/client_combat_capture.lua')
dofile('tools/dev/authority_two_x4_participant_probe.lua')
local function command(id, operation, token)
    incoming[#incoming + 1] = table.concat({'CMD', token or lease, epoch, id, operation}, '|')
    update()
end
local function action(number, expected)
    local found = events[number]
    assert(found and found[1] == screen and found[2] == expected, 'missing diagnostic ' .. expected)
    return found[3]
end
local function status(value) handlers[screen .. '.Status'](nil, value) end
local function arm(number)
    local payload = action(number, 'Arm')
    assert(payload.epoch == epoch and payload.watch_player_ship and not payload.watch_player_owned)
    return payload.request_id
end

update()
assert(#events == 0 and not admitted, 'armed before resume')
command(1, 'resume')
update()
assert(#events == 0 and admitted, 'armed while passenger')
helm = true
update()
if mode == 'disabled' then
    command(2, 'pause')
    update()
    assert(#events == 0)
    return
end
local arming = arm(1)
update()
assert(#events == 1)
if mode == 'wrong-epoch' then
    status(arming .. '|Armed|' .. epoch .. '|' .. lease)
    update()
    action(2, 'Disarm')
    update()
    assert(#events == 2 and #logs == 1)
    return
end
status(arming .. '|Armed|' .. epoch .. '|' .. epoch)
update()
local watch = action(2, 'Watch')
assert(watch.registry_index == 1 and watch.as_target and watch.as_source and watch.actor == nil)
assert(not watch.watch_weapons, 'capturing confirmed contacts should not log every trigger pull')
if mode == 'refusal' or mode == 'wrong-actor' then
    if mode == 'refusal' then
        status(watch.request_id .. '|WatchRefused|admitted_pilot')
    else
        status(watch.request_id .. '|Watched|admitted_pilot|0x7a2|HVC-420|player|1|0')
    end
    update()
    action(3, 'Disarm')
    update()
    assert(#events == 3 and #logs == 1 and not logs[1]:find('AUTH_PARTICIPANT ERROR', 1, true))
    return
end
status(watch.request_id .. '|Watched|admitted_pilot|0x7a1|HVC-420|player|1|0')
update()
assert(#events == 2 and logs[1] == string.format(
    '[X4LAN] AUTH_COMBAT_CAPTURE capturing epoch=%s index=1 checkpoint=%s sha256=%s code=HVC-420 owner=player',
    epoch, checkpoint, digest))
if mode == 'fault' then
    command(2, 'pause', string.rep('c', 32))
    action(3, 'Disarm')
    update()
    assert(#events == 3)
elseif mode == 'player-loss' then
    valid = false
    update()
    action(3, 'Disarm')
    update()
    assert(#events == 3)
elseif mode == 'helm-loss' then
    helm = false
    update()
    action(3, 'Disarm')
    update()
    assert(#events == 3)
else
    command(2, 'pause')
    local disarming = action(3, 'Disarm').request_id
    status(watch.request_id .. '|Watched|admitted_pilot|0x7a1|HVC-420|player|1|0')
    update()
    assert(#events == 3)
    status(disarming .. '|Disarmed|2')
    command(3, 'resume')
    arm(4)
    X4LAN_AUTHORITY_SELECTED_SEAT = {}
    update()
    action(5, 'Disarm')
end
assert(logs[2] == '[X4LAN] AUTH_COMBAT_CAPTURE stopped epoch=' .. epoch .. ' index=1')
