# Two admitted cockpits waiting for fresh health

On 26 November, a GPU-free Fedora X4 server admitted two real Linux X4 cockpits on Bazzite. HVC-420 took seat 1073 and KHI-630 took seat 1074. Both received movement acknowledgements. After the second pilot joined, neither cockpit confirmed a fresh authoritative shield reading for the shared running window. The trial required that baseline before releasing the trigger. It recorded zero shots and zero native damage writes.

| Pilot | Movement samples acknowledged | Health requests | Expired before completion | Delivered to X4 | Confirmed in X4 |
| --- | ---: | ---: | ---: | ---: | ---: |
| HVC-420 | 3,946 | 269 | 264 | 1 | 1 |
| KHI-630 | 3,916 | 266 | 265 | 0 | 0 |

HVC's single confirmation preceded KHI's admission. KHI independently read 2,366 shield and 3,600 hull from its own controlled ship. The Fedora adapter obtained six native health readings for HVC and eleven for KHI across the run. It recorded 228 and 223 failed reads respectively. Its log retained the exception class only. The records locate the missing confirmations before delivery to the receiving X4 UI. They do not distinguish queue delay, an expired native read, or a late transport reply for each request.

The Fedora test supervisor stopped on its 4,096 MiB available-memory threshold. Available RAM measured 11,219 MiB at startup and 4,080 MiB at minimum. The highest sampled full-memory pressure average over ten seconds was zero. The provider reported a paused final world and a new checkpoint. Both original test inputs passed their integrity checks, and both machines released their X4 processes. This teardown did not exercise two ordinary pilot departures.

The experiment used native X4 9.00, the movement candidate `9ab95213`, and private capture instrumentation at `1c1505b2`. The provider had ordered impact application enabled. Both clients connected through the private test route. Windows, Internet play and the evening four-ship checkpoint require their own evidence. The separate ordered-impact replay established the server's response to 47 supplied hits. [Ordered impact replay](AUTHORITY-COMPANY-BURST-REPLAY-20261126.md)
