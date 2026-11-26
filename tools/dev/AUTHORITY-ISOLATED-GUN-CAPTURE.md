# Private isolated gun capture

The development Mission Director observer in
[authority_isolated_gun_capture.xml](authority_isolated_gun_capture.xml) has
no regular overlay installation. It observes a single prepared HVC-420 /
KHI-630 fixture. It expects the occupied HVC to remain within 3 m of
(20,000, 100,000, −1,500) and the empty passive KHI within 3 m of
(20,000, 100,000, −650), in the same zone in Black Hole Sun V.

Send the `X4PrivateIsolatedGunCapture` Arm UI event with `window`, `epoch`,
`source_index=1073`, and `target_index=1074`, after the fixture reaches
these positions. The observer checks the exact Company registry
identities, current helm, source mounts, stock weapon macros, and local
ship, station, bullet and missile searches at both endpoints. The
eight-game-second scope includes three quiet seconds with no firing or
target health changes. Wait for the Armed event before firing. Keep the
target passive and use the existing short stock-burst fixture.

- Armed contains window, epoch, source and target indices, source and
  target native actors, initial shield and hull, start age and expiry.
- Fact contains window, monotonic sequence, native frame ordinal, game
  age, kind, epoch, source and target indices, actors, event component,
  attacker, damage, post-event shield and hull, weapon actor and macro,
  projectile actor and macro. Missing optional values use `-`.
- The Watermark UI request carries the window. ObservationWatermark
  returns window, later native frame ordinal, later game age and last fact
  sequence. It requires a positive native projectile search for a known
  projectile, a new age at least 25 ms later, and a fresh local range
  check. Frame ordinals compare typed native game age before text
  serialization. An equal displayed timestamp can represent either
  multiple events in one frame or rounded output.
- After refusal, disarm or expiry, Stopped returns window and reason.
  The observer covers one arm and 256 events per fixture session.

This wire carries observations only. No event certifies forwarding
eligibility. The bounded census and quiet interval do not establish
absence of environmental damage or projectile sources beyond the finite
range. The projectile query still needs native verification on this
fixture. A consumer would also need independent unmodified-primary
evidence from the stock weapon getter and an independently verified
isolated range. Keep the ordinary direct sender independent while this
is unresolved. A shield callback’s last attacker is supplemental and
does not authenticate individual damage or establish projectile identity.

The initial dynamic shield witness used a different diagnostic and
established subscription timing on one native fixture. This bounded
observer has schema and offline structural coverage only.

The installed base-game Black Hole Sun V map places the closest authored
resource-region centre 170.885 km or farther from the lane endpoints.
The conservative extent bound for the sector's resource regions is
40.5 km. The overlapping audio region definitions specify ambient sound.
None of the stock Cluster 06 region definitions referenced by the map
has a damage field. The prepared save adds a station-region marker
124.67 km from the nearer endpoint, linked to a station with a build
plot of at most 5 km along each axis. This establishes the authored
environment for these coordinates. Live objects, projectiles and
damage callbacks still need to be observed in the isolated native run.
