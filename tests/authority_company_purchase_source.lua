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
assert(source:request(intent, settle) and not source:request(intent, settle))
source:tick()
assert(outgoing[1] == prefix .. '1|cluster_01_sector001_macro|BIM-169|water|6|5819')
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
