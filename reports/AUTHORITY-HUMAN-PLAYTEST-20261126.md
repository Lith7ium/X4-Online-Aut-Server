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
