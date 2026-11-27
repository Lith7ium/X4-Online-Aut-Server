# Linux and Windows human playtest, 26 November 2026

**Weekend release decision: hold.** Two people joined the same Fedora-hosted base-game world. BBS-493 flew on Bazzite and XNA-002 flew on Windows over the internet. Both pilots saw some movement at close range. Neither could use the session to follow the other ship reliably or confirm a hit from the other cockpit. The evidence below applies to build `97ee6898e5050fc08bdf8fdcc560df3d94859f44`.

| What the pilots tested | What they observed |
| --- | --- |
| Cockpit flight | The Bazzite pilot saw XNA-002 move unevenly at close range and then appear stationary shortly after it entered travel mode. The pilot estimated a separation of two to three kilometres. The Windows pilot saw BBS-493 move with substantial delay. The displayed orientation did not follow the other pilot's turns reliably. |
| Targeting, radar and map | The Bazzite pilot saw no speed for the targeted ship and no movement on radar or the map. The Bazzite map showed black and brightly coloured blocks across the map, controls and tutorial dialog. Windows rendered the map correctly. |
| Player combat | Both pilots fired. A shooter saw local hits, but the pilots could not verify incoming fire or corresponding shield loss from the target cockpit in play. |

## Where movement stopped

On Bazzite, X4 logged 183 successful position readbacks for XNA-002, including 79 while observing it through radar. These reads verify the ship's position inside X4. The pilot reported that the radar symbol itself did not move. The launcher recorded no map movement confirmations.

At 267.93 seconds of local game time, cockpit playback rejected a correction. On the next logged attempt to acquire the same ship, its local position was 238.32 m from the sample. The client rejected 127 such attempts. The largest discrepancy reached 38.39 km while packets continued to arrive. The 50 m check at reacquisition measures the gap between the local copy and the reported position of **one ship**. The recording does not give the exact distance between the pilots at the first refusal, or the two samples needed to reconstruct that refusal.

The tested protocol carries linear velocity. The last acknowledged remote vector on Bazzite had a magnitude of about 345 m/s. The observer's playback in this build writes position while preserving local orientation. It does not set X4's native ship velocity. Packet reception therefore cannot establish what speed or heading the pilot saw in the HUD.

## Where combat damage arrived

Of five direct Bolt Repeater contacts confirmed to the Bazzite shooter, the first two hit idle Company ship EST-929 and the other three hit XNA-002. The last individual receipt for XNA-002 reported 2,340 of 2,366 shield points and 3,600 hull. The confirmed Fedora save recorded BBS-493 as its last attacker. For the return fire, the same save recorded XNA-002 as BBS-493's last attacker and 2,262 of 2,366 shield points at the last damage event. Bazzite's cockpit bridge independently read back approximately 2,262 shield points on its own ship after applying a server update. These are readings at specific events. Shields can regenerate afterward.

Windows recorded its initial health and local shots. Both clients use the same code to request current server health, write shield and hull in X4, and read back the result. This path does not replay the incoming projectile. Windows did not retain a confirmed cockpit readback for the incoming damage, and its launcher failed to write the final session summary at normal exit. We cannot tell whether a reduced value reached the Windows cockpit. Both pilots closed their games deliberately. Fedora saved after the last departure, and the operator verified the checkpoint marker and file hash.

An offline regression also found that a delayed or duplicated local reset message could disable all subsequent cockpit health updates during a valid write. The client now ignores a reset it has already applied. A newer reset still takes effect, and X4 logs an unconfirmed write once with its reason. The Windows playtest did not retain evidence linking this ordering to the reported symptom.

## What would change the release decision

A new two-person Linux/Windows internet flight needs to show both pilots following each other's turns and speed in close flight, through increasing separation and travel mode. The target speed and the positions on radar and the map need to be usable, and the Bazzite map needs to render clearly. Each pilot needs to see the other's fire and confirm the resulting shield loss in their cockpit against the server record.

## Bazzite map follow-up

An agent reproduced the coloured blocks in stock Linux X4 on the same Radeon RX 7900 XTX, using a read-only copy of the human session's offered save and the old windowed test preset. The stock map showed the same corruption across its controls and the Priority Orders dialog. Chromatic aberration was already disabled in the preset.

With the pilot's normal graphics options copied into a disposable windowed profile, the same map rendered cleanly in stock X4. It also rendered cleanly with the 26 November multiplayer extension installed, experimental UI enabled and live network endpoints disabled. The single setting responsible is still unknown. This comparison covers map rendering. Motion of map icons during multiplayer still needs a live check. Interactive launchers on the development branch now import each pilot's graphics settings. [Launcher change](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/7ad298b2715622b0e1921130d0e69f2ab307690a)

## Stationary turn on the development trial

In a single native Bazzite X4 instance, the observer followed 18 synthetic version 4 samples for HVC-420, with KHI-630 occupied. X4 read back +179° yaw, then −179° across the short arc, then −160° yaw, +17° pitch and −23° roll. The largest angle error at those targets was under 0.000005°. Both ships held their XYZ positions, KHI-630 kept its original orientation, and HVC-420 held the final orientation for 5.47 seconds. The test exercised the normal cockpit playback code on a copy of a Company save. It did not exercise a Windows cockpit, an internet connection or two players supplying their own turns. A separate offline test exercised rotation during fast translation and the rejection of an unexplained lateral correction.

## Continuous flight after a long receiver frame

In the first isolated recovery experiment, the same radar contact could be placed once at the latest verified position when two synthetic reports left it 272.82 m behind. A later source sequence moved the ship a further 9.83 m. The 300 m ceiling belonged to that experiment. A ship travelling normally can move farther between two render frames.

The optional continuous playback in `190e6ecc` advances an already identified pilot's ship using its reported velocity and elapsed real time. Fresh positions correct the trajectory. In native X4, HVC-420 remained on the observer's radar through a 411 ms gap in playback updates. Its next ordinary position update moved it **571.84 m**, and X4 independently read the position back 8.35 ms later. A new synthetic report changed the flight, after which the ship travelled another **402.88 m**. After five seconds of silence, playback stopped on the next frame at 5.005 s. A later native read found zero further displacement. The observer's own KHI-630 stayed stationary throughout.

The test used one Linux observer and synthetic version 4 samples. It verifies visible-ship position reconstruction in X4. The native target-speed HUD, map icons, two-client transport and smoothness for human pilots are separate checks. [Offline regression](../tests/authority_company_cockpit_continuous_playback.lua)

## What X4 reports as target speed

In a separate copied-save experiment HVC-420 started stationary on KHI-630's HUD. One native velocity update along HVC-420's measured forward axis set it to 60 m/s, with constant-velocity override disabled. X4's stock target-details query, using the connection name resolved from the active HUD target, returned **60 m/s**. Over the next 2.016 game seconds, the ships' measured separation decreased from 3,000.00 m to 2,879.05 m. KHI-630 stayed stationary. An earlier 60 m/s lateral impulse moved HVC-420 by 120.63 m in 2.01 seconds. The target-details query with an empty connection name returned zero during the lateral run. The lateral run did not record the HUD's resolved connection name.

This establishes a way to set forward velocity in native Linux X4 and read the value used by the stock HUD. No pilot verified the text on screen, and the networked playback option in this report does not yet set native ship velocity.

## Stopping a ship with native velocity

Two runs on copies of the same save tested whether a receiver can move and turn a ship while X4 also simulates its velocity. After a forward impulse of 60 m/s, the observer moved HVC-420 sideways by 1.5 m and turned it through 12°. X4 kept the original sector velocity. Its stock target-speed field changed from 60 to 58 m/s as the ship turned. During a 0.404 s real pause, game time stood still. The ship travelled 10.041 m between two pose reads. Predicting that distance using elapsed game time missed by less than 0.001 m. Using elapsed wall time missed by 24.25 m.

Sending zero velocity on its own after five seconds made HVC-420 return to the last explicitly written position. The last moving read and the first stopped read were 252.41 m apart. In the second run the receiver first read the moving ship's actual position and heading and wrote that pose back in the same update. X4 confirmed the stop 8.1 ms later at the captured position, and three subsequent reads through 0.816 game seconds recorded zero further movement. The observer's own ship stayed still in both runs.

These measurements concern one uncrewed ship in an aligned zone on native Linux. Continuous network playback still uses position writes. Connecting the velocity setter to it needs to account for the receiver's game clock during physical movement and its monotonic clock for the five-second loss deadline.

A further copied-save run used the continuous receiver, 20 synthetic orientation-and-motion packets and a native change from 60 to 90 m/s. It took 783 position readbacks. When packets ceased, the ship advanced another 446.09 m before the five-second cutoff. The receiver requested the stop after 5.005 real seconds. X4 confirmed zero velocity at the captured position 8.25 ms later, and subsequent reads found no drift.

The original run failed its 0.03 m readback tolerance once. A frame spanning the speed change missed by 0.490 m when the receipt time was treated as the instant X4 switched velocity. Replaying the same trace, 782 stable frames had a maximum error below 0.00014 m. The change frame fell within the distances compatible with the old and new speeds and was left unconfirmed. The next independent read missed by 0.000026 m. The revised classification has been checked offline. It has yet to run in a live network session.

## Separate map marker on Bazzite

A private native map experiment displayed a separate cyan marker at two successive synthetic ship positions on the same sector plane. Screenshots of the two predicted 18 × 18 pixel rectangles contained 52 and 0 cyan pixels at the first position, then 0 and 53 at the second. After the sample expired both counts returned to zero. A wheel event over the marker changed the stock map zoom in two runs. In a third run the camera stayed at the original zoom until the marker expired. Reliable wheel pass-through remains open. Four internal map picks at the new zoom level located a fifth point under the stationary mouse within 0.001 UI pixels. The probe restored the mouse's original map coordinates in the same UI callback.

This run used a copy of the base-game save and the pilot's normal graphics options on Bazzite. In another native run, X4's map picker accepted planes at −2 km, +2 km and the saved pilot's height of +100.80 km. An independent point on each plane agreed within 0.002 UI pixels. The marker has yet to follow a network pilot on screen.

The integrated receiver accepted a selected-ship sample in a native X4 map run, but X4 refused the extra UI frame. A follow-up with the player's graphics settings confirmed three active map frames. Its screenshot was taken during the opening fade, so the test stopped before sending a sample. Neither run demonstrated a moving marker from a network pilot.

In a later run with normal graphics, HVC-420 stayed selected. X4 acknowledged two synthetic movement samples, registered distinct marker frames for them and removed the frame at the end. Neither expected rectangle showed a cyan marker. The screenshots taken after the two UI acknowledgements were pixel-identical to the baseline. A UI acknowledgement alone does not establish that the window has presented the frame.

The next test sampled the actual window after each X Present completion. The stock map kept HVC-420 selected. Two injected positions went through the regular local REMOTE/SEEN interface. All 47 sampled frames at A displayed the separate marker there. At B, two consecutive frames contained 54 marker-coloured pixels at B and zero at A, 78 and 91 ms after acknowledgement. After the explicit end, the first frame still showed B, and the following two frames were clear at 87 and 101 ms. Both positions were supplied by the fixture. A live remote pilot supplying the path over the movement connection remains to be tested.

On the trial branch the ordinary Linux and Windows launchers expose this experiment through `--company-motion --company-map-marker` and `-CompanyMotion -CompanyMapMarker`, respectively. It is off by default. The selected map marker expires after one second without a fresh sample. An explicit end or loss of selection withdraws it immediately from the UI. The cockpit retains its five-second flight prediction. Native Windows rendering awaits testing.

## Visible fire in the target cockpit

One isolated Linux cockpit faced a stock Bolt Repeater ship across an 850 m firing lane. The recording covered six control triggers with no effect and six triggers that played X4's cosmetic muzzle and impact effects together. In a 566-frame capture all six triggers produced a small lighting change on the cockpit frame 92–107 ms later. The footage did not establish a visible flash on the target. A larger apparent change in that part of the image coincided with the tutorial panel fading. Both ships kept 2,366 shield points and 3,600 hull, and X4 recorded zero weapon-fired, attack or damage events throughout. This form of local feedback can be displayed without applying a second hit. An incoming shot that a pilot can recognize during multiplayer combat remains outstanding.

A second run viewed the other ship's validated exterior weapon mounts from 850 m. The stock HUD was hidden so the crosshair did not mask them. All six cosmetic impact pulses produced a visible flash on the tested exterior mount 87–103 ms after the trigger. The six control windows showed none, and the separate muzzle-only arm produced no clear flash there. Both ships' health and the native fire and damage counters stayed unchanged. This establishes an exterior effect at a known mount. Showing incoming fire in the victim's cockpit over a network remains open.

A separate cockpit test played X4's stock shield-surface impact effect on the controlled ship, on a private save without a multiplayer connection. In a silent private recording, all three effect pulses produced an in-cockpit sound. The quietest active peak exceeded the loudest sham peak by 19.7 dB. Two pulses also produced a blue shield pattern across the canopy, peaking 72 ms and 67 ms after their triggers. The first pulse produced sound but no measurable pattern through 800 ms. One sham coincided with a startup fade. Both later shams showed no pattern. Native shield and hull stayed at 2,366 and 3,600 throughout and the game recorded no shots, attacks or damage.

The target client's current health message carries a global revision and health values. Only the shooter receives the confirmed hit ID and target index. The cosmetic experiment therefore does not establish delivery of each confirmed hit to the victim.

## Two real Linux clients at 35.66 km

Two separate native X4 processes on Bazzite joined the standing Fedora server over the LAN. BBS-493 and XNA-002 resumed at their saved positions in Black Hole Sun V, 35,663.72 m apart. Both cockpits recognized the other pilot and returned movement receipts. BBS-493's native radar read 35,656 m against a 40,000 m radar range. This trial used the older V3 position-and-velocity format so that the standing provider and existing invitations could be kept.

The later pilot received its save offer 240.56 s after requesting it. One observed part of that delay was 99.66 s between Fedora publishing its checkpoint and confirming it for use. Downloading and installing the verified 16.23 MB file took 0.53 s. Native admission followed 46.42 s later. Both X4 instances were in their seats before the instrumented throttle attempt. The local input runner exited before any displacement was measured, so this run establishes a distant radar contact and a later join only.

On this older Fedora build, the later-join path validated the same checkpoint at several stages. The client polled twice after a not-ready reply, with only 0.1 s of deliberate waiting each time. A subsequent optimization on the trial branch combined the two full passes used to verify the saved marker when its reference table fits in memory. On one of the downloaded archives, a paired Linux measurement reduced that check from 6.33 to 2.94 s wall time. Both versions verified the same digest. Total join time with the change still needs measurement.

After fixing the input runner, a second flight began at the same measured separation. With ordinary thrust, BBS-493 travelled 3,500.57 m. The pilot requested braking at 3,039.43 m and the source later reported 0.385 m/s. In XNA-002's cockpit, X4 returned 42 native readbacks of BBS-493. Its displayed ship passed 1,235 m, 2,029 m and 2,828 m from its starting position before reaching 3,500.58 m. XNA-002 stayed within 0.001 m of its own saved position. The pilots ended 34,795 m apart.

Both instances also measured the distance between the two ships with X4's own distance query, about every 0.4 s. For 51 observer readings during the commanded flight, the nearest pilot reading on the shared Bazzite monotonic clock was within 0.167 s. The median absolute difference in reported range was 16 m and the largest was 32 m. Both eventually reported 34,792 m. This comparison includes sampling delay and measures only the distance between the ships.

These reads establish continued movement of the correct ship in native X4 at far radar range. The 0.00070 m maximum readback discrepancy measures how closely X4 applied its last local write. End-to-end flight accuracy, frame smoothness and movement of the rendered radar icon still need separate measurements. Both clients ran on Bazzite in separate isolated displays. Travel drive, V4 attitude and a Windows internet observer were outside this run.

The two ships started inside each other's radar range. For automatic radar discovery, the server supplies active pilot IDs and the client checks its local ship's sector and radar range before subscribing. The cockpit observer also excludes ships with an assigned NPC pilot. A ship returning from beyond radar range with a stale local position, and a remote player who retained a Company captain, require separate flight tests. [Cockpit selection](../src/authority/md/company_cockpit_observer.xml) · [Movement subscriptions](../src/authority/company_motion_service.py)

After the last pilot left, both Fedora checkpoint pointers agreed on `checkpoint-0bfdf9b07aa848d089df679fe298478c` (save SHA-256 `8da5731e10a8d1439b58f829d0caf2526a597a7929a92723c66c8ffa73048d55`). The saved BBS-493 position was within 0.001 m of both its final acknowledged position and XNA-002's independently received position. XNA-002 stayed at its saved starting point. The original human-session checkpoint and the preflight checkpoint retained their verified hashes. A cold restart from this newly saved flight was outside this test.

Both games exited and released their native resources. Both Linux launchers reported a participant cleanup failure. The cleanup code did not recognize the valid continuous-playback option when removing the local motion configuration. A regression fix on the trial branch covers this generated format. These clients also reported the expected paused world marker; their stock launcher leaves its independent exact-loaded-file proof flag false.

## Stationary turn across two V4 clients

A software-rendered Linux provider and two native Linux clients ran in separate processes on one host using an isolated copy. The clients began 4.24 km apart. The BBS-493 pilot applied stock steering for 0.18 s while stationary. Its reported yaw changed by 1.83°. XNA-002 read back three rotations of the same ship on radar while its position stayed fixed. For the largest of these samples X4 applied 0.0312974900 radians against a requested 0.0312974893 radians. The test driver required a 0.035 radian turn before engaging travel drive and ended without issuing the travel command. This run establishes transfer and application of a small rotation. Travel flight and the visible smoothness of a larger turn still need testing.

## Travel drive and braking with two V4 clients

In a fresh copy, BBS-493 steered left by 29.6° and engaged the stock travel drive. Its native HUD reported travel active in 70 samples and a peak of 651 m/s. The largest velocity acknowledged from the pilot’s timed position samples was 582.58 metres per real second. The pilot requested braking at 3,345.84 m and came to rest 4,980.57 m from the starting point. The private runner also required 850 m/s for its high-speed check. That threshold was not reached, and the overall runner timed out.

XNA-002 recorded 146 native position readbacks for BBS-493 on radar. Its local representation went **216.09 m beyond the pilot's final position**. It returned to within one metre in about two seconds and eventually matched the pilot's final XYZ exactly. This is a gameplay-relevant braking error, even though X4 applied each individual position write to within 0.002 m. The target was unselected in all stock HUD checks. This run establishes travel-mode movement across almost five kilometres on Linux loopback. Target-speed display, high-speed WAN flight and human-rated smoothness remain open.

## Offline validation

The Linux run of `tools/dev/check.py` on `86327b21` discovered 6,057 tests. 6,031 passed and 26 were skipped. There were zero failed assertions and zero test errors. The strict runner returned exit code 1. Fourteen tests require Windows. Twelve require additional reference files or a released tool absent from this checkout. The subsequent cockpit-health and saved-marker fixes passed their focused checks. The full-suite result applies to the named revision.
