# Adopting a real company pilot's flight on the GPU-free authority

On 24 November 2026 a native Linux X4 9.00 client flew WRN-302 sideways.
The authority moved the existing company hull 45.1711 m to the reported
position in the same sector. Independent Mission Director and UI reads
measured errors of 0.00052 m and 0.00000 m against the cockpit sample.
The authority paused on the last departure and confirmed a new checkpoint.
A fresh GPU-free X4 process loaded it and independently read the same position.

## Flight and observations

Both instances ran on one Linux host using private displays, software Vulkan
and separate disposable profiles. The client joined through the pinned TLS
endpoint on loopback and took the controls of WRN-302, saved registry index
7354, in `cluster_01_sector001_macro`. The server avatar stayed on XPJ-169
in Black Hole Sun V. The position operation was enabled only for the private
parked seed. All positions below are in metres in the ship's sector frame.

| Observation | Cockpit sequence | X | Y | Z |
| --- | ---: | ---: | ---: | ---: |
| Cockpit before the single strafe input | 4 | -332267.0000 | 24156.0000 | 380257.0000 |
| Cockpit during flight | 5 | -332286.1875 | 24156.0020 | 380252.9062 |
| Cockpit target and server UI readback | 6 | -332311.1875 | 24156.005859 | 380247.6250 |
| Server Mission Director readback | 6 | -332311.1880 | 24156.0060 | 380247.6250 |
| Last accepted cockpit sample, also read from the paused server | 17 | -332311.1875 | 24156.005859 | 380247.6250 |

The server accepted 17 cockpit samples. One stock Mission Director relocation
was independently observed. Before dispatch, the server resolved the company
ship from its saved registry and checked ownership, occupancy, docking and
sector. After dispatch, a separate request read the object again through
Mission Director and the UI. The final pause independently read the saved
ship's position and linked the observation to the verified checkpoint.

## Timing and scope

The 17 samples carried no velocity vector. Capture intervals ranged from
1.2955 to 1.8453 real seconds, beyond the sampler's one-second maximum for a
rate estimate. Independent finite differences over the two moving intervals
were approximately 12.63 and 16.89 m/s. These are offline averages. The
existing one-second rate limit remains in effect. This run demonstrated
adoption of a position. Smooth flight at a second cockpit, rotation, physics,
collision, combat, sector transitions and Windows over the Internet remain open.

In the first attempt the software-rendered client loaded the correct marker
but stopped during full registry inspection, before taking a seat or sending
input. The second attempt allowed the slow test client up to 300 s for the
same paused inspection of all 10,082 entries. Normal clients retain the
110 s limit. The second client's combined flight-and-rate diagnostic still
reported failure because the rate field was absent. The separate authority
receipts and matching original cockpit sequences establish the positional
result. The shutdown wrapper also reached its 210 s wait while the last
save was being finalized. The authority subsequently confirmed the save
and reported that its owned processes had stopped.

## Read-only cold restart

A fresh provider verified the digest of the final save, loaded the world,
checked the marker and all 10,082 registry entries, then resolved index 7354
to WRN-302 again. Its UI read matched cockpit sample 17 exactly. Its
Mission Director read was 0.0005194 m from that sample. The cold overlay
contained no relocation action. It opened no session for a player, recorded
zero engine control operations and published no checkpoint. Both providers
and the cockpit client exited. The original private seed remained bytewise
unchanged.

## Checkpoint identity

- Parked source save SHA-256 `68dc79959984dbd3499b7b6360bde5b0e24d5cbc845b49d24a908d043a7612ca`.
- Joining baseline `checkpoint-1de9aa6be24d44febc63241ecb5e5edc`.
- Final paused checkpoint `checkpoint-5a7879e60e4344e19ad035e9d098a2d3`.
- Final save SHA-256 `341d9aada6a0d764eec94c36ca11f6e5fb011111411b4b289cc330314ca0f47e`.

The private gameplay seed and connection settings stay with the operator.
