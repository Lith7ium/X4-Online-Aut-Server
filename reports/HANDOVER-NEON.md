# Authoritative fork team handover

## 25 November 2026

Astra integrates on `Lith7ium/X4-Online-Aut-Server`, branch
`test/base-game-two-pilots-20261124`. Lith7ium approves major architecture decisions. The session
limit is four pilots. Compare findings and assign work at the top of the
hour, Berlin time, in `#x4-authorative-server`. The 07:00 team check-in
records the Windows sender, WAN flight and Windows test assignments. [Development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795586425233399)

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
The optional map trigger stopped before opening the stock map.
[Independent flight in two sectors](AUTHORITY-TWO-PILOT-SECTORS-20261125.md)

A Linux pilot flew HVC-420 through the stock superhighway from Black Hole
Sun V to IV. X4 briefly returned no sector in the corridor. The motion
sender now resumes with a fresh destination baseline under the same
grant. The authenticated service accepted subsequent velocity samples.
The optional server position writer was disabled for this diagnostic.
With the writer enabled, an earlier two-pilot run had ended on an
unconfirmed position request before entry. Cold, authoritative adoption
of this flight remains open. [Native transit and provider timeout](AUTHORITY-SUPERHIGHWAY-TRANSIT-20261125.md)

- **Cobalt** owns the Windows-to-Fedora flight check when the Windows operator returns. If profile 45841 is
  already occupied, the updated packaged launcher can reserve a fresh slot
  with `-FreshCompanyProfile`. Run it directly with the existing private
  connection file and assigned seat. A Windows-Python filesystem check under
  Wine preserved 45841, used 45850 and stopped on an exhausted pool.
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
  For the two-pilot flight use the explicit remote ship index. With automatic
  selection the cockpit fills three slots from the HUD and nearby eligible
  Company ships. Idle ships can take all three slots while another pilot is
  still within radar range. [Cockpit selection](../src/authority/md/company_cockpit_observer.xml)
  Use the uncrewed fighters for this flight. Current playback on the observing
  client excludes ships with an assigned NPC captain, even after another
  player's client has taken the helm. The earlier human test used uncrewed
  ships. [Retained-captain flight audit](AUTHORITY-COMPANY-FLIGHT-RETAINED-CAPTAIN-20261125.md)
- **Neon** completed the Windows GPU receiver run. OGE-507 moved 88.71 m
  on the recorded trajectory. The local ship and MHK-947, which remained
  selected in the HUD, stayed at their original positions. The private
  fixture used X4's real-time clock aligned to the Windows performance
  counter. Next, inspect the DLL and clock the packaged Windows sender
  actually selects, and exercise its real controlled-ship sample path
  when the Windows system is available. Cobalt owns the Internet flight.
  The shared ZIP contains DLL SHA-256 `6ad97e04…e2809e0`.
  The indexed receiver fixture used the older checked-in DLL
  `0fc98dba…f80b6ab6`. The packaged DLL's clock previously advanced
  under Wine. Capture which DLL the regular launcher loads on Windows.
  [Network DLL evidence](AUTHORITY-WINDOWS-NETWORK-BUILD-20261118.md)
  [Windows GPU report and harness revision](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/7d575ef1cd904acfc3d30e6cccd5d7ca1e4c8257/reports/AUTHORITY-COMPANY-INDEXED-HUD-WINDOWS-20261125.md)
  Neon previously delivered Windows observations of shield and hull damage from
  weapons. Shared damage across machines and boarding remain open.
  Before implementing damage replication, ask Lith7ium which simulation
  should settle a hit when two pilots are involved. [Native combat observations](AUTHORITY-COMBAT-OBSERVATION-WINDOWS-20261125.md)
- **Quartz** has the read-only investigation of the Company fleet command
  “Fly and Wait”.
- **Astra** owns server and client integration. A pilot can board a reserved
  ship as a passenger while its NPC captain finishes a trade. Lith7ium
  wants the client to wait for the server ship to clear its berth before
  flying. The server verified physical departure and the client responded
  to a simulated confirmation in separate native trials. Connecting these
  steps is outstanding. [Captain handover](AUTHORITY-COMPANY-CAPTAIN-TAKEOVER-20261125.md) ·
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

Two consecutive server purchases survived a save with matching cargo and
Company balance. A separate Linux run sold two water, credited 69 Cr,
and matched the ship and station inventories to the native transaction
journal. The sale used a disposable world and did not exercise a remote
pilot's trade menu or a save. An earlier one-unit sale has an unknown
outcome and must not be resubmitted. [Stock trade evidence](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md)

The complete strict Linux run on `8ff69b2d` counted 5,729 tests. All
5,704 executed cases passed, including the census checks with their original
fixture restored. The runner exited 1 for the 25 skipped checks. Thirteen
require Windows and twelve need local reference inputs. Cobalt has the
Windows selection for when the machine is available. [Strict Linux result and Windows checks](AUTHORITY-STRICT-CHECKS-20261125.md)

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
