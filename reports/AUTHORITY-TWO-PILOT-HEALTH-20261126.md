# Two admitted cockpits waiting for fresh health

On 26 November, a GPU-free Fedora X4 server admitted two real Linux X4 cockpits on Bazzite. HVC-420 took seat 1073 and KHI-630 took seat 1074. Both received movement acknowledgements. After the second pilot joined, neither cockpit confirmed a fresh authoritative shield reading for the shared running window. The trial required that baseline before releasing the trigger. It recorded zero shots and zero native damage writes.

| Pilot | Movement samples acknowledged | Health requests | Expired before completion | Delivered to X4 | Confirmed in X4 |
| --- | ---: | ---: | ---: | ---: | ---: |
| HVC-420 | 3,946 | 269 | 264 | 1 | 1 |
| KHI-630 | 3,916 | 266 | 265 | 0 | 0 |

HVC's single confirmation preceded KHI's admission. KHI independently read 2,366 shield and 3,600 hull from its own controlled ship. The Fedora adapter obtained six native health readings for HVC and eleven for KHI across the run. It recorded 228 and 223 failed reads respectively. Its log retained the exception class only. The records locate the missing confirmations before delivery to the receiving X4 UI. They do not distinguish queue delay, an expired native read, or a late transport reply for each request.

The Fedora test supervisor stopped on its 4,096 MiB available-memory threshold. Available RAM measured 11,219 MiB at startup and 4,080 MiB at minimum. The highest sampled full-memory pressure average over ten seconds was zero. The provider reported a paused final world and a new checkpoint. Both original test inputs passed their integrity checks, and both machines released their X4 processes. This teardown did not exercise two ordinary pilot departures.

The experiment used native X4 9.00, the movement candidate `9ab95213`, and private capture instrumentation at `1c1505b2`. The provider had ordered impact application enabled. Both clients connected through the private test route. Windows, Internet play and the evening four-ship checkpoint require their own evidence. The separate ordered-impact replay established the server's response to 47 supplied hits. [Ordered impact replay](AUTHORITY-COMPANY-BURST-REPLAY-20261126.md)

## Timing a shared session

A second run kept the guns idle and used the ordinary provider settings. The trace joined each request to its reply by seat, session window and sequence. Durations below use the monotonic clock of the host that recorded both endpoints.

* KHI's first read in window 3 took 1.588 seconds in Fedora's native adapter. Its client received the answer after 1.782 seconds and confirmed the value inside X4 after 1.948 seconds. KHI's later requests supplied the revision it already held. One such request waited 1.777 seconds for an empty reply.
* HVC's third read in that window waited 1.717 seconds to enter the native adapter. For sequences 4, 6 and 7, Fedora sent valid answers between 1.678 and 1.852 seconds after enqueue. HVC reached its two-second deadline on each request and closed the connection. The clocks do not establish how much of the remaining time the connection setup consumed.

Both pilots left normally. Fedora paused the world and saved a new checkpoint. Neither pilot fired. One of the two cockpits confirmed its baseline for the shared session.

The follow-up changes let the server answer an unchanged revision while another pilot awaits a native read. A client that sent its full request can also keep the stream across a timeout. It validates and discards the late reply before requesting a fresh reading. A pending impact takes priority and uses a new stream if necessary. The existing two-second deadline still governs a fresh client read.
