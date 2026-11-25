local stream = dofile('src/authority/lua/company_observer_stream.lua')
local epoch, generation, prefix = ('a'):rep(32), ('b'):rep(32), 'C|private|test|'
local function reader(sector, index, actor, code)
    local self = {visible = true, matches_now = true}
    self.tick = function()
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

-- Only the automatic cockpit reader consumes roster metadata. The stream
-- preserves the newest revision while the lease expires and renews.
local rosters = {}
local auto = {
    set_pilot_roster = function(_, indices)
        if indices[1] == 1970 then return false end
        rosters[#rosters + 1] = table.concat(indices, ',')
        return true
    end,
}
local function roster(revision, suffix, packet_epoch, packet_prefix)
    incoming[#incoming + 1] = (packet_prefix or prefix)
        .. 'ROSTER|' .. (packet_epoch or epoch) .. '|' .. revision .. (suffix or '')
end
owner = stream.new({cockpit = cockpit, map = map}, epoch, net, prefix, function() return clock end, nil, auto)
roster('1', '|12|7354')
owner:tick(true)
assert(#rosters == 1 and rosters[1] == '12,7354')
local roster_view_count = #sent
roster('1', '|12')
roster('7', '|12', ('d'):rep(32))
roster('7', '|12', epoch, 'C|unowned|lease|')
roster('7', '|1970')
roster('7', '|3|3')
roster('7', '|3|2')
roster('7', '|1|200001')
roster('7', '|1|2|3|4')
roster('7', '|')
roster('9007199254740992', '|12')
roster('0', '|12')
owner:tick(true)
assert(#rosters == 1 and #sent == roster_view_count)
roster('2')
roster('1', '|12|7354')
owner:tick(true)
assert(#rosters == 2 and rosters[2] == '')
roster('3', '|12')
owner:tick(true)
clock = clock + 2.5
roster('3', '|12')
owner:tick(true)
clock = clock + .6
owner:tick(true)
assert(#rosters == 3 and rosters[3] == '12')
clock = clock + 2.5
owner:tick(true)
assert(#rosters == 4 and rosters[4] == '')
roster('2')
roster('3', '|12')
owner:tick(true)
assert(#rosters == 5 and rosters[5] == '12')
owner:close()
assert(cockpit.closed and map.closed)

-- Only a current, locally confirmed cockpit observation can reach the optional trial.
local trial_module = dofile('src/authority/lua/company_hull_trial.lua')
local received, writes, stops = {}, 0, 0
local native = {guard = function() return true end, close = function() end}
local trial_options = {
    enabled = true, own_index = 1970, target_index = 12, clock = function() return clock end,
    adapter = {bind = function(_, binding)
        assert(binding.slot == 'cockpit' and binding.actor == '0xc' and binding.index == 12)
        return native
    end},
    playback = {MAX_PREDICTION_SECONDS = 5, new = function(options)
        assert(options.guard(options.binding))
        return {
            push = function(_, binding, sample, arrival)
                assert(binding.serial == 1 and arrival == clock)
                received[#received + 1] = sample
                return true
            end,
            tick = function() writes = writes + 1; return true end,
            stop = function() stops = stops + 1 end,
        }
    end},
}
local trial = trial_module.new(trial_options)
owner = stream.new({cockpit = cockpit, map = map}, epoch, net, prefix, function() return clock end, trial)
local ordinary_send = net.send
net.send = function() return false end
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. clock)
owner:tick(true)
assert(#received == 0 and writes == 0)
net.send = ordinary_send
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. (clock - 5))
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. (clock + 1))
send('cockpit', 1, 12, 'sector_a', moving) -- Read-only legacy envelope.
send('map', 1, 7354, 'sector_b', moving)
send('cockpit', 2, 12, 'sector_a', moving)
send('cockpit', 1, 12, 'sector_a', pose)
owner:tick(true)
assert(#received == 0 and writes == 0)
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. clock)
owner:tick(true)
assert(#received == 1 and writes == 1 and received[1].real_velocity.y == .002)
assert(received[1].capture_time == 50.25 and received[1].source_time == 1)
owner:tick(false)
send('cockpit', 3, 12, 'sector_a', moving)
owner:tick(true)
assert(#received == 1 and writes == 1 and stops == 1)
owner:close()

-- A local revocation can arrive before its delayed REMOTE on the UDP socket.
local function end_packet(origin)
    return prefix .. table.concat({'END', 'cockpit', 1, epoch, 12, origin, 'sector_a'}, '|')
end
owner = stream.new({cockpit = cockpit, map = map}, epoch, net, prefix, function() return clock end,
                   trial_module.new(trial_options))
incoming[1] = end_packet(generation)
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. clock)
owner:tick(true)
assert(#received == 1 and writes == 1)
owner:close()

owner = stream.new({cockpit = cockpit, map = map}, epoch, net, prefix, function() return clock end,
                   trial_module.new(trial_options))
send('cockpit', 1, 12, 'sector_a', moving .. '|' .. clock)
incoming[2] = end_packet(('d'):rep(32))
owner:tick(true)
assert(#received == 2 and writes == 2)
incoming[1] = end_packet(generation)
owner:tick(true)
assert(writes == 2 and stops == 2)
owner:close()
