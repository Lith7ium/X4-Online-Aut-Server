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

-- Keep the real lease, selection and REMOTE/SEEN receive path. Only the
-- engine's projection inputs and widget renderer are replaced.
local marker_module = dofile('src/authority/lua/company_map_marker.lua')
local menu = {name='MapMenu', shown=true, holomap=9, frames={}}
local camera = {x=0,y=0,z=0,yaw=0,pitch=-1.55,roll=0,distance=20000}
local viewport = {x=0,y=0,width=100,height=100}
local toolbar
local viewed_sector, map_pointer, other_layer = 'sector_b', true, false
local capacity, register = true, true
local attempts = 0
local draws, clears, calibrations = {}, 0, 0
local function point(x,z)
    return {x=x,z=z,px=x,py=z}
end
local helper = {
    createFrameHandle = function(actual, properties)
        assert(actual == menu and properties.layer == 3 and properties.scaling == false)
        assert(properties.enableDefaultInteractions == false and properties.exclusiveInteractions == false)
        assert(properties.closeOnUnhandledClick == false)
        attempts = attempts + 1
        local id = #draws + 1
        return {id=id, display=function()
            if not register then return end
            draws[id] = properties
            menu.frames[3] = id
        end}
    end,
    clearFrame = function(actual, layer)
        assert(actual == menu and layer == 3 and not other_layer)
        menu.frames[layer] = nil
        clears = clears + 1
    end,
}
local marker = marker_module.new({
    helper=helper, layer=3, layer_available=function() return not other_layer end,
    can_allocate=function() return capacity end,
    view=function(binding, y)
        if map_pointer and viewed_sector == binding.sector_macro then
            return {sector=viewed_sector, height=y, menu=menu, holomap=9, camera=camera, viewport=viewport,
                ui_scale=1, fixed_top_down=true, toolbar=toolbar}
        end
    end,
    calibrate=function(view)
        calibrations = calibrations + 1
        return marker_module.calibrate(view, {point(10,10), point(90,10), point(90,90), point(10,90)},
            point(40,60))
    end,
})
local function map_packet(slot, serial, seq, settings)
    local v = settings or {}
    local values = {v.x or 40, v.y or 2000, 40, '-', '-', '-', 78900, 120000}
    if v.arrival ~= false then values[#values + 1] = v.arrival or clock end
    if v.oriented then values[#values + 1], values[#values + 2], values[#values + 3] = .1,.2,.3 end
    incoming[#incoming + 1] = prefix .. table.concat({v.oriented and 'REMOTE4' or 'REMOTE', slot, serial,
        epoch, slot == 'map' and 7354 or 12, v.generation or generation, seq,
        v.sector or (slot == 'map' and 'sector_b' or 'sector_a'), unpack(values)}, '|')
end
local function map_end(serial, gen)
    incoming[#incoming + 1] = prefix .. table.concat({'END','map',serial,epoch,7354,gen,'sector_b'}, '|')
end

clock, incoming, sent = 20, {}, {}
owner = stream.new({cockpit=cockpit,map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, marker, true)
owner:tick(true)
map_packet('map', 1, 1, {arrival=18}) -- queued before the UI could process it
owner:tick(true)
assert(#draws == 0 and sent[#sent]:match('SEEN|map|1|'))
map_packet('map', 1, 2, {sector='sector_a'})
map_packet('map', 1, 2, {arrival=false})
map_packet('cockpit', 1, 2, {arrival=false})
owner:tick(true)
assert(#draws == 0 and #sent == 5)
net.send = function() return false end
map_packet('map', 1, 2)
owner:tick(true)
assert(#draws == 0)
net.send = ordinary_send
map_packet('map', 1, 3, {x=30, oriented=true})
owner:tick(true)
assert(#draws == 1 and draws[1].x == 21 and draws[1].y == 31 and calibrations == 1)
assert(sent[#sent]:match('SEEN4|map|1|') and sent[#sent]:match('|120000|0.1|0.2|0.3$'))
clock = 20.15
map_packet('map', 1, 4, {x=60})
map_packet('map', 1, 3, {x=80})
map_packet('map', 1, 5, {x=80,generation=('f'):rep(32)})
map_end(1, ('f'):rep(32))
owner:tick(true)
assert(#draws == 2 and draws[2].x == 51 and calibrations == 1 and clears == 0)
clock = 20.30
map_packet('map', 1, 5, {x=60,y=-2000})
owner:tick(true)
assert(#draws == 3 and clears == 1 and calibrations == 2)
camera.distance = 21000
clock = 20.45
owner:tick(true)
assert(calibrations == 3 and #draws == 4 and clears == 2)
viewport.width = 90
clock = 20.60
owner:tick(true)
assert(calibrations == 4 and not menu.frames[3]) -- the new viewport rejects that fit input
viewport.width = 100
viewed_sector = 'sector_c'
clock = 20.75
map_packet('map', 1, 6)
owner:tick(true)
assert(#draws == 4)
viewed_sector = 'sector_b'
clock = 20.9
map_packet('map', 1, 7)
owner:tick(true)
assert(#draws == 5)
map_pointer = false
owner:tick(true)
assert(not menu.frames[3])
map_pointer = true
clock = 21.05
map_packet('map', 1, 8)
owner:tick(true)
assert(menu.frames[3])
local count_before_end = #sent
map_end(1, generation)
map_packet('map', 1, 9) -- old packet queued behind END
owner:tick(true)
assert(not menu.frames[3] and #draws == 6)
assert(#sent == count_before_end + 1 and
    sent[#sent] == prefix .. 'VIEW|map|2|' .. epoch .. '|sector_b|7354|0xde|WRN-302')

local new_generation = ('f'):rep(32)
clock = 21.17
map_end(1, generation)
map_packet('map', 1, 10, {generation=new_generation})
map_end(2, ('c'):rep(32)) -- foreign generation cannot refresh the new serial
map_packet('map', 2, 1, {generation=new_generation, x=65})
owner:tick(true)
assert(menu.frames[3] and #draws == 7 and draws[7].x == 56)
assert(sent[#sent]:match('SEEN|map|2|'))
for index = count_before_end + 2, #sent - 1 do
    assert(sent[index]:match('VIEW|cockpit|'))
end

map.visible = false
map_packet('map', 2, 2, {generation=new_generation})
owner:tick(true)
assert(not menu.frames[3] and sent[#sent] == prefix .. 'VIEW|map|3|' .. epoch .. '|-|0|-|-')
map.visible = true
clock = 21.3
other_layer, menu.frames[3] = true, 501
map_packet('map', 2, 3, {generation=new_generation})
map_packet('map', 4, 1, {generation=new_generation})
owner:tick(true)
assert(menu.frames[3] == 501 and #draws == 7)
other_layer = false
map_packet('map', 4, 2, {generation=new_generation})
owner:tick(true)
assert(menu.frames[3] == 501 and #draws == 7) -- occupied stock layer in same menu
menu.frames[3] = nil
clock = 21.45
map_packet('map', 4, 3, {generation=new_generation})
owner:tick(true)
assert(menu.frames[3] and #draws == 8)
clock = 22.45
owner:tick(true)
assert(not menu.frames[3]) -- exactly one second since launcher's receive
map_packet('map', 4, 3, {generation=new_generation})
owner:tick(true)
assert(not menu.frames[3]) -- duplicates can't make expired data fresh
map_packet('map', 4, 4, {generation=new_generation})
owner:tick(true)
assert(menu.frames[3])
map.matches_now = false -- withdraw between regular view poll and drawing
owner:tick(true)
assert(not menu.frames[3])
map.matches_now = true
map_packet('map', 4, 5, {generation=new_generation})
owner:tick(true)
assert(not menu.frames[3])
owner:close()

-- A full view suppresses requests, and an unreported native registration
-- refusal is attempted only once for the same calibration.
map.matches_now = true
clock, incoming, sent, capacity = 24, {}, {}, false
owner = stream.new({cockpit=cockpit,map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, marker, true)
local prior_attempts = attempts
map_packet('map', 1, 1)
owner:tick(true)
assert(attempts == prior_attempts and not menu.frames[3])
capacity, register = true, false
owner:tick(true)
assert(attempts == prior_attempts + 1 and not menu.frames[3])
map_packet('map', 1, 2, {x=60})
owner:tick(true)
assert(attempts == prior_attempts + 1 and not menu.frames[3])
owner:close()


-- The configured timed wire still works if marker initialization failed.
clock, incoming, sent = 26, {}, {}
owner = stream.new({cockpit=cockpit,map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, nil, true)
owner:tick(true)
map_packet('map', 1, 1, {arrival=clock + .1})
owner:tick(true)
assert(#sent == 2)
map_packet('map', 1, 1)
map_packet('map', 1, 2, {oriented=true})
owner:tick(true)
assert(#sent == 4 and sent[3]:match('SEEN|map|1|') and sent[4]:match('SEEN4|map|1|'))
map_end(1, generation)
map_packet('map', 1, 3)
owner:tick(true)
assert(#sent == 5 and sent[5] == prefix .. 'VIEW|map|2|' .. epoch .. '|sector_b|7354|0xde|WRN-302')
map_packet('map', 2, 1, {generation=new_generation})
owner:tick(true)
assert(#sent == 6 and sent[6]:match('SEEN|map|2|'))
owner:close()

-- An optional renderer fault closes only the renderer. Wire validation and
-- first- and next-generation SEEN remain on the configured map channel.
clock, incoming, sent = 27, {}, {}
local renderer_calls, renderer_closes = 0, 0
local broken_marker = {
    sample=function() renderer_calls = renderer_calls + 1 end,
    tick=function()
        if renderer_calls > 0 then error('renderer failed to draw') end
    end,
    close=function() renderer_closes = renderer_closes + 1 end,
}
owner = stream.new({cockpit=cockpit,map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, broken_marker, true)
owner:tick(true)
map_packet('map', 1, 1)
owner:tick(true)
assert(renderer_calls == 1 and renderer_closes == 1 and sent[#sent]:match('SEEN|map|1|'))
map_packet('map', 1, 2, {oriented=true})
owner:tick(true)
assert(renderer_calls == 1 and #sent == 4 and sent[4]:match('SEEN4|map|1|'))
map_end(1, generation)
map_packet('map', 1, 3)
owner:tick(true)
assert(#sent == 5 and sent[5]:match('VIEW|map|2|'))
map_packet('map', 2, 1, {generation=new_generation})
owner:tick(true)
assert(#sent == 6 and sent[6]:match('SEEN|map|2|'))
owner:close()
assert(renderer_closes == 1)

-- END can overtake its first UDP sample. Only the matching REMOTE
-- corroborates this unestablished generation, and never gets a receipt.
clock, incoming, sent = 28, {}, {}
owner = stream.new({cockpit=cockpit,map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, nil, true)
owner:tick(true)
map_end(1, ('d'):rep(32))
map_end(1, generation)
owner:tick(true)
assert(#sent == 2)
map_packet('map', 1, 1)
map_packet('map', 1, 2)
owner:tick(true)
assert(#sent == 3 and sent[3]:match('VIEW|map|2|'))
map_packet('map', 2, 1, {generation=new_generation})
owner:tick(true)
assert(#sent == 4 and sent[4]:match('SEEN|map|2|'))
owner:close()


-- An occupied toolbar hides only the overlay and does not change projection
-- or consume the remote sample. Exact rectangle edges can touch.
viewport.width, viewed_sector, map_pointer = 100, 'sector_b', true
other_layer, capacity, register = false, true, true
toolbar = {x=48, y=30, width=15, height=25}
local prepared, prior_draws = calibrations, #draws
clock = 30
marker:sample(10, {generation=generation, sequence=1, x=40, y=2000, z=40}, clock)
marker:tick({sector_macro='sector_b'}, 10, clock)
assert(not menu.frames[3] and #draws == prior_draws and calibrations == prepared + 1)
toolbar.x = 49
marker:tick({sector_macro='sector_b'}, 10, clock+.1)
assert(menu.frames[3] and #draws == prior_draws + 1 and calibrations == prepared + 1)
assert(draws[#draws].x == 31 and draws[#draws].y == 31)
toolbar.x = 48
marker:tick({sector_macro='sector_b'}, 10, clock+.2)
assert(not menu.frames[3] and #draws == prior_draws + 1 and calibrations == prepared + 1)
toolbar.y = 50
marker:tick({sector_macro='sector_b'}, 10, clock+.3)
assert(menu.frames[3] and #draws == prior_draws + 2 and calibrations == prepared + 1)
marker:close()

-- Run the native view adapter with the stock table geometry entry points.
-- The toolbar includes the label row and uses the frame's real offset.
local create_marker = marker_module.new
local ffi = package.loaded.ffi
local scale, percentage = 1, false
local mouse_x, mouse_y = .1, .1
local stock = {
    name='MapMenu', shown=true, picking=true, map=70, holomap=71, topLevel=72,
    frameData={[6]={x=11,y=17}}, topLevelHeight=74, frames={},
}
Helper = {borderSize=4}
Menus = {stock}
X4LAN_RESOLVE_STRING = {parse_hex=function() return 91 end}
local stock_camera = {x=0,y=0,z=0,yaw=0,pitch=-math.pi/2,roll=0}
package.loaded.ffi = {
    typeof=function() return true end,
    cdef=function() end,
    new=function(name) return name == 'UIPosRot[1]' and {[0]={}} or {} end,
    C={
        GetMapState=function(_, state) state[0] = {offset=stock_camera, cameradistance=2500} end,
        GetMapPositionOnEcliptic2=function(_, point, _, _, offset)
            point[0].x = 11 + (1 + mouse_x) * 500 * scale
            point[0].y = offset.y or 0
            point[0].z = 17 + (1 - mouse_y) * 350 * scale
            return 81
        end,
        SetMapRelativeMousePosition=function(_, _, x, y) mouse_x, mouse_y = x, y end,
        GetContextByClass=function() return 81 end,
        IsComponentClass=function() return true end,
        GetUIScale=function() return scale end,
    },
}
function IsValidWidgetElement(id) return id == stock.topLevel or id == stock.frames[3] end
function GetRenderTargetMousePosition() return mouse_x, mouse_y end
function GetSize() return 1000*scale, 700*scale end
function GetOffset(id)
    if id == stock.topLevel then return 315*scale, 20*scale end
    return 0, 0
end
function GetTableColumnWidths(id)
    assert(id == stock.topLevel)
    return 40*scale, 50*scale, 40*scale
end
function IsTableColumnWidthPercentage(id)
    assert(id == stock.topLevel)
    return percentage
end
marker_module.new = function(options) return options end
local native_view = marker_module.new_x4().view
local function check_stock_layout()
    local view = assert(native_view({actor='0x5b'}, 2000))
    assert(view.viewport.x == 11 and view.viewport.y == 17)
    assert(view.viewport.width == 1000*scale and view.viewport.height == 700*scale)
    assert(view.toolbar.x == 11+315*scale and view.toolbar.y == 17+20*scale)
    assert(view.toolbar.width == 138*scale and view.toolbar.height == 54*scale)
end
check_stock_layout()
scale = 1.5
Helper.borderSize, stock.topLevelHeight = 4*scale, 74*scale
check_stock_layout()
percentage = true
assert(native_view({actor='0x5b'}, 2000).toolbar == nil)
marker_module.new = create_marker

-- The native adapter and real marker keep moving and returning SEEN when
-- the toolbar has no usable rectangle. A measured overlap still hides it.
scale, Helper.borderSize, stock.topLevelHeight = 1, 4, 74
local registered = {}
Helper.createFrameHandle = function(actual, properties)
    assert(actual == stock and properties.layer == 3)
    local id = 500 + #registered
    return {id=id, display=function()
        registered[#registered+1] = properties
        stock.frames[3] = id
    end}
end
Helper.clearFrame = function(actual, layer)
    assert(actual == stock and layer == 3)
    stock.frames[3] = nil
end
clock, incoming, sent = 31, {}, {}
owner = stream.new({cockpit=cockpit, map=map}, epoch, net, prefix, function() return clock end,
    nil, nil, nil, nil, marker_module.new_x4(), true)
owner:tick(true)
local function native_sample(sequence, x)
    clock = clock + .125
    local previous = #sent
    map_packet('map', 1, sequence, {x=x})
    owner:tick(true)
    for i = previous + 1, #sent do
        if sent[i]:match('SEEN|map|1|') and sent[i]:match('|' .. sequence .. '|sector_b|') then return end
    end
    error('expected map receipt for sample ' .. sequence)
end
native_sample(1, 350)
assert(#registered == 1 and registered[1].x == 341)
stock.topLevel = nil
native_sample(2, 405)
assert(#registered == 2 and registered[2].x == 396)

stock.topLevel, percentage = 72, false
local column_widths = GetTableColumnWidths
GetTableColumnWidths = nil
native_sample(3, 420)
assert(#registered == 3 and registered[3].x == 411)
GetTableColumnWidths = function() error('toolbar invalidated while reading column widths') end
native_sample(4, 400)
assert(#registered == 4 and registered[4].x == 391)

GetTableColumnWidths = column_widths
native_sample(5, 350)
assert(#registered == 4 and not stock.frames[3])
native_sample(6, 510)
assert(#registered == 5 and registered[5].x == 501 and stock.frames[3])
owner:close()
assert(not stock.frames[3])
package.loaded.ffi = ffi
