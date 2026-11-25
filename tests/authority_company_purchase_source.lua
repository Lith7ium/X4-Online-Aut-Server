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

assert(source:request(intent, settle))
incoming = {prefix .. 'RECEIPT|4|refused|captain_required'}
source:tick()
assert(#receipts == 5 and receipts[5].outcome == 'refused' and receipts[5].reason == 'captain_required')

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
