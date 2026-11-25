# Bounded GPU-free X4 scheduler experiment

17 November 2026. Internal, single-instance Linux evidence; no multiplayer or
production-server acceptance. The user prioritized a server without graphics
and without GPU before the first playable multiplayer flow.

## Result

One native X4 instance advanced a newly generated universe with GPU device
access removed. After software-rendered startup, the retained Rust scheduler
reported **1,217 consecutive simulation steps with zero render decisions**.
The bounded observation reached its step/callback criteria, but **stable
server acceptance fails**: the engine emitted 2,274 vertex-buffer errors.
This is not a completely graphics-free engine: Vulkan, renderer resources,
render preparation and a private X server remain initialized.

The process used the ordinary full-universe internal gamestart and official
DLCs. No sector-limiting job or simulation rules were installed. This does not
establish mature-universe performance, every NPC's correctness, detailed
combat at the experimental tick rate, or correctness of multiplayer authority.

## Measurement

The steady window starts at the first externally sampled scheduler count of
at least 100 and ends at the last sample. Process times come from `/proc`;
100% CPU means one logical CPU fully occupied, not the whole processor.

| Observation | Result |
|---|---:|
| External window | 54.927 seconds, 55 samples |
| Simulation steps in that window | 1,100 at configured 20 Hz |
| X4 aggregate CPU | 83.60% |
| Movement worker CPU | 29.11% |
| Main thread CPU | 1.09% |
| RSS at window start / end | 7.468 / 7.569 GiB |
| Independent UI callback advance | 1,108 callbacks |
| Independent MD event advance | 54 callbacks |
| UI-reported game-time advance | 55.3 seconds |
| Forced draws, irregular dt, wait timeouts, dropped time | 0 |

UI logs have a coarser cadence than external samples; their endpoints are not
exactly simultaneous. The native cadence log independently reports 20 steps
per wall second and game/wall ratio 1.0000. CPU figures include all X4 work,
including remaining graphics preparation; probe/supervisor cost is not part
of that process total. Thread figures cover threads present at both endpoints.

There was no saturated simulation thread in this short window on the local
machine. This does not predict capacity on Silver's unidentified server CPU.
The historical H0 software-rendering run used about 11.86 CPU-seconds per wall
second, but differs in date, graphics driver, scheduling and observation
window. It is not a controlled before/after benchmark or a valid speedup ratio.
The approximately 104 MiB RSS increase warrants longer observation after the
buffer issue is fixed; this window alone neither proves nor excludes a leak.

## Engine and isolation

- Pinned X4 9.00 (611726), Steam build 23660954; executable SHA-256
  `113f73d74570084f6ce18a659b365009eccf579fb66dbabb5dad2b1718045f20`.
- Existing Rust `x4mp-agent`: `fixed20`, render interval `never`, render gap
  zero, catch-up cap 3,600. The trial ends well before that cap. This does not
  remove the scheduler's forced-render policy or establish unlimited skipping.
- The corrected second-flag-read patch preserves render-list flipping and
  selects the native no-draw branch. Render counts are scheduler decisions
  supported by verified branch sites, not independent Vulkan-submit counters.
- Xvfb 640x360; Mesa 26.2.6 llvmpipe/LLVM 23.1.2. The same namespace's Vulkan
  probe exposed only a CPU device. A fresh minimal `/dev` exposed no graphics
  or compute device nodes; existing Steam shared memory remained available.
- Read-only host/installation, disposable HOME/profile, allowed official DLCs
  plus the retained H0 observer. No authority gate, no client, no save loading,
  no save output, autosave disabled. All owned processes stopped; protected
  inputs and Steam identity checks passed.

## Failure and next work

The first attempt stopped during bootstrap because inherited preload helper
processes wrote expected executable refusals into X4's diagnostic file. It did
not test simulation. Commit `d1d54c93` adds the emitting PID to every agent log
record. The second runner selects only the owned X4 PID and checks its process
start identity; no executable-verification guard was weakened.

The second attempt emitted repeated
`Mesh::UpdateMeshFromDataExchanger ... failed to create/update all VertexBuffers`
errors from game time 7.29 through 62.14. These were outside the runner's original
transform-overflow warning matcher. Consequently `result.json.success=true`
means only the bounded scheduler/callback criteria; `assessment.json` explicitly
sets `stable_server_acceptance=false`. Preserve both rather than rewriting the
raw result into a clean server pass. Investigate resource retirement and render
preparation before a longer run; do not simply hide these errors.

[The follow-up static analysis](AUTHORITY-GRAPHICS-ARENA-20261117.md) identifies
a bounded staging arena whose rotation/reset is skipped along with drawing.
Its usual reuse waits for a Vulkan fence; dynamic mesh updates still record
buffer-copy commands. This is a concrete lifetime hypothesis, not a proven
safe fix. Resetting the cursor or graphics index alone would be unsound.

Offline preparation: 40 library unit tests, 3 signature-checker unit tests and
7 threaded scheduler harness tests passed. Exact-image/site signature checks
passed. After the logging-only change, its one focused test and release build
passed; the unchanged scheduler harness was not repeated. The earlier unrelated
full-suite failure remains open.

## Retained local evidence

- `.local/headless-scheduler-native-20261117-01/`: early bootstrap failure.
- `.local/headless-scheduler-native-20261117-02/`: `runner.py`, `setup.json`,
  `samples.json`, `result.json`, `assessment.json`, `vulkan-summary.txt`,
  `devices.txt`, owned-process identities, full engine/agent logs and profile.
- `.local/headless-agent-build-20261117/release/libx4mp_agent.so`, SHA-256
  `05e66eab9b3854437bbb5f91300c4617e77704ed372309c3bf78de35e3e69881`.

The trial source is local and experimental, not a supported server launcher.

## Software renderer CPU during a single-provider checkpoint, 25 November

During vanilla four-seat world preparation at `04f409f6`, one native X4
provider ran on a Ryzen 7 7800X3D. There were no game clients. The private
profile requested 640×360, immediate presentation and no frame-rate limit.
Vulkan reported a CPU device, llvmpipe (LLVM 23.1.5). After the preparation
cue confirmed the added captain and freight, five consecutive Linux
`/proc` measurements spanning 10.54 seconds recorded 12.03–12.91 logical
CPU cores for X4 and 6.074 GiB of proportional set size.

A separate 2.50-second measurement grouped per-thread CPU deltas by the
kernel thread name. Threads named `llvmpipe-*` accounted for 12.344
logical cores, `Main()` for 0.124, and the other named threads together
for 0.056. The busiest activity observed in this checkpoint interval was
software rendering. Simulation throughput, occupied-sector combat, and
performance on hosted Milan hardware require their own measurements.
These values describe the X4 process alone.

Private samples are in `.local/vanilla-four-trade-seed/run-01/`.
SHA-256 for `provider-checkpoint-resource-sample.json` is
`39dab6f5f8e666e3b36c748c879aa95275b42ebb429fc31656bf13d353b4d07c`.
SHA-256 for `provider-checkpoint-thread-groups.json` is
`0d510289acb336489097098ef770409870a7a0713b538c03581f357e996d9808`.
