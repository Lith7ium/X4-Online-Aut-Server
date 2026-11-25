# Two live Linux Company pilots with reciprocal cockpit movement

On 25 November 2026, two regular Linux pilots joined one regular recovered
provider on a single Bazzite host through pinned TLS on loopback. Each
pilot used the explicit reciprocal cockpit-playback index. Both receiving
cockpits measured the other ship moving through their stock radar view.
The provider verified a separate native stock position write for each
ship and saved both after the pilots left.

The tracked product code matched `8ff69b2d` in checkout `4b570958`.
All three licensed X4 9.00 (611726) instances ran base-game-only with
separate private profiles. The provider used a private 800 × 600 Xvfb
display with Mesa software Vulkan. The pilots used distinct private,
cookie-authenticated Xwayland displays and AMD RADV. The provider enabled
`--company-motion --company-pose-trial` and required a format-3 registry
snapshot.

## Genuine stock input and receiving readbacks

Both ships began 3,000 m apart at X = 20,000 m in
`cluster_06_sector002_macro`. The private driver waited for verified local
helm, independent pilot generations, reciprocal native `SEEN` receipts
and read-only local pose observations. It then delivered one 0.30 s
stock Strafe Left input per pilot, in sequence, and independently
confirmed each key release. The private Lua witness logged the existing
pre-write observation and chained the ordinary logger unchanged.

Each row links the source acknowledgement, validated receiving
`SEEN` and a later native post-write readback for the same ship,
sector, generation, receiving actor and view serial. The source and
receiver positions have independent baselines. No alignment contributed
to either measured displacement.

| Sender → receiving cockpit | Sequence | X velocity, m/s | Sender displacement, m | Receiver displacement, m | Time after receiver write, ms | Receiver error vs playback target, m | Receiver error vs source, m |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HVC-420 (1073) → KHI-630 (1074) | 153 | −0.718205 | 1.339844 | 1.365234 | 14.367 | 0.000342 | 0.025391 |
| KHI-630 (1074) → HVC-420 (1073) | 46 | +0.588042 | 1.343750 | 1.304688 | 19.768 | 0.000536 | 0.039063 |

The first attempt established both regular controls and both native
`SEEN` directions with zero input. Its private start condition incorrectly
required a post-write readback before motion. The production controller
performs a read-only observation and skips a write when the displacement
is zero. The corrected attempt started from a fresh copy of the original
recovery source and used the observation to gate input. The earlier
Linux native replay used a different log tag. The exact current logger
was checked offline for the stationary and later-write cases before
the corrected start.

## Provider and checkpoint

The provider accepted 134 samples, including 131 with velocity. It verified
two same-sector stock position writes and paused with both ships at the
applied targets. It applied HVC sequence 153 and the settled KHI sequence
51. One later HVC application record expired during the departure
transition. HVC's paused readback matched the applied target.

| Ship | Paused X4 UI X, m | Paused Mission Director X, m | Sector Y, m | Sector Z, m |
| --- | ---: | ---: | ---: | ---: |
| HVC-420 | 19998.66015625 | 19998.660 | 100000 | -1500 |
| KHI-630 | 20001.392578125 | 20001.393 | 100000 | +1500 |

| Checkpoint | Marker | Save SHA-256 |
| --- | --- | --- |
| Original source `checkpoint-6e5948ba473f4b399c01fcac9ba2f7bc` | `4cb79f3a13e9374d6b9ddbc44752c8d7` | `1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958` |
| HVC offer `checkpoint-538121389e6943debbb9f06b9ccc8be4` | `b552862b617b365fe2c1722272d54354` | `6d08fc5880bce2c8d7e63dc9cd375de6cbd2cb32fca4e64f313c437d387ed644` |
| KHI offer `checkpoint-c29928cee194425a9e425f99bdd429bc` | `1159da1fe7517dde344c01e0ed07c730` | `523135d4dac00112fbb76edc2af5c9b3224aea1ca16df4ae12ed870986d50c5c` |
| Last departure `checkpoint-d3c8157a612a4b079bb347728cd962ce` | `c45387ae8b642a5fced66daabb51eebd` | `a97cf1bdef73b4805d467d484d6ae9bbb189cccc9c4bb9822130b3e7951ff7ee` |

Independent parsing of the marked gzip saves verified the format-3 ship
identities and resolved the saved zone-local offsets in BBS-493. The
source and both offers had the same positions. The final sector X
values were 19998.660 m for HVC and 20001.393 m for KHI, within 0.000157 m
and 0.000422 m of the paused UI targets. Both registry indices, both
ship codes and their sector were preserved.

The host retained at least 11.02 GiB of available RAM. Peak process PSS
was 6.87 GiB for the provider, 3.24 GiB for HVC, and 3.20 GiB for KHI.
Full memory pressure remained at zero in the sampled 10-second window.
Both pilots and the provider exited successfully. Their owned processes
and the temporary host lock were gone. The original source remained
byte-identical. This is native Linux loopback evidence for short
same-sector movement. WAN, Windows, rotation, combat and extended
flight were outside this run's scope.

## Evidence digests

| Private artifact | SHA-256 |
| --- | --- |
| Verified directions | `dd68d937759c9fbc8494d492c5e8eb4f36897c8f17274626661046bf9f647749` |
| Provider result | `bf544a37de5b43ca3881d42e0277f9524d4ecb2c4f76f041ba20cfd530d8c4e1` |
| Supervisor result | `3dfa9026fc43f9c25a537c7014fcb5b397a1319062aef4882b52538585520485` |
| Proportional memory sampling | `f2d65c10f26588e8891dde8e3ce9f434b7a2f65d36e116b657b711926d3a953a` |
| Independent save and log verification | `6855964c4d0547618d7a14045eede1c79f22171483a5760008d77542c8bf8493` |
