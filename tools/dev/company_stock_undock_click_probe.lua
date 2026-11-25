-- Called by company_stock_undock_click_probe.py after it verifies the installed menu.
local ship, berth, admission = 101, 201, 'current'
local docked, lock, denied = true, false, false
local requests, stock_dispatch, cancel_dispatch, releases = 0, 0, 0, 0
local permission_checks = 0
local cell = {handlers = {}}
function cell:createButton(properties)
    self.properties = properties
    return self
end
function cell:setText() return self end

local menu = {currentplayership = ship, currentcontainer = berth, cleanup = function() end}
local env = {
    menu = menu, row = {[2] = cell}, Color = setmetatable({}, {__index = function(_, key) return key end}),
    ffi = {string = function(value) return value end},
    C = {
        UndockPlayerShip = function(check_only)
            if check_only then permission_checks = permission_checks + 1
            else stock_dispatch = stock_dispatch + 1 end
            return (lock or denied) and 'impossible' or 'granted'
        end,
        CanCancelConstruction = function() return true end,
    },
    Helper = {
        closeMenu = function() end,
        closeMenuAndOpenNewMenu = function(_, name)
            assert(name == 'UserQuestionMenu')
            cancel_dispatch = cancel_dispatch + 1
        end,
    },
    GetComponentData = function(id, key) return id == ship and key == 'isdocked' and docked end,
    GetLocalizedKeyName = function() return '' end,
    ReadText = function() return '' end,
    DebugError = error,
}
local function load_installed(source)
    return setfenv(assert(loadstring(source, '@installed menu_docked.lua')), env)
end
load_installed(functions)()
local stock = menu.buttonUndock
local function build_row()
    load_installed(row_code)()
    assert(cell.properties.active == nil)
    return assert(cell.handlers.onClick)
end

-- A test-only interception and confirmation model. All native calls are fakes.
local installed, armed, pending = true, false, nil
local function matches()
    return menu.currentplayership == ship and menu.currentcontainer == berth
        and docked and admission == 'current' and not menu.buildInProgress and not menu.buildToCancel
end
local function arm()
    if not matches() or not menu.isUndockButtonActive() then return false end
    armed, lock = true, true
    return true
end
local function tested_handler(...)
    if not installed or not armed or not matches() then return stock(...) end
    if not pending then
        requests = requests + 1
        pending = requests
    end
end
local function confirm(request, confirmed_admission)
    if not installed or not armed or not pending or pending ~= request
            or confirmed_admission ~= admission or not matches() then return false end
    armed, lock, pending = false, false, nil
    releases = releases + 1
    return true
end
local function cleanup()
    installed, armed, pending = false, false, nil
    if menu.buttonUndock == tested_handler then menu.buttonUndock = stock end
end

local preinstalled = build_row()
assert(preinstalled == stock)
menu.buttonUndock = tested_handler
denied = true
assert(not arm() and not confirm(1, 'current'))
build_row()()
assert(requests == 0 and stock_dispatch == 0 and releases == 0)
denied = false

assert(arm() and not menu.isUndockButtonActive())
preinstalled()
assert(requests == 0 and stock_dispatch == 0, 'existing row unexpectedly adopted new handler')
local click = build_row()
assert(click == tested_handler and cell.properties.bgColor() == 'button_background_inactive')
menu.currentcontainer = berth + 1
local checks_before = permission_checks
click()
assert(not confirm(1, 'current') and requests == 0 and lock)
assert(permission_checks == checks_before + 1, 'unrelated berth did not use the stock guard')
menu.currentcontainer = berth
menu.currentplayership = ship + 1
click()
assert(requests == 0 and lock)
menu.currentplayership = ship
menu.buildInProgress = 1
checks_before = permission_checks
click()
assert(requests == 0 and stock_dispatch == 0 and cancel_dispatch == 0 and lock)
menu.buildInProgress, menu.buildToCancel = nil, {1, 50}
click()
assert(requests == 0 and stock_dispatch == 0 and cancel_dispatch == 0 and lock)
assert(permission_checks == checks_before + 2, 'construction did not use the stock guard')
menu.buildInProgress, menu.buildToCancel = nil, nil

click()
click()
assert(requests == 1 and stock_dispatch == 0 and lock)
assert(not confirm(0, 'current') and not confirm(1, 'previous') and lock)
admission = 'previous'
assert(not confirm(1, 'previous') and lock)
admission = 'current'
menu.currentcontainer = berth + 1
assert(not confirm(1, 'current') and lock)
menu.currentcontainer = berth
assert(confirm(1, 'current') and not confirm(1, 'current'))
assert(releases == 1 and not lock and stock_dispatch == 0)

click()
assert(stock_dispatch == 1, 'unarmed row did not delegate to stock')
menu.buildInProgress, menu.buildToCancel = 1, {1, 50}
click()
assert(cancel_dispatch == 1 and stock_dispatch == 1, 'unarmed build cancellation did not delegate to stock')
menu.buildInProgress, menu.buildToCancel = nil, nil
assert(arm())
click()
cleanup()
assert(menu.buttonUndock == stock and build_row() == stock)
assert(not confirm(2, 'current') and releases == 1 and lock)
click()
assert(requests == 2 and stock_dispatch == 1, 'cleaned-up captured callback did not defer to stock lock')
lock = false  -- Test fixture reset. The interception never clears on cleanup.
click()
assert(requests == 2 and stock_dispatch == 2)

print('PASS: installed row and stock handler with simulated native lock and confirmation')
