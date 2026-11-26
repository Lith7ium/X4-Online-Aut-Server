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
