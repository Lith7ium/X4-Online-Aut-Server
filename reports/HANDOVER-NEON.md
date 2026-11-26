# Authoritative fork team handover

## 26 November 2026

Two admitted protocol pilots each tried to spend 106 Cr from a shared
160 Cr balance at UPV-940. The GPU-free vanilla provider booked
EST-929's purchase, refused BBS-493's order at the same price, and
showed both pilots the remaining 54 Cr. The last-departure save,
ship cargo and stock journal confirmed one debit and one transfer.
The test ran over loopback with headless pilot endpoints.
[Competing purchases, saved world and provider resource use](AUTHORITY-SHARED-WALLET-CONTENTION-20261126.md)

Reopening the trade window while a sale is pending now waits for the
receipt. If its unused quote expired before submission, the pilot gets
a refusal and can reopen trade. The window disables the sell slider
and clears the sell selection when the quote expires. Purchases remain available.
These paths passed Linux UDP and LuaJIT widget tests.

The server now uses the stock whole-credit affordability check when
placing a purchase. In native X4, a 116 Cr wallet can cover two units
quoted at 58.48 Cr each. A changed unit price still requires a fresh
confirmation. The native boundary probe read the game helpers and MD
expressions without placing an order.
[Native cash boundary](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md#native-whole-credit-affordability-boundary)

A fresh GPU-free X4 process loaded the last-pilot checkpoint after the
sector transfer. Before any pilot joined, two native readbacks placed
BBS-493 at (30000, 100000, 55000) m in the destination sector.
The ship kept its registry index and its original NPC captain.
[Cold restart and retained captain](AUTHORITY-CAPTAINED-COMPANY-FLIGHT-20261125.md#read-only-cold-restore-of-the-bbs-493-departure-save)

HVC-420 crossed the ordinary ZGH-239 → NEL-546 jump gate under stock
autopilot. A regular Linux cockpit and a separate GPU-free X4 provider
ran on Bazzite. The first destination sample discarded the previous
sector's velocity. By sample 195, velocity had resumed and the provider
verified the transfer within 1 mm. After the last-pilot save, a fresh
provider and cockpit restored the world through regular admission. The
pilot took the helm of HVC-420 in the destination sector. Its first
acknowledged position was 0.20 mm from its saved position. KHI-630
retained its own position across both saves. An earlier test observer had accepted
only the old superhighway destination. The correction was private to
the ordinary gate fixture. [Native gate, acknowledgements and saved world](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md#verified-ordinary-gate-movement-and-departure-save)

The ordinary boarding menu now has a native positive control. On a
disposable provider the test activated the enabled Start Operation button
once. X4 confirmed eight marines and exactly one attacker, ARU-541,
against ZNG-430. No player was connected.
[Native map start](AUTHORITY-BOARDING-BASELINE-20261125.md#native-start-operation-click-with-stock-component-ids)

In a separate GPU-free provider, the eight marines on ARU-541 launched in
two pods against the fully armed ZNG-430. The stock operation progressed
to infiltration, then X4 reported failure. Scale Plate retained the ship.
Pod attachment and the cause of failure are unknown. This trial started
on the provider. [Boarding timeline](AUTHORITY-BOARDING-BASELINE-20261125.md#native-follow-up-at-the-highest-stock-thresholds)

Cobalt's Windows 11 rerun passed 37 of 39 cases, with two skips. His
separate Windows run at `f0605562` passed all 15 NPC observation,
trade capture and purchase-source fixtures with zero skips. The
integrated Linux suite found seven errors in a shared test fixture.
After the fix, all 29 cases in the four affected modules passed.
The last full Linux run still has 25 skips. Cobalt observed six shield
hits on the occupied Company ship on Windows in a separate creative
world. He is preparing the same observation for an admitted pilot
with an offered checkpoint. The Internet flight awaits the operator.
[Windows hit observations](AUTHORITY-REGISTERED-PILOT-COMBAT-20261125.md#occupied-victim-on-windows) ·
[02:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795654839676439)
[Strict run and coverage gaps](AUTHORITY-STRICT-CHECKS-20261125.md) ·
[Windows fixture results](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795647665602239) ·
[01:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795651241349479)

Matching private operator packages for `060a9da6` are in
`.local/flight-build-060a9da6/`. All 176 files common to Linux and
Windows matched byte for byte. Their runtime files, including the
Windows DLL, match the reproducibly built `ba6f325a` archives.
The guides now give the sales switches for each platform and explain
how to check a purchase after a disconnect. Both Linux launchers and
the Windows wrapper's Python entry point passed their help command on
Linux at `ba6f325a`. Native Windows startup for this build awaits its operator.

| Package | Bytes | SHA-256 |
| --- | ---: | --- |
| Linux server and pilot | 1,633,760 | `8f6129ac68385e20ba9c594b78217e31ccd7238e8956df4e60809340e516797a` |
| Windows pilot | 3,113,495 | `d09b6e951ff8c870cf8823ef19c62359b47792728980e2395b57fc6932e4d2a4` |

## 25 November 2026

Astra integrates on `Lith7ium/X4-Online-Aut-Server`, branch
`test/base-game-two-pilots-20261124`. Lith7ium approves major architecture decisions. The session
limit is four pilots. Compare findings and assign work at the top of the
hour, Berlin time, in `#x4-authorative-server`. At 14:00 Cobalt had
reproduced the assigned Windows flight ZIP byte for byte and verified
the linked-extension-root check on NTFS. The Windows flight still needs
the operator. At 15:00 Neon took the registered NPC receive/playback check
on Windows and Quartz took a native stock “Fly and Wait” trial.
[Development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795615284533159)

At 16:00 Neon reported that a native Windows cockpit received all 90
recorded NPC samples, including 89 with velocity. The observer used a development
adapter. Its target was outside radar range. Neon now owns live NPC
sampling, interest-filtered playback, withdrawal and reacquisition on a
separate branch. Cobalt retains the actual Windows/Fedora Internet flight.
Molander reported that Quartz has exhausted its budget, with an estimated
return on 2 December. Astra has taken over the fleet-order work.
[Windows NPC experiment](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/ed9ebb7277dfa7bea79b8ecb7463fa63c27e7b60/reports/AUTHORITY-WINDOWS-NPC-RECEIVE-PLAYBACK-20261125.md) ·
[16:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795618874995979)

At 17:00 Neon reported 146 live Windows NPC source samples and 93 receiver
deliveries during radar or map interest. The experiment used one X4 process
and mapped the displacement of FVH-379 onto another registered ship, KKJ-450.
The harness toggled interest. Delivery stopped while interest was withdrawn
and one alignment followed reacquisition. The next evidence needed is the
same registered NPC across two independently running X4 instances. Neon's
branch is parked for its quota reset. Cobalt retains the actual Internet
flight and can run the four outstanding Windows dependency checks separately.
[Windows NPC report](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/032c69a8b5fe765dda8d67f2c308a8f7b563f74f/reports/AUTHORITY-REGISTERED-NPC-PATH-WINDOWS-20261125.md) ·
[17:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795622440162299)

A Linux pilot at the helm of WOO-831 used the stock map context menu to
send WRN-302, a free Company ship with an NPC captain, to a point in its
own sector. The CPU provider at `494b470b` accepted one MoveWait. Its
departure checkpoint retained the order. The pilot's local queue stayed
empty. The matching queue-choice packages at `1ad21866` add **Keep** and
**Clear** based on the real interaction menu. Their two-pilot control test
covered receive order with a simulated engine. The earlier native run
covered one order on an idle ship.
[Native fleet command and checkpoint](AUTHORITY-STOCK-FLEET-PAIR-LINUX-20261125.md)

A cold-start Linux pair at `1ad21866` then used the stock **Keep** and
**Clear** setting buttons. The same free WRN-302 retained its captain.
The native server queue changed from A to A+B and then to C. The pilot's
local queue stayed at its inherited A. The last-pilot checkpoint
`checkpoint-118cee1e9ca54ec9bba1cb4ab1609540` retained only C plus the
default behaviour. Its fleet journal holds all three request IDs. The
purchase ledger's business rows matched the source. This used one actual
pilot and a separate CPU-rendered provider on Bazzite.
[Native cold queue and saved result](AUTHORITY-STOCK-FLEET-QUEUE-LINUX-20261125.md)

Historical internal server/Linux pilot and Windows pilot packages for the
first NPC observation are in `.local/flight-build-5d2299f3`. Linux SHA-256
is `e29ef9f162b85bbd6dfe6c22e4689e4438906a04236086b7c05d104b69629981`,
Windows SHA-256 is `4babf51e632ede4bc6f4ec8bdb282f8d45e128eecd88c81d08ce18a29a99447e`.
All 173 shared source files matched. The pilot's NPC mode observes one
identified ship through native radar or an explicitly selected and picked
map target. It has no NPC playback writer. The provider uses its existing
authenticated movement channel for this one-window trial. The recorded
velocity is calculated from measured displacement per real second.

The first live Linux pair rejected the running world's expired join-offer ID.
After the guard was corrected, the stock radar observer for HVC-420 confirmed
one exact provider pose for the Antigone freighter MFQ-419 in two independent
runs. At `3e005330` the second native capture took 2.199 s and arrived after
the two-second source lifetime. The maximum Python polling gap was 131 ms.
With the 640×360 software profile at `24284592`, the cockpit confirmed 18
server poses spanning 17.65 km. All 18 matched the provider. At `0a4c4e08` each position still had its own
registry check. Faster dispatch delivered 35 positions over 18.67 km,
including a nonzero velocity of 1,146.46 m/s. Five low-attention position
jumps exceeded the existing finite-difference rate bound. The four-nonzero
acceptance and physical playback are open. The initial ZIPs predate these fixes.
[Native NPC observations and checkpoints](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md)

The regular Docked trade window at `abf70d48` received current server
offers. A purchase of two water settled at 116 Cr. On reopening, the
same pilot saw 9,768 Cr and four water aboard while the underlying local
game still held its pre-purchase values. The final provider checkpoint
and ledger recorded the purchase once.
[Stock trade window and independent provider](AUTHORITY-STOCK-MARKET-VIEW-20261125.md)

At 18:00 Cobalt reported the four remaining Windows dependency cases
passed and offered branch `contrib/cobalt-windows-client-tests-20261125`
at `bcf300cc`. Astra integrated the portable client fixes and retained
coverage for file replacement, non-IPv4 connections and both LuaJIT
runners. The assigned Windows/Fedora Internet flight
still needs the operator. Neon is parked until the weekly reset on
1 December at 13:09 Berlin. Molander confirmed that Quartz was archived.
Astra owns its former fleet work.
[18:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795626039051069)

At `5f83cb22` the disposable cockpit briefly held MFQ-419, then
released it. X4 confirmed its previous order running again. HVC-420
received 53 exact observations and three nonzero transmitted rates. The
provider separately sampled X4's native velocity 48 times. After rotation
into sector axes it closely matched the direction of the freighter's
movement. Its use on the wire requires a timing choice. A physical
position change on the receiving pilot is the next separate trial.
[Native velocity and order restoration](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md)

At `e1c6a7bd` the Linux cockpit physically moved MFQ-419 exactly once,
975.927 m to the next fresh server pose. Immediate native readback
reported zero position and orientation error. HVC-420 stayed in place.
The captain resumed the previous order. This remains a one-shot trial.
[Position readback and last-pilot checkpoint](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md)

Matching internal packages are in `.local/flight-build-e1c6a7bd`. The
Linux archive is 1,612,554 bytes, SHA-256
`29078593fe5cfab51de43308a4c5915d6e119dc71460c8945dbd9eab13163f7c`.
The Windows ZIP is 3,054,572 bytes, SHA-256
`76a26b0e7e43d6c77cc5cb760264378d800e0447923ac8038d9d5545cf2be452`.
All 174 shared source files matched, and the extracted Linux provider
and pilot help entrypoints ran. The verified alignment is Linux-only.
The operator has not flown this Windows package.

At 19:00 Cobalt confirmed that his Windows system also lacks the original
x4-re references and released lane tool. The corresponding twelve tests
remain open. His WAN flight is waiting for the operator. Astra is checking
X4's native ship velocity and the temporary cockpit-side NPC order.
[19:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795629624674239)

The 20:00 comparison covered one real cockpit correction, the saved marine
transport and the full Linux regression. A later simultaneous run at
`4deb8ecf` found 160 distinct samples of the locally simulated MFQ-419
on the pilot. The provider had seven distinct positions in the same
window. The pilot's previous native velocity predicted the next local
position to 0.017 m median error in game time. This makes the pilot a
candidate source for visible NPC movement. Authority transfer is open.
[20:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795633226619129) · [Simultaneous trajectories](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md)

One stock sale now has a receipt and a saved result across two Linux X4
instances. WRN-302 sold two water to CTD-093 through a single stock
confirmation over loopback TLS. The Company received 69 Cr, the station
gained two water and the pilot saw the new balance and cargo on reopening
the menu. The last-pilot save contains exactly one sale and the six prior
purchases. The issuing pilot's launcher reported a local config cleanup
error after the sale. The corrected cleanup at `df53039d` passed on a
private copy of the exact staged config. The verified trading run used
`f74edbfe`. The private archives in `.local/flight-build-f74edbfe`
correspond to that earlier revision and include the cleanup defect.
Windows and WAN sale acceptance are pending.
[Sale receipt, ship journal and final save](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md#sale-through-the-admitted-linux-pilots-stock-trade-menu) · [21:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795636827290579)

At `46fc471a` the GPU-free provider received 155 of the cockpit's 160
native observations of MFQ-419. Every received position and velocity
matched the pilot's raw record. Radar loss ended the stream while the
pilot was still connected. The same connection acknowledged 148
own-ship samples. This diagnostic route only recorded the freighter.
[Native receipt and limits](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md)

The internal operator archives at `.local/flight-build-04f409f6` contain
the cleanup fix and the market eligibility fix for separately stored
equipment. Linux is 1,627,954 bytes (SHA-256
`76e8dc601dc19054512437739ce765585001136aafe02668038549a1760860ac`).
Windows is 3,098,037 bytes (SHA-256
`6da7217a0b7e030ec491271720f0b4ef4994c687a07828e1e80ee5cd22b1ee90`).
Both refer to `04f409f68eae3078666191a6c0c4221ac7415ec6`. All 176
shared files matched. Deployment and human flight with this build are pending.

Run 11 at `9c558603` moved the same freighter observations onto the
regular pilot's game-to-launcher UDP socket. The provider accepted
154/160 samples over the authenticated movement channel. It also
acknowledged 159 own-ship samples and ended NPC reception at radar
loss. The Linux and Windows operator packages for this trial are in
`.local/flight-build-9c558603`. Linux SHA-256 is
`daae6d2e373ff970dd10ef12a9262e7dce30ed438d8c3dac62a1fbf6f1490756`
(1,632,127 bytes). Windows SHA-256 is
`6d2bac188052fcfc37100ba35080bf640f4091991b84ad13171ddb31c149b22c`
(3,110,247 bytes). All 176 shared source entries matched.
[Regular game IPC and Linux acceptance](AUTHORITY-REGISTERED-NPC-PAIR-LINUX-20261125.md) · [22:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795640423420209)

The confirmed private vanilla seed `.local/base-four-fighters-trade-seed.zip`
preserves all four start fighters and the existing Company scout.
BBS-493 has an assigned captain and four energy cells. The other
three fighters retain their original start positions and equipment.
[Seed, formation and verified restore](AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md)

Two local attempts to issue stock orders to BBS-493 from separate
cockpits ended before the second pilot reached the helm. The first
exposed a failed private stimulus and a pause-time disconnect. The
second stopped on a host memory-pressure reading. There were zero
fleet clicks in both. The second departure save preserved the captain
and the empty queue. A split-host run still needs both pilots to reach
the helm together before either issues an order.
[Two-pilot stock fleet attempts](AUTHORITY-STOCK-FLEET-TWO-PILOTS-20261125.md)

Cobalt tested the `9c558603` cockpit code on Windows 11 with CPython
3.11.9. Of 94 cases across seven modules, 67 passed, 13 skipped, two
failed and 12 errored. The Windows session and CLI modules passed all
43 cases. One failed expectation patched the Linux clock only, one
generated two indistinguishable timestamps, and 12 tests attempted
Linux provider process inspection on Windows. Cobalt owns the targeted
Windows follow-up. Separately, the NPC sender used the transport clock
for its second freshness check. The fix uses the native capture clock
at both points. A loopback test with independent coarse and fine clocks
reached the receiver with the fresh sample and rejected stale and
future samples. Windows gameplay on the corrected build remains open.
[23:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795644032861319)

The two NPC observation fixtures now run under the pinned Lupa LuaJIT
runtime without a separate executable. All three tests in that module
passed on Linux at `ec929eea`. Native Windows verification is pending.
The trade window also recovers after the server declines an unavailable
offer or quantity and rejects a stale Company basket after the pilot
changes ships. [Trade window regressions](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md#a-declined-basket-and-a-change-of-ship-in-the-trade-window)

At `bdec1390`, two admitted TLS protocol participants appended one
flight order each for BBS-493. The native provider returned both order
IDs, and the final paused save retained their order and the captain.
Both participant cockpits were simulated. [Two orders in the native save](AUTHORITY-STOCK-FLEET-TWO-PILOTS-20261125.md)

A separate GPU-free provider started a stock boarding operation against
ZNG-430 with ARU-541 and eight marines. The native operation event and
engine getters agreed on the target, attacker and marine count. This
was a disposable, unsaved provider trial. A pilot's menu click, boarding
pods and capture remain open. [Boarding evidence](AUTHORITY-BOARDING-BASELINE-20261125.md#native-start-through-stock-exports)

The provider also transferred BBS-493 to another sector with its saved
idle captain. One headless pilot supplied poses on the regular movement
connection. X4 and the marked departure save agreed on the target
position, captain and default Wait order. A cockpit gate flight and
taking over a ship with pending fleet orders remain open.
[Captained sector transfer](AUTHORITY-CAPTAINED-COMPANY-FLIGHT-20261125.md#cross-sector-transfer-with-the-saved-idle-captain)

A separate four-pilot vanilla seed is available locally at
`.local/base-four-fighters-seed.zip` (archive SHA-256
`2139fdb7ba217f8fb9cf8d8739be018c69c26c3474d8f8a86751a4a8c4143f47`).
X4 created BBS-493, EST-929, OVO-739 and XNA-002 in Black Hole Sun V.
Their saved loadouts passed inspection after the first reload. A GPU-free
Linux provider then reopened the confirmed world. Four independent loopback
TLS clients held reservations for ships 1077–1080 on one checkpoint. The
fifth received `Session Full`. The provider ended paused. This run covered
ship selection through the normal server intake. Four live cockpits and a
Windows internet flight still need testing. Local evidence is in
`.local/four-flight-admission/run-01/reservations.json` and
`.local/four-flight-admission/run-01/server/result.json`.

Fedora run-02 timed out on the native position inspection of HVC-420 (1073).
It accepted 38 stationary pilot samples and issued no pose write. Run-03
restored the last clean source with the native position writer disabled.
Bazzite entered the cockpit and received 2,653 service acknowledgements
before the native Linux sampler reported a generic tick failure. Bazzite
left cooperatively. Fedora confirmed the last-pilot checkpoint
`checkpoint-803481d8df9f484cbd07bd590a342123` (SHA-256
`a043498c333d4adf89027ff4e1158908f635aa85ddadb8ea7ef70b40d6afae76`).
Control, file and movement services passed authenticated LAN checks after
the save. The Windows operator was away and no WAN pilot flew.
An offline reproduction on Bazzite found that the native position reader
redeclared its LuaJIT FFI functions on every sample. Constructing the
original reader repeatedly failed at request 3,849 with `table overflow`.
The fix registers those functions once per loaded module. A regression
exercised 20,000 separately correlated position reads using the regular
reader, real LuaJIT and a simulated X4 ABI. The prior game's generic
`tick` error cannot establish the exact cause of its failure at sequence
3,217. Use the fixed player build for both pilots when the operator
returns. Check for sustained native flight and actual cockpit playback.

The 13:00 check-in assigned the private `2ad3fc46` packages for Cobalt's
fixed-index comparison. For automatic radar with live pilot priorities,
use a matching provider and players from `f564ab66`. The new archives are
in `.local/flight-build-f564ab66`: Linux SHA-256
`4e003266f9ad726cfa54c9019f75514a82a3922c103b9574b53121c4b6f21c6f`,
Windows SHA-256 `70b4761261076a9f078c1f4871a9b70ca96345a705f6b68e977ad2a797bbea77`.
On Bazzite both regular pilots found their peer ahead of two closer idle
ships and measured reciprocal motion with automatic cockpit playback.
[Native radar flight](AUTHORITY-TWO-PILOT-FLIGHT-20261125.md)

The earlier `2ad3fc46` packages remain available for the assigned comparison.
They preserve the observed ship's attitude on position writes and include
the earlier fix for corrections over 50 m after a rejected first position.
The cockpit controller, native pose adapter and position reader in both
archives match Git. The Windows ZIP retains the previously verified DLL.
In a native Linux cockpit, WRN-302 moved 16.59375 m through 26 corrections
with its nonzero yaw, pitch and roll preserved at each readback.
[Attitude evidence and test scope](AUTHORITY-COCKPIT-ATTITUDE-20261125.md)

| Internal player package | Bytes | SHA-256 |
| --- | ---: | --- |
| X4-Authority-Linux-2ad3fc46.tar.gz | 1,530,979 | `8f08a2b1f5bf1fe4b11c44a217e1d462f751d7629d7597ddba9004e7b4fccee1` |
| X4-Authority-Windows-2ad3fc46.zip | 2,893,154 | `1a1cd0f00833aa9ad099198057464b3362826385c372533e8c41fcb37118afab` |

For the subsequent writer-enabled sector-crossing test, the matching
`b418a409` packages are in `.local/flight-build-b418a409` on Bazzite.
Both archives contain the sender and radar code used in the successful
Linux crossing. The Linux archive also contains the four-second provider
readback change. The Windows ZIP includes the same verified network DLL.
These packages have been built and inspected locally. Fedora run-03 and
Cobalt's assigned fixed-index comparison still use their earlier builds.

| Sector-crossing package | Bytes | SHA-256 |
| --- | ---: | --- |
| X4-Authority-Linux-b418a409.tar.gz | 1,538,674 | `a013427e4d6799590a723cffba084a38fed6ce428cefd4a81f1cb0013a5bd0ca` |
| X4-Authority-Windows-b418a409.zip | 2,916,818 | `4b9b38948d5e4709ca7158b2f917c93b07a1e9b58e4fc0ccb600df523993c8cf` |

Cobalt can reproduce the Windows ZIP from this fork using the DLL in
the previously shared package. In PowerShell, from the repository root,
set `$dll` to the extracted `network\x4lan_net_64.dll` path and run
`py -3 tools/make_authority_windows_client_package.py --ref 2ad3fc46 --recorded-network-dll $dll --out X4-Authority-Windows-2ad3fc46.zip`.
Compare the printed SHA-256 with the table before launching.

The retained Fedora run-02 archive records revision `1611dbe1`. Its
deployed pose bridge and the bridge in the failing X4 overlay both hash
to `039f97b1d18332d6e5518613e9ba145bd182f4140e1a6a989f080cde6fcc2841`.
That build predates the timeout diagnostics and the handling of a late
inspection reply. Its log cannot distinguish a delayed reply from a
missing one. The replacement Linux package is staged separately on Fedora
under `provider-cobalt-outbound-20261125/kit-592733f8`. All 419 files
were compared with the archive. The deployment's Flatpak Steam wrapper
loaded the staged provider through its normal help entry point.

For the next writer-enabled run, use the sibling
`run-provider-592733f8.py --run-name run-04 --source <restored-store>`
with the absolute `checkpoints` directory of the restored world under the
new kit's `.local`. The launcher requires an explicit source and requests
persistent hosting. Close the current provider cleanly and migrate its
confirmed world before switching. The currently offered run-03 uses
the previous package in relay mode. A rehearsal on Fedora exported and
restored the earlier closed two-ship seed using the new kit. Its marker
and saved registry passed the restore checks. The checkpoint remained
`checkpoint-6e5948ba473f4b399c01fcac9ba2f7bc`, with SHA-256
`1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958`.
The rehearsal did not start X4 or use the running world's files.

A separate native purchase and departure test confirmed the server
released WRN-302's berth before its pilot received authorization and
X4 started the pilot's undock. The client-side final physical read was
missing. At `11f10348` a second Linux pair confirmed the same sequence
through X4's client undocked event. The final client berth fields were
still unavailable, and the provider saved WRN-302 detached with the
correct captain, cargo and balance.
[Linux purchase and departure evidence](AUTHORITY-PURCHASE-AND-UNDOCK-LINUX-20261125.md)

At `abf70d48`, opening the stock Docked → Trade window requested a fresh
server observation for WRN-302 at BIM-169. One stock confirmation bought
two water at 58.48 Cr each. X4 settled 116 Cr. Reopening the window showed
four water instead of two, 9,768 Cr instead of 9,884 Cr, and space for 21
further units instead of 23. Station supply fell by two. The pilot's own
local cargo and cash still held their initial values. The GPU-free server
paused and saved after the pilot left. The saved world and sealed ledger
agreed on exactly one new purchase. This was a Linux loopback test with
a real server, pilot and stock UI. Windows and WAN trade remain untested.
Sales are displayed and submission is currently limited to purchases.
[Native stock window evidence](AUTHORITY-STOCK-MARKET-VIEW-20261125.md) · [Package options](../docs/AUTHORITY-SERVER-PACKAGE.md#try-an-opt-in-company-purchase)

Matching private packages from that revision are in
`.local/flight-build-abf70d48` on Bazzite. Cobalt's assigned flight and
the Fedora provider use their earlier dedicated builds.

| Market test package | Bytes | SHA-256 |
| --- | ---: | --- |
| X4-Authority-Linux-abf70d48.tar.gz | 1,559,463 | `3b4a8fbb546310b5ce89043d0baa389bf28ea126a0b16af9c609efdea3e79f93` |
| X4-Authority-Windows-abf70d48.zip | 2,962,718 | `f8d1593aabc3c92178fd893092e2fa34b3961f4fb01c3338fccd6a869696b807` |

Lith7ium selected a separate marker for a visible remote pilot on the
stock map. The installed MapMenu and its exported interfaces examined so
far have no verified renderer for a standalone marker at an arbitrary
sector position. Keep the existing local ship and the player's guidance
and missions intact while investigating the display path. The stock
`add_holomap_text` action attaches a label to an already visible object.
`GetMapPositionOnEcliptic2` uses the current mouse pick. Neither provides
a way to draw a marker at the received coordinates. The base-game
`md/gs_boso.xml` creates a player-owned navigation beacon with
`create_object` in a chosen sector. A client-side beacon would create a
real object. Its use as a disposable marker is awaiting Lith7ium's decision.

Two ordinary Linux pilots have now flown the human test's vanilla Pulsars
in one session. In Black Hole Sun V, each receiving cockpit used radar
and measured the other pilot's short strafe, 1.365 m and 1.305 m respectively.
Both directions carried velocity. The provider applied and saved both
positions. All three native processes ran on Bazzite over loopback.
The outstanding live comparison is Windows over the Internet.
[Two pilots, both cockpit readbacks and final checkpoint](AUTHORITY-TWO-PILOT-FLIGHT-20261125.md)

Two regular pilots also flew concurrently in different sectors. HVC-420
remained in Black Hole Sun V and KHI-630 in Black Hole Sun IV. The
provider confirmed both movements and saved both ships within 0.4 mm of
its paused readbacks. Both pilots started in their respective sectors.
That run ended before the optional map opened.
[Independent flight in two sectors](AUTHORITY-TWO-PILOT-SECTORS-20261125.md)

A subsequent two-pilot Linux run selected KHI-630 on HVC-420's stock
map across those sectors. The rendered actor, selected actor and movement
receipt matched. At sequence 40 the sender and observer agreed on a
nonzero velocity. Closing the map withdrew the subscription while nine
later pilot samples were acknowledged. No icon displacement was measured.
The provider's position writer was disabled in this observation run.
[Native map selection and withdrawal](AUTHORITY-NATIVE-CROSS-SECTOR-MAP-20261125.md)

The normal player result now retains the actual samples acknowledged by
the movement service under `company_motion.last_acknowledged_sample` and
`company_motion.last_nonzero_velocity_acknowledged`. Compare epoch,
ship index, generation and sequence with the observer result. The Windows
archive shared at the 09:00 check-in predates these sender fields.

A Linux pilot flew HVC-420 through the stock superhighway from Black Hole
Sun V to IV. X4 briefly returned no sector in the corridor. The motion
sender now resumes with a fresh destination baseline under the same
grant. The authenticated service accepted subsequent velocity samples.
The optional server position writer was disabled for this diagnostic.
An earlier two-pilot run ended on an unconfirmed provider position request
before entry. In a subsequent one-pilot flight at `f20dc23a`, the GPU-free
provider transferred HVC-420 to Black Hole Sun IV at sample 161 and saved it
there after departure. A fresh provider found it in Black Hole Sun IV
within 0.47 mm of the recorded position. At `68fd061f` HVC's sender recovered
from a deliberately delayed sector read and the provider saved the destination
within 0.61 mm. In a separate natural passage, KHI remained connected and
both pilots' motion streams continued after HVC exited. The provider stopped
on its 2 s readback deadline for an earlier origin-sector write. The
matching response prefix arrived at 2.01597 s. Lith7ium approved a shared
four-second readback deadline. Two pilots then completed the same stock
passage at `1d0c4e4b`. The provider transferred HVC once, saved on last exit
and both ships were independently verified within 0.6 mm of their paused
positions. The longest successful readback in that flight was 1.599 s.
[Native flight and authority transfer](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md)

At `b418a409` a fresh GPU-free Linux server and a regular Linux cockpit
loaded the two-pilot departure. HVC-420's first cockpit position in
Black Hole Sun IV was 0.384 mm from the offered save. Both ships kept
their saved positions through this second departure. All 5,046 available
registry entries kept their ship code, class, macro and owner across the
restart, although X4 reassigned their saved component IDs.
[Cold rejoin and registry comparison](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md#native-hvc-420-rejoin-after-the-two-pilot-departure)

Lith7ium approved revoking a pilot whose control connection closes during
resume. The Company provider at `96bb4851` releases that pilot's reservation
and movement grant. Remaining pilots confirm the same transition. If the
last pilot leaves, the normal pause and save follow. Local TCP regressions
covered a surviving pilot, an empty session and fresh admission. A
native GPU-free X4 run then saved after a deliberate disconnect before
the first resume receipt. The same provider offered that exact save and
HVC-420 to a fresh connection. At `11f10348` the TCP checks also cover
a partial resume frame and two remaining pilots in a four-pilot session.
The updated private Linux kit is
`.local/flight-build-11f10348/X4-Authority-Linux-11f10348.tar.gz`
(1,539,196 bytes, SHA-256
`f5c2f0acbe7f798ab2617569327491ee0e538f7f62307feb5ec7274c9839cda1`).
It has not been deployed to Fedora. The client wire format is unchanged.
[Control loss during native resume](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md#confirmed-control-loss-during-native-resume)

- **Cobalt** owns the Windows-to-Fedora flight check when the Windows operator returns. If profile 45841 is
  already occupied, the updated packaged launcher can reserve a fresh slot
  with `-FreshCompanyProfile`. Run it directly with the existing private
  connection file and assigned seat. A Windows-Python filesystem check under
  Wine preserved 45841, used 45850 and stopped on an exhausted pool.
  His Windows fixture commits through `20f83c6c` have been integrated.
  At `ec91ffbd`, exercising the overlays with the pinned LuaJIT also
  caught and fixed a shared installer error in recovered motion playback.
  The regular Company cockpit uses a separate installation path. The affected
  Linux test groups passed all 115 cases. Windows CPython 3.14.8 under Wine
  passed 20 of 21 selected cases, including both checkpoint checks. The
  test filesystem lacks Windows junction support. Run
  `test_authority_windows_fixture_overlay.FixtureOverlayTest.test_linked_extension_root_is_refused`
  on a native Windows NTFS volume for this case.
  A native Windows join with an alternate profile is still open.
  The Windows package from `d0756d51` includes the recorded network DLL.
  Its SHA-256 is `5462d99f533f8af0abc53a8183a356331030cd9c6faf9413faaa316163974b9b`. [Shared Windows ZIP](https://x4multiplayeraicoms.slack.com/files/U0CA93RS75H/F0CLKT744UT/x4-authority-windows-d0756d51.zip)
  In this build each cockpit readback logs its view serial. Compare actual
  positions within the same ship, sector, generation and view. A new
  alignment starts its displacement measurement from the aligned position.
  During a real flight, record the Windows ship, its movement-channel binding
  and acknowledged moving samples. [Windows launch instructions](../docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#run-on-windows)
  The last human test showed both
  fighters, but Fedora's 392 samples all matched Bazzite's count. A native
  Windows observer has since played back recorded trajectories locally.
  [Human flight](AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md) ·
  [Windows playback](AUTHORITY-COMPANY-COCKPIT-MULTIPILOT-WINDOWS-20261125.md)
  On software Vulkan, the Linux cockpit selected the correct remote ship
  on radar while the HUD pointed elsewhere. It received an archived sample.
  The observer logged expiry 1.563 s after confirming the binding, before a
  position could be read back. The window for a fresh observation is 450 ms.
  [Native radar evidence](AUTHORITY-COMPANY-INDEXED-HUD-20261125.md)
  With the `2ad3fc46` packages, use the explicit remote ship index for the
  two-pilot diagnostic. Their proximity-based selection can fill all three
  slots with idle ships. Current source negotiates a list of up to three
  active foreign ship indices on the authenticated motion stream. The
  cockpit checks local visibility before giving those ships priority.
  A legacy provider can still serve the proximity-based selection.
  [Cockpit selection](../src/authority/md/company_cockpit_observer.xml)
  Use the uncrewed fighters for this flight. Current playback on the observing
  client excludes ships with an assigned NPC captain, even after another
  player's client has taken the helm. The earlier human test used uncrewed
  ships. [Retained-captain flight audit](AUTHORITY-COMPANY-FLIGHT-RETAINED-CAPTAIN-20261125.md)
- **Neon** completed the Windows GPU receiver run. OGE-507 moved 88.71 m
  on the recorded trajectory. The local ship and MHK-947, which remained
  selected in the HUD, stayed at their original positions. The private
  fixture used X4's real-time clock aligned to the Windows performance
  counter. Neon subsequently observed the packaged Windows DLL loaded in X4,
  verified its clock and correlated 37 of 37 controlled-ship samples
  with local acknowledgements. The next check pairs an authority NPC with
  the Windows receiver. Cobalt owns the Internet flight.
  [Native Windows sender evidence](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/8e2880cab4632afe20e09a4655dae4fcf63e591f/reports/AUTHORITY-WINDOWS-SENDER-DLL-20261125.md)
  The shared ZIP contains DLL SHA-256 `6ad97e04…e2809e0`.
  The indexed receiver fixture used the older checked-in DLL
  `0fc98dba…f80b6ab6`. The packaged DLL's clock previously advanced
  under Wine. Capture which DLL the regular launcher loads on Windows.
  [Network DLL evidence](AUTHORITY-WINDOWS-NETWORK-BUILD-20261118.md)
  [Windows GPU report and harness revision](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/7d575ef1cd904acfc3d30e6cccd5d7ca1e4c8257/reports/AUTHORITY-COMPANY-INDEXED-HUD-WINDOWS-20261125.md)
  Neon previously delivered Windows observations of shield and hull damage from
  weapons. A regular Linux pilot's stock guns have since produced 49 shield
  callbacks against KHI-630. Its registry identity and HVC-420's last-attacker
  identity matched the pilot's offered checkpoint. The provider retained
  the ships' starting health. [Registered-pilot volley](AUTHORITY-REGISTERED-PILOT-COMBAT-20261125.md)
  Shared damage across machines and boarding remain open. A confirmed
  disposable world now contains ARU-541 with its Company captain and
  eight saved marines near the registered Scale Plate target ZNG-430.
  A cold Linux provider resolved ARU-541 at temporary registry index 5049.
  With the avatar in another sector, the stock menu queries found Board
  selectable, the target capturable and all eight marines assignable.
  Persisting the new index and starting an operation are still open.
  [Boarding baseline and native probe](AUTHORITY-BOARDING-BASELINE-20261125.md)
  Before implementing damage replication, ask Lith7ium which simulation
  should settle a hit when two pilots are involved. [Native combat observations](AUTHORITY-COMBAT-OBSERVATION-WINDOWS-20261125.md)
- **Quartz** (currently unavailable) traced the stock map's “Fly and Wait” command to an NPC-captained
  Company ship. Astra exercised the stock handler on Linux. X4 accepted a single
  order and the ship came to rest 2.23 km from the exact requested position.
  Lith7ium approved allowing any admitted pilot to command an unreserved
  Company ship with an NPC captain. The first opt-in pins the target during
  server dispatch and reports native acceptance. Private packages are under
  `.local/flight-build-3f109634` on Bazzite. The Fedora provider used for
  Cobalt's assigned Internet flight has its earlier build.
  [Stock command and stopping position](AUTHORITY-STOCK-FLEET-ORDER-LINUX-20261125.md) ·
  [Fleet options](../docs/AUTHORITY-SERVER-PACKAGE.md#try-a-company-fleet-order) ·
  [Stock fleet order trace](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/d21fbfee/reports/AUTHORITY-COMPANY-FLY-AND-WAIT-SOURCE-20261125.md)
- **Astra** owns server and client integration. A pilot can board a reserved
  ship as a passenger while its NPC captain finishes a trade. Lith7ium
  wants the client to wait for the server ship to clear its berth before
  flying. The server verified physical departure and the client responded
  to a simulated confirmation in separate native trials. The later paired
  Linux runs connected these steps through X4's client undock event. [Captain handover](AUTHORITY-COMPANY-CAPTAIN-TAKEOVER-20261125.md) ·
  [Client undock trial](AUTHORITY-COMPANY-UNDOCK-LOCK-20261125.md)
  When connecting them, track the client's original berth and the server's
  actual berth separately. Both belong to the same station, but the server
  can assign another free trading bay. The release must cover the reserved
  ship leaving that server bay while the client still occupies its original
  bay. [Native alternate-bay trial](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md)
  An offline check found that the existing Undock button can capture a
  request handler before the Docked menu builds its row. A real click under
  the stock lock and the keyboard shortcut still need checking.
  [Stock Undock callback](AUTHORITY-COMPANY-STOCK-UNDOCK-CLICK-20261125.md)

In a private native server trial X4 refused to place WRN-302 while its
trade was active. Once its captain had undocked, X4 accepted the exact
requested berth. A separate query found the original captain PYN-740
at the helm and WRN-302 alone in the bay. The alternate-bay trial used
an uncrewed ship. Transmitting a newly observed landing from a pilot
remains open. [Crewed berth trial](AUTHORITY-COMPANY-BERTH-RETAINED-CAPTAIN-20261125.md)

The private Linux provider moved the undocked WRN-302 twice with its
original idle captain PYN-740 aboard. The final saved position was within
1 mm of the second target. Both targets came from a test driver.
[Provider move and save](AUTHORITY-TWO-IDLE-NPC-POSES-20261125.md)

A regular Bazzite pilot then took the helm of WRN-302 and strafed with
stock game input. The GPU-free provider on the same computer accepted
42 samples, including 41 with velocity. It moved WRN-302 by 17.4375 m to
the subsequent settled position. After the pilot left, the saved position
was within 0.51 mm of the paused target. PYN-740 remained aboard,
and the Company balance and cargo matched. This run used loopback TLS
and one pilot. [Native flight and final saved bytes](AUTHORITY-CAPTAINED-COMPANY-FLIGHT-20261125.md)

A separate native Bazzite observer saw WRN-302 at 800 m. The ordinary
HUD identified the ship while the indexed radar reader declined it because
of the NPC captain at its helm. WRN-302 moved 46.44 m locally during
the observation with no playback writer. Remote flight with that captain
still needs a way to coordinate local simulation on the observing client.
[Native cockpit observation](AUTHORITY-COMPANY-FLIGHT-RETAINED-CAPTAIN-20261125.md)

A pre-booking refusal for a busy provider, a missing captain or an
unconfirmed trading berth now reaches the cockpit. The pilot can open a
fresh offer after resolving the cause. The old Confirm button cannot
submit the same basket again. [Cockpit adapter](../src/authority/lua/company_trade_capture.lua)

A regular Linux pilot made a single purchase through the stock Confirm
callback with a separate native X4 provider over loopback TLS. The server
debited 116 Cr for two water. The pilot received the receipt. The final
server checkpoint and its ledger contain 9,768 Cr and four water on
WRN-302. The pilot's local engine still counted 9,884 Cr and two water
after the notice. The test driver invoked the real menu callback on Bazzite.
[Native buyer and provider](AUTHORITY-STOCK-PURCHASE-NATIVE-PAIR-20261125.md)

The authority then restarted from that departure checkpoint. A fresh
Linux pilot rejoined WRN-302, retained PYN-740 and read 9,768 Cr and
four water from the local engine. After this pilot left, all three
purchase records still matched the opening ledger.
[Native cold rejoin](AUTHORITY-STOCK-PURCHASE-COLD-REJOIN-20261125.md)

At `ba9cc171` the client retains a reconciled earlier basket for cockpit
display after a ship change. Loopback IPC covered a lost acknowledgement,
a client process restart and delivery with an unrelated new basket open.
The HUD text was exercised with LuaJIT and a simulated X4 display.

Two consecutive server purchases survived a save with matching cargo and
Company balance. A separate Linux run sold two water, credited 69 Cr,
and matched the ship and station inventories to the native transaction
journal. The sale used a disposable world and did not exercise a remote
pilot's trade menu or a save. An earlier one-unit sale has an unknown
outcome and must not be resubmitted. [Stock trade evidence](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md)

At `1f6fb7b6` the official Linux suite discovered 5,847 tests. All 5,822
executed tests passed, including the two worker cases affected by UDP
ownership checks in previous runs. The strict runner exited 1 because
25 cases were skipped. Thirteen have native Windows passes across two
revisions. Eleven need the original X4 UI or FFI references and one needs
the original released lane tool. These twelve cases remain unverified.
[Strict Linux result and Windows checks](AUTHORITY-STRICT-CHECKS-20261125.md)

### Native checkpoint after the last pilot disconnects during a join pause

Previously, the recovered provider could confirm its own pause for a new
join, then abandon the save when the last admitted peer disconnected
during that pause exchange. The recovered Company path now retires the
definitively disconnected peer and continues the guarded save. Ambiguous
I/O and unresolved delegated writes still fence the operation.

At `7aa5b6e82c7eaf88aa0d2b7078ea699e386899ae`, one GPU-free X4 provider
and two headless TLS participants exercised the regular control path. A
acknowledged Resume. B requested admission 3.048 seconds later. A
received and validated Pause, then closed its TLS socket without a Pause
receipt 0.760 seconds after B's request. B received its only ordinary
offer 85.168 seconds after A's close callback and then downloaded the
confirmed save. File timestamps on the provider placed `CURRENT` 2.378
seconds after the native save's last write and `CONFIRMED` another
30.479 seconds later. The logs do not separately time the registry
capture, native save operation or TLS delivery.

Independent reads of the downloaded files matched each offer's size and
SHA-256 and found distinct native checkpoint markers. Saved game time
advanced from 568.114 s in A's `checkpoint-ba292e37f912483b9edeee520e95c3c2`
to 571.813 s in B's `checkpoint-ffaa13cd47fd4a49ad198eb2ec89310f`.
B's digest was `844ac63126c610aad580aab9572c05652b96a2eec87e1e77a67ed1c6c29382ab`.
The provider ended paused at B's checkpoint with no owned processes
or held native slot. The original and staged source hashes were unchanged.

X4 produced both saves and their markers. A's game-side confirmations
were simulated by the harness. B verified its transfer and stopped
before game load or admission. Private verification evidence is under
`.local/native-pause-last-eof/run-01/`.


## Archived handover from 24 November, 18:00 Berlin time

### Assignments on 24 November

**Neon owns NPC movement from the server to a Windows pilot's screen.**
Build on your native reacquisition branch at `8416b817`. Bring gameplay
capture and playback into an admitted session, exercise multiple visible
ships, and measure the cost against the 10,082-entry world. The delayed
source checks are complete. The next milestone is a moving native source
and an admitted Windows observer of the same registered NPC. Coordinate a shared
native run with Astra when the ordinary player path is ready. [Scope and acceptance evidence](NEON-NPC-SHIP-MOTION.md)

**Cobalt owns map-visibility research and the ordinary Windows player
preflight** ahead of the coordinated Internet test. The initial observer
build was `cf57d0b1`. The `2a2b569d` packages retain a confirmed moving
sample when the observed pilot comes to rest. For the joint flight, use
the matching packages at `e38cfbea`. They also support the optional
same-sector cockpit playback trial. The gateway has passed a public-address
loopback check from the host LAN. We still need a Windows pilot on a separate
Internet connection. Exchange session settings privately.
[Playback measurements and package hashes](AUTHORITY-COMPANY-SHIP-PLAYBACK-20261124.md)
[Windows flight procedure](../docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#observe-a-short-cross-sector-flight)
Cobalt completed the local Windows preflight on the flight build. The
source-only package was reproducible, the extracted commands parsed and
the pinned game executable matched. The matching Windows ZIP with the verified
project network DLL is available in the project channel. Cobalt can check
the downloaded file against the posted hashes before the coordinated flight.
[Windows package with the DLL](https://x4multiplayeraicoms.slack.com/files/U0CA93RS75H/F0CLESBCX5H/x4-windows-player.zip)

A separate internal pair at `69268cc6` adds optional Steam discovery and
verified temporary save staging. Its new Windows entry path has package and
Linux fixture coverage. Cobalt's published preflight still refers to the
package above. [Silver reuse and package hashes](AUTHORITY-SILVER-REUSE-20261124.md)
[Windows preflight](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795536126403889)
[18:00 Slack check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795539697756849)

Cobalt's map investigation at `3167327b` is accepted for the current
milestone. In two paused Windows runs the raw renderer list and screenshots
agreed when the target left view and returned. The offscreen transition also
cleared focus. Cobalt is preparing the ordinary Windows launcher and will
coordinate a separate Internet connection with Lith7ium. Neon can use
renderer membership as evidence in the live NPC observation.
[Windows map observations](AUTHORITY-MAP-VISIBILITY-WINDOWS-20261120.md)

Astra owns movement of player ships, the provider and integration on
`experiment/authoritative-server-20261115`. For read-only contributor
accounts, a PR from a personal fork or a patch with a source commit works.
Bring the result, outstanding decisions and next substantive milestone to
the hourly check.

### NPC work already verified

The Linux loopback on Neon’s port `05e68bf7` exercised ENH-870 with real TCP
and UDP, simulated visibility and a synthetic source. Neon then enrolled a
new YDA-317 in a private native Windows game. After reacquisition, a requested
20 m/s yielded 31.766 m in 1.588 s. This Windows run supplies the first
component for Neon's current assignment. [Windows measurements](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/4b181f5f/reports/AUTHORITY-NPC-REACQUISITION-WINDOWS-20261124.md)

At `8416b817`, three delayed-source tests also passed independently on
Linux using real loopback sockets and simulated game callbacks. A second
active track continued while the first track waited for a fresh sample.
Pause, save and resume completed after the pending cancellation. One limit
remains. A pilot can have one prepared reentry with no expiry while interest
stays unchanged. If the source never arrives, another reentry for that pilot
can wait until a barrier, loss of view or disconnect. The change is on Neon's
branch. [Transport change](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/8416b817ef876c9d1d8c324c36963d81e34495b1)

### What changed since the previous handover

- The GPU-free provider runs the full licensed Linux X4 9.00 build with
  CPU Vulkan (lavapipe) and Xvfb. A native save, last departure and cold restart
  passed. A first local sizing run used about 3.72 CPU cores and up to 8.60 GiB
  summed process RSS. Provider hardware has yet to be measured.
  [Provider evidence](AUTHORITY-PROVIDER-20261124.md)
- The GPU-free server completed a real stock purchase while its avatar
  stayed on another station. After a private native cold restart WRN-302
  held six water, the shared account held 9,651 Cr and WOO-831 remained
  in its original sector. The trial save is unpublished because its first
  verifier misread a balance. The player-facing purchase path is still open.
  [Stock trade, account debit and cold restart](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md)
- Players select an unoccupied ship of one company. Admission verifies their
  actual cockpit against the offered full registry. The test world contains
  WOO-831 (1970) and WRN-302 (7354) in different sectors.
- An admitted player gets a separate TLS movement connection over the same
  public TCP port as control and save. A Linux X4 client took control of 7354,
  and the GPU-free server accepted 326 position samples, 325 with measured
  velocity. There was one real client. Its departure paused and saved the world.
  Two-way delivery and movement-only reconnect were tested on loopback with
  synthetic sources. In a later Linux flight the pilot strafed WRN-302 by
  45.17 m. The provider adopted the position and a fresh GPU-free X4 read it
  from the confirmed last-departure save within 0.52 mm. The software client
  sampled too slowly to report velocity during this flight. Remote rendering
  and direct Windows Internet gameplay with two pilots remain open.
  [Flight and cold-reload measurements](AUTHORITY-COMPANY-FLIGHT-20261124.md)
  [Native cockpit results](AUTHORITY-COMPANY-MOTION-20261124.md)
- The ordinary company client now supports a nearby cockpit target and one
  selected ship on a map of another sector at the same time. Native Bazzite
  sampled a stationary player ship every 0.117 s at the median. Fedora
  downloaded the world over LAN TLS and reached the X4 menu, then its
  Steam IPC failed before admission. In a separate native session WOO's
  stock map selected WRN in the other sector. Its position agreed with the
  saved world within 0.51 mm. On a subsequent solo WRN flight the authority
  acknowledged native samples with nonzero velocity, including 5.76144 m/s.
  Live flight between two pilots remains open.
  [Observer implementation and native results](AUTHORITY-COMPANY-OBSERVER-20261124.md)
- Astra owns server-avatar parking. Lith7ium chose the walkable player HQ
  when available, with a well-protected station until then. The current seed
  has no HQ. Its preferred fallback is the Argon defence platform XPJ-169 in
  Black Hole Sun V, with trading station JZJ-119 as backup. A native server parked on XPJ-169, saved the world and stopped. A fresh
  server loaded the same docking bay with both Company ships free. There were
  10,082 issued entries and 17 were unavailable after relocation. An initial
  preflight failure had stopped before any write. [Parking and cold-reload evidence](AUTHORITY-SERVER-PARKING-20261124.md)
- Cobalt delivered the Windows map pan observations. Two runs showed a
  listed NPC onscreen, absent after panning and clearing focus, and present
  after returning. Focus changed together with the camera. Cobalt retains
  map visibility and any fixed-focus control. These results alone do not
  establish subscription behavior for every sector or zoom.
  [Cobalt's report](AUTHORITY-MAP-VISIBILITY-WINDOWS-20261120.md)

Major design decisions still go to Lith7ium. The shared account, independently
occupied sectors, one high-attention owner per area, visibility-based movement
updates and pause/save on last departure remain the working constraints.
Saves, connection files, TLS credentials and native logs stay private.

### Internal operator kit and validation

The owner has the private archive
`X4-Authority-GPU-Free-Company-Trial-20261124.zip` (30,185,896 bytes,
SHA-256 `e4ccf955f2bafa800d534141dce45b0b1e17f24f4916fc186b6ab9b59bec4294`).
It pairs the Linux provider and Linux/Windows players from `519cd0f1`
with the cold-verified parked world. The packaged restore reproduced the
expected save digest. The handover repository contains the project code
and reports. The operator supplies a private connection file and a licensed
matching game installation separately.

The observer-build check collected 5,517 tests. Across the strict Linux
run and targeted follow-ups, 5,493 distinct tests passed. The remaining
24 require Windows (12), external reference files (11), or the released
lane tool (1). The strict suite and live Internet gameplay each have an
outstanding acceptance check. [Test and native evidence](AUTHORITY-COMPANY-OBSERVER-20261124.md)
