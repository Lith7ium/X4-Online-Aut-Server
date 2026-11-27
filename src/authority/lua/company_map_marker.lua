-- Private Company marker experiment. Projection and non-interactive map overlay.
local M = {}
local calibration_error_px, marker_size = 2, 18
-- Marker-only receipt expiry for this trial. The cockpit predictor owns its own clock.
local max_age_seconds = 1
local camera_keys = { "x", "y", "z", "yaw", "pitch", "roll", "distance" }
local viewport_keys = { "x", "y", "width", "height" }

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function same_fields(a, b, keys)
    if type(a) ~= "table" or type(b) ~= "table" then return false end
    for _, key in ipairs(keys) do
        if not finite(a[key]) or a[key] ~= b[key] then return false end
    end
    return true
end

local function copy_fields(source, keys)
    local copy = {}
    for _, key in ipairs(keys) do copy[key] = source[key] end
    return copy
end

local function valid_view(view)
    return view and view.menu and view.menu.name == "MapMenu" and view.menu.shown == true
        and view.holomap and view.holomap ~= 0 and view.menu.holomap == view.holomap
        and view.sector and finite(view.height) and view.fixed_top_down == true
        and finite(view.ui_scale) and view.ui_scale > 0
        and same_fields(view.camera, view.camera, camera_keys)
        and same_fields(view.viewport, view.viewport, viewport_keys)
        and view.viewport.width >= marker_size and view.viewport.height >= marker_size
end

local function same_view(view, calibrated)
    return valid_view(view) and view.menu == calibrated.menu and view.holomap == calibrated.holomap
        and view.sector == calibrated.sector and view.height == calibrated.height and view.ui_scale == calibrated.ui_scale
        and same_fields(view.camera, calibrated.camera, camera_keys)
        and same_fields(view.viewport, calibrated.viewport, viewport_keys)
end

local function solve(rows)
    for column = 1, 8 do
        local pivot = column
        for row = column + 1, 8 do
            if math.abs(rows[row][column]) > math.abs(rows[pivot][column]) then pivot = row end
        end
        if math.abs(rows[pivot][column]) < 1e-9 then return nil end
        rows[column], rows[pivot] = rows[pivot], rows[column]
        local equation = rows[column]
        local divisor = equation[column]
        for i = column, 9 do equation[i] = equation[i] / divisor end
        for row = 1, 8 do
            if row ~= column then
                local factor = rows[row][column]
                for i = column, 9 do rows[row][i] = rows[row][i] - factor * equation[i] end
            end
        end
    end
    local result = {}
    for i = 1, 8 do result[i] = rows[i][9] end
    return result
end

local function project(calibration, sector_x, sector_z)
    if not finite(sector_x) or not finite(sector_z) then return nil end
    local x = (sector_x - calibration.origin_x) / calibration.meters
    local z = (sector_z - calibration.origin_z) / calibration.meters
    local h = calibration.h
    local denominator = h[7] * x + h[8] * z + 1
    if not finite(denominator) or denominator < 1e-9 then return nil end
    local u = (h[1] * x + h[2] * z + h[3]) / denominator
    local v = (h[4] * x + h[5] * z + h[6]) / denominator
    if not finite(u) or not finite(v) then return nil end
    local viewport = calibration.viewport
    return viewport.x + u * viewport.width, viewport.y + v * viewport.height
end

local function valid_pick(pick, viewport)
    return type(pick) == "table" and finite(pick.x) and finite(pick.z)
        and finite(pick.px) and finite(pick.py)
        and viewport.x <= pick.px and pick.px < viewport.x + viewport.width
        and viewport.y <= pick.py and pick.py < viewport.y + viewport.height
end

function M.calibrate(view, fit, held_out)
    if not valid_view(view) then return nil, "view" end
    local viewport = view.viewport
    if type(fit) ~= "table" or #fit ~= 4 or not valid_pick(held_out, viewport) then
        return nil, "picks"
    end
    for _, point in ipairs(fit) do
        if not valid_pick(point, viewport) then return nil, "picks" end
        if point.x == held_out.x and point.z == held_out.z then return nil, "held-out" end
    end
    local origin_x, origin_z = fit[1].x, fit[1].z
    local meters = 0
    for _, point in ipairs(fit) do
        meters = math.max(meters, math.abs(point.x - origin_x), math.abs(point.z - origin_z))
    end
    if meters == 0 or not finite(meters) then return nil, "degenerate" end

    local equations = {}
    for _, point in ipairs(fit) do
        local x, z = (point.x - origin_x) / meters, (point.z - origin_z) / meters
        local u, v = (point.px - viewport.x) / viewport.width, (point.py - viewport.y) / viewport.height
        equations[#equations + 1] = { x, z, 1, 0, 0, 0, -u * x, -u * z, u }
        equations[#equations + 1] = { 0, 0, 0, x, z, 1, -v * x, -v * z, v }
    end
    local matrix = solve(equations)
    if not matrix then return nil, "degenerate" end
    local calibration = {
        menu = view.menu, holomap = view.holomap, sector = view.sector, height = view.height,
        ui_scale = view.ui_scale, camera = copy_fields(view.camera, camera_keys),
        viewport = copy_fields(viewport, viewport_keys),
        origin_x = origin_x, origin_z = origin_z, meters = meters, h = matrix,
    }
    local px, py = project(calibration, held_out.x, held_out.z)
    if not px then return nil, "held-out" end
    local error_px = math.sqrt((px - held_out.px)^2 + (py - held_out.py)^2)
    if error_px > calibration_error_px then return nil, "held-out", error_px end
    return calibration, nil, error_px
end

local function renderer(options, calibration)
    local helper, layer, layer_available = options.helper, options.layer, options.layer_available
    local previous_x, previous_y, owned_frame, registration_failed
    local function owns_layer()
        local frames = calibration.menu.frames
        return owned_frame ~= nil and frames ~= nil and frames[layer] == owned_frame
    end
    local function hide()
        if layer_available(calibration.menu, layer) and owns_layer() then helper.clearFrame(calibration.menu, layer) end
        previous_x, previous_y, owned_frame = nil, nil, nil
        return false
    end

    local function update(view, sample, now)
        if not layer_available(calibration.menu, layer) or not same_view(view, calibration)
            or not sample or sample.y ~= view.height
            or not finite(sample.received_at) or not finite(now)
            or now - sample.received_at < 0 or now - sample.received_at >= max_age_seconds then
            return hide()
        end
        local px, py = project(calibration, sample.x, sample.z)
        if not px then return hide() end
        local x, y = math.floor(px - marker_size / 2 + 0.5), math.floor(py - marker_size / 2 + 0.5)
        local viewport = calibration.viewport
        if x < viewport.x or y < viewport.y or x + marker_size > viewport.x + viewport.width
            or y + marker_size > viewport.y + viewport.height then
            return hide()
        end
        if previous_x ~= nil and not owns_layer() then return hide() end
        if x == previous_x and y == previous_y then return true end
        if previous_x == nil and (registration_failed
            or (view.menu.frames and view.menu.frames[layer]) or not options.can_allocate()) then return false end

        local frame = helper.createFrameHandle(view.menu, {
            layer = layer, x = x, y = y, width = marker_size, height = marker_size,
            scaling = false, standardButtons = {}, enableDefaultInteractions = false,
            closeOnUnhandledClick = false, exclusiveInteractions = false,
            blurBackground = false, startAnimation = false,
            overlay = {
                icon = "widget_arrow_right_01", width = marker_size, height = marker_size,
                color = { r = 40, g = 235, b = 245, a = 100 }, glowfactor = 0,
            },
        })
        frame:display()
        owned_frame = frame.id
        if not owns_layer() then
            registration_failed = true
            return hide()
        end
        previous_x, previous_y = x, y
        return true
    end

    return { update = update, hide = hide }
end

-- The stream has already matched the selection and echoed SEEN to the local
-- launcher. This consumer pins one generation per selection serial.
function M.new(options)
    local serial, generation, sequence, withdrawn, sample, calibrated, overlay, next_fit
    local function hide()
        if overlay then overlay.hide() end
        calibrated, overlay = nil, nil
    end

    local function select(selected_serial)
        if serial ~= selected_serial then
            serial, generation, sequence, withdrawn, sample = selected_serial, nil, nil, false, nil
            hide()
        end
    end

    local api = {}
    function api:sample(selected_serial, accepted, arrival)
        select(selected_serial)
        if withdrawn or (sequence and accepted.sequence <= sequence) then return end
        if generation and generation ~= accepted.generation then return end
        generation, sequence = accepted.generation, accepted.sequence
        accepted.received_at = arrival
        sample = accepted
    end

    function api:tick(binding, selected_serial, now)
        select(selected_serial)
        if not binding then withdrawn, sample = true, nil end
        if not sample or now < sample.received_at or now - sample.received_at >= max_age_seconds then
            sample = nil
            hide()
            return
        end
        local view = options.view(binding, sample.y)
        if not valid_view(view) or not options.layer_available(view.menu, options.layer) then
            sample = nil
            hide()
            return
        end
        if not same_view(view, calibrated or {}) then
            hide()
            if next_fit and now < next_fit then return end
            next_fit = now + 0.1
            calibrated = options.calibrate(view)
            if not calibrated then
                sample = nil
                return
            end
            overlay = renderer(options, calibrated)
        end
        overlay.update(view, sample, now)
    end

    function api:end_track(selected_serial, ended_generation)
        select(selected_serial)
        if not generation or generation == ended_generation then
            withdrawn, sample = true, nil
            hide()
        end
    end

    function api:close()
        sample = nil
        hide()
    end
    return api
end

function M.new_x4()
    local ffi = require('ffi')
    local function declare_type(name, declaration)
        if not pcall(ffi.typeof, name) then ffi.cdef(declaration) end
    end
    declare_type('UniverseID', 'typedef uint64_t UniverseID;')
    declare_type('UIPosRot', 'typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;')
    declare_type('HoloMapState', 'typedef struct {UIPosRot offset; float cameradistance;} HoloMapState;')
    ffi.cdef[[
        void GetMapState(UniverseID, HoloMapState*);
        UniverseID GetMapPositionOnEcliptic2(UniverseID, UIPosRot*, bool, UniverseID, UIPosRot);
        void SetMapRelativeMousePosition(UniverseID, bool, float, float);
        UniverseID GetContextByClass(UniverseID, const char*, bool);
        bool IsComponentClass(UniverseID, const char*);
        float GetUIScale(bool);
    ]]
    local C, helper, layer = ffi.C, assert(Helper), 3
    local resolve = assert(X4LAN_RESOLVE_STRING)
    local camera = ffi.new('HoloMapState[1]')
    local point, offset, zero = ffi.new('UIPosRot[1]'), ffi.new('UIPosRot'), ffi.new('UIPosRot')
    local function layer_available(menu, slot)
        for _, other in ipairs(Menus or {}) do
            if other ~= menu and type(other) == 'table' and type(other.frames) == 'table' and other.frames[slot] then return false end
        end
        return true
    end

    local function can_allocate()
        local frames = {}
        for _, menu in ipairs(Menus or {}) do
            if type(menu) == 'table' and type(menu.frames) == 'table' then
                for _, frame in pairs(menu.frames) do
                    if IsValidWidgetElement(frame) then frames[frame] = true end
                end
            end
        end
        local count = 0
        for _ in pairs(frames) do count = count + 1 end
        return count < 5
    end

    local function view_at(sector, height)
        if helper.minimizedMenu then return end -- Helper.display would otherwise dismiss it.
        local active
        for _, menu in ipairs(Menus or {}) do
            if type(menu) == 'table' and menu.name == 'MapMenu' and menu.shown == true then
                if active then return end
                active = menu
            end
        end
        if not active or active.picking ~= true or not active.map or not active.holomap
            or active.holomap == 0 or not active.frameData or not active.frameData[6] then return end
        local mx, my = GetRenderTargetMousePosition(active.map)
        if not finite(mx) or not finite(my) or math.abs(mx) >= 1 or math.abs(my) >= 1 then return end
        if C.GetMapPositionOnEcliptic2(active.holomap, point, false, 0, zero) ~= sector then return end
        local width, height_px = GetSize(active.map)
        local x, y = GetOffset(active.map)
        C.GetMapState(active.holomap, camera)
        local state, base = camera[0], active.frameData[6]
        local pos = state.offset
        return {
            menu=active, holomap=active.holomap, sector=sector, height=height,
            fixed_top_down=math.abs(pos.pitch + math.pi / 2) <= .05 and math.abs(pos.roll) <= .05,
            ui_scale=C.GetUIScale(false), mouse_x=mx, mouse_y=my,
            viewport={x=x + base.x, y=y + base.y, width=width, height=height_px},
            camera={x=pos.x, y=pos.y, z=pos.z, yaw=pos.yaw, pitch=pos.pitch, roll=pos.roll,
                distance=state.cameradistance},
        }
    end

    local function calibrate(view)
        local mx, my, viewport = view.mouse_x, view.mouse_y, view.viewport
        local sector = C.GetMapPositionOnEcliptic2(view.holomap, point, false, 0, zero)
        if sector ~= view.sector then return end
        local stock_x, stock_y, stock_z = point[0].x, point[0].y, point[0].z
        offset.x, offset.y, offset.z = stock_x, view.height, stock_z
        local function observe(x, y)
            if C.GetMapPositionOnEcliptic2(view.holomap, point, true, sector, offset) ~= sector
                or math.abs(point[0].y - view.height) >= .1 then return end
            return {x=point[0].x, z=point[0].z, px=viewport.x + (1 + x) * viewport.width / 2,
                py=viewport.y + (1 - y) * viewport.height / 2}
        end
        local held_out = observe(mx, my)
        if not held_out then return end
        local fit = {}
        local ok = pcall(function()
            for _, pair in ipairs({{-.2,.2}, {.2,.2}, {.2,-.2}, {-.2,-.2}}) do
                C.SetMapRelativeMousePosition(view.holomap, true, pair[1], pair[2])
                fit[#fit + 1] = assert(observe(pair[1], pair[2]))
            end
        end)
        C.SetMapRelativeMousePosition(view.holomap, true, mx, my)
        local restored = observe(mx, my)
        if not ok or not restored or restored.x ~= held_out.x or restored.z ~= held_out.z
            or C.GetMapPositionOnEcliptic2(view.holomap, point, false, 0, zero) ~= sector
            or point[0].x ~= stock_x or point[0].y ~= stock_y or point[0].z ~= stock_z
            or not same_view(view_at(sector, view.height), view) then return end
        return M.calibrate(view, fit, held_out)
    end

    return M.new({
        layer=layer, helper=helper, layer_available=layer_available, can_allocate=can_allocate, calibrate=calibrate,
        view=function(binding, height)
            local actor = resolve.parse_hex(binding.actor)
            local sector = actor and C.GetContextByClass(actor, 'sector', false)
            if sector and sector ~= 0 and C.IsComponentClass(sector, 'sector') then
                return view_at(sector, height)
            end
        end,
    })
end

X4LAN_COMPANY_MAP_MARKER = M
return M
