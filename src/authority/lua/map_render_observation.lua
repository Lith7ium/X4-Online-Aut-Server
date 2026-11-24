-- Opt-in, local read-only snapshot of the active map renderer's raw component list.
-- This does not claim that the native list is viewport- or radar-filtered.
local M = {}

local DEFAULT_MAX_COMPONENTS = 65536
local MAX_ALLOWED_COMPONENTS = 1000000

local function valid_limit(value)
	return type(value) == 'number' and value == math.floor(value) and
		value >= 1 and value <= MAX_ALLOWED_COMPONENTS
end

local function valid_count(value, limit)
	return type(value) == 'number' and value == math.floor(value) and
		value >= 0 and value <= limit
end

local function same_map(get_map, expected_menu, expected_handle_key, handle_key)
	local ok, menu, handle = pcall(get_map)
	if not ok or menu ~= expected_menu or handle == nil then
		return false
	end
	local key_ok, key = pcall(handle_key, handle)
	return key_ok and key == expected_handle_key
end

function M.new(options)
	if type(options) ~= 'table' then
		return nil, 'invalid_options'
	end
	local max_components = options.max_components or DEFAULT_MAX_COMPONENTS
	if not valid_limit(max_components) then
		return nil, 'invalid_limit'
	end
	for _, name in ipairs({'get_map', 'get_count', 'get_components', 'handle_key', 'component_id'}) do
		if type(options[name]) ~= 'function' then
			return nil, 'missing_' .. name
		end
	end

	local sampler = {}
	function sampler:sample()
		local ok, menu, handle = pcall(options.get_map)
		if not ok then
			return nil, 'map_unavailable'
		end
		if menu == nil or handle == nil then
			return {map_open = false, component_ids = {}, raw_count = 0}
		end

		local handle_ok, original_handle_key = pcall(options.handle_key, handle)
		if not handle_ok or type(original_handle_key) ~= 'string' or original_handle_key == '' then
			return nil, 'invalid_map_handle'
		end

		local count_ok, raw_count = pcall(options.get_count, handle)
		if not count_ok or not valid_count(raw_count, max_components) then
			return nil, 'invalid_component_count'
		end
		if not same_map(options.get_map, menu, original_handle_key, options.handle_key) then
			return nil, 'map_changed'
		end

		local ids = {}
		local read_ok, components, filled_count = pcall(options.get_components, handle, raw_count)
		if not read_ok or type(components) ~= 'table' or filled_count ~= raw_count then
			return nil, 'component_list_changed'
		end
		for index = 1, raw_count do
			local id_ok, component_id = pcall(options.component_id, components[index])
			if not id_ok or type(component_id) ~= 'string' or
				component_id:match('^%d+$') == nil or #component_id > 20 then
				return nil, 'invalid_component_id'
			end
			ids[#ids + 1] = component_id
		end
		if not same_map(options.get_map, menu, original_handle_key, options.handle_key) then
			return nil, 'map_changed'
		end

		local final_count_ok, final_count = pcall(options.get_count, handle)
		if not final_count_ok or final_count ~= raw_count or
			not same_map(options.get_map, menu, original_handle_key, options.handle_key) then
			return nil, 'map_changed'
		end

		return {map_open = true, component_ids = ids, raw_count = raw_count}
	end
	return sampler
end

local function native_access()
	local ffi = require('ffi')
	if not pcall(ffi.typeof, 'UniverseID') then
		ffi.cdef('typedef uint64_t UniverseID;')
	end
	ffi.cdef[[
		uint32_t GetNumMapRenderedComponents(UniverseID);
		uint32_t GetMapRenderedComponents(UniverseID*, uint32_t, UniverseID);
		uint32_t GetNumMapSelectedComponents(UniverseID);
		uint32_t GetMapSelectedComponents(UniverseID*, uint32_t, UniverseID);
	]]
	local native = ffi.C

	local function active_map()
		if type(_G.Menus) ~= 'table' then
			return nil
		end
		for _, menu in ipairs(_G.Menus) do
			if type(menu) == 'table' and menu.name == 'MapMenu' and menu.shown == true then
				local handle = menu.holomap
				if handle == nil then
					return nil
				end
				local map_id = ffi.cast('UniverseID', handle)
				if map_id == 0 then
					return nil
				end
				return menu, map_id
			end
		end
		return nil
	end

	local function decimal_id(value)
		return tostring(ffi.cast('UniverseID', value)):gsub('ULL$', '')
	end

	return ffi, native, active_map, decimal_id
end

function M.new_x4(options)
	if type(options) ~= 'table' or options.enabled ~= true then
		return nil, 'disabled'
	end
	local max_components = options.max_components or DEFAULT_MAX_COMPONENTS
	if not valid_limit(max_components) then
		return nil, 'invalid_limit'
	end

	local ffi, native, active_map, decimal_id = native_access()

	return M.new({
		max_components = max_components,
		get_map = active_map,
		handle_key = decimal_id,
		get_count = function(map_id)
			return tonumber(native.GetNumMapRenderedComponents(map_id))
		end,
		get_components = function(map_id, count)
			local buffer = ffi.new('UniverseID[?]', math.max(count, 1))
			local filled_count = tonumber(native.GetMapRenderedComponents(buffer, count, map_id))
			local components = {}
			for index = 0, math.min(filled_count, count) - 1 do
				components[#components + 1] = buffer[index]
			end
			return components, filled_count
		end,
		component_id = decimal_id,
	})
end

-- One explicitly selected component must also be present in the same raw
-- renderer list. Recheck the map handle, selection and count after enumeration.
function M.new_selected_x4()
	local ffi, native, active_map, decimal_id = native_access()
	local selected_buffer = ffi.new('UniverseID[1]')
	local rendered_buffer, capacity
	local function selection(map_id)
		if native.GetNumMapSelectedComponents(map_id) ~= 1 or
			native.GetMapSelectedComponents(selected_buffer, 1, map_id) ~= 1 or selected_buffer[0] == 0 then
			return nil
		end
		return selected_buffer[0]
	end

	local sampler = {}
	function sampler:sample()
		local menu, map_id = active_map()
		if not map_id then return nil end
		local selected = selection(map_id)
		if not selected then return nil end
		local count = tonumber(native.GetNumMapRenderedComponents(map_id))
		if not valid_count(count, DEFAULT_MAX_COMPONENTS) or count == 0 then return nil end
		if not capacity or count > capacity then
			capacity = count
			rendered_buffer = ffi.new('UniverseID[?]', capacity)
		end
		if native.GetMapRenderedComponents(rendered_buffer, count, map_id) ~= count then return nil end
		local found = false
		for index = 0, count - 1 do
			if rendered_buffer[index] == selected then
				found = true
				break
			end
		end
		local map_key = decimal_id(map_id)
		if not found or not same_map(active_map, menu, map_key, decimal_id)
			or selection(map_id) ~= selected or native.GetNumMapSelectedComponents(map_id) ~= 1
			or native.GetNumMapRenderedComponents(map_id) ~= count
			or not same_map(active_map, menu, map_key, decimal_id) then return nil end
		return selected
	end
	return sampler
end

X4LAN_MAP_RENDER_OBSERVATION = M
return M
