-- Privately staged by the host admin provider. Applies host menu commands to the server world.
-- One command runs at a time. Every command ends with a DONE row.
local M = {}
local ffi = require('ffi')
local C = ffi.C
local screen = 'X4AuthorityHostAdmin'

for _, declaration in ipairs({
    'void AddPlayerMoney(int64_t money);',
    'bool HasResearched(const char* wareid);',
    'uint32_t GetNumAllFactions(bool includehidden);',
    'uint32_t GetAllFactions(const char** result, uint32_t resultlen, bool includehidden);',
}) do
    -- The game's own menus usually declared these already.
    pcall(ffi.cdef, declaration)
end

-- Commands that the MD script applies: verb -> number of arguments.
local md_verbs = {RELATION=2, RESEARCH=1, BLUEPRINT=1, REPAIR=2, RENAME=3, CARGO=4, FILL=1, STORE=3}
local numeric = {RELATION={a2=true}, REPAIR={a1=true}, RENAME={a1=true}, CARGO={a1=true, a4=true},
    STORE={a3=true}}
local MAX_ROW = 600
local MAX_MONEY_STEP = 1e11 -- one billion credits, in cents

local function clean(value)
    local text = tostring(value):gsub('[|\r\n]', ' ')
    if #text > MAX_ROW then text = text:sub(1, MAX_ROW) end
    return text
end

local function split(list)
    local items = {}
    for item in list:gmatch('[^,]+') do
        if not item:match('^[a-z][a-z0-9_]*$') or #item > 96 then return end
        items[#items + 1] = item
    end
    if #items > 0 and #items <= 40 then return items end
end

local function factions()
    local count = C.GetNumAllFactions(false)
    local buffer = ffi.new('const char*[?]', count)
    count = C.GetAllFactions(buffer, count, false)
    local ids = {}
    for i = 0, count - 1 do ids[#ids + 1] = ffi.string(buffer[i]) end
    table.sort(ids)
    return ids
end

function M.new(lease, net)
    local active, next_hello
    local last_id = 0

    local function send(row)
        active.sequence = active.sequence + 1
        net:send('ADMIN_ROW|' .. lease .. '|' .. active.id .. '|' .. active.sequence .. '|' .. row)
    end

    local function finish(row)
        if row then send(row) end
        send('DONE')
        active = nil
    end

    RegisterEvent(screen .. '.Row', function(_, param)
        if not active or not active.md or type(param) ~= 'string' then return end
        local id, row = param:match('^(%d+)|(.+)$')
        if id ~= active.id then return end
        send(clean(row))
        if active.expect and row:match('^RESULT|') then
            active.expect = active.expect - 1
            if active.expect == 0 then finish() end
        end
    end)
    RegisterEvent(screen .. '.Done', function(_, param)
        if active and active.md and not active.expect and tostring(param) == active.id then finish() end
    end)

    local function trigger(control, request)
        local ok, result = pcall(AddUITriggeredEvent, screen, control, request)
        return ok and result ~= false
    end

    local function state()
        send('MONEY|' .. ('%.0f'):format(GetPlayerMoney() or 0))
        local ok, ids = pcall(factions)
        if ok then
            for _, id in ipairs(ids) do
                if id ~= 'player' then
                    local relation = GetUIRelation(id) or 0
                    local name = GetFactionData(id, 'name') or id
                    send('FACTION|' .. id .. '|' .. ('%d'):format(relation) .. '|' .. clean(name))
                end
            end
        else
            send('ERROR|faction_list')
        end
        active.md = true
        if not trigger('Snapshot', {id=active.id}) then finish('ERROR|snapshot_dispatch') end
    end

    local function money(value)
        local delta = tonumber(value)
        if not delta or delta ~= math.floor(delta) or math.abs(delta) > MAX_MONEY_STEP then
            return finish('ERROR|invalid_amount')
        end
        local cents = math.floor((GetPlayerMoney() or 0) * 100)
        if delta < -cents then delta = -cents end
        if delta ~= 0 then C.AddPlayerMoney(delta) end
        finish('MONEY|' .. ('%.0f'):format(GetPlayerMoney() or 0))
    end

    local function researched(list)
        local wares = split(list or '')
        if not wares then return finish('ERROR|invalid_wares') end
        for _, ware in ipairs(wares) do
            send('HAS|' .. ware .. '|' .. (C.HasResearched(ware) and '1' or '0'))
        end
        finish()
    end

    local function apply(verb, fields)
        local arguments = {}
        for i = 1, md_verbs[verb] do
            local value = fields[4 + i]
            if not value or value == '' then return finish('ERROR|missing_argument') end
            if numeric[verb] and numeric[verb]['a' .. i] then
                value = tonumber(value)
                if not value then return finish('ERROR|invalid_number') end
            end
            arguments['a' .. i] = value
        end
        local targets = {arguments.a1}
        if verb == 'RESEARCH' or verb == 'BLUEPRINT' then
            targets = split(arguments.a1)
            if not targets then return finish('ERROR|invalid_wares') end
        end
        active.md, active.expect = true, #targets
        for _, target in ipairs(targets) do
            local request = {id=active.id, verb=verb}
            for key, value in pairs(arguments) do request[key] = value end
            request.a1 = target
            if not trigger('Apply', request) then
                -- Rows already raised for earlier wares still arrive; end the command here.
                return finish('ERROR|apply_dispatch')
            end
        end
    end

    local function receive(now)
        if active or not net:pending() then return end
        local data = net:receive()
        if type(data) ~= 'string' then return end
        local fields = {}
        for value in (data .. '|'):gmatch('([^|]*)|') do fields[#fields + 1] = value end
        local id = fields[3] or ''
        if fields[1] ~= 'ADMIN' or fields[2] ~= lease or not id:match('^%d+$') or #id > 9
                or tonumber(id) <= last_id then
            return
        end
        last_id = tonumber(id)
        local verb = fields[4]
        active = {id=id, sequence=0, deadline=now + 10}
        DebugError('[X4LAN] AUTH_HOST_ADMIN ' .. id .. ' ' .. tostring(verb))
        if verb == 'STATE' then
            state()
        elseif verb == 'MONEY' then
            money(fields[5])
        elseif verb == 'RESEARCHED' then
            researched(fields[5])
        elseif md_verbs[verb] then
            apply(verb, fields)
        else
            finish('ERROR|unknown_verb')
        end
    end

    return function(paused)
        local now = GetCurRealTime()
        if not next_hello or now >= next_hello then
            net:send('ADMIN_HELLO|' .. lease)
            next_hello = now + .5
        end
        if active and active.md and now >= active.deadline then finish('ERROR|timeout') end
        if paused then return end
        receive(now)
    end
end

X4LAN_AUTHORITY_HOST_ADMIN = M
return M
