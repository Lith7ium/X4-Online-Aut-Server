local module = dofile('src/authority/lua/company_purchase_source.lua')
local lease = string.rep('a', 32)
local prefix = 'T|' .. lease .. '|'
local now, available = 0, false
local incoming, outgoing, receipts = {}, {}, {}
local source = module.new({
    lease = lease,
    clock = function() return now end,
    available = function() return available end,
    net = {
        send = function(_, packet) outgoing[#outgoing + 1] = packet end,
        receive = function() return table.remove(incoming, 1) end,
    },
})
local function settle(receipt)
    receipts[#receipts + 1] = receipt
end
local intent = {sector = 'cluster_01_sector001_macro', station_code = 'BIM-169', ware = 'water',
    quantity = 6, price_cents = 5819}
assert(not source:request(intent, settle))
source:set_running(true)
assert(not source:request(intent, settle))
available = true
assert(source:request(intent, settle, 34914) and not source:request(intent, settle))
source:tick()
assert(outgoing[1] == prefix .. '1|cluster_01_sector001_macro|BIM-169|water|6|5819|34914')
now = 1
source:tick()
assert(#outgoing == 2 and outgoing[1] == outgoing[2])
incoming = {prefix .. 'RECEIPT|2|unknown',
    prefix .. 'RECEIPT|1|confirmed|34900|965100|5|WRN-302',
    prefix .. 'RECEIPT|1|confirmed|1e4|965100|6|WRN-302',
    prefix .. 'RECEIPT|1|refused|price_changed|5819'}
source:tick()
assert(#receipts == 0)
incoming = {prefix .. 'RECEIPT|1|refused|price_changed|5800'}
source:set_running(false)
source:tick()
assert(#receipts == 1 and receipts[1].current_price_cents == 5800 and receipts[1].price_cents == 5819)
assert(not source:request(intent, settle))
source:set_running(true)
intent.price_cents = 5800
assert(source:request(intent, settle))
incoming = {prefix .. 'RECEIPT|1|confirmed|34900|965100|6|WRN-302',
    prefix .. 'RECEIPT|2|confirmed|34800|965200|6|WRN-302'}
source:tick()
assert(#receipts == 2 and receipts[2].price_cents == 5800 and receipts[2].settled_total_cents == 34800
    and receipts[2].company_balance_cents == 965200 and receipts[2].cargo_units == 6
    and receipts[2].ship_code == 'WRN-302')

assert(source:request(intent, settle, 34914))
incoming = {prefix .. 'RECEIPT|3|accepted_pending_statement',
    prefix .. 'RECEIPT|3|accepted_pending_statement'}
source:tick()
assert(#receipts == 3 and receipts[3].outcome == 'accepted_pending_statement')
assert(not source:request(intent, settle))
incoming = {prefix .. 'RECEIPT|3|pilot_reported|34914'}
source:tick()
assert(#receipts == 4 and receipts[4].outcome == 'pilot_reported'
    and receipts[4].settled_total_cents == 34914 and receipts[4].company_balance_cents == nil)

local clean_refusals = {'captain_required', 'dock_mismatch', 'provider_busy', 'no_unique_offer', 'not_tradeable'}
for offset, reason in ipairs(clean_refusals) do
    assert(source:request(intent, settle))
    incoming = {prefix .. 'RECEIPT|' .. (3 + offset) .. '|refused|' .. reason}
    source:tick()
    assert(#receipts == 4 + offset and receipts[#receipts].outcome == 'refused' and receipts[#receipts].reason == reason)
end

assert(source:request(intent, settle))
local last_sent = #outgoing
available = false
source:tick()
assert(#outgoing == last_sent)
available = true
source:set_running(false)
source:tick()
assert(#outgoing == last_sent)
source:set_running(true)
source:tick()
assert(#outgoing == last_sent + 1)

local displayed = {}
local display_attempts = 0
local display_available = true
local function new_notice_source()
    return module.new({
        lease = lease,
        clock = function() return now end,
        available = function() return available end,
        recovered_notice = function(notice)
            display_attempts = display_attempts + 1
            if display_available == 'error' then error('HUD unavailable') end
            if not display_available then return false end
            displayed[#displayed + 1] = notice
            return true
        end,
        net = {
            send = function(_, packet) outgoing[#outgoing + 1] = packet end,
            receive = function() return table.remove(incoming, 1) end,
        },
    })
end

local notice_source = new_notice_source()
local purchase_id = string.rep('b', 32)
local row = 'NOTICE|1|' .. purchase_id .. '|water|6|BIM-169|0'
local ack = prefix .. 'NOTICE_ACK|1|' .. purchase_id
incoming, outgoing, now, available = {prefix .. row}, {}, 0, false
notice_source:tick()
notice_source:set_running(true)
notice_source:tick()
assert(#outgoing == 0 and display_attempts == 0)
available = true
now = 0.25
notice_source:tick()
assert(#outgoing == 1 and outgoing[1] == prefix .. 'NOTICE_POLL')
now = 3.99
notice_source:tick()
assert(#outgoing == 1)
now = 4.25
notice_source:tick()
assert(#outgoing == 2 and outgoing[2] == prefix .. 'NOTICE_POLL')

local function tick_notice()
    now = now + 0.25
    notice_source:tick()
end

incoming = {
    prefix .. row:gsub('|1|', '|01|'),
    prefix .. row:gsub(purchase_id, string.rep('B', 32)),
    prefix .. row:gsub('water', 'Water'),
    prefix .. row:gsub('|6|', '|2147483648|'),
    prefix .. row:gsub('BIM%-169', 'bim-169'),
    prefix .. row:gsub('|0$', '|00'),
    prefix .. row:gsub('|0$', '|9007199254740992'),
    prefix .. row .. '|',
}
tick_notice()
assert(display_attempts == 0 and #outgoing == 2)
display_available = false
incoming = {prefix .. row}
tick_notice()
assert(display_attempts == 1 and #displayed == 0 and #outgoing == 2)
display_available = 'error'
incoming = {prefix .. row}
tick_notice()
assert(display_attempts == 2 and #displayed == 0 and #outgoing == 2)
display_available = true
incoming = {prefix .. row, prefix .. row:gsub('water', 'energycells'), prefix .. row}
tick_notice()
assert(display_attempts == 3 and #displayed == 1 and #outgoing == 4)
assert(outgoing[3] == ack and outgoing[4] == ack)
assert(displayed[1].purchase_id == purchase_id and displayed[1].ware == 'water'
    and displayed[1].quantity == 6 and displayed[1].station_code == 'BIM-169'
    and displayed[1].settled_total_cents == 0)

local next_id = string.rep('c', 32)
incoming = {prefix .. 'NOTICE|2|' .. next_id .. '|energycells|10|BIM-169|2450', prefix .. row}
tick_notice()
assert(#displayed == 2 and #outgoing == 5 and outgoing[5] == prefix .. 'NOTICE_ACK|2|' .. next_id)

local fresh_source = new_notice_source()
fresh_source:set_running(true)
incoming = {prefix .. row}
fresh_source:tick()
assert(#displayed == 3 and outgoing[6] == ack and outgoing[7] == prefix .. 'NOTICE_POLL')

-- A complete Company view may span pages, arrive out of order and contain
-- distinct station offers for the same ware. Nothing renders until assembled.
local market_in, market_out = {}, {}
local market_source = module.new({net = {
    send = function(_, value) market_out[#market_out + 1] = value end,
    receive = function() return table.remove(market_in, 1) end,
}, lease = lease, clock = function() return 0 end, available = function() return true end,
    trade_view = true})
market_source:set_running(true)
local station = {sector = 'cluster_01_sector001_macro', station_code = 'BIM-169'}
local view = assert(market_source.market:open(station))
market_source:tick()
assert(market_out[1] == prefix .. 'MARKET|' .. view.id .. '|cluster_01_sector001_macro|BIM-169|0')
local function market_message(kind, id, offset, suffix)
    market_in[#market_in + 1] = prefix .. kind .. '|' .. id .. '|' .. offset .. '|' .. suffix
end
for index = 8, 1, -1 do
    market_message('MARKET_ROW', view.id, 0, index .. '|buy|water|5848|25|1|2|6|1')
end
market_message('MARKET_PAGE', view.id, 0, 'observed|11600|9|8')
market_source:tick()
assert(view.status == 'pending' and view.offset == 0)
market_source:tick()
assert(view.status == 'pending' and view.offset == 8)
market_message('MARKET_ROW', view.id, 8, '1|sell|water|4510|25|1|2|6|0')
market_message('MARKET_PAGE', view.id, 8, 'observed|11600|9|1')
market_source:tick()
assert(view.status == 'observed' and #view.rows == 9
    and view.rows[1].tradeable and not view.rows[9].tradeable)
local reopened = assert(market_source.market:open(station))
market_message('MARKET_PAGE', view.id, 0, 'observed|0|0|0')
market_source:tick()
assert(view.status == 'unavailable' and reopened.id ~= view.id and reopened.status == 'pending')
market_message('MARKET_PAGE', reopened.id, 0, 'refused|dock_mismatch')
market_source:tick()
assert(reopened.status == 'unavailable' and reopened.rows == nil)

-- Only the admitted, selected trade window can consume its minted sale ID.
local sale_in, sale_out, sale_receipts = {}, {}, {}
local sale_source = module.new({net = {
    send = function(_, value) sale_out[#sale_out + 1] = value end,
    receive = function() return table.remove(sale_in, 1) end,
}, lease = lease, clock = function() return now end, available = function() return available end,
    trade_view = true, sales = true})
local sale_terms = {sector=station.sector, station_code=station.station_code, ware='water',
    quantity=2, price_cents=3473, ship_code='WRN-302'}
local function receive_sale(result) sale_receipts[#sale_receipts+1] = result end
assert(market_source.request_sale == nil and not sale_source:request_sale(sale_terms, receive_sale))
sale_source:set_running(true)
local function offer_sale(id)
    local view = assert(sale_source.market:open(station))
    sale_source:tick()
    sale_in = {prefix .. 'MARKET_PAGE|' .. view.id .. '|0|observed|930100|1|1|' .. id,
               prefix .. 'MARKET_ROW|' .. view.id .. '|0|1|sell|water|3473|60|2|12|0|1'}
    sale_source:tick()
    assert(view.status == 'observed')
    return view
end
local sale_id = ('c'):rep(32)
local sale_view = offer_sale(sale_id)
sale_terms.quantity = 13
assert(not sale_source:request_sale(sale_terms, receive_sale))
sale_terms.quantity = 2
local ok, id = sale_source:request_sale(sale_terms, receive_sale)
assert(ok and id == sale_id and not sale_source:request_sale(sale_terms, receive_sale))
sale_source.market:close()
local before_reopen = #sale_out
assert(sale_source.market:open(station))
sale_source.market:close()
local waiting_view = assert(sale_source.market:open(station))
assert(#sale_out == before_reopen, 'closing an unsent view must not revoke a submitted sale')
sale_source:tick()
local packet = prefix .. 'SALE|' .. sale_id .. '|' .. sale_view.id .. '|' .. sale_terms.sector
    .. '|' .. sale_terms.station_code .. '|water|2|3473|WRN-302'
assert(#sale_out == before_reopen + 1 and sale_out[#sale_out] == packet,
    'a new market view must wait for the submitted sale')
now = now + 30
sale_source:tick()
assert(sale_out[#sale_out] == packet and sale_out[#sale_out-1] == packet)
sale_in = {prefix .. 'SALE_RECEIPT|' .. ('d'):rep(32) .. '|WRN-302|confirmed|6900|937000|10',
           prefix .. 'SALE_RECEIPT|' .. sale_id .. '|BAD-001|confirmed|6900|937000|10',
           prefix .. 'SALE_RECEIPT|' .. sale_id .. '|WRN-302|confirmed|6900|937000|-1'}
sale_source:tick()
assert(#sale_receipts == 0 and not sale_source:request_sale(sale_terms, receive_sale))
sale_in = {prefix .. 'SALE_RECEIPT|' .. sale_id .. '|WRN-302|confirmed|6900|937000|0'}
sale_source:tick()
assert(#sale_receipts == 1 and sale_receipts[1].credited_cents == 6900 and sale_receipts[1].cargo_units == 0)
assert(waiting_view.status == 'pending' and sale_out[#sale_out] == prefix .. 'MARKET|' .. waiting_view.id
    .. '|cluster_01_sector001_macro|BIM-169|0', 'a fresh market view must start after settlement')
assert(not sale_source:request_sale(sale_terms, receive_sale))
local second_id = ('e'):rep(32)
offer_sale(second_id)
ok, id = sale_source:request_sale(sale_terms, receive_sale)
assert(ok and id == second_id)
sale_in = {prefix .. 'SALE_RECEIPT|' .. second_id .. '|WRN-302|refused|price_changed|3501'}
sale_source:tick()
assert(#sale_receipts == 2 and sale_receipts[2].outcome == 'refused'
    and sale_receipts[2].price_cents == 3473 and sale_receipts[2].current_price_cents == 3501)
