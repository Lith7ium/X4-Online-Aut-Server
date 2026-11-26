-- Opt-in, read-only combat capture for an admitted Company pilot (diagnostic runs only).
-- After the seat gate admits the pilot, this arms X4AuthorityCombatObserver on the offered
-- world's registry epoch and watches only the admitted registry index. It sends UI control
-- events and logs; it never writes hull, shield, damage, orders or any other game state.
-- The observer's own AUTH_COMBAT_OBS lines in the pilot's x4authority.log are the evidence.
local M = {}

local state = 'idle'
local pending = nil
local request = 0

local function log(message)
    DebugError('[X4LAN] AUTH_COMBAT_CAPTURE ' .. message)
end

local function config()
    local cfg = X4LAN_CONFIG and X4LAN_CONFIG.authority_combat_capture
    if type(cfg) ~= 'table' or type(cfg.epoch) ~= 'string' or not cfg.epoch:match('^%x+$')
            or #cfg.epoch ~= 32 or type(cfg.index) ~= 'number' or cfg.index < 1
            or cfg.index ~= math.floor(cfg.index) then
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
    AddUITriggeredEvent('X4AuthorityCombatObserver', control, values)
end

-- Exposed for offline tests: apply one observer status string.
function M.status(value)
    if type(value) ~= 'string' or pending == nil then
        return
    end
    local fields = split(value)
    if tonumber(fields[1]) ~= pending then
        return
    end
    pending = nil
    local cfg = config()
    if state == 'arming' then
        -- '<request>|Armed|<observer epoch>|<registry epoch>' once the registry is ready.
        if fields[2] == 'Armed' and fields[3] == cfg.epoch and fields[4] == cfg.epoch then
            state = 'armed'
        else
            state = 'failed'
            log('refused action=Arm status=' .. value)
        end
    elseif state == 'watching' then
        -- '<request>|Watched|<label>|<ship>|<code>|<owner>|<registry index>|<weapons>'
        if fields[2] == 'Watched' and tonumber(fields[7]) == cfg.index then
            state = 'capturing'
            log(string.format('capturing epoch=%s index=%d code=%s owner=%s',
                cfg.epoch, cfg.index, tostring(fields[5]), tostring(fields[6])))
        else
            state = 'failed'
            log('refused action=Watch status=' .. value)
        end
    end
end

RegisterEvent('X4AuthorityCombatObserver.Status', function(_, value)
    M.status(value)
end)

-- Called on every tick after the seat gate reports admission_ready().
function M.arm(seat)
    if seat == nil then
        return false
    end
    local cfg = config()
    if state == 'idle' then
        state = 'arming'
        send('Arm', { epoch = cfg.epoch, watch_player_ship = true, watch_player_owned = false })
    elseif state == 'armed' then
        state = 'watching'
        send('Watch', { registry_index = cfg.index, label = 'admitted_pilot',
                        as_target = true, as_source = false, watch_weapons = false })
    end
    return state ~= 'failed'
end

function M.state()
    return state
end

X4LAN_COMBAT_CAPTURE = M
return M
