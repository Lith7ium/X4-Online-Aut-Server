-- Read stock X4 transaction entries for one Company ship and station.
local ffi = require('ffi')
local M = {}
local MAX_ROWS = 64
local MAX_WINDOW = 30

local function component(text)
    local hex = type(text) == 'string' and text:match('^0[xX]([0-9a-fA-F]+)$')
    if not hex or #hex > 16 then return nil end
    local value = ffi.new('uint64_t')
    for digit in hex:gmatch('.') do value = value * 16 + tonumber(digit, 16) end
    if value ~= 0 then return value end
end

ffi.cdef[[
    bool IsComponentClass(uint64_t componentid, const char *class);
    uint32_t GetNumOrders(uint64_t componentid);
]]

function M.ship_orders_idle(handle)
    local ship = component(handle)
    return ship ~= nil and ffi.C.IsComponentClass(ship, 'ship') and ffi.C.GetNumOrders(ship) == 0
end

local function exact(value, ctype, suffix)
    if type(value) ~= 'cdata' or not ffi.istype(ctype, value) then error('invalid_ledger_integer', 0) end
    return (tostring(value):gsub(suffix .. '$', ''))
end

local function finite(time)
    return type(time) == 'number' and time >= 0 and time < math.huge
end

local function unknown(reason, rows)
    return {status='unknown', reason=reason, rows=rows or {}}
end

local function select_rows(input, ship, station, read)
    local buyer, seller = ship, station
    if input.side == 'sell' then buyer, seller = station, ship end
    local candidates, matches = {}, {}
    local linkage
    local unavailable
    for _, source in ipairs({'ship', 'station', 'player'}) do
        local records, reason = read(source, ship, station, input.start_time, input.end_time)
        if not records or #records > MAX_ROWS then
            unavailable = unavailable or reason or 'row_limit'
        else
            for _, record in ipairs(records) do
                if finite(record.time) and record.time > input.start_time and record.time <= input.end_time then
                    local buyer_matches, seller_matches = record.buyer == buyer, record.seller == seller
                    local identity = buyer_matches and seller_matches
                        and record.ware == input.ware and record.amount == input.quantity
                    local compatible = (buyer_matches or record.buyer == 0) and (seller_matches or record.seller == 0)
                        and (record.ware == input.ware or record.ware == '')
                        and (record.amount == input.quantity or record.amount == 0)
                    if compatible then
                        local is_trade = identity and record.eventtype == 'trade' and record.complete == true
                        local trade_id = exact(record.trade_id, 'int64_t', 'LL')
                        local usable_link = record.trade_id > 0
                        if is_trade then
                            if matches[source] or (linkage and usable_link and linkage ~= trade_id) then
                                return unknown('ambiguous_completed_trade')
                            end
                            matches[source] = true
                            if usable_link then linkage = trade_id end
                        end
                        candidates[#candidates + 1] = {
                            record=record, source=source, identity=identity, is_trade=is_trade,
                            trade_id=trade_id, buyer_matches=buyer_matches, seller_matches=seller_matches,
                        }
                    end
                end
            end
        end
    end
    local rows = {}
    local unlinked_completed = 0
    for _, candidate in ipairs(candidates) do
        local record = candidate.record
        local linked = linkage ~= nil and candidate.trade_id == linkage
        if candidate.is_trade and not linked then unlinked_completed = unlinked_completed + 1 end
        if candidate.identity or linked then
            rows[#rows + 1] = {
                source=candidate.source, time=record.time,
                buyer_id=exact(record.buyer, 'uint64_t', 'ULL'),
                seller_id=exact(record.seller, 'uint64_t', 'ULL'),
                ware=record.ware, quantity=record.amount, complete=record.complete,
                entry_id=exact(record.entry_id, 'int64_t', 'LL'), trade_id=candidate.trade_id,
                partner_id=exact(record.partner, 'uint64_t', 'ULL'), eventtype=record.eventtype,
                trade_eventtype=record.trade_eventtype,
                role=candidate.is_trade and 'completed_trade' or 'matching_account_or_order_event',
                buyer_matches=candidate.buyer_matches, seller_matches=candidate.seller_matches, linked=linked,
                money_cents=exact(record.money, 'int64_t', 'LL'),
                price_cents=exact(record.price, 'int64_t', 'LL'),
            }
        end
    end
    if unavailable then return unknown(unavailable, rows) end
    if not next(matches) then return unknown('no_completed_trade', rows) end
    if (linkage and unlinked_completed > 0) or unlinked_completed > 1 then
        return unknown('unlinked_completed_trades', rows)
    end
    return {status='observed', rows=rows, trade_id=linkage}
end

local function native_reader(source, ship, station, start_time, end_time)
    local C = ffi.C
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    -- Use the stock declaration when Helper has already registered it.
    if not pcall(ffi.typeof, 'TransactionLogEntry') then
        ffi.cdef[[
            typedef struct {
                double time;
                int64_t money;
                int64_t entryid;
                const char *eventtype;
                const char *eventtypename;
                UniverseID partnerid;
                const char *partnername;
                const char *partneridcode;
                int64_t tradeentryid;
                const char *tradeeventtype;
                const char *tradeeventtypename;
                UniverseID buyerid;
                UniverseID sellerid;
                const char *ware;
                uint32_t amount;
                int64_t price;
                bool complete;
            } TransactionLogEntry;
        ]]
    end
    ffi.cdef[[
        uint32_t GetNumTransactionLog(UniverseID componentid, double starttime, double endtime);
        uint32_t GetTransactionLog(TransactionLogEntry *result, uint32_t resultlen, UniverseID componentid,
                                   double starttime, double endtime);
        UniverseID GetPlayerID(void);
    ]]
    local id = source == 'ship' and ship or source == 'station' and station or C.GetPlayerID()
    if id == 0 then return nil, 'source_unavailable' end
    local count = tonumber(C.GetNumTransactionLog(id, start_time, end_time))
    if count > MAX_ROWS then return nil, 'row_limit' end
    if count == 0 then return {} end
    local native = ffi.new('TransactionLogEntry[?]', count)
    if tonumber(C.GetTransactionLog(native, count, id, start_time, end_time)) ~= count then
        return nil, 'ledger_changed'
    end
    local records = {}
    for index = 0, count - 1 do
        local row = native[index]
        records[#records + 1] = {
            time=row.time, buyer=row.buyerid, seller=row.sellerid, partner=row.partnerid, amount=row.amount,
            ware=row.ware ~= nil and ffi.string(row.ware) or '',
            eventtype=row.eventtype ~= nil and ffi.string(row.eventtype) or '',
            trade_eventtype=row.tradeeventtype ~= nil and ffi.string(row.tradeeventtype) or '',
            entry_id=row.entryid, trade_id=row.tradeentryid, complete=row.complete,
            money=row.money, price=row.price,
        }
    end
    return records
end

function M.collect(input, read)
    if type(input) ~= 'table' or type(input.ware) ~= 'string' or #input.ware > 128
        or (input.side ~= nil and input.side ~= 'buy' and input.side ~= 'sell')
        or not input.ware:match('^[a-z][a-z0-9_]*$') or type(input.quantity) ~= 'number'
        or input.quantity < 1 or input.quantity > 65535 or input.quantity % 1 ~= 0
        or not finite(input.start_time) or not finite(input.end_time)
        or input.end_time <= input.start_time or input.end_time - input.start_time > MAX_WINDOW then
        return unknown('invalid_request')
    end
    local ship, station = component(input.ship), component(input.station)
    if not ship or not station or ship == station then return unknown('invalid_component') end
    local ok, result = pcall(select_rows, input, ship, station, read or native_reader)
    if not ok then return unknown(result == 'invalid_ledger_integer' and result or 'ledger_read_failed') end
    return result
end

X4LAN_AUTHORITY_TRADE_LEDGER = M
return M
