# Purchase and server-confirmed departure at BIM-169

The Linux pilot and GPU-free provider on Bazzite ran commit
`2029684a170fe48f94fdb71fce9bee29d1f5fdec` with the experimental undock
option enabled. Separate X4 instances used private profiles and loopback TLS.
The pilot selected WRN-302 (7354), with PYN-740 assigned as captain. A private
driver invoked the registered stock **Confirm** and **Undock** callbacks once
each. The test exercised the game callbacks programmatically.

| Event | Native observation |
| --- | --- |
| Purchase | The stock offer quoted 58.48 Cr per water. The provider settled two water for 116 Cr. The pilot received the matching receipt. Its local economy still showed 9,884 Cr and two water. |
| Pilot requested departure | The held Undock callback ran about 30 ms after purchase confirmation. The request identified the originally occupied external bay at BIM-169 in Grand Exchange I. |
| Provider executed departure | Its first read found the captain idle. A separate native request dispatched one stock order for that same ship, captain, station and bay. |
| Provider released the berth | At provider game age 352.03 s the undock event fired with a dock assignment still present. At 385.86 s physical dock, assignment, old-bay membership, storage and outstanding order had all cleared. Only then did the provider confirm the pilot's original request. |
| Pilot executed departure | After receiving confirmation, the client cleared its own hold. X4 reported undock start at client game age 385.910 s and completion at 389.917 s for the same ship and bay. |

The client-side measurement cue failed to take its final physical reading.
The run reached its timeout and the client reported a cleanup failure.
The cockpit screenshot and the two client events cannot independently
confirm every dock field after the maneuver. Physical mouse and keyboard
input, a busy captain at first check, a second bay and Windows remain
outside this run.

The client log identifies the missing reading. At game age 339.53 s the
instantiated measurement cue signalled itself and X4 reported no listener.
It never sampled again. The private trial now attaches a physical berth
check to the stock undocked event and checks every 500 ms until the ship,
old bay and assignment show separation. This client cue passed the X4
9.00 MD schema. The provider's optional berth sampler now resets its
delayed cue each second.

On last-pilot removal the provider paused and saved checkpoint
`checkpoint-fa46fdede9ef45248bd2db27a39e3ce5`.
An independent parse found WRN-302 undocked in Grand Exchange I with
the same AI captain, four water and 9,768 Cr. The sealed ledger retained
one new purchase and the exact receipt delivered to the pilot. Both
games and the private display exited.

| Evidence | SHA-256 |
| --- | --- |
| Confirmed final save | `0d46255c31380ae324aa3119f28b166fb80cd14d7117766942aa0da20b84b93d` |
| Provider and client request correlation | `40b5ba60cd3ccabf43a29c188e7086b0020feaadb2af01afd278b33c1bc1a7a3` |
| Independent save and ledger verification | `ad2bfd9d6a21d58a64227a07bb8ff2535b6ead7d34877e5fbbc69d6bf130ffc6` |

## Follow-up with the event-triggered client observer

One complementary native Linux run at `11f10348` restored a verified copy
of the original pre-purchase checkpoint
`checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca`. Client X4 loaded the fresh offer
`checkpoint-c89d95461cfb4f559cdb019b3b7888e7` with marker
`2a06c1d3fa413bd3285f51515e0a4951` and confirmed helm control of WRN-302
(7354). The private stock callbacks each ran once. The provider confirmed
two water for 116 Cr, a Company balance of 9,768 Cr and four water.

| Event | Native game age, s |
| --- | ---: |
| Client bound WRN-302 to the original BIM-169 external bay and invoked Undock | 340.073 |
| Provider confirmed the assigned captain was idle before dispatch | 343.20 |
| Provider witnessed the matching undocked event, with assigned dock still present | 353.73 |
| Provider verified physical detachment, cleared assignment and old-bay membership | 377.89 |
| Client cleared its hold on the confirmed receipt | 377.85 |
| Client reported undock start and end for the bound ship, station and bay | 378.119 and 382.132 |

Provider and client ages are from their respective X4 instances. The
corrected observer emitted the client start and end events. It emitted
no later physical readback before the supervisor deadline, and the
earlier listener error did not recur. The client exited cooperatively
with no owned processes remaining and reported a participant cleanup
error. These results leave the client's final dock fields unverified.

The provider completed the natural last-pilot save and exited paused
with no owned processes remaining. Both checkpoint pointers agreed on
`checkpoint-17f23f30eed444989f5f22cc65bbaaf5`. Independent parsing verified
the new marker, detached WRN-302 in Grand Exchange I, PYN-740's original
seed and pilot assignment, four water, and 9,768 Cr. The sealed ledger added
exactly the new purchase with the same receipt. The input save, manifest
and ledger matched their original bytes. The virtual display and host
game lock were released.

| Follow-up evidence | SHA-256 |
| --- | --- |
| Confirmed final save | `afec7163aa862c9989199eea26d3f88c3ce3fe92168a9869d6da17235015d9be` |
| Provider check, dispatch and receipt correlation | `54c02cc97485807d15b29a9cf2cda278486b55cc67b18964c6de152276745796` |
| Independent save and ledger verification | `89263a3dabd9adadc1ce9904278999f0e204f9f0be1bb6fdc326badca4d9b64a` |
