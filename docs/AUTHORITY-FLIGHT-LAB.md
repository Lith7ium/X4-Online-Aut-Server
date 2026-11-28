# Authority flight lab

The flight lab plays scripted flights through a simulated internet link into
the real receiving-cockpit playback modules. It scores what the cockpit would
show against the true flight, frame by frame. It needs LuaJIT and no X4.

Its results are synthetic evidence. They show how the playback code reacts to
late, missing and uneven samples. They do not show how X4 itself moves a ship
between writes. A real flight over the internet is still the check that counts.

## Run it

From the source root:

    luajit tests/authority_flight_lab.lua                       # lab self-checks
    python3 tools/dev/authority_flight_lab.py                   # score table, seed 1
    python3 tools/dev/authority_flight_lab.py --seeds 1,2,3 --wan speed_limit
    python3 tools/dev/authority_flight_lab.py --compare         # differences from the baseline

`tests/fixtures/flight_lab_baseline.json` holds the scores of the unchanged
code. `tests/test_authority_flight_lab.py` fails when the scores without any
`--wan` option drift from it.

## What is simulated

- **Pilot.** Five scripted flights: `close_turns` (100 m/s, weaving),
  `steady_345` (345 m/s straight), `travel_brake` (travel drive to 650 m/s and a
  hard stop), `turn_while_moving` (150 m/s, 30 °/s turn) and `fast_travel`
  (3,649 m/s). The pilot samples every 0.1 s, as `company_motion_source.lua`
  does.
- **Link.** Each direction delays every packet by a fixed time plus random
  jitter. A lost packet is resent after a timeout, and later packets wait behind
  it, as on TCP.

  | Profile | Delay | Jitter | Loss | Burst |
  |---|---|---|---|---|
  | loopback | 0 | 0 | 0 | 1 |
  | good | 30 ms | 5 ms | 0 | 1 |
  | home | 60 ms | 20 ms | 0.5 % | 1 |
  | poor | 120 ms | 40 ms | 2 % | 3 |

- **Transport.** Pilot to server and server to observer each send only the
  newest sample, then wait for its acknowledgement, as the movement client and
  service do. `tools/dev/authority_flight_lab_relay.py` checks this model
  against the real client and service through an impaired loopback relay.
- **Receiver.** The real cockpit modules, with a fake engine that applies each
  write on the next frame. Frames are 60 per second, with one seeded 0.25 s long
  frame every 10 s.

## The scores

| Column | Meaning |
|---|---|
| p95 m, max m | Distance between the shown ship and the true ship: 95th percentile and worst frame. |
| hdg p95 | Heading error in degrees, 95th percentile. |
| freeze s | Longest time the shown ship did not move while the true ship did. One 0.25 s long frame is always part of it. |
| refused | Corrections refused as too large. |
| jumps | Frames where the shown ship moved more than 100 m and more than twice as far as the true ship. |
| back | Frames where the shown ship moved backwards. |
| overshoot m | How far past the true stop point the shown ship went. |

## The playback option

`--company-cockpit-wan-playback` (PowerShell `-CompanyCockpitWanPlayback`)
turns on `wan = {speed_limit = true}` for automatic cockpit playback. It needs
`--company-motion` and `--company-cockpit-playback` and is off by default.

With it, a correction may be as large as the ship's speed times the time since
the last good sample or confirmed write, plus 0.25 s. The limit is never less
than the old 50 m and never more than 5 km. The same limit applies to the
sample-to-sample discontinuity check and to restarting a track after a
realignment. A refused correction ends in one realignment instead of a frozen
ship. In the lab `--wan speed_limit` does the same.

`company_hull_playback.lua` also exports `adaptive_delay`, a delay rule from
measured transit spread. Playback does not use it; see the report.

Results: [reports/AUTHORITY-FLIGHT-LAB-20261128.md](../reports/AUTHORITY-FLIGHT-LAB-20261128.md).
