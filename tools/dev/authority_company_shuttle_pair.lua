-- Runs only in the private preparation or cold-inspection overlay.
local M = {}

function M.new_x4(config, guard)
    local screen = config.screen or 'X4AuthorityCompanyShuttlePair'
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    if not pcall(ffi.typeof, 'UIPosRot') then
        ffi.cdef('typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;')
    end
    if not pcall(ffi.typeof, 'SoftwareSlot') then
        ffi.cdef('typedef struct {const char* max; const char* current;} SoftwareSlot;')
    end
    ffi.cdef[[
        uint32_t GetNumSoftwareSlots(UniverseID,const char*);
        uint32_t GetSoftwareSlots(SoftwareSlot*,uint32_t,UniverseID,const char*);
        bool IsValidComponent(UniverseID); bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID,const char*); const char* GetObjectIDCode(UniverseID);
        UniverseID GetContextByClass(UniverseID,const char*,bool); UIPosRot GetObjectPositionInSector(UniverseID);
        UniverseID GetPlayerContainerID(void); UniverseID GetPlayerControlledShipID(void);
        UniverseID GetPlayerOccupiedShipID(void);
    ]]
    local C, parse = ffi.C, X4LAN_RESOLVE_STRING.parse_hex
    local action = config.mode == 'prepare' and 'Create' or 'Inspect'
    local waiting, failure, finished, deadline = nil, nil, false, GetCurRealTime() + 30
    local indices = config.indices or {10083,10084}
    assert(#indices == 2 or (config.live_station and #indices == 4), 'invalid fixture ship count')
    local slot, current, ships = 1, indices[1], {}
    local codes = {}
    local station_code, room = config.station or 'XPJ-169', config.room
    local function log(row) DebugError('[X4LAN] ' .. (config.tag or 'AUTH_COMPANY_SHUTTLE_PAIR') .. ' ' .. config.nonce .. '|' .. row) end
    local function refuse(reason)
        if not failure then failure = reason; log('ERROR|' .. reason) end
    end
    local function code(id)
        local ptr = C.GetObjectIDCode(id)
        return ptr ~= nil and ptr ~= ffi.NULL and ffi.string(ptr)
    end
    local function receive(which, row)
        if not guard() or waiting ~= which or finished or failure or type(row) ~= 'string' then
            return refuse('UNEXPECTED_' .. which)
        end
        waiting = nil
        if which == 'Create' then
            if row ~= config.nonce .. '|CREATED' then return refuse('CREATE_' .. row:sub(1,100)) end
            action = 'Inspect'
            return
        end
        if config.live_station then
            if config.mode == 'prepare' and row == config.nonce .. '|' .. current .. '|PARKING' then return end
            local core, observed_station, observed_room = row:match('^(.*)|([A-Z0-9]+%-[A-Z0-9]+)|([%w_]+)$')
            if not core then return refuse('INSPECT_' .. row:sub(1,100)) end
            if (config.station or ships[1]) and (observed_station ~= station_code or observed_room ~= room) then
                return refuse('STATION_CHANGED')
            end
            row, station_code, room = core, observed_station, observed_room
        end
        local nonce, index, shipref, sectorref, shipcode =
            row:match('^([a-f0-9]+)|(%d+)|AVAILABLE|(0[xX]%x+)|(0[xX]%x+)|([A-Z0-9]+%-[A-Z0-9]+)$')
        if nonce ~= config.nonce or tonumber(index) ~= current then return refuse('INSPECT_' .. row:sub(1,110)) end
        local id, sector = parse(shipref), parse(sectorref)
        if not id or not sector or not C.IsValidComponent(id) or not C.IsComponentClass(id, 'ship')
                or not C.IsComponentOperational(id) or code(id) ~= shipcode
                or not C.IsValidComponent(sector) or not C.IsComponentClass(sector, 'sector')
                or C.GetContextByClass(id, 'sector', false) ~= sector then return refuse('LOCAL_BINDING') end
        local container = C.GetPlayerContainerID()
        local station = C.GetContextByClass(container, 'station', true)
        if station == 0 or code(station) ~= station_code or C.GetPlayerControlledShipID() ~= 0
                or C.GetPlayerOccupiedShipID() ~= 0 then return refuse('LOCAL_AVATAR') end
        local ref = ConvertStringToLuaID((tostring(id):gsub('ULL$', '')))
        if not ref or X4LAN_RESOLVE_STRING.actor_u64(ConvertIDTo64Bit(ref)) ~= id then return refuse('LOCAL_ID') end
        local owner, docked, docking, stored = GetComponentData(ref,'owner','isdocked','isdocking','isinternallystored')
        if owner ~= 'player' or docked ~= false or docking ~= false or stored ~= false then return refuse('LOCAL_SEAT') end
        local count = tonumber(C.GetNumSoftwareSlots(id, ''))
        if count ~= #config.software then return refuse('SOFTWARE_SLOT_COUNT_' .. tostring(count)) end
        local slots = ffi.new('SoftwareSlot[?]', count)
        if C.GetSoftwareSlots(slots, count, id, '') ~= count then return refuse('SOFTWARE_READ') end
        local current_wares = {}
        for i = 0, count - 1 do
            local ware = slots[i].current
            if ware == nil or ware == ffi.NULL then return refuse('SOFTWARE_EMPTY') end
            current_wares[ffi.string(ware)] = true
        end
        for _, required in ipairs(config.software) do
            if not current_wares[required] then return refuse('SOFTWARE_MISSING_' .. required) end
        end
        local pose = C.GetObjectPositionInSector(id)
        if config.codes and config.codes[slot] ~= shipcode then return refuse('COLD_IDENTITY') end
        local first = ships[1]
        if first then
            for _, previous in ipairs(ships) do
                if previous.id == id or previous.code == shipcode then return refuse('DUPLICATE') end
            end
            if first.sector ~= sector then return refuse('DUPLICATE') end
            local dx, dy, dz = pose.x - first.pose.x, pose.y - first.pose.y, pose.z - first.pose.z
            local expected_x = slot > 2 and 3000 or 0
            local far_row = slot % 2 == 0
            local expected_z = far_row and 3000 or 0
            local yaw_direction = far_row and -1 or 1
            if not (math.abs(dx - expected_x) < 2 and math.abs(dy) < 2 and math.abs(dz - expected_z) < 3
                    and math.cos(first.pose.yaw) > .999 and yaw_direction * math.cos(pose.yaw) > .999
                    and math.cos(first.pose.pitch) > .999 and math.cos(pose.pitch) > .999) then
                return refuse(('POSE_%.2f_%.2f_%.2f_%.3f_%.3f'):format(dx,dy,dz,first.pose.yaw,pose.yaw))
            end
            if #indices == 4 and math.cos(pose.pitch - first.pose.pitch) <= .999 then
                return refuse('RELATIVE_PITCH')
            end
        end
        ships[slot] = {id=id, sector=sector, code=shipcode, pose=pose}
        codes[slot] = shipcode
        if slot < #indices then
            slot = slot + 1
            current = indices[slot]
            return
        end
        finished = true
        local kind = #indices == 2 and 'PAIR' or 'GROUP'
        log(kind .. '|' .. table.concat(codes, '|') .. (config.live_station and ('|' .. station_code .. '|' .. room) or ''))
    end
    for _, entry in ipairs({{'Create','Created'}, {'Inspect','Inspected'}}) do
        local which = entry[1]
        assert(RegisterEvent(screen .. '.' .. entry[2], function(_, row) receive(which, row) end) ~= false)
    end
    return function()
        if failure then error('company shuttle seed refused: ' .. failure) end
        if not guard() then refuse('PAUSE_OR_REGISTRY_LOST'); return false end
        if finished then return true end
        if GetCurRealTime() >= deadline then refuse('TIMEOUT_' .. action); return false end
        if not waiting then
            waiting = action
            local args = {nonce=config.nonce,epoch=config.epoch,index=current}
            if config.live_station then
                args.fresh, args.station, args.room = config.mode == 'prepare', config.station, config.room
                if action == 'Create' then
                    args.count = #indices
                    if config.boarding_transport == true then args.boarding_transport = true end
                end
            end
            if AddUITriggeredEvent(screen, action, args) == false then
                refuse('DISPATCH')
            end
        end
        return false
    end
end

X4LAN_COMPANY_SHUTTLE_PAIR = M
return M
