# Native server avatar parking and cold reload

The disposable two-ship format-3 world is still at the start of its HQ plot.
Its 1,385 station components include no player-owned station and no player HQ.
The operator approved a walkable player HQ when available and a protected
station until then. This test exercises the fallback on two separate native
GPU-free Linux X4 9.00 launches.

## Site selection

The installed game’s map definitions resolve Black Hole Sun V to
`cluster_06_sector002_macro`. Its universe generator places an Argon defence
platform (`arg_defence_cluster_06_sector002`) and an Argon trading station
(`tradestation_argon_01`) there. The verifier finds the instances in the
specific saved universe. Equipment counts exclude docked ships.

| Current save code | Station | Docks | Defence modules | Turrets | Shield generators |
| --- | --- | ---: | ---: | ---: | ---: |
| XPJ-169 | Argon defence platform | 1 | 11 | 88 | 64 |
| JZJ-119 | Argon trading station | 4 | 3 | 40 | 16 |

The platform is preferred because it is a fixed defence site with substantial
armament. The figures describe this checkpoint. The test did not measure its
survival over hours of play or simulate the loss and replacement of a station.

## What X4 confirmed

1. The first preflight stopped before any teleport or checkpoint. X4 rejected
   a Mission Director library declaration although the XML passed the stock
   schema. The fixed declaration follows native include-action semantics.
2. On the next launch, the live MD and independent Lua-side engine checks
   agreed on the dock, station, sector and disembarked avatar. X4 placed
   the avatar in `dockingbay_arg_s_01_macro` on XPJ-169. The two Company
   ships, WOO-831 (1970) and WRN-302 (7354), were operational and unoccupied,
   with the same codes, macros, registry bindings and sectors as before.
3. X4 saved the parked world. The save-byte verifier checked the avatar’s
   station/room ancestry and both Company ship identities. After stopping the
   owned processes the supervisor confirmed the new checkpoint. The two
   experiment cues were archived outside the reusable game extension.
4. A fresh server then loaded that exact checkpoint. The cold-verification
   overlay had no teleport action. Live readback again found the same
   defence platform and S docking bay, no occupied or controlled ship, and
   the two available Company ships. The verifier finished paused, created
   zero new checkpoints and reported zero remaining owned processes.
5. A separate read-only application exercise on the parked checkpoint used
   the production catalogue and reservation session with two in-memory peers.
   Each could reserve a distinct ship, and an attempt to take an occupied
   choice was refused. No player process, socket, admission or save was used
   in this reservation check.

The previous world had 10,082 issued and bound registry entries, 10,062
operational. At the same paused game time after relocation X4 reported
10,065 bound, 17 unavailable and 10,045 operational. Saved-byte checks and
the cold load confirmed these counts. An unavailable binding carries no
conclusion that its ship was destroyed.

| Evidence | Identifier or SHA-256 |
| --- | --- |
| Unmodified source save | `ae92930bd5bfa8eed14a1c93ecb50e706897226243c160544494995ce0c934f6` |
| Confirmed parked checkpoint | `checkpoint-9a29f775413040e4bae1ba950055e76d` |
| Parked save | `68dc79959984dbd3499b7b6360bde5b0e24d5cbc845b49d24a908d043a7612ca` |
| Native preparation result.json | `561167f6db8f1f0be8f2493c36a034a09b4f0a313a176733e4ddbaae6f1d337b` |
| Fresh read-only verification result.json | `b2b1d4e98c52b7fb0a5de0e67ada4804c824e43428529372e585231e38517af7` |

The ordinary provider can restore the portable parked save. The explicit
preparation experiment accepts the pinned two-ship source. The HQ path,
changing stations automatically during play, native Windows Internet play
and cross-platform position replication remain open. See the [operator
guide](../docs/AUTHORITY-SERVER-PACKAGE.md) and the separate [real cockpit
motion trial](AUTHORITY-COMPANY-MOTION-20261124.md).
