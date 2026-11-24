local stream = dofile('src/authority/lua/company_observer_stream.lua')
local epoch, generation, prefix = ('a'):rep(32), ('b'):rep(32), 'C|private|test|'
local function reader(sector, index, actor, code)
    local self = {visible = true, matches_now = true}
    self.tick = function() end
    self.current = function()
        if self.visible then
            return {epoch = epoch, sector_macro = sector, index = index, actor = actor, code = code}
        end
    end
    self.matches = function(_, expected_epoch, expected_sector, expected_index, expected_actor, expected_code)
        return self.visible and self.matches_now and expected_epoch == epoch and expected_sector == sector
            and expected_index == index and expected_actor == actor and expected_code == code
    end
    self.close = function() self.closed = true end
    return self
end
local cockpit = reader('sector_a', 12, '0xc', 'ABC-345')
local map = reader('sector_b', 7354, '0xde', 'WRN-302')
local incoming, sent, clock = {}, {}, 10
local net = {
    receive = function() return table.remove(incoming, 1) end,
    send = function(_, packet) sent[#sent + 1] = packet; return true end,
}
local owner = stream.new({cockpit = cockpit, map = map}, epoch, net, prefix, function() return clock end)
local pose = '-332311.1875|24156.005859375|380247.625|-|-|-|80|16894.774352879'
local moving = '1|2|3|0|2e-3|-1.5|1|50.25'
local function send(slot, serial, index, sector, values)
    local body = table.concat({'REMOTE', slot, serial, epoch, index, generation, 6, sector, values}, '|')
    incoming[#incoming + 1] = prefix .. body
end
owner:tick(true)
assert(sent[1] == prefix .. 'VIEW|cockpit|1|' .. epoch .. '|sector_a|12|0xc|ABC-345')
assert(sent[2] == prefix .. 'VIEW|map|1|' .. epoch .. '|sector_b|7354|0xde|WRN-302')
send('map', 1, 7354, 'sector_b', pose)
send('cockpit', 1, 12, 'sector_a', moving)
owner:tick(true)
assert(sent[3] == prefix .. 'SEEN|map|1|' .. epoch .. '|7354|' .. generation .. '|6|sector_b|0xde|WRN-302|' .. pose)
assert(sent[4] == prefix .. 'SEEN|cockpit|1|' .. epoch .. '|12|' .. generation .. '|6|sector_a|0xc|ABC-345|' .. moving)

-- A map change keeps the HUD target alive. Queued map packets cannot resurrect it.
map.visible = false
send('map', 1, 7354, 'sector_b', pose)
owner:tick(true)
assert(#sent == 5 and sent[5] == prefix .. 'VIEW|map|2|' .. epoch .. '|-|0|-|-')
map.visible = true
map.matches_now = false
send('map', 3, 7354, 'sector_b', pose)
send('cockpit', 1, 12, 'sector_a', '1|2|nan|0|0|0|8|9')
send('cockpit', 1, 12, 'sector_a', '1|2|3|-|0|-|8|9')
send('cockpit', 1, 12, 'sector_a', '1|2|3|0|0|0|8|-1')
owner:tick(true)
assert(#sent == 6 and sent[6]:match('VIEW|map|3|'))
map.matches_now = true
send('map', 1, 7354, 'sector_b', pose)
owner:tick(true)
assert(#sent == 6)
clock = clock + .6
owner:tick(true)
assert(#sent == 8 and sent[7] == sent[1] and sent[8] == sent[6])
send('cockpit', 1, 12, 'sector_a', moving)
owner:tick(false)
assert(#sent == 10 and sent[9]:match('VIEW|cockpit|2|') and sent[10]:match('VIEW|map|4|'))
owner:close()
assert(cockpit.closed and map.closed)
