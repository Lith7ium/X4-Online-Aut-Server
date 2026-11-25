# Registered NPC observations across two Linux X4 instances

A GPU-free Bazzite provider and a regular Linux pilot loaded independent
copies of one vanilla world. The pilot flew HVC-420 (registry index 1073).
The observation target was MFQ-419 (index 943), an Antigone freighter with
a native docking order. The input checkpoint was
`checkpoint-39a02fb17cc24fad896b3b578f9fdd08`, SHA-256
`972b596cebcac38396321477287b84e5bf8da0a4b60ec705717be061257f6c01`.
The test used the regular authenticated pilot connection and checked the
local cockpit's native radar visibility. A pilot acknowledgement records
which server pose reached that cockpit. It does not move the pilot's
copy of the freighter or measure where X4 rendered it.

| Fork revision | Provider | Observing cockpit | Confirmed last-pilot checkpoint |
| --- | --- | --- | --- |
| `5d2299f3` | Resolved the NPC after load. The motion guard rejected the running world before publication. | A nonempty view and 272 own-flight acknowledgements. Zero NPC poses. | `checkpoint-a69fd5e1661d41a18fc0137fff549907` |
| `1e8b5157` | Published the first pose. The source retired with `source_expired`. | Native radar saw MFQ-419 at 13,364 m with a 40,000 m range. Confirmed one identical pose and no velocity. | `checkpoint-00228a267c5a45ce8222b6903e403b92` |
| `3e005330` | Captured the second pose 2.199 s after the first. The source had expired 182 ms earlier. | Confirmed sequence 1 with matching actor binding and pose. | `checkpoint-c607719d2dd8470cb7fb984c79af31aa` |
| `24284592` | Published 25 native positions. No velocity met the rate interval. | 18 exact confirmations, 7 distinct positions, 17.65 km displacement. | `checkpoint-5208c84259934e7fbcb9d6e31abdf62f` |

In the first run the provider checked the identifier for a pending join
offer after resume. That identifier expires by design. The corrected
guard checks the coordinator's running checkpoint and its marker against
the independently scanned join save and current seat reservations.

In the second run the saved reference changed from `0x418ee` in the
original world to `0x41962` in the join offer. Native X4 resolved
`0x41962` for MFQ-419 after loading the offer. The cockpit confirmed
sequence 1 at (175818.84375, 305.3389892578125, 5660.52490234375) metres.
Registry epoch, index, generation, sector and both capture clocks matched.
The first frame contains no velocity. The two-second source expiry
occurred before either X4's sector-loss observation. The old receiver
checked expiry before draining its socket. The record cannot establish
whether another packet had already arrived.

The final save hashes for these runs were
`a1d1e33eab198a85d9eba287489e86af69cfee3c731889f39d864038841cd660`
and `87c2197bf1de6feb14649a79b73484b334e9d937141b94662b23f94212b90bfd`,
respectively. Both providers paused and saved after the pilot left.

In the third run the receiver drained queued packets before evaluating expiry.
The longest interval between running polls was 131 ms and there were zero stale
discards. X4 reported captures at host monotonic times 67609.074052945 and
67611.273421193. Python withdrew the source at 67611.091890567. The second
capture therefore did not exist when the source expired. Neither of the first
two samples had a velocity, and the native source logged no first derivative
before the freighter exited the sector. Its rate calculation accepts at most
one second between poses. This measurement locates the bottleneck on the
capture side for this run. The new final save SHA-256 was
`b1cb46f15266182a3106e4616738d35ccdaa4eb7b4c58e610c5b106977df2c07`.

## Reduced software rendering cost

The fourth provider used the unattended graphics preset and a matching
640×360 Xvfb viewport. Its pilot continued using the ordinary graphics profile.
During 20.146 s of server captures, the stock cockpit confirmed 18 poses
over the authenticated motion connection, including seven distinct positions.
All confirmed positions, sequences, sector, identity, generation, game times,
capture times and null velocities matched the provider. X4's cockpit radar
first saw the freighter at 13,366 m and later reported a sector loss.
This verifies that changing server positions can reach a second instance
through genuine radar interest. The pilot had no playback writer.

Successive captures confirmed by the cockpit were 1.044–1.918 s apart.
The second took 640 ms from previous capture to dispatch and a further
1,277 ms waiting for the MD reply. Dispatch itself took 0.192 ms and the
pose was read 0.152 ms after the reply. The CPU provider eventually
expired the source 2.032 s after its last capture. The normal final
checkpoint SHA-256 was
`ad051ecbb828856f8a8a0ec7231a1b5b86988b7713b713ed8ada6db0c8708413`.

During a 12.33 s sample immediately after admission, the provider X4 process
used 4.68 logical CPU cores on average and peaked at 6.78 GiB RSS / 6.70 GiB PSS.
The concurrent cockpit used 2.30 cores and 3.56 GiB RSS / 3.49 GiB PSS.
The busiest continuously observed provider thread was a Mesa llvmpipe worker
at 0.56 core. Both X4 processes ran on the same Ryzen 7 7800X3D with
overlapping CPU affinities. These measurements cover this scene and the
two X4 processes. They are insufficient to size a dedicated rental server
or establish its performance with four pilots.
