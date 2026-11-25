# Two provider poses with the original NPC captain

On 25 November 2026, one private native Linux provider on Bazzite loaded a
verified copy of checkpoint `1dac0e4056eb41739aeecd30dbb1f1ca` and
confirmed one stock undock from BIM-169 at game age 352.29 s. WRN-302 kept
the same assigned captain, PYN-740. The run used a private display and
isolated profile under revision `bb86cb88` with the developer trial
overlay at `2cfeda13`. The standard provider's pause, save and
checkpoint verification ran with its ordinary protections. [Trial code and verifier](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/2cfeda13df76b979b294fdbbc08086f5af35dddb)

The trial reserved registry index 7354 in
`cluster_01_sector001_macro`. Every operation checked the registry, the
same ship and captain, stock idle orders and the parked server avatar.
Two writes were permitted. A separate readback of the first target
and a fresh inspection of the ship were required before the second.
Both requests moved 50 m along sector X.

| Operation | Game age | Target in sector metres | Later native readbacks |
| --- | --- | --- | --- |
| First write | 355.107 s | (-168434.156, -3685.430, 46904.652) | 356.037 s, 358.883 s |
| Second write | 360.797 s | (-168384.156, -3685.430, 46904.652) | 361.741 s, 364.732 s |

The fresh inspection at 359.834 s matched the first target. Both native
MD/FFI readback pairs and the paused read at 365.194 s were within
0.000431 m of their respective targets.

## Saved checkpoint

The provider confirmed `checkpoint-39bf46000be4427e8d0d59898e06ef11`
with marker `7bf15f0bb48a08ea6e3019d79fea10e1` and save SHA-256
`48ef4719eddf1385dea4ccf2b0f4487cf5b77381b64fc5022f8d6b053008774b`.
The saved registry still identified Company ship WRN-302, undocked in
the same sector. Its saved AI-pilot post referenced the original PYN-740
with seed `2211373338772438693`. The avatar was still parked at XPJ-169.
The faction held 9,884.00 Cr and WRN-302 still carried two water.

The save's ship offset was zone-local. The verifier combined it with the
default zone origin from the pinned installed stock sectors map and
obtained (-168384.1565, -3685.430, 46904.65278125) m, 0.000928 m from
the second target. This used the original saved bytes. The original
source SHA-256 remained
`cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5`.

The provider shut down paused with no owned processes remaining and
released its temporary host lock. This was a local server trial.
Regular pilot admission, live pilot motion, Windows and WAN were
outside its scope.

The untouched generating case includes two extra trial cues and triggers
`source saved-state scripts differ` in ordinary recovery. A separate
private import archives the byte-verified generating cues alongside its
extension. The original supervisor result, saved bytes, registry and
sealed ledger were copied unchanged. The ordinary source guard verified
that import offline, and purchase and challenge rows match the source
ledger. The import's provenance retains the generating cue hashes.

## Evidence digests

| Artifact | SHA-256 |
| --- | --- |
| Native provider result | `35b4f52e98ee54487df41503f5da1a3b20b960143c9f7f50e5b9efa1d52c4489` |
| Verified save and pose evidence | `99e92169e110259a712c1160b2e68298b405e7a93db3b900a63a968bd7bf98c9` |
| Native engine log | `2eae66747fe8b0faceb066e3b479ab5d59bd716eab1f3076d72ac30e8a9abd57` |
| Private import provenance | `873ce70ab5cc14d2b1d371655218cbaa891f5b2eb1cb58c50cf9581b9204b2a0` |
| Stock sectors map used for the zone origin | `1a8d489782fd6d50be5ead86e3b1554bfb7ad4e6260aa7970fa81a6bdf82715b` |
