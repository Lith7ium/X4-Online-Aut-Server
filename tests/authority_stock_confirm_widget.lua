local registered_confirm, registered_dock_trade = dofile('tools/dev/authority_trade_cockpit_probe.lua')
local called = 0
local function capture() called = called + 1 end
local cell = {type='button', id=30, properties={uiTriggerID='confirmtrade', active=true},
    handlers={onClick=capture}}
local widget = {type='table', id=20, rows={{cell}}}
local frame = {id=10, properties={layer=4}, content={widget}}
local entry = {layer=4, tableobj=20, row=1, col=1, button=30, type='onClick',
    script=function(button) assert(button == 30); capture() end}
local menu = {contextFrame=frame, frames={[4]=10}, buttonConfirmTrade=capture, buttonScriptMap={entry}}
local function expect_failure(reason)
    local ok, error_text = pcall(registered_confirm, menu, {}, capture)
    assert(not ok and tostring(error_text):find(reason, 1, true), tostring(error_text))
end

function GetActiveFrame() return 10 end
function GetCellContent(id, row, column)
    assert(id == 20 and row == 1 and column == 1)
    return 30
end

assert(registered_confirm(menu, frame, capture) == nil)
assert(called == 0)
local bound = assert(registered_confirm(menu, {}, capture))
assert(bound == entry)
bound.script(bound.button)
assert(called == 1)
cell.properties.active = false
expect_failure('inactive_confirm_widget')
cell.properties.active = true
cell.handlers.onClick = function() error('unexpected native booking') end
expect_failure('unexpected_confirm_handler')
cell.handlers.onClick = capture
entry.button = 31
expect_failure('unbound_confirm_script')
entry.button = 30
cell.id = 32
expect_failure('unbound_confirm_cell')
cell.id = 30
menu.buttonScriptMap[2] = entry
expect_failure('ambiguous_confirm_script')
assert(called == 1)

menu.buttonScriptMap[2] = nil
menu.frame, menu.shown = frame, false
menu.currentplayership, menu.currentcontainer = 50, 60
menu.buttonTrade = function() error('direct fallback is prohibited') end
cell.properties.uiTriggerID = 'docked_trade'
assert(registered_dock_trade(menu, 50, 60) == nil)
menu.shown = true
assert(registered_dock_trade(menu, 50, 60) == entry)
assert(not pcall(registered_dock_trade, menu, 51, 60))
assert(not pcall(registered_dock_trade, menu, 50, 61))
cell.properties.active = false
assert(not pcall(registered_dock_trade, menu, 50, 60))
cell.properties.active = true
cell.properties.uiTriggerID = nil
assert(not pcall(registered_dock_trade, menu, 50, 60))
assert(called == 1)
