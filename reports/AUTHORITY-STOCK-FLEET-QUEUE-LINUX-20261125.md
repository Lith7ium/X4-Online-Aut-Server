# Native Company fleet queue on Linux

One regular Bazzite pilot controlled WOO-831 (1970) in X4 9.00 and
issued two stock **Fly and Wait** orders to WRN-302 (7354). The CPU
Vulkan provider and pilot ran the source at `1ad21866` with isolated
profiles and pinned loopback TLS. The provider cold-loaded the verified
prior acceptance save `checkpoint-76697f7e4d3b4110937567d8cca42a85`,
which already held order A for the same assigned NPC captain.

The private pilot driver selected the ship in the native map and used
sector points in `cluster_01_sector001_macro`. It invoked the registered
callbacks for the mounted **Keep**, **Clear** and **Fly and Wait**
buttons. The stock keep setting produced append and the clear setting
produced replace. A separate read-only provider observer read the
native queue before and after each request.

| Stock action | Matching authoritative result | Provider's observed non-default order queue |
| --- | --- | --- |
| Keep, then Fly and Wait B | Accepted `c2e582c4e0d391d074ebcbd200000001` | Started A (7844) remained first. Ready B (34142) appeared second. |
| Clear, then Fly and Wait C | Accepted `c2e582c4e0d391d074ebcbd200000002` | Ready C (34208) was the only entry. A and B were gone. |

Both wire receipts included the selected queue action and exact
destination. B was at (−138488.468750, −3648.029053, 101850.625000) m
and C at (−113488.468750, −3648.029053, 6850.625000) m. The provider
observer read those same points from the native order parameters. The
pilot's inherited local A (7845) remained its sole queue entry across
all seven reads, including before and after each click and at receipt.

The pilot exited through its normal session shutdown. The provider
paused after departure and confirmed
`checkpoint-118cee1e9ca54ec9bba1cb4ab1609540` with SHA-256
`bc2da3cbad6819138929328ef3769ced1c8de894edbc798eacfd03b946514e71`.
That save contained one started non-default MoveWait with C's position
and the same assigned NPC captain seed, `2211373338772438693`. Its
native journal contained exactly A, B and C. The sealed ledger bound
each generation to its own checkpoint and retained identical purchase
and challenge rows across the source, join and departure checkpoints.

Both games and private displays stopped, the TLS invitation and game
lock were removed, and the input checkpoint retained its verified SHA.
The first attempt stopped at the operator's local baseline assertion
because the cold pilot had inherited A. It reached regular helm and
then exited without sending an order.

This run exercised one native pilot and real mounted stock callbacks
through programmatic inputs. Physical pointer input, flight arrival,
Windows and WAN operation remain unverified. Separate socket and
scheduler tests exercised FIFO across two admitted TCP pilots with a
synthetic provider. Raw evidence is retained privately on the test host
under `.local/native-stock-fleet-v2-pair/`.
