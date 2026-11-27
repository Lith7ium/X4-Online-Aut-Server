# Flight lab: remote ship playback over a poor connection

Status: design, waiting for approval. Branch `contrib/cobalt-flight-lab-20261127`,
made from `trial/velocity-aware-playback-20261126` at `61d0d0fc`.

## Why

In the 26 November internet flight, each pilot saw the other ship move
unevenly and late. Its heading did not follow the turns, and it seemed to stop
soon after it entered travel drive. On Bazzite the receiving cockpit refused
127 reacquisitions in a row while packets kept arriving. The gap grew to
38.39 km.

Every existing movement test runs on one machine with perfect timing. None of
them can show what a home internet connection does to playback. Live flights
need several people and Astra's coordination, so each attempt takes days.

The lab reproduces a poor connection on one PC, without X4 and without a live
session. It scores how well the receiving cockpit follows the other ship.
Fixes are only proposed once they beat that score. The finished branch goes to
Astra as a measured change for the next human flight.

## What we expect to find

These are suspicions from the code and the report. The lab has to confirm or
reject each one.

1. Movement samples travel over TLS on TCP. When one packet is lost, every
   later sample waits until it has been resent. Samples then arrive late and in
   bursts.
2. Playback shows the other ship 0.15 s in the past (`INTERPOLATION_DELAY`). It
   refuses a correction larger than 50 m (`MAX_STEP`). At 345 m/s, a 0.15 s
   gap already exceeds 50 m. A single stall in travel drive can therefore
   freeze the ship for good. That would match the 127 refusals on Bazzite.
3. The 26 November build wrote position and kept the local orientation, so the
   heading could not follow. Version 4 samples carry attitude. The lab checks
   whether the human launchers use it.

## Goals

- Reproduce the 26 November symptoms with the current code under a realistic
  poor connection: freezes in travel drive, uneven motion, heading lag.
- Produce the same score on every run, on Linux and on Windows with `luajit`.
- Prove each fix by a better score, without a worse score on a perfect link.
- Keep the whole existing suite passing.

## Non-goals

- Target speed in the HUD and native velocity writes. That is Neon's work
  (`work/neon-cockpit-native-velocity*`).
- Radar and map icons, the Bazzite map rendering, and combat.
- Replacing TCP with datagrams. The lab measures how much of the error comes
  from TCP stalls. A transport change would be a separate proposal.
- Any X4 launch or live connection. Real-game checks wait for Astra's next
  flight.

## Anti-goals

- A score that improves while the real game does not. The lab drives the real
  Lua modules. A fake X4 only stands in for the engine. Each report says that
  its evidence is synthetic.
- Hiding error behind a long delay. A ship shown one second late looks smooth
  but is wrong. The score counts lag as error.
- Wire protocol changes that break mixing versions.
- Touching the player's normal X4 profile, saves or settings.

## Approaches

| | Approach | Trade-off | Decision |
| --- | --- | --- | --- |
| A | Simulated link inside the test suite. Flights pass through a link model into the real Lua playback with a fake X4. | Fast and the same on every run. It models TCP; it does not run it. | Chosen, stage 1 |
| B | Real movement service and client with a relay between them that delays and stalls bytes. | Real TLS and TCP, so it checks that model A is honest. Slower; timing can vary. | Chosen, stage 2 |
| C | Two real copies of X4 through the relay. | Closest to the game. Needs a Linux X4 server that this PC does not have. | Later, in Astra's next flight |

The user chose A, then B.

## Design

### Stage 1: the lab (Lua, runs in `luajit`)

**Flights.** Each flight is a script that gives the true position, velocity
and attitude at any time. The first five flights are:

| Flight | Based on |
| --- | --- |
| Close formation at 100 m/s with turns | The pilots' close-range view |
| Steady 345 m/s | The last remote velocity acknowledged on Bazzite |
| Travel drive up to 650 m/s, then brake | The 5.16 km travel run and its 216 m overshoot |
| Turning while moving | The steering run at `e6924289` |
| Travel at 3,649 m/s | The fitted travel maximum |

**Sender.** The sender samples the true flight at the pilot's sample rate and
builds version 4 samples in the format that `company_cockpit_playback.lua`
accepts. Capture time uses the sender's own clock, offset from the receiver's.
Velocity is left out when any component is above 5,000 m/s, as the real
sampler does.

**Link.** Each link profile gives a delay, jitter and a loss rate, with a fixed
random seed. A lost packet is resent after a retransmission timeout of at least
200 ms. As in TCP, every later sample waits behind it and then arrives in the
same burst. The profiles are:

| Profile | Delay | Jitter | Loss |
| --- | --- | --- | --- |
| Loopback | 0 | 0 | 0 |
| Good internet | 30 ms | 5 ms | 0 |
| Home internet | 60 ms | 20 ms | 0.5% |
| Poor internet | 120 ms | 40 ms | 2%, in bursts |

**Receiver.** The receiver is the module set from
`tests/authority_company_cockpit_continuous_playback.lua`: `motion_buffer.lua`,
`motion_playback.lua`, `company_hull_playback.lua`,
`company_hull_playback_x4.lua` and `company_cockpit_playback.lua`. A fake
engine applies each write on its next frame. Frames run at 60 per second, with
an occasional long frame. The lab runs ordinary and continuous playback, since
the human launchers can use either.

**Score.** For each flight on each link, the lab measures:

- distance between the shown ship and the true ship at the same moment: median,
  95th percentile and maximum;
- heading error: median and 95th percentile;
- longest freeze while the true ship moves;
- number of refused corrections;
- overshoot after braking;
- single-frame jumps longer than 100 m.

The lab prints a short table and writes JSON. The score for the current code is
kept as a baseline file. A test checks that the score is the same on every run,
and that each enabled fix keeps its gain against the baseline.

### Stage 2: real transport (Python)

A relay sits between `CompanyMotionClient` and `CompanyMotionService` in the
setup that `tests/test_authority_company_cockpit_playback.py` already uses. It
delays and stalls the byte stream with the same link profiles. It answers two
questions:

1. Do samples reach the Lua side when stage 1 says they do? If not, stage 1's
   link model gets corrected.
2. Does the Python client add delay or drop samples on top of the link, through
   batching or queue drains?

### Stage 3: fixes

Each fix is a separate commit with its own score. It stays off by default
behind a client option, as the project does for movement changes. Astra decides
whether it becomes the default. The first candidates are:

1. **Speed-aware correction limit.** The 50 m limit scales with the ship's
   speed and the time since the last good sample. Repeated refusals end in one
   bounded realignment instead of a permanent freeze.
2. **Delay that follows the connection.** The receiver measures how unevenly
   samples arrive. It sets its interpolation delay to cover most of that, within
   0.1 to 0.5 s.
3. **Newest sample wins after a stall.** When a burst arrives, playback moves
   towards the newest sample instead of replaying the backlog.
4. **Heading follows the samples.** Attitude is interpolated on the same
   timeline as position, if stage 1 shows that the launchers drop it.

## Files

- `tools/dev/authority_flight_lab.lua`: flights, sender, link, receiver set-up,
  score.
- `tools/dev/authority_flight_lab.py`: command line entry; runs the Lua lab and
  prints the table.
- `tests/authority_flight_lab.lua` and `tests/test_authority_flight_lab.py`:
  same score on every run, baseline and fix checks. They are hooked in like
  `tests/test_authority_company_hull_playback_x4.py`.
- `tests/fixtures/flight_lab_baseline.json`: the baseline score.
- `docs/AUTHORITY-FLIGHT-LAB.md`: how to run it and what the score means.
- `reports/AUTHORITY-FLIGHT-LAB-2026112x.md`: baseline against fixes, marked
  as synthetic evidence.

## Success

1. With the current code, the lab shows the freeze in travel drive under the
   poor internet profile. If it does not, the freeze has another cause. That
   finding is reported, and the plan is redone before any fix is written.
2. With the fixes, the poor and home profiles show no freezes. Their 95th
   percentile distance and heading errors drop clearly, and loopback is no
   worse.
3. The full suite has no new failures.
4. Astra gets one compact result post with the branch, the score table and what
   still needs a real flight.

## Left to the builder

- The pilot's real sample rate. Read it from the pilot source and the launcher
  options. Use 10 per second until then.
- Exact profile numbers, the long-frame pattern and the score thresholds.
- Which client options the 26 November human launchers used. Read them from
  the packaged launcher options in the trial branch.
