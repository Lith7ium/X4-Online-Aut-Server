# Two live Linux Company pilots in separate sectors

On 25 November 2026, a regular recovered provider and two regular native
Linux pilots ran X4 9.00 (611726) on one Bazzite host. Both clients used
pinned TLS on loopback, isolated base-game profiles and independent
cookie-authenticated RADV/Xwayland displays. The provider used its normal
private software Vulkan display. The product matched `8ff69b2d` in
checkout `d594da69`. The provider enabled `--company-motion` and
`--company-pose-trial` with the format-3 registry check.

The prior private split source had already placed HVC-420 in Black Hole
Sun V (`cluster_06_sector002_macro`) and KHI-630 in Black Hole Sun IV
(`cluster_06_sector001_macro`) through a provider transfer. The current
run used a verified copy of that marked save. It kept each pilot in the
original sector. The run covers concurrent flight in separate sectors.
Jump-gate and superhighway transit were outside its scope.

## Concurrent admitted flight

The ordinary clients verified control of registry indices 1073 and 1074
with distinct admitted generations. A private driver waited for neutral,
fresh source acknowledgements in both original sectors. Each pilot made
one 0.45 s stock Strafe Left input. Independent X11 receipts confirmed
both key releases. Each moving sample was acknowledged while the other
pilot still published from the other sector.

| Pilot and original sector | Moving sequence | Cockpit displacement, m | X velocity, m/s | Other pilot acknowledgement, capture-time separation |
| --- | ---: | ---: | ---: | --- |
| HVC-420, Sector002 | 148 | 3.818359 | −1.073816 | KHI sequence 31, 39.23 ms |
| KHI-630, Sector001 | 54 | 3.841797 | −0.746281 | HVC sequence 170, 133.48 ms |

The provider accepted 353 samples, including 350 with velocity. It verified
two ordinary stock position writes. It applied settled sequence 158 for HVC
and sequence 71 for KHI. Each paused UI position matched the applied
sample, with a separate Mission Director read within 0.000391 m. The
provider recorded zero transfers and zero application drops.

| Ship | Position in the saved sector, m | Saved position error from native paused UI, m |
| --- | --- | ---: |
| HVC-420, Sector002, zone BBS-493 | (20716.100, 100000, −1340) | 0.000391 |
| KHI-630, Sector001, zone YUD-806 | (31516.100, 115000, −3960) | 0.000391 |

## Confirmed checkpoint

Independent parsing of the marked gzip saves found the same registry
indices, ship identities and sectors in the source, both offers and final
checkpoint. It resolved both final positions from the zone-local saved
offsets. The provider paused and confirmed the last departure checkpoint.
Both clients and the provider exited successfully. No owned X4 process or
temporary host lock remained. The original source was byte-identical.
Available RAM stayed above 10.25 GiB and sampled full memory pressure was
zero. Peak PSS was 6.95 GiB for the provider, 3.31 GiB for HVC and
3.30 GiB for KHI.

| Checkpoint | ID | Marker | Save SHA-256 |
| --- | --- | --- | --- |
| Recovery source | `checkpoint-90a606d278ee438b9d2d3f9fc63be0c6` | `e33b325a1538eb7ccfcebe921e45ea74` | `56bceb78350c97f523db7088010d3dd3e85b48f081a10c8a5b8f92a234052c0b` |
| HVC offer | `checkpoint-771cd1678fc04ee082b51ac292a05916` | `ae857ffd6b88b995e813e9a97118dfdf` | `a04e0f9eb90da519f4992c6f4f338509cbc2d2c853eb6272bdccfbdc27da3c0c` |
| KHI offer | `checkpoint-250abb7cfad749788a237f7934c2c255` | `005a845e69f1bf0018f95a5b1de943d7` | `d7c74e54d4d9c0bf8a43f31a6c495c1d0d29038fb457d3aacd6e8025bd42913b` |
| Last departure | `checkpoint-d86e79511c56406fa953cb9dc1dd1689` | `7fedb87739cd5e4789c3b1fb2b1d82c9` | `1259bc163696c9d810d289d518f0b5916a77f1e5862188767cb84edfad1e54e7` |

## Optional stock map

The supervisor created the private map marker after both flights and the
pilot logs recorded no stock focus request or map receipt. The installed
private map trigger attempted to read the marker with `io.open`.
X4's UI Lua has no `io` library, as recorded by the native map visibility
probe. The missing stage preceded the read-only stock actor lookup and
map open. Both cockpit observers recorded zero samples across the
sector split. The optional map attempt provides no conclusion about
native map selection or graphical movement.

A separate private fixture uses the previously native-tested relative
`loadfile` mechanism for the one-shot arm. Its LuaJIT test passed with
`io` absent and a locally produced fixed marker. The correction has
only offline evidence and was prepared after this native run.

## Evidence digests

| Private artifact | SHA-256 |
| --- | --- |
| Concurrent flight proof | `65421f20a4285feb043a5cfae481c1fa6f8497df8975cf78b1e9b2bc109c59d8` |
| Provider result | `b51c1f8f9bbcad99959861b426bd61ed4b9d72554d79b38e4a07fece2f099b9a` |
| Independent saved-byte comparison | `b581666534be95aed2e84a63ff7843d3326bbfe2ecbdb3f60c7c1a5b28a2b2c3` |
| Supervisor result | `f0a0cfb475a9205d3d1c1f6606e8ffb3afc11f329093f09b74fab82efd3cd5f4` |
| PSS and host memory | `d87680f69399ed631284b61f618a7dbfc2217d9dc8e5722af35314c5719bbd34` |
| Original bound HVC Lua with map arm | `af1bc2012030b40baeac7b672171b2eeba923c05ecafd6d2e2d12bd85e866616` |
