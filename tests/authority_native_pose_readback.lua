-- Model the units returned by GetObjectPositionInSector after SetObjectSectorPos.
return function(written)
    return {
        x = written.x, y = written.y, z = written.z,
        yaw = math.rad(written.yaw), pitch = math.rad(written.pitch), roll = math.rad(written.roll),
    }
end
