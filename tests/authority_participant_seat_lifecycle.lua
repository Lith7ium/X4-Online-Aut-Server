local cfg = {
    authority_participant_port = 123,
    authority_participant_lease = string.rep('a', 32),
    authority_ticket = {world_epoch = string.rep('b', 32)},
    authority_company_motion = {},
    authority_company_purchase = {},
}
X4LAN_CONFIG = cfg
package.loaded.ffi = {cdef=function() end, C={IsPlayerValid=function() return true end}}

local function participant()
    local update, incoming, fault, refuse_resume_receipt
    local next_command, request_id = nil, 0
    local mock = {helm=false, admits=0, ticks=0, prepares=0, motion=false, purchase=false}
    local net = {
        send=function(_, payload)
            return not (refuse_resume_receipt and payload:match('|resume|confirmed$'))
        end,
        receive=function()
            local command = incoming
            incoming = nil
            return command
        end,
    }
    X4LAN_LOCAL_UDP_MODULE = {load=function() return {open=function() return net end} end}
    X4LAN_AUTHORITY_X4_CONTROL_ENGINE = {new=function() return {} end}
    X4LAN_COMPANY_MOTION_SOURCE = {new_x4=function() return {
        set_running=function(_, value) mock.motion = value end,
        tick=function() end,
    } end}
    X4LAN_COMPANY_PURCHASE_SOURCE = {new_x4=function() return {
        set_running=function(_, value) mock.purchase = value end,
        prepare_seat=function() assert(mock.helm); mock.prepares = mock.prepares + 1 end,
        tick=function() end,
    } end}
    X4LAN_AUTHORITY_CONTROL_STATE = {new=function(_, _, receipt) return {
        submit=function(_, _, _, id, op)
            next_command = {request_id=id, operation=op, status='confirmed'}
            return true
        end,
        on_update=function()
            local command = next_command
            next_command = nil
            if command then receipt(command) end
            return true
        end,
    } end}
    X4LAN_AUTHORITY_SELECTED_SEAT = {
        admit=function() mock.admits = mock.admits + 1; return true end,
        tick=function() mock.ticks = mock.ticks + 1; return true end,
        ready=function() return mock.helm end,
    }
    GetCurRealTime = function() return 1 end
    SetScript = function(name, callback) assert(name == 'onUpdate'); update = callback end
    DebugError = function(row) fault = row end
    dofile('tools/dev/authority_two_x4_participant_probe.lua')

    function mock:update() update() end
    function mock:command(operation, send_failure)
        request_id = request_id + 1
        incoming = table.concat({'CMD', cfg.authority_participant_lease, cfg.authority_ticket.world_epoch,
            request_id, operation}, '|')
        refuse_resume_receipt = send_failure
        update()
        return not fault
    end
    return mock
end

local passenger = participant()
passenger:update()
assert(passenger:command('pause'))
assert(passenger.admits == 0 and passenger.ticks == 0 and not passenger.motion and not passenger.purchase)
assert(passenger:command('resume'))
assert(passenger.admits == 1 and passenger.ticks == 1 and not passenger.motion and not passenger.purchase)
passenger.helm = true
passenger:update()
assert(passenger.motion and passenger.purchase and passenger.prepares == 1)
passenger.helm = false
passenger:update()
assert(not passenger.motion and not passenger.purchase and passenger.prepares == 1)
passenger.helm = true
assert(passenger:command('pause'))
assert(not passenger.motion and not passenger.purchase)
assert(passenger:command('resume'))
assert(passenger.admits == 1 and passenger.motion and passenger.purchase)

local abandoned = participant()
assert(not abandoned:command('resume', true))
abandoned.helm = true
abandoned:update()
assert(abandoned.admits == 0 and abandoned.ticks == 0 and not abandoned.motion and not abandoned.purchase)
