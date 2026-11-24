local Snapshot = assert(loadfile('tools/dev/authority_registry_availability_snapshot.lua'))()
local Gate = assert(loadfile('src/authority/lua/registry_checkpoint_gate.lua'))()

local COUNT = 10082
local EPOCH, NONCE, INITIAL = string.rep('a', 32), string.rep('b', 32), string.rep('c', 32)
local BINDING = string.rep('1', 10075) .. string.rep('0', 7)
local OPERATIONAL = string.rep('1', 10019) .. string.rep('0', 63)

local function make_gate(overrides)
    local time, paused = 0, true
    local requests, stores, reports, faults = {}, {}, {}, {}
    local options = {
        enabled = true, epoch = EPOCH, nonce = NONCE, initial_run_id = INITIAL,
        issued_count = COUNT, expected_binding_bits = BINDING,
        expected_operational_bits = OPERATIONAL, snapshot_owner = Snapshot, timeout = 110,
        clock = function() return time end,
        guard = function() return paused end,
        issue = function(action, payload)
            requests[#requests + 1] = {action = action, payload = payload}
            return true
        end,
        store_marker = function(marker) stores[#stores + 1] = marker end,
        report = function(value) reports[#reports + 1] = value end,
        fault = function(reason) faults[#faults + 1] = reason end,
    }
    for key, value in pairs(overrides or {}) do options[key] = value end
    local gate = Gate.new(options)
    return gate, requests, stores, reports, faults,
        function(value) time = value end, function(value) paused = value end
end

local function count_bits(value)
    local ones = select(2, value:gsub('1', ''))
    return ones, #value - ones
end

local function body(request, mode)
    local action, payload = request.action, request.payload
    if action == 'Initialize' or action == 'BeginNext' then return 'Initialized|' .. COUNT end
    if action == 'Seal' then return table.concat({'Sealed', COUNT, 10075, 7, 10019}, '|') end
    local first, count = payload.first, payload.count
    local binding = BINDING:sub(first, first + count - 1)
    local operational = OPERATIONAL:sub(first, first + count - 1)
    if mode == 'inspect' then
        return table.concat({first, count, 'Inspected', binding, binding,
            operational, operational}, '|')
    end
    local available, unavailable = count_bits(BINDING:sub(1, first + count - 1))
    local operational_count = count_bits(OPERATIONAL:sub(1, first + count - 1))
    return table.concat({first, count, 'Captured', available, unavailable,
        operational_count, binding, operational}, '|')
end

local function complete_active(gate, requests, start_index, mode)
    local cursor = start_index
    while gate:state().active_mode do
        if cursor > #requests then assert(gate:tick()) end
        if cursor <= #requests then
            local request = requests[cursor]
            assert(gate:event(table.concat({request.payload.request_id,
                request.payload.run_id, request.action, body(request, mode)}, '|')))
            if not gate:tick() then return cursor + 1 end
            cursor = cursor + 1
        end
    end
    return cursor
end

local function initialized_gate()
    local gate, requests, stores, reports, faults, set_time = make_gate()
    assert(gate:start())
    assert(gate:tick())
    local next_request = complete_active(gate, requests, 1, 'inspect')
    assert(gate:state().ready and not gate:state().stopped)
    assert(gate:state().current_run_id == INITIAL and #stores == 0 and #faults == 0)
    assert(#reports == 1 and reports[1].mode == 'inspect')
    return gate, requests, stores, reports, faults, next_request, set_time
end

local function test_two_fresh_full_captures()
    local gate, requests, stores, reports, faults, cursor = initialized_gate()
    local first_marker, second_marker = string.rep('d', 32), string.rep('e', 32)
    assert(gate:prepare(first_marker))
    assert(gate:state().current_run_id == INITIAL and #stores == 0)
    assert(gate:tick())
    cursor = complete_active(gate, requests, cursor, 'capture')
    assert(gate:state().ready and gate:state().current_run_id == first_marker)
    assert(stores[1] == first_marker and #stores == 1 and #faults == 0)
    assert(reports[2].mode == 'capture' and reports[2].run_id == first_marker)
    assert(reports[2].availability_bits == BINDING)

    assert(gate:tick()) -- Ready ticks do not require the world to remain paused.
    assert(gate:prepare(second_marker))
    assert(gate:tick())
    complete_active(gate, requests, cursor, 'capture')
    assert(stores[2] == second_marker and #stores == 2 and #faults == 0)
    assert(gate:state().current_run_id == second_marker)
end

local function test_corrupt_initial_inspect_blocks_readiness()
    local bad_binding = '0' .. BINDING:sub(2)
    local gate, requests, stores, _, faults = make_gate()
    assert(gate:start() and gate:tick())
    local request = requests[1]
    local answer = table.concat({request.payload.request_id, request.payload.run_id,
        request.action, 1, 64, 'Inspected', bad_binding:sub(1, 64),
        BINDING:sub(1, 64), OPERATIONAL:sub(1, 64), OPERATIONAL:sub(1, 64)}, '|')
    assert(gate:event(answer) and not gate:tick())
    assert(gate:state().stopped and not gate:state().ready)
    assert(#stores == 0 and #faults > 0)
end

local function test_mismatch_identifies_the_observation_and_first_indices()
    local pinned = '00' .. OPERATIONAL:sub(3)
    local gate, requests, stores, _, faults = make_gate({expected_operational_bits = pinned})
    assert(gate:start() and gate:tick())
    complete_active(gate, requests, 1, 'inspect')
    assert(gate:state().stopped and #stores == 0 and #faults == 1)
    assert(faults[1]:find('saved operational differs at 2 indices, first 1(0>1),2(0>1)', 1, true))
end

local function test_replay_marker_and_incomplete_capture_never_store()
    local gate, requests, stores, _, faults, _, set_time = initialized_gate()
    local marker = string.rep('f', 32)
    assert(gate:prepare(marker) and gate:tick())
    -- The capture remains partial and its request deadline expires.
    set_time(31)
    assert(not gate:tick())
    assert(#stores == 0 and gate:state().stopped and #faults == 1)
    assert(faults[1] == 'registry checkpoint operation timed out')

    local cursor
    gate, requests, stores, _, _, cursor = initialized_gate()
    assert(gate:prepare(marker) and gate:tick())
    -- A marker cannot be prepared a second time, even after successful completion.
    complete_active(gate, requests, cursor, 'capture')
    assert(stores[1] == marker and gate:state().ready)
    local ok = gate:prepare(marker)
    assert(not ok and #stores == 1 and gate:state().stopped)
end

local function test_reentrant_report_faults_before_store()
    local gate, requests, stores, _, faults
    local reenter = false
    gate, requests, stores, _, faults = make_gate({
        report = function(evidence)
            if evidence.mode == 'capture' and reenter then gate:tick() end
        end,
    })
    assert(gate:start() and gate:tick())
    local cursor = complete_active(gate, requests, 1, 'inspect')
    reenter = true
    assert(gate:prepare(string.rep('9', 32)) and gate:tick())
    complete_active(gate, requests, cursor, 'capture')
    assert(gate:state().stopped and #stores == 0 and #faults > 0)
end

local function test_guard_reentry_stops_before_issue()
    local gate, requests, stores, _, faults
    local reenter, did_reenter = false, false
    gate, requests, stores, _, faults = make_gate({
        guard = function()
            if reenter and not did_reenter then
                did_reenter = true
                gate:prepare(string.rep('8', 32))
            end
            return true
        end,
    })
    assert(gate:start() and gate:tick())
    local cursor = complete_active(gate, requests, 1, 'inspect')
    assert(gate:prepare(string.rep('7', 32)))
    local before = #requests
    reenter = true
    assert(not gate:tick())
    assert(gate:state().stopped and #requests == before and #stores == 0 and #faults > 0)
end

local function test_report_deadline_and_store_refusal_fence()
    local set_time
    local gate, requests, stores, _, faults
    gate, requests, stores, _, faults, set_time = make_gate({
        report = function(evidence)
            if evidence.mode == 'capture' then set_time(110) end
        end,
    })
    assert(gate:start() and gate:tick())
    local cursor = complete_active(gate, requests, 1, 'inspect')
    assert(gate:prepare(string.rep('6', 32)) and gate:tick())
    complete_active(gate, requests, cursor, 'capture')
    assert(gate:state().stopped and #stores == 0 and #faults == 1)
    assert(faults[1] == 'registry checkpoint operation timed out')

    local attempted = 0
    requests, stores, faults = {}, {}, {}
    local time = 0
    gate = Gate.new({enabled=true, epoch=EPOCH, nonce=NONCE, initial_run_id=INITIAL,
        issued_count=COUNT, expected_binding_bits=BINDING,
        expected_operational_bits=OPERATIONAL, timeout=110, snapshot_owner=Snapshot,
        clock=function() return time end, guard=function() return true end,
        issue=function(action, payload)
            requests[#requests + 1] = {action=action, payload=payload}; return true
        end,
        store_marker=function() attempted = attempted + 1; return false end,
        report=function() end, fault=function(reason) faults[#faults + 1] = reason end})
    local initial_cursor = 1
    assert(gate:start() and gate:tick())
    local next_cursor = complete_active(gate, requests, initial_cursor, 'inspect')
    assert(gate:prepare(string.rep('5', 32)) and gate:tick())
    complete_active(gate, requests, next_cursor, 'capture')
    assert(gate:state().stopped and attempted == 1 and #stores == 0 and #faults > 0)
end

local function test_tick_fault_diagnosis_is_bounded()
    for _, scenario in ipairs({
        {reason = 'registry checkpoint operation timed out', time = 110},
        {reason = 'registry checkpoint invalid or regressed clock', time = -1},
        {reason = 'registry checkpoint invalid or regressed clock', time = math.huge},
        {reason = 'registry checkpoint invalid or regressed clock', time = 'private clock data'},
        {reason = 'registry checkpoint invalid or regressed clock', clock_throws = true},
        {reason = 'snapshot owner tick failed', owner_throws = true},
    }) do
        local time, armed = 0, false
        local gate, _, stores, _, faults = make_gate({
            clock = function()
                if armed and scenario.clock_throws then error('private clock callback data') end
                return time
            end,
            guard = function()
                if armed and scenario.owner_throws then
                    error('private owner callback data:123: request timed out')
                end
                return true
            end,
        })
        assert(gate:start())
        time, armed = scenario.time or 0, true
        local ok, status = gate:tick()
        assert(not ok and status == 'faulted')
        assert(gate:state().stopped and #stores == 0)
        assert(#faults == 1 and faults[1] == scenario.reason, faults[1])
        assert(not gate:tick() and #faults == 1)
    end
end

local function make_first_gate()
    local time, paused = 0, true
    local requests, stores, reports, faults = {}, {}, {}, {}
    local gate = Gate.new({enabled=true, initial_mode='first_capture',
        source_marker=INITIAL, epoch=EPOCH, nonce=NONCE,
        issued_count=COUNT, timeout=110, snapshot_owner=Snapshot,
        clock=function() return time end, guard=function() return paused end,
        issue=function(action, payload)
            requests[#requests + 1] = {action=action, payload=payload}; return true
        end,
        store_marker=function(marker) stores[#stores + 1] = marker; return true end,
        report=function(evidence) reports[#reports + 1] = evidence end,
        fault=function(reason) faults[#faults + 1] = reason end})
    return gate, requests, stores, reports, faults,
        function(value) time = value end, function(value) paused = value end
end

local function test_first_capture_and_second_save_linkage()
    local gate, requests, stores, reports, faults = make_first_gate()
    local first_marker, second_marker = string.rep('d', 32), string.rep('e', 32)
    assert(not gate:admission_ready())
    assert(gate:start_first_capture(first_marker) and gate:tick())
    assert(requests[1].action == 'Initialize'
        and requests[1].payload.previous_run_id == nil)
    local cursor = complete_active(gate, requests, 1, 'capture')
    assert(gate:ready() and not gate:admission_ready())
    assert(reports[1].mode == 'first_capture' and reports[1].source_marker == INITIAL)
    assert(reports[1].previous_run_id == nil and stores[1] == first_marker)
    assert(gate:state().current_run_id == first_marker)
    assert(gate:confirm_first_save(first_marker, true) and gate:admission_ready())
    assert(gate:prepare(second_marker) and gate:tick())
    assert(requests[cursor].action == 'BeginNext'
        and requests[cursor].payload.previous_run_id == first_marker)
    complete_active(gate, requests, cursor, 'capture')
    assert(stores[2] == second_marker and reports[2].mode == 'capture'
        and reports[2].previous_run_id == first_marker and #faults == 0)
end

local function test_first_capture_collisions_and_failed_initialize()
    for _, reserved in ipairs({INITIAL, EPOCH, NONCE}) do
        local gate, requests, stores, _, faults = make_first_gate()
        assert(not gate:start_first_capture(reserved))
        assert(gate:state().stopped and #requests == 0 and #stores == 0 and #faults == 1)
    end
    local gate, requests, stores, _, faults = make_first_gate()
    assert(gate:start_first_capture(string.rep('d', 32)) and gate:tick())
    local request = requests[1]
    assert(request.action == 'Initialize')
    assert(gate:event(request.payload.request_id..'|'..request.payload.run_id
        ..'|Initialize|Refused'))
    assert(not gate:tick() and gate:state().stopped and #stores == 0 and #faults > 0)

    gate, requests, stores, _, faults = make_first_gate()
    local marker = string.rep('d', 32)
    assert(gate:start_first_capture(marker) and gate:tick())
    complete_active(gate, requests, 1, 'capture')
    assert(not gate:confirm_first_save(marker, false))
    assert(not gate:admission_ready() and gate:state().stopped)
    assert(#stores == 1 and #faults == 1)
end

local function test_shuttle_seed_extends_only_after_inspection()
    local normal = make_gate()
    assert(normal.accept_company_shuttle_pair == nil)
    local early = make_gate({company_shuttle_seed = true})
    assert(early:accept_company_shuttle_pair() == false and early:state().stopped)
    local gate, requests, stores, reports, faults = make_gate({company_shuttle_seed = true})
    assert(gate:start() and gate:tick())
    local cursor = complete_active(gate, requests, 1, 'inspect')
    assert(reports[1].issued_count == COUNT)
    assert(gate:accept_company_shuttle_pair())
    assert(gate:prepare(string.rep('d',32)) and gate:tick())
    assert(requests[cursor].action == 'BeginNext' and requests[cursor].payload.issued_count == COUNT + 2)
    assert(#stores == 0 and #faults == 0)
    assert(gate:accept_company_shuttle_pair() == false and gate:state().stopped)
end

test_shuttle_seed_extends_only_after_inspection()
test_two_fresh_full_captures()
test_corrupt_initial_inspect_blocks_readiness()
test_mismatch_identifies_the_observation_and_first_indices()
test_replay_marker_and_incomplete_capture_never_store()
test_reentrant_report_faults_before_store()
test_guard_reentry_stops_before_issue()
test_report_deadline_and_store_refusal_fence()
test_tick_fault_diagnosis_is_bounded()
test_first_capture_and_second_save_linkage()
test_first_capture_collisions_and_failed_initialize()
print('authority registry checkpoint gate: ok')
