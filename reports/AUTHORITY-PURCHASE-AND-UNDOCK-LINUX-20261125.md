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
