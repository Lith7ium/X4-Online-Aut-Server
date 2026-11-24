"""Focused synthetic test of original Lua ledger correlation and exact integers."""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime


class TradeLedgerTest(unittest.TestCase):
    def test_exact_completed_purchase_and_ambiguous_windows(self):
        source = Path(__file__).resolve().parents[1] / 'tools/dev/authority_trade_ledger.lua'
        lua = LuaRuntime()
        module = lua.execute(source.read_text())
        lua.execute(r"""
local M = ...
local ffi = require('ffi')
local function u(n) return ffi.new('uint64_t', n) end
local function i(n) return ffi.new('int64_t', n) end
local ship = u(0x20000000000001ULL)
local station = u(0x20000000000002ULL)
local request = {ship='0x20000000000001', station='0x20000000000002',
                 ware='water', start_time=100, end_time=101, quantity=6}
local function row(values)
    local result = {buyer=ship, seller=station, partner=station, time=100.5, ware='water',
                    amount=6, eventtype='trade', trade_eventtype='completed', complete=true,
                    entry_id=i(5), trade_id=i(4), money=i(-34800), price=i(34914)}
    for key, value in pairs(values or {}) do result[key] = value end
    return result
end
local function read(source, buyer, seller, first, last)
    assert(buyer == ship and seller == station and first == 100 and last == 101)
    if source == 'ship' then
        return {row(), row({time=100}), row({seller=u(9)})}
    elseif source == 'player' then
        return {row({buyer=u(0), seller=u(0), eventtype='transfer',
                     amount=0, ware='', complete=false, entry_id=i(7)})}
    end
    return {}
end
local captured = M.collect(request, read)
assert(captured.status == 'observed' and #captured.rows == 2 and captured.trade_id == '4')
assert(captured.rows[1].buyer_id == '9007199254740993' and captured.rows[1].money_cents == '-34800')
assert(captured.rows[1].price_cents == '34914' and captured.rows[1].quantity == 6)
assert(captured.rows[2].linked and not captured.rows[2].buyer_matches and not captured.rows[2].complete)
assert(M.collect(request, function() return {row(), row({entry_id=i(6)})} end).reason
       == 'ambiguous_completed_trade')
assert(M.collect(request, function() return {row({time=100})} end).reason == 'no_completed_trade')
assert(M.collect(request, function() return {row({money=-34800})} end).reason == 'invalid_ledger_integer')
assert(M.collect(request, function() return nil, 'ledger_changed' end).reason == 'ledger_changed')
assert(M.collect(request, function()
    local too_many = {}
    for n=1,65 do too_many[n] = row() end
    return too_many
end).reason == 'row_limit')
request.ware, request.quantity = 'energycells', 3
assert(M.collect(request, read).reason == 'no_completed_trade')
local live = M.collect(request, function(source)
    if source == 'ship' then return {row(), row({ware='energycells', amount=3})} end
    return {}
end)
assert(live.status == 'observed' and #live.rows == 1 and live.trade_id == '4')
assert(live.rows[1].ware == 'energycells' and live.rows[1].quantity == 3)
request.quantity = 65536
assert(M.collect(request, read).reason == 'invalid_request')
request.ware, request.quantity = 'energycells|water', 3
assert(M.collect(request, read).reason == 'invalid_request')
request.ware, request.end_time = 'energycells', 131
assert(M.collect(request, read).reason == 'invalid_request')
""", module)
