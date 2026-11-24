# Neon and Cobalt handover · X4 authoritative multiplayer

**Archived.** [Current handover for Neon and Cobalt](HANDOVER-NEON.md).

20 November 2026. Astra is technical lead on Lith7ium’s authoritative fork. Lith7ium approves major architecture changes. Silver develops his own fork independently. Astra’s goal is paused. Neon owns native NPC reacquisition after a sector change. Cobalt can work in parallel on what the client can observe through the map.

## Where to start

| Branch on [Lith7ium’s fork](https://github.com/Lith7ium/X4-Online-Aut-Server) | Contents |
| --- | --- |
| `experiment/authoritative-server-20261115` | Persistence and admission. The game run below used [58c07235](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/58c07235e7168df9f5693cd44bdb3efb286f1b34). |
| `review/neon-npc-reentry-20261120` | Neon's starting point. [05d9aa28](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/05d9aa2853c47d926c01a8b2fc6ac4894fc486f7) combines the host, participant and a loopback integration test. Sector re-entry is opt-in. |

On a separate machine, the following starts an independent working branch and runs the short test for cancellation followed by fresh alignment. Run it from the checkout root with the project's Python test environment active. On the existing development host the authoritative remote is named `fork`.

```sh
git clone --branch review/neon-npc-reentry-20261120 https://github.com/Lith7ium/X4-Online-Aut-Server.git x4-neon
cd x4-neon
git switch -c work/neon-native-reentry
PYTHONPATH=.:src:tests:tools/dev python3 -B -m unittest \
  tests.test_authority_source_latest_two_sectors.SourceLatestReentryLoopback.test_cancel_then_fresh_alignment_with_real_participant
```

## What passed in X4

One native Linux X4 server admitted **two successive, freshly launched Windows X4 clients under Proton** over local TLS. The bootstrap issued and bound 10,082 ship identities, of which 10,080 were operational. Each client inspected its full 10,082-entry register and confirmed resume. The server kept running while empty, paused and saved after each departure.

| Observation | First client | Fresh client after first departure |
| --- | --- | --- |
| Connection handle / load generation | 1 / 1 | 2 / 2 |
| Offered checkpoint | `checkpoint-eab7577c1afb4586b64d44d2883d0950` | `checkpoint-3a506a992f7b4ac4a8e8ba3807aa1182` |
| In-game clock, paused → running (s) | 6.580 → 10.933 | 15.579 → 19.887 |

Before the second download, the test matched the offer’s ID, size, SHA-256 and save marker to the first departure checkpoint. After loading, it matched the game’s reported registry and marker to that checkpoint. Both admissions used the same server process and world epoch. Final checkpoint `checkpoint-6c0ac4f73bfa4500acee7c2d63bb41c0` matched the second departure. The server verified three saves and finished paused. All child processes exited cleanly, with source files and protected inputs unchanged. The earlier run stopped before admission when simulation time advanced during register inspection. Pausing at the first valid frame fixed that observed failure.

Host evidence is in `.local/windows-registry-tls-pair-20261120-02/result.json` (SHA-256 `1b7662ad5a3a5df30652c5f306f98910ba428b715a41de54707fffd47f04897e`). The outer display result is `.local/registry-tls-display-pair-02/result.json`. The local source bundle for this paired test is `.local/neon-handover-20261120/private-runner-sources.zip`. Native reproduction uses Lith7ium’s licensed install and retained isolated checkpoint. Use the existing result when working on unrelated client code. [Registry checkpoints](../tools/dev/AUTHORITY-REGISTRY-CHECKPOINT.md) · [Server setup](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md) · [Windows client setup](../tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md)

## Neon · Reacquire the same NPC after a sector change

The owner approved one fresh alignment on sighting a known ship after an unobserved sector change. On the review branch, each affected peer prepares first. Only a capture requested after its prepare acknowledgement can align that peer. Losing visibility cancels preparation. Other peers keep receiving motion. All 131 focused tests across the affected modules passed. The integration case uses the actual network and participant code and a simulated game callback.

1. Start with the recovered test NPC **ENH-870, register index 1318** in its existing isolated fixture. Trace the sector and zone checks in [replica_motion_binding.lua](../src/authority/lua/replica_motion_binding.lua) and [client_motion_overlay.py](../src/authority/client_motion_overlay.py). Record its registry identity and lifetime before it leaves view.
2. Implement the opt-in X4 callback for prepare, cancel and align. Retire the old motion controller before moving the actor. Resolve the target sector and zone. On alignment, apply the fresh position and orientation to the same actor and resume motion with the sample’s velocity. Preserve the 50 m limit for subsequent corrections.
3. Accept the native experiment only when later, separate X4 readbacks confirm the same registry identity, target sector and pose, and later motion continues there. Also check cancellation before alignment and the other recipient’s uninterrupted stream. Record which checks ran in X4 and which ran over loopback. Keep the feature opt-in until the native result exists.

## Cobalt · Measure what the player can see on the map

Does X4 keep a ship in `GetMapRenderedComponents` when the player pans it offscreen? Clients need motion updates for ships the player can see on the map. The earlier probe observed only the player, and its camera missed the requested remote target. Start from `experiment/authoritative-server-20261115` on a separate working branch. The [map observation report](AUTHORITY-MAP-VIEW-NATIVE-20261118.md), [Lua sampler](../src/authority/lua/map_render_observation.lua) and [offline test](../tests/test_authority_map_render_observation.py) are the entry points.

1. Prepare the probe and its result checks offline. Use one known, radar-visible NPC in the player’s sector, distinct from Neon's ENH-870. Hold the selection, map filters and radar coverage constant.
2. For one later isolated X4 run, pan the same NPC from onscreen to offscreen and back. At each step record the NPC ID, actual camera readback, a captured map view, and raw list membership. Use the map images to establish whether the ship icon was on screen independently of the API under test. Coordinate use of the shared test machine with Neon.
3. Deliver a short evidence table covering the three views and a conclusion for that fixture. If the pan or visibility controls fail, record which failed. Leave server subscriptions and ship motion unchanged. A change in how the server selects ships requires a separate architecture discussion with Lith7ium.

## Rules for the shared universe

- The Linux host plays as a client beside a full X4 server. Save and pause an empty world. Resume after a client loads the marked checkpoint. Direct Internet uses TLS with a pinned server fingerprint and a shared access key.
- Players may occupy different sectors. One client owns high-attention simulation per area. When it leaves, another present client takes over or the server resumes responsibility. Extending ownership to a whole sector remains an open proposal.
- Outside a client’s high-attention area, send motion only for ships the player can actually see, including through the map. Superseded movement may be dropped. Include velocity and monotonic capture time. Confirm lifecycle events reliably. [Map visibility evidence](AUTHORITY-MAP-VIEW-NATIVE-20261118.md)
- Players initially share a company, fleet and account. The acceptance session includes fleet orders, trade, boarding, player-versus-player and NPC combat. Missions, spacesuits and ejected cargo have lower priority.

## Remaining acceptance work

Earlier telemetry covers one moving ship between two Linux X4 instances. This handover’s new result covers sequential reconnection on one machine. Concurrent players piloting distinct ships across sectors, actual Windows over the Internet, combat, trading, boarding and the complete regression suite still need passing results. Stable hosting on a rented GPU-free machine and its CPU budget remain unmeasured. Lith7ium prioritized clients pending a provider test. [Movement telemetry](AUTHORITY-MOTION-CLOCK-BASIS-20261118.md) · [GPU-free experiment](AUTHORITY-NO-GPU-20261117.md) · [Audit review and fixes](AUTHORITY-AUDIT-REVIEW-20261120.md)
