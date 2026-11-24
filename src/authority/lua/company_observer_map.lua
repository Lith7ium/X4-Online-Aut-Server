-- Read-only Company binding for one selection in the shown map's raw render list.
local M = {}

function M.new_x4(pinned, own_index, registry_ready)
    local sampler = X4LAN_MAP_RENDER_OBSERVATION.new_selected_x4()
    return X4LAN_COMPANY_OBSERVER_TARGET.new_map_x4(pinned, own_index, registry_ready,
        function() return sampler:sample() end)
end

X4LAN_COMPANY_OBSERVER_MAP = M
return M
