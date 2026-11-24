# Shared-company ship inventory

The join inventory reads the verified format-3 checkpoint and correlates issued
ship indices with saved ship components. An entry requires both the binding
availability bit and the operational observation bit, component ownership
`player`, and an enclosing sector macro. A usable saved name or registration
code is included when available.

The catalogue describes saved bytes. Join availability depends on current
reservations and occupancy. Boarding and steering still require authoritative
live checks. The save observations do not establish cockpit availability,
crew behavior or a ship lifetime.

A read-only Linux check of the confirmed final server checkpoint from the
paired run `windows-registry-tls-pair-20261120-02` returned one entry.
Ship index `1970` had code `WOO-831` in `cluster_14_sector001_macro` and
no saved display name. This fixture exercised catalogue extraction only.
X4 was not started.

## Native client on the provider's final checkpoint

One disposable Linux X4 9.00 client loaded the checkpoint saved after the last
participant left the GPU-free provider trial. Its ID was
`checkpoint-34d4d67ad1104656b6d3b66719da56d7`, SHA-256
`0472ae62375afc12fd776d4cbd0d8b00e8ddeb84b11102fbee87fd12c6c56c71`.
The catalogue contained one operational company ship, index 1970, WOO-831.
It was the player's last controlled ship in this save.

A native client using the seat logic from `c7d95652` reported, in order,
the requested load, its inspected checkpoint marker, the full live inspection
of 10,082 registry entries, the matching registry header, a controlled-seat
receipt for 1970 and the observed pause. The seat and pause receipts were at
game time 85.47. The received index refers to the registry in the loaded
client world. The captured engine log SHA-256 was
`810ef435ef7e4b791041c94ff34dc510580642f37aa6818373e3f07356d13234`.
The input hash was unchanged after the run, and the process owner confirmed
cleanup. This run established local cockpit control on an already occupied
ship. It used a local offer and launched one X4 client.

A separate loopback TCP test sent two requests for the same company ship
before the host polled either socket. One connection acquired the ship. The
other received an unavailable response, and could reserve the ship after
the admitted connection closed. This transport test used a simulated engine.
