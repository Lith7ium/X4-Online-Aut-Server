# Two Linux pilots ordering the same Company ship, 25 November 2026

Both attempts ended before either pilot clicked **Fly and Wait**. The
corrected attempt issued TLS offers for both seats, admitted EST-929 and
started the second native X4 process. A host memory-pressure guard stopped
the second pilot during load. The provider confirmed a normal departure
save with BBS-493's original captain and an empty explicit queue.

## Fixture and attempts

The GPU-free provider and both rendered Linux pilot processes ran on
Bazzite. Two distinct pinned TLS sessions selected EST-929 (1078) and
OVO-739 (1079). Their intended common target was the unreserved Pulsar
BBS-493 (1077), in Black Hole Sun V with its saved NPC captain. The private
harness armed each pilot's stock **Keep** and **Fly and Wait** callbacks
only after both native helms reported fresh readiness. The second step
also required the first pilot's receipt and a native server queue readback.
The intended queue was empty, then A, then A followed by B. An earlier
one-pilot test exercised the stock queue settings and saved the result.
[Prepared fixture](AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md#private-four-pulsar-trade-preparation-25-november-2026) ·
[One-pilot queue proof](AUTHORITY-STOCK-FLEET-QUEUE-LINUX-20261125.md)

| Attempt | Admission and stop | Fleet and checkpoint |
| --- | --- | --- |
| Run01 on `a87f589a` | EST-929 reached the helm. X4 rejected a private test driver's libc `access` call. That exception stopped the participant frame loop and the next admission's pause could not complete. OVO-739 received no offer. | Zero stock clicks, TLS order requests or native orders. No confirmed departure save. |
| Run02 on `7aa5b6e8` | EST-929 reached the helm. OVO-739 received and staged its own marked TLS offer and started X4. The host guard stopped it before its local load receipts or admission. | Zero dual-helm barriers, stock clicks, TLS order requests or native orders. EST-929 left normally and the provider confirmed its paused departure save. |

The corrected driver uses the existing mod-local UDP module with one
owned loopback socket per pilot, distinct tokens and fixed test steps.
LuaJIT tests withheld the callback for missing, invalid, premature and
duplicate signals under stubbed X4 FFI restrictions. Each valid pilot
step invoked the mounted callback once. The bundled UDP library and the
Linux peer-ownership check passed an offline exchange. The actual
participant frame acknowledged pause after an injected private hook
exception. Run02 ended before two live helms could exercise the gate.

The Run02 departure checkpoint was
`checkpoint-ed3fb27a6ad0423cb0e8341d84ceac9b`, marker
`578c8ab9c78cd2ca5cf3e3b581f315b4`, save SHA-256
`d41140db5103986d78c3e05b2fbff43530140bf53826a906fef738ea81c263e3`.
Independent readback verified both pointers, the saved marker, all five
Company registry entries, captain seed `-97168325741795052`, no explicit
BBS-493 orders and an empty sealed transaction ledger. Both attempts
released their owned processes, display services and private invitation.
The source archive retained SHA-256
`290b8cd2308acfbd6bfa0d2ffd0eaaccbdf9d436c95e5d02cd16233fbb0e55cf`
and all ten restored payload files matched it byte for byte. Private
evidence is in `.local/native-stock-fleet-two-pilots/`, including
Run02's `departure-verified.json`, `terminal.json` and `memory.jsonl`.

## Host pressure

The final Run02 sample recorded 1.63% in `/proc/pressure/memory`
`full avg10`. The preceding 342 samples recorded 0.00%. The prior sample
was 1.19 seconds earlier. The supervisor stopped on the first crossing,
so the trace establishes one elevated 10-second trend and leaves its
duration and source unresolved. A post-run check had returned to 0.00%.
At the guard, 12.09 GiB remained available and swap use had increased
by 0.53 GiB with 12.77 GiB free. The inspected launch scope and ancestor
cgroups had zero `high`, `max`, `oom`, `oom_kill` and `oom_group_kill`
events. The host `oom_kill` counter was zero and the accessible kernel
and oomd journals had no matching entries. The trial collected no
per-process cgroup history. These observations establish a host-wide
stall signal with RAM and swap still available. [Linux PSI](https://docs.kernel.org/accounting/psi.html) ·
[Linux cgroup memory events](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files)

| Earlier completed Bazzite three-X4 run | Lowest available RAM | Peak sampled memory `full avg10` |
| --- | ---: | ---: |
| Reciprocal native cockpit flight | 11.02 GiB | 0.00% |
| Reciprocal flight across separate sectors | 10.25 GiB | 0.00% |
| Stock map tracking | 9.94 GiB | 0.00% |
| Four-ship cockpit radar | 10.73 GiB | 0.18% |
| Superhighway transit with native provider readback | 9.32 GiB | 0.00% |

The shared harness kept the earlier three-X4 policy of 21 GiB available
before startup, 5 GiB kept in reserve, less than 1 GiB of new swap and
`full avg10` below 0.20%. It also checked the estimated second pilot load
against measured headroom. The 0.20% cutoff is a conservative host guard
inherited from those experiments. X4's minimum memory requirement was
outside their scope. [Cockpit trials](AUTHORITY-TWO-PILOT-FLIGHT-20261125.md) ·
[Separate sectors](AUTHORITY-TWO-PILOT-SECTORS-20261125.md) ·
[Transit](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md)

## Two-machine follow-up

Use a private Fedora software provider and two separately rendered
Bazzite pilot processes. This puts the authority and both pilot sessions
on opposite ends of a real machine boundary. It keeps both pilot displays
on Bazzite. Restore an independently hashed copy of the confirmed seed
into a new mode-0700 trial directory with its own checkpoint output,
Xvfb display, TLS certificate and invitation. Bind a fresh loopback port
and carry pinned game TLS over a TCP forward on the established
host-key-pinned SSH connection if its forwarding policy allows it.
The regular client still verifies the provider TLS pin and loads the
confirmed checkpoint offered on that connection.
This layout uses a separate trial port and profile from the dedicated
WAN provider on 47448. Cobalt's planned Windows flight uses that existing
setup. [Provider and TLS options](../docs/AUTHORITY-SERVER-PACKAGE.md#launch-the-provider-test)

Fedora's read-only operator check saw 11.2 GiB available and no X4 under
the account. The Bazzite software provider peaked at 6.07 GiB PSS during
Run02. Verify the Fedora game and dependencies, port forwarding and
measured cold-start headroom before scheduling. Keep the host guards
and verify process ownership on each host. The current private supervisor
expects all three process IDs locally. For the distributed test, keep the
two pilot gates on Bazzite and collect the provider readback on Fedora.
Once both pilot helms independently confirm admission, trigger each
stock map action once, in sequence. Capture the two TLS receipts and
native queues, then confirm the marked last-departure save, captain and
both saved destinations. Deployment and a live two-machine run remain
pending.

## Native provider with two headless TLS participants

One private loopback run at `bdec1390d576cb5fc5c797fa2a4e023abf7dd766`
used a single native X4 9.00 provider on Bazzite, on a Ryzen 7 7800X3D
with CPU Vulkan through llvmpipe. Two independent ordinary TLS control
connections selected EST-929 (1078) and OVO-739 (1079), downloaded their
distinct confirmed offers, and received normal acceptance and Resume.
A acknowledged the join pause for B. The harness simulated game-side
load and pause confirmations for both participants.

After both resumed, the protocol participants sent one Fleet v2 **Keep**
each for BBS-493 (1077). Both received accepted replies. Native queue
readback showed zero, one, then two MoveWait entries. B retained A's
native order 10407 and appended order 10497. The independently parsed
final save contained both destinations in that order in
`cluster_06_sector002_macro`, at (30000, 0, 55000) and
(55000, 0, −40000) metres. The saved captain seed remained
`-97168325741795052`. Both native attempt keys appeared once and no
business transaction was recorded.

The source copy retained the portable seed
`checkpoint-924ab73caa75491d97598b73b85de645` and SHA-256
`2966ae73ef1c0779684fb9dbd88cb031e89f44d31f874907d8648f9e809cae28`.
Both departures led to a marked final save at
`checkpoint-88bb0d566ee3429495170502c636b370`, SHA-256
`6d567ede0234799a86e077cdbbd246fc40ff351cb7e3c1c202deb6bdc56bc51c`.
The provider exited paused with no owned processes and the slot was
released. The verifier returned 0. Its private `verified.json` in
`.local/headless-stock-fleet/run-01/` has SHA-256
`249511d13f9455924a7ac4bef056707b196f082d48a8dd7410d3919123d34b71`.

Two native cockpit loads and stock-map actions, Fedora and a
machine-boundary transport remain open.
