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

## Native comparison on the evening candidate

Both regular Linux cockpits joined a fresh copy of the same world on `97ee6898`. KHI confirmed 2,366 shield and 3,600 hull in its own X4 process 1.939 seconds after taking the request. For KHI's replies that needed no native read, the median time from enqueue to send on Fedora fell from 859 ms to 0.2 ms. The maximum fell from 1,777 ms to 32 ms. The earlier run recorded nine such replies and the comparison recorded eleven.

HVC completed five requests during the twelve-second shared-window measurement without confirming a shield reading. For its requests 4 through 7, Fedora entered the native adapter immediately after enqueue. Initial dispatch took 21–29 ms. The interval from dispatch return to the poll that received the answer was 1.870–1.976 seconds. The receiving poll itself took 11–29 ms. That interval includes X4's handling of the request and the time until the host next polled a ready reply. The trace cannot apportion it further.

Both clients exited normally with zero shots. Fedora paused the world and confirmed the last-departure save. The original input passed its integrity check on both hosts. The change reduced unnecessary queueing. Reciprocal cockpit health and human Internet combat remain open.

## Firing while the shooter's own health read is pending

The regular sender used to wait for its own health reply before reading the native gun log again. In a pinned TLS loopback comparison, a synthetic adapter delayed a health read by one second. Two complete contact records were appended together while the read was outstanding. On `270810b1` the service first applied damage 1,027 ms after the append. With the revised sender it first applied damage after 74 ms. Both versions applied each reported hit once and returned the same two receipts. This comparison used synthetic log entries and damage on one machine.

The sender now monitors the same open log while waiting. A complete gun event can interrupt a health read. Partial lines and unrelated messages leave it running. The next requested health read gets an uninterrupted turn so sustained fire cannot continually displace it. Impacts already read from the same block are sent before another health request. These changes follow the pinned evening build `97ee6898`.

Four simultaneous shooters exposed another delay. Each shipped client has one combat relay and the server has four. With ordinary socket closure, each local relay still waited for the old health reply. All four hits reached the synthetic adapter exactly once, 1.037–1.082 seconds after their log entries. Explicitly abandoning the disposable read on the local TCP socket let its relay finish early. In the Linux loopback comparison the four hits reached the adapter in 95–112 ms. Every shooter received a receipt and resumed health reads. This changes only the local end of an abandoned health request. The normal closing procedure for impact exchanges still preserves uncertain batches for an exact retry.

The regression starts four authenticated client tunnels, holds the first health operation open and checks that a hit reaches the service before that operation expires. Each target loses exactly 26 shield, each shooter gets one receipt, and all four cockpit bridges then receive the updated health. The health adapter, gun log entries and cockpit acknowledgements in this check are synthetic. Windows execution and live X4 combat remain separate checks.
