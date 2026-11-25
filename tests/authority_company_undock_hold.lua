local hold = dofile('src/authority/lua/company_undock_hold.lua')
assert(hold.install({enabled = false}) == nil)

local epoch, rid = ('a'):rep(32), ('b'):rep(32)
local function setup()
    local f = {
        admission = 'attempt-1', actor = '0x1234', permission = true,
        berth = {sector = 'sector_a', station_code = 'BIM-169', bay_seed = '507326418098681806',
            bay_macro = 'bay_macro', dockarea_macro = 'area_macro', external = true, occupied_by_ship = true},
        reads = {}, requests = {}, commands = {}, stock = {},
    }
    local function stock(...)
        f.stock[#f.stock + 1] = {n = select('#', ...), ...}
    end
    local menu = {buttonUndock = stock, isUndockButtonActive = function() return f.permission and not f.locked end}
    local seat = {
        controlled_actor = function() return f.actor end,
        read_occupied_berth = function(_, callback)
            f.reads[#f.reads + 1] = callback
            return true
        end,
    }
    local api = assert(hold.install({
        enabled = true, menu = menu, seat = seat, epoch = epoch, ship_index = 7354,
        admission = function() return f.admission end,
        menu_context = function(actor)
            return actor == (f.menu_actor or '0x1234') and not f.menu_changed
        end,
        permission = menu.isUndockButtonActive,
        request_id = function() return rid end,
        request = function(request) f.requests[#f.requests + 1] = request; return true end,
        hold = function(action, binding, callback)
            assert(binding.actor == '0x1234' and binding.admission == 'attempt-1')
            assert(binding.epoch == epoch and binding.ship_index == 7354)
            assert(binding.berth.bay_seed == '507326418098681806')
            f.commands[#f.commands + 1] = {action, callback}
            return true
        end,
    }))
    f.menu, f.api = menu, api
    function f:read()
        table.remove(self.reads, 1)(self.berth)
    end
    function f:armed()
        assert(self.api:arm())
        self:read()
        assert(self.api:state() == 'setting' and self.commands[1][1] == 'set')
        self.locked = true
        self.commands[1][2](true)
        assert(self.api:state() == 'held')
    end
    function f:pending()
        self:armed()
        self.menu.buttonUndock('click', nil, 'argument', nil)
        self.menu.buttonUndock('repeat')
        self:read()
        self.menu.buttonUndock('repeat')
        assert(#self.requests == 1 and self.api:state() == 'pending' and #self.stock == 0)
        local result = {outcome = 'confirmed'}
        for key, value in pairs(self.requests[1]) do result[key] = value end
        assert(result.request_id == rid and result.sector == 'sector_a'
            and result.station_code == 'BIM-169' and result.bay_seed == self.berth.bay_seed
            and result.bay_macro == self.berth.bay_macro and result.dockarea_macro == self.berth.dockarea_macro
            and result.actor == nil and result.epoch == nil and result.ship_index == nil)
        return result
    end
    return f
end

local f = setup()
f.permission = false
assert(not f.api:arm() and #f.commands == 0)
f.permission = true
local grant = f:pending()
for key, different in pairs({request_id = ('c'):rep(32), outcome = 'refused',
                             station_code = 'OTHER', sector = 'sector_b', bay_seed = '2',
                             bay_macro = 'wrong', dockarea_macro = 'wrong'}) do
    local original = grant[key]
    grant[key] = different
    assert(not f.api:confirm(grant))
    grant[key] = original
end
f.menu.buildToCancel = {1, 2}
assert(not f.api:confirm(grant))
f.menu.buildToCancel = nil
assert(f.api:confirm(grant) and not f.api:confirm(grant))
f:read()
assert(f.commands[2][1] == 'release' and f.locked and #f.stock == 0)
f.locked = false
f.commands[2][2](true)
f.commands[2][2](true)
f.menu.buttonUndock()
assert(f.api:state() == 'consumed' and #f.stock == 1)
assert(f.stock[1].n == 4 and f.stock[1][1] == 'click' and f.stock[1][2] == nil
    and f.stock[1][3] == 'argument' and f.stock[1][4] == nil)

for _, phase in ipairs({'pending', 'authorizing', 'releasing'}) do
    f = setup()
    grant = f:pending()
    if phase ~= 'pending' then assert(f.api:confirm(grant)) end
    if phase == 'releasing' then f:read() end
    assert(f.api:state() == phase)
    f.api:stop()
    if phase == 'authorizing' then f:read() end
    if phase == 'releasing' then f.commands[2][2](true) end
    f.menu.buttonUndock()
    assert(not f.api:confirm(grant) and f.api:state() == 'stopped' and #f.stock == 0)
    assert(#f.commands == (phase == 'releasing' and 2 or 1))
end

for _, change in ipairs({'admission', 'actor', 'menu_changed', 'berth'}) do
    f = setup()
    grant = f:pending()
    if change == 'berth' then
        -- A bay change at the same station passes the UI context but fails
        -- the seat's fresh physical read before any release can be dispatched.
        f.berth.bay_seed = '222'
        assert(f.api:confirm(grant))
        f:read()
    else
        local original = f[change]
        f[change] = true
        f.api:tick()
        f[change] = original
    end
    assert(f.api:state() == 'failed' and not f.api:confirm(grant) and #f.commands == 1 and #f.stock == 0)
end

f = setup()
f:armed()
f.menu_actor = '0x1235'
f.menu.buttonUndock('unrelated')
assert(#f.requests == 0 and #f.stock == 1 and f.stock[1][1] == 'unrelated')

f = setup()
grant = f:pending()
assert(f.api:confirm(grant))
f:read()
f.commands[2][2](false)
assert(f.api:state() == 'failed' and #f.stock == 0 and not f.api:confirm(grant))

-- Exercise the actual native IPC adapter through two correlated MD answers.
local nonce = ('d'):rep(32)
local events, native, packets = {}, {}, {}
local permit, application_clock, replayed = true, 1, 0
local berth = {sector = 'sector_a', station_code = 'BIM-169', bay_seed = '507326418098681806',
    bay_macro = 'bay_macro', dockarea_macro = 'area_macro', external = true, occupied_by_ship = true}
local menu = {buttonUndock = function() replayed = replayed + 1 end,
    isUndockButtonActive = function() return permit end}
local seat = {
    controlled_actor = function() return '0x1234' end,
    read_occupied_berth = function(_, reply) reply(berth); return true end,
}
function RegisterEvent(name, callback)
    events[name] = callback
    return true
end
function AddUITriggeredEvent(screen, action, args)
    assert(screen == 'X4AuthorityClientUndock' and action == 'Change')
    assert(args.nonce == nonce and args.request_id == rid and args.epoch == epoch and args.index == 7354)
    assert(args.actor == '0x1234' and args.bay_seed == berth.bay_seed and args.sector == berth.sector)
    native[#native + 1] = args.action
    return true
end
function DebugError() end
hold.install_x4 = function(options)
    options.menu = menu
    options.permission = menu.isUndockButtonActive
    options.menu_context = function() return true end
    return hold.install(options)
end

local ipc = hold.new_ipc({lease = 'lease', undock_nonce = nonce, undock_request = rid,
    epoch = epoch, index = 7354}, {send = function(_, packet) packets[#packets + 1] = packet end},
    function() return application_clock end, function() return true end)
local stock_row = menu.buttonUndock
ipc:tick(nil)
assert(stock_row ~= menu.buttonUndock and #native == 0)
ipc:set_running(true)
ipc:tick(seat)
assert(native[1] == 'set')
local answer = events['X4AuthorityClientUndock.Result']
answer(nil, ('e'):rep(32) .. '|set|ok')
menu.buttonUndock()
assert(#packets == 0 and replayed == 0)
permit = false
answer(nil, nonce .. '|set|ok')
ipc:set_running(false)
ipc:tick(seat)
menu.buttonUndock()
assert(#packets == 0 and #native == 1 and replayed == 0)
ipc:set_running(true)
ipc:tick(seat)
menu.buttonUndock()
menu.buttonUndock()
ipc:tick(seat)
assert(packets[1] == 'T|lease|UNDOCK|' .. rid .. '|sector_a|BIM-169|507326418098681806|bay_macro|area_macro')
ipc:tick(seat)
assert(#packets == 1)
application_clock = 1.25
ipc:tick(seat)
assert(#packets == 2 and packets[1] == packets[2])
local receipt = {'UNDOCK_RESULT', 'confirmed', rid, 'sector_a', 'BIM-169', '2', 'bay_macro', 'area_macro'}
ipc:result(receipt)
assert(#native == 1)
receipt[6] = berth.bay_seed
ipc:set_running(false)
ipc:result(receipt)
ipc:tick(seat)
assert(#native == 1 and #packets == 2 and replayed == 0)
ipc:set_running(true)
ipc:tick(seat)
assert(native[2] == 'release' and replayed == 0)
permit = true
answer(nil, nonce .. '|release|ok')
answer(nil, nonce .. '|release|ok')
assert(replayed == 1 and #native == 2)

-- A receipt retained during pause must pass fresh physical and menu checks.
for _, fence in ipairs({'berth', 'menu', 'admission', 'terminal', 'inflight'}) do
    f = setup()
    grant = f:pending()
    if fence == 'inflight' then
        assert(f.api:confirm(grant))
        f:read()
        assert(f.api:state() == 'releasing')
    end
    f.api:pause()
    if fence == 'terminal' then
        f.api:stop()
    elseif fence == 'berth' then
        f.berth.bay_seed = '42'
    elseif fence == 'menu' then
        f.menu_changed = true
    elseif fence == 'admission' then
        f.admission = 'different'
    end
    f.api:resume()
    if fence == 'berth' then f:read() end
    if fence == 'inflight' then f.commands[2][2](true) end
    assert(f.api:state() == ((fence == 'terminal' or fence == 'inflight') and 'stopped' or 'failed'))
    assert(not f.api:confirm(grant) and #f.stock == 0)
end

-- A seat rebind after the provider authorized release fences the old MD reply.
native, packets, replayed, permit = {}, {}, 0, true
menu.buttonUndock = stock_row
ipc = hold.new_ipc({lease = 'lease', undock_nonce = nonce, undock_request = rid,
    epoch = epoch, index = 7354}, {send = function(_, packet) packets[#packets + 1] = packet end},
    function() return application_clock end, function() return true end)
ipc:set_running(true)
ipc:tick(seat)
answer = events['X4AuthorityClientUndock.Result']
answer(nil, nonce .. '|set|ok')
menu.buttonUndock()
ipc:tick(seat)
ipc:result(receipt)
ipc:tick(seat)
assert(native[2] == 'release')
ipc:tick({})
answer(nil, nonce .. '|release|ok')
ipc:set_running(true)
ipc:tick(seat)
assert(replayed == 0 and #native == 2)
