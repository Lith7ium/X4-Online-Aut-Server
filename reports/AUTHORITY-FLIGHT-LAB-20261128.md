# Flight lab report, 28 November 2026

Branch `contrib/cobalt-flight-lab-20261127`. All numbers come from the flight
lab ([docs/AUTHORITY-FLIGHT-LAB.md](../docs/AUTHORITY-FLIGHT-LAB.md)) and the
relay check. They are synthetic. No X4 ran for this report.

## Summary

- Ordinary cockpit playback froze the shown ship for good after one refused
  correction. In the lab this happened on every link, loopback included, at
  345 m/s and faster.
- With `--company-cockpit-wan-playback`, ordinary playback no longer freezes.
  On the poor link the longest freeze fell from up to 19 s to 0.25-0.5 s. The
  95th percentile error at 345 m/s fell from 6.5 km to 364 m.
- What remains is lag. The shown ship trails by each sample's own transit time,
  which reaches 0.25-1 s on a poor link because the transport waits for an
  acknowledgement per sample.
- Continuous playback never froze and is unchanged by the option.

## Reproduction gate

With the unchanged code, `steady_345` in ordinary mode had a 0.25 s long frame
at 0.62 s. At 345 m/s the next correction was over 50 m and was refused at
0.883 s (`correction_out_of_bounds`). The refusal does not mark the ship for
reacquisition, so every later sample was refused as well and the shown ship
stayed where it was for the rest of the flight.

## The real transport

The real movement client and service ran through two impaired loopback relays,
100 samples at 10 per second, with no X4:

| Link | Delivered (real) | Delivered (model) | Extra delay beyond the link, median / p95 | Longest gap |
|---|---|---|---|---|
| loopback | 100 | 100 | 8 / 9 ms | - |
| good | 100 | 100 | 5 / 10 ms | - |
| home | 56 | 57.8 | 92 / 156 ms | 0.42 s |
| poor | 23 | 24 | 97 / 327 ms | 0.91 s |

Each leg holds one unacknowledged sample and sends only the newest one. On a
home link two of every five samples never reach the observer. The median
latency on home was 247 ms (real) against 250 ms (model). The lab models this
path, and its tests keep the model and the real code in step.

## Scores before and after

Median over seeds 1-3 of the 95th percentile position error (m) and heading
error (degrees), longest freeze (s), and the totals over the three seeds of
jumps, backward frames and refused corrections. "Before" is the unchanged
code, "after" adds `speed_limit`. Ordinary mode:

| Flight / link | p95 m | hdg p95 | freeze s | jumps | back | refused |
|---|---|---|---|---|---|---|
| steady_345 / loopback | 6400 → 58 | 0 → 0 | 19.4 → 0.25 | 0 → 0 | 0 → 0 | 582 → 0 |
| steady_345 / home | 6442 → 130 | 0 → 0 | 19.4 → 0.25 | 0 → 0 | 0 → 51 | 342 → 0 |
| steady_345 / poor | 6489 → 364 | 0 → 0 | 19.4 → 0.25 | 0 → 12 | 0 → 40 | 133 → 0 |
| travel_brake / home | 1185 → 198 | 0 → 0 | 6.8 → 0.25 | 0 → 1 | 32 → 47 | 128 → 0 |
| travel_brake / poor | 4604 → 364 | 0 → 0 | 10.0 → 0.5 | 0 → 9 | 5 → 29 | 60 → 0 |
| close_turns / home | 40 → 40 | 10.9 → 10.9 | 0.25 → 0.25 | 0 → 0 | 74 → 74 | 0 → 0 |
| close_turns / poor | 1010 → 105 | 53.0 → 27.6 | 15.5 → 0.25 | 0 → 0 | 41 → 59 | 76 → 0 |
| turn_while_moving / poor | 184 → 122 | 28.2 → 25.0 | 9.7 → 0.25 | 0 → 0 | 12 → 28 | 26 → 0 |
| fast_travel / home | 44838 → 1178 | 0 → 0 | 18.9 → 0.25 | 0 → 50 | 0 → 56 | 366 → 0 |
| fast_travel / poor | 44000 → 2464 | 0 → 0 | 17.2 → 0.3 | 0 → 54 | 2 → 48 | 87 → 0 |

Loopback and good links are no worse in any row. Continuous mode gives the same
numbers before and after, for example `steady_345 / poor` 362 m and
`close_turns / poor` 93 m and 27.6°. Each freeze includes the lab's scripted
0.25 s long frame.

The ship now moves where it used to stand still. The cost is more jumps and
backward frames in ordinary mode: the shown ship realigns or catches up in
steps after stalls, instead of stopping.

## Fixes tried and not kept

- **Delay that follows the connection.** Setting the interpolation delay from
  the measured transit spread (0.10-0.50 s) removed most backward frames
  (`close_turns / home / ordinary` 74 → 11) and jumps (`fast_travel / home /
  ordinary` 50 → 2). It also added lag. The 95th percentile errors rose on home
  and poor, for example `steady_345 / home` 121 → 168 m and `fast_travel /
  home` 1193 → 1771 m. The rule stays in `company_hull_playback.lua` as
  `adaptive_delay`, unused.
- **Newest sample wins after a stall.** Not needed. The transport already sends
  only the newest sample, and the buffer never raises its lag after a stall. A
  0.6 s stall followed by the whole backlog scored the same as a smooth link.
- **Heading.** Version 4 samples already carry attitude, and playback follows
  it. The 5.0° heading p95 for `turn_while_moving` on loopback is the same
  0.15 s interpolation lag as the position.

## Still open

- **Lag on poor links.** Playback never predicts past the local time since a
  sample arrived. That is a deliberate guard against transit jitter. With
  stop-and-wait transport each sample's transit is 0.25-1 s on a poor link, so
  the shown ship trails by that much and steps forward when a faster sample
  arrives. The next gain is either a transport that does not wait for every
  acknowledgement, or prediction up to the measured transit. Both need a design
  decision first.
- **One refusal still closes a slot for good.** If a sample is refused while a
  write is still unconfirmed, the slot closes (`closed_uncertain`). That safety
  rule is unchanged. The speed limit only removes the false refusals that
  triggered it in the lab.
- **The lab cannot show X4's own motion.** The fake engine applies each write
  on the next frame. A real internet flight with the flag on is the next check.
- **Live checker.** `tools/dev/authority_two_x4_resume_live.py` does not yet
  accept `cockpit_wan_playback=true` in the config it checks. Add it before the
  first live trial with the flag.
