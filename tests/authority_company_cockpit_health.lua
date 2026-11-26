local module = assert(loadfile(arg[1]))()
local lease, epoch, generation = ('a'):rep(32), ('b'):rep(32), ('c'):rep(32)
local logs = {}
function DebugError(message) logs[#logs + 1] = message end

local function fixture()
    local f = {now=100, helm='0x4071', available=true, receipts={}, calls={}, read_count=0,
        health={ok=true, shield=30, shieldmax=2366, hull=3600, hullmax=3600}}
    f.api = module.new({enabled=true, lease=lease, epoch=epoch, index=1074,
        clock=function() return f.now end,
        controlled_actor=function() return f.helm end,
        available=function() return f.available end,
        read_health=function(actor)
            assert(actor == f.helm)
            f.read_count = f.read_count + 1
            return f.health
        end,
        on_receipt=function(receipt) f.receipts[#f.receipts + 1] = receipt end,
        register=function(name, callback)
            assert(name == 'X4AuthorityCompanyCockpitHealth.Result')
            f.event = callback
        end,
        issue=function(screen, command, args)
            assert(screen == 'X4AuthorityCompanyCockpitHealth')
            f.calls[#f.calls + 1] = {command=command, args=args}
            return f.fail_command ~= command
        end,
    })
    function f:row(sequence, updates)
        local result = {generation=generation, window=3, epoch=epoch, index=1074, sequence=sequence,
            shield=4, maxshield=2366, hull=3578, maxhull=3600, expires_at=self.now + 0.1,
            authority_revision=6}
        for key, value in pairs(updates or {}) do result[key] = value end
        return result
    end
    function f:reply(serial, status, proof)
        self.event(nil, (proof or lease) .. '|' .. serial .. '|' .. status)
    end
    assert(f.api:set_window(generation, 3))
    return f
end

assert(not pcall(module.new, {enabled=false}))

do
    local f = fixture()
    for _, invalid in ipairs({{generation=('d'):rep(32)}, {window=1}, {epoch=lease}, {index=1073},
        {sequence=0}, {hull=0}, {hull=3601}, {shield=-1}, {shield=2367}, {shield=0/0},
        {maxshield=math.huge}, {expires_at=99}, {expires_at=103}}) do
        assert(not f.api:apply(f:row(1, invalid)))
    end
    assert(not f.api:set_window(epoch, 4))
    assert(not f.api:set_window(generation, 2))
    f.available = false
    assert(not f.api:apply(f:row(1)))
    f.available, f.helm = true, '0x0'
    assert(not f.api:apply(f:row(1)))
    f.helm, f.health.hull = '0x4071', 0
    assert(not f.api:apply(f:row(1)))
    f.health.hull, f.health.shieldmax = 3600, 2367
    assert(not f.api:apply(f:row(1)))
    assert(#f.calls == 0)
end

do
    local f = fixture()
    local link = module.motion_link(f.api)
    local function packet(revision, reply, anchor)
        return {'HEALTH', tostring(revision), generation, epoch, '1074', '3', tostring(reply),
            tostring(anchor), '100.1', '4', '2366', '3578', '3600', '0'}
    end
    link:sample(100)
    link:receive(packet(1, 1, 100))
    assert(f.calls[1].command == 'Prepare')
    f:reply(1, 'prepare_rejected')
    link:gap()
    link:receive(packet(1, 2, 100))
    link:sample(101)
    link:receive(packet(1, 2, 100))
    link:receive(packet(1, 2, 102))
    link:receive({'HEALTH_CLEAR', '2'})
    link:receive(packet(1, 2, 101))
    assert(#f.calls == 1)
    link:receive(packet(2, 2, 101))
    assert(f.calls[2].command == 'Prepare' and f.calls[2].args.sequence == 2)
    assert(#logs == 0)
    f:reply(2, 'prepared')
    f.health.shield, f.health.hull = 4, 3578
    f:reply(2, 'applied')
    f:reply(2, 'applied')
    assert(#logs == 1 and logs[1] == '[X4LAN] AUTH_COMPANY_COCKPIT_HEALTH_CONFIRMED'
        .. ' index=1074 window=3 sequence=2 revision=0 server_shield=4 server_hull=3578 cockpit_shield=4 cockpit_hull=3578')
    local legacy = packet(2, 3, 101)
    legacy[14] = 'invalid'
    link:receive(legacy)
    assert(#f.calls == 3)
    legacy[14] = nil
    link:receive(legacy)
    f:reply(3, 'prepared')
    f:reply(3, 'applied')
    assert(f.receipts[#f.receipts].sequence == 3 and f.receipts[#f.receipts].status == 'confirmed')
    assert(#logs == 1)
end

do
    local f = fixture()
    assert(f.api:apply(f:row(28)))
    assert(#f.calls == 1 and f.calls[1].command == 'Prepare' and f.read_count == 1)
    local request = f.calls[1].args
    assert(request.lease == lease and request.generation == generation and request.window == 3
        and request.sequence == 28 and request.actor == f.helm and request.index == 1074
        and request.epoch == epoch and request.shield == 4 and request.hull == 3578)
    assert(not f.api:apply(f:row(29)))
    f:reply(2, 'prepared')
    f:reply(1, 'prepared', epoch)
    assert(#f.calls == 1 and #f.receipts == 0)
    f:reply(1, 'prepared')
    assert(#f.calls == 2 and f.calls[2].command == 'Apply')
    f:reply(1, 'prepared')
    f:reply(1, 'prepare_rejected')
    assert(#f.calls == 2 and #f.receipts == 0)
    f.health.shield, f.health.hull = 4, 3578
    f:reply(1, 'applied')
    local result = f.receipts[1]
    assert(#logs == 2 and logs[2]:find('sequence=28 revision=6', 1, true))
    assert(f.read_count == 2 and result.status == 'confirmed' and result.generation == generation
        and result.window == 3 and result.epoch == epoch and result.index == 1074 and result.sequence == 28
        and result.shield == 4 and result.hull == 3578 and result.maxshield == 2366 and result.maxhull == 3600)

    assert(f.api:set_window(nil) and f.api:set_window(generation, 3))
    assert(not f.api:apply(f:row(28)))
    assert(f.api:apply(f:row(29, {shield=50, hull=3600})))
    f:reply(2, 'prepared')
    f.health.shield, f.health.hull = 50, 3600
    f:reply(2, 'applied')
    assert(f.receipts[2].status == 'confirmed' and f.receipts[2].hull == 3600)
    f.helm = '0x4072'
    assert(not f.api:apply(f:row(30)))
    f.helm = '0x4071'
    assert(f.api:set_window(generation, 4))
    assert(not f.api:apply(f:row(30)))
    assert(f.api:apply(f:row(1, {window=4})))
    f:reply(3, 'prepare_rejected')
    assert(f.receipts[3].status == 'rejected')
end

do
    local f = fixture()
    assert(f.api:apply(f:row(10)))
    f.now = 100.1
    f:reply(1, 'prepared')
    assert(#f.calls == 2 and f.calls[2].command == 'Clear' and f.receipts[1].status == 'rejected')
    f:reply(1, 'applied')
    assert(#f.receipts == 1 and not f.api:apply(f:row(10)))
    assert(f.api:apply(f:row(11)))
    f.helm = nil
    f.api:tick()
    assert(f.calls[#f.calls].command == 'Clear' and f.receipts[2].status == 'rejected')
    f.helm = '0x4071'
    assert(not f.api:apply(f:row(12)))
    assert(f.api:set_window(generation, 3))
    assert(f.api:apply(f:row(12)))
    f:reply(3, 'prepared')
    f:reply(3, 'apply_rejected')
    assert(f.receipts[3].status == 'rejected' and f.api:apply(f:row(13)))
    f.api:close()
    assert(f.receipts[4].status == 'rejected' and not f.api:set_window(generation, 4))
end

do
    local f = fixture()
    assert(f.api:apply(f:row(1)))
    f:reply(1, 'prepared')
    assert(not f.api:set_window(nil))
    assert(f.calls[#f.calls].command == 'Clear' and f.receipts[1].status == 'unknown')
    f:reply(1, 'applied')
    assert(#f.receipts == 1 and not f.api:set_window(generation, 4) and not f.api:apply(f:row(2)))
end

do
    local f = fixture()
    assert(f.api:apply(f:row(1)))
    f:reply(1, 'prepared')
    f.health.hull = 3578
    -- Shield readback disagrees even though the MD result reports both setters ran.
    f:reply(1, 'applied')
    assert(logs[#logs]:find('reason=readback_unconfirmed', 1, true))
    assert(f.receipts[1].status == 'unknown' and not f.api:apply(f:row(2)))
end

do
    local f = fixture()
    f.fail_command = 'Apply'
    assert(f.api:apply(f:row(1)))
    f:reply(1, 'prepared')
    assert(logs[#logs]:find('reason=apply_dispatch_failed', 1, true))
    assert(f.receipts[1].status == 'unknown' and not f.api:apply(f:row(2)))
end

do
    local f = fixture()
    assert(f.api:apply(f:row(1, {expires_at=101.75})))
    assert(f.calls[1].args.validity == 0.25)
    f.now = 101
    f:reply(1, 'prepared')
    assert(f.calls[2].command == 'Clear' and f.receipts[1].status == 'rejected')
end

-- The local bridge clears each revision before sending its first snapshot.
-- UDP can duplicate the clear or deliver it after that snapshot.
do
    local f = fixture()
    local link = module.motion_link(f.api)
    local function begin_apply(revision, sequence)
        link:receive({'HEALTH', tostring(revision), generation, epoch, '1074', '3',
            tostring(sequence), '100', '100.1', '4', '2366', '3578', '3600', '6'})
        f:reply(sequence, 'prepared')
        assert(f.calls[#f.calls].command == 'Apply')
    end
    link:sample(100)
    begin_apply(1, 1)
    link:receive({'HEALTH_CLEAR', '1'})
    link:receive({'HEALTH_CLEAR', '1'})
    assert(#f.receipts == 0, 'a late clear for the current revision interrupted its health write')
    f.health.shield, f.health.hull = 4, 3578
    f:reply(1, 'applied')
    assert(f.receipts[1].status == 'confirmed')
    begin_apply(1, 2)
    link:receive({'HEALTH_CLEAR', '2'})
    assert(f.receipts[2].status == 'unknown', 'a newer revision must still invalidate a pending write')
    f:reply(2, 'applied')
    assert(#f.receipts == 2 and not f.api:set_window(generation, 4))
    assert(logs[#logs] == '[X4LAN] AUTH_COMPANY_COCKPIT_HEALTH_UNCONFIRMED'
        .. ' index=1074 window=3 sequence=2 reason=context_changed')
end
