# Silver's lockstep build and the authority fork

The 24 November review used upstream `cea737b1` and the Windows installer
workflow for `d4b964a6`, which produced a 43,578,083-byte artifact. The combat
review on 26 November used Silver's Windows prerelease `rk16`, published as
`playtest-2026-11-25`. The tag and upstream main pointed to
`61d7ed6b622381033d1a4031d5beb221bf4b17b4` at review time. The upstream
game runs below come from Silver's reports.
[Installer build](https://github.com/Unofficially-Online/X4-Online/actions/runs/45324840981)
· [24 November revision](https://github.com/Unofficially-Online/X4-Online/commit/cea737b1)
· [rk16 prerelease](https://github.com/Unofficially-Online/X4-Online/releases/tag/playtest-2026-11-25)

| Capability | Evidence on Silver's branch | Use on the authority fork |
| --- | --- | --- |
| Windows onboarding | Two physical Windows PCs at one site used the CI MSI, the game's Multiplayer menu and a join code. They loaded together and stayed in sync past tick 2,154 over a VPS relay. | Reuse Steam discovery and the user-facing flow. Keep our pinned, direct Linux-host connection. [Product run](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/NETPLAY.md#L647-L689) |
| Mid-session join | A second X4 instance on one Windows laptop joined at tick 3,324. The 188 MB join package compressed to 27 MB. The host paused for about two minutes. One controller drift is fixed; another is open. | Publish a verified temporary save into the final slot. Our host can pause during joins, and its checkpoint remains the source of truth. [Drop-in](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/NETPLAY.md#L691-L703) · [Controller findings](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/reports/LOCKSTEP-FAST-JOIN-20261123.txt) |
| Trade and fleet orders | Both test pilots bought and sold 20 food rations through stock menus. Holds and account agreed for 8,174 ticks. A separate test issued remote fleet trades over 9,287 ticks. The orders tests checked actual ship behaviour after Move, Follow, Protect, Attack and Dock. | Use stock menu callbacks and physical end-state checks. Submit each accepted financial or fleet command once to the authoritative X4 world. [Playtest results](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1462-L1508) · [Scenario code](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/tools/dev/action_tests.py#L304-L431) |
| Boarding | A wing destroyed a Xenon ship, and marines claimed an ownerless scout. Hostile boarding was listed among the unrun actions. | Reuse crew and ownership observations. Hostile boarding between participants needs its own test. [Combat and claim](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1495-L1536) · [Remaining actions](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1778-L1785) |

The sector and movement work offers a targeted test for our next handover.
Silver exercised players separating into different physics scenes and
rejoining. His join report traced a medium ship's drift to the *starting
rotation of its movement controller* even though the positions initially
agreed. For our server-led motion, compare orientation, angular velocity
and the first frames after an ownership change alongside position and
velocity. The lockstep replay and process-local controller records depend
on a shared simulation tick. Adopting those records would require a separate
architecture decision.
[Sector observations](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1115-L1139)
· [Join fix](https://github.com/Unofficially-Online/X4-Online/commit/cea737b1)

## Combat review and authority decision, 26 November 2026

- **Fire and aim in `rk16`.** Each client replays the slot's trigger on the
  ship's active primary group through X4's native weapons. Target selection
  replays a portable object reference. Manual aim carries a point in the
  ship's frame and aim flags. Secondary fire uses the slot's soft target and
  the stock `shoot_at` controller. These are reference points for observing
  the native shot and matching it to a ship on the authority fork.
  [Fire](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_pilot_fire.h#L319-L374)
  · [Target](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_ui_replay.h#L767-L790)
  · [Aim](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_pilot_aim.h#L20-L50)
- **Hit authority in `rk16`.** Hits on NPC ships stay native on each peer.
  For a keyed single-hit projectile against a player-slot ship, `rk16`
  defaults to the victim's claim. Every peer validates the claim against
  its copy of the projectile and target and applies the accepted outcome
  through X4's `OnHit`. Beams, piercing bullets and shots without portable
  shooter identities stay on their native paths. The relay exchanges input
  records and compares state digests. This claim path relies on peers
  simulating the same projectile identities, world and ticks.
  [Defaults](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/tools/dev/native_gate_defaults.lua#L213-L221)
  · [Hit handling](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_hit_native.h#L24-L56)
  · [Validation](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_hit_claims.h#L268-L290)
  · [Relay](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/tools/dev/native_gate_relay.py#L334-L390)
- **Scope of upstream tests.** An earlier scripted PvP fixture recorded 70
  applied hits across 6,300 ticks, with six deaths and five respawns. Its
  ships had no missile launchers. In the `rk16` two-instance test on one PC,
  player 2's turrets hit in enemy, fighter and defend modes. In “attack my
  current target,” they acquired the intended target but logged no hit.
  [PvP fixture](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/reports/LOCKSTEP-CENSUS-20261028.txt#L1348-L1358)
  · [Turret results](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/docs/PLAYTEST.md#L1523-L1586)
- **Close-range regression.** Silver records an earlier player firing at a
  Terran corvette. A position predicted ahead of the visible projectile
  missed the hull on all 50 native hits. For our fork, include a point-blank
  burst alongside a deliberate miss and keep X4's observed hit as the input.
  [Upstream diagnosis](https://github.com/Unofficially-Online/X4-Online/blob/61d7ed6b622381033d1a4031d5beb221bf4b17b4/src/native/engine/lockstep_hit_native.h#L30-L44)

The authority fork will take the hit confirmation for a player-fired shot from
the shooter's X4 instance, in both PvE and PvP. For an NPC-fired shot, the
simulation currently assigned to that NPC confirms the hit. The central world
authority verifies the reporting simulation's current assignment and the
registered attacker and victim, then applies each accepted impact at most once
and publishes the resulting state. For simultaneous attackers, server X4
applies confirmed impacts in arrival order to the target's current shields
and hull. Both shots count even when both pilots observed the same local
remaining shield value. Retries of the same impact must resolve to
the original result. A recipient's local hit observation can support diagnosis.
The impact identity must survive retransmission and use the shared registry
and a shot or impact identifier in place of process-local X4 handles.

The fork has measured native damage and projectile attribution in an admitted
Linux cockpit. A separate paused provider applied one recorded shield value and
verified it again after a cold load. Live impact submission, duplicate handling,
and matching health in the provider and both human cockpits remain to be tested.
[Native combat capture](AUTHORITY-REGISTERED-PILOT-COMBAT-20261125.md#occupied-pilot-hit-through-the-regular-linux-capture)
· [Shield replay](AUTHORITY-SHIELD-REPLAY-20261126.md)

## What the LAN builds and Silver's rk18 contribute

**Damage capture.** In LAN 7.4.1, eight native batches applied 16 hits from a human Windows pilot to the Linux host in 0.37 game seconds. Both pilots confirmed the damage in that direction. The LAN 7.5 report also records human PvP damage and destruction of an NPC. The archived implementation (`4078f44`, `x4lan_shot_capture.xml`, cues `Shield`, `Hull` and `Classify`) counted actual shield and hull damage events. It excluded same-frame collisions and the hull overflow of a counted shield hit. A cumulative count per shooter, victim and generation allowed a receiver to acknowledge an already applied count. Its attacker check used the target's last attacker in a controlled combat setup.

The authority fork's recorded duel shows why the event choice matters. The firing ship saw 47 distinct shield debits of 26 points, from 2,366 to 1,144. X4 recorded those debits at 26 different game times, with up to four at one time. Each callback named the firing ship as last attacker. The direct attack notification occurred once, at the first debit. The production sender forwarded that one contact. A tracker keyed only on game time would merge real hits. Adding the direct notification to all 47 debits would double-count the first contact. Multiple shooters or environmental damage require their own attribution evidence before these damage callbacks can drive the shared world. [Authority duel](AUTHORITY-TWO-COCKPIT-COMBAT-20261126.md)

**Motion.** The archived LAN player path (`coop_game.lua`, `applyVelocity`) sent a timed position, orientation and their rates. It also set the observed ship's *native linear velocity* to the derivative of its displayed path and released that override on loss. In a separate native Linux experiment with 24 shots, an X4 ship moving sideways by position updates at 100 m/s had zero native speed. Its projectiles had a median lateral speed of 1.558 m/s. With the native 100 m/s velocity applied, their median was 99.549 m/s. The validated setter used a free ship in an aligned zone, sector and cluster. LAN 7.5 still had human reports of highway jumps. Its NPC position path did not perform the same native velocity write.

Silver's rk18 still interpolates between two locally simulated poses with a common parent and compares predictions against the next measured pose. We brought the latter comparison into our replay tool. Over 42 comparable intervals of our recorded gate flight, holding the previous position missed the next capture by 195.69 m on average. Projecting with the **previous** reported velocity reduced the mean to 4.96 m and the maximum to 17.32 m. The first interval had 146.90 m of travel before a prior velocity was available on the admitted connection. These are source trajectory errors. Cockpit readback, native velocity, projectile behaviour and perceived motion are separate measurements. [Silver's renderer and predictor](https://github.com/Unofficially-Online/X4-Online/blob/8412a230/src/native/engine/lockstep_render_display.h#L243-L273) · [Authority replay tool](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/86e32912/tools/dev/authority_velocity_receiver_windows.py)

## Changes tested on the authority fork

- The isolated client now enters the trade screen through the active Trade
  button on X4's dock menu, using Silver's interaction sequence. In native
  Linux X4 the registered button opened the stock basket for WRN-302 at
  BIM-169. Selecting six water at 58.48 Cr showed 350 Cr in the basket;
  the registered Confirm callback produced that exact unit quote and quantity.
  Both callbacks ran programmatically. The pilot fixture left its physical
  hold empty and its balance at 10,000 Cr. Capture SHA-256
  `dc5a90531afae54a3efa4b5ca622ce1627e29bc3aedf2cf53eae3fc625c6d31c`.
- The authority Windows launcher can reuse Steam's X4 discovery when the
  player omits the game path. The explicit game directory still takes
  precedence. Package/CLI tests cover alternate libraries. The newly built package also found a second Steam library
  in the Linux test fixture. Native Windows execution is pending.
- The normal authority join path stages a verified temporary file beside
  the destination. The supported no-clobber publication uses a hard link on
  Unix and a rename on Windows. A Unix filesystem without hard-link support
  keeps the existing fenced exclusive-copy fallback. On Linux, staging the
  real 30,317,296-byte checkpoint yielded the original SHA-256, with no
  partly written slot at the I/O observation points and no temporary file
  left behind. Native Windows staging remains to be exercised.

The independent provider consumed the new dock-menu capture with a price of
58.48 Cr. Its own live offer was 58.77 Cr. It returned both prices in a
matched refusal, with the account still at 10,000 Cr and cargo at zero. This
exercises the opposite price direction from the earlier native 58.77-to-58.48
refusal. Both used recorded input. There was no live participant connection.
Provider result SHA-256:
`5f1bfd0c6e5af105d42630ea230b250b4f38145bd367a5037c1b9bef5567b3bb`.

Silver's tests also name a concrete next fleet experiment. Select a free
Company fighter, issue a stock move order and compare the requested destination
with its observed arrival. Follow with a stock Attack order against a named NPC
and measure its shields or hull. The acceptance evidence must come from the
authoritative game and both participants' observations.

## Internal packages

Both archives were built from `69268cc6ee41992a016fe5332cbd4e40af034351`.
The archived source bytes matched Git. The Windows ZIP includes the previously
verified project network DLL. Clients supply their own licensed X4 installation
and private connection settings. Cobalt's earlier Windows preflight applies
to the earlier package. This revision has no Windows Internet playtest yet.

| Package | Bytes | SHA-256 |
| --- | ---: | --- |
| X4-Windows-Player.zip | 2,595,632 | `2cf7d999f579300e1632b334208e5c5a32e82a9dd0dcbc7b4dff1ddc7bfca399` |
| X4-Linux-Provider-and-Player.tar.gz | 1,409,000 | `217a548b59aaa2363f5d33e896c767eb73c7379feb2d582787aa58ec5cb46ec0` |
