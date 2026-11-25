-- Capture the Company fleet order before the stock map can write the queue.
local M = {}

local notices = {
    unavailable = 'Company fleet orders are unavailable from this cockpit.',
    unsupported = 'Company Fly and Wait requires one ship and a plain map point in its sector. Priority, looped queues and shortcuts are unavailable.',
    ship_unavailable = 'Company Fly and Wait requires an operational, undocked Company ship with an NPC captain.',
    legacy_ship_busy = 'This Company session requires a ship with an empty order queue.',
    legacy_queue_override = 'This Company session supports the standard Fly and Wait action for an idle ship.',
    binding_unavailable = 'The selected ship could not be verified in the Company registry.',
    busy = 'A Company fleet order is already awaiting its result.',
    pending = 'Company Fly and Wait sent. Awaiting the server result.',
    accepted = 'The server accepted Company Fly and Wait.',
    refused = 'The server refused Company Fly and Wait.',
    unknown = 'The Company Fly and Wait result is unknown. Check the ship before issuing another order.',
}

function M.install(o)
    if o.enabled ~= true then return nil, 'disabled' end
    local map, interact = o.map, o.interact
    if type(map) ~= 'table' or map.name ~= 'MapMenu' or type(map.orderMoveWait) ~= 'function'
            or type(interact) ~= 'table' or interact.name ~= 'InteractMenu'
            or type(interact.buttonMoveWait) ~= 'function' or type(interact.orderMoveWait) ~= 'function'
            or type(interact.onCloseElement) ~= 'function' then return nil, 'unavailable' end

    -- The MapMenu handler serves the modifier shortcut. The normal context row
    -- instead calls InteractMenu.buttonMoveWait and must be handled separately.
    map.orderMoveWait = function()
        o.notice('unsupported')
    end
    interact.orderMoveWait = map.orderMoveWait
    interact.buttonMoveWait = function(clear)
        if map.shown ~= true then
            o.notice('unsupported')
            return
        end
        local ok, intent, why = pcall(o.read, map, interact, clear)
        if not ok or not intent then
            o.notice(ok and why or 'unsupported')
        else
            o.submit(intent)
        end
        interact.onCloseElement('close')
    end
    return true
end

local function finite_coordinate(value)
    local number = tonumber(value)
    if number and number == number and math.abs(number) <= 10000000 then return number end
end

function M.install_x4(map, interact, submit, available, version)
    local legacy = version ~= 2
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        bool IsComponentClass(UniverseID,const char*);
        bool IsComponentOperational(UniverseID);
        bool IsOrderSelectableFor(const char*,UniverseID);
        bool IsControlPressed(void);
        bool IsShiftPressed(void);
        UniverseID GetContextByClass(UniverseID,const char*,bool);
        UniverseID GetPlayerOccupiedShipID(void);
        uint32_t GetNumOrders(UniverseID);
        void ShowInfoLine(const char*,uint32_t);
    ]]
    local C = ffi.C
    local function component_ref(id)
        local ref = ConvertStringToLuaID((tostring(id):gsub('ULL$', '')))
        if ref and ConvertIDTo64Bit(ref) == id then return ref end
    end
    local function matches(ship, sector, code)
        if ship == 0 or ship == C.GetPlayerOccupiedShipID() or not C.IsComponentClass(ship, 'ship')
                or not C.IsComponentOperational(ship) or C.GetContextByClass(ship, 'sector', false) ~= sector
                or not C.IsOrderSelectableFor('MoveWait', ship) then return false end
        local owned, pilot, docked, docking, stored, current_code = GetComponentData(ship,
            'isplayerowned', 'assignedpilot', 'isdocked', 'isdocking', 'isinternallystored', 'idcode')
        return owned == true and pilot and C.IsComponentClass(ConvertIDTo64Bit(pilot), 'npc')
            and docked == false and docking == false and stored == false and current_code == code
    end
    local function read(map_menu, menu, clear)
        if not available() then return nil, 'unavailable' end
        local selected = menu.selectedplayerships
        if type(clear) ~= 'boolean' or map_menu.mode ~= nil or menu.shown == true or menu.priorityOrderMode
                or type(selected) ~= 'table' or #selected ~= 1 or menu.numorderloops ~= 0
                or not menu.componentSlot or not menu.offset then return nil, 'unsupported' end
        if legacy and (clear or C.IsControlPressed() or C.IsShiftPressed()
                or (menu.clearOtherOrders and menu.clearOrderQueueByDefault ~= true)) then
            return nil, 'legacy_queue_override'
        end

        local map_selected = map_menu.selectedcomponents
        if type(map_selected) ~= 'table' then return nil, 'unsupported' end
        local selected_key = next(map_selected)
        local ship, sector = selected[1], menu.offsetcomponent
        if selected_key == nil or next(map_selected, selected_key) ~= nil
                or ConvertStringTo64Bit(selected_key) ~= ship
                or not sector or sector == 0 or menu.componentSlot.component ~= sector
                or not C.IsComponentClass(sector, 'sector') then return nil, 'unsupported' end

        local x, y, z = finite_coordinate(menu.offset.x), finite_coordinate(menu.offset.y),
            finite_coordinate(menu.offset.z)
        if not x or not y or not z then return nil, 'unsupported' end
        local code = GetComponentData(ship, 'idcode')
        local sector_ref = component_ref(sector)
        local sector_macro = sector_ref and GetComponentData(sector_ref, 'macro')
        if type(sector_macro) ~= 'string' or #sector_macro > 128 or not sector_macro:match('^[A-Za-z0-9_]+$')
                or type(code) ~= 'string' or not code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$')
                or not matches(ship, sector, code) then return nil, 'ship_unavailable' end

        if legacy and C.GetNumOrders(ship) ~= 0 then return nil, 'legacy_ship_busy' end
        local component = component_ref(ship)
        if not component then return nil, 'binding_unavailable' end

        -- The stock interaction handler reads these effective flags. The queue
        -- toggle key may change clearOtherOrders without enabling priority.
        local queue_mode
        if not legacy then queue_mode = (clear or menu.clearOtherOrders) and 'replace' or 'append' end
        return {ship = ship, component = component, ship_code = code, sector = sector_macro, x = x, y = y, z = z,
            queue_mode = queue_mode, current = function()
                return matches(ship, sector, code) and (not legacy or C.GetNumOrders(ship) == 0)
            end}
    end

    return M.install({enabled = true, map = map, interact = interact, read = read, submit = submit,
        notice = M.notice_x4})
end

function M.notice_x4(status, id, reason)
    local text = notices[status]
    if reason then text = (text:gsub('%.$', '')) .. ' (' .. reason:gsub('_', ' ') .. ').' end
    require('ffi').C.ShowInfoLine(text, 4)
    DebugError('[X4LAN] AUTH_COMPANY_FLEET ' .. status .. (id and ' ' .. id or '')
        .. (reason and ' ' .. reason or ''))
end

X4LAN_COMPANY_FLEET_CAPTURE = M
return M
