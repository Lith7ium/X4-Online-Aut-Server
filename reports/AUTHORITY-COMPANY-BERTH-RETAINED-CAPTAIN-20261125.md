# Native berth adoption with the original Company captain

A private Bazzite X4 9.00 provider refused to place WRN-302 while its
existing stock trade was active. After stock undock physically cleared
the original bay, the reviewed cue placed the same ship at that exact
BIM-169 berth with Company captain PYN-740 still assigned and at the
helm. A separate native query confirmed the bay's sole occupant and
the original ship and captain.

The installed berth cue matched Git revision
`09c56284bd0b13546776b725519800c80d0cd866` byte for byte. The provider
used private Xvfb and Mesa software Vulkan. It loaded a disposable copy
of confirmed checkpoint `checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca`
and verified the saved registry.

| X4 game age (s) | Native evidence |
| ---: | --- |
| 322.00 | WRN-302 was still docked and running a critical `TradePerform` order. PYN-740 was aboard and held the helm. |
| 322.41 | The berth cue returned `ship_busy` with route `none`. The next native inspection still found the original bay. |
| 333.56 | The stock undock event matched the original ship, station and bay. |
| 353.56 | The physical dock, assigned dock and parking references had cleared. The old bay was empty, unassigned and back in its default state. |
| 355.98 | Adoption returned `adopted` by the `exact` route at BIM-169, Grand Exchange I. Requested and actual bay seed were both `507326418098681806`. |
| 356.381 | Independent MD readback found WRN-302 as the sole occupant of that external trading bay. The ship and captain runtime references matched those from before the refusal and after undock. The original pilot was still assigned, aboard, idle and at the helm. |

The supervisor paused the world and shut down with no remaining owned
processes. The original and copied input save hashes matched
`cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5`.
There was no published checkpoint or new trade order from the trial.
This run covers the native cue in a single private provider and does
not establish player transport or quote settlement after adoption.

Private result-file SHA-256
`1680132e3256b5c6a647567d86f38cafbb821a0feb250b6292663eaec71059c2`.
Private engine-log SHA-256
`da6b71d44b1f74dba00074b84fbad7eea2ca6f186a7034a722dce7f0b1b2252b`.
