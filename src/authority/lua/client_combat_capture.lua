-- Read-only observer for a resumed Company pilot in control of the admitted ship.
-- Each pilot overlay gives this capture a private Mission Director namespace.
local M = {}

local state = 'idle'
local pending, actor
local request = 0
local disabled = false

local function log(message)
    DebugError('[X4LAN] AUTH_COMBAT_CAPTURE ' .. message)
end

local function config()
    local cfg = X4LAN_CONFIG and X4LAN_CONFIG.authority_combat_capture
    if type(cfg) ~= 'table' or type(cfg.epoch) ~= 'string' or not cfg.epoch:match('^%x+$')
            or #cfg.epoch ~= 32 or type(cfg.index) ~= 'number' or cfg.index < 1
            or cfg.index ~= math.floor(cfg.index)
            or type(cfg.checkpoint) ~= 'string' or #cfg.checkpoint ~= 43
            or not cfg.checkpoint:match('^checkpoint%-[0-9a-f]+$')
            or type(cfg.sha256) ~= 'string' or #cfg.sha256 ~= 64 or not cfg.sha256:match('^[0-9a-f]+$') then
        error('combat capture configuration missing or invalid')
    end
    return cfg
end

local function split(text)
    local fields = {}
    for field in (text .. '|'):gmatch('([^|]*)|') do
        fields[#fields + 1] = field
    end
    return fields
end

local function send(control, values)
    request = request + 1
    pending = request
    values.request_id = request
    assert(AddUITriggeredEvent('X4AuthorityClientCombatCapture', control, values) ~= false)
end

function M.stop()
    if state == 'idle' or state == 'stopping' then return end
    if state == 'capturing' then
        local cfg = config()
        log(string.format('stopped epoch=%s index=%d', cfg.epoch, cfg.index))
    end
    state = 'stopping'
    actor = nil
    send('Disarm', {})
end

local function refuse(action)
    disabled = true
    log('refused action=' .. action)
end

local function status(value)
    if type(value) ~= 'string' or pending == nil then return end
    local fields = split(value)
    if tonumber(fields[1]) ~= pending then return end
    if state == 'stopping' then
        if fields[2] == 'Disarmed' then
            pending = nil
            state = 'idle'
        end
        return
    end
    pending = nil
    local cfg = config()
    if state == 'arming' then
        if fields[2] == 'Armed' and fields[3] == cfg.epoch and fields[4] == cfg.epoch then
            state = 'armed'
        else
            refuse('Arm')
        end
    elseif state == 'watching' then
        if fields[2] == 'Watched' and fields[3] == 'admitted_pilot' and fields[4] == actor
                and fields[6] == 'player' and tonumber(fields[7]) == cfg.index then
            state = 'capturing'
            log(string.format('capturing epoch=%s index=%d checkpoint=%s sha256=%s code=%s owner=player',
                cfg.epoch, cfg.index, cfg.checkpoint, cfg.sha256, tostring(fields[5])))
        else
            refuse('Watch')
        end
    end
end

RegisterEvent('X4AuthorityClientCombatCapture.Status', function(_, value)
    local ok = pcall(status, value)
    if not ok then refuse('Status') end
end)

-- The lease-checked participant update supplies a seat only while it is
-- admitted, running and still the selected Company seat.
function M.tick(seat)
    local controlled = seat and seat:controlled_actor()
    if disabled or not controlled or (actor and actor ~= controlled) then
        M.stop()
        return
    end
    local cfg = config()
    if state == 'idle' then
        actor = controlled
        state = 'arming'
        send('Arm', {epoch = cfg.epoch, watch_player_ship = true, watch_player_owned = false})
    elseif state == 'armed' then
        state = 'watching'
        send('Watch', {registry_index = cfg.index, label = 'admitted_pilot',
                        as_target = true, as_source = true, watch_weapons = false})
    end
end

X4LAN_COMBAT_CAPTURE = M
return M
