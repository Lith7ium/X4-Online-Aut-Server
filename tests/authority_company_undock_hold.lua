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
