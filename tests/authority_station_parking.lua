local parking = dofile('tools/dev/authority_station_parking.lua')
local nonce = ('1'):rep(32)
local screen, ship_screen = 'X4AuthorityStationParking', 'X4AuthorityStationParkingShips'
local ships = {{index=1970, code='WOO-831'}, {index=7354, code='WRN-302'}}
local location = {kind='FALLBACK', code='XPJ-169', sector='cluster_06_sector002_macro', room='dock_room'}
local function exercise(mode, valid_station)
    local callbacks, calls, logs = {}, {}, {}
    local function opts_issue(name, action, params)
        calls[#calls + 1] = {name, action, params}
        return true
    end
    local run = parking.new({
        mode=mode, nonce=nonce, expected=mode == 'verify' and location or nil, ships=ships,
        register=function(name, fn) callbacks[name] = fn; return true end,
        log=function(row) logs[#logs + 1] = row end, issue=opts_issue,
        clock=function() return 0 end, guard=function() return true end,
        check_previous=function(actor) return actor == '0x77' end,
        check_station=function(station) return valid_station and station.ref == '0x123' and station.room_ref == '0x17' end,
        check_ship=function(actor, code) return actor == '0x9' and (code == 'WOO-831' or code == 'WRN-302') end,
    })
    local function response(op, status, room, macro)
        callbacks[screen .. '.Result'](table.concat({nonce, op, status, 'FALLBACK', '0x123',
            'XPJ-169', '0xab', location.sector, room or '0', '0x77', 'WOO-831', macro or '0'}, '|'))
    end
    assert(not run() and calls[1][2] == 'Read')
    if mode == 'prepare' then
        response('read', 'READY')
        assert(not run() and calls[2][2] == 'Park' and calls[2][3].target == '0x123')
        response('park', 'DISPATCHED')
        assert(not run() and calls[3][2] == 'Read')
    end
    response('read', 'PARKED', '0x17', location.room)
    if not valid_station then
        assert(not pcall(run) and logs[1] == 'ERROR|LOCAL_STATION_READBACK')
        return
    end
    for _, ship in ipairs(ships) do
        assert(not run() and calls[#calls][2] == 'Inspect' and calls[#calls][3].index == ship.index)
        callbacks[ship_screen .. '.Result'](nonce .. '|' .. ship.index .. '|AVAILABLE|0x9')
    end
    assert(run() and logs[1] == 'PARKED|FALLBACK|XPJ-169|cluster_06_sector002_macro|dock_room')
    assert(#calls == (mode == 'prepare' and 5 or 3))
end
exercise('prepare', true)
exercise('verify', true)
exercise('verify', false)
