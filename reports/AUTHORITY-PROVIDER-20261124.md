# GPU-free Linux provider trial, 24 November 2026

## Package and host

The tested runtime archive was built from `aed451c5048121c8de47999d9cd4f71144c6ab78`.
It is 1,216,103 bytes, SHA-256
`ddca047cf997ca6f8b5497804552bdf3dda8a275d3d72e5ca6ec195221e23ecd`.
The separate private format-3 seed is 26,683,429 bytes, SHA-256
`cbf49d5e0dbb49ee1f55bd6ec00b78480a326b7c422e35bde5e894f68a19f68b`.
Keep both Steam data and credentials on the operator's host. The licensed Linux
9.00 installation and eight official extensions used 34,930,397,255 bytes
according to its Steam manifest (build 23660954).

One Ryzen 7 7800X3D desktop hosted the trial. The server, Xvfb and native X4
inherited an affinity of four distinct physical cores, one hardware thread per
core. The host also ran Steam. The game namespace had no graphics device nodes.
Vulkan enumerated a single CPU device and the X4 engine log independently
identified Mesa llvmpipe (LLVM 23.1.5). The stock renderer used the private
800 × 600 Xvfb display. The provider package uses no renderer-suppression patch.

## Native server and one simulated participant

The server mounted the licensed game read-only and loaded the portable world's
marker. It inspected all 10,082 issued registry entries and published its TLS
connection file 272.9 seconds after launch. Both the control and file backends
passed the pinned TLS and shared-key check. A client-side tunnel from the
extracted package forwarded local probe sockets to that gateway. All sockets
in this trial were on the same host.

The simulated participant requested the first checkpoint, downloaded and
verified 26,351,820 bytes, simulated its own X4 load and supplied participant
receipts. The native server resumed. Approximately 266 seconds elapsed from
the join request to the start of the observation window, including the first
save, transfer, verification and probe admission. The client held its session
for 60.000 seconds. Game time in the first and final X4 saves advanced from
24.437 to 85.465 seconds. After disconnect the server paused, saved and
verified its final checkpoint in approximately 226 seconds.

The server recorded one admitted connection, two verified saves and a paused
final state. CURRENT and CONFIRMED agreed. The final checkpoint was
`checkpoint-34d4d67ad1104656b6d3b66719da56d7`,
SHA-256 `0472ae62375afc12fd776d4cbd0d8b00e8ddeb84b11102fbee87fd12c6c56c71`.
The final capture inspected 10,082 entries, found 10,077 bound and 10,022
operational. The server exited successfully with no owned processes left.

## Cold restart from the last disconnect

A second native X4 process used the completed session's checkpoint store on
the same four cores. This start used the ordinary loopback deployment, no TLS
flags, and automatic discovery of the software Vulkan ICD. The supervisor
checked the source checkpoint ID, SHA-256 and registry receipt from the first
run. X4 reported the saved marker, a paused world at 85.47 game seconds and
the same live counts across all 10,082 entries. The local endpoint became
ready in 272 seconds. The second server shut down cleanly with zero admitted
clients and zero owned processes left. Both runs removed their endpoint files.

## Resource measurements

| Observation | X4 CPU, cores | Server process tree CPU, cores |
| --- | ---: | ---: |
| Paused, ready, no participant (18 seconds) | 3.63 | 3.70 |
| Resumed with one simulated participant (48 seconds in the middle of the hold) | 3.65 | 3.72 |

CPU figures are process CPU seconds per elapsed second. X4 alone reached
8.51 GiB resident high-water mark. The highest sampled sum of RSS in the
service process tree was 8.60 GiB. RSS sums can double-count shared pages.
These figures exclude Steam and OS services. The test did not apply a RAM
limit. The previous GPU-free experiment used a different renderer path.

Four dedicated fast cores carried this small active session. For a first
provider allocation, six fast dedicated cores, 16 GiB RAM and 60 GB of SSD
give test headroom. Record CPU model, sharing, host load and allotted memory.
The full registry checks currently take minutes on this configuration. The
workload did not exercise live combat, boarding, real clients, crowded
sectors or sustained late-game load.

X4 logged script-list import warnings while loading the seed, an unavailable
FSR3 queue on llvmpipe, and a read-only Steam shader-cache warning. Their
gameplay impact has not been measured.
The inspected engine log had no vertex-buffer failure of the kind recorded
in the earlier renderer-suppression experiment.

## Test scope

69 focused deadline, registry, bridge and supervisor tests passed. A real
clean-archive regression also exercised the standalone seed command and
the gated child-process entrypoint. An earlier strict Linux suite on the
integration base ran 5,400 tests and reported one failure and 26 skips.
The scheduler failure was reproduced as a race in its test responder and
fixed. Both scheduler tests and ten repetitions of the failing case then
passed. The strict suite has no complete passing result on this revision.
Windows-specific checks and separate reference fixtures were among the skips.
The GPU-free evidence is a native local server test. A provider deployment,
an Internet connection from Windows and real multiplayer gameplay are open.
